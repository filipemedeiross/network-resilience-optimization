from __future__ import annotations

import json
import math
import time
import hashlib
import importlib.metadata

from typing      import Callable
from dataclasses import dataclass

from mip import (
    BINARY  ,
    CBC     ,
    MAXIMIZE,
    MINIMIZE,
    Model             ,
    xsum              ,
    OptimizationStatus,
)

from .schemas import (
    MilitaryVertexInterdictionProblem,
    NodeRole                         ,
    SolveRequest                     ,
    SolveResponse                    ,
    SolveSolution                    ,
    SolveStats                       ,
    SolverMetadata                   ,
    WaterEdgeCutProblem              ,
)


API_VERSION = "1.0.0"


FORMULATION_VERSIONS = {
    "water_edge_cut"               : "water-edge-cut/1.0"              ,
    "military_vertex_interdiction" : "military-vertex-interdiction/1.0",
}
SOLUTION_STATUSES    = {
    OptimizationStatus.OPTIMAL ,
    OptimizationStatus.FEASIBLE,
}


class SolverExecutionError(RuntimeError):
    """Raised when CBC fails or returns an internally inconsistent incumbent."""


@dataclass(frozen=True)
class Incumbent:
    objective_value       : float
    removed_node_ids      : list[str]
    removed_edge_ids      : list[str]
    disconnected_node_ids : list[str]


def solver_metadata() -> SolverMetadata:
    try:
        version = importlib.metadata.version("mip")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"

    return SolverMetadata(name="CBC", library="python-mip", library_version=version)


