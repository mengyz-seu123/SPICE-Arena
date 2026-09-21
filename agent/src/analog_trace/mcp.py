"""Stdio MCP server exposing the frozen evaluator, no arbitrary shell tool."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
from .engine import tool_schemas, context, environment, evaluate, read_artifact, snapshot, fingerprint
from .storage import Trace, seal, canonical
from .report import render_report
from .feedback import evaluation_feedback, evaluation_history
from .submission import check_submission, prepare_candidate, enabled, preflight_counts


def serve(task, out, max_evals=8, input_stream=None, output_stream=None):
    incoming, outgoing = input_stream or sys.stdin, output_stream or sys.stdout
    root = Path(out).resolve()
    trace = Trace(root, existing=root.exists())
    if (root / 'task.json').is_file():
        if json.loads((root / 'task.json').read_text())['task'] != task:
            raise ValueError('MCP reconnect task mismatch')
        if json.loads((root / 'source.sha256.json').read_text()) != fingerprint():
            raise ValueError('MCP reconnect source mismatch; start a new run')
        (root / 'artifacts.sha256.json').unlink(missing_ok=True)
        trace.emit('run.resumed', mode='mcp')
    else:
        snapshot(trace, task)
        trace.emit('run.started', mode='mcp', task=task, max_evals=max_evals)
        trace.json('mcp-budget.json', {'max_evals': max_evals})
    budget = json.loads((root / 'mcp-budget.json').read_text())['max_evals']
    extras = [{'name': 'task_context', 'description': 'Read authoritative thresholds, topology template without values, numeric single-device syntax examples and submission policy; topology changes are allowed.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
              {'name': 'doctor', 'description': 'Check ngspice and PDK.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]
    schemas = [{'name': s['function']['name'], 'description': s['function']['description'], 'inputSchema': s['function']['parameters']} for s in tool_schemas(task)] + extras

    def call(name, args):
        if name == 'task_context':
            return context(task)
        if name == 'doctor':
            return environment()
        if name == 'read_artifact':
            return read_artifact(root, **args)
        if name == 'list_evaluations':
            reports = [json.loads(p.read_text()) for p in sorted((root / 'evaluations').glob('*/assessment.json'), key=lambda p: p.stat().st_mtime_ns)]
            history = evaluation_history(reports, budget - len(list((root / 'evaluations').glob('*'))))
            history['preflight'] = preflight_counts(root)
            return history
        if name == 'lint_candidate':
            if not enabled(task):raise ValueError('Submission lint is available for curriculum INV/OTA only')
            return check_submission(trace, task, **args, force_record=True)
        if name == 'prepare_candidate':
            result = prepare_candidate(task, **args)
            check_submission(trace, task, result['netlist'], result['ibias_uA'], 'Parameter-generated candidate', force_record=True)
            return result
        if name == 'evaluate_candidate':
            required = {'netlist', 'rationale'}
            if (not isinstance(args, dict) or not required <= set(args)
                    or set(args) - (required | {'ibias_uA'})
                    or not isinstance(args['rationale'], str)):
                raise ValueError('Expected netlist and rationale, with optional ibias_uA for OTA')
            if 'ibias_uA' in args and type(args['ibias_uA']) not in (int, float):
                raise ValueError('ibias_uA must be a number when provided')
            rejection = check_submission(trace, task, **args)
            if rejection is not None:
                rejection['remaining'] = budget - len(list((root / 'evaluations').glob('*')))
                return rejection
            identity = {'task': task, 'netlist': args['netlist'], 'ibias_uA': args.get('ibias_uA')}
            cid = 'c-' + hashlib.sha256(canonical(identity).encode()).hexdigest()[:20]
            if not (root / 'evaluations' / cid).exists() and len(list((root / 'evaluations').glob('*'))) >= budget:
                raise ValueError('Evaluation budget exhausted')
            report = evaluate(trace, task, cid, **args)
            return evaluation_feedback(report, budget - len(list((root / 'evaluations').glob('*'))))
        raise ValueError('Unknown tool: ' + name)

    try:
        for line in incoming:
            request = None
            try:
                request = json.loads(line)
                trace.emit('mcp.request', request=request)
                if not isinstance(request, dict):
                    raise ValueError('request must be an object')
                if 'id' not in request:
                    continue
                method = request.get('method')
                if method == 'initialize':
                    wanted = request.get('params', {}).get('protocolVersion')
                    protocol = wanted if wanted in {'2024-11-05', '2025-03-26', '2025-06-18'} else '2025-06-18'
                    result = {'protocolVersion': protocol, 'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'analog-arena-trace', 'version': '1.0.0'}}
                elif method == 'ping':
                    result = {}
                elif method == 'tools/list':
                    result = {'tools': schemas}
                elif method == 'tools/call':
                    params = request.get('params', {})
                    try:
                        value = call(params['name'], params.get('arguments') or {})
                        result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False, allow_nan=False)}], 'isError': isinstance(value,dict) and value.get('accepted') is False}
                    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as e:
                        result = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
                else:
                    outgoing.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32601, 'message': 'Method not found'}}) + '\n')
                    outgoing.flush()
                    continue
                response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
            except (ValueError, TypeError, KeyError) as e:
                response = {'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) else None, 'error': {'code': -32600, 'message': str(e)}}
            trace.emit('mcp.response', response=response)
            outgoing.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n')
            outgoing.flush()
    finally:
        trace.emit('mcp.closed')
        render_report(root)
        seal(trace)
