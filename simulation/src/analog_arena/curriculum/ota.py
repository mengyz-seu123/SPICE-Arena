"""Curriculum plateau checks exclude the driven input transition itself."""
from .common import window
from analog_arena.simulation.amplifier.testbench import TESTBENCH_CONTRACT

def plateau_checks(t,y):
    tb=TESTBENCH_CONTRACT['unity_gain_transient']
    rise=tb['delay_us']*1e-6
    fall=rise+tb['rise_time_ns']*1e-9+tb['high_time_us']*1e-6
    end=tb['stop_time_us']*1e-6
    windows={'initial':(rise-.2e-6,rise,tb['input_low_v']),
             'rise':(fall-.4e-6,fall,tb['input_high_v']),
             'fall':(end-.4e-6,end,tb['input_low_v'])}
    result={}
    for name,(lo,hi,target) in windows.items():
        errors=[v-target for v in window(t,y,lo,hi)]
        tolerance=.01*abs(target)
        result[name]={'window_s':[lo,hi],'target_v':target,'tolerance_v':tolerance,
                      'max_abs_error_v':max(abs(e) for e in errors),
                      'all_within_tolerance':all(abs(e)<=tolerance for e in errors)}
    return result
