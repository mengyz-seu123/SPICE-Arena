from __future__ import annotations
import json
import copy
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import yaml

from .storage import digest
from .tasks import ROOT, SIMULATION_ROOT, TASK_ROOT, CONTRACT, RELATION, task_config, assess, is_ota
from .curriculum import TASK_IDS as CURRICULUM_IDS, DATA as CURRICULUM_DATA
from .analogcoderpro import TASK_IDS as ANALOGCODERPRO_IDS


def environment():
    from analog_arena.simulation.ngspice import default_pdk, version
    pdk = default_pdk()
    try:
        spice = version()
    except Exception as e:
        spice = {'ok': False, 'error': str(e)}
    return {'ok': bool(spice.get('ok') and pdk.is_file()), 'python': sys.version,
            'platform': platform.platform(), 'ngspice': spice,
            'pdk': {'available': pdk.is_file(), 'path': str(pdk), 'library_sha256': digest(pdk) if pdk.is_file() else None},
            'codex': shutil.which('codex'), 'claude': shutil.which('claude')}


def fingerprint():
    files = [*sorted((SIMULATION_ROOT / 'src').rglob('*.py')),
             *sorted((SIMULATION_ROOT / 'src').rglob('*.tmpl')),
             SIMULATION_ROOT / 'examples/ota.spice',
             *sorted((ROOT / 'agent/src').rglob('*.py')),
             *sorted(p for p in CURRICULUM_DATA.rglob('*') if p.is_file()),
             TASK_ROOT / 'amplifier-task-metrics.md', ROOT / 'basic-cmos-sram-tasks.md',
             ROOT / 'agent/prompts/circuit-sim/SKILL.md',
            ROOT / 'agent/prompts/amplifier-design/SKILL.md']
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in files}


def snapshot(trace, task):
    if task in CURRICULUM_IDS:
        from .curriculum import contract as curriculum_contract
        contract = curriculum_contract(task)
    elif is_ota(task):
        from analog_arena.evaluation.amplifier.profile import software_contract
        contract = software_contract()
        if contract['evaluator_contract_version'] != CONTRACT:
            raise ValueError('Evaluator contract mismatch')
    else:
        from .cmos import contract as cmos_contract
        contract = cmos_contract(task)
    trace.json('contract.json', contract)
    task_context = context(task)
    trace.json('task.json', {'task': task, 'config': task_config(task), 'relation': task_context['relation'],
                            'workflow_revision':task_context.get('workflow_revision'), 'template_revision':task_context.get('template_revision')})
    trace.text('evaluation.yaml', yaml.safe_dump(task_config(task), sort_keys=False))
    trace.json('environment.json', environment())
    hashes = fingerprint()
    trace.json('source.sha256.json', hashes)
    for relative in hashes:
        target = trace.root / 'source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    # Hash externally configured model files to detect edits on resume.
    from analog_arena.simulation.ngspice import default_pdk
    pdk = default_pdk()
    model_root = pdk.parents[2]
    pdk_hashes = {str(p): digest(p) for p in sorted(model_root.rglob('*.spice'))} if pdk.is_file() else {}
    trace.json('pdk.sha256.json', pdk_hashes)


