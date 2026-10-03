from __future__ import annotations

import random
import secrets

from typing      import Any
from dataclasses import dataclass

from games.services.evaluation import validate_scenario


GENERATOR_VERSION = "1.0.0"

DEFAULT_INSTANCES_PER_KIND = 6
WATER_BASE_SEED            = 202400
MILITARY_BASE_SEED         = 202402
MAX_SCENARIO_SEED          = (2**63) - 1
DEFAULT_MILITARY_BUDGET    = 6


@dataclass(frozen=True)
class ScenarioSpec:
    slug : str
    title: str
    kind : str
    seed : int

    topology    : dict[str, Any]
    rules       : dict[str, Any]
    presentation: dict[str, Any]

    schema_version     : int = 1
    generator_version  : str = GENERATOR_VERSION
    formulation_version: str = "1"


def random_scenario_seed() -> int:
    return secrets.randbelow(MAX_SCENARIO_SEED + 1)


class _UnionFind:
    def __init__(self, values: list[str]):
        self.parent     = {
            value : value
            for value in values
        }

        self.components = len(values)

    def find(self, value: str) -> str:
        root = value

        while self.parent[root] != root:
            root = self.parent[root]

        while self.parent[value] != value:
            parent             = self.parent[value]
            self.parent[value] = root

            value = parent

        return root

    def union(self, left: str, right: str) -> bool:
        left_root  = self.find(left )
        right_root = self.find(right)

        if left_root == right_root:
            return False

        self.parent[right_root] = left_root
        self.components        -= 1

        return True


def _node_id(row: int, column: int) -> str:
    return f"n-r{row:02d}-c{column:02d}"

def _edge_id(source: str, target: str) -> str:
    left, right = sorted((source, target))

    return f"e:{left}--{right}"

def _grid_candidates(rows: int, columns: int) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    for row in range(rows):
        for column in range(columns):
            node = _node_id(row, column)

            if column + 1 < columns:
                candidates.append((node, _node_id(row, column + 1)))
            if row + 1 < rows:
                candidates.append((node, _node_id(row + 1, column)))

    return candidates


def _connected_edges(rows: int, columns: int, rng: random.Random) -> list[tuple[str, str]]:
    node_ids   = [
        _node_id(row, column)
        for row    in range(rows   )
        for column in range(columns)
    ]

    candidates = _grid_candidates(rows, columns)

    rng.shuffle(candidates)

    forest                          = _UnionFind(node_ids)
    selected: list[tuple[str, str]] = []

    for source, target in candidates:
        selected.append((source, target))
        forest  .union ( source, target )

        if forest.components == 1:
            break

    return sorted(
        (
            min(source, target),
            max(source, target),
        )
        for source, target in selected
    )


def _positions(rows: int, columns: int) -> dict[str, dict[str, float]]:
    return {
        _node_id(row, column): {
            "x" : column / (columns - 1) if columns > 1 else 0.5,
            "y" : row    / (rows    - 1) if rows    > 1 else 0.5,
        }
        for row    in range(rows   )
        for column in range(columns)
    }


