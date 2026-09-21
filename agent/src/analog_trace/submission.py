"""Explicit starter generation and non-simulating submission checks.

Rejected text stays in preflight/, outside the candidate simulation budget.
No candidate netlist is silently repaired or replaced.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import re
import uuid

REVISION = 'topology-template-20260912-v1'
DATA = Path(__file__).resolve().parents[3] / 'simulation/tasks/curriculum-l123/templates'


def enabled(task):
    from .curriculum import TASK_IDS
    return task in TASK_IDS and task.split('-')[0] in {'INV', 'OTA'}


def starter(task):
    if not enabled(task):
        raise ValueError('Topology templates are available for curriculum INV/OTA only')
    family = task.split('-')[0]
    netlist = (DATA / (family + '.spice')).read_text(encoding='utf-8')
    devices = {}
    for line in netlist.splitlines():
        if line.startswith('X'):
            tokens = line.split()
            devices[tokens[0]] = {k: None for k in ('W', 'L', 'M')}
    return {'netlist': netlist, 'ibias_uA': None,
            'parameters': devices, 'workflow_revision': REVISION}


def lint_candidate(task, netlist, ibias_uA=None):
    if not enabled(task):
        raise ValueError('Submission lint is available for curriculum INV/OTA only')
    diagnostics = []
    if not isinstance(netlist, str) or not netlist.strip() or len(netlist.encode()) > 128_000:
        diagnostics.append({'code': 'netlist_text', 'message': 'Netlist must be nonempty text under 128 KB'})
    family = task.split('-')[0]
    if family == 'OTA':
        if (type(ibias_uA) not in (int, float) or not math.isfinite(ibias_uA)
                or not 1 <= ibias_uA <= 40 or abs(ibias_uA * 10 - round(ibias_uA * 10)) > 1e-7):
            diagnostics.append({'code': 'ibias_range', 'message': 'IBIAS must be 1..40 uA on the 0.1 uA grid'})
    elif ibias_uA is not None:
        diagnostics.append({'code': 'unexpected_bias', 'message': 'ibias_uA is only accepted by the OTA task'})
    if not diagnostics:
        try:
            if family == 'OTA':
                from analog_arena.simulation.amplifier.candidate import expand_and_validate_dut
                expand_and_validate_dut(netlist)
            else:
                from analog_arena.curriculum.common import parse_cmos
                parse_cmos(netlist, family)
        except (ValueError, ArithmeticError) as exc:
            diagnostics = getattr(exc, 'diagnostics', None) or [{'code': 'netlist_contract', 'message': str(exc),
                'expected': 'Xname D G S B model W=<number_um> L=<number_um> M=1'}]
    from analog_arena.simulation.amplifier.candidate import _located_lines
    try:
        located = _located_lines(netlist) if isinstance(netlist, str) else []
    except ValueError:
        located = []
    active = [(number, line) for number, line in located if line[0].upper() in 'XRC']
    if family == 'OTA' and not diagnostics:
        nodes = {node.upper() for _, line in active for node in line.split()[1:(5 if line[0].upper() == 'X' else 3)]}
        missing = sorted({'VDD','GND','VINP','VINN','VOUT','IBIAS'} - nodes)
        if missing:
            diagnostics.append({'code':'unused_ports','message':'Required OTA ports are not referenced by active devices: '+', '.join(missing)})
    issues = []
    for d in diagnostics:
        tokens = dict(located).get(d.get('line'), '').split()
        issue = {'code':d.get('code'), 'line':d.get('line')}
        if tokens:
            issue.update(actual_field_count=len(tokens), instance_name_is_bare_x=tokens[0].upper()=='X')
            if d.get('code') in {'resistor_fields','capacitor_fields'}:issue['value_tokens']=tokens[3:]
        issues.append(issue)
    commented = [i for i,line in enumerate(netlist.splitlines(),1) if re.match(r'^\s*\*\s*[XRC]\S*\s+',line,re.I)] if isinstance(netlist,str) else []
    return {'accepted': not diagnostics, 'stage': 'preflight', 'workflow_revision': REVISION,
            'diagnostics': diagnostics, 'spice_evaluations': 0, 'budget_consumed': False,
            'agent_feedback':{'syntax':{'active_device_lines':len(active),'commented_device_lines':commented,'issues':issues}},
            'note': 'Static syntax/contract checks only; acceptance does not prove execution, functionality or performance.'}


def prepare_candidate(task, parameters=None, ibias_uA=None):
    result = starter(task)
    parameters = {} if parameters is None else parameters
    if not isinstance(parameters, dict):
        raise ValueError('parameters must map starter device names to W/L/M numeric values')
    for name, values in parameters.items():
        if name not in result['parameters'] or not isinstance(values, dict) or set(values) - {'W', 'L', 'M'}:
            raise ValueError('Unknown device or parameter; use the names and W/L/M fields from task_context')
        for key, value in values.items():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Device parameters must be finite numbers')
            result['parameters'][name][key] = value
    missing = [f'{name}.{key}' for name, values in result['parameters'].items()
               for key, value in values.items() if value is None]
    if missing:
        raise ValueError('No default design values. Supply all W/L/M fields: ' + ', '.join(missing))
    lines = []
    for line in result['netlist'].splitlines():
        tokens = line.split()
        if tokens and tokens[0] in result['parameters']:
            line = ' '.join(tokens[:6]) + ' ' + ' '.join(f'{k}={v}' for k, v in result['parameters'][tokens[0]].items())
        lines.append(line)
    result['netlist'] = '\n'.join(lines) + '\n'
    if ibias_uA is not None:
        result['ibias_uA'] = ibias_uA
    result.update(lint_candidate(task, result['netlist'], result['ibias_uA']))
    result['note'] = 'Explicit parameter-generated candidate; submit this exact netlist with evaluate_candidate if accepted. No simulation has run.'
    return result


def check_submission(trace, task, netlist, ibias_uA=None, rationale='', *, force_record=False):
    if not enabled(task):
        return None
    result = lint_candidate(task, netlist, ibias_uA)
    if result['accepted'] and not force_record:
        return None
    identity = uuid.uuid4().hex
    relative = f'preflight/{identity}.json'
    result['report_path'] = relative
    if not result['accepted'] and result['diagnostics']:
        code = result['diagnostics'][0]['code']
        streak = 1
        for path in sorted((trace.root/'preflight').glob('*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
            old = json.loads(path.read_text(encoding='utf-8'))['result']
            if old['accepted'] or not old['diagnostics'] or old['diagnostics'][0]['code'] != code:break
            streak += 1
        if streak > 1:
            result['agent_feedback']['repeated_primary_error']={'code':code,'consecutive_submissions':streak,
                'hint':'Fix the same primary syntax error before changing unrelated parameters.'}
    trace.json(relative, {'task': task, 'netlist': netlist, 'ibias_uA': ibias_uA,
                          'rationale': rationale, 'result': result})
    trace.emit('preflight.completed', **result)
    return result


def success_levels(report):
    assessment = report.get('assessment', {})
    execution = bool(report.get('status') == 'VALID' and assessment.get('status_valid')
                     and assessment.get('nominal_tt_result') and assessment.get('candidate_key'))
    from .curriculum import TASK_IDS
    task = report.get('selected_task')
    functional = bool(assessment.get('functional_valid'))
    if task in TASK_IDS:
        from analog_arena.curriculum.common import CONTRACT, required_functional_checks
        checks = report.get('functional_checks') or {}
        functional = (set(checks) == required_functional_checks(task.split('-')[0])
                      and all(v is True for v in checks.values()) and report.get('measurement_contract') == CONTRACT)
    return {'execution_valid': execution, 'functional_valid': bool(execution and functional),
            'performance_passed': bool(execution and functional and assessment.get('passed'))}


def level_counts(reports):
    levels = [success_levels(r) for r in reports]
    return {k: sum(x[k] for x in levels) for k in ('execution_valid', 'functional_valid', 'performance_passed')}


def preflight_counts(root):
    records = [json.loads(p.read_text(encoding='utf-8'))['result'] for p in (Path(root) / 'preflight').glob('*.json')]
    return {'checks': len(records), 'rejected': sum(not r['accepted'] for r in records)}
