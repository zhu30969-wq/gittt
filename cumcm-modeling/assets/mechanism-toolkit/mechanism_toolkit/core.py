"""Reusable ODE, heat-transfer, identification, and uncertainty tools for CUMCM.

The public result objects enforce two evidence boundaries.  A numerical value
is not labelled converged without a valid three-level Richardson certificate,
and a parameter point estimate is erased whenever the local sensitivity
Jacobian indicates non-identifiability.  Callers therefore cannot accidentally
turn a finest-grid value or one arbitrary member of an equivalent parameter
family into a stronger conclusion by merely forgetting a later check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping, Sequence

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares
from scipy.stats import norm, qmc


OdeMethod = Literal["RK45", "DOP853", "Radau", "BDF", "LSODA"]
ConvergenceStatus = Literal[
    "converged", "order_mismatch", "insufficient_levels", "not_attempted"
]
SamplingMethod = Literal["monte_carlo", "sobol", "halton"]
OracleStatus = Literal["exact_match", "within_tolerance", "mismatch"]
System = Callable[[float, np.ndarray, Mapping[str, float]], Sequence[float] | np.ndarray]
ParameterModel = Callable[[Mapping[str, float], np.ndarray], Sequence[float] | np.ndarray]
UncertaintyModel = Callable[[Mapping[str, np.ndarray]], Sequence[float] | np.ndarray]
ScalarField = Callable[[float, float], float]


@dataclass(frozen=True)
class OdeEvent:
    """Named solve_ivp event with explicit terminal and direction semantics."""

    name: str
    function: System
    terminal: bool = False
    direction: float = 0.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("event name must be non-empty")
        if self.direction not in {-1.0, 0.0, 1.0}:
            raise ValueError("event direction must be -1, 0, or 1")


@dataclass(frozen=True)
class OdeConfig:
    """Explicit integration controls; methods are never switched silently."""

    t_span: tuple[float, float]
    y0: Sequence[float]
    method: OdeMethod = "DOP853"
    rtol: float = 1e-8
    atol: float | Sequence[float] = 1e-10
    t_eval: Sequence[float] | None = None
    max_step: float = math.inf
    parameters: Mapping[str, float] = field(default_factory=dict)
    events: Sequence[OdeEvent] = ()
    stiffness_step_threshold: int = 10_000

    def __post_init__(self) -> None:
        if self.method not in {"RK45", "DOP853", "Radau", "BDF", "LSODA"}:
            raise ValueError(f"unsupported ODE method: {self.method!r}")
        start, finish = map(float, self.t_span)
        if not (math.isfinite(start) and math.isfinite(finish) and finish > start):
            raise ValueError("t_span must contain finite increasing endpoints")
        if np.asarray(self.y0).size == 0:
            raise ValueError("y0 must contain at least one state")
        if self.rtol <= 0 or np.any(np.asarray(self.atol, dtype=float) <= 0):
            raise ValueError("rtol and atol must be positive")
        if self.max_step <= 0:
            raise ValueError("max_step must be positive")
        if self.stiffness_step_threshold < 1:
            raise ValueError("stiffness_step_threshold must be positive")


@dataclass(frozen=True)
class OdeResult:
    t: np.ndarray
    y: np.ndarray
    method: OdeMethod
    success: bool
    message: str
    step_count: int
    nfev: int
    njev: int
    nlu: int
    triggered_events: tuple[str, ...]
    event_times: Mapping[str, tuple[float, ...]]
    stiffness_suspected: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "success": self.success,
            "message": self.message,
            "step_count": self.step_count,
            "nfev": self.nfev,
            "njev": self.njev,
            "nlu": self.nlu,
            "triggered_events": list(self.triggered_events),
            "event_times": {key: list(value) for key, value in self.event_times.items()},
            "stiffness_suspected": self.stiffness_suspected,
        }


def integrate_ode(system: System, config: OdeConfig) -> OdeResult:
    """Integrate one initial-value problem without silently changing methods."""

    parameters = dict(config.parameters)

    def rhs(time: float, state: np.ndarray) -> np.ndarray:
        value = np.asarray(system(time, state, parameters), dtype=float)
        if value.shape != state.shape:
            raise ValueError(
                f"system returned shape {value.shape}; expected state shape {state.shape}"
            )
        return value

    wrapped_events: list[Callable[[float, np.ndarray], float]] = []
    for specification in config.events:
        def event(
            time: float,
            state: np.ndarray,
            event_specification: OdeEvent = specification,
        ) -> float:
            value = np.asarray(
                event_specification.function(time, state, parameters), dtype=float
            )
            if value.size != 1:
                raise ValueError("an ODE event must return one scalar")
            return float(value.reshape(-1)[0])

        event.terminal = specification.terminal  # type: ignore[attr-defined]
        event.direction = specification.direction  # type: ignore[attr-defined]
        wrapped_events.append(event)

    solution = solve_ivp(
        rhs,
        tuple(map(float, config.t_span)),
        np.asarray(config.y0, dtype=float),
        method=config.method,
        rtol=float(config.rtol),
        atol=np.asarray(config.atol, dtype=float),
        t_eval=None if config.t_eval is None else np.asarray(config.t_eval, dtype=float),
        max_step=float(config.max_step),
        events=wrapped_events or None,
    )
    event_times = {
        specification.name: tuple(float(value) for value in times)
        for specification, times in zip(config.events, solution.t_events or ())
    }
    triggered = tuple(name for name, times in event_times.items() if times)
    steps = max(0, int(len(solution.t) - 1))
    nonstiff_method = config.method in {"RK45", "DOP853"}
    stiffness_suspected = nonstiff_method and (
        not solution.success or steps > config.stiffness_step_threshold
    )
    return OdeResult(
        t=np.asarray(solution.t, dtype=float),
        y=np.asarray(solution.y, dtype=float),
        method=config.method,
        success=bool(solution.success),
        message=str(solution.message),
        step_count=steps,
        nfev=int(solution.nfev),
        njev=int(solution.njev),
        nlu=int(solution.nlu),
        triggered_events=triggered,
        event_times=event_times,
        stiffness_suspected=stiffness_suspected,
    )


@dataclass
class ConvergenceCertificate:
    """Three-level Richardson evidence for one scalar reported quantity."""

    levels: Sequence[float]
    solutions: Sequence[float]
    theoretical_order: float
    order_tolerance: float
    observed_order: float | None = field(init=False, default=None)
    extrapolated_value: float | None = field(init=False, default=None)
    status: ConvergenceStatus = field(init=False, default="not_attempted")

    def __post_init__(self) -> None:
        self.levels = tuple(float(value) for value in self.levels)
        self.solutions = tuple(float(value) for value in self.solutions)
        if len(self.levels) != len(self.solutions):
            raise ValueError("levels and solutions must have equal length")
        if self.theoretical_order <= 0 or self.order_tolerance < 0:
            raise ValueError("theoretical order must be positive and tolerance non-negative")
        if len(self.levels) < 3:
            self.status = "insufficient_levels"
            return
        if any(not math.isfinite(value) or value <= 0 for value in self.levels):
            raise ValueError("refinement levels must be finite and positive")
        if any(not math.isfinite(value) for value in self.solutions):
            raise ValueError("refinement solutions must be finite")
        if any(left <= right for left, right in zip(self.levels, self.levels[1:])):
            raise ValueError("refinement levels must be strictly decreasing")

        h0, h1, h2 = self.levels[-3:]
        value0, value1, value2 = self.solutions[-3:]
        ratio_left = h0 / h1
        ratio_right = h1 / h2
        if not math.isclose(ratio_left, ratio_right, rel_tol=1e-7, abs_tol=1e-12):
            self.status = "order_mismatch"
            return
        coarse_difference = value0 - value1
        fine_difference = value1 - value2
        if coarse_difference == 0.0 and fine_difference == 0.0:
            self.observed_order = float(self.theoretical_order)
            self.extrapolated_value = value2
            self.status = "converged"
            return
        if fine_difference == 0.0 or coarse_difference == 0.0:
            self.status = "order_mismatch"
            return
        self.observed_order = math.log(
            abs(coarse_difference / fine_difference)
        ) / math.log(ratio_right)
        if not math.isfinite(self.observed_order):
            self.status = "order_mismatch"
            return
        denominator = ratio_right ** self.observed_order - 1.0
        if abs(denominator) > np.finfo(float).eps:
            self.extrapolated_value = value2 + (value2 - value1) / denominator
        if abs(self.observed_order - self.theoretical_order) > self.order_tolerance:
            self.status = "order_mismatch"
            self.extrapolated_value = None
        else:
            self.status = "converged"

    @classmethod
    def not_attempted(
        cls, theoretical_order: float = 1.0, order_tolerance: float = 0.0
    ) -> "ConvergenceCertificate":
        certificate = cls((), (), theoretical_order, order_tolerance)
        certificate.status = "not_attempted"
        return certificate


@dataclass
class MechanismResult:
    """Guarded scalar result plus the finest computed value."""

    value_at_finest_level: float
    certificate: ConvergenceCertificate | None = None
    converged_value: float | None = None
    convergence_status: ConvergenceStatus = field(init=False)

    def __post_init__(self) -> None:
        self.value_at_finest_level = float(self.value_at_finest_level)
        if not math.isfinite(self.value_at_finest_level):
            raise ValueError("value_at_finest_level must be finite")
        if self.certificate is None:
            self.convergence_status = "not_attempted"
            self.converged_value = None
        else:
            self.convergence_status = self.certificate.status
            self.converged_value = (
                self.certificate.extrapolated_value
                if self.certificate.status == "converged"
                else None
            )


Temperature = float | ScalarField


@dataclass(frozen=True)
class BoundaryCondition:
    """Dirichlet temperature or Robin convective boundary condition."""

    kind: Literal["dirichlet", "robin"]
    value: Temperature | None = None
    heat_transfer_coefficient: float | None = None
    ambient_temperature: Temperature | None = None

    def __post_init__(self) -> None:
        if self.kind == "dirichlet" and self.value is None:
            raise ValueError("a Dirichlet boundary requires value")
        if self.kind == "robin":
            if self.heat_transfer_coefficient is None or self.heat_transfer_coefficient < 0:
                raise ValueError("a Robin boundary requires a non-negative coefficient")
        if self.kind not in {"dirichlet", "robin"}:
            raise ValueError(f"unsupported boundary kind: {self.kind!r}")


@dataclass(frozen=True)
class Heat1DConfig:
    length: float
    diffusivity: float
    conductivity: float
    spatial_points: int
    t_span: tuple[float, float]
    initial_temperature: Temperature
    left_boundary: BoundaryCondition
    right_boundary: BoundaryCondition
    environment_temperature: Temperature | None = None
    method: OdeMethod = "BDF"
    rtol: float = 1e-8
    atol: float = 1e-10
    t_eval: Sequence[float] | None = None
    refinement_points: Sequence[int] = ()
    theoretical_order: float = 2.0
    order_tolerance: float = 0.35
    observable: Callable[[np.ndarray, np.ndarray], float] | None = None

    def __post_init__(self) -> None:
        if self.length <= 0 or self.diffusivity <= 0 or self.conductivity <= 0:
            raise ValueError("length, diffusivity, and conductivity must be positive")
        if self.spatial_points < 3:
            raise ValueError("spatial_points must be at least 3")
        if any(int(points) < 3 for points in self.refinement_points):
            raise ValueError("every refinement grid must have at least 3 points")


@dataclass(frozen=True)
class Heat1DResult:
    x: np.ndarray
    t: np.ndarray
    temperature: np.ndarray
    ode_result: OdeResult
    mechanism_result: MechanismResult
    refinement_values: Mapping[int, float]


def _temperature_value(value: Temperature, position: float, time: float) -> float:
    return float(value(position, time) if callable(value) else value)


def _dirichlet_derivative(value: Temperature, position: float, time: float) -> float:
    if not callable(value):
        return 0.0
    step = 1e-6 * max(1.0, abs(time))
    return (
        _temperature_value(value, position, time + step)
        - _temperature_value(value, position, time - step)
    ) / (2.0 * step)


def _solve_heat_grid(config: Heat1DConfig, points: int) -> tuple[np.ndarray, OdeResult]:
    x = np.linspace(0.0, float(config.length), int(points))
    dx = float(x[1] - x[0])
    if callable(config.initial_temperature):
        initial = np.asarray(
            [_temperature_value(config.initial_temperature, float(position), config.t_span[0]) for position in x],
            dtype=float,
        )
    else:
        initial = np.full(points, float(config.initial_temperature), dtype=float)
    if config.left_boundary.kind == "dirichlet":
        initial[0] = _temperature_value(
            config.left_boundary.value, 0.0, config.t_span[0]  # type: ignore[arg-type]
        )
    if config.right_boundary.kind == "dirichlet":
        initial[-1] = _temperature_value(
            config.right_boundary.value, config.length, config.t_span[0]  # type: ignore[arg-type]
        )

    def ambient(boundary: BoundaryCondition, position: float, time: float) -> float:
        source = boundary.ambient_temperature
        if source is None:
            source = config.environment_temperature
        if source is None:
            raise ValueError("a Robin boundary requires ambient/environment temperature")
        return _temperature_value(source, position, time)

    def heat_rhs(time: float, state: np.ndarray, _parameters: Mapping[str, float]) -> np.ndarray:
        derivative = np.empty_like(state)
        derivative[1:-1] = config.diffusivity * (
            state[:-2] - 2.0 * state[1:-1] + state[2:]
        ) / dx**2
        if config.left_boundary.kind == "dirichlet":
            derivative[0] = _dirichlet_derivative(
                config.left_boundary.value, 0.0, time  # type: ignore[arg-type]
            )
        else:
            coefficient = float(config.left_boundary.heat_transfer_coefficient)
            derivative[0] = config.diffusivity * (
                2.0 * (state[1] - state[0]) / dx**2
                - 2.0 * coefficient * (state[0] - ambient(config.left_boundary, 0.0, time))
                / (config.conductivity * dx)
            )
        if config.right_boundary.kind == "dirichlet":
            derivative[-1] = _dirichlet_derivative(
                config.right_boundary.value, config.length, time  # type: ignore[arg-type]
            )
        else:
            coefficient = float(config.right_boundary.heat_transfer_coefficient)
            derivative[-1] = config.diffusivity * (
                2.0 * (state[-2] - state[-1]) / dx**2
                - 2.0 * coefficient * (
                    state[-1] - ambient(config.right_boundary, config.length, time)
                ) / (config.conductivity * dx)
            )
        return derivative

    ode = integrate_ode(
        heat_rhs,
        OdeConfig(
            t_span=config.t_span,
            y0=initial,
            method=config.method,
            rtol=config.rtol,
            atol=config.atol,
            t_eval=config.t_eval,
            stiffness_step_threshold=25_000,
        ),
    )
    return x, ode


def solve_heat_1d(config: Heat1DConfig) -> Heat1DResult:
    """Solve one-dimensional heat diffusion with Dirichlet/Robin boundaries."""

    grids = tuple(int(value) for value in config.refinement_points) or (
        int(config.spatial_points),
    )
    grids = tuple(sorted(set(grids)))
    if int(config.spatial_points) not in grids:
        grids = tuple(sorted((*grids, int(config.spatial_points))))
    records: dict[int, tuple[np.ndarray, OdeResult, float]] = {}
    for points in grids:
        x, ode = _solve_heat_grid(config, points)
        if not ode.success:
            raise RuntimeError(f"heat integration failed on {points} points: {ode.message}")
        observable = config.observable or (
            lambda coordinates, state: float(
                np.interp(config.length / 2.0, coordinates, state)
            )
        )
        value = float(observable(x, ode.y[:, -1]))
        records[points] = (x, ode, value)
    finest_points = max(records)
    finest_x, finest_ode, finest_value = records[finest_points]
    ordered_points = tuple(sorted(records))
    levels = tuple(config.length / (points - 1) for points in ordered_points)
    values = tuple(records[points][2] for points in ordered_points)
    certificate = ConvergenceCertificate(
        levels=levels,
        solutions=values,
        theoretical_order=config.theoretical_order,
        order_tolerance=config.order_tolerance,
    )
    mechanism = MechanismResult(finest_value, certificate)
    return Heat1DResult(
        x=finest_x,
        t=finest_ode.t,
        temperature=finest_ode.y,
        ode_result=finest_ode,
        mechanism_result=mechanism,
        refinement_values={points: records[points][2] for points in ordered_points},
    )


@dataclass(frozen=True)
class IdentificationData:
    x: Sequence[float] | np.ndarray
    y: Sequence[float] | np.ndarray
    fit_indices: Sequence[int]
    holdout_indices: Sequence[int]

    def __post_init__(self) -> None:
        x = np.asarray(self.x)
        y = np.asarray(self.y)
        if len(x) != len(y):
            raise ValueError("identification x and y must have equal row counts")
        fit = {int(index) for index in self.fit_indices}
        holdout = {int(index) for index in self.holdout_indices}
        if not fit or not holdout:
            raise ValueError("fit_indices and holdout_indices must both be non-empty")
        if fit.intersection(holdout):
            raise ValueError("fit and holdout observations must be disjoint")
        if min(fit | holdout) < 0 or max(fit | holdout) >= len(y):
            raise ValueError("fit or holdout index lies outside the data")


@dataclass(frozen=True)
class IdentificationConfig:
    parameter_bounds: Mapping[str, tuple[float, float]]
    starts: int = 12
    seed: int = 2020
    max_nfev: int = 2_000
    condition_number_threshold: float = 1e8
    column_correlation_threshold: float = 0.995
    identifiable_combinations: Sequence[str] = ()
    residual_unit: str = "1"

    def __post_init__(self) -> None:
        if not self.parameter_bounds:
            raise ValueError("at least one bounded parameter is required")
        for name, (lower, upper) in self.parameter_bounds.items():
            if not name.strip() or not (
                math.isfinite(lower) and math.isfinite(upper) and lower < upper
            ):
                raise ValueError(f"invalid finite bounds for parameter {name!r}")
        if self.starts < 1 or self.max_nfev < 1:
            raise ValueError("starts and max_nfev must be positive")
        if self.condition_number_threshold <= 1:
            raise ValueError("condition-number threshold must exceed one")
        if not 0 < self.column_correlation_threshold <= 1:
            raise ValueError("column-correlation threshold must lie in (0, 1]")
        if not self.residual_unit.strip():
            raise ValueError("residual_unit must be non-empty")


@dataclass(frozen=True)
class IdentificationStart:
    start: Mapping[str, float]
    estimate: Mapping[str, float]
    cost: float
    success: bool
    nfev: int


@dataclass
class IdentificationResult:
    parameter_names: Sequence[str]
    point_estimate: Mapping[str, float] | None
    parameter_intervals: Mapping[str, tuple[float, float]]
    identifiable_combinations: Sequence[str]
    fit_residual: float
    holdout_residual: float
    jacobian_condition_number: float
    column_correlation_matrix: Sequence[Sequence[float]]
    condition_number_threshold: float
    column_correlation_threshold: float
    residual_unit: str = "1"
    starts: Sequence[IdentificationStart] = ()
    identifiable: bool = field(init=False)

    def __post_init__(self) -> None:
        self.parameter_names = tuple(str(value) for value in self.parameter_names)
        matrix = np.asarray(self.column_correlation_matrix, dtype=float)
        count = len(self.parameter_names)
        if matrix.shape != (count, count):
            raise ValueError("column_correlation_matrix shape must match parameters")
        off_diagonal = matrix.copy()
        np.fill_diagonal(off_diagonal, 0.0)
        max_correlation = (
            float(np.nanmax(np.abs(off_diagonal))) if off_diagonal.size else 0.0
        )
        condition_failed = (
            not math.isfinite(float(self.jacobian_condition_number))
            or float(self.jacobian_condition_number) > self.condition_number_threshold
        )
        correlation_failed = (
            not math.isfinite(max_correlation)
            or max_correlation >= self.column_correlation_threshold
        )
        self.identifiable = not (condition_failed or correlation_failed)
        if not self.identifiable:
            self.point_estimate = None
        self.identifiable_combinations = tuple(self.identifiable_combinations)
        self.starts = tuple(self.starts)

    @property
    def maximum_column_correlation(self) -> float:
        matrix = np.asarray(self.column_correlation_matrix, dtype=float).copy()
        np.fill_diagonal(matrix, 0.0)
        finite = np.abs(matrix[np.isfinite(matrix)])
        maximum = float(np.max(finite)) if finite.size else 1.0
        return min(1.0, max(0.0, maximum))

    def contract_fields(
        self, point_estimate_metric_refs: Sequence[str] = ()
    ) -> dict[str, object]:
        interval_method = (
            "linearized_jacobian"
            if self.identifiable
            else "unbounded_due_to_nonidentifiability"
        )
        return {
            "identifiable": self.identifiable,
            "point_estimate": (
                None if self.point_estimate is None else dict(self.point_estimate)
            ),
            "point_estimate_metric_refs": list(point_estimate_metric_refs),
            "parameter_intervals": {
                name: {
                    "lower": float(bounds[0]) if math.isfinite(bounds[0]) else None,
                    "upper": float(bounds[1]) if math.isfinite(bounds[1]) else None,
                    "level": 0.95 if self.identifiable else None,
                    "method": interval_method,
                }
                for name, bounds in self.parameter_intervals.items()
            },
            "identifiable_combinations": list(self.identifiable_combinations),
            "fit_residual": {"value": float(self.fit_residual), "unit": self.residual_unit},
            "holdout_residual": {"value": float(self.holdout_residual), "unit": self.residual_unit},
            "jacobian_condition_number": (
                float(self.jacobian_condition_number)
                if math.isfinite(self.jacobian_condition_number)
                else None
            ),
            "maximum_column_correlation": self.maximum_column_correlation,
            "condition_number_threshold": float(self.condition_number_threshold),
            "column_correlation_threshold": float(self.column_correlation_threshold),
        }


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(np.asarray(values, dtype=float)))))


def identify_parameters(
    model: ParameterModel,
    data: IdentificationData,
    config: IdentificationConfig,
) -> IdentificationResult:
    """Fit bounded parameters with fixed-seed multistart and check identifiability."""

    names = tuple(config.parameter_bounds)
    lower = np.asarray([config.parameter_bounds[name][0] for name in names], dtype=float)
    upper = np.asarray([config.parameter_bounds[name][1] for name in names], dtype=float)
    x = np.asarray(data.x, dtype=float)
    y = np.asarray(data.y, dtype=float)
    fit_index = np.asarray(tuple(data.fit_indices), dtype=int)
    holdout_index = np.asarray(tuple(data.holdout_indices), dtype=int)

    def parameters(vector: np.ndarray) -> dict[str, float]:
        return {name: float(value) for name, value in zip(names, vector)}

    def residual(vector: np.ndarray) -> np.ndarray:
        prediction = np.asarray(model(parameters(vector), x[fit_index]), dtype=float)
        if prediction.shape != y[fit_index].shape:
            raise ValueError("parameter model output shape must match selected observations")
        return prediction - y[fit_index]

    rng = np.random.default_rng(config.seed)
    starts = rng.uniform(lower, upper, size=(config.starts, len(names)))
    starts[0] = (lower + upper) / 2.0
    fitted: list[tuple[object, IdentificationStart]] = []
    for start in starts:
        outcome = least_squares(
            residual,
            start,
            bounds=(lower, upper),
            max_nfev=config.max_nfev,
        )
        record = IdentificationStart(
            start=parameters(start),
            estimate=parameters(outcome.x),
            cost=float(outcome.cost),
            success=bool(outcome.success),
            nfev=int(outcome.nfev),
        )
        fitted.append((outcome, record))
    successful = [item for item in fitted if item[1].success]
    candidates = successful or fitted
    best_outcome, _best_record = min(candidates, key=lambda item: item[1].cost)
    best_vector = np.asarray(best_outcome.x, dtype=float)
    jacobian = np.asarray(best_outcome.jac, dtype=float)
    condition_number = float(np.linalg.cond(jacobian))
    norms = np.linalg.norm(jacobian, axis=0)
    denominator = np.outer(norms, norms)
    with np.errstate(divide="ignore", invalid="ignore"):
        correlations = (jacobian.T @ jacobian) / denominator
    correlations = np.where(np.isfinite(correlations), correlations, np.nan)
    for index, norm_value in enumerate(norms):
        correlations[index, index] = 1.0 if norm_value > 0 else np.nan

    raw_estimate = parameters(best_vector)
    fit_residual = residual(best_vector)
    holdout_prediction = np.asarray(
        model(raw_estimate, x[holdout_index]), dtype=float
    )
    if holdout_prediction.shape != y[holdout_index].shape:
        raise ValueError("parameter model output shape must match holdout observations")
    degrees = max(1, len(fit_index) - len(names))
    variance = float(2.0 * best_outcome.cost / degrees)
    covariance = variance * np.linalg.pinv(jacobian.T @ jacobian)
    standard_errors = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    intervals = {
        name: (
            float(max(lower[index], best_vector[index] - 1.96 * standard_errors[index])),
            float(min(upper[index], best_vector[index] + 1.96 * standard_errors[index])),
        )
        for index, name in enumerate(names)
    }
    result = IdentificationResult(
        parameter_names=names,
        point_estimate=raw_estimate,
        parameter_intervals=intervals,
        identifiable_combinations=config.identifiable_combinations,
        fit_residual=_rms(fit_residual),
        holdout_residual=_rms(holdout_prediction - y[holdout_index]),
        jacobian_condition_number=condition_number,
        column_correlation_matrix=correlations.tolist(),
        condition_number_threshold=config.condition_number_threshold,
        column_correlation_threshold=config.column_correlation_threshold,
        residual_unit=config.residual_unit,
        starts=tuple(record for _outcome, record in fitted),
    )
    if not result.identifiable:
        result.parameter_intervals = {name: (-math.inf, math.inf) for name in names}
    return result


@dataclass(frozen=True)
class DistributionSpec:
    kind: Literal["uniform", "normal", "lognormal"]
    parameters: tuple[float, float]

    def __post_init__(self) -> None:
        first, second = map(float, self.parameters)
        if self.kind == "uniform" and not first < second:
            raise ValueError("uniform parameters are lower, upper with lower < upper")
        if self.kind in {"normal", "lognormal"} and second <= 0:
            raise ValueError("normal/lognormal scale must be positive")
        if self.kind not in {"uniform", "normal", "lognormal"}:
            raise ValueError(f"unsupported distribution: {self.kind!r}")

    def transform(self, unit_values: np.ndarray) -> np.ndarray:
        clipped = np.clip(np.asarray(unit_values, dtype=float), 1e-12, 1.0 - 1e-12)
        first, second = map(float, self.parameters)
        if self.kind == "uniform":
            return first + (second - first) * clipped
        if self.kind == "normal":
            return norm.ppf(clipped, loc=first, scale=second)
        return np.exp(norm.ppf(clipped, loc=first, scale=second))


@dataclass(frozen=True)
class UncertaintyConfig:
    sample_size: int
    method: SamplingMethod = "sobol"
    seed: int = 2020
    absolute_precision: float = 0.0
    relative_precision: float = 0.01
    quantiles: Sequence[float] = (0.025, 0.5, 0.975)

    def __post_init__(self) -> None:
        if self.sample_size < 2:
            raise ValueError("sample_size must be at least two")
        if self.method not in {"monte_carlo", "sobol", "halton"}:
            raise ValueError(f"unsupported sampling method: {self.method!r}")
        if self.method == "sobol" and self.sample_size & (self.sample_size - 1):
            raise ValueError("Sobol sample_size must be a power of two")
        if self.absolute_precision < 0 or self.relative_precision < 0:
            raise ValueError("precision tolerances must be non-negative")
        if any(not 0.0 < value < 1.0 for value in self.quantiles):
            raise ValueError("quantiles must lie strictly between zero and one")


@dataclass
class UncertaintyResult:
    raw_estimate: float
    reportable_estimate: float | None
    mc_standard_error: float
    sample_size: int
    sample_size_sufficient: bool
    precision_limit: float
    quantiles: Mapping[float, float]
    method: SamplingMethod
    seed: int
    outputs: np.ndarray = field(repr=False)

    def __post_init__(self) -> None:
        if not self.sample_size_sufficient:
            self.reportable_estimate = None
        elif self.reportable_estimate is None:
            self.reportable_estimate = float(self.raw_estimate)


def propagate_uncertainty(
    model: UncertaintyModel,
    distributions: Mapping[str, DistributionSpec],
    config: UncertaintyConfig,
) -> UncertaintyResult:
    """Propagate independent inputs and guard estimates whose MCSE is too large."""

    if not distributions:
        raise ValueError("at least one uncertain input distribution is required")
    dimension = len(distributions)
    if config.method == "monte_carlo":
        unit = np.random.default_rng(config.seed).random((config.sample_size, dimension))
    elif config.method == "sobol":
        engine = qmc.Sobol(d=dimension, scramble=True, seed=config.seed)
        unit = engine.random_base2(int(math.log2(config.sample_size)))
    else:
        engine = qmc.Halton(d=dimension, scramble=True, seed=config.seed)
        unit = engine.random(config.sample_size)
    samples = {
        name: specification.transform(unit[:, index])
        for index, (name, specification) in enumerate(distributions.items())
    }
    outputs = np.asarray(model(samples), dtype=float).reshape(-1)
    if len(outputs) != config.sample_size or not np.all(np.isfinite(outputs)):
        raise ValueError("uncertainty model must return one finite value per sample")
    estimate = float(np.mean(outputs))
    mcse = float(np.std(outputs, ddof=1) / math.sqrt(config.sample_size))
    limit = float(config.absolute_precision + config.relative_precision * abs(estimate))
    sufficient = mcse <= limit
    return UncertaintyResult(
        raw_estimate=estimate,
        reportable_estimate=estimate,
        mc_standard_error=mcse,
        sample_size=config.sample_size,
        sample_size_sufficient=sufficient,
        precision_limit=limit,
        quantiles={
            float(level): float(np.quantile(outputs, level))
            for level in config.quantiles
        },
        method=config.method,
        seed=config.seed,
        outputs=outputs,
    )


@dataclass(frozen=True)
class AnalyticOracleResult:
    case: str
    status: OracleStatus
    numerical_value: tuple[float, ...]
    reference_value: tuple[float, ...]
    maximum_absolute_error: float
    tolerance: float
    method: str


def verify_analytic_oracle(case: str) -> AnalyticOracleResult:
    """Run one built-in truth-source check independent of any competition paper."""

    normalized = case.strip().lower().replace("-", "_")
    if normalized in {"exponential", "exponential_decay", "linear_ode"}:
        rate = 1.7
        finish = 2.0
        solution = integrate_ode(
            lambda _t, state, parameters: [-parameters["rate"] * state[0]],
            OdeConfig(
                (0.0, finish),
                (1.0,),
                method="DOP853",
                rtol=1e-12,
                atol=1e-14,
                parameters={"rate": rate},
            ),
        )
        numerical = (float(solution.y[0, -1]),)
        reference = (math.exp(-rate * finish),)
        tolerance = 1e-10
        method = solution.method
    elif normalized in {"robertson", "stiff_robertson"}:
        def robertson(
            _time: float, state: np.ndarray, _parameters: Mapping[str, float]
        ) -> list[float]:
            first, second, third = state
            return [
                -0.04 * first + 1e4 * second * third,
                0.04 * first - 1e4 * second * third - 3e7 * second**2,
                3e7 * second**2,
            ]

        solution = integrate_ode(
            robertson,
            OdeConfig(
                (0.0, 1.0),
                (1.0, 0.0, 0.0),
                method="Radau",
                rtol=2e-10,
                atol=1e-13,
            ),
        )
        numerical = tuple(float(value) for value in solution.y[:, -1])
        reference = (0.966459737333, 0.0000307462658, 0.0335095164012)
        tolerance = 2e-9
        method = solution.method
    elif normalized in {"heat", "heat_dirichlet", "heat_series"}:
        length = 1.0
        diffusivity = 0.2
        finish = 0.3
        result = solve_heat_1d(
            Heat1DConfig(
                length=length,
                diffusivity=diffusivity,
                conductivity=1.0,
                spatial_points=81,
                t_span=(0.0, finish),
                initial_temperature=lambda position, _time: math.sin(
                    math.pi * position / length
                ),
                left_boundary=BoundaryCondition("dirichlet", value=0.0),
                right_boundary=BoundaryCondition("dirichlet", value=0.0),
                method="BDF",
                rtol=2e-10,
                atol=1e-12,
            )
        )
        numerical = tuple(float(value) for value in result.temperature[:, -1])
        reference = tuple(
            float(
                math.exp(-diffusivity * math.pi**2 * finish / length**2)
                * math.sin(math.pi * position / length)
            )
            for position in result.x
        )
        tolerance = 8e-5
        method = "MOL-BDF"
    else:
        raise ValueError(f"unknown analytic oracle case: {case!r}")
    error = float(
        np.max(np.abs(np.asarray(numerical, dtype=float) - np.asarray(reference, dtype=float)))
    )
    status: OracleStatus
    if error <= 1e-12:
        status = "exact_match"
    elif error <= tolerance:
        status = "within_tolerance"
    else:
        status = "mismatch"
    return AnalyticOracleResult(
        case=normalized,
        status=status,
        numerical_value=numerical,
        reference_value=reference,
        maximum_absolute_error=error,
        tolerance=tolerance,
        method=method,
    )