def context(task):
    if task in ANALOGCODERPRO_IDS:
        from .analogcoderpro import context as analogcoderpro_context
        result = analogcoderpro_context(task)
        result['skills'] = {
            'circuit-sim': (ROOT / 'agent/prompts/circuit-sim/SKILL.md').read_text(encoding='utf-8')
        }
        return result
    if task in CURRICULUM_IDS:
        from .curriculum import context as curriculum_context
        return curriculum_context(task)
    config = task_config(task)
    result = {'task': task, 'config': config, 'relation': RELATION}
    if not is_ota(task):
        from .cmos import contract as cmos_contract
        result['contract'] = cmos_contract(task)
        result['task_specification'] = (ROOT / 'basic-cmos-sram-tasks.md').read_text(encoding='utf-8')
        result['example_netlist'] = _cmos_example(task)
        result['example_note'] = 'Interface example only; it is not a claimed optimized result.'
        result['skills'] = {'circuit-sim': (ROOT / 'agent/prompts/circuit-sim/SKILL.md').read_text(encoding='utf-8')}
        return result
    from analog_arena.evaluation.amplifier.profile import software_contract
    if os.getenv('ANALOG_TRACE_COMPACT_CONTEXT') == '1':
        result['context_mode'] = 'compact'
        result['contract'] = software_contract()
        result['skills'] = {'circuit-sim': 'Use the arena CLI through the evaluator tool; inspect returned artifacts.',
                            'amplifier-design': 'Use the supplied OTA contract, preserve units, and report measured validity.'}
        result['metrics_spec'] = 'Use the 11 constraints in config. All must be finite, individually valid, and pass for one candidate. Never combine candidates.'
    else:
        result['contract'] = software_contract()
        result['example_netlist'] = (SIMULATION_ROOT / 'examples/ota.spice').read_text(encoding='utf-8')
        result['example_note'] = 'Execution example only; no claim that this design meets either task.'
        result['context_mode'] = 'full'
        result['metrics_spec'] = (ROOT / 'basic-cmos-sram-tasks.md').read_text(encoding='utf-8') if task == 'ota' else (TASK_ROOT / 'amplifier-task-metrics.md').read_text(encoding='utf-8')
        result['skills'] = {name: (ROOT / f'agent/prompts/{name}/SKILL.md').read_text(encoding='utf-8') for name in ['circuit-sim', 'amplifier-design']}
    return result


def _cmos_example(task):
    if task == 'inverter':
        return '''.subckt DUT IN OUT VDD VSS
XP OUT IN VDD VDD sky130_fd_pr__pfet_01v8 W=1.2 L=0.15
XN OUT IN VSS VSS sky130_fd_pr__nfet_01v8 W=0.8 L=0.15
.ends DUT'''
    return '''.subckt DUT Q QB BL BLB WL VDD VSS
XP1 Q QB VDD VDD sky130_fd_pr__pfet_01v8 W=0.84 L=0.15
XN1 Q QB VSS VSS sky130_fd_pr__nfet_01v8 W=0.42 L=0.15
XP2 QB Q VDD VDD sky130_fd_pr__pfet_01v8 W=0.84 L=0.15
XN2 QB Q VSS VSS sky130_fd_pr__nfet_01v8 W=0.42 L=0.15
XA1 Q WL BL VSS sky130_fd_pr__nfet_01v8 W=0.42 L=0.15
XA2 QB WL BLB VSS sky130_fd_pr__nfet_01v8 W=0.42 L=0.15
.ends DUT'''


def clean_sim_env(secrets=()):
    # Keep simulator/PDK and runtime configuration, remove common model credentials.
    result = {k: v for k, v in os.environ.items() if v not in secrets and not any(s in k.upper() for s in ['API_KEY', 'TOKEN', 'SECRET', 'PASSWORD'])}
    result['PYTHONPATH'] = str(SIMULATION_ROOT / 'src') + (os.pathsep + result['PYTHONPATH'] if result.get('PYTHONPATH') else '')
    result['PYTHONIOENCODING'] = 'utf-8'
    return result


