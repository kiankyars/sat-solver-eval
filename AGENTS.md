# SAT solver improvement experiment

Read `PROMPT.md`, `README.md`, `docs/SOLVER.md`, and `docs/DATASET.md` first.
The task is to improve the solver, not the scoring system. This seed contains no
optimization result and no hidden test instances.

- Start with `./sat doctor`, `./sat test`, then the commands in `PROMPT.md`.
- Edit only C/C++ sources/headers (`.c`, `.h`, `.cpp`, `.hpp`, `.cc`, `.hh`, `.inc`)
  under `solver/kissat/src/**` and `solver/satsuma/src/**`. Preserve licenses.
  Build recipes, wrappers, checker sources, harness, manifests, settings, and
  this file are frozen. Do not move `baseline-v1` or disable integrity checks.
- `./sat build --variant baseline` rebuilds the original source from the tag.
  Candidate builds use the working tree. Use identical backend and profiles.
- Never access hidden data or request per-instance validation feedback.
  Public competition instances may have appeared in model training data.
- No benchmark-name/hash answer tables, embedded answers, evaluator probing,
  runtime network, or cross-instance persistent state. Instance-content-based
  general heuristics are allowed.
- Correctness comes first: SAT needs an original-formula assignment; UNSAT
  needs an original-formula binary DSR proof accepted by the frozen checker.
- Make small, measured source changes. Track failed ideas as well as wins in
  ignored `runs/` notes. Do not select a winner from one noisy timing result.
- No cloud spending is authorized by this file. Setup is local; the official
  profile is expensive and needs explicit operator authorization and Linux.
- Native/macOS and Docker Desktop measurements are development previews.
  Do not describe a development speedup as new state of the art.
- Finish with `./sat test`, fresh baseline/candidate comparisons, and
  `./sat submit`. Report resource limits, source hashes, correctness failures,
  all selected runs, and limitations. Do not change the evaluator to fix a
  candidate failure. Report a suspected harness bug to the user separately.
