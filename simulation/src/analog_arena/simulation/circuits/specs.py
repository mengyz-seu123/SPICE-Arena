from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


MOS_1V8 = frozenset(
    {"sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8"}
)
MOS_HV = frozenset(
    {"sky130_fd_pr__nfet_g5v0d10v5", "sky130_fd_pr__pfet_g5v0d10v5"}
)
BJT = frozenset(
    {
        "sky130_fd_pr__pnp_05v5_W0p68L0p68",
        "sky130_fd_pr__pnp_05v5_W3p40L3p40",
        "sky130_fd_pr__npn_05v5_W1p00L1p00",
        "sky130_fd_pr__npn_05v5_W1p00L2p00",
    }
)


@dataclass(frozen=True)
class MetricSpec:
    analysis: str
    worst: str
    unit: str


@dataclass(frozen=True)
class CircuitSpec:
    id: str
    description: str
    top_subcircuit: str
    ports: tuple[str, ...]
    analyses: tuple[str, ...]
    metrics: Mapping[str, MetricSpec]
    fixture: str
    allowed_models: frozenset[str]
    evidence_level: str = "transistor-level-reference"
    required_model_groups: tuple[frozenset[str], ...] = ()

    @property
    def fixture_path(self) -> Path:
        return Path(__file__).with_name("fixtures") / self.fixture


def _m(analysis: str, worst: str, unit: str) -> MetricSpec:
    return MetricSpec(analysis, worst, unit)


