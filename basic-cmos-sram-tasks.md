# Circuit task interfaces
The task configuration and evaluator contracts define numerical constraints and measurements.
Use `arena-trace tasks` to inspect tasks and `arena contract` to inspect simulation contracts.

- Inverter: `.subckt DUT IN OUT VDD VSS`.
- SRAM: `.subckt DUT Q QB BL BLB WL VDD VSS`.
- OTA: `.subckt OTA VDD GND VINP VINN VOUT IBIAS`; pass bias separately in microamperes.

Submit only the DUT subcircuit. The evaluator provides supply, stimulus and measurement fixtures. Use SKY130 devices, explicit dimensions and valid body connections. All required functional checks and metrics must pass on the same candidate. See `simulation/tasks/curriculum-l123/` for curriculum interfaces and `agent/src/analog_trace/cmos.py` for legacy CMOS contracts.
