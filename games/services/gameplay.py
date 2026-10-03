from __future__ import annotations

import uuid

from typing          import Any
from collections.abc import Callable, Mapping

from django.db.models import F
from django.utils     import timezone
from django.db        import IntegrityError, transaction

from games.models import Attempt         , \
                         Play            , \
                         Scenario        , \
                         ScenarioRevision, \
                         SolverResult

from games.services.publication import verified_optimal_result
from games.services.evaluation  import InvalidSelection           , evaluate_attempt
from games.services.instances   import InstanceGenerationExhausted, provision_generated_instance


class GameplayError(RuntimeError):
    pass

class PlayNotFound(GameplayError):
    pass

class VersionConflict(GameplayError):
    def __init__(
        self,
        current_version: int ,
        current_status : str ,
        revealed       : bool,
    ):
        self.current_version = current_version
        self.current_status  = current_status
        self.revealed        = revealed

        super().__init__(f"Versao esperada diverge da versao atual ({current_version}).")

class SolutionUnavailable(GameplayError):
    pass


CONCURRENT_INSTANCE_RETRIES = 3


def create_play(
    *,
    revision      : ScenarioRevision,
    visitor_id    : uuid.UUID       | str        ,
    solver_result : SolverResult    | None = None,
) -> Play:
    if revision.publication_status not in {
        ScenarioRevision.PublicationStatus.PUBLISHED,
        ScenarioRevision.PublicationStatus.SOLVING  ,
    }:
        raise SolutionUnavailable("O cenario ainda nao possui solucao publicada.")

    optimum = solver_result or verified_optimal_result(revision)

    if optimum is None:
        raise SolutionUnavailable("O cenario nao possui gabarito otimo verificado.")

    if (
            optimum.revision_id != revision.pk                 or
            optimum.status      != SolverResult.Status.OPTIMAL or
        not optimum.verified
    ):
        raise SolutionUnavailable("O gabarito verificado nao pertence ao cenario.")

    return Play.objects.create(
        revision     =revision  ,
        solver_result=optimum   ,
        visitor_id   =visitor_id,
    )


def create_next_play(
    *,
    scenario_id,

    visitor_id  : uuid.UUID         | str        ,
    seed_factory: Callable[[], int] | None = None,
    client      : Any               | None = None,
    max_attempts: int               | None = None,
) -> Play:
    used_hashes: set[str] = set()

    for _ in range(CONCURRENT_INSTANCE_RETRIES):
        used_hashes.update(
            Play.objects.filter(
                visitor_id           =visitor_id ,
                revision__scenario_id=scenario_id,
            ).values_list(
                "revision__content_hash", flat=True
            )
        )

        provisioned = provision_generated_instance(
            scenario_id            =scenario_id ,
            excluded_content_hashes=used_hashes ,
            seed_factory           =seed_factory,
            client                 =client      ,
            max_attempts           =max_attempts,
        )

        with transaction.atomic():
            try:
                Scenario.objects.select_for_update().only("pk").get(
                    pk    =scenario_id,
                    active=True       ,
                )
            except Scenario.DoesNotExist as exc:
                raise SolutionUnavailable("O cenario nao esta disponivel.") from exc

            already_used = Play.objects.filter(
                visitor_id            =visitor_id ,
                revision__scenario_id =scenario_id,
                revision__content_hash=provisioned.revision.content_hash,
            ).exists()

            if not already_used:
                return create_play(
                    revision     =provisioned.revision     ,
                    solver_result=provisioned.solver_result,
                    visitor_id   =visitor_id,
                )

        used_hashes.add(provisioned.revision.content_hash)

    raise InstanceGenerationExhausted


def restart_play(
    *,

    play_id   : uuid.UUID | str,
    visitor_id: uuid.UUID | str,

    seed_factory: Callable[[], int] | None = None,
    client      : Any               | None = None,
    max_attempts: int               | None = None,
) -> Play:
    current = _owned_play(play_id, visitor_id)

    return create_next_play(
        scenario_id =current.revision.scenario_id,

        visitor_id  =visitor_id  ,
        seed_factory=seed_factory,
        client      =client      ,
        max_attempts=max_attempts,
    )


def _owned_play(play_id: uuid.UUID | str, visitor_id: uuid.UUID | str) -> Play:
    try:
        return Play.objects.select_related("revision__scenario", "solver_result").get(
            pk        =play_id   ,
            visitor_id=visitor_id,
        )
    except (Play.DoesNotExist, ValueError, TypeError) as exc:
        raise PlayNotFound("Partida inexistente para este visitante.") from exc


