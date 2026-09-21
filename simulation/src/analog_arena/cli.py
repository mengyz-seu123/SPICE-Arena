"""Small CLI shared by people, Codex and Claude Code."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import yaml


def software_contract(profile=None, circuit=None):
    if profile is None:
        from .simulation.circuits.specs import circuit_ids
        return {"schema": "analog-arena/platform-contract/v2",
                "profiles": ["sky130-ota", "sky130-generic"], "circuits": list(circuit_ids()),
                "arbitrary_decks": "arena simulate --netlist <deck> --output <new-directory>",
                "selection": "arena contract --profile <profile> [--circuit <id>]"}
    if profile == "sky130-ota":
        if circuit:
            raise ValueError("sky130-ota does not accept --circuit")
        from .evaluation.amplifier.profile import software_contract as contract
        return contract()
    if profile == "sky130-generic":
        if not circuit:
            from .simulation.circuits.specs import circuit_ids
            return {"profile": profile, "circuits": list(circuit_ids())}
        from .evaluation.generic import software_contract as contract
        return contract(circuit)
    raise ValueError(f"unknown profile: {profile}")


def evaluate(config, netlist, output, ibias=None):
    from .evaluation.config import validate_config
    from .evaluation.constraints import score_result
    config = validate_config(config)
    source, root = Path(netlist).resolve(), Path(output).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    is_ota = config["evaluator"]["profile"] == "sky130-ota"
    if is_ota and ibias is None:
        raise ValueError("sky130-ota requires --ibias in uA")
    if not is_ota and ibias is not None:
        raise ValueError("--ibias is only supported by sky130-ota")
    root.mkdir(parents=True, exist_ok=False)
    candidate = root / "input"
    candidate.mkdir()
    (candidate / "dut.spice").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (candidate / "candidate.json").write_text(json.dumps({"ibias_uA": ibias} if is_ota else {}), encoding="utf-8")
    (root / "evaluation.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    if is_ota:
        from .evaluation.amplifier.profile import evaluate_candidate
    else:
        from .evaluation.generic import evaluate_candidate
    result = evaluate_candidate(config, candidate, root)
    result.update(score_result(result, config["constraints"]))
    result_path = root / "result.json"
    stored = json.loads(result_path.read_text(encoding="utf-8"))
    stored.update(result)
    result_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def _table(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        tokens = line.split()
        if not tokens or line.lstrip().startswith(('#', '*')):
            continue
        try:
            row = [float(v) for v in tokens]
        except ValueError:
            if not rows and not any(c.isdigit() for c in tokens[0]):
                continue
            raise ValueError(f"invalid numeric table row: {line}")
        if len(row) < 3:
            raise ValueError("amplifier tables require at least three columns; see arena metrics --help")
        rows.append(row[:3])
    return rows


def build_parser():
    parser = argparse.ArgumentParser(description="General SPICE simulation and circuit measurements")
    parser.add_argument("--version", action="version", version="analog-arena 0.6.0")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="extract the bundled SKY130 PDK")
    setup.add_argument("--archive")
    setup.add_argument("--output")
    doctor = commands.add_parser("doctor", help="check simulator and optional PDK")
    doctor.add_argument("--pdk")
    doctor.add_argument("--ngspice")
    contract = commands.add_parser("contract", help="inspect available circuit templates and measurements")
    contract.add_argument("--profile", choices=["sky130-ota", "sky130-generic"])
    contract.add_argument("--circuit")
    contract.add_argument("--config")
    sim = commands.add_parser("simulate", help="run any complete SPICE deck; no template required")
    sim.add_argument("--netlist", required=True)
    sim.add_argument("--output", required=True, help="new artifact directory")
    sim.add_argument("--ngspice")
    sim.add_argument("--timeout", type=float, default=180)
    ev = commands.add_parser("evaluate", help="simulate a DUT with a selected measurement template")
    ev.add_argument("--config", required=True)
    ev.add_argument("--netlist", required=True)
    ev.add_argument("--output", required=True, help="new artifact directory")
    ev.add_argument("--ibias", type=float)
    metrics = commands.add_parser("metrics", help="extract measurements from files without simulation")
    metrics.add_argument("--kind", choices=["measures", "amplifier-ac", "amplifier-transient"], required=True)
    metrics.add_argument("--input", required=True, help="log, or table whose first 3 columns are AC=f_Hz,out_dB,phase_deg; transient=t_s,out_V,in_V")
    metrics.add_argument("--output")
    metrics.add_argument("--input-amplitude", type=float, default=1.0, help="AC input amplitude in V (template default: 1 V)")
    score = commands.add_parser("score", help="check stored metrics against constraints without simulation")
    score.add_argument("--input", required=True)
    score.add_argument("--constraints", required=True, help="YAML metric-to-min/max mapping, or config containing constraints")
    return parser


def main(argv=None):
    # WSL diagnostics may contain Unicode outside a Windows console code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "setup":
            from .simulation.environment import setup_pdk
            result = setup_pdk(args.archive, args.output)
        elif args.command == "doctor":
            from .simulation.ngspice import version, default_pdk
            pdk = Path(args.pdk) if args.pdk else default_pdk()
            result = version(args.ngspice)
            result["pdk"] = {"path": str(pdk), "available": pdk.is_file()}
            if args.pdk:
                result["ok"] = result["ok"] and pdk.is_file()
        elif args.command == "contract":
            if args.config:
                if args.profile or args.circuit:
                    raise ValueError("--config cannot be combined with --profile/--circuit")
                from .evaluation.config import load_config
                ev = load_config(args.config)["evaluator"]
                result = software_contract(ev["profile"], ev.get("circuit"))
            else:
                if args.circuit and not args.profile:
                    raise ValueError("--circuit requires --profile")
                result = software_contract(args.profile, args.circuit)
        elif args.command == "simulate":
            from .simulation.ngspice import simulate
            result = simulate(args.netlist, args.output, executable=args.ngspice, timeout_s=args.timeout)
        elif args.command == "evaluate":
            from .evaluation.config import load_config
            result = evaluate(load_config(args.config), args.netlist, args.output, args.ibias)
        elif args.command == "metrics":
            if args.kind == "measures":
                from .evaluation.circuits import _parse_measures
                result = _parse_measures(Path(args.input).read_text(encoding="utf-8", errors="replace"))
            else:
                from .evaluation.amplifier.metrics import extract_ac_metrics, extract_transient_metrics
                rows = _table(args.input)
                result = extract_ac_metrics(rows, input_amplitude_v=args.input_amplitude) if args.kind == "amplifier-ac" else extract_transient_metrics(rows)
                from .evaluation.amplifier.profile import _row_metric_validity
                metric_row = result if args.kind == "amplifier-ac" else {**result, "transient": result}
                analyses = {"ac"} if args.kind == "amplifier-ac" else {"transient"}
                result = {**result, "metric_validity": _row_metric_validity(metric_row, analyses)}
            if args.output:
                with Path(args.output).open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
        elif args.command == "score":
            from .evaluation.constraints import score_result
            constraints = yaml.safe_load(Path(args.constraints).read_text(encoding="utf-8"))
            result = score_result(json.loads(Path(args.input).read_text(encoding="utf-8")), constraints.get("constraints", constraints))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result.get("ok") is False or result.get("status") == "INVALID" else 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
