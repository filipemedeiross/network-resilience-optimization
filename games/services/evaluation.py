from __future__ import annotations

import re
import math

from typing          import Any
from numbers         import Real
from collections     import deque
from collections.abc import Mapping
from dataclasses     import dataclass


class DomainValidationError(ValueError           ):
    pass

class InvalidScenario      (DomainValidationError):
    pass

class InvalidSelection     (DomainValidationError):
    pass


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class Verification:
    verified   : bool
    error      : str
    evaluation : dict[str, Any] | None = None


def _identifier(
    value : Any,
    label : str,
) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise InvalidScenario(
            f"{label} deve usar 1-128 letras ASCII, numeros, '.', '_', ':' ou '-'."
        )

    return value


def _number(
    value : Any,
    label : str,

    *,

    nonnegative : bool = True,
) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidScenario(f"{label} deve ser numerico.")

    number = float(value)
    if not math.isfinite(number):
        raise InvalidScenario(f"{label} deve ser finito.")

    if nonnegative and number < 0:
        raise InvalidScenario(f"{label} nao pode ser negativo.")

    return number


def _graph(
    topology : Mapping[str, Any],
) -> tuple[dict[str, dict], dict[str, dict], dict[str, set[str]]]:
    if not isinstance(topology, Mapping):
        raise InvalidScenario("topology deve ser um objeto JSON.")

    raw_nodes = topology.get("nodes")
    raw_edges = topology.get("edges")

    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise InvalidScenario("topology.nodes deve ser uma lista nao vazia.")

    if not isinstance(raw_edges, list):
        raise InvalidScenario("topology.edges deve ser uma lista.")

    nodes: dict[str, dict] = {}
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            raise InvalidScenario("Cada no deve ser um objeto JSON.")

        node_id = _identifier(raw.get("id"), "node.id")

        if node_id in nodes:
            raise InvalidScenario(f"ID de no duplicado: {node_id}.")

        nodes[node_id] = dict(raw)

    edges          : dict[str, dict]     = {}
    adjacency                            = {node_id : set() for node_id in nodes}
    endpoint_pairs : set[frozenset[str]] = set()

    for raw in raw_edges:
        if not isinstance(raw, Mapping):
            raise InvalidScenario("Cada aresta deve ser um objeto JSON.")

        edge_id = _identifier(raw.get("id"    ), "edge.id")
        source  = _identifier(raw.get("source"), f"edge {edge_id}.source")
        target  = _identifier(raw.get("target"), f"edge {edge_id}.target")

        if edge_id in edges:
            raise InvalidScenario(f"ID de aresta duplicado: {edge_id}.")

        if source not in nodes or target not in nodes:
            raise InvalidScenario(f"Aresta {edge_id} referencia no inexistente.")

        if source == target:
            raise InvalidScenario(f"Aresta {edge_id} e um auto-laco.")

        pair = frozenset((source, target))

        if pair in endpoint_pairs:
            raise InvalidScenario(f"Aresta paralela entre {source} e {target}.")

        endpoint_pairs    .add   (pair  )
        edges    [edge_id] = dict(raw   )
        adjacency[source ].add   (target)
        adjacency[target ].add   (source)

    visited : set[str] = set()
    pending            = [next(iter(nodes))]

    while pending:
        node_id = pending.pop()

        if node_id in visited:
            continue

        visited.add   (node_id)
        pending.extend(adjacency[node_id] - visited)

    if visited != set(nodes):
        raise InvalidScenario("A topologia deve formar um grafo conexo.")

    return nodes, edges, adjacency


def _role_node(
    nodes : Mapping[str, Mapping[str, Any]],
    role  : str                            ,
) -> str:
    matches = [
        node_id
        for node_id, node in nodes.items()
        if  node.get("role") == role
    ]

    if len(matches) != 1:
        raise InvalidScenario(f"A topologia deve ter exatamente um no com role={role!r}.")

    return matches[0]


def validate_scenario(
    kind     : str              ,
    topology : Mapping[str, Any],
    rules    : Mapping[str, Any],
) -> None:
    nodes, edges, _ = _graph(topology)

    if not isinstance(rules, Mapping):
        raise InvalidScenario("rules deve ser um objeto JSON.")

    if kind == "water":
        _role_node(nodes, "origin"     )
        _role_node(nodes, "destination")

        for edge_id, edge in edges.items():
            if not isinstance(edge.get("removable"), bool):
                raise InvalidScenario(f"Aresta {edge_id} deve declarar removable.")

        return

    if kind == "military":
        _role_node(nodes              , "headquarters")
        _number   (rules.get("budget"), "rules.budget")

        for node_id, node in nodes.items():
            if not isinstance(node.get("removable"), bool):
                raise InvalidScenario(f"No {node_id} deve declarar removable.")

            cost = _number(node.get("removal_cost"), f"node {node_id}.removal_cost")

            if node["removable"] and cost <= 0:
                raise InvalidScenario(
                    f"No removivel {node_id} deve ter removal_cost positivo."
                )

        return

    raise InvalidScenario(f"Tipo de cenario desconhecido: {kind!r}.")


