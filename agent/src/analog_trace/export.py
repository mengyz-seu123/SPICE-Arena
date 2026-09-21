"""Public, verified trajectory export for dataset consumers."""
from __future__ import annotations

import json
from pathlib import Path

from .storage import digest, verify
from .tasks import assess, rank
from .trajectory import validate_trajectory
reward_from_result = None  # No reward-training dependency in this source release.


def export_run(root):
    root = Path(root).resolve()
    audit = verify(root)
    if not audit.get('ok') or not audit.get('sealed') or not audit.get('artifacts_checked'):
        raise ValueError('Dataset export requires a sealed, verified run')
    inventory = json.loads((root / 'artifacts.sha256.json').read_text())

    def read(name):
        path = root / name
        if name not in inventory or digest(path) != inventory[name]:
            raise ValueError('Export input is not covered by the seal: ' + name)
        return json.loads(path.read_text(encoding='utf-8'))

    if 'state.json' not in inventory:
        if 'trajectory.json' not in inventory:
            raise ValueError('Training export requires a normalized trajectory.json')
        trajectory = read('trajectory.json')
        summary = read('summary.json') if 'summary.json' in inventory else {}
        check = validate_trajectory(trajectory, require_complete=False)
        if not check['valid']:
            raise ValueError('Invalid native trajectory: ' + '; '.join(check['errors']))
        # A partially emitted stream can be useful for diagnosis but cannot be a
        # supervised/RL example: every emitted tool action needs its observation.
        # Keep the trace exportable for audit while making eligibility explicit.
        return {**trajectory, 'trace': str(root), 'trace_head': audit['head'],
                'trace_verified': True, 'task': summary.get('task'),
                'status': summary.get('status'), 'passed': bool(summary.get('passed')),
                'eligible': bool(check['trainable'] and summary.get('status') in
                                 {'completed', 'passed', 'model_finished', 'budget_exhausted'}),
                'valid_metrics': int(trajectory.get('valid_metrics', 0)),
                'passed_metrics': int(trajectory.get('passed_metrics', 0)),
                'passed': bool(trajectory.get('passed', summary.get('passed', False))),
                'trajectory_validation': check,
                'training_note': ('Native stream-json normalized; raw client JSONL remains in the sealed trace. '
                                  'Eligibility requires a complete trainable trajectory.')}
    state = read('state.json')
    task = read('task.json')['task']
    messages = read('messages.json')
    if state['task'] != task or messages != state['messages']:
        raise ValueError('Task or conversation does not match sealed state')
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError('Missing conversation')
    prefix = []
    for message in messages:
        if message.get('role') not in {'system', 'user'}:
            break
        prefix.append(message)
    completion = messages[len(prefix):]
    assistant = [m for m in completion if m.get('role') == 'assistant' and (m.get('content') or m.get('tool_calls'))]
    assessments = []
    for name in sorted(inventory):
        if name.startswith('evaluations/') and name.endswith('/assessment.json'):
            report = read(name)
            result_name = (Path(name).parent / 'simulation/result.json').as_posix()
            if result_name not in inventory:
                continue  # Structural rejection has no simulator result.
            result = read(result_name)
            if report.get('result_sha256') != inventory[result_name]:
                raise ValueError('Assessment refers to a different simulation result')
            assessment = assess(result, task)
            assessments.append({'candidate_id': report['candidate_id'], 'assessment': assessment})
    best = max(assessments, key=lambda r: rank(r['assessment']), default=None)
    assessment = best['assessment'] if best else None
    measurement_ok = bool(assessment and assessment['status_valid'] and
                          assessment['nominal_tt_result'] and assessment['candidate_key'])
    checks = assessment['checks'] if measurement_ok else {}
    valid = sum(c['valid'] for c in checks.values())
    passed_metrics = sum(c['passed'] for c in checks.values())
    passed = bool(assessment and assessment['passed'])
    request_names = sorted(n for n in inventory if n.startswith('api/') and n.endswith('/request.json'))
    # Request directories are UUIDs, so lexical order is not conversation
    # order. Finalization intentionally omits tools. Retain the schema from
    # decision requests instead of randomly selecting the final request.
    tools = []
    for request_name in request_names:
        request_tools = read(request_name).get('tools') or []
        if request_tools:
            if tools and request_tools != tools:
                raise ValueError('Tool schemas changed within one trajectory')
            tools = request_tools
    source_hashes = read('source.sha256.json')
    verifier = {'contract': read('contract.json'), 'task': read('task.json'),
                'sources': {name: value for name, value in source_hashes.items()
                            if name.startswith('simulation/') or name.endswith('/tasks.py')}}
    def canonical_call(call):
        function = call.get('function') if isinstance(call, dict) else None
        source = function if isinstance(function, dict) else call
        return {'id': call.get('id', ''), 'name': source.get('name', ''),
                'arguments': source.get('arguments', call.get('arguments', {}))}

    tool_calls = [canonical_call(c) for m in messages if m.get('role') == 'assistant'
                  for c in m.get('tool_calls', []) if isinstance(c, dict)]
    tool_results = [{'tool_call_id': m.get('tool_call_id'), 'content': m.get('content', '')}
                    for m in messages if m.get('role') == 'tool']
    reward_info = None
    legacy_reward = task in {'task1', 'task2'}
    if legacy_reward and reward_from_result is not None and assessment is not None:
        reward_info = reward_from_result(
            {'task': task, 'status': 'VALID' if assessment.get('status_valid') else 'INVALID',
             'metrics': {name: detail.get('value') for name, detail in checks.items()},
             'metric_validity': {name: {'valid': detail.get('valid')} for name, detail in checks.items()}},
            task=task)
    trajectory = {'schema': 'analog-trace/trajectory/v1', 'trace': str(root), 'trace_head': audit['head'],
            'trace_verified': True, 'task': task, 'status': state['status'],
            'policy': state['provider'], 'prompt_messages': prefix, 'completion': completion,
            'messages': messages, 'tools': tools, 'verifier': verifier,
            'tool_calls': tool_calls, 'tool_results': tool_results, 'evaluations': assessments,
            'model': state['provider'].get('model'), 'policy_version': state.get('policy_version'),
            'best_candidate_id': best['candidate_id'] if best else None,
            'assessment': assessment, 'passed': passed, 'valid_metrics': valid,
            'passed_metrics': passed_metrics,
            'reward': reward_info['legacy_reward'] if reward_info else float(passed) + valid / 11 + passed_metrics / 11,
            'reward_normalized': reward_info['reward'] if reward_info else 0.0,
            'normalized_metrics': reward_info['normalized_metrics'] if reward_info else {},
            'training_note': 'Captured behavior data; no token log probabilities or policy weights are provided.'}
    if not legacy_reward:
        # Schema v1 requires a numeric reward; zero here is only a placeholder.
        trajectory.update(reward=0.0, reward_status='not_computed',
                          training_note='Unscored trajectory for audit/SFT; reward is a schema placeholder, not a measured score.')
    check = validate_trajectory(trajectory)
    if not check['valid']:
        raise ValueError('Invalid direct trajectory: ' + '; '.join(check['errors']))
    trajectory['trajectory_validation'] = check
    trajectory['eligible'] = bool(assistant and state['status'] in {'passed', 'model_finished', 'budget_exhausted'}
                                  and check['trainable'])
    return trajectory
