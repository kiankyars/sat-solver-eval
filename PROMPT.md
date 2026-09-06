# Ready-to-paste experiment prompt

Improve the SAT solver in this repository to reduce PAR-2 on the development
corpus. The baseline is the complete SAT Competition 2026
`satsuma-iter+kissat` pipeline, frozen at `baseline-v1`.

Edit only C/C++ sources and headers under `solver/kissat/src/**` and
`solver/satsuma/src/**`. Preserve DIMACS CNF
input, original-formula SAT assignments, and original-formula binary DSR UNSAT
proofs. Do not modify the evaluator, checker, build settings, wrappers, corpus,
or resource limits. Incorrect answers or invalid certificates disqualify the
candidate. Never hardcode benchmark answers, identify benchmarks to look up
answers, use runtime network, or share persistent state across instances.

Final evaluation uses one CPU core, 32 GiB memory, and a 5,000-second solver
wall-time limit per instance, including preprocessing and proof generation.
Timeouts, crashes, and unverified results cost 10,000 seconds in PAR-2; a bad
certificate is disqualifying, not merely slow. Verification has a separate
10,000-second/32-GiB budget. Each output/proof file is capped at 4 GiB; scratch
space is bounded. The fixed runner measures end-to-end invocation time,
including container startup, identically for baseline and candidate.

Begin with `./sat doctor` and `./sat test`. If setup is needed, run `./sat setup`.
Measure `./sat evaluate --variant baseline --profile quick`, make and test
small changes, rebuild with `./sat build`, then run
`./sat evaluate --variant candidate --profile quick`. Compare the resulting
reports with `./sat compare BASELINE_REPORT CANDIDATE_REPORT`.
For resource-isolated development, build with `./sat setup --backend docker`
and pass `--backend docker` to subsequent builds and evaluations.
Use the development profile with three repeats to confirm promising changes;
quick-profile results alone are exploratory. Keep compute local unless the
user explicitly approves a budget.

Optimize on public development data only. Final evaluation uses unseen,
family-disjoint instances and independent correctness tests on a separate
trusted host. Public holdouts are not guaranteed absent from pretraining;
fresh holdouts will be produced by the operator only after submission freeze.
Do not create, inspect, or select those holdouts yourself. Do not claim a
frontier improvement until the preregistered held-out comparison supports it.

Deliver the best justified source patch using `./sat submit`, reproducible
baseline/candidate reports, a short explanation of the change, and regressions
or uncertainty. Do not run the expensive official profile without permission.