def evaluate(trace, task, candidate_id, netlist, ibias_uA=None, rationale='', timeout_s=180):
    """Idempotent by caller-supplied ID. Completed outputs are never re-simulated."""
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', candidate_id):
        raise ValueError('Invalid candidate ID')
    from .submission import check_submission
    rejection = check_submission(trace, task, netlist, ibias_uA, rationale)
    if rejection is not None:
        return rejection
    if not isinstance(netlist, str) or not netlist.strip() or len(netlist.encode()) > 128_000:
        raise ValueError('Netlist must be nonempty text under 128 KB')
    if is_ota(task) and (type(ibias_uA) not in (int, float) or not math.isfinite(ibias_uA) or not 1 <= ibias_uA <= 40 or abs(ibias_uA * 10 - round(ibias_uA * 10)) > 1e-7):
        raise ValueError('IBIAS must be 1..40 uA on the 0.1 uA grid')
    if not is_ota(task) and ibias_uA is not None:
        raise ValueError('ibias_uA is only accepted by the OTA task')
    base = trace.root / 'evaluations' / candidate_id
    request = {'netlist': netlist, 'ibias_uA': ibias_uA, 'rationale': rationale, 'task': task}
    if base.exists():
        saved = json.loads((base / 'request.json').read_text(encoding='utf-8'))
        normalized_saved = {k: saved.get(k) for k in ('task', 'netlist', 'ibias_uA')}
        normalized_request = {k: request.get(k) for k in ('task', 'netlist', 'ibias_uA')}
        if normalized_saved != normalized_request:
            raise ValueError('Candidate ID already belongs to different simulation inputs')
        if (base / 'assessment.json').is_file():
            return json.loads((base / 'assessment.json').read_text(encoding='utf-8'))
        # Never silently execute a possibly already-billed/completed simulation again.
        if not (base / 'simulation/result.json').is_file():
            result = {'status': 'INVALID', 'profile': task_config(task)['evaluator']['profile'], 'metrics': {}, 'metric_validity': {},
                      'corners': {}, 'error': 'Interrupted evaluation; submit a new candidate ID to retry'}
            return finish_evaluation(trace, task, candidate_id, result, 'interrupted')
        result = json.loads((base / 'simulation/result.json').read_text(encoding='utf-8'))
        return finish_evaluation(trace, task, candidate_id, result, 'recovered')
    base.mkdir(parents=True)
    trace.json(f'evaluations/{candidate_id}/request.json', request)
    trace.text(f'evaluations/{candidate_id}/dut.spice', netlist)
    config = task_config(task)
    config['evaluator']['timeout_s'] = timeout_s
    trace.text(f'evaluations/{candidate_id}/evaluation.yaml', yaml.safe_dump(config, sort_keys=False))
    trace.emit('evaluation.started', candidate_id=candidate_id, ibias_uA=ibias_uA,
               netlist_sha256=digest(base / 'dut.spice'), rationale=rationale)
    started = time.monotonic()
    if task in CURRICULUM_IDS:
        from analog_arena.curriculum.runtime import evaluate as evaluate_curriculum
        result = evaluate_curriculum(task.split('-')[0], netlist, base / 'simulation', ibias_uA, timeout_s)
        return finish_evaluation(trace, task, candidate_id, result, 'completed')
    if not is_ota(task):
        from .cmos import evaluate as evaluate_cmos
        result = evaluate_cmos(task, netlist, base / 'simulation', timeout_s)
        result['candidate_key'] = candidate_id
        return finish_evaluation(trace, task, candidate_id, result, 'completed')
    cmd = [sys.executable, '-m', 'analog_arena', 'evaluate', '--config', str(base / 'evaluation.yaml'),
           '--netlist', str(base / 'dut.spice'), '--ibias', str(ibias_uA), '--output', str(base / 'simulation')]
    trace.emit('process.started', candidate_id=candidate_id, argv=cmd, cwd=str(ROOT))
    try:
        # Original evaluator has per-analysis timeouts; this outer guard also
        # bounds parse/setup failures. No user/model-supplied shell is executed.
        proc = subprocess.run(cmd, cwd=ROOT, env=clean_sim_env(trace.redactor.secrets), capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout_s * 5 + 60)
        trace.text(f'evaluations/{candidate_id}/stdout.txt', proc.stdout)
        trace.text(f'evaluations/{candidate_id}/stderr.txt', proc.stderr)
        trace.emit('process.completed', candidate_id=candidate_id, returncode=proc.returncode,
                   elapsed_s=time.monotonic() - started,
                   stdout_file=f'evaluations/{candidate_id}/stdout.txt',
                   stderr_file=f'evaluations/{candidate_id}/stderr.txt')
        if (base / 'simulation/result.json').is_file():
            result = json.loads((base / 'simulation/result.json').read_text(encoding='utf-8'))
        else:
            result = {'status': 'INVALID', 'profile': task_config(task)['evaluator']['profile'], 'metrics': {}, 'metric_validity': {},
                      'corners': {}, 'error': proc.stderr or proc.stdout, 'returncode': proc.returncode}
        if proc.returncode != 0:
            result['status'] = 'INVALID'
    except subprocess.TimeoutExpired as e:
        def decoded(v):
            return v.decode('utf-8', errors='replace') if isinstance(v, bytes) else (v or '')
        trace.text(f'evaluations/{candidate_id}/stdout.txt', decoded(e.stdout))
        trace.text(f'evaluations/{candidate_id}/stderr.txt', decoded(e.stderr))
        trace.emit('process.timeout', candidate_id=candidate_id, elapsed_s=time.monotonic() - started)
        result = {'status': 'INVALID', 'profile': task_config(task)['evaluator']['profile'], 'metrics': {}, 'metric_validity': {}, 'corners': {}, 'error': 'simulation timeout'}
    return finish_evaluation(trace, task, candidate_id, result, 'completed')


