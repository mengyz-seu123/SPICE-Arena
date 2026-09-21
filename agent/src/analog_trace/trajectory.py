"""Canonical trajectories and adapters for native Claude/Codex traces."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "analog-trace/trajectory/v1"
_REQUIRED_FIELDS = {
    "schema", "messages", "tool_calls", "tool_results", "evaluations",
    "reward", "model", "policy_version",
}
_MESSAGE_ROLES = {"system", "user", "assistant", "tool"}


@dataclass
class Trajectory:
    """The stable, provider-independent record used by SFT and RL tooling."""

    messages: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    evaluations: list[dict] = field(default_factory=list)
    reward: float = 0.0
    model: str = ""
    policy_version: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        # Metadata is deliberately nested so it cannot overwrite schema fields.
        return {
            "schema": SCHEMA,
            "messages": self.messages,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "evaluations": self.evaluations,
            "reward": self.reward,
            "model": self.model,
            "policy_version": self.policy_version,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Trajectory":
        if not isinstance(value, Mapping):
            raise TypeError("Trajectory must be a mapping")
        metadata = value.get("metadata", {})
        return cls(
            messages=list(value.get("messages", [])),
            tool_calls=list(value.get("tool_calls", [])),
            tool_results=list(value.get("tool_results", [])),
            evaluations=list(value.get("evaluations", [])),
            reward=value.get("reward", 0.0),
            model=value.get("model", ""),
            policy_version=value.get("policy_version", ""),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )


def _text(value: Any) -> str:
    """Convert provider content blocks into a deterministic text observation."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    if isinstance(value, Mapping):
        if isinstance(value.get("text"), str):
            return value["text"]
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _identifier(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_call(call: Mapping[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    source = function if isinstance(function, Mapping) else call
    return {
        "id": _identifier(call.get("id") or call.get("tool_use_id") or
                          call.get("call_id") or source.get("id")),
        "name": source.get("name", call.get("name", "")),
        "arguments": source.get("arguments", call.get("arguments", call.get("input", {}))),
    }


def from_events(
    events: Iterable[Mapping[str, Any]], *, model: str = "", policy_version: str = "",
    evaluations: Iterable[Mapping[str, Any]] | None = None, reward: float = 0.0,
    prompt: str | None = None,
) -> dict:
    """Normalize Claude stream-json, Codex JSONL, and MCP tool events.

    Claude Code emits a complete ``assistant.message`` event after optional partial
    stream events.  Only complete messages are consumed, which prevents
    ``--include-partial-messages`` from duplicating actions.  Tool observations are
    represented once as both a canonical ``tool_results`` row and a ``tool`` message.
    """
    messages: list[dict] = []
    calls: list[dict] = []
    results: list[dict] = []
    calls_by_id: dict[str, dict] = {}
    results_by_id: dict[str, dict] = {}
    selected_model = model if isinstance(model, str) else ""
    capture_errors: list[str] = []
    terminal_seen = False
    partial_open = False
    mcp_requests: dict[str, str] = {}
    seen_messages: set[str] = set()

    def add_call(raw: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        normalized = _normalize_call(raw)
        call_id = normalized["id"]
        if call_id and call_id in calls_by_id:
            if normalized != calls_by_id[call_id]:
                capture_errors.append(f"conflicting duplicate tool call: {call_id}")
            return calls_by_id[call_id], False
        calls.append(normalized)
        if call_id:
            calls_by_id[call_id] = normalized
        return normalized, True

    def add_result(call_id: Any, content: Any, is_error: Any = False) -> tuple[dict[str, Any], bool]:
        normalized_id = _identifier(call_id)
        if normalized_id and normalized_id in results_by_id:
            old = results_by_id[normalized_id]
            if old["content"] != content or old["is_error"] != bool(is_error):
                capture_errors.append(f"conflicting duplicate tool result: {normalized_id}")
            return old, False
        result = {
            "tool_call_id": normalized_id,
            "content": content,
            "is_error": bool(is_error),
        }
        results.append(result)
        if normalized_id:
            results_by_id[normalized_id] = result
        messages.append({"role": "tool", "tool_call_id": normalized_id, "content": _text(content)})
        return result, True

    def add_message(raw: Mapping[str, Any]) -> None:
        nonlocal selected_model
        role = raw.get("role")
        if role not in _MESSAGE_ROLES:
            return
        if raw.get("id"):
            key = json.dumps([raw["id"], raw], sort_keys=True, ensure_ascii=False)
            if key in seen_messages:
                return
            seen_messages.add(key)
        if not selected_model and isinstance(raw.get("model"), str):
            selected_model = raw["model"]
        content = raw.get("content", "")
        if role == "tool":
            add_result(raw.get("tool_call_id") or raw.get("tool_use_id") or raw.get("id"), content,
                       raw.get("is_error", False))
            return

        text_parts: list[str] = []
        message_calls: list[dict] = []
        saw_tool_result = False
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, Mapping):
                    text_parts.append(_text(block))
                    continue
                block_type = block.get("type")
                if block_type in {"text", "output_text"}:
                    text_parts.append(_text(block.get("text", "")))
                elif block_type in {"tool_use", "tool_call"}:
                    call, added = add_call(block)
                    if added:
                        message_calls.append(call)
                elif block_type in {"tool_result", "tool_result_message"}:
                    saw_tool_result = True
                    add_result(block.get("tool_use_id") or block.get("tool_call_id") or block.get("id"),
                               block.get("content", block.get("output", "")), block.get("is_error", False))
                elif block_type in {"thinking", "redacted_thinking", "reasoning", "signature"}:
                    # Provider reasoning/signatures are audit metadata, not visible actions.
                    continue
                else:
                    capture_errors.append(f"unsupported {role} content block: {block_type}")
            normalized_content = "".join(text_parts)
        else:
            normalized_content = _text(content)

        # OpenAI-compatible tool calls live beside content rather than inside it.
        for call in raw.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            normalized, added = add_call(call)
            if added and normalized not in message_calls:
                message_calls.append(normalized)

        # Claude represents a tool result as a user message.  Its canonical
        # equivalent is the synthetic tool row above; retain user text only.
        if role == "user" and saw_tool_result and not normalized_content:
            return
        message: dict[str, Any] = {"role": role, "content": normalized_content}
        if role == "assistant" and message_calls:
            message["tool_calls"] = message_calls
        messages.append(message)

    def add_direct_tool_call(raw: Mapping[str, Any]) -> None:
        call, added = add_call(raw)
        if added:
            messages.append({"role": "assistant", "content": "", "tool_calls": [call]})

    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            continue
        payload = raw_event.get("data", raw_event)
        event = payload.get("event", payload) if isinstance(payload, Mapping) else raw_event
        if not isinstance(event, Mapping):
            continue
        if not selected_model and isinstance(event.get("model"), str):
            selected_model = event["model"]

        event_type = event.get("type")
        if event_type == "message_start":
            partial_open = True
            continue
        if event_type == "message_stop":
            partial_open = False
            continue
        if event_type in {"result", "turn.completed"}:
            terminal_seen = True
            if event.get("is_error") or event.get("subtype", "success") != "success":
                capture_errors.append("native client reported a failed terminal result")
            continue
        if event_type in {"error", "turn.failed"}:
            capture_errors.append("native client reported an error")
            continue
        # Standalone MCP logs are correlated by the JSON-RPC request ID. Never
        # merge these transport IDs into a Claude stream that already has tool IDs.
        rpc = payload.get("request", payload.get("response", event)) if isinstance(payload, Mapping) else event
        if isinstance(rpc, Mapping) and rpc.get("method") == "tools/call":
            request_id = str(rpc.get("id", ""))
            params = rpc.get("params", {})
            if request_id and isinstance(params, Mapping):
                call_id = "mcp:" + request_id
                mcp_requests[request_id] = call_id
                add_direct_tool_call({"id": call_id, **params})
            continue
        if isinstance(rpc, Mapping) and str(rpc.get("id", "")) in mcp_requests and ("result" in rpc or "error" in rpc):
            result = rpc.get("result", rpc.get("error", {}))
            add_result(mcp_requests[str(rpc["id"])], result.get("content", result),
                       "error" in rpc or result.get("isError", False))
            continue

        # Codex non-interactive JSONL represents actions as completed items.
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, Mapping):
            item_type = item.get("type")
            if item_type in {"agent_message", "message"}:
                add_message({"role": "assistant", "content": item.get("text", item.get("content", ""))})
            elif item_type in {"function_call", "tool_call"}:
                add_direct_tool_call(item)
            elif item_type in {"function_call_output", "tool_result"}:
                add_result(item.get("call_id") or item.get("tool_call_id") or item.get("id"),
                           item.get("output", item.get("content", "")), item.get("is_error", False))
            continue

        message = event.get("message")
        if isinstance(message, Mapping) and message.get("role") in _MESSAGE_ROLES:
            add_message(message)
            continue

        # OpenAI-compatible complete assistant/tool records and direct MCP events.
        if event.get("role") in _MESSAGE_ROLES:
            add_message(event)
            continue
        event_type = event.get("type")
        if event_type in {"assistant", "assistant_message"} and isinstance(event.get("content"), (str, list)):
            add_message({"role": "assistant", "content": event["content"],
                         "tool_calls": event.get("tool_calls", [])})
        elif event_type in {"tool_use", "tool_call"} or (
            isinstance(event.get("name"), str) and ("input" in event or "arguments" in event)
        ):
            add_direct_tool_call(event)
        elif event_type in {"tool_result", "tool_result_message"} or event.get("tool_result") is not None:
            add_result(event.get("tool_use_id") or event.get("tool_call_id") or event.get("id"),
                       event.get("content", event.get("tool_result")), event.get("is_error", False))

    if prompt is not None:
        first_user = next((m for m in messages if m.get("role") != "system"), None)
        if first_user is None or first_user.get("role") != "user":
            messages.insert(0, {"role": "user", "content": prompt})
        elif first_user.get("content") != prompt:
            capture_errors.append("captured initial user message differs from the supplied prompt")
    if partial_open:
        capture_errors.append("native stream ended before message_stop")
    return Trajectory(
        messages=messages,
        tool_calls=calls,
        tool_results=results,
        evaluations=list(evaluations or []),
        reward=reward,
        model=selected_model,
        policy_version=policy_version if isinstance(policy_version, str) else "",
        metadata={"capture_errors": capture_errors, "native_terminal_seen": terminal_seen},
    ).to_dict()


def _read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    """Retain usable rows and record corruption so it cannot become training data."""
    rows: list[dict] = []
    errors: list[str] = []
    with path.open(encoding="utf-8-sig", errors="replace") as stream:
        for index, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                if "\ufffd" in line:
                    raise ValueError("invalid UTF-8")
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise ValueError("event is not a JSON object")
                rows.append(dict(value))
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{path.name}:{index}: {exc}")
    return rows, errors


def load_native_jsonl(path: str | Path, **kwargs: Any) -> dict:
    events, errors = _read_jsonl(Path(path))
    trajectory = from_events(events, **kwargs)
    trajectory["metadata"]["capture_errors"].extend(errors)
    return trajectory


def load_native_run(root: str | Path) -> dict:
    root = Path(root)
    normalized = root / "trajectory.json"
    if normalized.is_file():
        value = json.loads(normalized.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("trajectory.json must be an object")
        return dict(value)
    summary_path = root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    source = root / "client-stdout.jsonl"
    if not source.is_file():
        source = root / "events.jsonl"
    prompt_path = root / "prompt.txt"
    return load_native_jsonl(
        source, model=summary.get("model") or summary.get("returned_model") or "",
        policy_version=summary.get("policy_version") or "", reward=summary.get("reward", 0.0),
        prompt=prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else None,
    )


def validate_trajectory(value: Mapping[str, Any], require_complete: bool = True) -> dict:
    """Validate structural consistency and report whether a trace can train a policy.

    ``require_complete=False`` is useful while a native stream is still in
    progress.  Such a trace is never reported as trainable until every action has
    its matching observation.
    """
    if not isinstance(value, Mapping):
        return {
            "valid": False, "complete": False, "trainable": False,
            "errors": ["trajectory must be a mapping"], "training_errors": [],
            "tool_calls": 0, "tool_results": 0,
        }

    errors: list[str] = []
    training_errors: list[str] = []
    missing = _REQUIRED_FIELDS - set(value)
    if missing:
        errors.append("missing fields: " + ", ".join(sorted(missing)))
    if "schema" in value and value.get("schema") != SCHEMA:
        errors.append(f"unsupported schema: {value.get('schema')!r}")

    def rows(name: str) -> list[Any]:
        data = value.get(name, [])
        if not isinstance(data, list):
            errors.append(f"{name} must be a list")
            return []
        return data

    messages = rows("messages")
    calls = rows("tool_calls")
    results = rows("tool_results")
    rows("evaluations")
    if "metadata" in value and not isinstance(value["metadata"], Mapping):
        errors.append("metadata must be a mapping")

    call_ids: set[str] = set()
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            errors.append(f"tool_calls[{index}] must be a mapping")
            continue
        call_id = _identifier(call.get("id"))
        if not call_id:
            errors.append(f"tool_calls[{index}] has no id")
            continue
        if call_id in call_ids:
            errors.append(f"duplicate tool call id: {call_id}")
        call_ids.add(call_id)
        if not isinstance(call.get("name"), str) or not call["name"].strip():
            errors.append(f"tool_calls[{index}] has no name")

    result_ids: set[str] = set()
    for index, result in enumerate(results):
        if not isinstance(result, Mapping):
            errors.append(f"tool_results[{index}] must be a mapping")
            continue
        result_id = _identifier(result.get("tool_call_id"))
        if not result_id:
            errors.append(f"tool_results[{index}] has no tool_call_id")
            continue
        if result_id in result_ids:
            errors.append(f"duplicate tool result id: {result_id}")
        result_ids.add(result_id)
        if "content" not in result:
            errors.append(f"tool_results[{index}] has no content")
        if "is_error" in result and not isinstance(result["is_error"], bool):
            errors.append(f"tool_results[{index}] has non-boolean is_error")

    assistant_call_ids: set[str] = set()
    tool_message_ids: set[str] = set()
    roles: list[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            errors.append(f"messages[{index}] must be a mapping")
            continue
        role = message.get("role")
        if role not in _MESSAGE_ROLES:
            errors.append(f"messages[{index}] has invalid role")
            continue
        roles.append(role)
        if "content" not in message:
            errors.append(f"messages[{index}] has no content")
        if role == "assistant":
            message_calls = message.get("tool_calls", [])
            if not isinstance(message_calls, list):
                errors.append(f"messages[{index}].tool_calls must be a list")
                continue
            for call_index, call in enumerate(message_calls):
                if not isinstance(call, Mapping):
                    errors.append(f"messages[{index}].tool_calls[{call_index}] must be a mapping")
                    continue
                call_id = _identifier(call.get("id"))
                if not call_id:
                    errors.append(f"messages[{index}].tool_calls[{call_index}] has no id")
                    continue
                if call_id in assistant_call_ids:
                    errors.append(f"duplicate assistant tool call id: {call_id}")
                assistant_call_ids.add(call_id)
        elif role == "tool":
            result_id = _identifier(message.get("tool_call_id"))
            if not result_id:
                errors.append(f"messages[{index}] tool message has no tool_call_id")
                continue
            if result_id in tool_message_ids:
                errors.append(f"duplicate tool message id: {result_id}")
            tool_message_ids.add(result_id)

    missing_global = sorted(assistant_call_ids - call_ids)
    if missing_global:
        errors.append("assistant calls missing from tool_calls: " + ", ".join(missing_global))
    calls_without_assistant = sorted(call_ids - assistant_call_ids)
    if calls_without_assistant:
        errors.append("tool_calls missing from assistant messages: " + ", ".join(calls_without_assistant))
    orphan_results = sorted(result_ids - call_ids)
    if orphan_results:
        errors.append("tool results without calls: " + ", ".join(orphan_results))
    missing_tool_messages = sorted(result_ids - tool_message_ids)
    if missing_tool_messages:
        errors.append("tool results missing from messages: " + ", ".join(missing_tool_messages))
    tool_messages_without_results = sorted(tool_message_ids - result_ids)
    if tool_messages_without_results:
        errors.append("tool messages missing from tool_results: " + ", ".join(tool_messages_without_results))

    calls_without_results = sorted(call_ids - result_ids)
    complete = not calls_without_results
    if calls_without_results and require_complete:
        errors.append("tool calls without results: " + ", ".join(calls_without_results))
    if "assistant" not in roles:
        errors.append("no assistant message")
    if "user" not in roles:
        errors.append("no user message")

    reward = value.get("reward")
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        errors.append("reward is not numeric")
    elif not math.isfinite(float(reward)):
        errors.append("reward is non-finite")
    if not isinstance(value.get("model"), str):
        errors.append("model must be a string")
    if not isinstance(value.get("policy_version"), str):
        errors.append("policy_version must be a string")

    if not isinstance(value.get("model"), str) or not value.get("model").strip():
        training_errors.append("missing model")
    if not isinstance(value.get("policy_version"), str) or not value.get("policy_version").strip():
        training_errors.append("missing policy_version")
    if not complete:
        training_errors.append("tool calls are missing results")

    valid = not errors
    return {
        "valid": valid,
        "complete": complete,
        "trainable": valid and complete and not training_errors,
        "errors": errors,
        "training_errors": training_errors,
        "tool_calls": len(call_ids),
        "tool_results": len(result_ids),
    }


def validate_trace(path: str | Path) -> dict:
    path = Path(path)
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        value = load_native_run(path)
    return validate_trajectory(value)


class ClaudeNativeAdapter:
    """Incremental adapter retained for native integrations and dataset consumers."""

    def __init__(self, model: str = "", policy_version: str = ""):
        self.model = model
        self.policy_version = policy_version
        self.events: list[Mapping[str, Any]] = []

    def add(self, event: Mapping[str, Any] | str) -> "ClaudeNativeAdapter":
        if isinstance(event, str):
            event = json.loads(event)
        if not isinstance(event, Mapping):
            raise TypeError("native event must be a mapping or JSON object string")
        self.events.append(event)
        return self

    feed = add

    def result(self, **kwargs: Any) -> dict:
        settings = {"model": self.model, "policy_version": self.policy_version}
        settings.update(kwargs)
        return from_events(self.events, **settings)

    def finish(self, reward: float | None = None, evaluations: Iterable[Mapping[str, Any]] = ()) -> Trajectory:
        value = self.result(reward=0.0 if reward is None else reward, evaluations=evaluations)
        return Trajectory.from_dict(value)


def adapt_claude_stream(events: Iterable[Mapping[str, Any]], **kwargs: Any) -> dict:
    return from_events(events, **kwargs)


def assert_valid_trajectory(value: Mapping[str, Any], require_complete: bool = True) -> dict:
    report = validate_trajectory(value, require_complete=require_complete)
    if not report["valid"]:
        raise ValueError("Invalid trajectory: " + "; ".join(report["errors"]))
    return report
