# Amplifier metrics
The executable contract is returned by `arena contract --profile sky130-ota`; task thresholds are defined in `agent/src/analog_trace/tasks.py`.
The eleven required metrics cover gain (dB), GBW (MHz), slew rate (V/us), power (mW), area score, signed phase margin (degrees), rising and falling settling times (us), CMRR (dB), and positive and negative PSRR (dB).
Every metric must be finite, individually valid and satisfy the selected task for one TT candidate. Task1 is stricter than task2. Missing or invalid metrics fail. Area score is a circuit cost proxy, not layout area.
