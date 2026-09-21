"""SFT dataset export and assistant-only loss masks."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from .trajectory import Trajectory, validate_trajectory


def _as_dict(item: Trajectory | Mapping[str, Any]) -> dict[str, Any]:
    return item.to_dict() if isinstance(item, Trajectory) else dict(item)


def sft_messages(item: Trajectory | Mapping[str, Any], include_tools: bool = True) -> list[dict[str, Any]]:
    data = _as_dict(item)
    messages = []
    for message in data.get("messages", []):
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}: continue
        if not include_tools and role == "tool": continue
        msg = {"role": role, "content": message.get("content", "")}
        if message.get("tool_calls"): msg["tool_calls"] = message["tool_calls"]
        if message.get("tool_call_id"): msg["tool_call_id"] = message["tool_call_id"]
        messages.append(msg)
    return messages


def to_sft_example(item: Trajectory | Mapping[str, Any], require_valid: bool = True) -> dict[str, Any]:
    data = _as_dict(item)
    check = validate_trajectory(data)
    if require_valid and not check["trainable"]:
        raise ValueError("Trajectory is not trainable: " + "; ".join(check["errors"]))
    return {"messages": sft_messages(data), "reward": data.get("reward"),
            "model": data.get("model"), "policy_version": data.get("policy_version"),
            "trajectory_schema": data.get("schema", "analog-trace/trajectory/v1")}


def export_sft(items: Iterable[Trajectory | Mapping[str, Any]], path: str | Path | None = None,
               require_valid: bool = True) -> list[dict[str, Any]]:
    rows = [to_sft_example(item, require_valid=require_valid) for item in items]
    if path is not None:
        target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as stream:
            for row in rows: stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def assistant_message_mask(messages: Iterable[Mapping[str, Any]]) -> list[bool]:
    """Return one response mask entry per message (true only for assistant rows)."""
    return [m.get("role") == "assistant" for m in messages]


def response_mask(messages: Iterable[Mapping[str, Any]], tokenizer: Any | None = None,
                  add_special_tokens: bool = False) -> Any:
    """Build assistant-only labels.

    With a tokenizer, returns ``input_ids`` and ``labels`` where non-assistant
    tokens are -100. Without one, returns the message-level boolean mask.
    """
    rows = list(messages)
    if tokenizer is None: return assistant_message_mask(rows)
    input_ids: list[int] = []; labels: list[int] = []
    for message in rows:
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(str(x.get("text", x)) if isinstance(x, Mapping) else str(x) for x in content)
        ids = tokenizer.encode(str(content), add_special_tokens=add_special_tokens)
        input_ids.extend(ids)
        labels.extend(ids if message.get("role") == "assistant" else [-100] * len(ids))
    return {"input_ids": input_ids, "labels": labels, "loss_mask": [x != -100 for x in labels]}


def build_sft_labels(messages: Iterable[Mapping[str, Any]], tokenizer: Any) -> dict[str, Any]:
    return response_mask(messages, tokenizer=tokenizer)
