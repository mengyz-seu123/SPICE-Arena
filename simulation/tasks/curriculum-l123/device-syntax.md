# INV device syntax
Each line is an independent syntax example, not a complete circuit or recommended sizing.
```spice
XN_EX DN GN SN VSS sky130_fd_pr__nfet_01v8 W=0.60 L=0.18 M=1
XP_EX DP GP SP VDD sky130_fd_pr__pfet_01v8 W=1.20 L=0.20 M=1
```
Order: instance, drain, gate, source, body, model, W, L, M. Use one X-prefixed instance name, not separate X and name tokens. W/L are bare micrometre values. Follow the task contract for device limits. Choose every W/L/M explicitly; prepare_candidate supplies no defaults. Topology edits are allowed; lint the complete DUT afterward.

Only contract-permitted MOS devices are allowed; no resistors, capacitors or independent sources.

# OTA device syntax
Each line is an independent syntax example, not a complete circuit or recommended sizing.
```spice
XN_EX DN GN SN GND sky130_fd_pr__nfet_01v8 W=0.60 L=0.18 M=1
XP_EX DP GP SP VDD sky130_fd_pr__pfet_01v8 W=1.20 L=0.20 M=1
```
Order: instance, drain, gate, source, body, model, W, L, M. Use one X-prefixed instance name, not separate X and name tokens. W/L are bare micrometre values. Follow the task contract for device limits. Choose every W/L/M explicitly; prepare_candidate supplies no defaults. Topology edits are allowed; lint the complete DUT afterward.

Resistor example: `R_EX RA RB 10k`; capacitor example: `C_EX CA CB 2p`. Pass chosen bias separately as ibias_uA, following the contract range and grid.
