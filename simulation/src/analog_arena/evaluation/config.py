"""Evaluation inputs; no budgets, sessions, history database or search policy."""
from __future__ import annotations
from pathlib import Path
from typing import Mapping
import math
import yaml
from .constraints import validate_constraints


def validate_config(raw: Mapping) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("evaluation config must be a mapping")
    evaluator = dict(raw.get("evaluator") or {})
    profile = evaluator.get("profile")
    if profile not in {"sky130-ota", "sky130-generic"}:
        raise ValueError("evaluator.profile must be sky130-ota or sky130-generic")
    corners = evaluator.get("corners", ["TT"])
    if not isinstance(corners, list) or not corners or not set(corners) <= {"TT", "SS", "FF", "SF", "FS"}:
        raise ValueError("corners must be a nonempty list of TT, SS, FF, SF, FS")
    evaluator["corners"] = list(dict.fromkeys(corners))
    timeout = evaluator.get("timeout_s", 180)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_s must be a finite positive number")
    evaluator["timeout_s"] = timeout
    config = {"evaluator": evaluator, "constraints": validate_constraints(raw.get("constraints", {})),
              "objectives": dict(raw.get("objectives") or {})}
    if any(mode not in {"min", "max"} for mode in config["objectives"].values()):
        raise ValueError("objective direction must be min or max")
    if profile == "sky130-generic":
        from analog_arena.simulation.circuits.specs import get_circuit_spec
        from .generic import validate_task_config
        spec = get_circuit_spec(str(evaluator.get("circuit", "")))
        evaluator.setdefault("analyses", list(spec.analyses))
        validate_task_config(config)
    else:
        from .amplifier.profile import validate_metric_requests
        if evaluator.get("circuit"):
            raise ValueError("sky130-ota does not accept evaluator.circuit")
        evaluator.setdefault("analyses", ["op", "ac", "transient", "rejection"])
        analyses = evaluator["analyses"]
        if not isinstance(analyses, list) or not analyses or not set(analyses) <= {"op", "ac", "transient", "rejection"}:
            raise ValueError("OTA analyses must use op, ac, transient, rejection")
        validate_metric_requests(config)
    # A template evaluation always checks all requested corners. Old search
    # prefilter/budget/access/factory fields do not control this operation.
    evaluator.pop("prefilter", None)
    return config


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    config = validate_config(yaml.safe_load(path.read_text(encoding="utf-8")))
    pdk = config["evaluator"].get("pdk")
    if pdk and not Path(pdk).is_absolute():
        config["evaluator"]["pdk"] = str((path.parent / pdk).resolve())
    return config
