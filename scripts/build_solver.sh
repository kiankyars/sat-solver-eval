#!/usr/bin/env bash
# Build the full Satsuma-iter + Kissat submission, never a bare-Kissat substitute.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILD=${SAT_EVAL_BUILD_DIR:-"$ROOT/build"}
JOBS=${SAT_BUILD_JOBS:-2}
case "$JOBS" in ''|*[!0-9]*|0) echo 'SAT_BUILD_JOBS must be a positive integer' >&2; exit 2;; esac
for tool in cc c++ make; do
    command -v "$tool" >/dev/null || { echo "Missing build dependency: $tool" >&2; exit 2; }
done
mkdir -p "$BUILD/bin" "$BUILD/checker"
BUILD=$(cd -- "$BUILD" && pwd)
WORK=$(mktemp -d "$BUILD/compile.XXXXXXXX")
# Only remove the uniquely allocated scratch tree, not the user's source tree.
trap 'rm -rf -- "$WORK"' EXIT
cp -R "$ROOT/solver/kissat" "$WORK/kissat"
cp -R "$ROOT/solver/satsuma" "$WORK/satsuma"
cp -R "$ROOT/vendor/dsr-trim" "$WORK/dsr-trim"
(
    cd "$WORK/kissat"
    CC=cc ./configure --competition -O3
    make -B -j "$JOBS" kissat
)
make -B -C "$WORK/satsuma" -j "$JOBS" CC=cc CXX=c++ \
    CFLAGS='-O3 -Wall -DNDEBUG' \
    CXXFLAGS='-std=c++20 -O3 -Wall -DNDEBUG'
make -B -C "$WORK/dsr-trim" -j "$JOBS" CC=cc CFLAGS='-O3' all
cp "$WORK/kissat/build/kissat" "$BUILD/bin/kissat"
cp "$WORK/satsuma/satsuma" "$BUILD/bin/satsuma"
cp "$WORK/dsr-trim/bin/dsr-trim" "$BUILD/checker/dsr-trim"
cp "$WORK/dsr-trim/bin/lsr-check" "$BUILD/checker/lsr-check"
echo "Built Satsuma-iter + Kissat and independent SR proof checkers."
