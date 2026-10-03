from __future__ import annotations

import math
import uuid

from typing   import Any
from numbers  import Real
from datetime import datetime

from django.utils import timezone
from django.db    import connection, transaction

from games.models                 import ScenarioRevision, SolverResult
from games.services.solver_client import SolverClient    , SolverServiceError
from games.services.evaluation    import Verification    , verify_solver_solution


class PublicationError(RuntimeError):
    pass

class RevisionAlreadySolving(PublicationError):
    pass

class RevisionAlreadyPublished(PublicationError):
    pass

class RevisionClaimLost(PublicationError):
    pass


def verified_optimal_result(revision: ScenarioRevision) -> SolverResult | None:
    return (
        revision.solver_results.filter(
            status  =SolverResult.Status.OPTIMAL,
            verified=True                       ,
        )
        .order_by("-created_at")
        .first   ()
    )


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None

    value = float(value)

    return value if math.isfinite(value) else None


def _duration_ms(response: dict[str, Any]) -> int:
    stats = response.get("stats")

    if not isinstance(stats, dict):
        return 0

    seconds = _optional_float(stats.get("wall_seconds"))

    return max(0, round(seconds * 1000)) if seconds is not None else 0


def _verification(
    revision: ScenarioRevision, response: dict[str, Any]
) -> Verification:
    if response.get("formulation_version") != revision.formulation_version:
        return Verification(
            False, "A versao da formulacao retornada diverge da revisao.",
        )

    status = response.get("status")

    if status not in {
        SolverResult.Status.OPTIMAL ,
        SolverResult.Status.FEASIBLE,
    }:
        return Verification(
            False, str(response.get("termination_reason") or "O solver nao retornou solucao."),
        )

    solution = response.get("solution")

    verification = verify_solver_solution(
        kind    =revision.scenario.kind,
        topology=revision.topology     ,
        rules   =revision.rules        ,
        solution=solution,
    )

    if not verification.verified or status != SolverResult.Status.OPTIMAL:
        return verification

    objective  = _optional_float(solution.get("objective_value"))
    best_bound = _optional_float(solution.get("best_bound"     ))
    gap        = _optional_float(solution.get("gap"            ))

    if objective is None or best_bound is None or gap is None:
        return Verification(False, "Resultado otimo sem objetivo, bound ou gap valido.")

    tolerance = 1e-6 * max(1.0, abs(objective))

    if abs(best_bound - objective) > tolerance or abs(gap) > 1e-6:
        return Verification(False, "Status otimo diverge do bound ou gap retornado.")

    return verification


def _finish_claim(
    revision : ScenarioRevision,
    *,

    claimed_at         : datetime,
    publication_status : str     ,
) -> None:
    updated = ScenarioRevision.objects.filter(
        pk                =revision.pk                               ,
        publication_status=ScenarioRevision.PublicationStatus.SOLVING,
        updated_at        =claimed_at,
    ).update(
        publication_status=publication_status,
        updated_at        =timezone.now()    ,
    )

    if updated != 1:
        raise RevisionClaimLost(f"A reserva de resolucao de {revision} nao e mais valida.")


def recover_stale_revisions(*, stale_before: datetime) -> int:
    stale = ScenarioRevision.objects.filter(
        publication_status=ScenarioRevision.PublicationStatus.SOLVING,
        updated_at__lt    =stale_before                              ,
    )

    recovered = 0

    for revision in stale.iterator():
        fallback = (
            ScenarioRevision.PublicationStatus.PUBLISHED
            if   verified_optimal_result(revision) is not None
            else ScenarioRevision.PublicationStatus.FAILED
        )

        recovered += ScenarioRevision.objects.filter(
            pk                =revision.pk,
            publication_status=ScenarioRevision.PublicationStatus.SOLVING,
            updated_at__lt    =stale_before                              ,
        ).update(
            publication_status=fallback      ,
            updated_at        =timezone.now(),
        )

    return recovered


