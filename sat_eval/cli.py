"""Local setup, matched measurements, and source-only submission tooling."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from . import container, corpus
from .runner import ResourceLimits, evaluate_instance, summarize

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config/experiment.json"


def config() -> dict:
    return json.loads(SETTINGS.read_text())


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "solver").rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            if name.startswith(tuple(config()["editable_paths"])):
                raise ValueError("Editable solver source symlinks are not permitted")
            record = b"link\0" + name.encode() + b"\0" + hashlib.sha256(os.readlink(path).encode()).hexdigest().encode()
        elif path.is_file():
            record = b"file\0" + name.encode() + b"\0" + corpus.sha256_file(path).encode()
        else:
            continue
        digest.update(record + b"\n")
    return digest.hexdigest()


def guard() -> str:
    """Tamper evidence, not a security boundary against a same-user agent."""
    settings = config()
    reference = settings["baseline_ref"]
    commit = git("rev-parse", reference + "^{commit}")
    names = git("diff", "--name-only", reference, "--").splitlines()
    names += git("ls-files", "--others", "--exclude-standard").splitlines()
    names += git("ls-files", "--others", "--ignored", "--exclude-standard", "--", "solver/").splitlines()
    allowed = tuple(settings["editable_paths"])
    extensions = set(settings.get("editable_extensions", [".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".inc"]))
    forbidden = [name for name in names if name and not (name.startswith(allowed) and Path(name).suffix in extensions)]
    if forbidden:
        raise ValueError("Frozen experiment files changed: " + ", ".join(forbidden[:12]))
    for folder in allowed:
        for path in (ROOT / folder).rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Source symlink not permitted: {path}")
    return commit


@contextmanager
def source_tree(variant: str):
    if variant == "candidate":
        yield ROOT
    else:
        with tempfile.TemporaryDirectory(prefix="sat-baseline-") as temporary:
            path = Path(temporary)
            archive = subprocess.Popen(["git", "-C", str(ROOT), "archive", config()["baseline_ref"]], stdout=subprocess.PIPE)
            extraction = subprocess.run(["tar", "-x", "-C", str(path)], stdin=archive.stdout)
            archive.stdout.close()
            if archive.wait() or extraction.returncode:
                raise RuntimeError("Could not extract frozen baseline source")
            yield path


def doctor() -> int:
    info = {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
            "free_disk_gib": round(shutil.disk_usage(ROOT).free / 2**30, 1),
            "tools": {name: shutil.which(name) for name in ("cc", "c++", "make", "git", "docker")}}
    try:
        info["docker"] = container.docker_info()
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        info["docker"] = "unavailable; native preview still works"
    try:
        info["baseline_commit"] = guard()
        info["integrity"] = "pass"
    except (ValueError, subprocess.CalledProcessError) as error:
        info["integrity"] = str(error)
    info["official_ready"] = bool(platform.system() == "Linux" and isinstance(info["docker"], dict)
                                  and info["docker"].get("MemTotal", 0) >= 34 * 2**30)
    print(json.dumps(info, indent=2))
    return 0


def build(variant: str, backend: str) -> None:
    reference = guard()
    directory = ROOT / "build" / backend / variant
    directory.mkdir(parents=True, exist_ok=True)
    with source_tree(variant) as source:
        metadata = {"variant": variant, "backend": backend, "source_sha256": source_digest(source),
                    "baseline_commit": reference, "built_at": stamp(), "platform": platform.platform(),
                    "machine": platform.machine()}
        if backend == "native":
            subprocess.run([str(source / "scripts/build_solver.sh")], env={**os.environ, "SAT_EVAL_BUILD_DIR": str(directory)}, check=True)
            metadata["compiler"] = subprocess.check_output(["c++", "--version"], text=True).splitlines()[0]
            metadata["compiler_cc"] = subprocess.check_output(["cc", "--version"], text=True).splitlines()[0]
            metadata["compiler_cxx"] = metadata["compiler"]
            metadata["binaries"] = {name: corpus.sha256_file(directory / name) for name in
                                    ("bin/kissat", "bin/satsuma", "checker/dsr-trim")}
        else:
            container.docker_info()
            metadata["toolchain_image"] = container.image_id("sat-eval-toolchain:v1")
            image_name = f"sat-eval-{variant}:v1"
            subprocess.run(["docker", "build", "--network=none", "-t", image_name, str(source)], check=True)
            metadata["image_id"] = container.image_id(image_name)
        write_json(directory / "build.json", metadata)


def load_build(variant: str, backend: str) -> dict:
    location = ROOT / "build" / backend / variant
    path = location / "build.json"
    if not path.is_file():
        raise ValueError(f"Missing build: ./sat build --variant {variant} --backend {backend}")
    metadata = json.loads(path.read_text())
    if metadata["baseline_commit"] != guard():
        raise ValueError("Build baseline reference has changed; rebuild")
    if variant == "candidate" and metadata["source_sha256"] != source_digest(ROOT):
        raise ValueError("Candidate source changed since build; run ./sat build")
    if backend == "native":
        for name, expected in metadata["binaries"].items():
            if corpus.sha256_file(location / name) != expected:
                raise ValueError(f"Built binary changed: {name}")
    else:
        container.image_id(metadata["image_id"])
    return metadata


def evaluate(args) -> int:
    settings = config()
    profile = settings["profiles"][args.profile]
    baseline_commit = guard()
    corpus_root = args.corpus_root.resolve()
    manifest = corpus.load_manifest(args.manifest)
    if manifest["split"] in {"validation", "final"}:
        if args.profile != "official" or corpus_root.is_relative_to(ROOT) or args.manifest.resolve().is_relative_to(ROOT):
            raise ValueError("Holdouts require official profile and both corpus/manifest outside the candidate checkout")
    if args.profile == "official":
        if not args.acknowledge_cost or args.backend != "docker" or platform.system() != "Linux":
            raise ValueError("Official evaluation requires Linux, --backend docker, and --acknowledge-cost; see docs/OPERATOR.md")
    info = container.docker_info() if args.backend == "docker" else None
    if info and info.get("MemTotal", 0) < (profile["memory_mib"] + 1024) * 2**20:
        raise ValueError("Docker needs the configured memory limit plus at least 1 GiB headroom")
    if args.profile == "official" and "docker desktop" in info.get("OperatingSystem", "").lower():
        raise ValueError("Official evaluation needs a dedicated Linux host, not Docker Desktop")
    rows = manifest["instances"][:args.limit] if args.limit else manifest["instances"]
    if args.profile == "official" and args.limit:
        raise ValueError("Official scoring cannot truncate the registered manifest")
    for row in rows:
        input_path = corpus.safe_path(corpus_root, row["path"])
        if corpus.sha256_file(input_path) != row["sha256"]:
            raise ValueError(f"Corpus hash mismatch: {row['id']}")
    build_metadata = load_build(args.variant, args.backend)
    trusted_checker = load_build("baseline", args.backend)
    repeats = profile["repeats"]
    run_root = args.output.resolve() if args.output else ROOT / "runs" / f"{stamp()}-{args.variant}-{args.profile}-{args.backend}"
    run_root.mkdir(parents=True, exist_ok=False)
    dataset_identity = hashlib.sha256(json.dumps([(r["id"], r["sha256"], r["family"]) for r in rows], sort_keys=True).encode()).hexdigest()
    metadata = {"schema_version": 1, "variant": args.variant, "profile": args.profile, "limits": profile,
                "backend": args.backend, "baseline_commit": baseline_commit, "build": build_metadata,
                "checker_build": trusted_checker, "corpus_sha256": dataset_identity, "split": manifest["split"],
                "host": {"platform": platform.platform(), "machine": platform.machine(), "node": platform.node()},
                "docker": info, "started_at": stamp(), "instances": len(rows), "repeats": repeats,
                "official_claim": False}
    write_json(run_root / "metadata.json", metadata)
    limits = ResourceLimits(profile["solver_seconds"], profile["memory_mib"], file_size_mib=profile["file_size_mib"])
    checker_limits = ResourceLimits(profile["checker_seconds"], profile["memory_mib"], file_size_mib=profile["file_size_mib"])
    if args.backend == "native":
        solver = ["env", "SAT_EVAL_BUILD_DIR=" + str(ROOT / "build/native" / args.variant), str(ROOT / "scripts/run_solver.sh")]
        checker = [str(ROOT / "build/native/baseline/checker/dsr-trim"), "-f", "{input}", "{proof}", "/dev/null"]
        mode = "native-preview"
    else:
        common = [sys.executable, str(ROOT / "sat_eval/container.py"), "--memory-mib", str(profile["memory_mib"]),
                  "--file-size-mib", str(profile["file_size_mib"])]
        solver = [*common, "--kind", "solver", "--image", build_metadata["image_id"], "--seconds", str(profile["solver_seconds"])]
        checker = [*common, "--kind", "checker", "--image", trusted_checker["image_id"], "--seconds", str(profile["checker_seconds"]), "{input}", "{proof}"]
        mode = "linux-container" if args.profile == "official" else "docker-preview"
    print(f"{len(rows)} instances x {repeats} repeats, <= {len(rows)*repeats*profile['solver_seconds']/3600:.2f} solver hours; verification separate.", flush=True)
    results = []
    for repeat in range(repeats):
        for row in rows:
            output = run_root / f"{row['id']}-r{repeat + 1}"
            try:
                result = evaluate_instance(corpus.safe_path(corpus_root, row["path"]), solver, checker, limits, checker_limits,
                                           output_dir=output, family=row["family"], run_id=f"repeat-{repeat + 1}", measurement_mode=mode)
            finally:
                if args.backend == "docker":
                    container.cleanup(output)
            results.append(result)
            print(f"[{len(results)}/{len(rows)*repeats}] {row['id']}: {result.status} {result.solver_seconds:.3f}s", flush=True)
            write_json(run_root / "report.json", {"metadata": metadata, "summary": summarize(results), "results": [r.to_dict() for r in results]})
            if result.disqualified:
                print(f"DISQUALIFIED: {result.reason}. Stopping; see {run_root / 'report.json'}", flush=True)
                return 2
    print(json.dumps(summarize(results), indent=2))
    print(f"Report: {run_root / 'report.json'}")
    return 0


def compare(first: Path, second: Path) -> int:
    baseline, candidate = (json.loads(path.read_text()) for path in (first, second))
    for key in ("profile", "limits", "backend", "baseline_commit", "corpus_sha256", "split", "host", "docker", "repeats"):
        if baseline["metadata"][key] != candidate["metadata"][key]:
            raise ValueError(f"Reports are not a matched comparison: {key} differs")
    toolchain_keys = ("compiler", "compiler_cc", "compiler_cxx") if baseline["metadata"]["backend"] == "native" else ("toolchain_image",)
    for key in toolchain_keys:
        if baseline["metadata"]["build"].get(key) != candidate["metadata"]["build"].get(key):
            raise ValueError(f"Build toolchain differs: {key}")
    for key in ("baseline_commit", "source_sha256", "binaries", "image_id"):
        if baseline["metadata"]["checker_build"].get(key) != candidate["metadata"]["checker_build"].get(key):
            raise ValueError(f"Trusted checker differs: {key}")
    if baseline["metadata"]["variant"] != "baseline" or candidate["metadata"]["variant"] != "candidate":
        raise ValueError("Pass baseline report first and candidate report second")
    for report in (baseline, candidate):
        if report["summary"]["runs"] != report["metadata"]["instances"] * report["metadata"]["repeats"]:
            raise ValueError("Incomplete report cannot support a matched comparison")
        if not report["summary"]["eligible"]:
            raise ValueError("Disqualified/empty report cannot support a performance claim")
    base, cand = baseline["summary"], candidate["summary"]
    print(json.dumps({"baseline_par2_seconds": base["par2_seconds"], "candidate_par2_seconds": cand["par2_seconds"],
                      "par2_reduction_percent": 100 * (1 - cand["par2_seconds"] / base["par2_seconds"]),
                      "baseline_solved": base["solved_all_repeats"], "candidate_solved": cand["solved_all_repeats"],
                      "instances": base["instances"], "baseline_families": base["families"], "candidate_families": cand["families"],
                      "claim": "Development comparison only; no held-out or state-of-the-art claim."}, indent=2))
    return 0


def submit() -> int:
    baseline = guard()
    new_sources = git("ls-files", "--others", "--exclude-standard", "--", *config()["editable_paths"])
    if new_sources:
        raise ValueError("Stage new source files with git add before submission so the patch includes them: " + new_sources)
    destination = ROOT / "submissions" / stamp()
    destination.mkdir(parents=True)
    archive = destination / "solver-source.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for name in config()["editable_paths"]:
            for path in sorted((ROOT / name).rglob("*")):
                if path.is_file():
                    output.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    patch = subprocess.check_output(["git", "-C", str(ROOT), "diff", "--binary", config()["baseline_ref"], "--", *config()["editable_paths"]])
    (destination / "tracked-changes.patch").write_bytes(patch)
    write_json(destination / "submission.json", {"baseline_commit": baseline, "head_commit": git("rev-parse", "HEAD"),
                "source_sha256": source_digest(ROOT), "archive_sha256": corpus.sha256_file(archive),
                "created_at": stamp(), "note": "Source archive includes new files; tracked patch alone may not. No evaluator/corpus included."})
    print(destination)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("test")
    sub.add_parser("integrity")
    sub.add_parser("submit")
    for name in ("setup", "build"):
        command = sub.add_parser(name)
        command.add_argument("--backend", choices=("native", "docker"), default="native")
        if name == "build":
            command.add_argument("--variant", choices=("baseline", "candidate"), default="candidate")
    command = sub.add_parser("evaluate")
    command.add_argument("--variant", choices=("baseline", "candidate"), default="candidate")
    command.add_argument("--backend", choices=("native", "docker"), default="native")
    command.add_argument("--profile", choices=config()["profiles"], default="quick")
    command.add_argument("--manifest", type=Path, default=ROOT / "data/manifests/dev.json")
    command.add_argument("--corpus-root", type=Path, default=ROOT / "data/corpus")
    command.add_argument("--output", type=Path)
    command.add_argument("--limit", type=int)
    command.add_argument("--acknowledge-cost", action="store_true")
    command = sub.add_parser("compare")
    command.add_argument("baseline", type=Path)
    command.add_argument("candidate", type=Path)
    command = sub.add_parser("corpus")
    command.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    try:
        if args.command == "doctor":
            return doctor()
        if args.command == "test":
            return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        if args.command == "integrity":
            print("Frozen-file integrity passed against " + guard())
        elif args.command == "build":
            build(args.variant, args.backend)
        elif args.command == "setup":
            guard()
            if args.backend == "docker":
                try:
                    container.image_id("sat-eval-toolchain:v1")
                except subprocess.CalledProcessError:
                    subprocess.run(["docker", "build", "-f", "Dockerfile.toolchain", "-t", "sat-eval-toolchain:v1", "."], check=True)
            build("baseline", args.backend)
            build("candidate", args.backend)
            corpus.fetch(ROOT / "data/manifests/dev.json", ROOT / "data/corpus", ROOT / "data/locked/dev.json", limit=None)
            corpus.make_smoke(ROOT / "data/smoke")
            print("Ready. Setup did not run a performance experiment.")
        elif args.command == "evaluate":
            if args.limit is not None and args.limit < 1:
                raise ValueError("--limit must be positive")
            return evaluate(args)
        elif args.command == "compare":
            return compare(args.baseline, args.candidate)
        elif args.command == "corpus":
            return corpus.main(args.args)
        elif args.command == "submit":
            return submit()
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
