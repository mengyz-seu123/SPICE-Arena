from __future__ import annotations
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from .storage import Trace, seal
from .tasks import AGENT_ROOT


def command(client, executable, model, local, mcp_root, task, max_evals, workspace):
    server = {'command': sys.executable, 'args': [str(AGENT_ROOT / 'trace.py'), 'mcp', '--task', task, '--out', str(mcp_root), '--max-evals', str(max_evals)]}
    if client == 'codex':
        cmd = [executable, 'exec', '--json', '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C', str(workspace),
               '-c', 'mcp_servers.analog_arena.command=' + json.dumps(server['command']),
               '-c', 'mcp_servers.analog_arena.args=' + json.dumps(server['args']),
               '-c', 'mcp_servers.analog_arena.tool_timeout_sec=1000']
        if local:
            cmd += ['--oss', '--local-provider', 'ollama']
        if model:
            cmd += ['--model', model]
        return cmd + ['-']
    cmd = [executable, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
           '--mcp-config', json.dumps({'mcpServers': {'analog_arena': server}}), '--strict-mcp-config',
           '--allowedTools', 'mcp__analog_arena__task_context,mcp__analog_arena__doctor,mcp__analog_arena__evaluate_candidate,mcp__analog_arena__read_artifact,mcp__analog_arena__list_evaluations,mcp__analog_arena__prepare_candidate,mcp__analog_arena__lint_candidate']
    if model:
        cmd += ['--model', model]
    return cmd


def stop_process(proc):
    if proc.poll() is not None:
        return
    if os.name == 'posix':
        os.killpg(proc.pid, signal.SIGTERM)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()