def publish_revision(
    revision_id: uuid.UUID | str,
    *,

    client             : SolverClient | None = None,
    time_limit_seconds : float        | None = None,

    force: bool = False,
) -> SolverResult:
    if connection.in_atomic_block:
        raise PublicationError(
            "publish_revision nao pode ser chamado dentro de transaction.atomic()."
        )

    revision = ScenarioRevision.objects.select_related("scenario").get(pk=revision_id)
    allowed  = [
        ScenarioRevision.PublicationStatus.DRAFT ,
        ScenarioRevision.PublicationStatus.FAILED,
    ]

    if force:
        allowed.append(ScenarioRevision.PublicationStatus.PUBLISHED)

    was_published = (
        revision.publication_status == ScenarioRevision.PublicationStatus.PUBLISHED
    )

    claimed_at = timezone.now()

    claimed = ScenarioRevision.objects.filter(
        pk                    =revision.pk,
        publication_status__in=allowed    ,
    ).update(
        publication_status=ScenarioRevision.PublicationStatus.SOLVING,
        updated_at        =claimed_at                                ,
    )

    if not claimed:
        current = ScenarioRevision.objects.only("publication_status").get(pk=revision.pk)

        if current.publication_status == ScenarioRevision.PublicationStatus.SOLVING  :
            raise RevisionAlreadySolving (f"{revision} ja esta em resolucao.")
        if current.publication_status == ScenarioRevision.PublicationStatus.PUBLISHED:
            raise RevisionAlreadyPublished(f"{revision} ja esta publicado."  )

        raise PublicationError(f"Nao foi possivel reservar {revision} para resolucao.")

    request_id = uuid.uuid4()

    client = client or SolverClient()

    fallback_status = (
        ScenarioRevision.PublicationStatus.PUBLISHED
        if   was_published
        else ScenarioRevision.PublicationStatus.FAILED
    )

    finalized = False
    try:
        try:
            response = client.solve(
                kind              =revision.scenario.kind ,
                topology          =revision.topology      ,
                rules             =revision.rules         ,
                request_id        =request_id             ,
                time_limit_seconds=time_limit_seconds     ,
                schema_version    =revision.schema_version,
            )
        except SolverServiceError as exc:
            with transaction.atomic():
                result = SolverResult.objects.create(
                    revision  =revision  ,
                    request_id=request_id,

                    status            =SolverResult.Status.ERROR,
                    termination_reason="client_error"           ,
                    solution          ={}                       ,

                    api_version        =revision.schema_version     ,
                    formulation_version=revision.formulation_version,

                    verified          =False   ,
                    verification_error=str(exc),
                )

                _finish_claim(
                    revision,
                    claimed_at        =claimed_at     ,
                    publication_status=fallback_status,
                )

            finalized = True

            return result

        solution = response.get("solution")
        solution = solution if isinstance(solution, dict) else {}

        verification = _verification(revision, response)

        solver          = response.get("solver") if isinstance(response.get("solver"), dict) else {}
        objective_value = _optional_float(solution.get("objective_value"))

        status         = response["status"]
        should_publish = status == SolverResult.Status.OPTIMAL and verification.verified

        with transaction.atomic():
            result = SolverResult.objects.create(
                revision  =revision  ,
                request_id=request_id,

                status            =status         ,
                termination_reason="client_error" ,
                objective_value   =objective_value,

                best_bound=_optional_float(solution.get("best_bound")),
                gap       =_optional_float(solution.get("gap"       )),

                solution=solution,

                api_version   =int(response.get("schema_version", revision.schema_version)),
                solver_name   =str(solver.get("name") or solver.get("library") or "")[:100],
                solver_version=str(solver.get("library_version") or "")[:100],

                formulation_version=str(
                    response.get("formulation_version") or revision.formulation_version
                )[:64],

                duration_ms=_duration_ms(response),
                input_hash =str(response.get("input_sha256") or "")[:64],

                verified          =verification.verified,
                verification_error=verification.error   ,
            )

            _finish_claim(
                revision,

                claimed_at        =claimed_at,
                publication_status=(
                    ScenarioRevision.PublicationStatus.PUBLISHED
                    if   should_publish
                    else fallback_status
                ),
            )

        finalized = True

        return result
    finally:
        if not finalized:
            ScenarioRevision.objects.filter(
                pk                =revision.pk,
                publication_status=ScenarioRevision.PublicationStatus.SOLVING,
                updated_at        =claimed_at                                ,
            ).update(
                publication_status=fallback_status,
                updated_at        =timezone.now() ,
            )


solve_and_publish_revision = publish_revision
