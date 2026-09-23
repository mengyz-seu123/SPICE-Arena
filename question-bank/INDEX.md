# Task index

78 tasks; original public and author IDs retained. Titles are translated into English.

## Construction / modification

### OTA (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC011](construction-modification/OTA/RC011/task.json) | ST2-OTA-T01 | Replace a resistive load with an active load | None |
| [RC012](construction-modification/OTA/RC012/task.json) | ST2-OTA-T02 | Add a second gain stage | None |
| [RC013](construction-modification/OTA/RC013/task.json) | ST2-OTA-T03 | Complete the output bias branch | None |
| [RC014](construction-modification/OTA/RC014/task.json) | ST2-OTA-T04 | Establish a compensation path | None |
| [RC015](construction-modification/OTA/RC015/task.json) | ST2-OTA-T05 | Repair the series compensation connection | None |
| [RC016](construction-modification/OTA/RC016/task.json) | ST2-OTA-T06 | Add an output driver and resolve its interface | None |

### INV (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC031](construction-modification/INV/RC031/task.json) | ST2-INV-T01 | Extend a single stage into an inverting buffer chain | None |
| [RC032](construction-modification/INV/RC032/task.json) | ST2-INV-T02 | Complete the intermediate inverter stage | None |
| [RC033](construction-modification/INV/RC033/task.json) | ST2-INV-T03 | Repair interstage connections | None |
| [RC034](construction-modification/INV/RC034/task.json) | ST2-INV-T04 | Increase the drive of the preceding stage | None |
| [RC035](construction-modification/INV/RC035/task.json) | ST2-INV-T05 | Identify and remove parasitic MOS loads | None |
| [RC036](construction-modification/INV/RC036/task.json) | ST2-INV-T06 | Repair an unnecessary series-device connection | None |

### CMP (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC051](construction-modification/CMP/RC051/task.json) | ST2-CMP-T01 | Build an output stage from a preamplifier | None |
| [RC052](construction-modification/CMP/RC052/task.json) | ST2-CMP-T02 | Complete the output pull-up branch | None |
| [RC053](construction-modification/CMP/RC053/task.json) | ST2-CMP-T03 | Complete the output pull-down branch | None |
| [RC054](construction-modification/CMP/RC054/task.json) | ST2-CMP-T04 | Increase drive while preserving decision polarity | None |
| [RC055](construction-modification/CMP/RC055/task.json) | ST2-CMP-T05 | Remove an unnecessary output capacitor | None |
| [RC056](construction-modification/CMP/RC056/task.json) | ST2-CMP-T06 | Repair the preamplifier active load | None |

## Diagnosis / repair

### OTA (8)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC001](diagnosis-repair/OTA/RC001/task.json) | ST2-OTA-B01 | Establish the current-mirror reference branch | None |
| [RC002](diagnosis-repair/OTA/RC002/task.json) | ST2-OTA-B02 | Match input common-mode voltage and bias | None |
| [RC003](diagnosis-repair/OTA/RC003/task.json) | ST2-OTA-B03 | Restore the interstage operating point | None |
| [RC004](diagnosis-repair/OTA/RC004/task.json) | ST2-OTA-B04 | Correct polarity after adding an inverting stage | None |
| [RC017](diagnosis-repair/OTA/RC017/task.json) | ST2-OTA-R01 | Undo a change that degrades phase margin | None |
| [RC018](diagnosis-repair/OTA/RC018/task.json) | ST2-OTA-R02 | Recover from a rail-saturated student candidate | student_failure |
| [RC019](diagnosis-repair/OTA/RC019/task.json) | ST2-OTA-S01 | Stop when core and resource constraints are satisfied | passing_candidate |
| [RC020](diagnosis-repair/OTA/RC020/task.json) | ST2-OTA-S02 | Retain the best previously passing candidate | passing_candidate |

### INV (8)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC021](diagnosis-repair/INV/RC021/task.json) | ST2-INV-B01 | Restore supply and bulk connections | None |
| [RC022](diagnosis-repair/INV/RC022/task.json) | ST2-INV-B02 | Restore the pull-up path | None |
| [RC023](diagnosis-repair/INV/RC023/task.json) | ST2-INV-B03 | Restore the pull-down path | None |
| [RC024](diagnosis-repair/INV/RC024/task.json) | ST2-INV-B04 | Restore the polarity of cascaded inverters | None |
| [RC037](diagnosis-repair/INV/RC037/task.json) | ST2-INV-R01 | Undo an unbalanced sizing change | None |
| [RC038](diagnosis-repair/INV/RC038/task.json) | ST2-INV-R02 | Recover from a student cascade error | student_failure |
| [RC039](diagnosis-repair/INV/RC039/task.json) | ST2-INV-S01 | Satisfy delay, logic-level, and resource constraints | passing_candidate |
| [RC040](diagnosis-repair/INV/RC040/task.json) | ST2-INV-S02 | Select a previously passing inverter | passing_candidate |

### CMP (8)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC041](diagnosis-repair/CMP/RC041/task.json) | ST2-CMP-B01 | Establish the tail-current reference | None |
| [RC042](diagnosis-repair/CMP/RC042/task.json) | ST2-CMP-B02 | Adapt to the input common-mode voltage | None |
| [RC043](diagnosis-repair/CMP/RC043/task.json) | ST2-CMP-B03 | Repair decision polarity | None |
| [RC044](diagnosis-repair/CMP/RC044/task.json) | ST2-CMP-B04 | Restore bias from the preamplifier to the output | None |
| [RC057](diagnosis-repair/CMP/RC057/task.json) | ST2-CMP-R01 | Undo a speedup that degrades one output level | None |
| [RC058](diagnosis-repair/CMP/RC058/task.json) | ST2-CMP-R02 | Recover from a stuck student output | student_failure |
| [RC059](diagnosis-repair/CMP/RC059/task.json) | ST2-CMP-S01 | Stop after both decision directions pass | passing_candidate |
| [RC060](diagnosis-repair/CMP/RC060/task.json) | ST2-CMP-S02 | Select a previously passing comparator | passing_candidate |

