"""Policy identity and checkpoint metadata helpers.

The training code may be backed by Transformers, a hosted API, or a local
server.  These helpers keep the identity of the policy independent of that
backend and make it safe to attach to every trajectory.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def policy_hash(model: str, checkpoint: str | os.PathLike | None = None, metadata: dict | None = None) -> str:
    """Return a stable hash for model/checkpoint identity."""
    payload = {'model': model or '', 'metadata': metadata or {}}
    if checkpoint:
        root = Path(checkpoint)
        payload['checkpoint'] = str(root.resolve())
        if root.is_file():
            payload['files'] = {root.name: _hash_file(root)}
        elif root.is_dir():
            payload['files'] = {str(p.relative_to(root)): _hash_file(p)
                                for p in sorted(root.rglob('*')) if p.is_file()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def make_metadata(model: str, checkpoint: str | os.PathLike | None = None,
                  policy_version: str | None = None, **extra) -> dict:
    version = policy_version or policy_hash(model, checkpoint, extra)
    return {'model': model, 'policy_version': version,
            'checkpoint': str(Path(checkpoint).resolve()) if checkpoint else None,
            'created_at': time.time(), **extra}


def save_metadata(checkpoint: str | os.PathLike, model: str, **kwargs) -> dict:
    root = Path(checkpoint)
    root.mkdir(parents=True, exist_ok=True)
    data = make_metadata(model, root, **kwargs)
    # Hashing the directory before writing metadata avoids recursive hashes.
    data['policy_version'] = policy_hash(model, root, {k: v for k, v in data.items() if k != 'policy_version'})
    (root / 'policy.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return data


def load_metadata(checkpoint: str | os.PathLike) -> dict:
    path = Path(checkpoint) / 'policy.json'
    if not path.is_file():
        raise FileNotFoundError(f'missing policy metadata: {path}')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not data.get('model') or not data.get('policy_version'):
        raise ValueError('policy.json requires model and policy_version')
    return data
