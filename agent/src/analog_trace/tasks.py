from __future__ import annotations
import copy
import math
from pathlib import Path
from .curriculum import TASK_IDS as CURRICULUM_IDS
from .analogcoderpro import TASK_IDS as ANALOGCODERPRO_IDS

ROOT = Path(__file__).resolve().parents[3]
SIMULATION_ROOT = ROOT / 'simulation'
AGENT_ROOT = ROOT / 'agent'
TASK_ROOT = SIMULATION_ROOT / 'tasks'
CONTRACT = 'unified-nominal-ota-v4-signed-pm'
TARGETS = {
    'task1': {'gain_db': 120.0, 'gbw_mhz': 2.0, 'sr_min_v_per_us': 0.6},
    'task2': {'gain_db': 100.0, 'gbw_mhz': 1.0, 'sr_min_v_per_us': 0.5},
}
OTA_TARGETS = {'gain_db': 65.0, 'gbw_mhz': 0.8, 'sr_min_v_per_us': 0.5}
OTA_GATES = {
    'power_mw': {'max': 0.5}, 'area_score': {'max': 150.0}, 'pm_deg': {'min': 45.0},
    'rise_settling_time_us': {'max': 4.0}, 'fall_settling_time_us': {'max': 4.0},
    'cmrr_db': {'min': 40.0}, 'psrr_plus_db': {'min': 40.0}, 'psrr_minus_db': {'min': 40.0},
}
GATES = {
    'power_mw': {'max': 0.5}, 'area_score': {'max': 150.0},
    'pm_deg': {'min': 45.0}, 'rise_settling_time_us': {'max': 1.0},
    'fall_settling_time_us': {'max': 1.0}, 'cmrr_db': {'min': 80.0},
    'psrr_plus_db': {'min': 80.0}, 'psrr_minus_db': {'min': 80.0},
}
RELATION = 'Task1 is stricter: its feasible set is a subset of task2.'


def task_config(task):
    if task in ANALOGCODERPRO_IDS:
        from .analogcoderpro import config
        return config(task)
    if task in CURRICULUM_IDS:
        from .curriculum import config
        return config(task)
    if task == 'ota':
        return {'evaluator': {'profile': 'sky130-ota', 'analyses': ['op', 'ac', 'transient', 'rejection'],
                              'corners': ['TT'], 'timeout_s': 180},
                'objectives': {k: 'max' for k in OTA_TARGETS},
                'constraints': {**{k: {'min': v} for k, v in OTA_TARGETS.items()}, **copy.deepcopy(OTA_GATES)}}
    if task in {'inverter', 'sram6t'}:
        from .cmos import CONTRACTS
        return {'evaluator': {'profile': CONTRACTS[task]['profile'], 'analyses': ['transient'], 'corners': ['TT'], 'timeout_s': 180},
                'objectives': {}, 'constraints': copy.deepcopy(CONTRACTS[task]['metrics'])}
    if task not in TARGETS:
        raise ValueError('unknown task')
    return {'evaluator': {'profile': 'sky130-ota', 'analyses': ['op', 'ac', 'transient', 'rejection'],
                          'corners': ['TT'], 'timeout_s': 180},
            'objectives': {k: 'max' for k in TARGETS[task]},
            'constraints': {**{k: {'min': v} for k, v in TARGETS[task].items()}, **copy.deepcopy(GATES)}}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def assess(result, task):
    """Check every metric from this one stored simulation only."""
    metrics = result.get('metrics') or {}
    validity = result.get('metric_validity') or {}
    checks = {}
    for name, limits in task_config(task)['constraints'].items():
        value = metrics.get(name)
        detail = validity.get(name)
        valid = finite(value) and isinstance(detail, dict) and detail.get('valid') is True
        passed = valid and all(value >= v if op == 'min' else value <= v for op, v in limits.items())
        checks[name] = {'value': value if finite(value) else None, 'limits': limits,
                        'valid': valid, 'passed': passed,
                        'reason': None if passed else ('missing_nonfinite_or_invalid_metric' if not valid else 'threshold_failed')}
    expected_profile = task_config(task)['evaluator']['profile']
    same_condition = result.get('profile') == expected_profile and set(result.get('corners') or {}) == {'TT'}
    status_valid = result.get('status') == 'VALID'
    candidate = result.get('candidate_key')
    functional_valid = True
    relation = RELATION
    if task in ANALOGCODERPRO_IDS:
        from .analogcoderpro import RELATION as analogcoderpro_relation
        topology = result.get('topology_check') or {}
        functional_valid = bool(status_valid and topology.get('accepted') is True
                                and all((validity.get(name) or {}).get('valid') is True
                                        for name in task_config(task)['constraints']))
        relation = analogcoderpro_relation
    elif task in CURRICULUM_IDS:
        from .curriculum import CONTRACT as curriculum_contract, RELATION as curriculum_relation
        from analog_arena.curriculum.common import required_functional_checks
        fc = result.get('functional_checks')
        functional_valid = bool(result.get('curriculum_valid') is True and result.get('functional_valid') is True
                                and result.get('measurement_complete') is True and isinstance(fc, dict) and fc
                                and set(fc)==required_functional_checks(task.split('-')[0])
                                and all(v is True for v in fc.values()) and result.get('measurement_contract') == curriculum_contract
                                and (result.get('corners', {}).get('TT') or {}).get('status') == 'VALID')
        relation = curriculum_relation
    return {'task': task, 'passed': bool(status_valid and same_condition and candidate and functional_valid and all(c['passed'] for c in checks.values())),
            'status_valid': status_valid, 'nominal_tt_result': same_condition,
            'candidate_key': candidate, 'checks': checks, 'functional_valid': functional_valid, 'relation': relation,
            'scope': 'stored result check; measurement provenance must be verified separately'}


def is_ota(task):
    return task in {'task1', 'task2', 'ota'} or (task in CURRICULUM_IDS and task.startswith('OTA-'))


def rank(assessment):
    """Heuristic for ordering candidates, never a substitute for pass/fail."""
    checks = assessment['checks'].values()
    valid_count = sum(c['valid'] for c in checks)
    passed_count = sum(c['passed'] for c in checks)
    violation = 0.0
    for c in checks:
        if not c['valid']:
            violation += 10.0
            continue
        for op, limit in c['limits'].items():
            violation += max(0.0, (limit - c['value'] if op == 'min' else c['value'] - limit) / max(abs(limit), 1e-12))
    return (assessment['passed'], assessment['status_valid'], valid_count, passed_count, -violation)
