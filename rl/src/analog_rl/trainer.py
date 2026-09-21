"""Small SFT/GRPO trainer primitives with optional torch/TRL integration.

The classes are intentionally usable without optional ML dependencies: they
provide deterministic collation, masks, objective calculations and checkpoint
metadata.  If a torch model is supplied, ``train`` performs ordinary optimizer
steps; TRL can still be used by applications that need distributed training.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .masks import action_mask, conversation_tokens, masked_labels, message_response_mask
from .policy import PolicyMetadata, make_policy_metadata, save_policy_metadata


def _as_list(value: Any) -> list[Any]:
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    # Tokenizers commonly return ``[[ids...]]`` for a single string when
    # configured to return tensors.  Conversation collation expects one
    # sequence per message, so remove that synthetic batch dimension.
    if value and isinstance(value[0], (list, tuple)) and len(value) == 1:
        value = value[0]
    return list(value)


def tokenize_messages(messages: Sequence[Mapping[str, Any]], tokenizer: Any, *,
                      tools: Sequence[Mapping[str, Any]] | None = None) -> tuple[list[int], list[int]]:
    """Tokenize a full conversation and produce an assistant-only mask.

    ``apply_chat_template`` is used when available so role/control tokens and
    tool calls are represented exactly as the policy sees them. The portable
    fallback in :func:`conversation_tokens` keeps explicit role delimiters.
    """
    ids, mask = conversation_tokens(messages, tokenizer, tools=tools)
    return [int(token) for token in ids], [int(value) for value in mask]


def collate_sft(examples: Sequence[Mapping[str, Any]], tokenizer: Any, *, pad_to: int | None = None,
                ignore_index: int = -100) -> dict[str, list[list[int]]]:
    """Collate chat examples and mask all non-assistant labels."""
    if not examples:
        raise ValueError("SFT batch is empty")
    pad_id = getattr(tokenizer, "pad_token_id", 0) or 0
    rows = [tokenize_messages(example["messages"], tokenizer, tools=example.get("tools")) for example in examples]
    if any(len(ids) < 2 or not any(mask[1:]) for ids, mask in rows):
        raise ValueError("each SFT example requires a predictable assistant token")
    width = pad_to or max(len(ids) for ids, _ in rows)
    if width < max(len(ids) for ids, _ in rows):
        raise ValueError("pad_to is shorter than an example")
    out_ids, out_mask, out_labels = [], [], []
    for ids, mask in rows:
        ids, mask = ids[:width], mask[:width]
        padding = width - len(ids)
        out_ids.append(ids + [pad_id] * padding)
        out_mask.append([1] * len(ids) + [0] * padding)
        out_labels.append(masked_labels(ids, mask, ignore_index) + [ignore_index] * padding)
    return {"input_ids": out_ids, "attention_mask": out_mask, "labels": out_labels}


@dataclass
class SFTConfig:
    output_dir: str = "checkpoint"
    learning_rate: float = 1e-5
    epochs: int = 1
    batch_size: int = 1
    max_length: int | None = None
    ignore_index: int = -100
    gradient_accumulation_steps: int = 1
    model_name: str = ""

    def __post_init__(self):
        _validate_training_config(self)
        if self.max_length is not None and (type(self.max_length) is not int or self.max_length < 2):
            raise ValueError("max_length must be at least two tokens")


@dataclass
class GRPOConfig:
    output_dir: str = "checkpoint"
    learning_rate: float = 1e-6
    epochs: int = 1
    clip_range: float = 0.2
    kl_coefficient: float = 0.01
    model_name: str = ""
    gradient_accumulation_steps: int = 1

    def __post_init__(self):
        _validate_training_config(self)
        if not 0 <= self.clip_range < 1 or not math.isfinite(self.kl_coefficient) or self.kl_coefficient < 0:
            raise ValueError("invalid GRPO clip range or KL coefficient")


def _validate_training_config(config: Any) -> None:
    for name in ("epochs", "batch_size", "gradient_accumulation_steps"):
        value = getattr(config, name, 1)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not math.isfinite(config.learning_rate) or config.learning_rate <= 0:
        raise ValueError("learning_rate must be finite and positive")


def _optimizer_step(optimizer: Any, normalization: int) -> None:
    # Normalize by actual target tokens (SFT) or sequences (GRPO), including
    # short accumulation windows and variable-size microbatches.
    for group in optimizer.param_groups:
        for parameter in group["params"]:
            if parameter.grad is not None:
                parameter.grad.div_(normalization)
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)


def _save_trainer_checkpoint(trainer: Any, output_dir: str | Path, kind: str) -> PolicyMetadata:
    import torch

    if trainer.model is None:
        raise ValueError("a model is required to save a training checkpoint")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if hasattr(trainer.model, "save_pretrained"):
        trainer.model.save_pretrained(root)
    else:
        torch.save(trainer.model.state_dict(), root / "pytorch_model.bin")
    if trainer.tokenizer is not None and hasattr(trainer.tokenizer, "save_pretrained"):
        trainer.tokenizer.save_pretrained(root)
    torch.save({"state": trainer.state, "config": asdict(trainer.config),
                "optimizer": trainer.optimizer.state_dict() if trainer.optimizer is not None else None,
                "rng_state": torch.get_rng_state()}, root / "training_state.pt")
    metadata = make_policy_metadata(trainer.config.model_name or trainer.model.__class__.__name__, root,
                                    tokenizer=getattr(trainer.tokenizer, "name_or_path", ""),
                                    trainer=kind, step=trainer.state["global_step"])
    save_policy_metadata(root, metadata)
    return metadata


def grpo_objective(current_logprobs: Any, old_logprobs: Any, reference_logprobs: Any,
                   advantages: Any, action_mask_values: Any, *, clip_range: float = 0.2,
                   kl_coefficient: float = 0.01) -> dict[str, Any]:
    """Compute token-level clipped GRPO objective and diagnostics.

    Inputs may be nested Python lists or torch tensors.  A token contributes
    only when ``action_mask_values`` is true.  The returned ``loss`` is a Python
    float for list inputs and a scalar tensor for torch inputs.
    """
    if not 0 <= clip_range < 1 or not math.isfinite(kl_coefficient) or kl_coefficient < 0:
        raise ValueError("invalid GRPO clip range or KL coefficient")
    try:
        import torch
        tensor_inputs = (current_logprobs, old_logprobs, reference_logprobs, advantages, action_mask_values)
        device = next((x.device for x in tensor_inputs if isinstance(x, torch.Tensor)), None)
        def tensor(value, *, dtype=torch.float32):
            return value.to(device) if isinstance(value, torch.Tensor) else torch.as_tensor(value, dtype=dtype, device=device)
        cur = tensor(current_logprobs)
        old = tensor(old_logprobs).detach()
        ref = tensor(reference_logprobs).detach()
        adv = tensor(advantages).detach()
        mask = tensor(action_mask_values, dtype=torch.float32).detach()
        if cur.ndim not in (1, 2) or cur.shape[-1] == 0:
            raise ValueError("GRPO log probabilities must be [tokens] or [batch,tokens]")
        def align(value, name):
            if value.ndim != cur.ndim or value.shape != cur.shape:
                raise ValueError(f"{name} shape {tuple(value.shape)} must match current {tuple(cur.shape)}")
            return value
        old, ref, mask = align(old, "old_logprobs"), align(ref, "reference_logprobs"), align(mask, "action_mask")
        if adv.ndim == cur.ndim:
            if adv.shape != cur.shape:
                raise ValueError(f"advantages shape {tuple(adv.shape)} must match current {tuple(cur.shape)}")
        elif adv.ndim == cur.ndim - 1 and adv.shape == cur.shape[:-1]:
            adv = adv.unsqueeze(-1)
        elif adv.ndim == 0:
            adv = adv.expand_as(cur)
        else:
            raise ValueError("advantages must be scalar, [batch], or [batch,tokens]")
        if torch.any((mask != 0) & (mask != 1)):
            raise ValueError("action_mask must be binary")
        if not torch.isfinite(adv).all() or any(not torch.isfinite(value[mask.bool()]).all() for value in (cur, old, ref)):
            raise ValueError("GRPO action log probabilities and advantages must be finite")
        if torch.any(mask.sum(-1) == 0):
            raise ValueError("each GRPO sequence requires an action token")
        # Excluded positions can contain unavailable (-inf) log probabilities.
        # Replace them before exponentiation to avoid NaN * zero gradients.
        cur, old, ref = [torch.where(mask.bool(), value, torch.zeros_like(value)) for value in (cur, old, ref)]
        ratio = torch.exp(cur - old)
        clipped = torch.clamp(ratio, 1 - clip_range, 1 + clip_range)
        surrogate = torch.minimum(ratio * adv, clipped * adv)
        # GRPO k3 estimator: exp(log p_ref - log p_cur) - (log p_ref-log p_cur) - 1.
        log_ratio_ref = ref - cur
        approx_kl = torch.exp(log_ratio_ref) - log_ratio_ref - 1.0
        denom = mask.sum(-1)
        policy_loss = -((surrogate * mask).sum(-1) / denom).mean()
        kl = ((approx_kl * mask).sum(-1) / denom).mean()
        result = {"loss": policy_loss + kl_coefficient * kl, "policy_loss": policy_loss,
                  "kl": kl, "ratio": ratio, "mask": mask}
        if not torch.isfinite(result["loss"]):
            raise ValueError("GRPO objective overflow; log probability differences are too large")
        return result if any(isinstance(value, torch.Tensor) for value in tensor_inputs) else {
            key: value.tolist() for key, value in result.items()}
    except ImportError:
        # Keep preparation-only environments numerically consistent with torch:
        # average actions within each sequence, then average the sequences.
        if not isinstance(current_logprobs, (list, tuple)) or not current_logprobs:
            raise ValueError("GRPO log probabilities must be [tokens] or [batch,tokens]")
        batched = isinstance(current_logprobs[0], (list, tuple))
        cur = list(current_logprobs) if batched else [list(current_logprobs)]
        width = len(cur[0])
        if not width or any(not isinstance(row, (list, tuple)) or len(row) != width for row in cur):
            raise ValueError("GRPO log probabilities must form a nonempty rectangular array")

        def align(value, name):
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{name} must match current log probabilities")
            rows = list(value) if batched else [list(value)]
            if len(rows) != len(cur) or any(not isinstance(row, (list, tuple)) or len(row) != width for row in rows):
                raise ValueError(f"{name} must match current log probabilities")
            return rows

        old = align(old_logprobs, "old_logprobs")
        ref = align(reference_logprobs, "reference_logprobs")
        mask = align(action_mask_values, "action_mask")
        if not isinstance(advantages, (list, tuple)):
            adv = [[float(advantages)] * width for _ in cur]
        elif batched and len(advantages) == len(cur) and all(not isinstance(value, (list, tuple)) for value in advantages):
            adv = [[float(value)] * width for value in advantages]
        else:
            adv = align(advantages, "advantages")
        policy_values, kl_values, ratios = [], [], []
        for values, old_values, ref_values, weights, selected in zip(cur, old, ref, adv, mask):
            if any(value not in (0, 1) for value in selected):
                raise ValueError("action_mask must be binary")
            if not any(selected):
                raise ValueError("each GRPO sequence requires an action token")
            if any(not math.isfinite(value) for value in weights):
                raise ValueError("GRPO action log probabilities and advantages must be finite")
            row_policy, row_kl, row_ratios = [], [], []
            for value, old_value, ref_value, weight, keep in zip(values, old_values, ref_values, weights, selected):
                if not keep:
                    row_ratios.append(1.0)
                    continue
                if any(not math.isfinite(number) for number in (value, old_value, ref_value)):
                    raise ValueError("GRPO action log probabilities and advantages must be finite")
                try:
                    ratio = math.exp(value - old_value)
                    delta = ref_value - value
                    token_kl = math.expm1(delta) - delta
                except OverflowError as exc:
                    raise ValueError("GRPO objective overflow; log probability differences are too large") from exc
                row_ratios.append(ratio)
                clipped = min(max(ratio, 1 - clip_range), 1 + clip_range)
                row_policy.append(-min(ratio * weight, clipped * weight))
                row_kl.append(token_kl)
            policy_values.append(sum(row_policy) / len(row_policy))
            kl_values.append(sum(row_kl) / len(row_kl))
            ratios.append(row_ratios)
        policy_loss = sum(policy_values) / len(policy_values)
        kl = sum(kl_values) / len(kl_values)
        loss = policy_loss + kl_coefficient * kl
        if not math.isfinite(loss):
            raise ValueError("GRPO objective overflow; log probability differences are too large")
        return {"loss": loss, "policy_loss": policy_loss, "kl": kl,
                "ratio": ratios if batched else ratios[0], "mask": mask if batched else mask[0]}


class SFTTrainer:
    """Framework-neutral trainer; uses torch when available and a model is given."""
    def __init__(self, model: Any = None, tokenizer: Any = None, train_dataset: Iterable[Mapping[str, Any]] | None = None,
                 config: SFTConfig | None = None, **kwargs: Any):
        self.model, self.tokenizer = model, tokenizer
        self.train_dataset = list(train_dataset or [])
        self.config = config or SFTConfig(**{k: v for k, v in kwargs.items() if k in SFTConfig.__annotations__})
        self.state = {"global_step": 0, "loss": None}
        self.optimizer = None

    def collate(self, examples: Sequence[Mapping[str, Any]]) -> dict[str, list[list[int]]]:
        return collate_sft(examples, self.tokenizer, pad_to=self.config.max_length,
                           ignore_index=self.config.ignore_index)

    def compute_loss(self, model: Any, inputs: Mapping[str, Any], return_outputs: bool = False) -> Any:
        """Transformers/TRL-compatible loss hook."""
        # Standard Hugging Face causal-LM heads use -100 as their ignore index.
        model_inputs = dict(inputs)
        if self.config.ignore_index != -100:
            model_inputs["labels"] = inputs["labels"].masked_fill(inputs["labels"] == self.config.ignore_index, -100)
        outputs = model(**model_inputs)
        loss = outputs.loss if hasattr(outputs, "loss") else outputs["loss"]
        return (loss, outputs) if return_outputs else loss

    def train(self, *, output_dir: str | None = None) -> dict[str, Any]:
        if self.model is None or self.tokenizer is None:
            return {"status": "prepared", **self.state, "examples": len(self.train_dataset)}
        if not self.train_dataset:
            return {"status": "prepared", **self.state, "examples": 0}
        try:
            import torch
        except ImportError:
            raise RuntimeError("torch is required for parameter updates")
        self.model.train()
        parameters = list(self.model.parameters())
        if not parameters:
            raise ValueError("SFT model must have trainable parameters")
        if self.optimizer is None:
            self.optimizer = torch.optim.AdamW(parameters, lr=self.config.learning_rate)
        losses, total_loss_weight = [], 0
        accumulation = self.config.gradient_accumulation_steps
        self.optimizer.zero_grad(set_to_none=True)
        pending_batches = pending_targets = 0
        device = parameters[0].device
        for _ in range(self.config.epochs):
            for start in range(0, len(self.train_dataset), max(1, self.config.batch_size)):
                batch = self.collate(self.train_dataset[start:start + self.config.batch_size])
                tensors = {key: torch.as_tensor(value, dtype=torch.long, device=device)
                           for key, value in batch.items()}
                loss = self.compute_loss(self.model, tensors)
                if loss.ndim != 0 or not torch.isfinite(loss):
                    raise ValueError("SFT model must return a finite scalar loss")
                # Causal-LM loss predicts labels at t+1.  Weight microbatches
                # by their supervised token count so variable lengths and a
                # final short accumulation window have the same objective as
                # one concatenated batch.
                target_count = int((tensors["labels"][:, 1:] != self.config.ignore_index).sum().item())
                if target_count == 0:
                    raise ValueError("SFT batch has no causal-LM target tokens")
                (loss * target_count).backward()
                pending_batches += 1
                pending_targets += target_count
                if pending_batches >= accumulation:
                    _optimizer_step(self.optimizer, pending_targets)
                    pending_batches = pending_targets = 0
                    self.state["global_step"] += 1
                losses.append(float(loss.detach().cpu()) * target_count)
                total_loss_weight += target_count
        if pending_batches:
            _optimizer_step(self.optimizer, pending_targets)
            self.state["global_step"] += 1
        self.state["loss"] = sum(losses) / total_loss_weight
        self.save_checkpoint(output_dir or self.config.output_dir)
        return {"status": "trained", **self.state, "examples": len(self.train_dataset)}

    def save_checkpoint(self, output_dir: str | Path) -> PolicyMetadata:
        return _save_trainer_checkpoint(self, output_dir, "sft")


class GRPOTrainer:
    def __init__(self, model: Any = None, tokenizer: Any = None, config: GRPOConfig | None = None, **kwargs: Any):
        self.model, self.tokenizer = model, tokenizer
        self.config = config or GRPOConfig(**{k: v for k, v in kwargs.items() if k in GRPOConfig.__annotations__})
        self.state = {"global_step": 0, "loss": None}
        self.optimizer = None

    def objective(self, current_logprobs: Any, old_logprobs: Any, reference_logprobs: Any,
                  advantages: Any, action_mask_values: Any) -> dict[str, Any]:
        return grpo_objective(current_logprobs, old_logprobs, reference_logprobs, advantages,
                              action_mask_values, clip_range=self.config.clip_range,
                              kl_coefficient=self.config.kl_coefficient)

    def compute_loss(self, model: Any, inputs: Mapping[str, Any], return_outputs: bool = False) -> Any:
        if model is None:
            result = self.objective(inputs["current_logprobs"], inputs["old_logprobs"],
                                    inputs["reference_logprobs"], inputs["advantages"], inputs["action_mask"])
        else:
            import torch

            if "input_ids" not in inputs:
                raise ValueError("model GRPO training requires input_ids and action labels")
            device = next(model.parameters()).device
            model_inputs = {key: torch.as_tensor(value, device=device)
                            for key, value in inputs.items() if key in {"input_ids", "attention_mask"}}
            labels = torch.as_tensor(inputs.get("labels", inputs["input_ids"]), device=device, dtype=torch.long)
            if labels.ndim != 2 or labels.shape != model_inputs["input_ids"].shape or labels.shape[1] < 2:
                raise ValueError("GRPO labels must match [batch, tokens] input_ids with at least two tokens")
            outputs = model(**model_inputs)
            logits = outputs.logits if hasattr(outputs, "logits") else outputs["logits"]
            if logits.ndim != 3 or logits.shape[:2] != labels.shape:
                raise ValueError("GRPO logits must match [batch, tokens, vocabulary]")
            # Causal logits at t predict label t+1.  Low-precision model logits
            # are promoted before softmax, keeping token logprobs stable.
            logits = logits[:, :-1]
            if logits.dtype in (torch.float16, torch.bfloat16):
                logits = logits.float()
            targets = labels[:, 1:]
            current = torch.log_softmax(logits, dim=-1).gather(-1, targets.clamp_min(0).unsqueeze(-1)).squeeze(-1)

            def causal_field(name):
                value = torch.as_tensor(inputs[name], device=device)
                if value.ndim == 2 and value.shape == labels.shape:
                    return value[:, 1:]
                if value.shape == current.shape:
                    return value
                if name == "advantages" and (value.ndim == 0 or value.shape == current.shape[:-1]):
                    return value
                raise ValueError(f"{name} must align to raw [batch, tokens] or shifted [batch, tokens-1] values")

            mask = causal_field("action_mask")
            if torch.any((targets < 0) & mask.bool()):
                raise ValueError("GRPO action_mask selects a missing causal-LM label")
            attention = model_inputs.get("attention_mask")
            if attention is not None:
                if attention.shape != labels.shape:
                    raise ValueError("attention_mask must match input_ids")
                if torch.any((attention[:, 1:] == 0) & mask.bool()):
                    raise ValueError("GRPO action_mask selects a padding token")
            result = self.objective(current, causal_field("old_logprobs"), causal_field("reference_logprobs"),
                                    causal_field("advantages"), mask)
        return (result["loss"], result) if return_outputs else result["loss"]

    def train(self, batches: Iterable[Mapping[str, Any]] | None = None, *, output_dir: str | None = None) -> dict[str, Any]:
        """Optimize fixed rollout batches with caller-provided old/reference logprobs.

        Model batches contain full ``input_ids`` and optional full ``labels``;
        rollout logprobs/masks may be full-length or already shifted by one.
        Without a model this only evaluates objectives and cannot train.
        """
        all_batches = list(batches or [])
        if not all_batches:
            return {"status": "prepared", **self.state}
        model = self.model
        pending_batches = pending_sequences = 0
        losses, total_sequences = [], 0
        accumulation = self.config.gradient_accumulation_steps
        if model is not None:
            try:
                import torch
                parameters = list(model.parameters())
                if not parameters:
                    raise ValueError("GRPO model must have trainable parameters")
                if self.optimizer is None:
                    self.optimizer = torch.optim.AdamW(parameters, lr=self.config.learning_rate)
                # Policy ratios are defined for fixed token probabilities.  In
                # evaluation mode dropout cannot make a replayed trajectory's
                # current logprob stochastic, while gradients still flow.
                model.eval()
                self.optimizer.zero_grad(set_to_none=True)
            except ImportError as exc:
                raise RuntimeError("torch is required for parameter updates") from exc
        for _ in range(self.config.epochs if model is not None else 1):
            for batch in all_batches:
                loss, result = self.compute_loss(model, batch, return_outputs=True)
                mask = result["mask"]
                sequence_count = len(mask) if len(mask) and hasattr(mask[0], "__len__") else 1
                losses.append(float(loss.detach().cpu() if hasattr(loss, "detach") else loss) * sequence_count)
                total_sequences += sequence_count
                if model is not None:
                    (loss * sequence_count).backward()
                    pending_batches += 1
                    pending_sequences += sequence_count
                    if pending_batches >= accumulation:
                        _optimizer_step(self.optimizer, pending_sequences)
                        pending_batches = pending_sequences = 0
                        self.state["global_step"] += 1
        if model is not None and pending_batches:
            _optimizer_step(self.optimizer, pending_sequences)
            self.state["global_step"] += 1
        self.state["loss"] = sum(losses) / total_sequences
        if model is not None:
            self.save_checkpoint(output_dir or self.config.output_dir)
        return {"status": "trained" if model is not None else "evaluated", **self.state}

    def save_checkpoint(self, output_dir: str | Path) -> PolicyMetadata:
        return _save_trainer_checkpoint(self, output_dir, "grpo")


# Common naming used by external trainers.
compute_grpo_loss = grpo_objective
build_sft_response_mask = message_response_mask
build_rl_action_mask = action_mask

def _trl_policy_callback(trainer_name, parent_version=None):
    from transformers import TrainerCallback

    class PolicyCallback(TrainerCallback):
        def on_save(self, args, state, control, model=None, **kwargs):
            checkpoint = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            config = getattr(model, "config", None)
            name = getattr(config, "_name_or_path", "") or type(model).__name__
            metadata = make_policy_metadata(name, checkpoint, trainer=trainer_name,
                                            step=state.global_step, parent_version=parent_version)
            save_policy_metadata(checkpoint, metadata)
            return control

    return PolicyCallback()


def create_trl_sft_trainer(model, tokenizer, dataset, **kwargs):
    """Build TRL SFT with explicit assistant-only labels, including tool calls.

    Accepts a datasets Dataset of messages, prompt/response pairs, or tokenized
    rows with explicit labels. Raw text alone cannot establish response bounds.
    """
    try:
        from trl import SFTConfig as TRLSFTConfig, SFTTrainer as TRLSFTTrainer
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError("Install the compatible dependencies in rl/requirements-train.txt") from exc

    args = copy.deepcopy(kwargs.pop("args", None))
    if args is None:
        args = TRLSFTConfig(output_dir="checkpoint")
    if not isinstance(args, TRLSFTConfig):
        raise TypeError("args must be trl.SFTConfig")
    # Labels have already excluded context; TRL must not derive new labels
    # from raw text or apply a second, incompatible completion mask.
    args.assistant_only_loss = False
    args.completion_only_loss = False

    def prepare(example):
        messages = example.get("messages")
        if messages is None and "prompt" in example:
            response = example.get("response", example.get("completion"))
            if isinstance(example["prompt"], list) and isinstance(response, list):
                messages = example["prompt"] + response
            elif isinstance(example["prompt"], str) and isinstance(response, str):
                messages = [{"role": "user", "content": example["prompt"]},
                            {"role": "assistant", "content": response}]
        if messages is not None:
            ids, mask = tokenize_messages(messages, tokenizer, tools=example.get("tools"))
            labels = masked_labels(ids, mask)
        elif "input_ids" in example and "labels" in example:
            ids, labels = list(example["input_ids"]), list(example["labels"])
        else:
            raise ValueError("SFT requires messages, prompt/response, or explicit input_ids/labels")
        if args.max_length is not None:
            ids, labels = ids[:args.max_length], labels[:args.max_length]
        if len(ids) != len(labels) or not any(label != -100 for label in labels[1:]):
            raise ValueError("SFT example has no predictable assistant tokens after truncation")
        return {"input_ids": ids, "labels": labels}

    def prepare_dataset(data):
        return data.map(prepare, remove_columns=data.column_names)

    dataset = prepare_dataset(dataset)
    if kwargs.get("eval_dataset") is not None:
        evaluation = kwargs["eval_dataset"]
        kwargs["eval_dataset"] = ({key: prepare_dataset(value) for key, value in evaluation.items()}
                                   if isinstance(evaluation, dict) else prepare_dataset(evaluation))
    parent = kwargs.pop("policy_parent_version", None)
    kwargs["callbacks"] = list(kwargs.get("callbacks") or []) + [_trl_policy_callback("trl-sft", parent)]
    return TRLSFTTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=args, **kwargs)


def create_trl_grpo_trainer(model, reward_funcs=None, args=None, **kwargs):
    """Build standard token-ratio GRPO; TRL's default objective may differ."""
    try:
        from trl import GRPOConfig as TRLGRPOConfig, GRPOTrainer as TRLGRPOTrainer
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError("Install the compatible dependencies in rl/requirements-train.txt") from exc
    args = copy.deepcopy(args) if args is not None else TRLGRPOConfig(
        output_dir="checkpoint", loss_type="grpo", beta=0.01, disable_dropout=True)
    if args.loss_type != "grpo" or args.importance_sampling_level != "token":
        raise ValueError("standard GRPO requires loss_type='grpo' and importance_sampling_level='token'")
    parent = kwargs.pop("policy_parent_version", None)
    kwargs["callbacks"] = list(kwargs.get("callbacks") or []) + [_trl_policy_callback("trl-grpo", parent)]
    return TRLGRPOTrainer(model=model, reward_funcs=reward_funcs, args=args, **kwargs)
