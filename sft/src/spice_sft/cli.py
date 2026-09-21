"""Prepare verified conversations and run supervised fine-tuning."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def prepare(runs: list[str], output: str) -> dict:
    from analog_trace.export import export_run
    from analog_trace.sft import to_sft_example
    rows = []
    for run in runs:
        trajectory = export_run(run)
        if not trajectory.get("eligible"):
            raise ValueError(f"Run is not eligible for SFT: {run}")
        example = to_sft_example(trajectory)
        row = {"messages": example["messages"]}
        if trajectory.get("tools"):
            row["tools"] = trajectory["tools"]
        rows.append(row)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"examples": len(rows), "output": str(target)}


def train(args: argparse.Namespace) -> dict:
    from .trainer import SFTConfig, SFTTrainer
    target = Path(args.output)
    if target.exists():
        raise FileExistsError(f"Use a new checkpoint directory: {target}")
    rows = []
    with Path(args.data).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
                raise ValueError(f"Line {number} requires a messages list")
            if not any(isinstance(m, dict) and m.get("role") == "assistant" for m in row["messages"]):
                raise ValueError(f"Line {number} has no assistant response")
            rows.append(row)
    if not rows:
        raise ValueError("SFT dataset is empty")
    config = SFTConfig(output_dir=str(target), model_name=args.model,
                       learning_rate=args.learning_rate, epochs=args.epochs,
                       batch_size=args.batch_size,
                       gradient_accumulation_steps=args.gradient_accumulation_steps)
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("Tokenizer requires a pad token or an EOS token")
        tokenizer.pad_token = tokenizer.eos_token
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForCausalLM.from_pretrained(args.model).to(device)
    trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=rows, config=config)
    return trainer.train()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare SFT data and train a circuit agent")
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Export selected sealed runs to messages JSONL")
    prep.add_argument("--runs", nargs="+", required=True)
    prep.add_argument("--output", required=True)
    training = commands.add_parser("train", help="Supervised fine-tuning with assistant-only loss")
    training.add_argument("--data", required=True)
    training.add_argument("--model", required=True)
    training.add_argument("--output", required=True)
    training.add_argument("--epochs", type=int, default=1)
    training.add_argument("--batch-size", type=int, default=1)
    training.add_argument("--learning-rate", type=float, default=1e-5)
    training.add_argument("--gradient-accumulation-steps", type=int, default=1)
    training.add_argument("--device")
    args = parser.parse_args(argv)
    result = prepare(args.runs, args.output) if args.command == "prepare" else train(args)
    print(json.dumps(result, indent=2))
    return 0
