from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time

from .engine import tool_schemas, context, evaluate, environment, fingerprint, snapshot, read_artifact
from .provider import ChatProvider, ProviderConfig
from .storage import Trace, digest, seal
from .tasks import rank, is_ota
from .feedback import evaluation_feedback, evaluation_history
from .submission import check_submission, prepare_candidate, enabled, level_counts, preflight_counts

SYSTEM = '''You are an analog CMOS design research agent. Use the supplied circuit-sim skill
and the exact selected task specification. Propose and test DUT netlists via evaluate_candidate. Preserve the evaluator, fixtures, thresholds and PDK.
Report only measured results and distinguish hypothesis from evidence. Briefly state
the design change, expected trade-offs and failure interpretation for each experiment.
Do not fabricate metrics. An execution status VALID alone is insufficient. All required
metrics must be finite, individually valid, and pass for the SAME candidate. Every required functional check must also pass.
For OTA, ibias_uA is required. For inverter and SRAM, omit ibias_uA and submit only the required DUT subcircuit.
You may read logs/waveforms with read_artifact and compare candidates, but do not
combine metrics from different candidates. The provided example is not a passing design.
Use tools for evaluations. After budget exhaustion summarize evidence and limitations.
For curriculum INV/OTA, use the topology starting point with all parameter values left for you to choose. Read the independent numeric device-syntax examples; they are not a complete design. You may change connections, add/remove devices or choose another topology. prepare_candidate requires every device W/L/M and explicit OTA bias without defaults; use lint_candidate before submitting freely edited raw SPICE. Preflight rejection is not a simulator evaluation. Report execution, function and full performance separately.
'''


