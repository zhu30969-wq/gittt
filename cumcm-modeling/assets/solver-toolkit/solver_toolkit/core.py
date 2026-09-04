"""Small, reusable solver interfaces for CUMCM optimization work.

The toolkit deliberately separates a candidate solver from independent
verification.  A paper value is never an oracle: exact enumeration, an exact
MILP reduction, or another explicitly registered mathematical source must
provide the reference value used by :func:`verify_small_instance`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import perf_counter
from typing import Callable, Iterable, Literal, Mapping, Sequence

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, differential_evolution, milp


Direction = Literal["minimize", "maximize"]
CertificateStatus = Literal["exact_match", "suboptimal", "not_reducible"]
Vector = Sequence[float] | np.ndarray
Objective = Callable[[np.ndarray, Mapping[str, float]], float]


@dataclass(frozen=True)
class ConstraintSpec:
    """One hard constraint whose violation is reported independently."""

    id: str
    evaluator: Callable[[np.ndarray, Mapping[str, float]], float]
    lower: float | None = None
    upper: float | None = None
    unit: str = "1"
    tolerance: float = 1e-8

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("constraint id must be non-empty")
        if self.lower is None and self.upper is None:
            raise ValueError(f"constraint {self.id!r} needs a lower or upper bound")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError(f"constraint {self.id!r} has lower > upper")
        if self.tolerance < 0:
            raise ValueError("constraint tolerance must be non-negative")

    def measure(
        self,
        solution: np.ndarray,
        parameters: Mapping[str, float],
    ) -> tuple[float, float]:
        value = float(self.evaluator(solution, parameters))
        if not math.isfinite(value):
            return value, math.inf
        violation = 0.0
        if self.lower is not None:
            violation = max(violation, float(self.lower) - value)
        if self.upper is not None:
            violation = max(violation, value - float(self.upper))
        return value, max(0.0, violation)


@dataclass(frozen=True)
class EnumerationOracle:
    """A finite decision set that can be traversed completely."""

    candidates: Callable[["ProblemInstance"], Iterable[Vector]]
    max_candidates: int = 100_000

    def __post_init__(self) -> None:
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")


@dataclass(frozen=True)
class MilpOracle:
    """An exact linear/integer reduction expressed in the original direction."""

    objective_coefficients: Sequence[float]
    integrality: Sequence[int]
    constraint_matrix: Sequence[Sequence[float]] = ()
    constraint_lower: Sequence[float] = ()
    constraint_upper: Sequence[float] = ()
    objective_offset: float = 0.0


@dataclass(frozen=True)
class ParameterSweep:
    """Evaluate a result under explicitly listed values of one parameter."""

    name: str
    values: Sequence[float]
    evaluator: Callable[["ProblemInstance", "SolveResult", float], float]
    unit: str = "1"


@dataclass(frozen=True)
class SeedSweep:
    """Repeat a registered solve/evaluation under a fixed seed list."""

    seeds: Sequence[int]
    evaluator: Callable[["ProblemInstance", "SolveResult", int], float]
    unit: str = "1"


@dataclass(frozen=True)
class RefinementSweep:
    """Evaluate numerical error at decreasing grid or time-step sizes."""

    levels: Sequence[float]
    error_evaluator: Callable[["ProblemInstance", "SolveResult", float], float]
    level_name: str = "step_size"
    error_unit: str = "1"
    theoretical_order: float | None = None


@dataclass(frozen=True)
class SensitivityPlan:
    parameter_sweeps: Sequence[ParameterSweep] = ()
    seed_sweep: SeedSweep | None = None
    refinement_sweep: RefinementSweep | None = None


@dataclass(frozen=True)
class ProblemInstance:
    """Solver-neutral mathematical instance with auditable callbacks."""

    name: str
    direction: Direction
    unit: str
    objective: Objective
    bounds: Sequence[tuple[float, float]]
    constraints: Sequence[ConstraintSpec]
    baseline_builder: Callable[["ProblemInstance"], Vector]
    variable_names: Sequence[str] = ()
    parameters: Mapping[str, float] = field(default_factory=dict)
    enumeration_oracle: EnumerationOracle | None = None
    milp_oracle: MilpOracle | None = None
    sensitivity_plan: SensitivityPlan | None = None
    oracle_tolerance: float = 1e-7
    bound_tolerance: float = 1e-8

    def __post_init__(self) -> None:
        if self.direction not in {"minimize", "maximize"}:
            raise ValueError("direction must be 'minimize' or 'maximize'")
        if not self.bounds:
            raise ValueError("at least one decision bound is required")
        for lower, upper in self.bounds:
            if not (math.isfinite(lower) and math.isfinite(upper) and lower <= upper):
                raise ValueError(f"invalid finite decision bound: {(lower, upper)!r}")
        if self.variable_names and len(self.variable_names) != len(self.bounds):
            raise ValueError("variable_names must be empty or match the decision size")
        if self.oracle_tolerance < 0:
            raise ValueError("oracle_tolerance must be non-negative")
        if self.bound_tolerance < 0:
            raise ValueError("bound_tolerance must be non-negative")


@dataclass(frozen=True)
class SolveConfig:
    method: Literal["differential_evolution", "enumeration", "milp"]
    seeds: Sequence[int] = (0,)
    repetitions: int | None = None
    maxiter: int = 80
    popsize: int = 10
    tolerance: float = 1e-8
    penalty_weight: float = 1e8
    polish: bool = True

    def __post_init__(self) -> None:
        if self.method not in {"differential_evolution", "enumeration", "milp"}:
            raise ValueError(f"unsupported solve method: {self.method!r}")
        if self.repetitions is not None and self.repetitions < 1:
            raise ValueError("repetitions must be positive")
        if self.maxiter < 1 or self.popsize < 1:
            raise ValueError("maxiter and popsize must be positive")
        if self.tolerance < 0 or self.penalty_weight <= 0:
            raise ValueError("tolerance must be non-negative and penalty_weight positive")


@dataclass
class SolveResult:
    """A result shaped for direct transfer into a results artifact.

    ``bound`` is a mathematical bound, not a convergence statistic.  The
    post-initialization guard always erases ``gap`` when no bound exists, even
    if a caller accidentally supplied a proxy value.
    """

    incumbent: float
    unit: str
    bound: float | None
    gap: float | None
    solver_name: str
    seeds: Sequence[int]
    repetitions: int
    wall_time: float
    raw_solution: Vector
    direction: Direction
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.incumbent = float(self.incumbent)
        self.bound = None if self.bound is None else float(self.bound)
        self.gap = None if self.bound is None else (
            None if self.gap is None else float(self.gap)
        )
        self.seeds = tuple(int(seed) for seed in self.seeds)
        self.raw_solution = tuple(float(value) for value in self.raw_solution)
        if self.repetitions < 1:
            raise ValueError("repetitions must be positive")
        if self.wall_time < 0:
            raise ValueError("wall_time must be non-negative")
        if self.gap is not None and self.gap < 0:
            raise ValueError("gap must be non-negative")

    def contract_fields(self, metric_ref: str) -> dict[str, object]:
        """Return fragments that can be merged into a ``results`` contract.

        Solver-specific fields live under the artifact's open ``extensions``
        object; ``run`` and diagnostic patches contain only keys accepted by
        the current results Schema.
        """

        solver_bounds: dict[str, object] = {}
        if self.bound is not None:
            solver_bounds = {
                "objective_incumbent": {
                    "value": self.incumbent,
                    "unit": self.unit,
                },
                "objective_bound": {"value": self.bound, "unit": self.unit},
            }
        return {
            "metric": {
                "metric_ref": metric_ref,
                "measurement": {"value": self.incumbent, "unit": self.unit},
                "sample_size": self.repetitions,
                "uncertainty": None,
            },
            "run": {
                "seeds": list(self.seeds),
                "repetitions_completed": self.repetitions,
                "environment_note": (
                    f"solver_toolkit solver={self.solver_name}; "
                    f"wall_time_seconds={self.wall_time:.9g}"
                ),
            },
            "extensions": {
                "solver_toolkit": {
                    "solver_name": self.solver_name,
                    "wall_time_seconds": self.wall_time,
                    "raw_solution": list(self.raw_solution),
                    "direction": self.direction,
                    "mathematical_bound": self.bound,
                    "relative_gap": self.gap,
                    "metadata": self.metadata,
                }
            },
            "solver_bound_evidence": solver_bounds,
        }


@dataclass(frozen=True)
class Diagnostic:
    constraint_id: str
    status: Literal["PASS", "BLOCK"]
    observed_value: float
    lower: float | None
    upper: float | None
    tolerance: float
    violation: float
    unit: str

    def as_dict(self) -> dict[str, object]:
        return {
            "constraint_id": self.constraint_id,
            "status": self.status,
            "observed_value": self.observed_value,
            "lower": self.lower,
            "upper": self.upper,
            "tolerance": self.tolerance,
            "violation": self.violation,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class Certificate:
    status: CertificateStatus
    oracle: Literal["enumeration", "milp", "none"]
    candidate_objective: float
    exact_objective: float | None
    exact_solution: tuple[float, ...] | None
    absolute_gap: float | None
    relative_gap: float | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "oracle": self.oracle,
            "candidate_objective": self.candidate_objective,
            "exact_objective": self.exact_objective,
            "exact_solution": (
                None if self.exact_solution is None else list(self.exact_solution)
            ),
            "absolute_gap": self.absolute_gap,
            "relative_gap": self.relative_gap,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SensitivityReport:
    parameter_sweeps: dict[str, list[dict[str, float | str]]]
    seed_distribution: dict[str, object] | None
    refinement: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        return {
            "parameter_sweeps": self.parameter_sweeps,
            "seed_distribution": self.seed_distribution,
            "refinement": self.refinement,
        }


class BaselineInfeasibleError(ValueError):
    """Raised when the declared baseline does not satisfy every hard constraint."""


def _solution_array(instance: ProblemInstance, values: Vector) -> np.ndarray:
    solution = np.asarray(values, dtype=float)
    if solution.ndim != 1 or solution.size != len(instance.bounds):
        raise ValueError(
            f"solution for {instance.name!r} must have {len(instance.bounds)} values"
        )
    return solution


def _objective(instance: ProblemInstance, solution: np.ndarray) -> float:
    value = float(instance.objective(solution, instance.parameters))
    if not math.isfinite(value):
        raise ValueError("objective must be finite")
    return value


def _better(direction: Direction, left: float, right: float) -> bool:
    return left < right if direction == "minimize" else left > right


def validate_solution(
    instance: ProblemInstance,
    result: SolveResult,
) -> list[Diagnostic]:
    """Back-substitute every hard constraint and expose each violation."""

    solution = _solution_array(instance, result.raw_solution)
    diagnostics: list[Diagnostic] = []
    for index, (value, (lower, upper)) in enumerate(zip(solution, instance.bounds)):
        violation = max(float(lower) - float(value), float(value) - float(upper), 0.0)
        name = instance.variable_names[index] if instance.variable_names else f"x{index}"
        diagnostics.append(
            Diagnostic(
                constraint_id=f"bound:{name}",
                status="PASS" if violation <= instance.bound_tolerance else "BLOCK",
                observed_value=float(value),
                lower=float(lower),
                upper=float(upper),
                tolerance=instance.bound_tolerance,
                violation=violation,
                unit="decision-unit",
            )
        )
    for constraint in instance.constraints:
        value, violation = constraint.measure(solution, instance.parameters)
        diagnostics.append(
            Diagnostic(
                constraint_id=constraint.id,
                status="PASS" if violation <= constraint.tolerance else "BLOCK",
                observed_value=value,
                lower=constraint.lower,
                upper=constraint.upper,
                tolerance=constraint.tolerance,
                violation=violation,
                unit=constraint.unit,
            )
        )
    return diagnostics


def _is_feasible(instance: ProblemInstance, solution: np.ndarray) -> bool:
    placeholder = SolveResult(
        incumbent=_objective(instance, solution),
        unit=instance.unit,
        bound=None,
        gap=123.0,
        solver_name="feasibility-probe",
        seeds=(),
        repetitions=1,
        wall_time=0.0,
        raw_solution=solution,
        direction=instance.direction,
    )
    return all(row.status == "PASS" for row in validate_solution(instance, placeholder))


def build_baseline(instance: ProblemInstance) -> SolveResult:
    """Build and verify the instance's real, executable baseline."""

    started = perf_counter()
    solution = _solution_array(instance, instance.baseline_builder(instance))
    result = SolveResult(
        incumbent=_objective(instance, solution),
        unit=instance.unit,
        bound=None,
        gap=None,
        solver_name="registered-baseline",
        seeds=(),
        repetitions=1,
        wall_time=perf_counter() - started,
        raw_solution=solution,
        direction=instance.direction,
    )
    failures = [row for row in validate_solution(instance, result) if row.status == "BLOCK"]
    if failures:
        detail = ", ".join(
            f"{row.constraint_id}: violation={row.violation:g} {row.unit}"
            for row in failures
        )
        raise BaselineInfeasibleError(f"registered baseline is infeasible ({detail})")
    return result


