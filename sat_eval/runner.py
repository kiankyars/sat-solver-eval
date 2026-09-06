"""Run resource-bounded candidates and independently validate their answers.

Native runs are development previews, not isolation boundaries. A Linux
container must enforce aggregate memory, one CPU, and filesystem/network
isolation for official evaluation. POSIX RLIMIT_AS alone is per process.
"""

from __future__ import annotations

import hashlib
import math
import os
import resource
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Sequence

from .check import (
    CertificateError,
    checker_verified,
    iter_clauses,
    parse_solver_output,
    read_header,
)


@dataclass(frozen=True)
class ResourceLimits:
    wall_seconds: float = 5000.0
    memory_mib: int = 32768
    cpu_seconds: int | None = None
    file_size_mib: int = 1024

    def __post_init__(self) -> None:
        if not math.isfinite(self.wall_seconds) or self.wall_seconds <= 0:
            raise ValueError("wall_seconds must be finite and positive")
        if self.memory_mib <= 0 or self.file_size_mib <= 0:
            raise ValueError("Memory and output file limits must be positive")
        if self.cpu_seconds is not None and self.cpu_seconds <= 0:
            raise ValueError("CPU limit must be positive")


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    wall_seconds: float
    timed_out: bool
    error: str | None = None


@dataclass(frozen=True)
class EvaluationResult:
    instance: str
    sha256: str
    family: str
    run_id: str
    status: str
    claimed_status: str | None
    verified: bool
    disqualified: bool
    solver_seconds: float
    checker_seconds: float
    par2_seconds: float
    timeout_seconds: float
    solver_returncode: int | None
    checker_returncode: int | None
    reason: str
    measurement_mode: str
    output_dir: str

    def to_dict(self) -> dict:
        return asdict(self)


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _child_limits(limits: ResourceLimits) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cpu = limits.cpu_seconds or math.ceil(limits.wall_seconds)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    file_bytes = limits.file_size_mib * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    if sys.platform.startswith("linux"):
        memory_bytes = limits.memory_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if hasattr(os, "sched_getaffinity"):
            available = os.sched_getaffinity(0)
            if available:
                os.sched_setaffinity(0, {min(available)})


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_process(
    command: Sequence[str],
    limits: ResourceLimits,
    *,
    stdout: Path,
    stderr: Path,
    cwd: Path,
) -> ProcessResult:
    if not command:
        raise ValueError("Empty command")
    env = os.environ.copy()
    env.update({"LC_ALL": "C", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    start = time.monotonic()
    with stdout.open("xb") as output, stderr.open("xb") as errors:
        try:
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                cwd=cwd,
                env=env,
                start_new_session=True,
                preexec_fn=lambda: _child_limits(limits),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ProcessResult(None, time.monotonic() - start, False, str(exc))
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            _kill_group(process)

        remaining = max(0, limits.wall_seconds - (time.monotonic() - start))
        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        try:
            process.wait()
        finally:
            timer.cancel()
            timer.join()
            _kill_group(process)
            process.wait()
        elapsed = time.monotonic() - start
        timed_out = expired.is_set() or elapsed > limits.wall_seconds
        return ProcessResult(process.returncode, elapsed, timed_out)


def evaluate_instance(
    instance: Path,
    solver_command: Sequence[str],
    checker_command: Sequence[str],
    limits: ResourceLimits,
    checker_limits: ResourceLimits,
    *,
    output_dir: Path,
    family: str = "unknown",
    run_id: str = "run",
    measurement_mode: str = "native-preview",
) -> EvaluationResult:
    """Append INPUT PROOF to solver command; expand checker argument templates.

    The caller owns checker_command and must freeze it outside candidate writes.
    Each call requires a new output directory. Failed checker infrastructure or
    bad reference inputs raise errors instead of silently assigning solver scores.
    """
    if measurement_mode not in {"native-preview", "docker-preview", "linux-container"}:
        raise ValueError("Unknown measurement mode")
    if measurement_mode == "linux-container" and not sys.platform.startswith("linux"):
        raise ValueError("Official container measurement requires Linux")
    instance = Path(instance).resolve(strict=True)
    header = read_header(instance)
    for _ in iter_clauses(instance):
        pass
    original_digest = _digest(instance)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    candidate_dir = output_dir / "candidate"
    candidate_dir.mkdir(mode=0o777)
    candidate_dir.chmod(0o777)
    stdout = output_dir / "solver.stdout"
    proof = candidate_dir / "certificate.dsr"
    solver = run_process(
        [*solver_command, str(instance), str(proof)],
        limits,
        stdout=stdout,
        stderr=output_dir / "solver.stderr",
        cwd=output_dir,
    )
    claimed_status = None
    checker = ProcessResult(None, 0, False)

    def result(status: str, reason: str, *, verified: bool = False, disqualified: bool = False) -> EvaluationResult:
        return EvaluationResult(
            instance=str(instance),
            sha256=original_digest,
            family=family,
            run_id=run_id,
            status=status,
            claimed_status=claimed_status,
            verified=verified,
            disqualified=disqualified,
            solver_seconds=solver.wall_seconds,
            checker_seconds=checker.wall_seconds,
            par2_seconds=solver.wall_seconds if verified else 2 * limits.wall_seconds,
            timeout_seconds=limits.wall_seconds,
            solver_returncode=solver.returncode,
            checker_returncode=checker.returncode,
            reason=reason,
            measurement_mode=measurement_mode,
            output_dir=str(output_dir),
        )

    try:
        if _digest(instance) != original_digest:
            return result("invalid", "Reference input changed during solver execution", disqualified=True)
    except OSError:
        return result("invalid", "Reference input disappeared during solver execution", disqualified=True)
    if solver.timed_out:
        return result("timeout", "Solver exceeded the wall-time limit")
    if solver.error:
        return result("crash", solver.error)
    if measurement_mode != "native-preview":
        # Docker reserves 125-127 for launch failures. GNU timeout returns 124;
        # SIGKILL/OOM (including timeout --signal=KILL) is exposed as 137.
        if solver.returncode in {125, 126, 127}:
            raise RuntimeError(f"Docker solver infrastructure failed (exit {solver.returncode}); see solver.stderr")
        if solver.returncode == 124:
            return result("timeout", "Container workload exceeded its wall-time limit")
        if solver.returncode == 137:
            return result("resource", "Container workload was killed by a resource limit or inner timeout")
    if solver.returncode not in {0, 10, 20}:
        return result("crash", "Solver exited abnormally or hit a resource limit")
    try:
        output_stat = stdout.lstat()
    except FileNotFoundError:
        return result("invalid", "Solver removed its output file", disqualified=True)
    if not stat.S_ISREG(output_stat.st_mode):
        return result("invalid", "Solver output must be a regular file, not a symlink", disqualified=True)
    try:
        answer = parse_solver_output(stdout, header.variables, collect_assignment=False)
        claimed_status = answer.status
    except CertificateError as exc:
        if solver.returncode in {10, 20}:
            return result("invalid", str(exc), disqualified=True)
        return result("unsolved", str(exc))
    expected_exit = {"SATISFIABLE": 10, "UNSATISFIABLE": 20, "UNKNOWN": 0}[answer.status]
    if solver.returncode != expected_exit:
        return result("invalid", "Exit code contradicts the solver status", disqualified=True)
    if answer.status == "UNKNOWN":
        return result("unsolved", "Solver returned UNKNOWN")
    if answer.status == "SATISFIABLE":
        checker = run_process(
            [sys.executable, str(Path(__file__).with_name("check.py")), "sat", str(instance), str(stdout)],
            checker_limits,
            stdout=output_dir / "checker.stdout",
            stderr=output_dir / "checker.stderr",
            cwd=output_dir,
        )
        if checker.error:
            raise RuntimeError(f"SAT checker infrastructure failed: {checker.error}")
        if checker.timed_out:
            return result("checker_timeout", "SAT checker exceeded its separate wall-time limit")
        if checker.returncode is not None and checker.returncode < 0:
            return result("checker_resource", "SAT checker terminated by signal or resource limit")
        if checker.returncode == 75:
            return result("checker_resource", "SAT checker exhausted its memory limit")
        with (output_dir / "checker.stdout").open() as stream:
            verified = checker.returncode == 0 and any(line.strip() == "s VERIFIED SAT" for line in stream)
        if not verified:
            return result("invalid", "Assignment does not satisfy every original clause", disqualified=True)
        return result("sat", "Assignment satisfies every original clause", verified=True)

    try:
        proof_stat = proof.lstat()
    except FileNotFoundError:
        return result("invalid", "UNSAT result has no certificate", disqualified=True)
    if not stat.S_ISREG(proof_stat.st_mode):
        return result("invalid", "Certificate must be a regular file, not a symlink", disqualified=True)
    check_argv = [arg.replace("{input}", str(instance)).replace("{proof}", str(proof)) for arg in checker_command]
    checker = run_process(
        check_argv,
        checker_limits,
        stdout=output_dir / "checker.stdout",
        stderr=output_dir / "checker.stderr",
        cwd=output_dir,
    )
    if checker.error:
        raise RuntimeError(f"Checker infrastructure failed: {checker.error}")
    if checker.timed_out:
        return result("checker_timeout", "Checker exceeded its separate wall-time limit")
    if measurement_mode != "native-preview":
        # Checker infrastructure/resource failures do not prove an answer wrong.
        if checker.returncode in {125, 126, 127}:
            raise RuntimeError(f"Docker checker infrastructure failed (exit {checker.returncode}); see checker.stderr")
        if checker.returncode == 124:
            return result("checker_timeout", "Container checker exceeded its separate wall-time limit")
        if checker.returncode == 137:
            return result("checker_resource", "Container checker was killed by a resource limit or inner timeout")
    if checker.returncode is not None and checker.returncode < 0:
        return result("checker_resource", "Checker terminated by signal or resource limit")
    if checker.returncode == 255:
        with (output_dir / "checker.stderr").open("r", errors="replace") as stream:
            if any(line.strip() in {
                "Ran out of memory on xmalloc()",
                "Ran out of memory on xcalloc()",
                "Ran out of memory on xrealloc()",
            } for line in stream):
                return result("checker_resource", "DSR checker exhausted its memory limit")
    if not checker_verified(output_dir / "checker.stdout", checker.returncode):
        return result("invalid", "Independent DSR checker rejected the certificate", disqualified=True)
    return result("unsat", "DSR proof verifies against the original formula", verified=True)


def summarize(results: Sequence[EvaluationResult]) -> dict:
    """Average PAR-2 across repeats, then equally across distinct instances.

    Any incorrect result disqualifies the entire candidate. Every repeat must
    verify for an instance to count as consistently solved. A timed-out repeat
    contributes its full penalty even when the other repeats finish quickly.
    """
    groups: dict[tuple[str, str], list[EvaluationResult]] = defaultdict(list)
    for result in results:
        groups[(result.sha256, result.family)].append(result)
    instances = []
    for (digest, family), repeats in groups.items():
        instances.append({
            "sha256": digest,
            "family": family,
            "repeats": len(repeats),
            "par2_seconds": sum(row.par2_seconds for row in repeats) / len(repeats),
            "median_par2_seconds": median(row.par2_seconds for row in repeats),
            "solved_all_repeats": all(row.verified for row in repeats),
            "disqualified": any(row.disqualified for row in repeats),
        })
    families: dict[str, list[dict]] = defaultdict(list)
    for instance in instances:
        families[instance["family"]].append(instance)
    disqualified = any(row.disqualified for row in results)
    diagnostic_par2 = sum(row["par2_seconds"] for row in instances) / len(instances) if instances else None
    return {
        "eligible": bool(instances) and not disqualified,
        "disqualified": disqualified,
        "runs": len(results),
        "instances": len(instances),
        "verified_runs": sum(row.verified for row in results),
        "solved_all_repeats": sum(row["solved_all_repeats"] for row in instances),
        "par2_seconds": None if disqualified else diagnostic_par2,
        "diagnostic_par2_seconds": diagnostic_par2,
        "diagnostic_mean_of_repeat_medians_seconds": sum(row["median_par2_seconds"] for row in instances) / len(instances) if instances else None,
        "measurement_modes": sorted({row.measurement_mode for row in results}),
        "families": {
            name: {
                "instances": len(rows),
                "solved_all_repeats": sum(row["solved_all_repeats"] for row in rows),
                "par2_seconds": None if disqualified else sum(row["par2_seconds"] for row in rows) / len(rows),
            }
            for name, rows in sorted(families.items())
        },
    }
