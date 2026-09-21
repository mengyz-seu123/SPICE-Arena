"""Token masks for supervised and policy-gradient training.

The environment portions of a trajectory (system/user messages and tool
observations) are context. Assistant text and tool calls are model actions. This
module deliberately works with plain Python lists so importing the RL package
never requires torch or transformers.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _as_list(value: Any) -> list[Any]:
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if value and isinstance(value[0], (list, tuple)) and len(value) == 1:
        value = value[0]
    return list(value or [])


def _encode(tokenizer: Any, text: str) -> list[int]:
    """Encode text without requiring a particular tokenizer implementation."""
    if tokenizer is None:
        return text.split()
    try:
        result = tokenizer(text, add_special_tokens=False)
    except TypeError:
        result = tokenizer(text)
    if isinstance(result, Mapping):
        result = result.get("input_ids", [])
    # torch/numpy arrays and nested one-item batches are common return values.
    return _as_list(result)


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content", "")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, Sequence):
        text = "".join(str(part.get("text", part)) if isinstance(part, Mapping) else str(part) for part in content)
    else:
        text = str(content)
    # Calls remain actions when the same assistant message also contains text.
    if message.get("tool_calls"):
        text += json.dumps(message["tool_calls"], sort_keys=True, separators=(",", ":"))
    return text


def conversation_tokens(messages: Sequence[Mapping[str, Any]], tokenizer: Any = None, *,
                        tools: Sequence[Mapping[str, Any]] | None = None) -> tuple[list[Any], list[int]]:
    """Render the policy's chat template, masking assistant text and calls.

    Templates without generation tags require unambiguous generation headers
    and character offsets. Tokenizers with no template use explicit ChatML
    delimiters. A template's role tokens are context, never assistant targets.
    """
    normalized = []
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported conversation role: {role!r}")
        message = copy.deepcopy(dict(message))
        if message.get("content") is None:
            message["content"] = ""
        for call in message.get("tool_calls") or []:
            function = call.get("function")
            if not isinstance(function, dict):
                raise ValueError("tool calls must contain a function object")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ValueError("tool call arguments must be valid JSON") from exc
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be an object")
            function["arguments"] = arguments
        normalized.append(message)
    if not normalized:
        return [], []
    apply_template = getattr(tokenizer, "apply_chat_template", None)
    if callable(apply_template) and getattr(tokenizer, "chat_template", True):
        options = {"tools": tools} if tools is not None else {}
        try:
            encoded = apply_template(normalized, tokenize=True, add_generation_prompt=False,
                                     return_dict=True, return_assistant_tokens_mask=True, **options)
        except TypeError:
            encoded = None
        if isinstance(encoded, Mapping) and "assistant_masks" in encoded:
            ids = _as_list(encoded["input_ids"])
            assistant = _as_list(encoded["assistant_masks"])
            if len(ids) != len(assistant) or any(value not in (0, 1) for value in assistant):
                raise ValueError("chat template assistant mask must align and be binary")
            if any(assistant):
                return ids, [int(value) for value in assistant]
        rendered = apply_template(normalized, tokenize=False, add_generation_prompt=False, **options)
        if not isinstance(rendered, str):
            raise ValueError("chat template must render text")
        spans = []
        for index, message in enumerate(normalized):
            if message["role"] != "assistant":
                continue
            prefix = apply_template(normalized[:index], tokenize=False, add_generation_prompt=True, **options)
            completed = apply_template(normalized[:index + 1], tokenize=False,
                                       add_generation_prompt=False, **options)
            if not completed.startswith(prefix) or not rendered.startswith(completed) or completed == prefix:
                raise ValueError("chat template assistant boundaries are ambiguous; use generation tags")
            spans.append((len(prefix), len(completed)))
        try:
            encoded = tokenizer(rendered, add_special_tokens=False, return_offsets_mapping=True)
        except (TypeError, NotImplementedError):
            encoded = None
        if not isinstance(encoded, Mapping) or "offset_mapping" not in encoded:
            raise ValueError("chat template requires generation tags or tokenizer offset mappings")
        ids = _as_list(encoded["input_ids"])
        offsets = encoded["offset_mapping"]
        if hasattr(offsets, "tolist"):
            offsets = offsets.tolist()
        if len(offsets) != len(ids):
            raise ValueError("offset mapping length differs from token count")
        return ids, [int(end > start and any(left <= start and end <= right for left, right in spans))
                     for start, end in offsets]
    tokens: list[Any] = []
    mask: list[int] = []
    for message in normalized:
        header = _encode(tokenizer, f"<|im_start|>{message['role']}\n")
        body = _encode(tokenizer, _message_text(message) + "<|im_end|>\n")
        tokens.extend(header + body)
        mask.extend([0] * len(header) + [int(message["role"] == "assistant")] * len(body))
    return tokens, mask


def build_response_mask(input_ids: Sequence[Any], assistant_spans: Sequence[tuple[int, int]] | None = None,
                        response_start: int | None = None, response_end: int | None = None) -> list[int]:
    """Return a 0/1 mask for assistant response token positions.

    ``assistant_spans`` uses half-open ``(start, end)`` offsets.  For a single
    completion, ``response_start``/``response_end`` are convenient aliases.
    No implicit ``all ones`` behavior is used: omitted spans produce zeros.
    """
    n = len(input_ids)
    spans = list(assistant_spans or [])
    if response_end is not None and response_start is None:
        raise ValueError("response_end requires response_start")
    if response_start is not None:
        spans.append((response_start, n if response_end is None else response_end))
    mask = [0] * n
    for start, end in spans:
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= n:
            raise ValueError("response spans must be integer offsets within input ids")
        for i in range(start, end):
            mask[i] = 1
    return mask


def build_action_mask(input_ids: Sequence[Any], assistant_spans: Sequence[tuple[int, int]] | None = None,
                      response_start: int | None = None, response_end: int | None = None) -> list[int]:
    """Build the RL action mask; alias with policy-gradient terminology."""
    return build_response_mask(input_ids, assistant_spans, response_start, response_end)


def message_response_mask(messages: Sequence[Mapping[str, Any]], tokenizer: Any = None, **kwargs: Any) -> list[int]:
    """Return assistant-only mask for a list of chat messages."""
    return conversation_tokens(messages, tokenizer, **kwargs)[1]


def response_mask(messages_or_ids: Sequence[Any], tokenizer: Any = None, **kwargs: Any) -> list[int]:
    """Compatibility helper accepting either messages or token ids.

    For messages, assistant roles are detected.  For ids, explicit spans must
    be supplied via keyword arguments.
    """
    if messages_or_ids and isinstance(messages_or_ids[0], Mapping):
        return message_response_mask(messages_or_ids, tokenizer, **kwargs)
    return build_response_mask(messages_or_ids, **kwargs)


def action_mask(messages_or_ids: Sequence[Any], tokenizer: Any = None, **kwargs: Any) -> list[int]:
    if messages_or_ids and isinstance(messages_or_ids[0], Mapping):
        return message_response_mask(messages_or_ids, tokenizer, **kwargs)
    return build_action_mask(messages_or_ids, **kwargs)


def masked_labels(input_ids: Sequence[int], mask: Sequence[int], ignore_index: int = -100) -> list[int]:
    """Create SFT labels with non-assistant/environment positions ignored."""
    if len(input_ids) != len(mask):
        raise ValueError("input_ids and mask must have equal lengths")
    if any(value not in (0, 1) for value in mask):
        raise ValueError("token mask must be binary")
    return [int(token) if bool(keep) else ignore_index for token, keep in zip(input_ids, mask)]


def masked_mean(values: Sequence[Any], mask: Sequence[Any], eps: float = 1e-12) -> Any:
    """Mean over selected positions, returning zero for an empty mask."""
    if len(values) != len(mask):
        raise ValueError("values and mask must have equal lengths")
    selected = [value for value, keep in zip(values, mask) if bool(keep)]
    if not selected:
        return 0.0
    return sum(selected) / max(eps, len(selected))


# Explicit names used by downstream integrations.
response_token_mask = message_response_mask
rl_action_mask = action_mask
build_sft_response_mask = message_response_mask
build_rl_action_mask = action_mask
