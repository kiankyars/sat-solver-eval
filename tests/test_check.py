import tempfile
import unittest
from pathlib import Path

from sat_eval.check import (
    CertificateError,
    FormulaError,
    checker_verified,
    iter_clauses,
    parse_solver_output,
    read_header,
    validate_sat_assignment,
)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def file(self, name, text):
        path = self.root / name
        path.write_text(text)
        return path

    def test_multiline_dimacs_comments_and_partial_model(self):
        formula = self.file("input.cnf", "c test\np cnf 3 2\n1\nc between lines\n2 0 -2 3 0\n")
        self.assertEqual(read_header(formula).variables, 3)
        self.assertEqual(list(iter_clauses(formula)), [(1, 2), (-2, 3)])
        validate_sat_assignment(formula, {1: True, 3: True})

    def test_unassigned_clause_is_not_satisfied(self):
        formula = self.file("input.cnf", "p cnf 2 1\n-2 0\n")
        with self.assertRaises(CertificateError):
            validate_sat_assignment(formula, {1: True})

    def test_empty_clause_is_unsatisfied(self):
        formula = self.file("input.cnf", "p cnf 0 1\n0\n")
        with self.assertRaises(CertificateError):
            validate_sat_assignment(formula, {})

    def test_empty_formula_is_satisfied(self):
        formula = self.file("input.cnf", "p cnf 0 0\n")
        validate_sat_assignment(formula, {})

    def test_reject_bad_dimacs(self):
        for text in (
            "p cnf 1 1\n1", "p cnf 1 1\n2 0", "p cnf 1 2\n1 0",
            "p cnf 1 0\n1 0", "p cnf -1 0\n", "p cnf 1 1\np cnf 1 0\n",
            "p dnf 1 1\n1 0", "p cnf 1 1\nx 0", "p cnf 1",
        ):
            with self.subTest(text=text), self.assertRaises(FormulaError):
                list(iter_clauses(self.file("bad.cnf", text)))

    def test_parse_model(self):
        output = self.file("stdout", "c SAT solver 1\ns SATISFIABLE\nv 1 -2\nv 3 0\n")
        answer = parse_solver_output(output, 3)
        self.assertEqual(answer.assignment, {1: True, 2: False, 3: True})

    def test_reject_bad_output(self):
        for text in (
            "s SATISFIABLE\nv 1 -1 0\n",
            "s SATISFIABLE\nv 4 0\n",
            "s SATISFIABLE\nv 1\n",
            "s SATISFIABLE\n",
            "s SATISFIABLE\nv 1 0\ns UNSATISFIABLE\n",
            "s SATISFIABLE\ns SATISFIABLE\nv 1 0\n",
            "s UNSATISFIABLE\nv 1 0\n",
            "s UNKNOWN\nv 0\n",
            "s SATISFIABLE extra\nv 1 0\n",
            "s SATISFIABLE\nv __import__('os') 0\n",
            "s SATISFIABLE\nv True 0\n",
            "SATISFIABLE\nv 1 0\n",
        ):
            with self.subTest(text=text), self.assertRaises(CertificateError):
                parse_solver_output(self.file("bad.stdout", text), 3)

    def test_assignment_range_and_type(self):
        formula = self.file("input.cnf", "p cnf 1 1\n1 0\n")
        for assignment in ({0: True}, {2: True}, {1: 1}, {True: True}):
            with self.subTest(assignment=assignment), self.assertRaises(CertificateError):
                validate_sat_assignment(formula, assignment)

    def test_checker_requires_exit_and_exact_success_line(self):
        output = self.file("checker.stdout", "c success\ns VERIFIED UNSAT\n")
        self.assertTrue(checker_verified(output, 0))
        self.assertFalse(checker_verified(output, 1))
        self.assertFalse(checker_verified(output, -9))
        output.write_text("c s VERIFIED UNSAT\n")
        self.assertFalse(checker_verified(output, 0))


if __name__ == "__main__":
    unittest.main()
