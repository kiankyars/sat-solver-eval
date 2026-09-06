# SAT solver improvement experiment

A ready-to-edit **Satsuma-iter + Kissat** baseline, public competition inputs,
and a separate certificate-checking evaluator. The goal is to improve a strong
solver under fixed resource limits, not exploit a weak success signal.

Task prompts are supplied separately from the repository. No solver
optimization has been performed in this seed.

## Start

Requires Python 3.11+, Git, and a C/C++ toolchain (`cc`, `c++`, `make`). There are
no Python runtime dependencies or API keys. On macOS, use the Xcode command-line
tools; Linux needs a C++20-capable compiler. From the repository root:

```sh
./sat doctor
./sat setup
./sat test
./sat evaluate --variant baseline --profile quick

# After source edits:
./sat build
./sat evaluate --variant candidate --profile quick
./sat compare runs/BASELINE/report.json runs/CANDIDATE/report.json
./sat submit
```

Setup builds both variants and the independent checker, downloads/verifies the
30-instance starter corpus, and creates tiny correctness smoke inputs. It does
not run an optimization experiment or start any paid compute. Baseline builds
come from the frozen `baseline-v2` Git tag, even after the candidate is edited.
Candidate builds use the working tree. Stale source/binary builds are rejected.

`baseline-v2` removes the checked-in task instructions; its solver, checker,
build recipes, scoring, resource limits, and development corpus are unchanged
from `baseline-v1`. Earlier prompts remain in Git history. Existing v1 builds
need `./sat setup` to record the new seed reference.

Run an inexpensive six-instance end-to-end smoke check:

```sh
./sat evaluate --variant baseline --profile smoke \
  --manifest data/smoke/manifest.json --corpus-root data/smoke
SAT_EVAL_INTEGRATION=1 SAT_EVAL_BUILD_DIR="$PWD/build/native/candidate" ./sat test
```

`--limit 3` is available for a quick infrastructure pilot; it is not a full
development result. Reports and certificates go under ignored `runs/`.

## Resource-isolated development

With Docker running:

```sh
./sat setup --backend docker
./sat evaluate --variant baseline --backend docker --profile quick
./sat build --backend docker
./sat evaluate --variant candidate --backend docker --profile quick
```

The container has no network, one pinned CPU, a memory cap, read-only root and
input, no capabilities, and only a per-instance writable certificate directory.
The evaluator and its logs are not mounted. UNSAT proofs are checked using the
**baseline** checker image, never the candidate's checker. SAT assignments are
checked in a separate resource-limited host process against the original input.

The toolchain is built once from a digest-pinned Ubuntu base, then baseline and
candidate builds reuse the same image without network. Reports record the
resolved toolchain/image IDs. Apt package versions are resolved on first setup:
preserve/export that toolchain image for exact cross-machine reproducibility,
and never rebuild it between compared runs.

Native runs and Docker Desktop runs are **previews**, not official benchmark
results. macOS does not enforce the native Linux memory/affinity controls.

| Profile | Solver / checker limit | Memory | Repeats | Purpose |
|---|---:|---:|---:|---|
| `smoke` | 10 / 20 seconds | 1 GiB | 1 | Infrastructure correctness |
| `quick` | 30 / 60 seconds | 4 GiB | 1 | Cheap iteration |
| `development` | 300 / 600 seconds | 4 GiB | 3 | Confirm public-data changes |
| `official` | 5,000 / 10,000 seconds | 32 GiB | 3 | Separately authorized Linux evaluation |

File caps are 64 MiB / 256 MiB / 1 GiB / 4 GiB respectively; the fixed output
parser additionally accepts at most 256 MiB of solver stdout. Container temporary
space is memory-backed and capped; the certificate directory uses host storage.
See the operator guide for disk provisioning and threat-model limitations.

PAR-2 is mean per-instance runtime with unsolved instances charged twice the
timeout. Repeats are averaged per instance, retaining every timeout penalty; consistent solved counts
require every repeat to verify. Any incorrect answer or rejected certificate
disqualifies the entire submission. Checking time is recorded separately.

## What is included

- Full source for the actual 2026 winning pipeline, not stock Kissat alone.
  Preprocessing, modified Kissat, binary DSR proof generation, and licenses are
  preserved. Compiler/runner portability adaptations are explicit.
- Independently implemented DSR checking against the **original** formula,
  original-formula SAT model checking, adversarial tests, and a small exhaustive
  correctness oracle. The DSR checker is not formally verified.
- **30 acquired, SHA-256-pinned** public 2024/2025 instances, plus a **300-entry
  acquisition catalog**, with family/lineage and duplicate-exclusion metadata.
  CNFs are downloaded, not committed or relicensed. Initial acquisition is
  44 MiB compressed / 873 MiB unpacked.
- Matched build/run metadata, PAR-2 and per-family reports, frozen-file checks,
  and source-only submission archives.

The proposed 100-instance validation and 300-instance final sets are **not yet
provisioned**. Their curation, lineage review, and post-submission fresh inputs
belong to a separate evaluator. A sibling folder or `.gitignore` does not hide
data from an agent with access to the machine. Public holdouts also cannot
establish absence from model pretraining.

## Experiment documents

- [docs/SOLVER.md](docs/SOLVER.md): source provenance, proof format, adaptations, licenses.
- [docs/DATASET.md](docs/DATASET.md): acquisition, expansion, and leakage-aware splits.
- [docs/OPERATOR.md](docs/OPERATOR.md): trusted final evaluation and claim criteria.
- [config/experiment.json](config/experiment.json): declared resource profiles.

A development speedup over this baseline is **not automatically new state of
the art**. That requires a sufficiently broad, preregistered held-out comparison
on matched hardware against appropriate contemporary solvers.