class Runner:
    def __init__(self, trace, state, provider=None):
        self.trace, self.state = trace, state
        config = ProviderConfig(**state['provider'])
        self.provider = provider or ChatProvider(config, trace)
        self.config = config

    @classmethod
    def create(cls, out, task, config, max_turns=12, max_evals=8, prompt='', provider=None):
        if max_turns < 1 or max_evals < 1:
            raise ValueError('Budgets must be positive')
        config.validate()
        key = config.key()
        trace = Trace(out, secrets=[key])
        trace.emit('run.started', task=task, provider=config.snapshot(), max_turns=max_turns, max_evals=max_evals)
        snapshot(trace, task)
        state = {'schema': 'analog-trace/state/v1', 'task': task, 'provider': config.snapshot(),
                 'policy_version': config.policy_version or hashlib.sha256(json.dumps(config.snapshot(), sort_keys=True).encode()).hexdigest(),
                 'max_turns': max_turns, 'max_evals': max_evals, 'max_tool_calls': 4 * max_evals if enabled(task) else max_evals,
                 'valid_target': 30, 'feasible_target': 1,
                 'turns': 0, 'tool_calls': 0, 'evaluations': 0,
                 'messages': [{'role': 'system', 'content': SYSTEM},
                              {'role': 'user', 'content': json.dumps(context(task), ensure_ascii=False) + '\nUser instruction:\n' + prompt}],
                 'pending': [], 'tool_results': {}, 'reports': [], 'status': 'ready', 'error': None}
        trace.json('state.json', state)
        return cls(trace, state, provider)

    @classmethod
    def resume(cls, out):
        root = Path(out).resolve()
        state = json.loads((root / 'state.json').read_text(encoding='utf-8'))
        if state['status'] in {'passed', 'model_finished', 'budget_exhausted'}:
            raise ValueError('Run is already finished; create a new run to extend its budget')
        config = ProviderConfig(**state['provider'])
        trace = Trace(root, secrets=[config.key()], existing=True)
        expected = json.loads((root / 'source.sha256.json').read_text())
        if fingerprint() != expected:
            raise ValueError('Source/skills/spec changed; resume refused. Start a new run.')
        for p, sha in json.loads((root / 'pdk.sha256.json').read_text(encoding='utf-8')).items():
            if digest(p) != sha:
                raise ValueError('PDK changed; resume refused')
        previous_env = json.loads((root / 'environment.json').read_text())
        current_env = environment()
        if previous_env['pdk']['path'] != current_env['pdk']['path'] or previous_env['ngspice'] != current_env['ngspice']:
            raise ValueError('Selected PDK/simulator changed; start a new run')
        # Seal only describes the previous paused/failed checkpoint.
        (root / 'artifacts.sha256.json').unlink(missing_ok=True)
        trace.emit('run.resumed', previous_status=state['status'], turns=state['turns'])
        state.update(status='ready', error=None)
        return cls(trace, state)

    def save(self):
        self.trace.json('state.json', self.state)

    def model_messages(self):
        """Bound provider input while retaining the complete on-disk history."""
        messages = self.state['messages']
        if len(messages) <= 26:
            return messages
        head = messages[:2]
        tail = list(messages[2:])
        while len(head) + len(tail) > 24 and tail:
            # Drop the oldest complete assistant/tool exchange. This keeps
            # tool messages paired for OpenAI-compatible providers.
            if tail[0].get('role') == 'assistant':
                tail.pop(0)
                while tail and tail[0].get('role') == 'tool':
                    tail.pop(0)
            else:
                tail.pop(0)
        valid = sum(1 for r in self.state['reports'] if r.get('status') == 'VALID' or r.get('assessment', {}).get('status_valid'))
        feasible = sum(1 for r in self.state['reports'] if r.get('assessment', {}).get('passed'))
        return head + [{'role': 'user', 'content': f'History compacted for context. Stored evaluations: {len(self.state["reports"])}; valid: {valid}; feasible: {feasible}. Continue with one concrete tool call.'}] + tail

    def dispatch(self, call):
        s = self.state
        call_id = call['id']
        if call_id in s['tool_results']:
            return s['tool_results'][call_id]
        name = call['function']['name']
        args = json.loads(call['function']['arguments'])
        self.trace.emit('tool.started', tool_call_id=call_id, name=name, arguments=args)
        try:
            if s.setdefault('tool_calls', 0) >= s.get('max_tool_calls', s['max_evals']):
                raise ValueError('Tool-call budget exhausted; no further tools allowed')
            s['tool_calls'] += 1
            self.save()
            if s.get('finalization_turn'):
                raise ValueError('Finalization is tool-free; no additional calls allowed')
            if name == 'evaluate_candidate':
                permitted = {'netlist', 'ibias_uA', 'rationale'}
                if set(args) - permitted or not {'netlist', 'rationale'} <= set(args) or (not is_ota(s['task']) and 'ibias_uA' in args) or not isinstance(args.get('rationale'), str):
                    raise ValueError('evaluate_candidate requires netlist and rationale; ibias_uA is OTA-only')
                if is_ota(s['task']) and not enabled(s['task']) and 'ibias_uA' not in args:
                    # The SiliconFlow Qwen tool schema omits optional fields;
                    # retain the evaluator's conventional nominal bias instead
                    # of rejecting an otherwise complete candidate.
                    args['ibias_uA'] = 30.0
                rejection = check_submission(self.trace, s['task'], **args)
                if rejection is not None:
                    rejection['remaining'] = s['max_evals'] - s['evaluations']
                    s['tool_results'][call_id] = rejection
                    self.trace.emit('tool.completed', tool_call_id=call_id, name=name, result=rejection)
                    self.save()
                    return rejection
                # Candidate identity is the simulation input, not the provider's
                # transient tool-call id. This makes retries and equivalent
                # calls consume one evaluation and one artifact set.
                candidate_key = {'task': s['task'], 'netlist': args['netlist'],
                                 'ibias_uA': args.get('ibias_uA')}
                candidate_id = 'c-' + hashlib.sha256(
                    json.dumps(candidate_key, sort_keys=True, separators=(',', ':')).encode()
                ).hexdigest()[:20]
                base = self.trace.root / 'evaluations' / candidate_id
                cached_report = next((r for r in s['reports'] if r.get('candidate_id') == candidate_id), None)
                if not base.exists() and cached_report is None:
                    charged = s.setdefault('charged_calls', [])
                    if call_id not in charged and s['evaluations'] >= s['max_evals']:
                        raise ValueError('Evaluation budget exhausted; no further simulation allowed')
                    # Charge before execution; recovery cannot double spend the same call.
                    if call_id not in charged:
                        s['evaluations'] += 1
                        charged.append(call_id)
                        self.save()
                result = cached_report or evaluate(self.trace, s['task'], candidate_id, **args)
                if not any(r['candidate_id'] == candidate_id for r in s['reports']):
                    s['reports'].append(result)
                result = evaluation_feedback(result, s['max_evals'] - s['evaluations'])
            elif name == 'read_artifact':
                if set(args) - {'path', 'offset', 'limit'} or 'path' not in args:
                    raise ValueError('Invalid read_artifact arguments')
                result = read_artifact(self.trace.root, **args)
            elif name == 'list_evaluations':
                if args:
                    raise ValueError('list_evaluations takes no arguments')
                result = evaluation_history(s['reports'], s['max_evals'] - s['evaluations'])
                result['preflight'] = preflight_counts(self.trace.root)
            elif name == 'lint_candidate':
                if not enabled(s['task']):raise ValueError('Submission lint is available for curriculum INV/OTA only')
                result = check_submission(self.trace, s['task'], **args, force_record=True)
            elif name == 'prepare_candidate':
                result = prepare_candidate(s['task'], **args)
                check_submission(self.trace, s['task'], result['netlist'], result['ibias_uA'], 'Parameter-generated candidate', force_record=True)
            else:
                raise ValueError('Unknown tool: ' + name)
        except (ValueError, OSError, RuntimeError, TypeError) as e:
            result = {'error': str(e), 'error_type': type(e).__name__}
        s['tool_results'][call_id] = result
        self.trace.emit('tool.completed', tool_call_id=call_id, name=name, result=result)
        self.save()
        return result

    def run(self, require_environment=True):
        s = self.state
        start = time.monotonic()
        try:
            if require_environment and not environment()['ok']:
                raise RuntimeError('ngspice/PDK not ready; run simulation/environment/setup.py and agent/trace.py doctor')
            while True:
                # Pending calls are checkpointed before execution. Results are cached
                # before appending tool messages, making normal crash recovery idempotent.
                while s['pending']:
                    call = s['pending'][0]
                    result = self.dispatch(call)
                    s['messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result, ensure_ascii=False, allow_nan=False)})
                    s['pending'].pop(0)
                    self.save()
                # Once a candidate passes, or the evaluation budget is spent,
                # give the model one extra, tool-free turn to summarize the
                # final measured observation.  Decision turns remain bounded
                # by ``max_turns``; the finalization call deliberately sits
                # outside that budget so max_turns=1/max_evals=1 is usable.
                # ``finalization_turn`` is checkpointed so an interrupted run
                # cannot replay the final call after resume.
                passed = any(r['assessment']['passed'] for r in s['reports'])
                valid_count = sum(1 for r in s['reports'] if r.get('status') == 'VALID' or r.get('assessment', {}).get('status_valid'))
                modern_budget = 'valid_target' in s
                valid_target = int(s.get('valid_target', 30))
                target_hit = passed or (modern_budget and valid_count >= valid_target)
                budget_hit = s['evaluations'] >= s['max_evals']
                finalizing = bool(s['reports']) and (target_hit or budget_hit or s['turns'] >= s['max_turns'])
                if finalizing:
                    if s.get('finalization_turn'):
                        s['status'] = 'passed' if passed else 'budget_exhausted'
                        break
                    s['finalization_turn'] = True
                elif s['turns'] >= s['max_turns']:
                    s['status'] = 'budget_exhausted'
                    break
                s['status'] = 'running'
                s['turns'] += 1
                self.save()
                self.trace.emit('turn.started', turn=s['turns'], remaining_evaluations=s['max_evals'] - s['evaluations'])
                # No tools are exposed during finalization. A compliant API
                # therefore cannot make another call after the budget edge.
                result = self.provider.complete(self.model_messages(), None if finalizing else tool_schemas(s['task']))
                message = result['message']
                if self.config.send_reasoning_back:
                    content = ''.join(r['value'] for r in result['reasoning'] if r['field'] == 'reasoning_content' and isinstance(r['value'], str))
                    if content:
                        message['reasoning_content'] = content
                s['messages'].append(message)
                s['pending'] = list(message.get('tool_calls', []))
                self.save()
                if not s['pending']:
                    if finalizing:
                        s['status'] = 'passed' if passed else ('valid_target_reached' if valid_count >= valid_target else 'budget_exhausted')
                        break
                    if not modern_budget or s['turns'] >= s['max_turns']:
                        s['status'] = 'model_finished'
                        break
                    s['messages'].append({'role': 'user', 'content': 'Continue the experiment. Call exactly one available tool with valid JSON; do not only describe a plan.'})
                    self.save()
        except KeyboardInterrupt:
            s.update(status='interrupted', error='KeyboardInterrupt; resume is available')
            self.trace.emit('run.interrupted')
        except Exception as e:
            # A provider may be unavailable for the optional post-budget
            # synthesis turn. The simulation result is still a valid,
            # recoverable budget exhaustion; preserve it for resume/export.
            if s.get('finalization_turn') and s['reports']:
                s.update(status='passed' if any(r['assessment']['passed'] for r in s['reports']) else 'budget_exhausted', error=None)
                self.trace.emit('run.finalization_unavailable', error_type=type(e).__name__, error=str(e))
            else:
                s.update(status='failed', error=str(e))
                self.trace.emit('run.failed', error_type=type(e).__name__, error=str(e))
        finally:
            self.save()
            self.trace.json('messages.json', s['messages'])
            reports = sorted(s['reports'], key=lambda r: rank(r['assessment']), reverse=True)
            summary = {'status': s['status'], 'task': s['task'], 'error': s['error'],
                       'passed': any(r['assessment']['passed'] for r in reports),
                       'valid': sum(1 for r in reports if r.get('status') == 'VALID' or r.get('assessment', {}).get('status_valid')),
                       'functional': sum(r.get('curriculum_valid') is True for r in reports),
                       'spice_evaluations': sum(r.get('spice_evaluations') or 0 for r in reports),
                       'feasible': sum(1 for r in reports if r.get('assessment', {}).get('passed')),
                       'turns': s['turns'], 'tool_calls': s.get('tool_calls', 0), 'evaluations': s['evaluations'],
                       'best_candidate': reports[0]['candidate_id'] if reports else None,
                       'best_assessment': reports[0]['assessment'] if reports else None,
                       'elapsed_this_invocation_s': time.monotonic() - start,
                       'note': 'Best is one candidate, ranked heuristically; no claim of optimality or guaranteed convergence.'}
            summary['levels'] = level_counts(reports)
            summary['functional'] = summary['levels']['functional_valid']
            summary['preflight'] = preflight_counts(self.trace.root)
            self.trace.json('summary.json', summary)
            self.trace.emit('run.finished', **summary)
            from .report import render_report
            render_report(self.trace.root)
            seal(self.trace)
        return summary
