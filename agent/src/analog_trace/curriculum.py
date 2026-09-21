"""Active introductory curriculum. Historical task IDs remain explicit legacy calls."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from analog_arena.curriculum.common import CONTRACT, FIELDS, PROFILES

ROOT=Path(__file__).resolve().parents[3]
DATA=ROOT/'simulation/tasks/curriculum-l123'
TASK_IDS=tuple(f'{family}-L{level}-D{direction:02d}' for family in ('INV','OTA','SRAM') for level in (1,2,3) for direction in (1,2,3))
RELATION='L1-L3 progressively tighten each direction. Levels are curriculum hypotheses, not calibrated performance boundaries. L4/L5 are not released.'

def config(task):
    if task not in TASK_IDS:raise ValueError('Unknown active curriculum task; L4/L5 are not released')
    data=json.loads((DATA/'tasks.json').read_text(encoding='utf-8'))
    if data.get('measurement_contract')!=CONTRACT or set(data['tasks'])!=set(TASK_IDS):raise ValueError('Curriculum catalog/measurement contract mismatch')
    result=copy.deepcopy(data['tasks'][task])
    family=task.split('-')[0]
    if set(result['constraints'])!=set(FIELDS[family]) or result['evaluator']['profile']!=PROFILES[family]:raise ValueError('Incomplete curriculum metrics or wrong profile')
    return result

def contract(task):
    cfg=config(task);family=task.split('-')[0]
    return {'measurement_contract':CONTRACT,'profile':PROFILES[family],'family':family,
            'evaluator':cfg['evaluator'],'required_metrics':FIELDS[family],
            'functional_valid_required':True,'specification':(DATA/f'{family}-contract.md').read_text(encoding='utf-8')}

def context(task):
    family=task.split('-')[0]
    result={'task':task,'config':config(task),'relation':RELATION,'contract':contract(task),
            'example_netlist':(DATA/'templates'/f'{family}.spice').read_text(encoding='utf-8'),
            'example_note':'Format example only, not an executable candidate. Design a complete DUT with valid connections, dimensions and bias. Follow the contract and config; the evaluator supplies the testbench.',
            'template_kind':'format_only',
            'template_revision':'format-only-20260911-v1',
            'metrics_spec':'All metrics and functional checks must be valid and pass for one candidate in one evaluation. Do not combine candidates or skip failure diagnostics.'}
    if family in {'INV','OTA'}:
        from .submission import starter, REVISION
        initial = starter(task)
        result.update(example_netlist=initial['netlist'], template_kind='topology_without_values',
                      template_revision=REVISION, workflow_revision=REVISION,
                      example_note='The starting topology preserves devices and connections without default dimensions or bias. Device examples illustrate syntax only. Topology edits are allowed; run lint_candidate afterward. Static acceptance does not establish simulation, functional or performance success.',
                      device_syntax_examples=(DATA/'device-syntax.md').read_text(encoding='utf-8'),
                      topology_changes_allowed=True,
                      starter_parameters=initial['parameters'],
                      success_levels=['execution_valid','functional_valid','performance_passed'],
                      submission_policy={'preflight_required':True,'rejected_submissions_consume_evaluation_budget':False,
                                         'rejected_submissions_recorded_separately':True})
    if family=='OTA':
        result['ibias_format']={'parameter':'ibias_uA','type':'number','unit':'uA','note':'Choose bias explicitly. There is no default; see the contract for valid limits.'}
    return result
