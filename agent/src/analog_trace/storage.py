from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def text(self, text):
        for secret in self.secrets:
            text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}', '[REDACTED_API_KEY]', text)
        text = re.sub(r'(?i)Bearer\s+[^\s"\\]+', 'Bearer [REDACTED]', text)
        return text

    def value(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, list):
            return [self.value(x) for x in obj]
        if isinstance(obj, dict):
            return {k: '[REDACTED]' if k.lower() in {'authorization', 'api_key', 'apikey', 'access_token', 'password'} else self.value(v) for k, v in obj.items()}
        return obj


def write_json(path, obj, redactor=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    payload = redactor.value(obj) if redactor else obj
    with temp.open('w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


class Trace:
    """Append-only, fsynced JSONL. Hash chaining detects accidental changes, not forgery."""
    def __init__(self, root, secrets=(), existing=False):
        self.root = Path(root).resolve()
        self.redactor = Redactor(secrets)
        self.lock = threading.Lock()
        if existing:
            audit = verify(self.root)
            if not audit['ok']:
                raise ValueError('Trace verification failed: ' + str(audit))
            self.seq, self.previous = audit['events'], audit['head']
        else:
            self.root.mkdir(parents=True, exist_ok=False)
            self.seq, self.previous = 0, '0' * 64
        self.path = self.root / 'events.jsonl'

    def emit(self, kind, **data):
        with self.lock:
            row = {'seq': self.seq + 1, 'time': now(), 'type': kind,
                   'prev_sha256': self.previous, 'data': self.redactor.value(data)}
            head = hashlib.sha256(canonical(row).encode()).hexdigest()
            row['sha256'] = head
            with self.path.open('a', encoding='utf-8', newline='\n') as f:
                f.write(canonical(row) + '\n')
                f.flush()
                os.fsync(f.fileno())
            self.seq += 1
            self.previous = head
            return row

    def json(self, relative, obj):
        write_json(self.root / relative, obj, self.redactor)

    def text(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.redactor.text(text), encoding='utf-8')


def verify(root):
    root = Path(root)
    previous, count, sealed_hash = '0' * 64, 0, None
    try:
        with (root / 'events.jsonl').open(encoding='utf-8') as f:
            for line in f:
                row = json.loads(line)
                sha = row.pop('sha256')
                if row['seq'] != count + 1 or row['prev_sha256'] != previous:
                    raise ValueError('sequence or previous hash mismatch')
                if hashlib.sha256(canonical(row).encode()).hexdigest() != sha:
                    raise ValueError('event hash mismatch')
                previous, count = sha, count + 1
                if row['type'] == 'run.sealed':
                    sealed_hash = row['data']['artifacts_sha256']
                else:
                    sealed_hash = None
        manifest = root / 'artifacts.sha256.json'
        if sealed_hash is not None and (not manifest.is_file() or digest(manifest) != sealed_hash):
            raise ValueError('sealed artifact manifest is missing or changed')
        if manifest.is_file():
            for name, expected in json.loads(manifest.read_text()).items():
                p = (root / name).resolve()
                if not p.is_relative_to(root.resolve()) or digest(p) != expected:
                    raise ValueError('artifact hash mismatch: ' + name)
        return {'ok': True, 'events': count, 'head': previous,
                'sealed': sealed_hash is not None,
                'artifacts_checked': sealed_hash is not None and manifest.is_file()}
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {'ok': False, 'events': count, 'error': str(e)}


def seal(trace):
    files = {}
    for path in sorted(trace.root.rglob('*')):
        if path.is_file() and path.relative_to(trace.root).as_posix() not in {'artifacts.sha256.json', 'events.jsonl'}:
            files[path.relative_to(trace.root).as_posix()] = digest(path)
    trace.json('artifacts.sha256.json', files)
    trace.emit('run.sealed', artifacts_sha256=digest(trace.root / 'artifacts.sha256.json'), files=len(files))
