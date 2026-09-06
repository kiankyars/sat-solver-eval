"""Provenance, comparison, and source-submission regression tests."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from sat_eval import cli
from sat_eval.runner import EvaluationResult, summarize


class CliTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="sat-cli-test-")
        self.root = Path(self.scratch.name)

    def tearDown(self):
        self.scratch.cleanup()

    def file(self, relative, contents=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
        return path

    def test_source_digest_deterministic_and_sensitive_to_content(self):
        source = self.file("solver/first.c", "first")
        self.file("solver/sub/second.h", "second")
        first = cli.source_digest(self.root)
        self.assertEqual(first, cli.source_digest(self.root))
        source.write_text("changed")
        self.assertNotEqual(first, cli.source_digest(self.root))

    def test_source_digest_has_unambiguous_file_boundaries(self):
        # The old path + NUL + contents encoding allowed distinct source trees
        # to have the same digest without finding a cryptographic collision.
        source = self.file("solver/a", b"xsolver/b\x00y")
        combined = cli.source_digest(self.root)
        source.write_bytes(b"x")
        self.file("solver/b", b"y")
        self.assertNotEqual(combined, cli.source_digest(self.root))

    def test_source_digest_rejects_editable_symlinks(self):
        self.file("outside.c", "outside")
        location = self.root / "solver/kissat/src/link.c"
        location.parent.mkdir(parents=True)
        location.symlink_to(self.root / "outside.c")
        with self.assertRaises(ValueError):
            cli.source_digest(self.root)

    def test_load_build_detects_stale_source_and_modified_binary(self):
        source = self.file("solver/kissat/src/a.c", "original")
        binary = self.file("build/native/candidate/bin/kissat", b"binary")
        metadata = {"variant": "candidate", "backend": "native", "baseline_commit": "fixed",
                    "source_sha256": cli.source_digest(self.root),
                    "binaries": {"bin/kissat": cli.corpus.sha256_file(binary)}}
        self.file("build/native/candidate/build.json", json.dumps(metadata))
        with mock.patch.object(cli, "ROOT", self.root), mock.patch.object(cli, "guard", return_value="fixed"):
            self.assertEqual(cli.load_build("candidate", "native"), metadata)
            source.write_text("modified source")
            with self.assertRaisesRegex(ValueError, "source changed"):
                cli.load_build("candidate", "native")
            source.write_text("original")
            binary.write_bytes(b"modified executable")
            with self.assertRaisesRegex(ValueError, "binary changed"):
                cli.load_build("candidate", "native")

    def test_guard_freezes_build_files_even_inside_source_directory(self):
        settings = {"baseline_ref": "baseline-v1", "editable_paths": ["solver/kissat/src/", "solver/satsuma/src/"]}
        with mock.patch.object(cli, "ROOT", self.root), mock.patch.object(cli, "config", return_value=settings):
            for name in ("solver/kissat/src/makefile", "solver/satsuma/src/dejavu/CMakeLists.txt", "solver/kissat/src/injected.o", "sat_eval/check.py"):
                with self.subTest(name=name), mock.patch.object(cli, "git", side_effect=["fixed", name, "", ""]):
                    with self.assertRaisesRegex(ValueError, "Frozen experiment files changed"):
                        cli.guard()
            with mock.patch.object(cli, "git", side_effect=["fixed", "solver/kissat/src/search.c", "", ""]):
                self.assertEqual(cli.guard(), "fixed")

    def report(self, variant, *, backend="native", elapsed=1.0):
        result = EvaluationResult(instance="test.cnf", sha256="a" * 64, family="test", run_id="repeat-1",
                                  status="sat", claimed_status="SATISFIABLE", verified=True,
                                  disqualified=False, solver_seconds=elapsed, checker_seconds=0.1,
                                  par2_seconds=elapsed, timeout_seconds=10.0, solver_returncode=10,
                                  checker_returncode=0, reason="verified", measurement_mode="native-preview",
                                  output_dir="scratch")
        build = {"variant": variant, "backend": backend, "source_sha256": "base" if variant == "baseline" else "candidate",
                 "baseline_commit": "fixed", "compiler": "c++ version 1", "compiler_cc": "cc version 1",
                 "compiler_cxx": "c++ version 1",
                 "toolchain_image": "sha256:toolchain-1", "image_id": "sha256:" + variant,
                 "binaries": {"checker/dsr-trim": "checker-1"}}
        checker = {**copy.deepcopy(build), "variant": "baseline", "source_sha256": "base", "image_id": "sha256:baseline"}
        metadata = {"variant": variant, "profile": "smoke", "limits": {"solver_seconds": 10},
                    "backend": backend, "baseline_commit": "fixed", "corpus_sha256": "dataset-1", "split": "development",
                    "host": {"node": "same-host"}, "docker": None, "repeats": 1, "instances": 1,
                    "build": build, "checker_build": checker}
        return {"metadata": metadata, "summary": summarize([result]), "results": [result.to_dict()]}

    def compare(self, baseline, candidate):
        base = self.file("baseline.json", json.dumps(baseline))
        cand = self.file("candidate.json", json.dumps(candidate))
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.compare(base, cand)

    def test_matched_report_comparison(self):
        self.assertEqual(self.compare(self.report("baseline", elapsed=2.0), self.report("candidate")), 0)

    def test_report_comparison_rejects_different_compilers(self):
        baseline, candidate = self.report("baseline"), self.report("candidate")
        candidate["metadata"]["build"]["compiler"] = "different C++ compiler"
        with self.assertRaises(ValueError):
            self.compare(baseline, candidate)

    def test_report_comparison_rejects_different_docker_toolchain(self):
        baseline, candidate = self.report("baseline", backend="docker"), self.report("candidate", backend="docker")
        candidate["metadata"]["build"]["toolchain_image"] = "sha256:toolchain-2"
        with self.assertRaises(ValueError):
            self.compare(baseline, candidate)

    def test_report_comparison_rejects_different_c_compiler(self):
        baseline, candidate = self.report("baseline"), self.report("candidate")
        candidate["metadata"]["build"]["compiler_cc"] = "different C compiler"
        with self.assertRaises(ValueError):
            self.compare(baseline, candidate)

    def test_report_comparison_rejects_different_trusted_checker(self):
        baseline, candidate = self.report("baseline"), self.report("candidate")
        candidate["metadata"]["checker_build"]["binaries"]["checker/dsr-trim"] = "checker-2"
        with self.assertRaises(ValueError):
            self.compare(baseline, candidate)

    def test_submit_requires_staging_then_includes_new_sources_and_deletions(self):
        settings = {"baseline_ref": "baseline-v1", "editable_paths": ["solver/kissat/src/", "solver/satsuma/src/"]}
        config_path = self.file("config/experiment.json", json.dumps(settings))
        old = self.file("solver/kissat/src/old.c", "old\n")
        self.file("solver/satsuma/src/main.cpp", "main\n")
        self.file("README.md", "frozen\n")
        for args in (("init",), ("add", "."),
                     ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline"),
                     ("tag", "baseline-v1")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        old.unlink()
        self.file("solver/kissat/src/new.c", "new untracked source\n")
        with mock.patch.object(cli, "ROOT", self.root), mock.patch.object(cli, "SETTINGS", config_path):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "Stage new source"):
                    cli.submit()
                subprocess.run(["git", "-C", str(self.root), "add", "solver/kissat/src/new.c"],
                               check=True, capture_output=True)
                self.assertEqual(cli.submit(), 0)
        submission = next((self.root / "submissions").iterdir())
        with tarfile.open(submission / "solver-source.tar.gz") as archive:
            names = archive.getnames()
            self.assertIn("solver/kissat/src/new.c", names)
            self.assertNotIn("solver/kissat/src/old.c", names)
            self.assertNotIn("README.md", names)
        self.assertIn("deleted file mode", (submission / "tracked-changes.patch").read_text())
        self.assertIn("new file mode", (submission / "tracked-changes.patch").read_text())


if __name__ == "__main__":
    unittest.main()