def finish_evaluation(trace, task, candidate_id, result, outcome):
    assessments = {name: assess(result, name) for name in (['task1', 'task2'] if task in {'task1', 'task2'} else [task])}
    result_file = trace.root / 'evaluations' / candidate_id / 'simulation/result.json'
    report = {'candidate_id': candidate_id, 'selected_task': task, 'assessment': assessments[task],
              'both_tasks': assessments, 'metrics': result.get('metrics', {}),
              'metric_validity': result.get('metric_validity', {}), 'status': result.get('status'),
              'diagnostics': result.get('diagnostics', []), 'diagnostics_truncated': result.get('diagnostics_truncated', False),
              'error': result.get('error'), 'failed_checks': result.get('failed_checks', []),
              'functional_checks': result.get('functional_checks', {}), 'curriculum_valid': result.get('curriculum_valid'),
              'measurement_contract': result.get('measurement_contract'), 'spice_evaluations': result.get('spice_evaluations'),
              'artifact_path': f'evaluations/{candidate_id}', 'outcome': outcome,
              'result_sha256': digest(result_file) if result_file.is_file() else None}
    from .submission import success_levels
    report['levels'] = success_levels(report)
    report['measurement_complete'] = bool(assessments[task]['checks'] and all(c['valid'] for c in assessments[task]['checks'].values()))
    # Summarize only the actual change from this run's preceding completed candidate.
    import difflib
    base = trace.root / 'evaluations' / candidate_id
    request_path = base / 'request.json'
    if request_path.is_file():
        previous = [p for p in (trace.root / 'evaluations').glob('*/assessment.json')
                    if p.parent != base and (p.parent / 'request.json').is_file()]
        if previous:
            prev = max(previous, key=lambda p: p.stat().st_mtime_ns)
            before = json.loads((prev.parent / 'request.json').read_text(encoding='utf-8'))
            after = json.loads(request_path.read_text(encoding='utf-8'))
            changes = list(difflib.ndiff(before['netlist'].splitlines(), after['netlist'].splitlines()))
            report['change_summary'] = {'compared_to': prev.parent.name,
                                       'added_lines': sum(s.startswith('+ ') for s in changes),
                                       'removed_lines': sum(s.startswith('- ') for s in changes),
                                       'ibias_changed': before.get('ibias_uA') != after.get('ibias_uA')}
    from .feedback import enrich_report
    enrich_report(report, base)
    trace.json(f'evaluations/{candidate_id}/assessment.json', report)
    trace.emit('evaluation.completed', **report)
    return report