def _distant_pair(rows: int, columns: int, rng: random.Random) -> tuple[str, str]:
    coordinates      = [
        (row, column)
        for row    in range(rows   )
        for column in range(columns)
    ]

    minimum_distance = max(2, (rows + columns) // 2)

    valid_pairs = [
        (left, right)
        for index, left in enumerate(coordinates)
        for right       in coordinates[index + 1 :]
        if  abs(left[0] - right[0]) + abs(left[1] - right[1]) >= minimum_distance
    ]

    origin, destination = rng.choice(valid_pairs)

    return _node_id(*origin), _node_id(*destination)


def generate_water_scenario(
    *, seed: int = 202400, rows: int = 10, columns: int = 10
) -> ScenarioSpec:
    rng = random.Random(seed)

    origin, destination = _distant_pair   (rows, columns, rng)
    selected_edges      = _connected_edges(rows, columns, rng)

    node_ids = [
        _node_id(row, column)
        for row    in range(rows   )
        for column in range(columns)
    ]
    nodes    = [
        {
            "id" : node_id,

            "role" : (
                "origin"
                if   node_id == origin
                else "destination"
                if   node_id == destination
                else "ordinary"
            ),

            "removable"    : False,
            "removal_cost" : 0    ,
        }
        for node_id in node_ids
    ]
    edges = [
        {
            "id" : _edge_id(source, target),

            "source" : source,
            "target" : target,

            "removable" : source not in {origin, destination} and target not in {origin, destination},
        }
        for source, target in selected_edges
    ]
    topology = {
        "directed" : False,

        "grid" : {
            "rows"    : rows   ,
            "columns" : columns,
        },

        "nodes" : nodes,
        "edges" : edges,
    }

    rules = {"objective": "minimize_removed_edges"}

    presentation = {
        "positions" : _positions(rows, columns),
        "labels"    : {
            "origin"      : "Origem" ,
            "destination" : "Destino",
        },
    }

    validate_scenario("water", topology, rules)

    return ScenarioSpec(
        slug ="rede-de-distribuicao-de-agua",
        title="Rede de distribuição de água",
        kind ="water",

        seed               =seed        ,
        topology           =topology    ,
        rules              =rules       ,
        presentation       =presentation,
        formulation_version="water-edge-cut/1.0",
    )


def generate_military_scenario(
    *,

    seed   : int = 202402,
    rows   : int = 10    ,
    columns: int = 10    ,
    budget : int = DEFAULT_MILITARY_BUDGET,
) -> ScenarioSpec:
    rng = random.Random(seed)

    selected_edges = _connected_edges(rows, columns, rng)

    node_ids = [
        _node_id(row, column)
        for row    in range(rows   )
        for column in range(columns)
    ]

    headquarters = rng.choice(node_ids)

    neighbours = {
        target if source == headquarters else source
        for source, target in selected_edges
        if  headquarters in {source, target}
    }

    costs = {
        node_id : rng.choices(
            (1, 2, 3), weights=(2, 2, 6), k=1
        )[0]
        for node_id in node_ids
    }
    nodes = [
        {
            "id" : node_id,

            "role"         : "headquarters" if node_id == headquarters else "ordinary",
            "removable"    : node_id != headquarters and node_id not in neighbours    ,
            "removal_cost" : costs[node_id],
        }
        for node_id in node_ids
    ]
    edges = [
        {
            "id" : _edge_id(source, target),

            "source" : source,
            "target" : target,

            "removable" : False,
        }
        for source, target in selected_edges
    ]
    topology = {
        "directed" : False,

        "grid" : {
            "rows"    : rows   ,
            "columns" : columns,
        },

        "nodes" : nodes,
        "edges" : edges,
    }

    rules = {
        "objective" : "maximize_disconnected_units",
        "budget"    : budget                       ,
    }
    presentation = {
        "positions" : _positions(rows, columns),
        "labels"    : {
            "headquarters" : "Quartel-general",
            "ordinary"     : "Unidade"        ,
        },
    }

    validate_scenario("military", topology, rules)

    return ScenarioSpec(
        slug ="rede-de-distribuicao-militar",
        title="Rede de distribuição militar",
        kind ="military",

        seed               =seed        ,
        topology           =topology    ,
        rules              =rules       ,
        presentation       =presentation,
        formulation_version="military-vertex-interdiction/1.0",
    )


def generate_scenario_instance(*, kind: str, seed: int) -> ScenarioSpec:
    if (
            isinstance(seed, bool) or
        not isinstance(seed, int ) or
        not 0 <= seed <= MAX_SCENARIO_SEED
    ):
        raise ValueError("seed deve ser um inteiro de 0 a 2^63 - 1.")

    if kind == "water":
        return generate_water_scenario   (seed=seed)
    if kind == "military":
        return generate_military_scenario(seed=seed)

    raise ValueError(f"Tipo de cenario desconhecido: {kind!r}.")


def generate_known_scenarios(
    *,

    instances_per_kind: int = DEFAULT_INSTANCES_PER_KIND,
) -> tuple[ScenarioSpec, ...]:
    if (
            isinstance(instances_per_kind, bool) or
        not isinstance(instances_per_kind, int ) or
        instances_per_kind < 1
    ):
        raise ValueError("instances_per_kind deve ser um inteiro positivo.")

    water    = tuple(
        generate_water_scenario(
            seed=WATER_BASE_SEED + offset
        )
        for offset in range(instances_per_kind)
    )
    military = tuple(
        generate_military_scenario(
            seed=MILITARY_BASE_SEED + offset
        )
        for offset in range(instances_per_kind)
    )

    return water + military