## Parameter optimization

### OTA (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC005](parameter-optimization/OTA/RC005/task.json) | ST2-OTA-P01 | Improve insufficient gain with speed headroom | None |
| [RC006](parameter-optimization/OTA/RC006/task.json) | ST2-OTA-P02 | Improve insufficient GBW with phase-margin headroom | None |
| [RC007](parameter-optimization/OTA/RC007/task.json) | ST2-OTA-P03 | Improve insufficient GBW near the phase-margin limit | None |
| [RC008](parameter-optimization/OTA/RC008/task.json) | ST2-OTA-P04 | Improve insufficient positive slew rate | None |
| [RC009](parameter-optimization/OTA/RC009/task.json) | ST2-OTA-P05 | Improve insufficient negative slew rate | None |
| [RC010](parameter-optimization/OTA/RC010/task.json) | ST2-OTA-P06 | Improve insufficient phase margin with GBW headroom | None |

### INV (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC025](parameter-optimization/INV/RC025/task.json) | ST2-INV-P01 | Improve falling-edge delay | None |
| [RC026](parameter-optimization/INV/RC026/task.json) | ST2-INV-P02 | Improve rising-edge delay | None |
| [RC027](parameter-optimization/INV/RC027/task.json) | ST2-INV-P03 | Improve insufficient drive in both directions | None |
| [RC028](parameter-optimization/INV/RC028/task.json) | ST2-INV-P04 | Adjust channel length to improve delay | None |
| [RC029](parameter-optimization/INV/RC029/task.json) | ST2-INV-P05 | Allocate device sizes across stages | None |
| [RC030](parameter-optimization/INV/RC030/task.json) | ST2-INV-P06 | Reduce resource use in an oversized circuit | None |

### CMP (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC045](parameter-optimization/CMP/RC045/task.json) | ST2-CMP-P01 | Improve positive-direction decision delay | None |
| [RC046](parameter-optimization/CMP/RC046/task.json) | ST2-CMP-P02 | Improve negative-direction decision delay | None |
| [RC047](parameter-optimization/CMP/RC047/task.json) | ST2-CMP-P03 | Improve preamplifier response | None |
| [RC048](parameter-optimization/CMP/RC048/task.json) | ST2-CMP-P04 | Correct input-pair imbalance | None |
| [RC049](parameter-optimization/CMP/RC049/task.json) | ST2-CMP-P05 | Restore the output high level | None |
| [RC050](parameter-optimization/CMP/RC050/task.json) | ST2-CMP-P06 | Restore the output low level | None |

## Multi-step design

### OTA (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC061](multi-step-design/OTA/RC061/task.json) | TR2-OTA-A01 | Set initial sizing and bias for a five-transistor OTA | None |
| [RC062](multi-step-design/OTA/RC062/task.json) | TR2-OTA-A02 | Set the initial operating point with a resistive load | None |
| [RC063](multi-step-design/OTA/RC063/task.json) | TR2-OTA-B01 | Increase gain from a weak active-load stage | None |
| [RC064](multi-step-design/OTA/RC064/task.json) | TR2-OTA-B02 | Build an active-load structure from a resistive-load stage | None |
| [RC065](multi-step-design/OTA/RC065/task.json) | TR2-OTA-C01 | Coordinate compensation and speed after improving gain | None |
| [RC066](multi-step-design/OTA/RC066/task.json) | TR2-OTA-C02 | Recover from a failed student state and stop on success | student_failure |

### INV (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC067](multi-step-design/INV/RC067/task.json) | TR2-INV-A01 | Set initial sizes for a single-stage inverter | None |
| [RC068](multi-step-design/INV/RC068/task.json) | TR2-INV-A02 | Allocate initial sizes across three stages | None |
| [RC069](multi-step-design/INV/RC069/task.json) | TR2-INV-B01 | Extend a single stage into an inverting buffer chain | None |
| [RC070](multi-step-design/INV/RC070/task.json) | TR2-INV-B02 | Complete the intermediate stage and restore drive | None |
| [RC071](multi-step-design/INV/RC071/task.json) | TR2-INV-C01 | Reallocate sizes and correct speed in both directions | None |
| [RC072](multi-step-design/INV/RC072/task.json) | TR2-INV-C02 | Recover from a student cascade error through multiple steps | student_failure |

### CMP (6)

| ID | Original ID | Title | Runtime dependency |
|---|---|---|---|
| [RC073](multi-step-design/CMP/RC073/task.json) | TR2-CMP-A01 | Set preamplifier bias and device sizes | None |
| [RC074](multi-step-design/CMP/RC074/task.json) | TR2-CMP-A02 | Allocate output-driver device sizes | None |
| [RC075](multi-step-design/CMP/RC075/task.json) | TR2-CMP-B01 | Build an output stage from a working preamplifier | None |
| [RC076](multi-step-design/CMP/RC076/task.json) | TR2-CMP-B02 | Complete the driver branch | None |
| [RC077](multi-step-design/CMP/RC077/task.json) | TR2-CMP-C01 | Correct bias, output levels, and delay through multiple steps | None |
| [RC078](multi-step-design/CMP/RC078/task.json) | TR2-CMP-C02 | Recover from a student decision failure in one direction | student_failure |
