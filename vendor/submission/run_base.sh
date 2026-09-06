#!/bin/bash

# Usage: ./run.sh <solver> <cnf> <proof/dir>

# The core solver running script.
# Use `run_satsuma_kissat.sh` or `run_satsuma_ae_kissat.sh` to run each solver.

# Get this script's directory (full, non-relative; agnostic to symlinks)
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Constant limits
MAX_CNF_SIZE=1073741824         # 1 GB, in bytes
MAX_NUM_CNF_VARS=1000000        # 1 million variables
MAX_NUM_CNF_CLAUSES=100000000   # 100 million clauses

# SAT Competition return codes (currently unused)
ERROR_RETURN_CODE=0
SAT_RETURN_CODE=10
UNSAT_RETURN_CODE=20

# Must provide exactly 3 arguments
if [ $# -ne 3 ]; then
    echo "Usage: $0 <solver> <cnf> <proof/dir>" >&2
    exit 1
fi

SOLVER="$1"
CNF="$2"
PROOF_DIR="$3"
SB_CNF="$PROOF_DIR/temp.cnf"
PROOF="$PROOF_DIR/proof.out"

SATSUMA="$SCRIPT_DIR/satsuma"

# Check if the solver executable exists and is a file
if [ ! -f "$SOLVER" ]; then
    echo "Error: Solver executable '$SOLVER' not found!" >&2
    exit 1
fi

# Check if the satsuma executable exists and is a file
if [ ! -f "$SATSUMA" ]; then
    echo "Error: Satsuma executable '$SATSUMA' not found!" >&2
    exit 1
fi

# Check if the CNF formula file exists
if [ ! -f "$CNF" ]; then
    echo "Error: File '$CNF' not found!" >&2
    exit 1
fi

# Check that the proof directory exists and is a directory
if [ ! -d "$PROOF_DIR" ]; then
    echo "Error: Proof directory '$PROOF_DIR' not found!" >&2
    exit 1
fi

# Remove the proof file, if it already exists
if [ -f "$PROOF" ]; then
    rm "$PROOF"
fi

# Get the file size, in bytes
# Output of `ls` is mostly POSIX-specified. The file size is in the 5th column
# CNF_FILE_SIZE=$(ls -ln "$CNF" | awk '{print $5}')

# Find out how many variables and clauses the formula has
# CNF_HEADER=$(awk '$1=="p" && $2=="cnf" {print $3, $4; exit}' "$CNF")
# CNF_NUM_VARS=$(echo $CNF_HEADER | awk '{print $1}')
# CNF_NUM_CLAUSES=$(echo $CNF_HEADER | awk '{print $2}')

# Only run satsuma if no limits are exceeded
# if [ $CNF_FILE_SIZE -lt $MAX_CNF_SIZE ] \
#     && [ $CNF_NUM_VARS -lt $MAX_NUM_CNF_VARS ] \
#     && [ $CNF_NUM_CLAUSES -lt $MAX_NUM_CNF_CLAUSES ]; then
echo "c Running satsuma on $CNF"
"$SATSUMA" fix "$CNF" \
    --silent \
    --full-skip-limit 100000000 \
    --add-reduced-as-unit \
    --bsr --proof-file "$PROOF" \
    --out-file "$SB_CNF"
CNF_TO_SOLVE="$SB_CNF"
# else
#     echo "c Skipping satsuma, the formula is too large"
#     CNF_TO_SOLVE="$CNF"
# fi

# Run the solver (already configured for quiet mode)
"$SOLVER" "$CNF_TO_SOLVE" "$PROOF"
SOLVER_RETURN_CODE=$?

# Cleanup
if [ -f "$SB_CNF" ]; then 
    rm "$SB_CNF"
fi

# Return the captured return code from the solver
exit $SOLVER_RETURN_CODE
