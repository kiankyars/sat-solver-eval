#!/bin/bash

# Get this script's directory (full, non-relative; agnostic to symlinks)
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
CURR_DIR=$(pwd)

set -ex

echo "Building kissat..."
cd "$SCRIPT_DIR/solver/kissat"
rm -f makefile src/makefile
CC=gcc ./configure --competition
make kissat
cp build/kissat "$SCRIPT_DIR/kissat"

echo "Building AE_kissat2025_MAB..."
cd "$SCRIPT_DIR/solver/AE_kissat2025_MAB"
rm -f makefile src/makefile
CC=gcc ./configure --competition
make kissat
cp build/kissat "$SCRIPT_DIR/ae_kissat_mab"

echo "Building satsuma..."
cd "$SCRIPT_DIR/solver/satsuma-dev"
make
cp satsuma "$SCRIPT_DIR/satsuma"

if [ -d "$SCRIPT_DIR/dsr-trim" ]; then
    cd "$SCRIPT_DIR/dsr-trim"
    make
fi

cd "$CURR_DIR"
