#!/bin/bash

# Usage: ./run_satsuma_kissat.sh <cnf> <proof/dir>

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

if [ $# -ne 2 ]; then
    echo "Usage: $0 <cnf> <proof/dir>"
    exit 1
fi

# Note: "$@" expands to all positional arguments passed to the script, starting from $1.
KISSAT="$SCRIPT_DIR/solver/kissat/build/kissat"
"$SCRIPT_DIR/run_base.sh" "$KISSAT" "$@"
