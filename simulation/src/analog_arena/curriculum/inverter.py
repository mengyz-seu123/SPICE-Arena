from __future__ import annotations
from .common import at, columns, complete_result, crossing, header, integrate, run_deck, window

def evaluate(dut, mos, out, timeout):
    deck=header()+f'''VSUP VDD 0 1.8
VINPUT IN 0 DC 0 PULSE(0 1.8 10n 1n 1n 99n 200n)
{dut}
XDUT IN OUT VDD 0 DUT
CLOAD OUT 0 100f
.control
set wr_vecnames
set wr_singlescale
set numdgt=15
tran 0.1n 620n 0 5p
wrdata transient.tsv v(IN) v(OUT) i(VSUP)
dc VINPUT 0 1.8 0.002
wrdata dc.tsv v(IN) v(OUT) i(VSUP)
quit
.endc
.end
'''
    run_deck(out/'transient-dc',deck,timeout)
    t,vin,vout,current=columns(out/'transient-dc/transient.tsv',4)
    x,dcin,y,dc_current=columns(out/'transient-dc/dc.tsv',4)
    tphl=[];tplh=[];rise=[];fall=[]
    for ns in (210,410):
        lo,hi=ns*1e-9,(ns+40)*1e-9
        tphl.append((crossing(t,vout,.9,False,lo,hi)-crossing(t,vin,.9,True,lo,hi))*1e9)
        fall.append((crossing(t,vout,.18,False,lo,hi)-crossing(t,vout,1.62,False,lo,hi))*1e9)
    for ns in (310,510):
        lo,hi=ns*1e-9,(ns+40)*1e-9
        tplh.append((crossing(t,vout,.9,True,lo,hi)-crossing(t,vin,.9,False,lo,hi))*1e9)
        rise.append((crossing(t,vout,1.62,True,lo,hi)-crossing(t,vout,.18,True,lo,hi))*1e9)
    vm=crossing(x,[a-b for a,b in zip(y,x)],0,False,x[0],x[-1])
    sx=[(a+b)/2 for a,b in zip(x,x[1:])];slope=[(b-a)/(d-c) for a,b,c,d in zip(y,y[1:],x,x[1:])]
    hits=[]
    for i in range(len(slope)-1):
        a,b=slope[i],slope[i+1]
        if (a+1)*(b+1)<0:hits.append(sx[i]+(sx[i+1]-sx[i])*(-1-a)/(b-a))
    left=[v for v in hits if v<vm];right=[v for v in hits if v>vm]
    if not left or not right:raise ValueError('DC transfer has no usable -1 slope noise-margin crossings')
    vil,vih=min(left),max(right)
    power=integrate(t,current,210e-9,610e-9)*(-1.8)/400e-9*1e6
    m={'voh_v':min(window(t,vout,350e-9,400e-9)+window(t,vout,550e-9,600e-9)),
       'vol_v':max(window(t,vout,250e-9,300e-9)+window(t,vout,450e-9,500e-9)),
       'tphl_ns':max(tphl),'tplh_ns':max(tplh),'tpd_max_ns':max(tphl+tplh),'rise_ns':max(rise),'fall_ns':max(fall),'vm_v':vm,
       'power_static_uw':max(0,-1.8*dc_current[0],-1.8*dc_current[-1])*1e6,'power_dynamic_uw':power,
       'area_mos_um2':sum(m.w*m.l for m in mos),'nml_v':vil-y[-1],'nmh_v':y[0]-vih}
    checks={'bidirectional_inversion':True,'nonnegative_delays':min(tphl+tplh)>=0,'positive_edge_times':min(rise+fall)>0,
            'output_in_rails':min(vout)>=-.01 and max(vout)<=1.81,'positive_supply_power':power>0,
            'dc_noise_margins':0<=vil<vm<vih<=1.8 and m['nml_v']>0 and m['nmh_v']>0}
    return complete_result('INV',m,checks,{'output_min_v':min(vout),'output_max_v':max(vout),'mos_count':len(mos),'max_step_ps':5,'load_ff':100,'stage_count_prescribed':False},1)
