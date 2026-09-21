from __future__ import annotations
from dataclasses import dataclass, field, asdict
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlparse
import uuid
import requests


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderConfig:
    base_url: str = 'https://api.b.ai/v1'
    model: str = 'qwen3.8-flash'
    api_key_env: str = 'BAI_API_KEY'
    dotenv_files: list[str] = field(default_factory=list)
    dotenv_keys: list[str] = field(default_factory=list)
    temperature: float = 1.0
    max_tokens: int = 4096
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 120.0
    request_timeout_s: float = 600.0
    retries: int = 2
    extra_body: dict = field(default_factory=dict)
    tool_choice: str | dict | None = 'auto'
    send_reasoning_back: bool = False
    accept_length_with_content: bool = False
    policy_version: str | None = None

    @classmethod
    def load(cls, path, model=None, base_url=None):
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        if model:
            raw['model'] = model
        if base_url:
            raw['base_url'] = base_url
        config = cls(**raw)
        config.validate()
        return config

    def validate(self):
        parsed = urlparse(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('base_url must not contain credentials, query or fragment')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('Use HTTPS for remote APIs; HTTP is supported on loopback only')
        if not parsed.hostname or not self.model:
            raise ValueError('base_url and model are required')
        for name in ['temperature', 'connect_timeout_s', 'read_timeout_s', 'request_timeout_s']:
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or (name != 'temperature' and v == 0):
                raise ValueError('invalid ' + name)
        if type(self.max_tokens) is not int or self.max_tokens <= 0 or type(self.retries) is not int or not 0 <= self.retries <= 5:
            raise ValueError('invalid max_tokens or retries')
        if not isinstance(self.extra_body, dict) or set(self.extra_body) & {'model', 'messages', 'stream', 'tools', 'tool_choice', 'n', 'max_tokens', 'temperature'}:
            raise ValueError('extra_body may not override protocol or budget fields')
        if self.tool_choice is not None and not isinstance(self.tool_choice, (str, dict)):
            raise ValueError('tool_choice must be a string, object or null')
        if type(self.send_reasoning_back) is not bool:
            raise ValueError('send_reasoning_back must be a boolean')
        if type(self.accept_length_with_content) is not bool:
            raise ValueError('accept_length_with_content must be a boolean')
        if self.policy_version is not None and (not isinstance(self.policy_version, str) or not self.policy_version):
            raise ValueError('policy_version must be a nonempty string or null')
        if not all(isinstance(v, str) and v for v in self.dotenv_files + self.dotenv_keys):
            raise ValueError('dotenv files and keys must be nonempty strings')

    def key(self):
        key = os.environ.get(self.api_key_env, '') if self.api_key_env else ''
        if not key:
            wanted = {self.api_key_env.lower(), *(name.lower() for name in self.dotenv_keys)}
            for filename in self.dotenv_files:
                path = Path(filename)
                if not path.is_file():
                    continue
                for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
                    if '=' not in line or line.lstrip().startswith('#'):
                        continue
                    name, value = line.split('=', 1)
                    if name.strip().lower() in wanted:
                        key = value.strip().strip('"').strip("'")
                        break
                if key:
                    break
        if self.api_key_env and not key:
            raise ValueError('Set environment variable ' + self.api_key_env)
        return key

    def snapshot(self):
        return asdict(self)


class Assembler:
    """Collect fragmented function calls and provider-exposed reasoning independently."""
    def __init__(self):
        self.content = ''
        self.reasoning = []
        self.calls = {}
        self.finish = None
        self.usage = None
        self.model = None

    def add(self, obj):
        if not isinstance(obj, dict):
            raise ProviderError('Response event must be an object')
        if obj.get('error'):
            raise ProviderError('Provider error: ' + json.dumps(obj['error'], ensure_ascii=False))
        if obj.get('usage') is not None:
            self.usage = obj['usage']
        if obj.get('model'):
            self.model = obj['model']
        for choice in obj.get('choices', []):
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta', choice.get('message', {})) or {}
            content = delta.get('content')
            if isinstance(content, str):
                self.content += content
            elif content is not None:
                raise ProviderError('Only textual assistant content is supported; raw event was retained')
            for key in ['reasoning_content', 'reasoning', 'thinking', 'reasoning_details']:
                if delta.get(key) not in (None, '', []):
                    self.reasoning.append({'field': key, 'value': delta[key]})
            for fragment in delta.get('tool_calls', []) or []:
                index = fragment.get('index', 0)
                call = self.calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                # SiliconFlow emits null on continuation chunks after the
                # initial function-typed tool-call fragment.
                if fragment.get('type') not in {None, 'function'}:
                    raise ProviderError('Unsupported tool type')
                if fragment.get('id'):
                    # Some providers repeat the complete ID in every chunk.
                    if not call['id']:
                        call['id'] = fragment['id']
                    elif fragment['id'] != call['id']:
                        call['id'] += fragment['id']
                fn = fragment.get('function') or {}
                for key in ['name', 'arguments']:
                    if fn.get(key):
                        call['function'][key] += fn[key]
            if choice.get('finish_reason') is not None:
                self.finish = choice['finish_reason']

    def result(self):
        if self.finish not in {'stop', 'tool_calls', 'function_call'}:
            raise ProviderError('Incomplete or rejected generation: finish_reason=' + str(self.finish))
        calls = [self.calls[k] for k in sorted(self.calls)]
        if self.finish == 'tool_calls' and not calls:
            raise ProviderError('tool_calls finish without any tool call')
        ids = set()
        for call in calls:
            if not call['id'] or not call['function']['name'] or call['id'] in ids:
                raise ProviderError('Missing/duplicate tool ID or empty function name')
            ids.add(call['id'])
            try:
                args = json.loads(call['function']['arguments'])
                if not isinstance(args, dict):
                    raise ValueError('arguments must be an object')
            except ValueError as e:
                raise ProviderError('Malformed tool arguments; no tools were executed') from e
        message = {'role': 'assistant', 'content': self.content or None}
        if calls:
            message['tool_calls'] = calls
        return {'message': message, 'reasoning': self.reasoning, 'usage': self.usage,
                'returned_model': self.model, 'finish_reason': self.finish,
                'reasoning_available': bool(self.reasoning)}


def sse_payloads(lines, record):
    """SSE framing including multi-line data, comments, CRLF and final EOF frame."""
    data = []
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', errors='strict')
        record(raw)
        line = raw.rstrip('\r')
        if line.startswith('\ufeff'):
            line = line[1:]
        if not line:
            if data:
                yield '\n'.join(data)
                data = []
            continue
        if line.startswith(':'):
            continue
        key, _, value = line.partition(':')
        if key == 'data':
            data.append(value[1:] if value.startswith(' ') else value)
    if data:
        yield '\n'.join(data)


class ChatProvider:
    def __init__(self, config, trace, session=None):
        config.validate()
        self.config, self.trace = config, trace
        self.session = session or requests.Session()

    def complete(self, messages, tools=None):
        c = self.config
        key = c.key()
        request_id = uuid.uuid4().hex
        body = {'model': c.model, 'messages': messages, 'stream': True,
                'temperature': c.temperature, 'max_tokens': c.max_tokens, **c.extra_body}
        if tools:
            body['tools'] = tools
            if c.tool_choice is not None:
                body['tool_choice'] = c.tool_choice
        headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        url = c.base_url.rstrip('/') + '/chat/completions'
        self.trace.json(f'api/{request_id}/request.json', body)
        self.trace.emit('provider.request', request_id=request_id, url=url, model=c.model,
                        message_count=len(messages), request_file=f'api/{request_id}/request.json')
        started = time.monotonic()
        response = None
        try:
            for attempt in range(c.retries + 1):
                self.trace.emit('provider.attempt', request_id=request_id, attempt=attempt + 1)
                response = self.session.post(url, headers=headers, json=body, stream=True,
                                             timeout=(c.connect_timeout_s, c.read_timeout_s), allow_redirects=False)
                self.trace.emit('provider.http', request_id=request_id, status=response.status_code,
                                headers={k: v for k, v in response.headers.items() if k.lower() in {'content-type', 'x-request-id', 'request-id', 'retry-after'}})
                if response.status_code == 429 or response.status_code >= 500:
                    error = response.text
                    self.trace.emit('provider.http_error', request_id=request_id, body=error)
                    response.close()
                    if attempt < c.retries:
                        delay = min(2 ** attempt, 8)
                        self.trace.emit('provider.retry', request_id=request_id, delay_s=delay)
                        time.sleep(delay)
                        continue
                if response.status_code != 200:
                    error = response.text
                    self.trace.text(f'api/{request_id}/http-error.txt', error)
                    raise ProviderError(f'HTTP {response.status_code}: {error}')
                break
            assembler = Assembler()
            response.encoding = 'utf-8'
            if 'application/json' in response.headers.get('Content-Type', '').lower():
                raw = response.text
                self.trace.text(f'api/{request_id}/response.raw.json', raw)
                self.trace.emit('provider.json', request_id=request_id,
                                raw_response_file=f'api/{request_id}/response.raw.json')
                assembler.add(json.loads(raw))
            else:
                raw_path = self.trace.root / f'api/{request_id}/response.sse'
                def record(line):
                    if time.monotonic() - started > c.request_timeout_s:
                        raise ProviderError('Request wall-clock budget exceeded')
                    with raw_path.open('a', encoding='utf-8', newline='\n') as f:
                        f.write(self.trace.redactor.text(line) + '\n')
                        f.flush()
                for payload in sse_payloads(response.iter_lines(chunk_size=1), record):
                    if payload.strip() == '[DONE]':
                        break
                    obj = json.loads(payload)
                    assembler.add(obj)
            if c.accept_length_with_content and assembler.finish == 'length' and assembler.content and not assembler.calls:
                assembler.finish = 'stop'
            result = assembler.result()
            if not result['message'].get('content') and not result['message'].get('tool_calls'):
                raise ProviderError('No usable content or tool calls returned')
            result.update(request_id=request_id, elapsed_s=time.monotonic() - started)
            self.trace.json(f'api/{request_id}/response.json', result)
            self.trace.emit('provider.completed', request_id=request_id,
                            response_file=f'api/{request_id}/response.json',
                            returned_model=result['returned_model'], usage=result['usage'],
                            finish_reason=result['finish_reason'], elapsed_s=result['elapsed_s'],
                            reasoning_available=result['reasoning_available'],
                            reasoning_fragments=len(result['reasoning']),
                            tool_names=[call['function']['name'] for call in result['message'].get('tool_calls', [])])
            return result
        except Exception as e:
            self.trace.emit('provider.failed', request_id=request_id, error_type=type(e).__name__, error=str(e), elapsed_s=time.monotonic() - started)
            if isinstance(e, ProviderError):
                raise
            raise ProviderError(str(e)) from e
        finally:
            if response is not None:
                response.close()