def _selection_ids(selection: Mapping[str, Any], field: str) -> list[str]:
    if not isinstance(selection, Mapping):
        raise InvalidSelection("selection deve ser um objeto JSON.")

    unexpected = set(selection) - {field}

    if unexpected:
        raise InvalidSelection(
            "Campos de selecao desconhecidos: " + ", ".join(sorted(unexpected)) + "."
        )

    values = selection.get(field, [])

    if not isinstance(values, list):
        raise InvalidSelection(f"selection.{field} deve ser uma lista.")

    if any(not isinstance(value, str) or not value for value in values):
        raise InvalidSelection(f"Todos os itens de selection.{field} devem ser IDs string.")

    if len(set(values)) != len(values):
        raise InvalidSelection(f"selection.{field} contem IDs duplicados.")

    return sorted(values)


def _reachable(
    start     : str                   ,
    adjacency : Mapping[str, set[str]],
    *,
    removed_nodes : set[str           ] | None = None,
    removed_edges : set[frozenset[str]] | None = None,
) -> set[str]:
    removed_nodes = removed_nodes or set()
    removed_edges = removed_edges or set()

    if start in removed_nodes:
        return set()

    visited = {start}
    queue   = deque([start])

    while queue:
        node = queue.popleft()

        for neighbour in adjacency[node]:
            if neighbour in removed_nodes:
                continue

            if frozenset((node, neighbour)) in removed_edges:
                continue

            if neighbour not in visited:
                visited.add   (neighbour)
                queue  .append(neighbour)

    return visited


def _gap(
    score   : float       ,
    optimum : float | None,
    *,
    minimize : bool,
    success  : bool,
) -> float | None:
    if optimum is None or not success:
        return None

    raw = score - optimum if minimize else optimum - score

    return max(0.0, raw)


def evaluate_water(
    topology  : Mapping[str, Any],
    rules     : Mapping[str, Any],
    selection : Mapping[str, Any],
    *,
    optimal_objective : float | None = None,
) -> dict[str, Any]:
    validate_scenario("water", topology, rules)

    nodes, edges, adjacency = _graph        (topology )
    selected_ids            = _selection_ids(selection, "edge_ids")

    unknown = set(selected_ids) - set(edges)

    if unknown:
        raise InvalidSelection("Arestas inexistentes: " + ", ".join(sorted(unknown)) + ".")

    protected = [
        edge_id
        for edge_id in selected_ids
        if  not edges[edge_id]["removable"]
    ]

    if protected:
        raise InvalidSelection("Arestas nao removiveis: " + ", ".join(protected) + ".")

    origin      = _role_node(nodes, "origin"     )
    destination = _role_node(nodes, "destination")

    removed_pairs = {
        frozenset(
            (
                edges[edge_id]["source"],
                edges[edge_id]["target"],
            )
        )
        for edge_id in selected_ids
    }

    reachable    = _reachable(origin, adjacency, removed_edges=removed_pairs)
    disconnected = sorted    (set(nodes) - reachable)

    success = destination not in reachable

    score   = len (selected_ids)
    gap     = _gap(
        score            ,
        optimal_objective,
        minimize=True    ,
        success =success ,
    )

    return {
        "valid"    : True   ,
        "success"  : success,
        "feasible" : success,

        "objective_value"   : score,
        "score"             : score,
        "optimal_objective" : optimal_objective,

        "gap"        : gap,
        "is_optimal" : bool(
            success and optimal_objective is not None and gap == 0
        ),

        "budget_used" : None,
        "budget"      : None,

        "selected_edge_ids"     : selected_ids,
        "disconnected_node_ids" : disconnected,
        "reason"                : (
            "destination_disconnected"
            if   success
            else "destination_still_connected"
        ),
    }