def read_artifact(root, path, offset=0, limit=12000):
    root = Path(root).resolve()
    target = (root / path).resolve()
    allowed = target.is_relative_to(root / 'evaluations') or (target.parent == root / 'preflight' and target.suffix == '.json')
    if not allowed or not target.is_file():
        raise ValueError('Only files inside this run/evaluations or preflight records are readable')
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 64000:
        raise ValueError('Invalid byte offset or limit (1..64000)')
    with target.open('rb') as f:
        f.seek(offset)
        chunk = f.read(limit)
    return {'path': path, 'offset_bytes': offset, 'next_offset_bytes': offset + len(chunk),
            'size_bytes': target.stat().st_size, 'content': chunk.decode('utf-8', errors='replace'),
            'has_more': offset + len(chunk) < target.stat().st_size}


TOOL_SCHEMAS = [
    {'type': 'function', 'function': {'name': 'evaluate_candidate', 'description': 'Submit a complete DUT for fixed TT evaluation. Curriculum INV/OTA syntax/contract failures are recorded in preflight without simulation or candidate-budget charge; accepted distinct candidates consume one evaluation each, including simulator INVALID. Prefer prepare_candidate or lint_candidate first. Returns execution, functional and full-performance results for this candidate.',
       'parameters': {'type': 'object', 'properties': {'netlist': {'type': 'string'}, 'ibias_uA': {'type': 'number', 'description': 'Required only for OTA; omit for inverter and SRAM.'}, 'rationale': {'type': 'string', 'description': 'Brief design hypothesis and expected trade-off for the experiment record.'}}, 'required': ['netlist', 'rationale'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'read_artifact', 'description': 'Read a log, waveform or result under evaluations/ in this run. Use byte pagination for large files.',
       'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}}, 'required': ['path'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'list_evaluations', 'description': 'List already evaluated candidates and checks in this run.',
       'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}},
]

TOOL_SCHEMAS += [
    {'type':'function','function':{'name':'lint_candidate','description':'Check curriculum INV/OTA raw netlist and bias before submission. No simulation, no candidate budget, no automatic repairs; records diagnostics.',
        'parameters':{'type':'object','properties':{'netlist':{'type':'string'},'ibias_uA':{'type':'number'}},'required':['netlist'],'additionalProperties':False}}},
    {'type':'function','function':{'name':'prepare_candidate','description':'Generate a candidate using the provided topology connections. Supply ALL W/L/M values for EVERY device and explicit OTA ibias_uA; there are NO default design values. To change topology, freely edit the full DUT and use lint_candidate instead. Returns text and static validation only.',
        'parameters':{'type':'object','properties':{'parameters':{'type':'object','description':'Map EVERY template device name to ALL W/L/M numeric values; no omitted devices or fields.',
            'additionalProperties':{'type':'object','properties':{'W':{'type':'number'},'L':{'type':'number'},'M':{'type':'integer'}},'required':['W','L','M'],'additionalProperties':False}},'ibias_uA':{'type':'number'}},'required':['parameters'],'additionalProperties':False}}},
]


def tool_schemas(task):
    from .submission import enabled
    schemas = copy.deepcopy(TOOL_SCHEMAS if enabled(task) else [s for s in TOOL_SCHEMAS if s['function']['name'] not in {'lint_candidate', 'prepare_candidate'}])
    for schema in schemas:
        tool = schema['function']
        tool['description'] = tool['description'].replace('Curriculum INV/OTA', 'This task').replace('curriculum INV/OTA', 'this task')
        if not is_ota(task):
            tool['parameters']['properties'].pop('ibias_uA', None)
            tool['description'] = tool['description'].replace(' and explicit OTA ibias_uA', '').replace('raw netlist and bias', 'raw netlist')
    return schemas
