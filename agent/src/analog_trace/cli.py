from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from .tasks import AGENT_ROOT, assess, task_config, RELATION
from .storage import Trace, seal, verify
from .curriculum import TASK_IDS, RELATION as CURRICULUM_RELATION

ALL_TASK_IDS = list(TASK_IDS) + ['ota', 'inverter', 'sram6t', 'task1', 'task2']


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def parser():
    p = argparse.ArgumentParser(description='Circuit curriculum L1-L3, provider reasoning trace and independent evaluation')
    p.add_argument('--version', action='version', version='analog-arena-trace 1.0.0')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Check Python, ngspice, PDK and native clients')
    sub.add_parser('tasks', help='Print exact task thresholds and set relationship')
    for name in ['probe', 'run']:
        cmd = sub.add_parser(name, help='Test provider streaming' if name == 'probe' else 'Run the model/evaluator experiment loop')
        cmd.add_argument('--config', default=str(AGENT_ROOT / 'configs/bai.json'))
        cmd.add_argument('--model')
        cmd.add_argument('--base-url')
        cmd.add_argument('--out', required=True)
        if name == 'probe':
            cmd.add_argument('--prompt', default='Hello World')
            cmd.add_argument('--tools', action='store_true', help='Also test function-call output with a harmless echo tool')
        else:
            cmd.add_argument('--task', choices=ALL_TASK_IDS, required=True)
            cmd.add_argument('--max-turns', type=positive, default=12)
            cmd.add_argument('--max-evals', type=positive, default=8)
            cmd.add_argument('--prompt', default='')
    resume = sub.add_parser('resume', help='Resume failed/interrupted direct run without repeating completed tools')
    resume.add_argument('--run', required=True)
    ev = sub.add_parser('evaluate', help='Evaluate one local netlist and save trace/checks')
    ev.add_argument('--task', choices=ALL_TASK_IDS, required=True)
    ev.add_argument('--netlist', required=True)
    ev.add_argument('--ibias', type=float)
    ev.add_argument('--out', required=True)
    lint = sub.add_parser('lint', help='Static INV/OTA submission check; no simulation')
    lint.add_argument('--task', choices=[t for t in TASK_IDS if t.startswith(('INV-', 'OTA-'))], required=True)
    lint.add_argument('--netlist', required=True)
    lint.add_argument('--ibias', type=float)
    starter = sub.add_parser('starter', help='Show a topology template without values, or fill ALL explicit device parameters')
    starter.add_argument('--task', choices=[t for t in TASK_IDS if t.startswith(('INV-', 'OTA-'))], required=True)
    starter.add_argument('--parameters', help='JSON file mapping device names to W/L/M numbers')
    starter.add_argument('--ibias', type=float)
    sc = sub.add_parser('score', help='Offline strict checks; does not re-simulate or prove provenance')
    sc.add_argument('--input', required=True)
    sc.add_argument('--task', choices=ALL_TASK_IDS, required=True)
    for name in ['report', 'verify', 'export']:
        cmd = sub.add_parser(name)
        cmd.add_argument('--run', required=True)
    validate = sub.add_parser('validate', help='Validate a normalized trajectory or native trace')
    validate.add_argument('--input', required=True)
    native = sub.add_parser('native', help='Run installed Codex/Claude with MCP evaluator and capture emitted JSONL')
    native.add_argument('--client', choices=['codex', 'claude'], required=True)
    native.add_argument('--task', choices=ALL_TASK_IDS, required=True)
    native.add_argument('--model')
    native.add_argument('--local', action='store_true', help='Use local provider (Codex/Ollama or Claude/vLLM bridge)')
    native.add_argument('--vllm-base-url', default='http://127.0.0.1:8000/v1', help='vLLM OpenAI endpoint for Claude --local')
    native.add_argument('--executable')
    native.add_argument('--max-evals', type=positive, default=8)
    native.add_argument('--timeout', type=positive, default=1800)
    native.add_argument('--out', required=True)
    mcp = sub.add_parser('mcp', help='Serve evaluator tools using stdio MCP')
    mcp.add_argument('--task', choices=ALL_TASK_IDS, required=True)
    mcp.add_argument('--out', required=True)
    mcp.add_argument('--max-evals', type=positive, default=8)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    code = 0
    try:
        if args.command == 'doctor':
            from .engine import environment
            result = environment()
            code = 0 if result['ok'] else 2
        elif args.command == 'tasks':
            result = {'tasks': {name: task_config(name) for name in TASK_IDS}, 'count': len(TASK_IDS), 'relation': CURRICULUM_RELATION,
                      'legacy_ids': ['ota', 'inverter', 'sram6t', 'task1', 'task2']}
        elif args.command in {'run', 'probe'}:
            from .provider import ProviderConfig, ChatProvider
            config = ProviderConfig.load(args.config, args.model, args.base_url)
            if args.command == 'run':
                from .runner import Runner
                result = Runner.create(args.out, args.task, config, args.max_turns, args.max_evals, args.prompt).run()
                code = 0 if result['passed'] else (2 if result['status'] == 'failed' else 3)
            else:
                trace = Trace(args.out, secrets=[config.key()])
                trace.emit('probe.started', config=config.snapshot())
                tools = None
                prompt = args.prompt
                if args.tools:
                    tools = [{'type': 'function', 'function': {'name': 'echo', 'description': 'Echo a text value for a protocol test.', 'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text'], 'additionalProperties': False}}}]
                    prompt += '\nCall the echo tool exactly once with text="probe-ok".'
                try:
                    response = ChatProvider(config, trace).complete([{'role': 'user', 'content': prompt}], tools)
                    calls = response['message'].get('tool_calls') or []
                    tool_ok = bool(calls) and all(c['function']['name'] == 'echo' for c in calls)
                    result = {'ok': not args.tools or tool_ok, 'requested_model': config.model,
                              'reasoning_available': response['reasoning_available'], 'returned_model': response['returned_model'],
                              'tools_requested': args.tools, 'tool_call_returned': bool(calls), 'response': response}
                except Exception as e:
                    result = {'ok': False, 'requested_model': config.model, 'error': str(e)}
                trace.json('summary.json', result)
                trace.emit('probe.finished', **result)
                from .report import render_report
                render_report(trace.root)
                seal(trace)
                code = 0 if result['ok'] else 2
        elif args.command == 'resume':
            from .runner import Runner
            result = Runner.resume(args.run).run()
            code = 0 if result['passed'] else (2 if result['status'] == 'failed' else 3)
        elif args.command == 'lint':
            from .submission import lint_candidate
            result = lint_candidate(args.task, Path(args.netlist).read_text(encoding='utf-8'), args.ibias)
            code = 0 if result['accepted'] else 2
        elif args.command == 'starter':
            from .submission import prepare_candidate, starter
            parameters = json.loads(Path(args.parameters).read_text(encoding='utf-8')) if args.parameters else None
            result = starter(args.task) if parameters is None else prepare_candidate(args.task, parameters, args.ibias)
            code = 0 if parameters is None or result['accepted'] else 2
        elif args.command == 'evaluate':
            from .engine import evaluate, snapshot
            netlist = Path(args.netlist).read_text(encoding='utf-8')
            trace = Trace(args.out)
            trace.emit('run.started', mode='evaluate', task=args.task)
            snapshot(trace, args.task)
            result = evaluate(trace, args.task, 'candidate-001', netlist, args.ibias, 'User-supplied candidate')
            trace.json('summary.json', result)
            from .report import render_report
            render_report(trace.root)
            seal(trace)
            code = (0 if result['assessment']['passed'] else (2 if result['status'] != 'VALID' else 3)) if 'assessment' in result else 2
        elif args.command == 'score':
            result = assess(json.loads(Path(args.input).read_text(encoding='utf-8')), args.task)
            code = 0 if result['passed'] else 3
        elif args.command == 'verify':
            result = verify(args.run)
            code = 0 if result['ok'] else 2
        elif args.command == 'validate':
            from .trajectory import validate_trace
            result = validate_trace(args.input)
            code = 0 if result.get('valid') else 2
        elif args.command == 'export':
            from .export import export_run
            result = export_run(args.run)
        elif args.command == 'report':
            # Generated reports are part of a sealed run. Render separately so
            # offline viewing cannot invalidate previous evidence hashes.
            import shutil
            from .report import render_report
            from tempfile import TemporaryDirectory
            original = Path(args.run).resolve()
            with TemporaryDirectory() as temp:
                target = Path(temp)
                for name in ['events.jsonl', 'summary.json']:
                    if (original / name).is_file():
                        shutil.copyfile(original / name, target / name)
                if (original / 'evaluations').is_dir():
                    for source in (original / 'evaluations').glob('*/assessment.json'):
                        destination = target / source.relative_to(original)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                if (original / 'preflight').is_dir():
                    shutil.copytree(original / 'preflight', target / 'preflight')
                generated = render_report(target)
                output = original.parent / (original.name + '-report.html')
                # Adjust relative links to the original run.
                from urllib.parse import quote
                text = generated.read_text(encoding='utf-8').replace('href="', 'href="' + quote(original.name) + '/')
                output.write_text(text, encoding='utf-8')
            result = {'report': str(output)}
        elif args.command == 'native':
            from .native import run_native
            result = run_native(args.out, args.client, args.task, args.model, args.local, args.max_evals, args.timeout, args.executable, args.vllm_base_url)
            code = 0 if result['passed'] else (2 if result['status'] != 'completed' else 3)
        elif args.command == 'mcp':
            from .mcp import serve
            serve(args.task, args.out, args.max_evals)
            return 0
        from .storage import Redactor
        import os
        output_redactor = Redactor([v for k, v in os.environ.items() if any(x in k.upper() for x in ['API_KEY', 'TOKEN', 'SECRET'])])
        print(json.dumps(output_redactor.value(result), ensure_ascii=False, indent=2, allow_nan=False))
        return code
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as e:
        from .storage import Redactor
        import os
        redactor = Redactor([v for k, v in os.environ.items() if any(x in k.upper() for x in ['API_KEY', 'TOKEN', 'SECRET'])])
        print(json.dumps({'error': redactor.text(str(e)), 'error_type': type(e).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
