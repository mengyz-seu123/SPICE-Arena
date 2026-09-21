from __future__ import annotations
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

from analog_arena.simulation.ngspice import default_ngspice, default_pdk, run_batch, sim_path

CONTRACT = 'sky130-curriculum-l123-free-inv-full-sram-v1'
FIELDS = {
 'INV': ['voh_v','vol_v','tphl_ns','tplh_ns','tpd_max_ns','rise_ns','fall_ns','vm_v','power_static_uw','power_dynamic_uw','area_mos_um2','nml_v','nmh_v'],
 'OTA': ['gain_db','gbw_mhz','sr_min_v_per_us','pm_deg','power_mw','area_score','rise_settling_time_us','fall_settling_time_us','cmrr_db','psrr_plus_db','psrr_minus_db'],
 'SRAM': ['write_delay_max_ns','read_delay_max_ns','read_snm_mv','operation_energy_max_fj','standby_power_nw','area_mos_um2'],
}
PROFILES = {'INV':'sky130-inverter-free-l123-v1','SRAM':'sky130-sram6t-full-l123-v1','OTA':'sky130-ota'}

def required_functional_checks(family):
    if family=='INV':return {'bidirectional_inversion','nonnegative_delays','positive_edge_times','output_in_rails','positive_supply_power','dc_noise_margins'}
    if family=='OTA':return {'rail_valid','slew_measurable','overshoot_valid','oscillation_free','initial_settled','rise_settled','fall_settled'}
    keys={'storage_nodes_in_rails','bitlines_in_rails','read_bistability','read_snm_grid_converged'}
    operations=[('write',1,0)]+[(kind,data,repeat) for repeat in (1,2) for data in (0,1) for kind in ('write','read')]
    for kind,data,repeat in operations:
        tag=f'{kind}{data}_repeat{repeat}'
        suffixes=['post_operation_levels','hold_no_flip','hold_final_levels']
        if kind=='write':suffixes+=['written']+(['opposite_initial_state'] if repeat else [])
        else:suffixes+=['initial_state','read_no_flip','sense_50mv']
        keys.update(tag+'_'+s for s in suffixes)
    return keys

def finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

