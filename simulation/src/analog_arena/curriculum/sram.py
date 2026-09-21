from __future__ import annotations
from array import array
from .common import at, columns, complete_result, crossing, dump, header, integrate, run_deck, sustained_time, window
from .snm import butterfly

EDGE_NS=.05

def schedule():
    signals={k:[] for k in ['PRE','W0','W1','WL']};operations=[]
    def pulse(name,start,end):signals[name]+=[(start,1.8),(end,0.)]
    def operation(kind,data,start,measured,repeat):
        pulse('PRE',start,start+20)
        if kind=='write':
            pulse('W'+str(data),start+21,start+43)
            wl_on,wl_off=start+23,start+42
            check_start,check_end=start+44,start+46
            recover_end=start+67;hold_start=start+68
        else:
            wl_on,wl_off=start+23,start+42
            check_start,check_end=start+44,start+46
            recover_end=start+67;hold_start=start+68
        pulse('WL',wl_on,wl_off);pulse('PRE',check_end,recover_end)
        end=hold_start+100
        operations.append({'kind':kind,'data':data,'repeat':repeat,'measured':measured,'start_ns':start,'wl_on_ns':wl_on,'wl_off_ns':wl_off,'check_start_ns':check_start,'check_end_ns':check_end,'hold_start_ns':hold_start,'end_ns':end,'energy_end_ns':hold_start})
        return end
    end=operation('write',1,0.,False,0)  # real write prepares the opposite initial state
    for repeat in [1,2]:
        for data in [0,1]:
            end=operation('write',data,end,True,repeat)
            end=operation('read',data,end,True,repeat)
    return signals,operations,end

def pwl(events,end):
    points=[(0.,0.)];value=0.
    for t,new in sorted(events):
        if new==value:continue
        points += [(t,value),(t+EDGE_NS,new)];value=new
    points.append((end,value))
    clean=[]
    for t,val in points:
        if clean and abs(t-clean[-1][0])<1e-10:clean[-1]=(t,val)
        else:clean.append((t,val))
    return 'PWL('+' '.join(f'{t:.8f}n {val:g}' for t,val in clean)+')'

def transient_deck(dut):
    signals,ops,end=schedule()
    deck=header()+f'''.options method=gear
VCELL VDD 0 1.8
VWRITE HIGH 0 1.8
VPRE PREHIGH 0 1.8
VLOW LOW 0 0
VWL WL 0 {pwl(signals['WL'],end)}
VCPRE CPRE 0 {pwl(signals['PRE'],end)}
VCW0 CW0 0 {pwl(signals['W0'],end)}
VCW1 CW1 0 {pwl(signals['W1'],end)}
BP0 BL PREHIGH I={{V(BL,PREHIGH)*(1e-12+(0.01-1e-12)*min(1,max(0,V(CPRE)/1.8)))}}
BP1 BLB PREHIGH I={{V(BLB,PREHIGH)*(1e-12+(0.01-1e-12)*min(1,max(0,V(CPRE)/1.8)))}}
BW0L BL LOW I={{V(BL,LOW)*(1e-12+(0.01-1e-12)*min(1,max(0,V(CW0)/1.8)))}}
BW0H BLB HIGH I={{V(BLB,HIGH)*(1e-12+(0.01-1e-12)*min(1,max(0,V(CW0)/1.8)))}}
BW1H BL HIGH I={{V(BL,HIGH)*(1e-12+(0.01-1e-12)*min(1,max(0,V(CW1)/1.8)))}}
BW1L BLB LOW I={{V(BLB,LOW)*(1e-12+(0.01-1e-12)*min(1,max(0,V(CW1)/1.8)))}}
CBL BL 0 50f
CBLB BLB 0 50f
CQ Q 0 1f
CQB QB 0 1f
{dut}
XDUT Q QB BL BLB WL VDD 0 DUT
.control
set wr_vecnames
set wr_singlescale
set numdgt=15
tran 10p {end:.8f}n 0 2p
wrdata transient.tsv v(Q) v(QB) v(WL) v(BL) v(BLB) i(VCELL) i(VWRITE) i(VPRE) i(VWL)
quit
.endc
.end
'''
    return deck,ops

def snm_deck(mos):
    # Same six devices and sizes. Only storage gates are opened for the DC VTC measurement.
    # Q=fa(SCAN) and QB=fb(SCAN) are measured simultaneously without cross feedback.
    lines=[]
    for m in mos:
        line=m.line(gate='ARENA_SCAN' if m.g in {'Q','QB'} else None)
        line=' '.join('0' if token=='VSS' else token for token in line.split())
        lines.append(line)
    return header()+'''VDD VDD 0 1.8
VWL WL 0 1.8
VBL BL 0 1.8
VBLB BLB 0 1.8
VSCAN ARENA_SCAN 0 0
'''+ '\n'.join(lines)+'''
.control
set wr_vecnames
set wr_singlescale
set numdgt=15
dc VSCAN 0 1.8 0.001
wrdata snm_1mv.tsv v(Q) v(QB)
dc VSCAN 0 1.8 0.0001
wrdata snm_0p1mv.tsv v(Q) v(QB)
quit
.endc
.end
'''