def evaluate_military(
    topology  : Mapping[str, Any],
    rules     : Mapping[str, Any],
    selection : Mapping[str, Any],
    *,
    optimal_objective : float | None = None,
) -> dict[str, Any]:
    validate_scenario("military", topology, rules)

    nodes, _, adjacency = _graph        (topology )
    selected_ids        = _selection_ids(selection, "node_ids")

    unknown = set(selected_ids) - set(nodes)

    if unknown:
        raise InvalidSelection("Nos inexistentes: " + ", ".join(sorted(unknown)) + ".")

    protected = [
        node_id
        for node_id in selected_ids
        if  not nodes[node_id]["removable"]
    ]
    if protected:
        raise InvalidSelection("Nos nao removiveis: " + ", ".join(protected) + ".")

    budget      = _number(rules.get("budget"), "rules.budget")
    budget_used = sum    (
        _number(
            nodes[node_id]["removal_cost"],
            f"node {node_id}.removal_cost",
        )
        for node_id in selected_ids
    )

    within_budget = budget_used <= budget + 1e-9

    headquarters  = _role_node(nodes       , "headquarters")
    removed       = set       (selected_ids)
    reachable     = _reachable(headquarters, adjacency, removed_nodes=removed)

    disconnected  = sorted(set(nodes) - removed - reachable)
    affected      = sorted(removed | set(disconnected)     )
    score         = len   (affected)

    success       = within_budget and score > 0
    gap           = _gap(
        score            ,
        optimal_objective,
        minimize=False   ,
        success =success ,
    )

    return {
        "valid"             : within_budget    ,
        "success"           : success          ,
        "feasible"          : success          ,
        "objective_value"   : score            ,
        "score"             : score            ,
        "optimal_objective" : optimal_objective,

        "gap"        : gap,
        "is_optimal" : bool(
            success and optimal_objective is not None and gap == 0
        ),

        "budget_used"           : budget_used ,
        "budget"                : budget      ,
        "selected_node_ids"     : selected_ids,
        "disconnected_node_ids" : disconnected,
        "affected_node_ids"     : affected    ,

        "reason" : (
            "budget_exceeded"
            if   not within_budget
            else "units_disconnected"
            if   success
            else "no_units_disconnected"
        ),
    }


def evaluate_attempt(
    *,
    kind              : str                ,
    topology          : Mapping[str, Any]  ,
    rules             : Mapping[str, Any]  ,
    selection         : Mapping[str, Any]  ,
    optimal_objective : float | None = None,
) -> dict[str, Any]:
    if kind == "water":
        return evaluate_water(
            topology, rules, selection, optimal_objective=optimal_objective
        )

    if kind == "military":
        return evaluate_military(
            topology, rules, selection, optimal_objective=optimal_objective
        )

    raise InvalidScenario(f"Tipo de cenario desconhecido: {kind!r}.")


def verify_solver_solution(
    *,
    kind     : str                     ,
    topology : Mapping[str, Any]       ,
    rules    : Mapping[str, Any]       ,
    solution : Mapping[str, Any] | None,
) -> Verification:
    if not isinstance(solution, Mapping):
        return Verification(False, "O solver nao retornou uma solucao JSON.")

    try:
        if kind == "water":
            selected = solution.get("removed_edge_ids", [])

            if not isinstance(selected, list):
                return Verification(False, "removed_edge_ids ausente ou invalido.")

            selection = {"edge_ids": selected}
        elif kind == "military":
            selected = solution.get("removed_node_ids", [])

            if not isinstance(selected, list):
                return Verification(False, "removed_node_ids ausente ou invalido.")

            selection = {"node_ids": selected}
        else:
            return Verification(False, f"Tipo de cenario desconhecido: {kind!r}.")

        evaluation = evaluate_attempt(
            kind     =kind     ,
            topology =topology ,
            rules    =rules    ,
            selection=selection,
        )
    except DomainValidationError as exc:
        return Verification(False, str(exc))

    if not evaluation["success"]:
        return Verification(False, "A solucao retornada nao satisfaz o objetivo.", evaluation)

    reported = solution.get("objective_value")

    if isinstance(reported, bool) or not isinstance(reported, Real):
        return Verification(False, "objective_value ausente ou invalido.", evaluation)

    if abs(float(reported) - float(evaluation["objective_value"])) > 1e-6:
        return Verification(
            False,
            "O objetivo retornado diverge da avaliacao independente.",
            evaluation                                               ,
        )

    reported_disconnected = solution.get("disconnected_node_ids")

    if not isinstance(reported_disconnected, list) or any(
        not isinstance(node_id, str) for node_id in reported_disconnected
    ):
        return Verification(False, "disconnected_node_ids ausente ou invalido.", evaluation)

    if set(reported_disconnected) != set(evaluation["disconnected_node_ids"]):
        return Verification(
            False,
            "Os nos desconectados retornados divergem da avaliacao independente.",
            evaluation                                                           ,
        )

    return Verification(True, "", evaluation)
