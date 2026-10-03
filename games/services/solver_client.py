from __future__ import annotations

import json
import time
import uuid
import hashlib
import requests

from typing          import Any
from collections.abc import Mapping
from urllib.parse    import urljoin
from django.conf     import settings

from games.services.evaluation import validate_scenario


class SolverServiceError (RuntimeError      ):
    pass

class SolverUnavailable  (SolverServiceError):
    pass

class SolverProtocolError(SolverServiceError):
    pass


def build_problem(
    *,

    kind    : str,
    topology: Mapping[str, Any],
    rules   : Mapping[str, Any],
) -> dict[str, Any]:
    validate_scenario(kind, topology, rules)

    special_roles = (
        {"origin", "destination"}
        if   kind == "water"
        else {"headquarters"}
    )

    nodes = [
        {
            "id" : node["id"],

            "role" : (
                     node.get("role")
                if   node.get("role") in special_roles
                else "ordinary"
            ),

            "removable"    : node.get("removable"   , False) if kind == "military" else False,
            "removal_cost" : node.get("removal_cost", 0    ) if kind == "military" else 0    ,
        }
        for node in topology["nodes"]
    ]
    edges = [
        {
            "id" : edge["id"],

            "source"    : edge["source"],
            "target"    : edge["target"],
            "removable" : edge.get("removable", False),
        }
        for edge in topology["edges"]
    ]

    problem : dict[str, Any] = {
        "type" : (
            "water_edge_cut"
            if   kind == "water"
            else "military_vertex_interdiction"
        ),

        "nodes" : nodes,
        "edges" : edges,
    }

    if kind == "military":
        problem["budget"] = rules["budget"]

    return problem


def solver_input_sha256(*, problem: Mapping[str, Any], schema_version: int) -> str:
    normalized: dict[str, Any] = {
        "type" : problem["type"],

        "nodes" : sorted(
            (
                {
                    "id"           : str  (node["id"]),
                    "role"         : str  (node.get("role"        , "ordinary")),
                    "removable"    : bool (node.get("removable"   , True      )),
                    "removal_cost" : float(node.get("removal_cost", 1         )),
                }
                for node in problem["nodes"]
            ),
            key=lambda node: node["id"],
        ),

        "edges" : sorted(
            (
                {
                    "id"        : str (edge["id"    ]),
                    "source"    : str (edge["source"]),
                    "target"    : str (edge["target"]),
                    "removable" : bool(edge.get("removable", True)),
                }
                for edge in problem["edges"]
            ),
            key=lambda edge: edge["id"],
        ),
    }

    if problem["type"] == "military_vertex_interdiction":
        normalized["budget"] = float(problem["budget"])

    encoded = json.dumps(
        {
            "schema_version" : schema_version,
            "problem"        : normalized    ,
        },

        sort_keys   =True      ,
        separators  =(",", ":"),
        ensure_ascii=False     ,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


class SolverClient:
    def __init__(
        self,
        *,

        base_url       : str              | None = None,
        connect_timeout: float            | None = None,
        read_timeout   : float            | None = None,
        busy_retries   : int              | None = None,
        session        : requests.Session | None = None,
    ):
        self.base_url = (
            base_url or settings.SOLVER_SERVICE_URL
        ).rstrip("/") + "/"

        self.connect_timeout = (
            connect_timeout
            if   connect_timeout is not None
            else settings.SOLVER_CONNECT_TIMEOUT
        )

        self.read_timeout = (
            read_timeout
            if   read_timeout is not None
            else settings.SOLVER_READ_TIMEOUT
        )
        self.busy_retries = (
            busy_retries
            if   busy_retries is not None
            else settings.SOLVER_BUSY_RETRIES
        )

        if self.busy_retries < 0:
            raise ValueError("busy_retries nao pode ser negativo.")

        self.session = session or requests.Session()

    def solve(
        self,
        *,

        kind : str,

        topology           : Mapping[str, Any],
        rules              : Mapping[str, Any],
        request_id         : uuid.UUID | str | None = None,
        time_limit_seconds : float           | None = None,
        schema_version     : int = 1                      ,
    ) -> dict[str, Any]:
        request_id = str(request_id or uuid.uuid4())

        limit = (
            time_limit_seconds
            if   time_limit_seconds is not None
            else settings.SOLVER_TIME_LIMIT
        )

        problem = build_problem(
            kind    =kind    ,
            topology=topology,
            rules   =rules   ,
        )

        payload = {
            "request_id"     : request_id    ,
            "schema_version" : schema_version,

            "problem" : problem,
            "limits"  : {"time_seconds": limit},
        }

        expected_input_hash = solver_input_sha256(
            problem       =problem       ,
            schema_version=schema_version,
        )

        effective_read_timeout = max(self.read_timeout, float(limit) + 2.0)

        for attempt in range(self.busy_retries + 1):
            try:
                response = self.session.post(
                    urljoin(
                        self.base_url, "api/v1/solves"
                    ),

                    json   =payload,
                    timeout=(
                        self.connect_timeout  ,
                        effective_read_timeout,
                    ),
                )
            except requests.Timeout as exc:
                raise SolverUnavailable("O servico MIP excedeu o timeout.") from exc
            except requests.RequestException as exc:
                raise SolverUnavailable(
                    "Nao foi possivel conectar ao servico MIP."
                ) from exc

            if response.status_code != 503 or attempt == self.busy_retries:
                break

            headers = getattr(response, "headers", {})

            try:
                retry_after = float(headers.get("Retry-After", "1"))
            except (TypeError, ValueError):
                retry_after = 1.0

            time.sleep(min(2.0, max(0.1, retry_after)))

        if response.status_code != 200:
            body = response.text[:500]

            raise SolverServiceError(
                f"O servico MIP respondeu HTTP {response.status_code}: {body}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise SolverProtocolError("O servico MIP retornou JSON invalido.") from exc

        if not isinstance(data, dict):
            raise SolverProtocolError("A resposta do servico MIP deve ser um objeto JSON.")
        if str(data.get("request_id")) != request_id:
            raise SolverProtocolError("request_id divergente na resposta do servico MIP.")

        if data.get("status") not in {
            "optimal"    ,
            "feasible"   ,
            "infeasible" ,
            "no_solution",
            "error"      ,
        }:
            raise SolverProtocolError("Status desconhecido na resposta do servico MIP.")

        if data.get("schema_version") != schema_version     :
            raise SolverProtocolError("schema_version divergente na resposta do servico MIP.")
        if data.get("input_sha256"  ) != expected_input_hash:
            raise SolverProtocolError("input_sha256 divergente na resposta do servico MIP."  )

        return data