def extract_transient(cols,ops):
    t,q,qb,wl,bl,blb,icell,iwrite,ipre,iwl=cols
    state_margin={0:array('d',(min(.3-a,b-1.5) for a,b in zip(q,qb))),1:array('d',(min(a-1.5,.3-b) for a,b in zip(q,qb)))}
    read_margin={0:array('d',(b-a-.05 for a,b in zip(bl,blb))),1:array('d',(a-b-.05 for a,b in zip(bl,blb)))}
    powers={'cell':array('d',(-1.8*x for x in icell)),'write':array('d',(-1.8*x for x in iwrite)),'precharge':array('d',(-1.8*x for x in ipre)),'wordline':array('d',(-v*i for v,i in zip(wl,iwl)))}
    # Switching feedthrough is allowed up to 0.1V beyond the rails. The strict
    # 0.3/1.5V post-operation levels and read/hold no-flip checks remain mandatory.
    checks={'storage_nodes_in_rails':min(q)>=-.1 and min(qb)>=-.1 and max(q)<=1.9 and max(qb)<=1.9,
            'bitlines_in_rails':min(bl)>=-.1 and min(blb)>=-.1 and max(bl)<=1.9 and max(blb)<=1.9}
    details=[];write_delays=[];read_delays=[];energies=[];standby=[]
    for op in ops:
        d=op['data'];kind=op['kind'];tag=f"{kind}{d}_repeat{op['repeat']}";sm=state_margin[d]
        start=op['start_ns']*1e-9;end=op['end_ns']*1e-9;check_start=op['check_start_ns']*1e-9;check_end=op['check_end_ns']*1e-9;hold=op['hold_start_ns']*1e-9
        on=crossing(t,wl,.9,True,op['wl_on_ns']*1e-9,(op['wl_on_ns']+.1)*1e-9)
        off=crossing(t,wl,.9,False,op['wl_off_ns']*1e-9,(op['wl_off_ns']+.1)*1e-9)
        bitlow,bithigh=(q,qb) if d==0 else (qb,q)
        checks[tag+'_post_operation_levels']=min(window(t,sm,check_start,check_end))>=0
        checks[tag+'_hold_no_flip']=max(window(t,bitlow,hold,end))<.9 and min(window(t,bithigh,hold,end))>.9
        checks[tag+'_hold_final_levels']=min(window(t,sm,end-10e-9,end))>=0
        detail={**op,'wl_cross_s':on,'checks':{}}
        if kind=='write':
            if op['measured']:checks[tag+'_opposite_initial_state']=at(t,state_margin[1-d],on-0.1e-9)>=0
            settled=sustained_time(t,sm,on,check_end)
            checks[tag+'_written']=settled is not None
            delay=None if settled is None else (settled-on)*1e9
            if op['measured']:write_delays.append(delay)
        else:
            checks[tag+'_initial_state']=at(t,sm,on-.1e-9)>=0
            checks[tag+'_read_no_flip']=max(window(t,bitlow,on,off))<.9 and min(window(t,bithigh,on,off))>.9
            sensed=sustained_time(t,read_margin[d],on,off)
            checks[tag+'_sense_50mv']=sensed is not None
            delay=None if sensed is None else (sensed-on)*1e9
            read_delays.append(delay)
        energy_components={k:integrate(t,p,start,hold,positive=True)*1e15 for k,p in powers.items()}
        idle_components={k:integrate(t,p,end-10e-9,end,positive=True)/10e-9*1e9 for k,p in powers.items()}
        if op['measured']:
            energies.append(sum(energy_components.values()))
            if kind=='write':standby.append(sum(idle_components.values()))
        detail.update(delay_ns=delay,energy_fj=energy_components,standby_nw=idle_components,checks={k:v for k,v in checks.items() if k.startswith(tag)})
        details.append(detail)
    m={'write_delay_max_ns':max(write_delays) if len(write_delays)==4 and None not in write_delays else None,
       'read_delay_max_ns':max(read_delays) if len(read_delays)==4 and None not in read_delays else None,
       'operation_energy_max_fj':max(energies),'standby_power_nw':max(standby)}
    return m,checks,{'operations':details,'output_ranges_v':{'Q':[min(q),max(q)],'QB':[min(qb),max(qb)],'BL':[min(bl),max(bl)],'BLB':[min(blb),max(blb)]},'energy_sources':list(powers),'preparation_not_scored':True}

def evaluate(dut,mos,out,timeout):
    deck,ops=transient_deck(dut)
    dump(out/'schedule.json',ops)
    run_deck(out/'transient',deck,timeout)
    m,checks,diagnostics=extract_transient(columns(out/'transient/transient.tsv',10),ops)
    run_deck(out/'read-snm',snm_deck(mos),timeout)
    coarse=butterfly(*columns(out/'read-snm/snm_1mv.tsv',3))
    fine=butterfly(*columns(out/'read-snm/snm_0p1mv.tsv',3))
    delta=abs(fine['read_snm_mv']-coarse['read_snm_mv'])
    m.update(area_mos_um2=sum(v.w*v.l for v in mos),read_snm_mv=fine['read_snm_mv'] if delta<=.5 else None)
    checks.update(read_bistability=fine['bistable'],read_snm_grid_converged=delta<=.5)
    diagnostics.update(read_snm={'coarse':coarse,'fine':fine,'difference_mv':delta,'grid_mv':.1},max_step_ps=2)
    dump(out/'read-snm/measurement.json',diagnostics['read_snm'])
    return complete_result('SRAM',m,checks,diagnostics,2)
