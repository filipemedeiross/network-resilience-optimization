from __future__ import annotations

import re
import math

from uuid   import UUID
from enum   import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _normalise_identifier(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("an identifier must be a string or integer")

    value = str(value).strip()

    if not value or len(value) > 128 or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            "an identifier must contain 1-128 letters, numbers, '.', '_', ':' or '-'"
        )

    return value


Identifier = Annotated[str, BeforeValidator(_normalise_identifier)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeRole(str, Enum):
    ORDINARY     = "ordinary"
    ORIGIN       = "origin"
    DESTINATION  = "destination"
    HEADQUARTERS = "headquarters"


class ProblemNode(StrictModel):
    id           : Identifier
    role         : NodeRole = NodeRole.ORDINARY
    removable    : bool     = True
    removal_cost : float    = Field(default=1.0, ge=0.0, le=1_000_000.0)

    @model_validator(mode="after")
    def validate_removal_cost(self) -> "ProblemNode":
        if not math.isfinite(self.removal_cost):
            raise ValueError("removal_cost must be finite")

        if self.removable and self.removal_cost <= 0:
            raise ValueError("a removable node must have a positive removal_cost")

        return self


class ProblemEdge(StrictModel):
    id        : Identifier
    source    : Identifier
    target    : Identifier
    removable : bool = True


class BaseProblem(StrictModel):
    nodes: list[ProblemNode] = Field(min_length=2, max_length=1_000 )
    edges: list[ProblemEdge] = Field(min_length=1, max_length=20_000)

    def validate_graph(self) -> None:
        node_ids = [node.id for node in self.nodes]

        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node ids must be unique")

        edge_ids = [edge.id for edge in self.edges]

        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("edge ids must be unique")

        known_nodes                         = set(node_ids)
        endpoint_pairs: set[frozenset[str]] = set()

        adjacency = {
            node_id : set()
            for node_id in node_ids
        }

        for edge in self.edges:
            if edge.source not in known_nodes or edge.target not in known_nodes:
                raise ValueError(f"edge '{edge.id}' refers to an unknown node")

            if edge.source == edge.target:
                raise ValueError(f"edge '{edge.id}' is a self-loop")

            pair = frozenset((edge.source, edge.target))

            if pair in endpoint_pairs:
                raise ValueError("parallel edges are not supported")

            endpoint_pairs        .add(pair)
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)

        visited : set[str] = set()
        pending            = [node_ids[0]]

        while pending:
            node_id = pending.pop()

            if node_id in visited:
                continue

            visited.add   (node_id)
            pending.extend(adjacency[node_id] - visited)

        if visited != known_nodes:
            raise ValueError("the input graph must be connected")


class WaterEdgeCutProblem(BaseProblem):
    type: Literal["water_edge_cut"]

    @model_validator(mode="after")
    def validate_water_problem(self) -> "WaterEdgeCutProblem":
        self.validate_graph()

        origins      = [
            node
            for node in self.nodes
            if  node.role == NodeRole.ORIGIN
        ]
        destinations = [
            node
            for node in self.nodes
            if  node.role == NodeRole.DESTINATION
        ]

        if len(origins) != 1 or len(destinations) != 1:
            raise ValueError(
                "water_edge_cut requires exactly one origin and one destination"
            )

        if any(node.role == NodeRole.HEADQUARTERS for node in self.nodes):
            raise ValueError("headquarters is not a valid role in water_edge_cut")

        return self


class MilitaryVertexInterdictionProblem(BaseProblem):
    type   : Literal["military_vertex_interdiction"]
    budget : float = Field(ge=0.0, le=1_000_000_000.0)

    @model_validator(mode="after")
    def validate_military_problem(self) -> "MilitaryVertexInterdictionProblem":
        self.validate_graph()

        if not math.isfinite(self.budget):
            raise ValueError("budget must be finite")

        headquarters = [
            node
            for node in self.nodes
            if  node.role == NodeRole.HEADQUARTERS
        ]

        if len(headquarters) != 1:
            raise ValueError(
                "military_vertex_interdiction requires exactly one headquarters"
            )

        if headquarters[0].removable:
            raise ValueError("the headquarters node must have removable=false")

        if any(
            node.role in (
                NodeRole.ORIGIN     ,
                NodeRole.DESTINATION,
            )
            for node in self.nodes
        ):
            raise ValueError(
                "origin and destination are not valid roles in military_vertex_interdiction"
            )

        return self


Problem = Annotated[
    WaterEdgeCutProblem | MilitaryVertexInterdictionProblem, Field(discriminator="type"),
]


class SolveLimits(StrictModel):
    time_seconds: float = Field(default=10.0, gt=0.0, le=300.0)

    @model_validator(mode="after")
    def validate_finite_time(self) -> "SolveLimits":
        if not math.isfinite(self.time_seconds):
            raise ValueError("time_seconds must be finite")

        return self


class SolveRequest(StrictModel):
    request_id     : UUID
    schema_version : Literal[1]
    problem        : Problem
    limits         : SolveLimits = Field(default_factory=SolveLimits)


class SolveSolution(StrictModel):
    objective_value       : float
    best_bound            : float | None = None
    gap                   : float | None = None
    removed_node_ids      : list[str] = Field(default_factory=list)
    removed_edge_ids      : list[str] = Field(default_factory=list)
    disconnected_node_ids : list[str] = Field(default_factory=list)


class SolveStats(StrictModel):
    wall_seconds     : float
    node_count       : int
    edge_count       : int
    variable_count   : int
    constraint_count : int


class SolverMetadata(StrictModel):
    name            : str
    library         : str
    library_version : str


class SolveResponse(StrictModel):
    request_id     : UUID
    schema_version : Literal[1] = 1

    input_sha256        : str
    formulation_version : str

    status             : Literal["optimal", "feasible", "infeasible", "no_solution", "error"]
    termination_reason : str

    solution : SolveSolution | None = None
    stats    : SolveStats
    solver   : SolverMetadata
