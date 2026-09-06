"""Small Docker launch adapter; the trusted evaluator stays on the host.

Only a per-invocation scratch directory is writable inside the solver. Never
mount the repository, evaluator logs, Docker socket, or full dataset there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys


def docker_info() -> dict:
    output = subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    info = json.loads(output)
    return {key: info.get(key) for key in ("ServerVersion", "OSType", "Architecture", "MemTotal", "NCPU", "OperatingSystem", "Name")}


def image_id(name: str) -> str:
    return subprocess.check_output(["docker", "image", "inspect", name, "--format", "{{.Id}}"], text=True).strip()


def container_name(output: Path, kind: str) -> str:
    suffix = hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:24]
    return f"sat-eval-{kind}-{suffix}"


def cleanup(output: Path) -> None:
    # CID files live OUTSIDE the writable candidate mount.
    for kind in ("solver", "checker"):
        path = output / f"{kind}.cid"
        identity = container_name(output, kind)
        if path.is_file():
            recorded = path.read_text().strip()
            if len(recorded) == 64 and all(char in "0123456789abcdef" for char in recorded):
                identity = recorded
        # The deterministic name also works if the adapter died before the CLI
        # finished writing its CID file.
        subprocess.run(["docker", "rm", "-f", identity], capture_output=True, timeout=20)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("solver", "checker"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--memory-mib", type=int, required=True)
    parser.add_argument("--file-size-mib", type=int, required=True)
    parser.add_argument("input", type=Path)
    parser.add_argument("proof", type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError("Container wall-time limit must be finite and positive")
    if args.memory_mib <= 0 or args.file_size_mib <= 0:
        raise ValueError("Container memory and file-size limits must be positive")
    original = args.input.resolve(strict=True)
    proof = args.proof.absolute()
    scratch = proof.parent.resolve(strict=True)
    output = scratch.parent
    is_solver = args.kind == "solver"
    if not is_solver and (proof.is_symlink() or not proof.is_file()):
        raise ValueError("Checker requires a regular proof")
    scratch.chmod(0o777)
    cid = output / f"{args.kind}.cid"
    cid.unlink(missing_ok=True)
    command = [
        "docker", "run", "--rm", "--init", "--pull", "never", "--cidfile", str(cid),
        "--name", container_name(output, args.kind),
        "--log-driver", "none",
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--user", "65534:65534",
        "--env", "TMPDIR=/tmp",
        "--cpuset-cpus", "0", "--cpus", "1", "--pids-limit", "64",
        "--memory", f"{args.memory_mib}m", "--memory-swap", f"{args.memory_mib}m",
        "--ulimit", "core=0", "--ulimit", f"fsize={args.file_size_mib * 1024 * 1024}",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,size={args.memory_mib}m,mode=1777",
        "--mount", f"type=bind,src={original},dst=/input.cnf,readonly",
    ]
    if is_solver:
        command.extend(["--mount", f"type=bind,src={scratch},dst=/output"])
        workload = ["/opt/sat/scripts/run_solver.sh", "/input.cnf", "/output/" + proof.name]
    else:
        command.extend(["--mount", f"type=bind,src={proof},dst=/proof.dsr,readonly"])
        workload = ["/opt/sat/build/checker/dsr-trim", "-f", "/input.cnf", "/proof.dsr", "/dev/null"]
    # Inner timeout also kills workloads if the host adapter is interrupted.
    command.extend([args.image, "timeout", "--signal=KILL", str(args.seconds), *workload])
    try:
        return subprocess.call(command)
    finally:
        cleanup(output)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Container infrastructure failed: {exc}", file=sys.stderr)
        raise SystemExit(125)
