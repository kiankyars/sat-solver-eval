"""Independent DIMACS and SAT-model validation.

No candidate code participates in these checks. UNSAT certificates are delegated
to the separately built, pinned DSR checker by runner.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterator, TextIO


class CertificateError(ValueError):
    """The candidate's output cannot establish its claimed result."""


class FormulaError(ValueError):
    """The reference input is not supported DIMACS CNF."""


@dataclass(frozen=True)
class FormulaHeader:
    variables: int
    clauses: int


@dataclass(frozen=True)
class SolverAnswer:
    status: str
    assignment: dict[int, bool]


def _dimacs_tokens(stream: TextIO) -> Iterator[str]:
    for line in stream:
        stripped = line.strip()
        if not stripped or stripped.startswith("c"):
            continue
        yield from stripped.split()


def _integer(token: str, error_type: type[ValueError]) -> int:
    if not token or not token.removeprefix("-").isascii() or not token.removeprefix("-").isdigit():
        raise error_type(f"Expected an integer, got {token[:40]!r}")
    try:
        return int(token)
    except ValueError as exc:
        raise error_type("Integer is too long") from exc


def read_header(path: Path) -> FormulaHeader:
    with Path(path).open("r", encoding="ascii") as stream:
        tokens = _dimacs_tokens(stream)
        try:
            if next(tokens) != "p" or next(tokens) != "cnf":
                raise FormulaError("Expected one 'p cnf VARIABLES CLAUSES' header")
            variables = _integer(next(tokens), FormulaError)
            clauses = _integer(next(tokens), FormulaError)
        except StopIteration as exc:
            raise FormulaError("Missing or incomplete DIMACS header") from exc
        if variables < 0 or clauses < 0:
            raise FormulaError("DIMACS counts must be nonnegative")
        return FormulaHeader(variables, clauses)


def iter_clauses(path: Path) -> Iterator[tuple[int, ...]]:
    """Read all clauses with bounded storage apart from the longest clause."""
    with Path(path).open("r", encoding="ascii") as stream:
        tokens = _dimacs_tokens(stream)
        try:
            if next(tokens) != "p" or next(tokens) != "cnf":
                raise FormulaError("Expected one 'p cnf VARIABLES CLAUSES' header")
            variables = _integer(next(tokens), FormulaError)
            expected = _integer(next(tokens), FormulaError)
        except StopIteration as exc:
            raise FormulaError("Missing or incomplete DIMACS header") from exc
        if variables < 0 or expected < 0:
            raise FormulaError("DIMACS counts must be nonnegative")
        clause: list[int] = []
        count = 0
        for token in tokens:
            literal = _integer(token, FormulaError)
            if abs(literal) > variables:
                raise FormulaError("Literal exceeds the declared variable count")
            if literal:
                clause.append(literal)
            else:
                count += 1
                if count > expected:
                    raise FormulaError("More clauses than declared in DIMACS header")
                yield tuple(clause)
                clause.clear()
        if clause:
            raise FormulaError("Final clause is not terminated by zero")
        if count != expected:
            raise FormulaError("Clause count differs from DIMACS header")


def parse_solver_output(
    path: Path,
    variables: int,
    *,
    max_bytes: int = 256 * 1024 * 1024,
    collect_assignment: bool = True,
) -> SolverAnswer:
    """Parse exactly one competition-style status and a consistent model."""
    if Path(path).stat().st_size > max_bytes:
        raise CertificateError("Solver output exceeds the fixed parsing limit")
    status = None
    assignment: dict[int, bool] = {}
    saw_model = False
    model_terminated = False
    try:
        with Path(path).open("r", encoding="utf-8", errors="strict") as stream:
            for line in stream:
                fields = line.split()
                if not fields:
                    continue
                if fields[0] == "s":
                    if len(fields) != 2 or fields[1] not in {"SATISFIABLE", "UNSATISFIABLE", "UNKNOWN"}:
                        raise CertificateError("Malformed solver status")
                    if status is not None:
                        raise CertificateError("Multiple solver status lines")
                    status = fields[1]
                elif fields[0] == "v":
                    saw_model = True
                    if len(fields) == 1:
                        raise CertificateError("Empty model line")
                    for token in fields[1:]:
                        literal = _integer(token, CertificateError)
                        if literal == 0:
                            model_terminated = True
                            continue
                        model_terminated = False
                        if abs(literal) > variables:
                            raise CertificateError("Model literal exceeds the input variable count")
                        variable, value = abs(literal), literal > 0
                        if collect_assignment:
                            previous = assignment.get(variable)
                            if previous is not None and previous != value:
                                raise CertificateError("Model contains conflicting literals")
                            assignment[variable] = value
    except UnicodeError as exc:
        raise CertificateError("Solver output is not valid UTF-8") from exc
    if status is None:
        raise CertificateError("Missing solver status")
    if status != "SATISFIABLE" and saw_model:
        raise CertificateError("Model accompanies a non-SAT status")
    if status == "SATISFIABLE" and (not saw_model or not model_terminated):
        raise CertificateError("SAT result needs a zero-terminated model")
    return SolverAnswer(status, assignment)


def validate_sat_assignment(path: Path, assignment: dict[int, bool]) -> None:
    """Require a true assigned literal in EVERY original clause.

    Partial assignments are valid only when every possible extension satisfies
    the formula. Merely leaving an unsatisfied clause undecided is insufficient.
    """
    header = read_header(path)
    if any(type(key) is not int or key < 1 or key > header.variables for key in assignment):
        raise CertificateError("Assignment contains an invalid variable")
    if any(type(value) is not bool for value in assignment.values()):
        raise CertificateError("Assignment values must be Boolean")
    for index, clause in enumerate(iter_clauses(path), start=1):
        if not any(assignment.get(abs(literal)) == (literal > 0) for literal in clause):
            raise CertificateError(f"Model does not satisfy original clause {index}")


def checker_verified(path: Path, returncode: int | None) -> bool:
    """DSR-TRIM success is both exit zero and its explicit verification line."""
    if returncode != 0:
        return False
    with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
        return any(line.strip() == "s VERIFIED UNSAT" for line in stream)


def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "sat":
        print("Usage: check.py sat ORIGINAL.cnf SOLVER.stdout", file=sys.stderr)
        return 2
    try:
        formula, output = Path(argv[1]), Path(argv[2])
        answer = parse_solver_output(output, read_header(formula).variables)
        if answer.status != "SATISFIABLE":
            raise CertificateError("Expected SATISFIABLE")
        validate_sat_assignment(formula, answer.assignment)
    except MemoryError:
        print("c CHECKER RESOURCE EXHAUSTED", file=sys.stderr)
        return 75
    except (CertificateError, FormulaError, OSError, UnicodeError) as exc:
        print(f"Certificate rejected: {exc}", file=sys.stderr)
        return 1
    print("s VERIFIED SAT")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
