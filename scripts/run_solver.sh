#!/usr/bin/env bash
# Interface: run_solver.sh ORIGINAL.cnf OUTPUT.proof
# stdout: DIMACS s/v lines; exit 10=SAT, 20=UNSAT, other=unsolved/error.
# Proof: binary DSR, including symmetry-preprocessor steps from the original CNF.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILD=${SAT_EVAL_BUILD_DIR:-"$ROOT/build"}
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 INPUT.cnf OUTPUT.proof" >&2
    exit 2
fi
CNF=$1
PROOF=$2
for executable in "$BUILD/bin/satsuma" "$BUILD/bin/kissat"; do
    if [ ! -x "$executable" ]; then
        echo "Missing executable: $executable; run scripts/build_solver.sh" >&2
        exit 2
    fi
done
if [ ! -f "$CNF" ]; then echo "Input not found: $CNF" >&2; exit 2; fi
if [ -e "$PROOF" ]; then echo "Refusing to overwrite existing proof: $PROOF" >&2; exit 2; fi
mkdir -p -- "$(dirname -- "$PROOF")"
WORK=$(mktemp -d "${TMPDIR:-$(dirname -- "$PROOF")}/satsuma-run.XXXXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
# These are the exact winning submission's preprocessing options.
"$BUILD/bin/satsuma" fix "$CNF" \
    --silent \
    --full-skip-limit 100000000 \
    --add-reduced-as-unit \
    --bsr --proof-file "$PROOF" \
    --out-file "$WORK/preprocessed.cnf"
if [ ! -f "$WORK/preprocessed.cnf" ] || [ ! -f "$PROOF" ]; then
    echo 'Preprocessing failed to create formula/proof.' >&2
    exit 2
fi
# Submission Kissat appends its DRAT-compatible steps to Satsuma's DSR proof.
# --add-reduced-as-unit preserves original variables; the resulting SAT model
# can therefore be checked directly against ORIGINAL.cnf (no reconstruction).
set +e
"$BUILD/bin/kissat" "$WORK/preprocessed.cnf" "$PROOF"
result=$?
set -e
exit "$result"
