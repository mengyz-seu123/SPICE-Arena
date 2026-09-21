from __future__ import annotations

from typing import Any

from analog_arena.evaluation.canonical import (
    DEFAULT_OUTPUT_TARGET_V,
    DEFAULT_OUTPUT_TOLERANCE_V,
    DEFAULT_RAIL_TOLERANCE_V,
    DEFAULT_VDD_V,
    DEFAULT_VSS_V,
)


AC_INPUT_COMMON_MODE_V = 0.45
AC_INPUT_AMPLITUDE_V = 1.0
AC_LOAD_PF = 100.0
SUPPLY_VSS_V = DEFAULT_VSS_V
NOMINAL_VDD_V = DEFAULT_VDD_V
TRANSIENT_INPUT_LOW_V = 0.3
TRANSIENT_INPUT_HIGH_V = 0.5
TRANSIENT_RAIL_TOLERANCE_V = 0.0
TRANSIENT_INITIAL_TOLERANCE_FRACTION = 0.01
TRANSIENT_SETTLING_TOLERANCE_FRACTION = 0.01
TRANSIENT_OVERSHOOT_TOLERANCE_V = 0.02


TESTBENCH_CONTRACT: dict[str, Any] = {
    "supplies": {"vss_v": SUPPLY_VSS_V, "nominal_vdd_v": NOMINAL_VDD_V},
    "external_ibias": {
        "port": "IBIAS",
        "source_positive_direction": "IBIAS_to_VSS",
        "source_current_uA": "evaluate --ibias (uA)",
        "spice_fixture": "I_BIAS IBIAS VSS +ibias_uA",
        "dc_conduction_path_required": True,
        "dc_source_path_required_to": "VDD",
        "gate_or_bulk_only_reference_is_invalid": True,
    },
    "unity_gain_ac": {
        "dut_port_binding": {
            "VINP": "dc_common_mode",
            "VINN": "ac_drive_with_dc_output_feedback",
            "VOUT": "output",
        },
        "input_common_mode_v": AC_INPUT_COMMON_MODE_V,
        "ac_input_v": AC_INPUT_AMPLITUDE_V,
        "load_pf": AC_LOAD_PF,
        "sweep_hz": {"start": 0.1, "stop": 10_000_000_000.0, "points_per_decade": 50},
    },
    "unity_gain_transient": {
        "dut_port_binding": {"VINP": "step_input", "VINN": "VOUT", "VOUT": "VOUT"},
        "spice_fixture": "XT VDD VSS step_input VOUT VOUT IBIAS OTA",
        "input_low_v": TRANSIENT_INPUT_LOW_V,
        "input_high_v": TRANSIENT_INPUT_HIGH_V,
        "delay_us": 2.0,
        "rise_time_ns": 1.0,
        "fall_time_ns": 1.0,
        "high_time_us": 4.0,
        "period_us": 20.0,
        "step_ns": 2.0,
        "stop_time_us": 10.0,
        "load_pf": 100.0,
    },
    "validity": {
        "op_output_target_v": DEFAULT_OUTPUT_TARGET_V,
        "op_output_tolerance_v": DEFAULT_OUTPUT_TOLERANCE_V,
        "op_rail_tolerance_v": DEFAULT_RAIL_TOLERANCE_V,
        "transient_rail_tolerance_v": TRANSIENT_RAIL_TOLERANCE_V,
        "transient_initial_tolerance_fraction": TRANSIENT_INITIAL_TOLERANCE_FRACTION,
        "transient_settling_tolerance_fraction": TRANSIENT_SETTLING_TOLERANCE_FRACTION,
        "transient_overshoot_audit_tolerance_v": TRANSIENT_OVERSHOOT_TOLERANCE_V,
        "phase_margin": "signed unwrapped phase at the first downward 0 dB crossing",
    },
}
