# SPICE Arena question bank

78 task definitions for OTA, inverter (INV), and comparator (CMP) circuits,
organized by the four task categories in the current DATE manuscript.

| Category | OTA | INV | CMP | Total |
|---|---:|---:|---:|---:|
| Construction / modification | 6 | 6 | 6 | 18 |
| Diagnosis / repair | 8 | 8 | 8 | 24 |
| Parameter optimization | 6 | 6 | 6 | 18 |
| Multi-step design | 6 | 6 | 6 | 18 |
| Total | 26 | 26 | 26 | 78 |

## Contents

- [INDEX.md](INDEX.md): all 78 tasks, grouped by category and circuit family.
- [manifest.json](manifest.json): machine-readable classification, original IDs,
  relative file paths, budgets, and runtime dependency types.
- `<category>/<family>/<RCxxx>/task.json`: task definition (author title translated to English), including
  acceptance predicates, target constraints, candidate budget, and provenance.
- `starter.spice`: original authored starting circuit, stored with LF line endings.
- `previous.spice`: authored preceding circuit for RC017, RC037, and RC057;
  this is part of the question input, not an experimental trace.
- [SHA256SUMS.txt](SHA256SUMS.txt): checksums for every package file except itself.

## Mapping from the original task IDs

| Paper category | Original task IDs | Count per family |
|---|---|---:|
| Construction / modification | ST2-*-T01..T06 | 6 |
| Diagnosis / repair | ST2-*-B01..B04, R01..R02, S01..S02 | 8 |
| Parameter optimization | ST2-*-P01..P06 | 6 |
| Multi-step design | TR2-*-A01..A02, B01..B02, C01..C02 | 6 |

The diagnosis / repair category includes bias/polarity repair, failure recovery,
and selection/stopping. Original public IDs RC001..RC078, author IDs, circuit
text, predicates, and budgets are retained. Classification is recorded separately
in the package manifest; each task.json retains its original schema.

## Scope and runtime inputs

This package contains the full set of 78 task definitions and authored circuits.
It contains no rollouts, measurements, waveforms, results, reference answers,
training samples, simulator code, or PDK models. A runtime evaluator must supply
initial measurements and enforce the predicates in task.json; a starting circuit
is not a validated solution. Local stage success and full-design success are
distinct criteria.

Twelve definitions require measured candidates to be supplied at runtime:

- `student_failure`: RC018, RC038, RC058, RC066, RC072, RC078.
- `passing_candidate`: RC019, RC020, RC039, RC040, RC059, RC060.

These dependencies are preserved as task-generation requirements. Historical
experimental candidates and their measurements are deliberately not included.
Thus this is a complete definition set, not a frozen replay of those 12
experiment-dependent question instances. Baseline/history observations must be
created by the runtime before presenting such tasks to a model.

The `author_title` and author provenance fields are catalog metadata and may
describe the intended fault. Keep them outside the model-facing task context,
as in the original task interface.

The source manifest is identified by SHA-256 in manifest.json. All 78 starter
checksums match its `starter_sha256` values after LF normalization. Folder
classification follows the current manuscript; no experimental success counts
are included.

## Integration with the minimal code release

The RC001..RC078 catalog is distributed as task data. It is separate from the
legacy INV/OTA/SRAM curriculum exposed by `arena-trace tasks`; these RC IDs are
not registered as executable tasks in this minimal release. This addition does
not include the original recovery-task runner or verifier. A consumer must map
the task predicates and runtime dependencies to a compatible evaluator.

Author titles are translated into English for this release. Task schemas and
all other task fields remain unchanged. No author names, private machine paths,
or experimental records are added.
