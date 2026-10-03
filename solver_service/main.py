from __future__ import annotations

import os
import time
import logging
import threading

from fastapi import FastAPI      , \
                    HTTPException, \
                    Response     , \
                    status

from .        import __version__
from .schemas import SolveRequest , \
                     SolveResponse, \
                     SolveStats
from .solver  import API_VERSION           , \
                     FORMULATION_VERSIONS  , \
                     SolverExecutionError  , \
                     canonical_input_sha256, \
                     probe_solver          , \
                     solve                 , \
                     solver_metadata


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc

    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")

    return value


def _positive_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc

    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")

    return value


MAX_NODES        = _positive_int_env  ("SOLVER_MAX_NODES"       , 500  )
MAX_EDGES        = _positive_int_env  ("SOLVER_MAX_EDGES"       , 5_000)
MAX_TIME_SECONDS = _positive_float_env("SOLVER_MAX_TIME_SECONDS", 60.0 )
MAX_CONCURRENCY  = _positive_int_env  ("SOLVER_MAX_CONCURRENCY" , 1    )

SOLVE_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENCY)
LOGGER      = logging  .getLogger       (__name__       )


app = FastAPI(
    title    ="Network Resilience MIP Solver",
    version  =API_VERSION,
    docs_url ="/docs"    ,
    redoc_url=None       ,
)


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def health_ready(response: Response) -> dict[str, str]:
    if not SOLVE_SLOTS.acquire(blocking=False):
        return {"status": "ready", "solver": "busy"}

    try:
        ready = probe_solver()
    except Exception:
        ready = False
    finally:
        SOLVE_SLOTS.release()

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return {"status": "not_ready"}

    return {"status": "ready", "solver": "idle"}


@app.get("/version", tags=["metadata"])
def version() -> dict[str, object]:
    return {
        "service_version" : __version__,

        "api_version"  : API_VERSION         ,
        "formulations" : FORMULATION_VERSIONS,
        "solver"       : solver_metadata().model_dump(),
    }


@app.post("/api/v1/solves", response_model=SolveResponse, tags=["solver"])
def solve_problem(request: SolveRequest) -> SolveResponse:
    if len(request.problem.nodes) > MAX_NODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY             ,
            detail     =f"Node count exceeds service limit ({MAX_NODES})",
        )
    if len(request.problem.edges) > MAX_EDGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY             ,
            detail     =f"Edge count exceeds service limit ({MAX_EDGES})",
        )
    if request.limits.time_seconds > MAX_TIME_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY                      ,
            detail     =f"time_seconds exceeds service limit ({MAX_TIME_SECONDS})",
        )

    if not SOLVE_SLOTS.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE            ,
            detail     ="solver concurrency limit reached; retry later",
            headers    ={"Retry-After": "1"},
        )

    started = time.perf_counter()

    try:
        return solve(request)
    except SolverExecutionError as exc:
        LOGGER.error("Solver returned an invalid incumbent: %s", exc)

        return SolveResponse(
            request_id  =request.request_id             ,
            input_sha256=canonical_input_sha256(request),

            formulation_version=FORMULATION_VERSIONS[request.problem.type],

            status            ="error" ,
            termination_reason=str(exc),
            solution          =None    ,

            stats=SolveStats(
                wall_seconds=time.perf_counter() - started,
                node_count  =len(request.problem.nodes)   ,
                edge_count  =len(request.problem.edges)   ,

                variable_count  =0,
                constraint_count=0,
            ),

            solver=solver_metadata(),
        )
    except Exception:
        LOGGER.exception("Unexpected CBC failure for request %s", request.request_id)

        return SolveResponse(
            request_id  =request.request_id             ,
            input_sha256=canonical_input_sha256(request),

            formulation_version=FORMULATION_VERSIONS[request.problem.type],

            status            ="error" ,
            termination_reason=str(exc),
            solution          =None    ,

            stats=SolveStats(
                wall_seconds=time.perf_counter() - started,
                node_count  =len(request.problem.nodes)   ,
                edge_count  =len(request.problem.edges)   ,

                variable_count  =0,
                constraint_count=0,
            ),

            solver=solver_metadata(),
        )
    finally:
        SOLVE_SLOTS.release()
