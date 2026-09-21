"""AnalogCoder-Pro Easy/Medium tasks adapted to fixed Analog-Arena fixtures."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Iterable


SOURCE_COMMIT = "05542af46020e5c37d5a7e3ca79da9f24626e1c9"
MEASUREMENT_CONTRACT = "analogcoderpro-easy-medium-adapter-v1"
RELATION = "Independent adapted tasks; pass rate is strict passed tasks divided by attempted tasks."
OUTPUT_LIMITS = {"vout_min_v": {"min": 0.05}, "vout_max_v": {"max": 1.75}}


def _gain(direction: str, minimum: float = 1.0) -> dict:
    limit = {"gain_signed": {"max": -minimum}} if direction == "negative" else {
        "gain_signed": {"min": minimum}
    }
    return {**limit, **copy.deepcopy(OUTPUT_LIMITS)}


TASKS = {
    "ACP-E01-CS-R": {
        "source_task_id": 1, "level": "Easy", "fixture": "cs", "ports": ["IN", "OUT", "VDD", "VSS"],
        "description": "a single-stage common-source amplifier with resistive load R",
        "topology": "One NMOS common-source stage and one resistor from VDD to OUT.",
        "constraints": _gain("negative"),
    },
    "ACP-E02-3STAGE-CS-R": {
        "source_task_id": 2, "level": "Easy", "fixture": "cs", "ports": ["IN", "OUT", "VDD", "VSS"],
        "description": "a three-stage amplifier with single input and output",
        "topology": "Three cascaded NMOS common-source stages, each with one resistor to VDD.",
        "constraints": _gain("negative"),
    },
    "ACP-E03-SOURCE-FOLLOWER-R": {
        "source_task_id": 3, "level": "Easy", "fixture": "follower", "ports": ["IN", "OUT", "VDD", "VSS"],
        "description": "a common-drain amplifier with resistive load R",
        "topology": "One NMOS source follower and one resistor from OUT to VSS.",
        "constraints": {"gain_signed": {"min": 0.2, "max": 1.2}, **copy.deepcopy(OUTPUT_LIMITS)},
    },
    "ACP-E04-COMMON-GATE-R": {
        "source_task_id": 4, "level": "Easy", "fixture": "common_gate", "ports": ["IN", "VBIAS", "OUT", "VDD", "VSS"],
        "description": "a single-stage common-gate amplifier with resistive load R",
        "topology": "One NMOS with source=IN, gate=VBIAS, drain=OUT, plus one VDD-to-OUT resistor.",
        "constraints": _gain("positive", 0.5),
    },
    "ACP-E05-CASCODE-R": {
        "source_task_id": 5, "level": "Easy", "fixture": "cascode", "ports": ["IN", "VBIAS", "OUT", "VDD", "VSS"],
        "description": "a single-stage cascode amplifier with two NMOS transistors and resistive load R",
        "topology": "Two stacked NMOS devices: lower gate=IN, upper gate=VBIAS, with one VDD-to-OUT resistor.",
        "constraints": _gain("negative"),
    },
    "ACP-E06-NMOS-R": {
        "source_task_id": 6, "level": "Easy", "fixture": "inverter", "ports": ["IN", "OUT", "VDD", "VSS"],
        "description": "a NMOS inverter with resistive load R",
        "topology": "Exactly one NMOS pull-down and one resistor from VDD to OUT; no PMOS.",
        "constraints": {"voh_min_v": {"min": 1.5}, "vol_v": {"max": 0.3},
                        "tphl_ns": {"min": 0.0, "max": 10.0}, "tplh_ns": {"min": 0.0, "max": 10.0}},
    },
    "ACP-E07-CMOS-INV": {
        "source_task_id": 7, "level": "Easy", "fixture": "inverter", "ports": ["IN", "OUT", "VDD", "VSS"],
        "description": "a logical inverter with 1 NMOS and 1 PMOS",
        "topology": "Exactly one NMOS pull-down and one PMOS pull-up; both gates at IN and drains at OUT.",
        "constraints": {"voh_min_v": {"min": 1.5}, "vol_v": {"max": 0.3},
                        "tphl_ns": {"min": 0.0, "max": 10.0}, "tplh_ns": {"min": 0.0, "max": 10.0}},
    },
    "ACP-E08-NMOS-CCS-R": {
        "source_task_id": 8, "level": "Easy", "fixture": "current_source", "ports": ["VBIAS", "OUT", "VDD", "VSS"],
        "description": "a simple NMOS constant current source with resistive load R",
        "topology": "One NMOS current sink and one resistor from VDD to OUT.",
        "constraints": {"current_min_uA": {"min": 10.0}, "current_max_uA": {"max": 2000.0},
                        "current_regulation_fraction": {"max": 0.3}, **copy.deepcopy(OUTPUT_LIMITS)},
    },
    "ACP-M14-TWOSTAGE-MILLER": {
        "source_task_id": 14, "level": "Medium", "fixture": "cs", "ports": ["IN", "OUT", "VDD", "VSS"],
        "description": "a two-stage amplifier with a Miller compensation capacitor",
        "topology": "Two cascaded NMOS common-source stages, two VDD load resistors, and a capacitor from stage one output to OUT.",
        "constraints": _gain("positive"),
    },
    "ACP-M15-CS-PMOS-DIODE": {
        "source_task_id": 15, "level": "Medium", "fixture": "cs", "ports": ["IN", "OUT", "VDD", "VSS"],
        "description": "a common-source amplifier with PMOS diode-connected load",
        "topology": "One NMOS common-source device and one diode-connected PMOS load at OUT.",
        "constraints": _gain("negative"),
    },
    "ACP-M16-DIFF-ACTIVE": {
        "source_task_id": 16, "level": "Medium", "fixture": "diff_active",
        "ports": ["INP", "INN", "VBIAS", "OUTP", "OUT", "VDD", "VSS"],
        "description": "a differential opamp with active PMOS current-mirror load, tail current source, and two outputs",
        "topology": "NMOS differential pair, NMOS bias-controlled tail, and two-PMOS current-mirror load.",
        "constraints": {"differential_gain_abs": {"min": 2.0}, **copy.deepcopy(OUTPUT_LIMITS)},
    },
    "ACP-M17-CASCODE-MIRROR": {
        "source_task_id": 17, "level": "Medium", "fixture": "cascode_mirror", "ports": ["IREF", "IOUT", "VDD", "VSS"],
        "description": "a cascode current mirror with four MOSFETs and reference-current input",
        "topology": "Four-NMOS cascode mirror: two diode-connected input-side devices and two corresponding output-side devices.",
        "constraints": {"iout_uA": {"min": 50.0, "max": 150.0}, "mirror_error_fraction": {"max": 0.5},
                        "vout_v": {"min": 0.1, "max": 1.7}},
    },
    "ACP-M18-DIFF-RESISTIVE": {
        "source_task_id": 18, "level": "Medium", "fixture": "diff_resistive",
        "ports": ["INP", "INN", "OUT", "VDD", "VSS"],
        "description": "a differential common-source opamp with dual resistive loads, tail current, and a single output",
        "topology": "NMOS differential pair, two VDD load resistors, and one ideal tail-current source.",
        "constraints": {"differential_gain_abs": {"min": 2.0}, **copy.deepcopy(OUTPUT_LIMITS)},
    },
}

TASK_IDS = tuple(TASKS)
EASY_TASK_IDS = tuple(task for task, item in TASKS.items() if item["level"] == "Easy")
MEDIUM_TASK_IDS = tuple(task for task, item in TASKS.items() if item["level"] == "Medium")


def config(task: str) -> dict:
    item = TASKS.get(task)
    if item is None:
        raise ValueError("unknown AnalogCoder-Pro adapted task")
    return {"evaluator": {"profile": f"sky130-acp-{item['source_task_id']:02d}-v1", "analyses": ["transient"],
                          "corners": ["TT"], "timeout_s": 180},
            "objectives": {}, "constraints": copy.deepcopy(item["constraints"])}


def _interface(item: dict) -> str:
    return ".subckt DUT " + " ".join(item["ports"])


def contract(task: str) -> dict:
    item = TASKS[task]
    return {
        "measurement_contract": MEASUREMENT_CONTRACT,
        "profile": config(task)["evaluator"]["profile"],
        "task": task,
        "source": {"project": "AnalogCoder-Pro", "repository": "https://github.com/laiyao1/AnalogCoderPro",
                   "commit": SOURCE_COMMIT, "task_id": item["source_task_id"], "level": item["level"],
                   "description": item["description"]},
        "required_interface": _interface(item),
        "required_topology": item["topology"],
        "device_syntax": [
            "Xname D G S B sky130_fd_pr__nfet_01v8 W=<um> L=<um> [M=<integer>]",
            "Xname D G S B sky130_fd_pr__pfet_01v8 W=<um> L=<um> [M=<integer>]",
            "Rname node1 node2 <positive resistance, e.g. 10k>",
            "Cname node1 node2 <positive capacitance, e.g. 1p>",
            "Iname node1 node2 <positive current, e.g. 100u>; allowed only when required",
        ],
        "testbench": "Fixed TT SKY130, VDD up to 1.8 V, fixed bias/step sources and bounded transient scoring.",
        "required_metrics": copy.deepcopy(item["constraints"]),
    }


def context(task: str) -> dict:
    item = TASKS[task]
    return {"task": task, "display_task": f"AnalogCoder-Pro {item['level']} {item['source_task_id']}",
            "config": config(task), "contract": contract(task), "relation": RELATION,
            "starting_netlist": _interface(item) + "\n* Add only the devices required by this task.\n.ends DUT\n",
            "example_note": "Interface-only skeleton; it contains no answer devices, internal nodes, or design values.",
            "success_levels": ["syntax_valid", "topology_valid", "execution_valid", "performance_passed"],
            "topology_changes_allowed": False}


_VALUE = re.compile(r"([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(meg|[tgkmunpf])?(?:ohm)?", re.I)


def _positive_value(token: str) -> bool:
    match = _VALUE.fullmatch(token)
    return bool(match and float(match.group(1)) > 0)


def _parse(task: str, text: str) -> tuple[dict, list[str]]:
    item = TASKS[task]
    diagnostics: list[str] = []
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 128_000:
        return {}, ["Netlist must be nonempty text under 128 KB"]
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("*")]
    if len(lines) < 3 or lines[0].upper().split() != _interface(item).upper().split():
        diagnostics.append(f"DUT must start with {_interface(item)}")
    if not lines or lines[-1].upper().split() not in ([".ENDS"], [".ENDS", "DUT"]):
        diagnostics.append("DUT must end with .ends DUT")
    devices = {"mos": [], "resistors": [], "capacitors": [], "currents": []}
    names = set()
    for line in lines[1:-1] if len(lines) >= 2 else []:
        fields = line.split()
        name = fields[0].upper()
        if name in names:
            diagnostics.append(f"Duplicate element name: {fields[0]}")
            continue
        names.add(name)
        if name.startswith("X"):
            if len(fields) not in (8, 9):
                diagnostics.append(f"Malformed MOS line: {line}")
                continue
            model = fields[5].lower()
            if model not in {"sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8"}:
                diagnostics.append(f"Unsupported MOS model: {fields[5]}")
                continue
            params = {}
            try:
                for token in fields[6:]:
                    key, value = token.split("=", 1)
                    params[key.upper()] = float(value)
            except (ValueError, TypeError):
                diagnostics.append(f"Malformed MOS parameters: {line}")
                continue
            if set(params) not in ({"W", "L"}, {"W", "L", "M"}) or min(params.get("W", 0), params.get("L", 0)) <= 0 or params.get("M", 1) < 1:
                diagnostics.append(f"MOS requires positive W/L and optional positive M: {line}")
                continue
            devices["mos"].append({"name": name, "d": fields[1].upper(), "g": fields[2].upper(),
                                   "s": fields[3].upper(), "b": fields[4].upper(),
                                   "kind": "n" if "nfet" in model else "p"})
        elif name[0] in "RCI":
            if len(fields) != 4 or not _positive_value(fields[3]):
                diagnostics.append(f"Malformed positive {name[0]} element: {line}")
                continue
            key = {"R": "resistors", "C": "capacitors", "I": "currents"}[name[0]]
            devices[key].append({"name": name, "a": fields[1].upper(), "b": fields[2].upper(), "value": fields[3]})
        else:
            diagnostics.append(f"Only prescribed MOS, resistor, capacitor, and current-source elements are allowed: {line}")
    return devices, diagnostics


def _between(device: dict, first: str, second: str) -> bool:
    return {device["a"], device["b"]} == {first, second}


def _nmos(device: dict, d: str, g: str, s: str, b: str = "VSS") -> bool:
    return device["kind"] == "n" and (device["d"], device["g"], device["s"], device["b"]) == (d, g, s, b)


def _pmos(device: dict, d: str, g: str, s: str, b: str = "VDD") -> bool:
    return device["kind"] == "p" and (device["d"], device["g"], device["s"], device["b"]) == (d, g, s, b)


def _resistive_cs_chain(devices: dict, stages: int) -> tuple[bool, list[str]]:
    mos, resistors = devices["mos"], devices["resistors"]
    if len(mos) != stages or len(resistors) != stages or devices["currents"]:
        return False, []
    node, ordered = "IN", []
    for _ in range(stages):
        choices = [d for d in mos if d not in ordered and d["kind"] == "n" and d["g"] == node
                   and d["s"] == "VSS" and d["b"] == "VSS"]
        if len(choices) != 1 or not any(_between(r, "VDD", choices[0]["d"]) for r in resistors):
            return False, []
        ordered.append(choices[0])
        node = choices[0]["d"]
    return node == "OUT", [device["d"] for device in ordered]


def _topology_valid(task: str, d: dict) -> bool:
    mos, resistors, capacitors, currents = d["mos"], d["resistors"], d["capacitors"], d["currents"]
    if task == "ACP-E01-CS-R":
        return len(mos) == len(resistors) == 1 and not capacitors and not currents and _nmos(mos[0], "OUT", "IN", "VSS") and _between(resistors[0], "VDD", "OUT")
    if task == "ACP-E02-3STAGE-CS-R":
        valid, _ = _resistive_cs_chain(d, 3)
        return valid and not capacitors
    if task == "ACP-E03-SOURCE-FOLLOWER-R":
        return len(mos) == len(resistors) == 1 and not capacitors and not currents and _nmos(mos[0], "VDD", "IN", "OUT") and _between(resistors[0], "OUT", "VSS")
    if task == "ACP-E04-COMMON-GATE-R":
        return len(mos) == len(resistors) == 1 and not capacitors and not currents and _nmos(mos[0], "OUT", "VBIAS", "IN") and _between(resistors[0], "VDD", "OUT")
    if task == "ACP-E05-CASCODE-R":
        if len(mos) != 2 or len(resistors) != 1 or capacitors or currents or not _between(resistors[0], "VDD", "OUT"):
            return False
        lower = [x for x in mos if x["kind"] == "n" and x["g"] == "IN" and x["s"] == "VSS" and x["b"] == "VSS"]
        return len(lower) == 1 and any(_nmos(x, "OUT", "VBIAS", lower[0]["d"]) for x in mos if x is not lower[0])
    if task == "ACP-E06-NMOS-R":
        return len(mos) == len(resistors) == 1 and not capacitors and not currents and _nmos(mos[0], "OUT", "IN", "VSS") and _between(resistors[0], "VDD", "OUT")
    if task == "ACP-E07-CMOS-INV":
        return len(mos) == 2 and not resistors and not capacitors and not currents and any(_nmos(x, "OUT", "IN", "VSS") for x in mos) and any(_pmos(x, "OUT", "IN", "VDD") for x in mos)
    if task == "ACP-E08-NMOS-CCS-R":
        return len(mos) == len(resistors) == 1 and not capacitors and not currents and _nmos(mos[0], "OUT", "VBIAS", "VSS") and _between(resistors[0], "VDD", "OUT")
    if task == "ACP-M14-TWOSTAGE-MILLER":
        valid, nodes = _resistive_cs_chain(d, 2)
        return valid and len(capacitors) == 1 and _between(capacitors[0], nodes[0], "OUT")
    if task == "ACP-M15-CS-PMOS-DIODE":
        return len(mos) == 2 and not resistors and not capacitors and not currents and any(_nmos(x, "OUT", "IN", "VSS") for x in mos) and any(_pmos(x, "OUT", "OUT", "VDD") for x in mos)
    if task == "ACP-M16-DIFF-ACTIVE":
        if len(mos) != 5 or resistors or capacitors or currents:
            return False
        pair = [x for x in mos if x["kind"] == "n" and x["g"] in {"INP", "INN"} and x["d"] in {"OUTP", "OUT"} and x["b"] == "VSS"]
        if len(pair) != 2 or {x["g"] for x in pair} != {"INP", "INN"} or {x["d"] for x in pair} != {"OUTP", "OUT"} or len({x["s"] for x in pair}) != 1:
            return False
        tail = pair[0]["s"]
        return (any(_nmos(x, tail, "VBIAS", "VSS") for x in mos)
                and any(_pmos(x, "OUTP", "OUTP", "VDD") for x in mos)
                and any(_pmos(x, "OUT", "OUTP", "VDD") for x in mos))
    if task == "ACP-M17-CASCODE-MIRROR":
        if len(mos) != 4 or any(x["kind"] != "n" for x in mos) or resistors or capacitors or currents:
            return False
        bottom = [x for x in mos if x["d"] == x["g"] and x["s"] == "VSS" and x["b"] == "VSS"]
        if len(bottom) != 1:
            return False
        n1 = bottom[0]["d"]
        output_bottom = [x for x in mos if x["kind"] == "n" and x["g"] == n1 and x["s"] == "VSS" and x["b"] == "VSS" and x["d"] not in {n1, "IREF", "IOUT"}]
        return (len(output_bottom) == 1 and any(_nmos(x, "IREF", "IREF", n1) for x in mos)
                and any(_nmos(x, "IOUT", "IREF", output_bottom[0]["d"]) for x in mos))
    if task == "ACP-M18-DIFF-RESISTIVE":
        if len(mos) != 2 or len(resistors) != 2 or capacitors or len(currents) != 1:
            return False
        if any(x["kind"] != "n" or x["g"] not in {"INP", "INN"} or x["b"] != "VSS" for x in mos):
            return False
        if {x["g"] for x in mos} != {"INP", "INN"} or len({x["s"] for x in mos}) != 1 or "OUT" not in {x["d"] for x in mos}:
            return False
        tail = mos[0]["s"]
        return all(any(_between(r, "VDD", x["d"]) for r in resistors) for x in mos) and _between(currents[0], tail, "VSS")
    raise ValueError("unknown AnalogCoder-Pro adapted task")


def validate_topology(task: str, text: str) -> dict:
    devices, diagnostics = _parse(task, text)
    if diagnostics:
        return {"accepted": False, "syntax_valid": False, "topology_valid": False, "diagnostics": diagnostics}
    topology_valid = _topology_valid(task, devices)
    if not topology_valid:
        diagnostics.append(TASKS[task]["topology"])
    return {"accepted": topology_valid, "syntax_valid": True, "topology_valid": topology_valid, "diagnostics": diagnostics}


def fixture(task: str, dut: str, pdk: Path) -> str:
    common = f'''* Analog-Arena fixed {task} fixture
.lib "{pdk.as_posix()}" tt
{dut.strip()}
'''
    mode = TASKS[task]["fixture"]
    if mode == "inverter":
        return common + '''VDD VDD 0 1.8
VIN IN 0 PWL(0 0 100n 0 101n 1.8 200n 1.8 201n 0 300n 0)
XDUT IN OUT VDD 0 DUT
CLOAD OUT 0 10f
.control
set filetype=ascii
tran 0.05n 300n
wrdata waveform.tsv time v(IN) v(OUT)
quit
.endc
.end
'''
    if mode in {"cs", "follower"}:
        bias = 1.2 if mode == "follower" else 0.7
        delta = 0.01 if mode == "follower" else 0.001
        return common + f'''VDD VDD 0 1.8
VIN IN 0 PWL(0 {bias} 100n {bias} 100.1n {bias + delta} 200n {bias + delta})
XDUT IN OUT VDD 0 DUT
CLOAD OUT 0 20f
.control
set filetype=ascii
tran 0.05n 200n
wrdata waveform.tsv time v(IN) v(OUT)
quit
.endc
.end
'''
    if mode in {"common_gate", "cascode"}:
        bias, gate = ((0.2, 0.9) if mode == "common_gate" else (0.7, 1.1))
        return common + f'''VDD VDD 0 1.8
VIN IN 0 PWL(0 {bias} 100n {bias} 100.1n {bias + 0.01} 200n {bias + 0.01})
VBIAS VBIAS 0 {gate}
XDUT IN VBIAS OUT VDD 0 DUT
CLOAD OUT 0 20f
.control
set filetype=ascii
tran 0.05n 200n
wrdata waveform.tsv time v(IN) v(OUT)
quit
.endc
.end
'''
    if mode == "current_source":
        return common + '''VDD VDD 0 PWL(0 1.6 100n 1.6 100.1n 1.8 200n 1.8)
VBIAS VBIAS 0 0.9
XDUT VBIAS OUT VDD 0 DUT
.control
set filetype=ascii
tran 0.05n 200n
wrdata waveform.tsv time v(VDD) v(OUT) i(VDD)
quit
.endc
.end
'''
    if mode == "diff_active":
        return common + '''VDD VDD 0 1.8
VINP INP 0 PWL(0 0.9 100n 0.9 100.1n 0.905 200n 0.905)
VINN INN 0 PWL(0 0.9 100n 0.9 100.1n 0.895 200n 0.895)
VBIAS VBIAS 0 0.7
XDUT INP INN VBIAS OUTP OUT VDD 0 DUT
CLOADP OUTP 0 20f
CLOAD OUT 0 20f
.control
set filetype=ascii
tran 0.05n 200n
wrdata waveform.tsv time v(INP) v(INN) v(OUTP) v(OUT)
quit
.endc
.end
'''
    if mode == "diff_resistive":
        return common + '''VDD VDD 0 1.8
VINP INP 0 PWL(0 0.9 100n 0.9 100.1n 0.905 200n 0.905)
VINN INN 0 PWL(0 0.9 100n 0.9 100.1n 0.895 200n 0.895)
XDUT INP INN OUT VDD 0 DUT
CLOAD OUT 0 20f
.control
set filetype=ascii
tran 0.05n 200n
wrdata waveform.tsv time v(INP) v(INN) v(OUT)
quit
.endc
.end
'''
    if mode == "cascode_mirror":
        return common + '''VDD VDD 0 1.8
IREFSRC VDD IREF 100u
RLOAD VDD IOUT 5k
XDUT IREF IOUT VDD 0 DUT
CLOAD IOUT 0 20f
.control
set filetype=ascii
tran 0.05n 50n
wrdata waveform.tsv time v(IOUT)
quit
.endc
.end
'''
    raise ValueError("unknown fixture")


def waveform_columns(task: str) -> int:
    return {"inverter": 3, "cs": 3, "follower": 3, "common_gate": 3, "cascode": 3,
            "current_source": 4, "diff_active": 5, "diff_resistive": 4,
            "cascode_mirror": 2}[TASKS[task]["fixture"]]


def _sample(rows: Iterable[tuple[float, ...]], t: float, column: int) -> float:
    data = list(rows)
    for left, right in zip(data, data[1:]):
        if left[0] <= t <= right[0]:
            ratio = (t - left[0]) / (right[0] - left[0])
            return left[column] + ratio * (right[column] - left[column])
    raise ValueError(f"requested time {t} is outside waveform")


def score_waveform(task: str, rows: list[tuple[float, ...]]) -> dict:
    mode = TASKS[task]["fixture"]
    if mode in {"cs", "follower", "common_gate", "cascode"}:
        before, after = _sample(rows, 90e-9, 2), _sample(rows, 190e-9, 2)
        delta = 0.01 if mode in {"follower", "common_gate", "cascode"} else 0.001
        return {"gain_signed": (after - before) / delta,
                "vout_min_v": min(before, after), "vout_max_v": max(before, after)}
    if mode == "current_source":
        out1, out2 = _sample(rows, 90e-9, 2), _sample(rows, 190e-9, 2)
        current1, current2 = abs(_sample(rows, 90e-9, 3)) * 1e6, abs(_sample(rows, 190e-9, 3)) * 1e6
        return {"current_min_uA": min(current1, current2), "current_max_uA": max(current1, current2),
                "current_regulation_fraction": abs(current2 - current1) / max(current1, current2, 1e-30),
                "vout_min_v": min(out1, out2), "vout_max_v": max(out1, out2)}
    if mode == "diff_active":
        outp1, outp2 = _sample(rows, 90e-9, 3), _sample(rows, 190e-9, 3)
        out1, out2 = _sample(rows, 90e-9, 4), _sample(rows, 190e-9, 4)
        return {"differential_gain_abs": abs(((out2 - outp2) - (out1 - outp1)) / 0.01),
                "vout_min_v": min(outp1, outp2, out1, out2), "vout_max_v": max(outp1, outp2, out1, out2)}
    if mode == "diff_resistive":
        out1, out2 = _sample(rows, 90e-9, 3), _sample(rows, 190e-9, 3)
        return {"differential_gain_abs": abs((out2 - out1) / 0.01),
                "vout_min_v": min(out1, out2), "vout_max_v": max(out1, out2)}
    if mode == "cascode_mirror":
        vout = _sample(rows, 45e-9, 1)
        current = (1.8 - vout) / 5000 * 1e6
        return {"iout_uA": current, "mirror_error_fraction": abs(current - 100.0) / 100.0, "vout_v": vout}
    raise ValueError("inverter scoring is handled by the shared fixed fixture")