def dump(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

@dataclass(frozen=True)
class Mos:
    name: str
    d: str
    g: str
    s: str
    b: str
    model: str
    w: float
    l: float

    @property
    def pmos(self):return 'pfet' in self.model

    def line(self, gate=None):
        return f'{self.name} {self.d} {gate or self.g} {self.s} {self.b} {self.model} W={self.w:g} L={self.l:g} M=1'

def parse_cmos(text, family):
    """Restricted device syntax, but no inverter stage-count or device-name assumptions."""
    if family not in {'INV','SRAM'}:raise ValueError('CMOS family required')
    lines=[s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('*')]
    ports='IN OUT VDD VSS' if family=='INV' else 'Q QB BL BLB WL VDD VSS'
    if len(lines)<4 or lines[0].upper().split()!=['.SUBCKT','DUT',*ports.split()] or lines[-1].upper().split()!=['.ENDS','DUT']:
        raise ValueError(f'DUT interface must be .subckt DUT {ports}, ending .ends DUT')
    mos=[];names=set()
    for line in lines[1:-1]:
        t=line.split()
        if len(t) not in (8,9) or not re.fullmatch(r'X[A-Za-z0-9_]+',t[0],re.I):raise ValueError('DUT may contain only SKY130 MOS instances; no sources, analyses, includes or nested circuits')
        name=t[0].upper()
        if name in names:raise ValueError('duplicate device name')
        names.add(name)
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',x) for x in t[1:5]):raise ValueError('use named DUT nodes and the declared VSS port')
        model=t[5].lower()
        if model not in {'sky130_fd_pr__nfet_01v8','sky130_fd_pr__pfet_01v8'}:raise ValueError('only ordinary SKY130 1.8V MOS models are allowed')
        params={}
        for token in t[6:]:
            if token.count('=')!=1:raise ValueError('use W=<um> L=<um> M=1')
            k,v=token.split('=');k=k.upper()
            if k in params:raise ValueError('duplicate MOS parameter')
            params[k]=float(v)
        if set(params) not in ({'W','L'},{'W','L','M'}) or params.get('M',1)!=1:raise ValueError('only W, L and optional M=1 accepted')
        w,l=params['W'],params['L'];wmax,lmax=(50,2) if family=='INV' else (10,1)
        if not finite(w) or not finite(l) or not .42<=w<=wmax or not .15<=l<=lmax:raise ValueError(f'W must be .42..{wmax} um; L .15..{lmax} um')
        if family=='SRAM' and any(abs(v*100-round(v*100))>1e-7 for v in [w,l]):raise ValueError('SRAM W/L require a .01um grid')
        d,g,s,b=[x.upper() for x in t[1:5]]
        if b!=('VDD' if 'pfet' in model else 'VSS'):raise ValueError('tie ordinary PMOS bodies to VDD and NMOS bodies to VSS')
        mos.append(Mos(name,d,g,s,b,model,w,l))
    if family=='INV':
        nodes={n for m in mos for n in (m.d,m.g,m.s)}
        if not {'IN','OUT','VDD','VSS'}<=nodes:raise ValueError('all INV ports must be connected')
        if any(m.d=='IN' or m.s=='IN' for m in mos):raise ValueError('IN is an input gate port, not a power source')
        if not any(m.d=='OUT' or m.s=='OUT' for m in mos):raise ValueError('OUT must be driven by DUT devices')
    else:validate_sram(mos)
    canonical='\n'.join([f'.subckt DUT {ports}',*[m.line() for m in mos],'.ends DUT'])+'\n'
    return canonical,mos

def validate_sram(mos):
    if len(mos)!=6:raise ValueError('SRAM task requires a six-transistor cell')
    expected=[]
    for q,qb,bl in [('Q','QB','BL'),('QB','Q','BLB')]:
        expected += [(True,qb,'VDD',{q,'VDD'}),(False,qb,'VSS',{q,'VSS'}),(False,'WL','VSS',{q,bl})]
    unmatched=list(mos)
    for pmos,gate,body,ends in expected:
        hit=next((m for m in unmatched if m.pmos==pmos and m.g==gate and m.b==body and {m.d,m.s}==ends),None)
        if hit is None:raise ValueError('SRAM must contain cross-coupled CMOS inverters and two WL-controlled access NMOS')
        unmatched.remove(hit)

def header():
    return f'* Fixed external curriculum fixture\n.lib "{sim_path(default_pdk())}" tt\n.temp 27\n'

def run_deck(out, deck, timeout):
    out.mkdir(parents=True,exist_ok=False)
    (out/'tb.spice').write_text(deck,encoding='utf-8')
    process=run_batch(out,executable=default_ngspice(),timeout_s=timeout)
    if process['status']!='VALID':raise ValueError(f'ngspice failed; inspect {out.name}/process.json and ngspice.log')
    return process

def columns(path, width):
    """Compact arrays keep long SRAM waveforms out of Python object-per-value storage."""
    cols=[array('d') for _ in range(width)]
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            fields=line.split()
            try:row=[float(x) for x in fields]
            except ValueError:
                if not cols[0]:continue
                raise ValueError(f'malformed waveform row in {path.name}')
            if len(row)!=width or not all(math.isfinite(x) for x in row):raise ValueError(f'invalid waveform in {path.name}')
            if cols[0] and row[0]<cols[0][-1]:raise ValueError('waveform axis decreased')
            if cols[0] and row[0]==cols[0][-1]:
                for c,v in zip(cols,row):c[-1]=v
            else:
                for c,v in zip(cols,row):c.append(v)
    if len(cols[0])<2:raise ValueError('missing waveform samples')
    return cols

def at(x,y,t):
    if t<x[0]-1e-15 or t>x[-1]+1e-15:raise ValueError('waveform does not cover requested window')
    i=min(max(bisect_right(x,t)-1,0),len(x)-2)
    return y[i]+(y[i+1]-y[i])*(t-x[i])/(x[i+1]-x[i])

def window(x,y,lo,hi):
    return [at(x,y,lo),*y[bisect_right(x,lo):bisect_left(x,hi)],at(x,y,hi)]

def crossing(x,y,threshold,up,lo,hi):
    hits=[]
    for i in range(max(0,bisect_left(x,lo)-1),min(len(x)-1,bisect_right(x,hi))):
        a,b=y[i],y[i+1]
        if (a<threshold<=b) if up else (a>threshold>=b):
            t=x[i]+(x[i+1]-x[i])*(threshold-a)/(b-a)
            if lo<=t<=hi:hits.append(t)
    if len(hits)!=1:raise ValueError(f'expected one {"rising" if up else "falling"} crossing of {threshold}; got {len(hits)}')
    return hits[0]

def integrate(x,y,lo,hi,positive=False):
    total=0.;lastx=lo;last=at(x,y,lo)
    for i in range(bisect_right(x,lo),bisect_left(x,hi)+1):
        curx=x[i] if i<len(x) and x[i]<hi else hi
        cur=y[i] if curx<hi else at(x,y,hi)
        dt=curx-lastx
        if positive:
            if last>=0 and cur>=0:total+=(last+cur)*dt/2
            elif last>0:total+=dt*last*last/(2*(last-cur))
            elif cur>0:total+=dt*cur*cur/(2*(cur-last))
        else:total+=(last+cur)*dt/2
        lastx,last=curx,cur
    return total

def sustained_time(x, margin, lo, hi):
    """First time a piecewise-linear margin remains nonnegative through hi."""
    start=at(x,margin,lo);end=at(x,margin,hi)
    if end<0:return None
    bad_time=None;bad_value=None;next_time=None;next_value=None
    if start<0:bad_time,bad_value=lo,start
    for i in range(bisect_right(x,lo),bisect_left(x,hi)):
        if margin[i]<0:bad_time,bad_value=x[i],margin[i];next_time=None
        elif bad_time is not None and next_time is None:next_time,next_value=x[i],margin[i]
    if bad_time is None:return lo
    if next_time is None:next_time,next_value=hi,end
    return bad_time+(next_time-bad_time)*(-bad_value)/(next_value-bad_value)

def complete_result(family, metrics, checks, diagnostics, process_count):
    if set(checks)!=required_functional_checks(family):raise ValueError('Incomplete functional check schema')
    validity={k:{'valid':finite(metrics.get(k)),'reason':None if finite(metrics.get(k)) else 'missing_or_invalid_measurement'} for k in FIELDS[family]}
    complete=all(v['valid'] for v in validity.values())
    return {'profile':PROFILES[family],'measurement_contract':CONTRACT,'status':'VALID','corners':{'TT':{'status':'VALID'}},'metrics':metrics,'metric_validity':validity,'measurement_complete':complete,'functional_checks':checks,'functional_valid':bool(checks) and all(checks.values()),'curriculum_valid':bool(complete and checks and all(checks.values())),'failed_checks':[k for k,v in checks.items() if not v],'diagnostics':diagnostics,'spice_evaluations':process_count}
