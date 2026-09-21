"""Supervised training with assistant-only labels."""
from __future__ import annotations
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from .masks import conversation_tokens, masked_labels
from .policy import PolicyMetadata, make_policy_metadata, save_policy_metadata


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


def _validate_training_config(config: Any) -> None:
    for name in ("epochs", "batch_size", "gradient_accumulation_steps"):
        value = getattr(config, name, 1)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not math.isfinite(config.learning_rate) or config.learning_rate <= 0:
        raise ValueError("learning_rate must be finite and positive")


def _optimizer_step(optimizer: Any, normalization: int) -> None:
    # Normalize by actual target tokens, including
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
