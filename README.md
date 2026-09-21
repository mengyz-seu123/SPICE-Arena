# SPICE-Arena

Circuit design agents with SPICE simulation and independent metric evaluation.

SPICE-Arena connects language-model agents to ngspice, evaluates candidate circuits against explicit constraints, and records tool interactions for analysis and training-data preparation. It includes inverter, OTA and SRAM curriculum tasks, additional circuit fixtures, and generic circuit evaluation.

## Contents

- `simulation/src/analog_arena/`: ngspice execution, testbenches, metric extraction and circuit evaluation.
- `agent/src/analog_trace/`: model providers, agent loop, MCP tools, candidate validation, trace export and reporting.
- `sft/src/spice_sft/`: SFT data preparation, chat-template masks and supervised training.
- `simulation/tasks/`: task constraints, interfaces and topology templates.
- `simulation/examples/`: small netlists and evaluation configurations.
- `agent/prompts/`: concise English instructions loaded by the agent.

## Installation

Use Python 3.10 or later. Clone or unpack the repository into a path without spaces and run from its root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1`.

Install ngspice separately and make it available on PATH. SKY130 tasks also require a separate SKY130 PDK installation:

```bash
export ARENA_PDK=/absolute/path/to/libs.tech/ngspice/sky130.lib.spice
arena doctor --pdk "$ARENA_PDK"
```

On PowerShell, use `$env:ARENA_PDK = "C:/path/to/libs.tech/ngspice/sky130.lib.spice"`. Set `NGSPICE_EXECUTABLE` if ngspice is not on PATH. Keep the editable source checkout: task files and prompts are loaded relative to it.

## Quick start

Inspect the tasks and prepare a topology template without a model call:

```bash
arena-trace tasks
arena contract --profile sky130-ota
arena-trace starter --task INV-L1-D01
```

Templates require explicit device parameters. To validate a completed inverter DUT:

```bash
arena-trace lint --task INV-L1-D01 --netlist candidate.spice
```

Run a complete SPICE deck directly:

```bash
arena simulate --netlist simulation/examples/rc.spice --output artifacts/rc-01
```

Run an agent using a local OpenAI-compatible model server:

```bash
arena-trace run --task INV-L1-D01 \
  --config agent/configs/local-openai.json --model YOUR_SERVED_MODEL \
  --max-turns 12 --max-evals 8 --out artifacts/inv-01
```

For a remote provider, edit the placeholder URL and model in `agent/configs/openai-compatible.json`, set `OPENAI_API_KEY` in your environment, and pass that config instead. Never place credentials in tracked files. Use a new output directory for each run. Windows users can enter multiline examples as one line.

Source entry points are also available as `python agent/trace.py --help` and `python sft/sft.py --help`. See `arena-trace --help` for MCP, evaluation and trace commands.

## Task and evaluation semantics

The curriculum contains INV, OTA and SRAM tasks at L1-L3. Numerical thresholds are stored in `simulation/tasks/curriculum-l123/tasks.json`; brief English interface notes sit alongside it. Evaluator code defines the exact measurements. Task levels are not calibrated model success rates.

Execution validity, functional validity and satisfaction of all performance constraints are separate results. A design passes only when the required checks succeed for one candidate under the selected contract. Netlist examples demonstrate interfaces and execution; they do not claim optimized or passing designs.

## Supervised fine-tuning

Install the optional training dependencies:

```bash
python -m pip install -e ".[sft]"
```

Prepare selected, sealed agent runs as training conversations. The exporter checks the run seal and trajectory eligibility; a failed or incomplete export raises an error. Select suitable demonstrations before calling it. Eligibility alone does not mean the circuit met every specification.

```bash
arena-sft prepare --runs artifacts/inv-01 artifacts/inv-02 --output data/sft.jsonl
```

Each JSONL row contains `messages` and, when present, `tools`. System prompts, user messages and tool observations remain context; only assistant responses and tool calls receive supervised labels. Reward fields and local trace paths are not included in the training rows.

Train a causal language model using an existing local model directory or a model identifier:

```bash
arena-sft train --data data/sft.jsonl --model /path/to/base-model \
  --output checkpoints/sft --epochs 1 --batch-size 1 \
  --gradient-accumulation-steps 8 --learning-rate 1e-5
```

This entry point performs full-parameter SFT with the source project's training primitives. It uses CUDA when available, otherwise CPU; `--device` overrides that choice. GPU memory requirements depend on model size and conversation length. The tokenizer must support faithful conversation rendering and assistant masks. Output contains model weights, tokenizer files and checkpoint metadata. Use a new output path for each export or training run.

The source entry point is `python sft/sft.py`. Training data and model weights are supplied separately.

## Release scope

This is a minimal source release. It includes one source tree for the Agent environment and SFT, with task configurations, fixtures and small examples. Experiment records, collected trajectories, raw simulation data, checkpoints, model weights, PDK bundles and private provider configuration are excluded. English runtime notes are concise summaries. This release is not a complete paper-results reproduction bundle.

Third-party notices are in `NOTICE.md` and `simulation/resources/licenses/`.