def canonical_input_sha256(request: SolveRequest) -> str:
    """Hash the versioned mathematical problem, excluding request id and timeout."""

    problem = request.problem.model_dump(mode="json")

    problem["nodes"] = sorted(problem["nodes"], key=lambda node: node["id"])
    problem["edges"] = sorted(problem["edges"], key=lambda edge: edge["id"])

    payload = {
        "schema_version" : request.schema_version,
        "problem"        : problem               ,
    }

    encoded = json.dumps(
        payload,
        sort_keys   =True      ,
        separators  =(",", ":"),
        ensure_ascii=False     ,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def _finite_or_none(
    value_getter : Callable[[], float | None]
) -> float | None:
    try:
        value = value_getter()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None

    if value is None:
        return None

    value = float(value)

    return value if math.isfinite(value) else None


def _status_and_reason(status: OptimizationStatus) -> tuple[str, str]:
    if status == OptimizationStatus.OPTIMAL:
        return "optimal", "optimal"

    if status == OptimizationStatus.FEASIBLE:
        return "feasible", "limit_reached_with_incumbent"

    if status in (OptimizationStatus.INFEASIBLE, OptimizationStatus.INT_INFEASIBLE):
        return "infeasible", "infeasible"

    if status == OptimizationStatus.UNBOUNDED:
        return "error", "unbounded"

    if status == OptimizationStatus.ERROR:
        return "error", "solver_error"

    return "no_solution", "limit_reached_without_incumbent"


def _selected(variable: object) -> bool:
    value = getattr(variable, "x", None)

    return value is not None and value >= 0.5


def _reachable_nodes(
    node_ids : set[str]                  ,
    edges    : list[tuple[str, str, str]],
    start    : str                       ,
    removed_nodes: set[str] | None = None,
    removed_edges: set[str] | None = None,
) -> set[str]:
    removed_nodes = removed_nodes or set()
    removed_edges = removed_edges or set()

    adjacency = {
        node_id : set()
        for node_id in node_ids - removed_nodes
    }

    for edge_id, source, target in edges:
        if (
            edge_id in removed_edges or
            source  in removed_nodes or
            target  in removed_nodes
        ):
            continue

        adjacency[source].add(target)
        adjacency[target].add(source)

    visited: set[str] = set()
    pending           = [start]

    while pending:
        node_id = pending.pop()

        if node_id in visited:
            continue

        visited.add   (node_id)
        pending.extend(adjacency[node_id] - visited)

    return visited


def _new_model(name: str, sense: str) -> Model:
    model = Model(name=name, sense=sense, solver_name=CBC)

    model.verbose = 0

    return model


def _solve_water(
    problem: WaterEdgeCutProblem, time_seconds: float
) -> tuple[Model, OptimizationStatus, Incumbent | None, str]:
    model = _new_model("water_edge_cut", MINIMIZE)

    partition = {
        node.id : model.add_var(name=f"side_{index}", var_type=BINARY)
        for index, node in enumerate(problem.nodes)
    }
    cut = {
        edge.id : model.add_var(name=f"cut_{index}", var_type=BINARY)
        for index, edge in enumerate(problem.edges)
    }

    origin      = next(
        node.id
        for node in problem.nodes
        if  node.role == NodeRole.ORIGIN
    )
    destination = next(
        node.id
        for node in problem.nodes
        if  node.role == NodeRole.DESTINATION
    )

    model += partition[origin     ] == 0
    model += partition[destination] == 1

    for edge in problem.edges:
        model += cut[edge.id] >= partition[edge.source] - partition[edge.target]
        model += cut[edge.id] >= partition[edge.target] - partition[edge.source]

        if not edge.removable:
            model += cut[edge.id] == 0

    model.objective = xsum(cut.values())

    status    = model.optimize    (max_seconds=time_seconds)
    _, reason = _status_and_reason(status)

    if status not in SOLUTION_STATUSES:
        return model, status, None, reason

    removed_edges = sorted(edge_id for edge_id, var in cut.items() if _selected(var))
    edge_tuples   = [(edge.id, edge.source, edge.target) for edge in problem.edges]

    reachable = _reachable_nodes(
        {node.id for node in problem.nodes},
        edge_tuples,
        origin     ,
        removed_edges=set(removed_edges),
    )

    if destination in reachable:
        raise SolverExecutionError("CBC incumbent does not disconnect the destination")

    removable_edges = {edge.id for edge in problem.edges if edge.removable}

    if not set(removed_edges) <= removable_edges:
        raise SolverExecutionError("CBC incumbent removes a protected edge")

    disconnected = sorted({node.id for node in problem.nodes} - reachable)

    incumbent = Incumbent(
        objective_value      =float(len(removed_edges)),
        removed_node_ids     =[]                       ,
        removed_edge_ids     =removed_edges,
        disconnected_node_ids=disconnected ,
    )

    return model, status, incumbent, reason


def _military_incumbent(
    problem           : MilitaryVertexInterdictionProblem,
    removed_vars      : dict[str, object],
    disconnected_vars : dict[str, object],
) -> Incumbent:
    removed      = sorted(
        node_id
        for node_id, var in removed_vars.items()
        if  _selected(var)
    )
    disconnected = sorted(
        node_id
        for node_id, var in disconnected_vars.items()
        if  _selected(var)
    )

    node_by_id   = {node.id: node for node in problem.nodes}
    headquarters = next(
        node.id for node in problem.nodes if node.role == NodeRole.HEADQUARTERS
    )

    edge_tuples = [(edge.id, edge.source, edge.target) for edge in problem.edges]
    reachable   = _reachable_nodes(
        set(node_by_id), edge_tuples, headquarters, removed_nodes=set(removed)
    )

    actual_disconnected = set(node_by_id) - set(removed) - reachable

    if set(disconnected) != actual_disconnected:
        raise SolverExecutionError(
            "CBC partition is inconsistent with the removed military nodes"
        )

    if any(not node_by_id[node_id].removable for node_id in removed):
        raise SolverExecutionError("CBC incumbent removes a protected military node")

    removal_cost = sum(node_by_id[node_id].removal_cost for node_id in removed)

    if removal_cost > problem.budget + 1e-7:
        raise SolverExecutionError("CBC incumbent exceeds the military budget")

    return Incumbent(
        objective_value     =float(len(removed) + len(disconnected)),
        removed_node_ids    =removed      ,
        removed_edge_ids    =[]           ,
        disconnected_node_ids=disconnected,
    )


def _solve_military(
    problem: MilitaryVertexInterdictionProblem, time_seconds: float
) -> tuple[Model, OptimizationStatus, Incumbent | None, str]:
    started = time.perf_counter()

    model   = _new_model("military_vertex_interdiction", MAXIMIZE)

    removed = {
        node.id : model.add_var(name=f"removed_{index}", var_type=BINARY)
        for index, node in enumerate(problem.nodes)
    }
    disconnected = {
        node.id : model.add_var(name=f"disconnected_{index}", var_type=BINARY)
        for index, node in enumerate(problem.nodes)
    }

    headquarters = next(
        node.id
        for node in problem.nodes
        if  node.role == NodeRole.HEADQUARTERS
    )

    model += removed     [headquarters] == 0
    model += disconnected[headquarters] == 0

    for node in problem.nodes:
        model += removed[node.id] + disconnected[node.id] <= 1

        if not node.removable:
            model += removed[node.id] == 0

    model += (
        xsum(node.removal_cost * removed[node.id] for node in problem.nodes)
        <= problem.budget
    )

    for edge in problem.edges:
        model += (
            disconnected[edge.source] - disconnected[edge.target]
            <= removed[edge.source] + removed[edge.target]
        )
        model += (
            disconnected[edge.target] - disconnected[edge.source]
            <= removed[edge.source] + removed[edge.target]
        )

    affected = xsum(
        removed[node.id] + disconnected[node.id] for node in problem.nodes
    )

    model.objective = affected
    primary_status  = model.optimize(max_seconds=time_seconds)

    _, reason = _status_and_reason(primary_status)

    if primary_status not in SOLUTION_STATUSES:
        return model, primary_status, None, reason

    incumbent     = _military_incumbent(problem, removed, disconnected)
    primary_bound = _finite_or_none    (lambda : model.objective_bound)
    primary_gap   = _finite_or_none    (lambda : model.gap)

    if primary_status == OptimizationStatus.OPTIMAL:
        remaining = time_seconds - (time.perf_counter() - started)

        if remaining >= 0.01:
            primary_optimum = round(incumbent.objective_value)

            model += affected == primary_optimum

            model.sense        = MINIMIZE
            small_count_weight = 1.0 / (len(problem.nodes) + 1.0)

            model.objective = xsum(
                (node.removal_cost + small_count_weight) * removed[node.id]
                for node in problem.nodes
            )

            secondary_status = model.optimize(max_seconds=remaining)

            if secondary_status in SOLUTION_STATUSES:
                incumbent = _military_incumbent(problem, removed, disconnected)

                if secondary_status == OptimizationStatus.OPTIMAL:
                    reason = "optimal_with_minimum_cost_tiebreak"
                else:
                    reason = "optimal_primary_tiebreak_limit_reached"

    setattr(model, "_primary_objective_bound", primary_bound)
    setattr(model, "_primary_gap"            , primary_gap  )

    return model, primary_status, incumbent, reason


def solve(request: SolveRequest) -> SolveResponse:
    started             = time.perf_counter()
    formulation_version = FORMULATION_VERSIONS[request.problem.type]

    if isinstance(request.problem, WaterEdgeCutProblem):
        model, optimization_status, incumbent, reason = _solve_water(
            request.problem, request.limits.time_seconds
        )
    else:
        model, optimization_status, incumbent, reason = _solve_military(
            request.problem, request.limits.time_seconds
        )

    status, default_reason = _status_and_reason(optimization_status)
    reason                 = reason or default_reason

    best_bound = getattr(model, "_primary_objective_bound", None)
    gap        = getattr(model, "_primary_gap"            , None)

    if best_bound is None:
        best_bound = _finite_or_none(lambda: model.objective_bound)
    if gap is None:
        gap        = _finite_or_none(lambda: model.gap            )

    solution = None

    if incumbent is not None:
        solution = SolveSolution(
            objective_value=incumbent.objective_value,
            best_bound     =best_bound               ,
            gap            =gap                      ,

            removed_node_ids     =incumbent.removed_node_ids     ,
            removed_edge_ids     =incumbent.removed_edge_ids     ,
            disconnected_node_ids=incumbent.disconnected_node_ids,
        )

    return SolveResponse(
        request_id  =request.request_id             ,
        input_sha256=canonical_input_sha256(request),

        formulation_version=formulation_version,
        status             =status             ,
        termination_reason =reason             ,
        solution           =solution           ,

        stats=SolveStats(
            wall_seconds    =time.perf_counter() - started,
            node_count      =len(request.problem.nodes)   ,
            edge_count      =len(request.problem.edges)   ,
            variable_count  =model.num_cols,
            constraint_count=model.num_rows,
        ),

        solver=solver_metadata(),
    )


def probe_solver() -> bool:
    model    = _new_model   ("readiness_probe", MAXIMIZE       )
    variable = model.add_var(name="probe"     , var_type=BINARY)

    model.objective = variable

    status = model.optimize(max_seconds=1.0)

    return status == OptimizationStatus.OPTIMAL and _selected(variable)
