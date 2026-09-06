# Trusted evaluation operator

This is the operator's procedure, not permission for the optimization agent to
access a hidden test. The shipped repository is ready for local optimization;
hidden-corpus curation and official Linux evaluation remain external work.

The current seed contains no task prompt or auto-loaded agent instruction file.
This removes a duplicated instruction layer, not all contextual influences:
repository documentation and old prompts in Git history remain accessible.
Use the same environment and externally supplied prompt for compared agent runs.

## Before optimization

1. Record the remote `main` commit and `baseline-v2` commit. Preserve a trusted
   clone and Docker toolchain image on a separate host/account inaccessible to
   the optimization agent. Local Git tags and integrity checks are tamper
   evidence, **not** an immutable security boundary against the same user.
2. Decide whether the 30-instance starter is the experiment's development set
   or expand the 300-entry catalog first. Expansion is operator setup, not a
   candidate optimization. Freeze the expanded manifest, corpus, and settings
   in a new experiment seed before the candidate sees it. Record the exact task
   prompt and agent configuration externally with the run, not in the candidate
   checkout. Do not change the seed tag or data under an active run. See
   `DATASET.md`.
3. Preregister test counts, family/lineage disjointness, duplicate exclusions,
   difficulty-selection rules, SAT/UNSAT mix, hardware/compiler/image IDs,
   timeout/memory/file caps, run ordering, repeats, metrics, and feedback budget.
   The shipped default is equal weight per instance, with per-family reports.
4. Prepare 100 sealed validation inputs and 150 withheld public-family final
   inputs. Generate the other 150 fresh final inputs **after** source submission
   freeze using independently chosen application data and documented generators.
   Do not pick inputs by inspecting candidate performance. Public unseen files
   are not a pretraining-contamination guarantee; fresh seeds are not new
   generator families. Tiny shipped smoke tests do not substitute for holdouts.
5. Audit generator/source relationships manually, then run `corpus import` as
   documented in `DATASET.md`. Compare development, validation, and final with
   each other, including byte/isomorphism hashes and conservative lineage keys.

## Accept only source changes

`./sat submit` produces `solver-source.tar.gz`, a tracked-source patch, and a
manifest with baseline/source/archive hashes. The archive includes new files;
new source files must be staged before submission so the patch includes them.
Do not accept a candidate evaluator, checker, executable, container image,
build script, or modified manifest as trusted infrastructure.

On a clean copy of the registered seed, inspect the patch and confirm every
changed path is under `solver/kissat/src/` or `solver/satsuma/src/`.
Only the C/C++ source/header extensions listed in `config/experiment.json` are
editable; build files and licenses remain frozen. Run
`git apply --check` before applying the patch, then `./sat integrity`. Verify
that the resulting source hash matches the submission manifest. Reject source
symlinks and files outside the allowlist. Do not blindly extract an untrusted
archive over a trusted checkout. Build source with the original fixed recipes
and the frozen toolchain image (`--network=none`). Build baseline from its tag.

For actively adversarial candidates, use disposable VMs, build-time sandboxing,
and an evaluator service with separate privileges. Docker is useful isolation,
not a formally secure sandbox; compiler/kernel/checker vulnerabilities remain
possible. Never mount a Docker socket or evaluator directory into a candidate.

## Host and run procedure

Use a dedicated Linux machine, at least 34 GiB Docker-available RAM (more host
headroom is preferable), one fixed CPU affinity, and ample dedicated scratch
storage. The script pins CPU 0. Ensure it is available and isolated from other
jobs; do not compare different architectures, CPUs, compiler images, or profiles.
Control thermal state and frequency scaling, warm up consistently, and
alternate baseline/candidate run order across paired sessions.

The solver container receives only its current read-only input and an empty
writable certificate directory. It cannot access the rest of the corpus, host
logs, evaluator, or network. The container is destroyed after each invocation;
no state carries between instances. Preprocessing scratch lives in bounded
tmpfs, charged to the memory cap. The proof directory is host-backed, with a
per-file cap but **no aggregate directory quota** in the portable adapter. Use
a dedicated quota-limited filesystem for adversarial official evaluation;
monitor free space and stop on infrastructure failure. Archive or remove
certificates under operator control between batches if necessary.

The host evaluator starts a fresh frozen checker for every certificate. The
independent SAT assignment checker is resource-limited on Linux; the DSR checker
runs in a separate fixed baseline image with read-only input and proof. DSR
checking is independently implemented, not formally verified. An invalid
certificate disqualifies the submission; a checker timeout/resource exhaustion
is unsolved, not incorrect. Infrastructure failures invalidate a run and must
not be presented as candidate errors or performance gains.

Example on the **operator host**, after the corpus is imported and budgets approved:

```sh
./sat setup --backend docker
./sat evaluate --variant baseline --backend docker --profile official \
  --manifest /srv/sat-private/final.json --corpus-root /srv/sat-private/cnfs \
  --output /srv/sat-results/baseline --acknowledge-cost
./sat evaluate --variant candidate --backend docker --profile official \
  --manifest /srv/sat-private/final.json --corpus-root /srv/sat-private/cnfs \
  --output /srv/sat-results/candidate --acknowledge-cost
./sat compare /srv/sat-results/baseline/report.json /srv/sat-results/candidate/report.json
```

The runner refuses official evaluation on macOS, Docker Desktop, insufficient
Docker memory, or without explicit cost acknowledgement. That flag is an
operator acknowledgment, not authority to spend money or obtain credentials.
The runner records raw certificate paths, so operator reports must remain
private. At most three validation feedback rounds should return **aggregate
scores only**, with no instance IDs, proofs, logs, or family-specific clues.
The operator service, not the local CLI, must enforce this feedback count.

## Cost and interpretation

At 300 final instances, a single 5,000-second repeat can consume **417 core-hours
per solver**, before checking. Three repeats of both baseline and candidate can
consume **2,500 solver core-hours**, with further certificate-checking time and
storage. No such workload is launched by setup, CI, or this seed task.

Primary outcome is matched held-out PAR-2 with zero invalid results. Report
consistent solved count, per-family scores, solver/checker time, failures, and
resource settings. Preserve all preregistered trials, not just the best timing.
Use paired, family-clustered uncertainty estimates when making a research
claim; the supplied `compare` command is a descriptive comparison, not a
significance test. Report public-family and fresh-application final subsets
separately. Any unforeseen harness change requires a new frozen evaluation and
rerunning both solvers; never fix the evaluator only for one candidate.

The CLI never automatically declares an official/state-of-the-art result.
Independent correctness stress tests, broader contemporary solver comparisons,
lineage audit, and the preregistered analysis determine what can be claimed.