def _enumeration_optimum(
    instance: ProblemInstance,
) -> tuple[np.ndarray, float, int]:
    oracle = instance.enumeration_oracle
    if oracle is None:
        raise ValueError("enumeration oracle is not configured")
    best_solution: np.ndarray | None = None
    best_value: float | None = None
    visited = 0
    for candidate in oracle.candidates(instance):
        visited += 1
        if visited > oracle.max_candidates:
            raise ValueError(
                f"enumeration needs more than max_candidates={oracle.max_candidates}"
            )
        solution = _solution_array(instance, candidate)
        if not _is_feasible(instance, solution):
            continue
        value = _objective(instance, solution)
        if best_value is None or _better(instance.direction, value, best_value):
            best_solution, best_value = solution.copy(), value
    if best_solution is None or best_value is None:
        raise ValueError("enumeration oracle found no feasible decision")
    return best_solution, best_value, visited


def _milp_optimum(instance: ProblemInstance) -> tuple[np.ndarray, float, dict[str, object]]:
    oracle = instance.milp_oracle
    if oracle is None:
        raise ValueError("MILP oracle is not configured")
    coefficients = np.asarray(oracle.objective_coefficients, dtype=float)
    if coefficients.shape != (len(instance.bounds),):
        raise ValueError("MILP objective coefficient count does not match decision size")
    integrality = np.asarray(oracle.integrality, dtype=int)
    if integrality.shape != coefficients.shape:
        raise ValueError("MILP integrality count does not match decision size")
    sign = 1.0 if instance.direction == "minimize" else -1.0
    lower = np.asarray([row[0] for row in instance.bounds], dtype=float)
    upper = np.asarray([row[1] for row in instance.bounds], dtype=float)
    constraints: LinearConstraint | None = None
    if oracle.constraint_matrix:
        matrix = np.asarray(oracle.constraint_matrix, dtype=float)
        constraint_lower = np.asarray(oracle.constraint_lower, dtype=float)
        constraint_upper = np.asarray(oracle.constraint_upper, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != coefficients.size:
            raise ValueError("MILP constraint matrix has the wrong shape")
        if matrix.shape[0] != constraint_lower.size or matrix.shape[0] != constraint_upper.size:
            raise ValueError("MILP constraint bound count does not match matrix rows")
        constraints = LinearConstraint(matrix, constraint_lower, constraint_upper)
    solved = milp(
        c=sign * coefficients,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=constraints,
        options={"mip_rel_gap": 0.0},
    )
    if not solved.success or solved.x is None:
        raise ValueError(
            f"exact MILP oracle did not terminate optimally: {solved.message}"
        )
    solution = np.asarray(solved.x, dtype=float)
    linear_value = float(coefficients @ solution + oracle.objective_offset)
    callback_value = _objective(instance, solution)
    if not math.isclose(
        linear_value,
        callback_value,
        rel_tol=instance.oracle_tolerance,
        abs_tol=instance.oracle_tolerance,
    ):
        raise ValueError(
            "MILP objective and instance objective disagree at the oracle solution"
        )
    metadata = {
        "message": str(solved.message),
        "mip_gap": float(getattr(solved, "mip_gap", 0.0) or 0.0),
        "mip_node_count": int(getattr(solved, "mip_node_count", 0) or 0),
    }
    return solution, callback_value, metadata


def solve(instance: ProblemInstance, config: SolveConfig) -> SolveResult:
    """Run one registered solver route and return a contract-shaped result."""

    started = perf_counter()
    if config.method == "enumeration":
        solution, value, visited = _enumeration_optimum(instance)
        return SolveResult(
            incumbent=value,
            unit=instance.unit,
            bound=value,
            gap=0.0,
            solver_name="complete-enumeration",
            seeds=(),
            repetitions=1,
            wall_time=perf_counter() - started,
            raw_solution=solution,
            direction=instance.direction,
            metadata={"candidates_visited": visited},
        )
    if config.method == "milp":
        solution, value, metadata = _milp_optimum(instance)
        return SolveResult(
            incumbent=value,
            unit=instance.unit,
            bound=value,
            gap=0.0,
            solver_name="scipy.optimize.milp",
            seeds=(),
            repetitions=1,
            wall_time=perf_counter() - started,
            raw_solution=solution,
            direction=instance.direction,
            metadata=metadata,
        )

    seeds = tuple(int(seed) for seed in config.seeds)
    repetitions = config.repetitions if config.repetitions is not None else len(seeds)
    if repetitions < 1 or not seeds:
        raise ValueError("differential evolution needs a non-empty fixed seed list")
    if len(seeds) != repetitions:
        raise ValueError("seeds and repetitions must have the same length")
    sign = 1.0 if instance.direction == "minimize" else -1.0

    def penalized(solution: np.ndarray) -> float:
        raw = sign * _objective(instance, solution)
        violation = sum(
            constraint.measure(solution, instance.parameters)[1] ** 2
            for constraint in instance.constraints
        )
        return raw + config.penalty_weight * violation

    candidates: list[tuple[float, np.ndarray, int | None]] = []
    try:
        baseline = build_baseline(instance)
        candidates.append(
            (baseline.incumbent, np.asarray(baseline.raw_solution), None)
        )
    except BaselineInfeasibleError:
        pass
    run_metadata: list[dict[str, object]] = []
    for seed in seeds:
        solved = differential_evolution(
            penalized,
            list(instance.bounds),
            seed=seed,
            maxiter=config.maxiter,
            popsize=config.popsize,
            tol=config.tolerance,
            polish=config.polish,
            updating="immediate",
            workers=1,
        )
        solution = np.asarray(solved.x, dtype=float)
        feasible = _is_feasible(instance, solution)
        value = _objective(instance, solution)
        run_metadata.append(
            {
                "seed": seed,
                "objective": value,
                "feasible": feasible,
                "success": bool(solved.success),
                "message": str(solved.message),
                "evaluations": int(solved.nfev),
            }
        )
        if feasible:
            candidates.append((value, solution, seed))
    if not candidates:
        raise RuntimeError("solver produced no feasible candidate and baseline was infeasible")
    best_value, best_solution, selected_seed = candidates[0]
    for value, solution, seed in candidates[1:]:
        if _better(instance.direction, value, best_value):
            best_value, best_solution, selected_seed = value, solution, seed
    return SolveResult(
        incumbent=best_value,
        unit=instance.unit,
        bound=None,
        gap=None,
        solver_name="scipy.optimize.differential_evolution",
        seeds=seeds,
        repetitions=repetitions,
        wall_time=perf_counter() - started,
        raw_solution=best_solution,
        direction=instance.direction,
        metadata={"selected_seed": selected_seed, "runs": run_metadata},
    )


def verify_small_instance(
    instance: ProblemInstance,
    result: SolveResult,
) -> Certificate:
    """Compare a main result with a complete enumeration or exact MILP oracle."""

    candidate_solution = _solution_array(instance, result.raw_solution)
    candidate_objective = _objective(instance, candidate_solution)
    if not math.isclose(
        candidate_objective,
        result.incumbent,
        rel_tol=instance.oracle_tolerance,
        abs_tol=instance.oracle_tolerance,
    ):
        return Certificate(
            status="not_reducible",
            oracle="none",
            candidate_objective=result.incumbent,
            exact_objective=None,
            exact_solution=None,
            absolute_gap=None,
            relative_gap=None,
            reason="candidate incumbent does not match its raw solution",
        )
    if not _is_feasible(instance, candidate_solution):
        return Certificate(
            status="not_reducible",
            oracle="none",
            candidate_objective=candidate_objective,
            exact_objective=None,
            exact_solution=None,
            absolute_gap=None,
            relative_gap=None,
            reason="candidate is infeasible, so an optimality comparison is not meaningful",
        )

    oracle_name: Literal["enumeration", "milp", "none"]
    try:
        if instance.enumeration_oracle is not None:
            exact_solution, exact_objective, _visited = _enumeration_optimum(instance)
            oracle_name = "enumeration"
        elif instance.milp_oracle is not None:
            exact_solution, exact_objective, _metadata = _milp_optimum(instance)
            oracle_name = "milp"
        else:
            return Certificate(
                status="not_reducible",
                oracle="none",
                candidate_objective=candidate_objective,
                exact_objective=None,
                exact_solution=None,
                absolute_gap=None,
                relative_gap=None,
                reason="no finite enumeration or exact MILP reduction is registered",
            )
    except (ValueError, RuntimeError) as exc:
        return Certificate(
            status="not_reducible",
            oracle="none",
            candidate_objective=candidate_objective,
            exact_objective=None,
            exact_solution=None,
            absolute_gap=None,
            relative_gap=None,
            reason=str(exc),
        )

    directed_gap = (
        exact_objective - candidate_objective
        if instance.direction == "maximize"
        else candidate_objective - exact_objective
    )
    tolerance = instance.oracle_tolerance
    if directed_gap < -tolerance:
        return Certificate(
            status="not_reducible",
            oracle=oracle_name,
            candidate_objective=candidate_objective,
            exact_objective=exact_objective,
            exact_solution=tuple(float(value) for value in exact_solution),
            absolute_gap=None,
            relative_gap=None,
            reason="candidate is better than the registered exact oracle; reduction is inconsistent",
        )
    absolute_gap = max(0.0, directed_gap)
    relative_gap = absolute_gap / max(abs(exact_objective), tolerance or 1e-15)
    status: CertificateStatus = (
        "exact_match" if absolute_gap <= tolerance else "suboptimal"
    )
    reason = (
        "candidate matches the exact small-instance optimum"
        if status == "exact_match"
        else "candidate is feasible but trails the exact small-instance optimum"
    )
    return Certificate(
        status=status,
        oracle=oracle_name,
        candidate_objective=candidate_objective,
        exact_objective=exact_objective,
        exact_solution=tuple(float(value) for value in exact_solution),
        absolute_gap=absolute_gap,
        relative_gap=relative_gap,
        reason=reason,
    )


def run_sensitivity(
    instance: ProblemInstance,
    result: SolveResult,
) -> SensitivityReport:
    """Run preregistered parameter, seed, and discretization probes."""

    plan = instance.sensitivity_plan
    if plan is None:
        return SensitivityReport({}, None, None)
    parameter_rows: dict[str, list[dict[str, float | str]]] = {}
    for sweep in plan.parameter_sweeps:
        parameter_rows[sweep.name] = [
            {
                "parameter_value": float(value),
                "observed": float(sweep.evaluator(instance, result, float(value))),
                "unit": sweep.unit,
            }
            for value in sweep.values
        ]

    seed_distribution: dict[str, object] | None = None
    if plan.seed_sweep is not None:
        values = [
            float(plan.seed_sweep.evaluator(instance, result, int(seed)))
            for seed in plan.seed_sweep.seeds
        ]
        seed_distribution = {
            "seeds": [int(seed) for seed in plan.seed_sweep.seeds],
            "values": values,
            "unit": plan.seed_sweep.unit,
            "mean": float(np.mean(values)) if values else None,
            "sample_standard_deviation": (
                float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
            ),
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
        }

    refinement: dict[str, object] | None = None
    if plan.refinement_sweep is not None:
        levels = [float(value) for value in plan.refinement_sweep.levels]
        errors = [
            float(plan.refinement_sweep.error_evaluator(instance, result, level))
            for level in levels
        ]
        if any(level <= 0 for level in levels) or any(error < 0 for error in errors):
            raise ValueError("refinement levels must be positive and errors non-negative")
        measured_orders: list[float | None] = []
        for index in range(1, len(levels)):
            previous_error, error = errors[index - 1], errors[index]
            ratio = levels[index - 1] / levels[index]
            if previous_error <= 0 or error <= 0 or ratio <= 0 or math.isclose(ratio, 1.0):
                measured_orders.append(None)
            else:
                measured_orders.append(math.log(previous_error / error) / math.log(ratio))
        refinement = {
            "level_name": plan.refinement_sweep.level_name,
            "levels": levels,
            "errors": errors,
            "error_unit": plan.refinement_sweep.error_unit,
            "measured_orders": measured_orders,
            "theoretical_order": plan.refinement_sweep.theoretical_order,
        }
    return SensitivityReport(parameter_rows, seed_distribution, refinement)
