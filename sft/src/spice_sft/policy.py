"""Checkpoint identity for supervised training."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
SCHEMA_VERSION = 1
METADATA_FILE = "policy.json"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PolicyMetadata:
    model: str
    policy_version: str
    checkpoint_hash: str = ""
    tokenizer: str = ""
    trainer: str = ""
    step: int = 0
    parent_version: str | None = None
    schema_version: int = SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PolicyMetadata":
        required = {"model", "policy_version"}
        missing = required - set(data)
        if missing:
            raise ValueError(f"policy metadata missing: {', '.join(sorted(missing))}")
        fields = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
        return cls(**fields)


def checkpoint_hash(checkpoint: str | os.PathLike[str], *, exclude: set[str] | None = None) -> str:
    """Hash all checkpoint files deterministically (paths + bytes)."""
    root = Path(checkpoint)
    if root.is_file():
        return hashlib.sha256(root.read_bytes()).hexdigest()
    if not root.is_dir():
        raise FileNotFoundError(root)
    ignored = set(exclude or ()) | {METADATA_FILE}
    digest = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name not in ignored)
    for path in files:
        digest.update(str(path.relative_to(root)).replace(os.sep, "/").encode())
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def make_policy_metadata(model: str, checkpoint: str | os.PathLike[str] | None = None, *,
                         tokenizer: str = "", trainer: str = "", step: int = 0,
                         parent_version: str | None = None, **extra: Any) -> PolicyMetadata:
    chash = checkpoint_hash(checkpoint) if checkpoint is not None and Path(checkpoint).exists() else ""
    identity = fingerprint({"model": model, "checkpoint_hash": chash, "tokenizer": tokenizer,
                            "trainer": trainer, "step": step, "parent_version": parent_version, "extra": extra})
    return PolicyMetadata(model=model, policy_version=identity, checkpoint_hash=chash,
                          tokenizer=tokenizer, trainer=trainer, step=step,
                          parent_version=parent_version, extra=extra)


def save_policy_metadata(checkpoint: str | os.PathLike[str], metadata: PolicyMetadata | Mapping[str, Any]) -> Path:
    root = Path(checkpoint)
    root.mkdir(parents=True, exist_ok=True)
    if not isinstance(metadata, PolicyMetadata):
        metadata = PolicyMetadata.from_dict(metadata)
    path = root / METADATA_FILE
    # Atomic write prevents a partially written policy identity on interruption.
    fd, tmp = tempfile.mkstemp(prefix=".policy-", dir=str(root), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(metadata.to_dict(), stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path
