from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sat_eval import container


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input.cnf"
        self.input.write_text("p cnf 1 1\n1 0\n")
        self.scratch = self.root / "run" / "candidate"
        self.scratch.mkdir(parents=True)
        self.proof = self.scratch / "certificate.dsr"

    def invoke(self, kind, *, seconds="10", memory="1024"):
        return container.main([
            "--kind", kind, "--image", "sha256:immutable-image",
            "--seconds", seconds, "--memory-mib", memory,
            "--file-size-mib", "1024", str(self.input), str(self.proof),
        ])

    @patch("sat_eval.container.cleanup")
    @patch("sat_eval.container.subprocess.call", return_value=10)
    def test_solver_has_only_input_and_scratch_mounts(self, call, cleanup):
        self.assertEqual(self.invoke("solver"), 10)
        command = call.call_args.args[0]
        mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
        self.assertEqual(mounts, [
            f"type=bind,src={self.input.resolve()},dst=/input.cnf,readonly",
            f"type=bind,src={self.scratch.resolve()},dst=/output",
        ])
        self.assertIn("TMPDIR=/tmp", command)
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--log-driver") + 1], "none")
        self.assertEqual(command[command.index("--network") + 1], "none")
        cid = Path(command[command.index("--cidfile") + 1])
        self.assertEqual(cid.parent, self.scratch.parent.resolve())
        self.assertNotEqual(cid.parent, self.scratch.resolve())
        cleanup.assert_called_once_with(self.scratch.parent.resolve())
        self.assertEqual(command[command.index("--name") + 1], container.container_name(self.scratch.parent, "solver"))

    @patch("sat_eval.container.cleanup")
    @patch("sat_eval.container.subprocess.call", return_value=0)
    def test_checker_mounts_original_and_proof_readonly(self, call, cleanup):
        self.proof.touch()
        self.assertEqual(self.invoke("checker"), 0)
        command = call.call_args.args[0]
        mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
        self.assertTrue(all(value.endswith(",readonly") for value in mounts))
        self.assertIn("/opt/sat/build/checker/dsr-trim", command)
        self.assertNotIn(f"type=bind,src={self.scratch},dst=/output", command)

    def test_checker_rejects_symlink_proof(self):
        self.proof.symlink_to(self.input)
        with self.assertRaises(ValueError):
            self.invoke("checker")

    def test_reject_invalid_resource_limits(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.invoke("solver", seconds=value)
        with self.assertRaises(ValueError):
            self.invoke("solver", memory="0")

    @patch("sat_eval.container.subprocess.run")
    def test_cleanup_uses_only_valid_container_ids(self, run):
        (self.scratch.parent / "solver.cid").write_text("a" * 64)
        (self.scratch.parent / "checker.cid").write_text("--dangerous-argument")
        container.cleanup(self.scratch.parent)
        self.assertEqual(run.call_args_list[0].args[0], ["docker", "rm", "-f", "a" * 64])
        self.assertEqual(run.call_args_list[1].args[0], ["docker", "rm", "-f", container.container_name(self.scratch.parent, "checker")])
        self.assertEqual(run.call_count, 2)

    @patch("sat_eval.container.subprocess.run")
    def test_cleanup_without_cid_uses_scoped_names(self, run):
        container.cleanup(self.scratch.parent)
        names = [call.args[0][-1] for call in run.call_args_list]
        self.assertEqual(names, [container.container_name(self.scratch.parent, kind) for kind in ("solver", "checker")])


if __name__ == "__main__":
    unittest.main()
