"""Opt-in native/full-pipeline certificate tests.

Run: SAT_EVAL_INTEGRATION=1 python3 -m unittest discover -s tests -p '*integration.py'
These use a deliberately separate tiny exhaustive SAT oracle and model checker.
They are correctness tests, never a performance score or a hidden benchmark.
"""
from __future__ import annotations

import itertools
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("SAT_EVAL_BUILD_DIR", ROOT / "build")).resolve()


@unittest.skipUnless(os.environ.get("SAT_EVAL_INTEGRATION") == "1",
                     "set SAT_EVAL_INTEGRATION=1 after building solver")
class SolverIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in (BUILD / "bin/kissat", BUILD / "bin/satsuma",
                     BUILD / "checker/dsr-trim"):
            if not tool.is_file():
                raise RuntimeError(f"Build the solver first; missing {tool}")

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="sat-integration-")
        self.work = Path(self.scratch.name)
        self.env = dict(os.environ, SAT_EVAL_BUILD_DIR=str(BUILD))

    def tearDown(self):
        self.scratch.cleanup()

    def write_cnf(self, name, variables, clauses):
        path = self.work / name
        body = "".join(" ".join(map(str, clause)) + " 0\n" for clause in clauses)
        path.write_text(f"p cnf {variables} {len(clauses)}\n" + body)
        return path

    @staticmethod
    def satisfiable_by_exhaustion(variables, clauses):
        return any(
            all(any(assignment[abs(lit) - 1] == (lit > 0) for lit in clause)
                for clause in clauses)
            for assignment in itertools.product((False, True), repeat=variables)
        )

    def run_solver(self, cnf, proof):
        return subprocess.run(
            [str(ROOT / "scripts/run_solver.sh"), str(cnf), str(proof)],
            env=self.env, capture_output=True, text=True, timeout=15,
        )

    def proof_accepted(self, cnf, proof):
        checked = subprocess.run(
            [str(BUILD / "checker/dsr-trim"), "-f", str(cnf), str(proof), "/dev/null"],
            capture_output=True, text=True, timeout=15,
        )
        return checked.returncode == 0 and "s VERIFIED UNSAT" in checked.stdout.splitlines()

    def assert_formula_correct(self, variables, clauses):
        cnf = self.write_cnf("input.cnf", variables, clauses)
        proof = self.work / "output.proof"
        # The wrapper intentionally does not overwrite an existing proof.
        if proof.exists():
            proof.unlink()
        run = self.run_solver(cnf, proof)
        expected_sat = self.satisfiable_by_exhaustion(variables, clauses)
        self.assertEqual(run.returncode, 10 if expected_sat else 20,
                         (run.stdout, run.stderr, clauses))
        if expected_sat:
            self.assertIn("s SATISFIABLE", run.stdout.splitlines())
            model = {int(lit) for line in run.stdout.splitlines() if line.startswith("v ")
                     for lit in line.split()[1:] if int(lit) != 0}
            self.assertFalse(any(-lit in model for lit in model), "contradictory model")
            self.assertTrue(all(any(lit in model for lit in clause) for clause in clauses),
                            ("model does not satisfy ORIGINAL formula", run.stdout, clauses))
        else:
            self.assertIn("s UNSATISFIABLE", run.stdout.splitlines())
            self.assertTrue(self.proof_accepted(cnf, proof),
                            ("proof does not verify against ORIGINAL formula", run.stdout))

    def test_tiny_formulas_against_exhaustive_oracle(self):
        fixtures = [
            (0, []),
            (1, []),
            (1, [[]]),
            (1, [[1]]),
            (1, [[1], [-1]]),
            (3, [[1, -1, 2], [-2, 3], [-3]]),
            (2, [[1, 2], [1, -2], [-1, 2], [-1, -2]]),
            (6, [[1, 2], [3, 4], [5, 6], [-1, -3], [-1, -5], [-3, -5],
                 [-2, -4], [-2, -6], [-4, -6]]),  # three pigeons, two holes
        ]
        for variables, clauses in fixtures:
            with self.subTest(variables=variables, clauses=clauses):
                self.assert_formula_correct(variables, clauses)

    def test_seeded_random_formulas_against_exhaustive_oracle(self):
        rng = random.Random(0x5A7C0DE)
        for index in range(32):
            variables = rng.randint(2, 8)
            clauses = [
                [v if rng.getrandbits(1) else -v
                 for v in rng.sample(range(1, variables + 1), rng.randint(1, min(3, variables)))]
                for _ in range(rng.randint(1, 5 * variables))
            ]
            with self.subTest(index=index):
                self.assert_formula_correct(variables, clauses)

    def test_wrong_formula_and_corrupted_certificates_rejected(self):
        unsat = self.write_cnf("unsat.cnf", 2, [[1, 2], [1, -2], [-1, 2], [-1, -2]])
        sat = self.write_cnf("sat.cnf", 2, [[1, 2]])
        proof = self.work / "valid.proof"
        run = self.run_solver(unsat, proof)
        self.assertEqual(run.returncode, 20)
        self.assertTrue(self.proof_accepted(unsat, proof))
        self.assertFalse(self.proof_accepted(sat, proof), "proof accepted for wrong SAT formula")
        # A byte-truncated proof is not guaranteed to be invalid: the complete
        # contradiction can occur before trailing deletion/terminator bytes.
        # Instead forge an unsupported empty-clause addition on a formula
        # with no unit clauses, so the required derivation is genuinely absent.
        for name, contents in (("empty.proof", b""),
                               ("forged-empty-clause.proof", b"a\x00"),
                               ("garbage.proof", b"not a proof\n")):
            corrupted = self.work / name
            corrupted.write_bytes(contents)
            with self.subTest(corruption=name):
                self.assertFalse(self.proof_accepted(unsat, corrupted))


if __name__ == "__main__":
    unittest.main()