def _normalized_selection(result: Mapping[str, Any]) -> dict[str, list[str]]:
    if "selected_edge_ids" in result:
        return {"edge_ids": list(result["selected_edge_ids"])}

    return {"node_ids": list(result["selected_node_ids"])}


def submit_attempt(
    *,

    play_id   : uuid.UUID | str,
    visitor_id: uuid.UUID | str,
    request_id: uuid.UUID | str,

    expected_version: int              ,
    selection       : Mapping[str, Any],
) -> Attempt:
    play = _owned_play(play_id, visitor_id)

    try:
        request_uuid = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidSelection("request_id deve ser um UUID valido.") from exc

    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise InvalidSelection("expected_version deve ser inteiro.")

    optimum = play.solver_result

    if optimum is None or optimum.objective_value is None:
        raise SolutionUnavailable("O cenario nao possui gabarito otimo verificado.")

    result = evaluate_attempt(
        kind             =play.revision.scenario.kind,
        topology         =play.revision.topology     ,
        rules            =play.revision.rules        ,
        selection        =selection                  ,
        optimal_objective=optimum.objective_value    ,
    )

    normalized_selection = _normalized_selection(result)

    existing = Attempt.objects.filter(
        play      =play        ,
        request_id=request_uuid,
    ).first()

    if existing is not None:
        if (
            existing.expected_version != expected_version    or
            existing.selection        != normalized_selection
        ):
            raise InvalidSelection("request_id ja foi usado com outra tentativa.")

        existing.play = play

        return existing

    with transaction.atomic():
        updated = Play.objects.filter(
            pk        =play.pk           ,
            visitor_id=visitor_id        ,
            version   =expected_version  ,
            status    =Play.Status.ACTIVE,
        ).update(
            version=F("version") + 1,
            status =(
                Play.Status.COMPLETED
                if   result["is_optimal"]
                else Play.Status.ACTIVE
            ),
            updated_at=timezone.now(),
        )

        if updated != 1:
            current = Play.objects.filter(
                pk        =play.pk   ,
                visitor_id=visitor_id,
            ).first()

            if current is None:
                raise PlayNotFound("Partida inexistente para este visitante.")

            retry = Attempt.objects.filter(
                play      =play        ,
                request_id=request_uuid,
            ).first()

            if retry is not None:
                if (
                    retry.expected_version != expected_version    or
                    retry.selection        != normalized_selection
                ):
                    raise InvalidSelection("request_id ja foi usado com outra tentativa.")

                retry.play = current

                return retry

            raise VersionConflict(current.version, current.status, current.revealed)

        try:
            with transaction.atomic():
                attempt = Attempt.objects.create(
                    play      =play        ,
                    request_id=request_uuid,

                    expected_version =expected_version    ,
                    committed_version=expected_version + 1,

                    selection=normalized_selection,
                    result   =result              ,
                )
        except IntegrityError:
            attempt = Attempt.objects.get(play=play, request_id=request_uuid)

    play.refresh_from_db()

    attempt.play = play

    return attempt


def reveal_play(
    *,

    play_id         : uuid.UUID | str,
    visitor_id      : uuid.UUID | str,
    expected_version: int,
) -> Play:
    play    = _owned_play(play_id, visitor_id)

    optimum = play.solver_result

    if optimum is None:
        raise SolutionUnavailable("O cenario nao possui gabarito otimo verificado.")
    if play.revealed:
        play.revealed_solution = optimum.solution

        return play

    with transaction.atomic():
        updated = Play.objects.filter(
            pk        =play.pk         ,
            visitor_id=visitor_id      ,
            version   =expected_version,
            revealed  =False,
        ).update(
            version   =F("version") + 1    ,
            status    =Play.Status.REVEALED,
            revealed  =True          ,
            updated_at=timezone.now(),
        )

        if updated != 1:
            current = Play.objects.filter(pk=play.pk, visitor_id=visitor_id).first()

            if current is None:
                raise PlayNotFound("Partida inexistente para este visitante.")

            if current.revealed:
                current.revealed_solution = optimum.solution

                return current

            raise VersionConflict(current.version, current.status, current.revealed)

    play.refresh_from_db()

    play.revealed_solution = optimum.solution

    return play