def run_native(out, client, task, model=None, local=False, max_evals=8, timeout_s=1800, executable=None, vllm_base_url='http://127.0.0.1:8000/v1'):
    exe = executable or shutil.which(client)
    if not exe:
        raise ValueError(f'{client} CLI not installed or not on PATH')
    secrets = [v for k, v in os.environ.items() if any(x in k.upper() for x in ['API_KEY', 'TOKEN', 'SECRET'])]
    trace = Trace(out, secrets=secrets)
    workspace = trace.root / 'workspace'
    workspace.mkdir()
    prompt = f'''Work on circuit task {task} using the analog_arena MCP tools. First call doctor
and task_context to read the latest exact specification and starting circuit.
For curriculum INV/OTA read the topology template and independent numeric device examples.
Choose every W/L/M and OTA bias explicitly: prepare_candidate has no default values.
You may add/remove devices or change topology; use lint_candidate for freely edited raw netlists;
rejected preflight submissions do not consume candidate budget. Submit accepted
candidates with evaluate_candidate, inspect evidence with read_artifact. At most
{max_evals} unique candidates. Do not edit evaluator or fixtures. Use MCP for every
simulation. Summarize the best SINGLE measured candidate, validities and failures.
Report execution validity, functional validity and full performance separately.
Do not claim success unless every required metric and functional check passes for the same candidate. Stop if the environment is missing.
'''
    trace.text('prompt.txt', prompt)
    proxy = None
    child_env = None
    if client == 'claude' and local:
        from .vllm_proxy import VLLMProxy
        proxy = VLLMProxy(vllm_base_url, model).start()
        child_env = os.environ.copy()
        child_env.update({'ANTHROPIC_BASE_URL': proxy.base_url, 'ANTHROPIC_API_KEY': 'local-vllm',
                          'ANTHROPIC_MODEL': model or 'local-model'})
    cmd = command(client, exe, model, local and client == 'codex', trace.root / 'mcp', task, max_evals, workspace)
    trace.json('command.json', {'argv': cmd, 'cwd': str(workspace), 'stdin_file': 'prompt.txt'})
    trace.emit('native.started', client=client, model=model, local=local, argv=cmd)
    start = time.monotonic()
    proc = None
    reader_threads = []
    status, error, failures = 'failed', None, []
    try:
        proc = subprocess.Popen(cmd, cwd=workspace, env=child_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', start_new_session=os.name == 'posix')
        events = queue.Queue()
        def reader(pipe, channel):
            try:
                for line in pipe:
                    with (trace.root / f'client-{channel}.jsonl').open('a', encoding='utf-8') as f:
                        f.write(trace.redactor.text(line))
                        f.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        event = {'text': line.rstrip('\n')}
                    if not isinstance(event, dict):
                        event = {'value': event}
                    if event.get('type') in {'error', 'turn.failed'} or event.get('is_error') is True:
                        failures.append(event)
                    trace.emit('native.' + channel, event=event)
            finally:
                events.put((channel, None))
                pipe.close()
        for pipe, channel in [(proc.stdout, 'stdout'), (proc.stderr, 'stderr')]:
            thread = threading.Thread(target=reader, args=(pipe, channel), daemon=True)
            reader_threads.append(thread)
            thread.start()
        proc.stdin.write(prompt)
        proc.stdin.close()
        closed = set()
        while len(closed) < 2:
            if time.monotonic() - start > timeout_s:
                raise TimeoutError('Native CLI wall-clock budget exceeded')
            try:
                channel, line = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                closed.add(channel)
                continue
        code = proc.wait(timeout=5)
        status = 'completed' if code == 0 and not failures else 'failed'
    except KeyboardInterrupt:
        status, error = 'interrupted', 'KeyboardInterrupt'
    except Exception as e:
        status, error = 'failed', str(e)
    finally:
        if proc:
            stop_process(proc)
        if proxy:
            proxy.close()
        for thread in reader_threads:
            thread.join(timeout=5)
        reports = [json.loads(p.read_text()) for p in sorted((trace.root / 'mcp/evaluations').glob('*/assessment.json'))]
        # Normalize Claude/Codex JSONL into the same trainable schema used by
        # direct API runs.  Raw client output remains retained for audit.
        try:
            from .trajectory import load_native_jsonl
            stdout_file = trace.root / 'client-stdout.jsonl'
            policy_version = os.environ.get('ANALOG_POLICY_VERSION')
            if not policy_version:
                import hashlib
                policy_version = hashlib.sha256((model or client).encode()).hexdigest()
            trajectory = load_native_jsonl(stdout_file, model=model,
                                           policy_version=policy_version)
            if not any(m.get('role') == 'user' for m in trajectory.get('messages', [])):
                trajectory['messages'].insert(0, {'role': 'user', 'content': prompt})
            if not trajectory.get('model'):
                trajectory['model'] = model or client
            trajectory['evaluations'] = reports
            # Publish the same public fields consumed by RL/SFT builders as
            # direct API runs. Native clients do not expose the runner state,
            # so derive verifier and measurement fields from sealed MCP files.
            task_data = json.loads((trace.root / 'task.json').read_text())
            contract_data = json.loads((trace.root / 'contract.json').read_text())
            trajectory['task'] = task_data['task']
            trajectory['policy'] = {'client': client, 'model': model or client,
                                    'local': local}
            trajectory['verifier'] = {'contract': contract_data, 'task': task_data}
            trajectory['prompt_messages'] = [
                {'role': 'user', 'content': prompt}]
            trajectory['completion'] = list(trajectory.get('messages', []))
            if trajectory['completion'] and trajectory['completion'][0].get('role') == 'user':
                trajectory['completion'] = trajectory['completion'][1:]
            if reports:
                passed = [r for r in reports if r.get('assessment', {}).get('passed')]
                trajectory['reward'] = float(bool(passed)) + sum(
                    1 for r in (passed[-1:] if passed else reports)
                    for c in r.get('assessment', {}).get('checks', {}).values()
                    if c.get('valid')) / 11
                best = max(reports, key=lambda r: sum(bool(c.get('valid')) for c in r.get('assessment', {}).get('checks', {}).values()))
                checks = best.get('assessment', {}).get('checks', {})
                trajectory['valid_metrics'] = sum(bool(c.get('valid')) for c in checks.values())
                trajectory['passed_metrics'] = sum(bool(c.get('passed')) for c in checks.values())
                trajectory['passed'] = bool(best.get('assessment', {}).get('passed'))
            else:
                trajectory['valid_metrics'] = trajectory['passed_metrics'] = 0
                trajectory['passed'] = False
            trace.json('trajectory.json', trajectory)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            trace.emit('trajectory.failed', error=str(exc))
        summary = {'status': status, 'error': error, 'client_errors': failures, 'client': client, 'task': task,
                   'returncode': proc.returncode if proc else None, 'elapsed_s': time.monotonic() - start,
                   'evaluations': len(reports), 'passed': any(r['assessment']['passed'] for r in reports),
                   'trace_scope': 'Emitted CLI events after credential redaction; hidden prompts/internal reasoning are not available.'}
        trace.json('summary.json', summary)
        trace.emit('native.finished', **summary)
        seal(trace)
    return summary
