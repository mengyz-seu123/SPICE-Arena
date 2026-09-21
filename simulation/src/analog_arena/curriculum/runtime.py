from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .common import CONTRACT, FIELDS, PROFILES, columns, complete_result, dump, finite, parse_cmos

def evaluate(family,netlist,output,ibias_uA=None,timeout_s=180):
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    (out/'dut-original.spice').write_text(netlist,encoding='utf-8')
    result=None
    try:
        if family=='OTA':
            from analog_arena.cli import evaluate as evaluate_ota
            from analog_arena.evaluation.amplifier.metrics import extract_transient_metrics
            config={'evaluator':{'profile':'sky130-ota','analyses':['op','ac','transient','rejection'],'corners':['TT'],'timeout_s':timeout_s},'constraints':{}}
            result=evaluate_ota(config,str(out/'dut-original.spice'),str(out/'ota'),ibias_uA)
            if result['status']!='VALID':raise ValueError('OTA execution invalid; inspect ota/result.json and stage logs')
            t,y,u,_=columns(out/'ota/tt/tran.tsv',4)
            tr=extract_transient_metrics(list(zip(t,y,u)))
            from .ota import plateau_checks
            plateaus=plateau_checks(t,y)
            checks={k:tr.get(k) is True for k in ['rail_valid','slew_measurable','overshoot_valid','oscillation_free']}
            checks['initial_settled']=plateaus['initial']['all_within_tolerance']
            horizon=(t[-1]-t[0])*1e6
            for side in ['rise','fall']:
                value=tr.get(side+'_settling_time_us')
                checks[side+'_settled']=bool(finite(value) and 0<=value<horizon and plateaus[side]['all_within_tolerance'])
            m=result['metrics'];basevalid=result['metric_validity']
            for k in FIELDS['OTA']:
                if not basevalid.get(k,{}).get('valid'):m[k]=None
            result=complete_result('OTA',m,checks,{'legacy_transient_extraction':tr,'curriculum_plateau_checks':plateaus},len(list((out/'ota').glob('tt/*/execution.json'))))
        else:
            dut,mos=parse_cmos(netlist,family)
            (out/'dut.spice').write_text(dut,encoding='utf-8')
            if family=='INV':
                from .inverter import evaluate as measure
            elif family=='SRAM':
                from .sram import evaluate as measure
            else:raise ValueError('unknown curriculum family')
            result=measure(dut,mos,out,timeout_s)
    except (OSError,ValueError,TypeError,KeyError,ArithmeticError) as exc:
        result={'profile':PROFILES[family],'measurement_contract':CONTRACT,'status':'INVALID','corners':{},'metrics':{k:None for k in FIELDS[family]},'metric_validity':{k:{'valid':False,'reason':str(exc)} for k in FIELDS[family]},'measurement_complete':False,'functional_valid':False,'curriculum_valid':False,'functional_checks':{},'failed_checks':['execution_or_measurement_failed'],'error':str(exc)}
        if getattr(exc, 'diagnostics', None):
            result['diagnostics'] = exc.diagnostics
            result['diagnostics_truncated'] = exc.diagnostics_truncated
    # Count real simulator process records, including failed attempts. OTA parent summaries are not launches.
    result['spice_evaluations']=len(list(out.rglob('process.json')))+len([p for p in out.rglob('execution.json') if p.parent.name in {'core','rejection','transient'}])
    result['candidate_key']=hashlib.sha256((netlist+'\n'+str(ibias_uA)+'\n'+CONTRACT).encode()).hexdigest()
    dump(out/'result.json',result)
    return result