_CIRCUITS: dict[str, CircuitSpec] = {
    "dac3": CircuitSpec(
        "dac3",
        "3-bit transmission-gate resistor DAC",
        "DUT",
        ("VDD", "VSS", "B0", "B1", "B2", "VREF", "VOUT"),
        ("transient",),
        {
            "max_inl_lsb": _m("transient", "max", "LSB"),
            "max_dnl_lsb": _m("transient", "max", "LSB"),
            "min_code_step_v": _m("transient", "min", "V"),
            "output_span_v": _m("transient", "min", "V"),
            "settling_time_ns": _m("transient", "max", "ns"),
        },
        "dac3.spice.tmpl",
        MOS_1V8,
        "process-switches-with-resistor-network",
        (MOS_1V8,),
    ),
    "logic-combinational-3stage": CircuitSpec(
        "logic-combinational-3stage",
        "three-stage combinational logic chain",
        "DUT",
        ("VDD", "VSS", "A", "Y"),
        ("transient",),
        {
            "logic_pass_count": _m("transient", "min", "cases"),
            "tphl_ns": _m("transient", "max", "ns"),
            "tplh_ns": _m("transient", "max", "ns"),
            "vout_high_v": _m("transient", "min", "V"),
            "vout_low_v": _m("transient", "max", "V"),
        },
        "logic_combinational_3stage.spice.tmpl",
        MOS_1V8,
        required_model_groups=(MOS_1V8,),
    ),
    "sequential-divider-3stage": CircuitSpec(
        "sequential-divider-3stage",
        "three cascaded toggle flip-flops with divide-by-2/4/8 outputs",
        "DUT",
        ("VDD", "VSS", "CLK", "Q1", "Q2", "Q3"),
        ("transient",),
        {
            "divide2_ratio": _m("transient", "max", "ratio"),
            "divide4_ratio": _m("transient", "max", "ratio"),
            "divide8_ratio": _m("transient", "max", "ratio"),
            "q3_high_v": _m("transient", "min", "V"),
            "q3_low_v": _m("transient", "max", "V"),
        },
        "sequential_divider_3stage.spice.tmpl",
        MOS_1V8,
        required_model_groups=(MOS_1V8,),
    ),
    "signal-generator-ring": CircuitSpec(
        "signal-generator-ring",
        "five-stage ring-oscillator signal generator",
        "DUT",
        ("VDD", "VSS", "OUT"),
        ("transient",),
        {
            "frequency_mhz": _m("transient", "min", "MHz"),
            "period_ns": _m("transient", "max", "ns"),
            "period_stability_pct": _m("transient", "max", "%"),
            "vout_high_v": _m("transient", "min", "V"),
            "vout_low_v": _m("transient", "max", "V"),
            "power_mw": _m("transient", "max", "mW"),
        },
        "signal_generator_ring.spice.tmpl",
        MOS_1V8,
        required_model_groups=(MOS_1V8,),
    ),
    "comparator": CircuitSpec(
        "comparator",
        "1.8 V continuous comparator",
        "DUT",
        ("VDD", "VSS", "VINP", "VINN", "VOUT"),
        ("transient",),
        {
            "decision_delay_ns": _m("transient", "max", "ns"),
            "out_high_v": _m("transient", "min", "V"),
            "out_low_v": _m("transient", "max", "V"),
            "power_mw": _m("transient", "max", "mW"),
        },
        "comparator.spice.tmpl",
        MOS_1V8,
        required_model_groups=(MOS_1V8,),
    ),
    "level-shifter-hv": CircuitSpec(
        "level-shifter-hv",
        "1.8 V to 3.3 V static level shifter",
        "DUT",
        ("VDDL", "VDDH", "VSS", "IN", "OUT"),
        ("transient",),
        {
            "rise_delay_ns": _m("transient", "max", "ns"),
            "fall_delay_ns": _m("transient", "max", "ns"),
            "vout_high_v": _m("transient", "min", "V"),
            "vout_low_v": _m("transient", "max", "V"),
            "power_mw": _m("transient", "max", "mW"),
        },
        "level_shifter_hv.spice.tmpl",
        MOS_1V8 | MOS_HV,
        required_model_groups=(MOS_HV,),
    ),
    "analog-ldo": CircuitSpec(
        "analog-ldo",
        "continuous-feedback analog LDO with an external 0.6 V reference",
        "DUT",
        ("VIN", "VSS", "VREF", "VOUT"),
        ("dc", "transient"),
        {
            "vout_1p4_v": _m("dc", "min", "V"),
            "vout_1p8_v": _m("dc", "min", "V"),
            "line_regulation_mv_per_v": _m("dc", "max", "mV/V"),
            "vout_light_v": _m("transient", "min", "V"),
            "vout_heavy_v": _m("transient", "min", "V"),
            "load_regulation_mv": _m("transient", "max", "mV"),
            "droop_mv": _m("transient", "max", "mV"),
            "settling_time_us": _m("transient", "max", "us"),
            "input_current_ua": _m("transient", "max", "uA"),
        },
        "analog_ldo.spice.tmpl",
        MOS_1V8,
        "process-error-amplifier-and-pass-device-with-external-reference",
        (frozenset({"sky130_fd_pr__pfet_01v8"}), frozenset({"sky130_fd_pr__nfet_01v8"})),
    ),
    "digital-ldo": CircuitSpec(
        "digital-ldo",
        "clocked digital-LDO power stage with evaluator-supplied controller",
        "DUT",
        ("VIN", "VSS", "EN0", "EN1", "EN2", "EN3", "VOUT"),
        ("transient",),
        {
            "vout_light_v": _m("transient", "min", "V"),
            "vout_heavy_v": _m("transient", "min", "V"),
            "droop_mv": _m("transient", "max", "mV"),
            "settling_time_us": _m("transient", "max", "us"),
            "input_current_ua": _m("transient", "max", "uA"),
        },
        "digital_ldo.spice.tmpl",
        MOS_1V8 | MOS_HV,
        "process-pass-array-with-trusted-digital-controller",
        (frozenset({"sky130_fd_pr__pfet_01v8", "sky130_fd_pr__pfet_g5v0d10v5"}),),
    ),
    "bandgap-reference": CircuitSpec(
        "bandgap-reference",
        "SKY130 PNP CTAT/PTAT bandgap core",
        "DUT",
        ("VDD", "VSS", "E1", "E8", "VREF"),
        ("dc",),
        {
            "vref_25c_v": _m("dc", "min", "V"),
            "vref_min_v": _m("dc", "min", "V"),
            "vref_max_v": _m("dc", "max", "V"),
            "temp_span_mv": _m("dc", "max", "mV"),
            "tempco_ppm_per_c": _m("dc", "max", "ppm/C"),
            "supply_current_ua": _m("dc", "max", "uA"),
        },
        "bandgap_reference.spice.tmpl",
        MOS_1V8 | BJT,
        "pdk-pnp-core-with-trusted-bias-and-summing-scaffolding",
        (BJT,),
    ),
}


def circuit_ids() -> tuple[str, ...]:
    return tuple(_CIRCUITS)


def get_circuit_spec(circuit: str) -> CircuitSpec:
    try:
        return _CIRCUITS[circuit]
    except KeyError as exc:
        raise ValueError(
            f"unknown circuit {circuit!r}; available: {', '.join(circuit_ids())}"
        ) from exc
