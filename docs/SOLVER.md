# Frozen upstream source provenance

The editable `solver/kissat/` and `solver/satsuma/` trees are the actual
`satsuma-iter-kissat` entry in the official SAT Competition 2026 source archive:

- URL: https://satcompetition.github.io/2026/downloads/solvers/anders.tar.xz
- Retrieved: 2026-09-06
- SHA-256: `1951097402831eaaa9cdd6b670a09b9e9cbe1714e16d48b17d420e3b40816623`
- Archive root: `anders/satsuma-iter-kissat/src/`
- `solver/kissat/` maps to archive `solver/kissat/`.
- `solver/satsuma/` maps to archive `solver/satsuma-dev/`.
- `vendor/dsr-trim/` maps to archive `dsr-trim/`.

Generated executables, object files, static libraries, and Kissat's generated
build directory/makefile were omitted. The unused AE-Kissat alternative was
not copied. Source and upstream license files were otherwise retained.
The `vendor/submission/` directory preserves the relevant original build/run scripts
and license notes for comparison; those original scripts are not our runner.

## Full winning pipeline, with portability adaptations

The runner performs Satsuma `fix` with `--silent --full-skip-limit 100000000
--add-reduced-as-unit --bsr`, then the submission's **modified** Kissat.
Kissat appends its proof to the symmetry-breaking preprocessor's proof rather
than replacing it. This is not the same experiment as running stock Kissat.

The adaptation uses `cc`/`c++` and omits Satsuma's `-march=native` so the same
fixed source/configuration builds on native macOS and Linux. All comparisons
must use the same compiler, architecture, build flags, and runner. We preserve
Kissat's upstream `--competition` mode and explicitly select `-O3` (upstream
otherwise guesses a lower optimization level for the compiler name `cc`).
Build artifacts are isolated outside
the source trees. Forced recompilation (`make -B`) prevents stale inherited
objects from bypassing the frozen compiler settings. The wrapper also fixes normal 10/20 return-code handling and
uses a unique scratch directory with checked preprocessing errors.

## Certificates and independent checking

UNSAT certificates are **binary deletion substitution redundancy (DSR/SR)**,
not merely DRAT: symmetry breaking needs the stronger proof system. Use the
frozen, separately compiled checker with the **original** input formula:

```
build/checker/dsr-trim -f ORIGINAL.cnf OUTPUT.proof /dev/null
```

Require a successful exit and the exact output line `s VERIFIED UNSAT`.
The independent checker is the official submission's unmodified dsr-trim
source; upstream: https://github.com/ccodel/dsr-trim. It is independently
implemented, but is not itself formally verified. `lsr-check` is also built
for optional checking of emitted LSR certificates; it is not a substitute
for the original-formula DSR check unless the conversion is performed.

SAT models can be checked directly against the original CNF: the winning
`--add-reduced-as-unit` preprocessing option retains assignments of reduced
variables, rather than requiring an external model reconstruction step.

## Licensing

The combined solver is GPLv3 because it includes Cliquer (GPLv2-or-later).
Individual Satsuma and Kissat components are MIT; dsr-trim is Apache-2.0.
See `vendor/submission/LICENSE`, `vendor/submission/LICENSE_NOTES.md`, and the component
license files. Retain these notices in derivative distributions.

Set `SAT_EVAL_BUILD_DIR` to an absolute path to build/run a separate binary
tree (default: `<repository>/build`). It must be the same for the build and
run invocations. Keep a frozen baseline build separately from candidate builds.
