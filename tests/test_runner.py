import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from sat_eval.runner import ResourceLimits, evaluate_instance, summarize


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.formula = self.root / "input.cnf"
        self.formula.write_text("p cnf 2 2\n1 0\n-2 0\n")
        self.limits = ResourceLimits(wall_seconds=3, memory_mib=1024)
        self.checker = [sys.executable, "-c", "print('s VERIFIED UNSAT')"]
        self.counter = 0

    def evaluate(self, source, *, checker=None, limits=None, checker_limits=None, measurement_mode="native-preview"):
        self.counter += 1
        return evaluate_instance(
            self.formula,
            [sys.executable, "-c", "import sys, pathlib; " + source],
            checker or self.checker,
            limits or self.limits,
            checker_limits or self.limits,
            output_dir=self.root / f"result-{self.counter}",
            family="unit",
            run_id=str(self.counter),
            measurement_mode=measurement_mode,
        )

    def test_verified_sat_is_scored(self):
        result = self.evaluate("print('s SATISFIABLE\\nv 1 -2 0'); sys.exit(10)")
        self.assertEqual(result.status, "sat")
        self.assertTrue(result.verified)
        self.assertGreater(result.checker_seconds, 0)
        self.assertEqual(result.par2_seconds, result.solver_seconds)
        self.assertFalse(result.disqualified)
        json.dumps(result.to_dict())

    def test_wrong_model_disqualifies(self):
        result = self.evaluate("print('s SATISFIABLE\\nv 1 2 0'); sys.exit(10)")
        self.assertEqual(result.status, "invalid")
        self.assertTrue(result.disqualified)
        self.assertEqual(result.par2_seconds, 6)

    def test_partial_model_must_satisfy_all_clauses(self):
        result = self.evaluate("print('s SATISFIABLE\\nv 1 0'); sys.exit(10)")
        self.assertTrue(result.disqualified)

    def test_returncode_and_status_must_agree(self):
        result = self.evaluate("print('s SATISFIABLE\\nv 1 -2 0'); sys.exit(20)")
        self.assertTrue(result.disqualified)
        self.assertIn("Exit code", result.reason)

    def test_unknown_and_crashes_are_penalized(self):
        unknown = self.evaluate("print('s UNKNOWN')")
        crash = self.evaluate("sys.exit(1)")
        for result in (unknown, crash):
            self.assertFalse(result.disqualified)
            self.assertFalse(result.verified)
            self.assertEqual(result.par2_seconds, 6)

    def test_timeout_is_unsolved(self):
        result = self.evaluate("import time; time.sleep(4)", limits=ResourceLimits(0.1, 1024))
        self.assertEqual(result.status, "timeout")
        self.assertFalse(result.disqualified)
        self.assertEqual(result.par2_seconds, 0.2)

    @unittest.skipUnless(hasattr(__import__("os"), "fork"), "Requires POSIX fork")
    def test_descendant_is_killed_when_parent_exits(self):
        marker = self.root / "orphan-survived"
        code = (
            "import os, time\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            " time.sleep(0.3)\n"
            f" pathlib.Path({str(marker)!r}).touch()\n"
            " sys.exit(0)\n"
            "print('s SATISFIABLE\\nv 1 -2 0')\n"
            "sys.exit(10)"
        )
        result = self.evaluate(code)
        self.assertTrue(result.verified)
        time.sleep(0.35)
        self.assertFalse(marker.exists())

    def test_replaced_stdout_symlink_disqualifies(self):
        result = self.evaluate(
            "output = pathlib.Path.cwd() / 'solver.stdout'; output.unlink(); output.symlink_to(sys.argv[1]); sys.exit(10)"
        )
        self.assertTrue(result.disqualified)
        self.assertIn("regular file", result.reason)

    def test_unsat_needs_proof(self):
        result = self.evaluate("print('s UNSATISFIABLE'); sys.exit(20)")
        self.assertTrue(result.disqualified)
        self.assertIn("no certificate", result.reason)

    def test_unsat_delegates_to_frozen_checker(self):
        result = self.evaluate("pathlib.Path(sys.argv[2]).write_bytes(b'proof'); print('s UNSATISFIABLE'); sys.exit(20)")
        self.assertEqual(result.status, "unsat")
        self.assertTrue(result.verified)
        self.assertTrue((Path(result.output_dir) / "candidate/certificate.dsr").is_file())
        self.assertTrue((Path(result.output_dir) / "checker.stdout").is_file())

    def test_checker_exit_zero_without_success_is_invalid(self):
        result = self.evaluate(
            "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
            checker=[sys.executable, "-c", "print('c incomplete')"],
        )
        self.assertTrue(result.disqualified)

    def test_checker_wrong_exit_with_success_is_invalid(self):
        result = self.evaluate(
            "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
            checker=[sys.executable, "-c", "import sys; print('s VERIFIED UNSAT'); sys.exit(1)"],
        )
        self.assertTrue(result.disqualified)

    def test_checker_timeout_is_unsolved_not_incorrect(self):
        result = self.evaluate(
            "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
            checker=[sys.executable, "-c", "import time; time.sleep(4)"],
            checker_limits=ResourceLimits(0.1, 1024),
        )
        self.assertEqual(result.status, "checker_timeout")
        self.assertFalse(result.disqualified)

    def test_checker_signal_is_unsolved(self):
        result = self.evaluate(
            "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
            checker=[sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"],
        )
        self.assertEqual(result.status, "checker_resource")
        self.assertFalse(result.disqualified)

    def test_checker_explicit_allocation_failure_is_unsolved(self):
        result = self.evaluate(
            "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
            checker=[sys.executable, "-c", "import sys; print('Ran out of memory on xmalloc()', file=sys.stderr); sys.exit(255)"],
        )
        self.assertEqual(result.status, "checker_resource")
        self.assertFalse(result.disqualified)

    def test_docker_checker_resource_and_timeout_are_unsolved(self):
        for code, expected in ((124, "checker_timeout"), (137, "checker_resource")):
            with self.subTest(code=code):
                result = self.evaluate(
                    "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
                    checker=[sys.executable, "-c", f"import sys; sys.exit({code})"],
                    measurement_mode="docker-preview",
                )
                self.assertEqual(result.status, expected)
                self.assertFalse(result.disqualified)
                self.assertEqual(result.par2_seconds, 6)

    def test_docker_checker_infrastructure_error_is_not_bad_proof(self):
        for code in (125, 126, 127):
            with self.subTest(code=code), self.assertRaises(RuntimeError):
                self.evaluate(
                    "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
                    checker=[sys.executable, "-c", f"import sys; sys.exit({code})"],
                    measurement_mode="docker-preview",
                )

    def test_docker_solver_infrastructure_error_aborts_evaluation(self):
        with self.assertRaises(RuntimeError):
            self.evaluate("sys.exit(125)", measurement_mode="docker-preview")

    def test_checker_command_placeholder_expansion(self):
        result = self.evaluate(
            "pathlib.Path(sys.argv[2]).touch(); print('s UNSATISFIABLE'); sys.exit(20)",
            checker=[sys.executable, "-c", "import sys, pathlib; assert pathlib.Path(sys.argv[1]).read_text().startswith('p cnf'); assert pathlib.Path(sys.argv[2]).is_file(); print('s VERIFIED UNSAT')", "{input}", "{proof}"],
        )
        self.assertTrue(result.verified)

    def test_reference_mutation_disqualifies(self):
        result = self.evaluate("pathlib.Path(sys.argv[1]).write_text('p cnf 0 0\\n'); print('s SATISFIABLE\\nv 0'); sys.exit(10)")
        self.assertTrue(result.disqualified)
        self.assertIn("changed", result.reason)

    def test_symlink_certificate_disqualifies(self):
        result = self.evaluate("pathlib.Path(sys.argv[2]).symlink_to(sys.argv[1]); print('s UNSATISFIABLE'); sys.exit(20)")
        self.assertTrue(result.disqualified)
        self.assertIn("regular file", result.reason)

    def test_summary_disqualifies_whole_submission(self):
        valid = self.evaluate("print('s SATISFIABLE\\nv 1 -2 0'); sys.exit(10)")
        invalid = self.evaluate("print('s SATISFIABLE\\nv -1 2 0'); sys.exit(10)")
        summary = summarize([valid, invalid])
        self.assertTrue(summary["disqualified"])
        self.assertFalse(summary["eligible"])
        self.assertIsNone(summary["par2_seconds"])
        self.assertEqual(summary["solved_all_repeats"], 0)
        self.assertEqual(summary["instances"], 1)

    def test_summary_uses_per_instance_repeat_mean(self):
        from dataclasses import replace
        base = self.evaluate("print('s SATISFIABLE\\nv 1 -2 0'); sys.exit(10)")
        rows = [replace(base, par2_seconds=value, run_id=str(index)) for index, value in enumerate((1, 2, 100))]
        summary = summarize(rows)
        self.assertEqual(summary["par2_seconds"], 103 / 3)
        self.assertEqual(summary["families"]["unit"]["par2_seconds"], 103 / 3)
        self.assertEqual(summary["diagnostic_mean_of_repeat_medians_seconds"], 2)

    def test_timeout_penalty_is_not_hidden_by_other_repeats(self):
        from dataclasses import replace
        base = self.evaluate("print('s SATISFIABLE\\nv 1 -2 0'); sys.exit(10)")
        fast = replace(base, par2_seconds=1)
        timeout = replace(base, par2_seconds=6, verified=False, status="timeout")
        summary = summarize([fast, fast, timeout])
        self.assertEqual(summary["par2_seconds"], 8 / 3)
        self.assertEqual(summary["solved_all_repeats"], 0)


if __name__ == "__main__":
    unittest.main()
