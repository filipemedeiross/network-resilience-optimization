from __future__ import annotations

from typing          import Any
from dataclasses     import dataclass
from datetime        import timedelta
from collections.abc import Callable, Collection

from django.db.models import Max
from django.conf      import settings
from django.utils     import timezone
from django.db        import IntegrityError, transaction

from games.models import Scenario        , \
                         ScenarioRevision, \
                         SolverResult

from games.services.canonical  import revision_content_hash

from games.services.generation import ScenarioSpec              , \
                                      generate_scenario_instance, \
                                      random_scenario_seed

from games.services.publication import PublicationError        , \
                                       RevisionAlreadyPublished, \
                                       RevisionAlreadySolving  , \
                                       publish_revision        , \
                                       verified_optimal_result

from games.services.solver_client import SolverClient


class InstanceProvisionError(RuntimeError):
    public_message = "Não foi possível preparar uma nova instância. Tente novamente."

class InstanceGenerationExhausted(InstanceProvisionError):
    public_message = "Não foi possível gerar uma instância inédita após várias tentativas."

class InstanceResolutionInProgress(InstanceProvisionError):
    public_message = "Esta instância já está sendo resolvida. Tente novamente em instantes."

class InstanceResolutionFailed(InstanceProvisionError):
    public_message = "O serviço MIP não conseguiu verificar a nova instância. Tente novamente."


@dataclass(frozen=True)
class ProvisionedInstance:
    revision     : ScenarioRevision
    solver_result: SolverResult
    cache_hit    : bool


def _content_hash(spec: ScenarioSpec) -> str:
    return revision_content_hash(
        topology           =spec.topology           ,
        rules              =spec.rules              ,
        schema_version     =spec.schema_version     ,
        formulation_version=spec.formulation_version,
    )


def generate_candidate(
    *,

    kind: str,

    excluded_content_hashes: Collection[str] = (),
    seed_factory           : Callable[[], int] | None = None,
    max_attempts           : int               | None = None,
) -> tuple[ScenarioSpec, str]:
    seed_factory = seed_factory or random_scenario_seed

    if max_attempts is None:
        max_attempts = settings.INSTANCE_GENERATION_MAX_ATTEMPTS

    if (
            isinstance(max_attempts, bool) or
        not isinstance(max_attempts, int ) or
        max_attempts < 1
    ):
        raise ValueError("max_attempts deve ser um inteiro positivo.")

    excluded = set(excluded_content_hashes)

    for _ in range(max_attempts):
        spec = generate_scenario_instance(kind=kind, seed=seed_factory())

        content_hash = _content_hash(spec)
        if content_hash not in excluded:
            return spec, content_hash

    raise InstanceGenerationExhausted


def get_or_create_revision_for_spec(
    *,

    scenario_id  : int         ,
    spec         : ScenarioSpec,
    content_hash : str         ,
) -> tuple[ScenarioRevision, bool]:
    if content_hash != _content_hash(spec):
        raise InstanceProvisionError("O hash informado diverge da instancia gerada.")

    with transaction.atomic():
        try:
            scenario = (
                Scenario
                .objects
                .select_for_update()
                .only             ("id", "kind", "active"     )
                .get              (pk=scenario_id, active=True)
            )
        except Scenario.DoesNotExist as exc:
            raise InstanceProvisionError("Cenario indisponivel.") from exc

        if scenario.kind != spec.kind:
            raise InstanceProvisionError("O tipo gerado diverge do cenario.")

        existing = (
            ScenarioRevision
            .objects
            .filter(
                scenario_id =scenario.pk ,
                content_hash=content_hash,
            )
            .order_by("-revision")
            .first   ()
        )

        if existing is not None:
            return existing, False

        latest = (
            ScenarioRevision
            .objects
            .filter   (scenario_id=scenario.pk)
            .aggregate(value=Max("revision")  )["value"]
            or 0
        )

        try:
            with transaction.atomic():
                revision = ScenarioRevision.objects.create(
                    scenario=scenario  ,
                    revision=latest + 1,

                    topology           =spec.topology           ,
                    rules              =spec.rules              ,
                    presentation       =spec.presentation       ,
                    schema_version     =spec.schema_version     ,
                    generator_version  =spec.generator_version  ,
                    formulation_version=spec.formulation_version,

                    seed        =spec.seed   ,
                    content_hash=content_hash,
                )
        except IntegrityError:
            revision = ScenarioRevision.objects.filter(
                scenario_id =scenario.pk ,
                content_hash=content_hash,
            ).first()

            if revision is None:
                raise

            return revision, False

        return revision, True


def _release_stale_claim(revision: ScenarioRevision) -> bool:
    stale_before = timezone.now() - timedelta(
        seconds=settings.SOLVER_CLAIM_TTL_SECONDS
    )

    return bool(
        ScenarioRevision.objects.filter(
            pk                =revision.pk                               ,
            publication_status=ScenarioRevision.PublicationStatus.SOLVING,
            updated_at__lt    =stale_before,
        ).update(
            publication_status=ScenarioRevision.PublicationStatus.FAILED,
            updated_at        =timezone.now()                           ,
        )
    )


def _usable_cached_optimum(revision: ScenarioRevision) -> SolverResult | None:
    cached = verified_optimal_result(revision)

    if cached is None:
        return None

    if revision.publication_status not in {
        ScenarioRevision.PublicationStatus.PUBLISHED,
        ScenarioRevision.PublicationStatus.SOLVING  ,
    }:
        updated = ScenarioRevision.objects.filter(
            pk=revision.pk,

            publication_status__in=(
                ScenarioRevision.PublicationStatus.DRAFT ,
                ScenarioRevision.PublicationStatus.FAILED,
            ),
        ).update(
            publication_status=ScenarioRevision.PublicationStatus.PUBLISHED,
            updated_at        =timezone.now()                              ,
        )

        if updated:
            revision.publication_status = ScenarioRevision.PublicationStatus.PUBLISHED
        else:
            revision.refresh_from_db(fields=("publication_status", "updated_at"))

    return cached


def ensure_verified_optimum(
    revision: ScenarioRevision,
    *,

    client : SolverClient | Any | None = None,
) -> tuple[SolverResult, bool]:
    cached = _usable_cached_optimum(revision)

    if cached is not None:
        return cached, True

    revision.refresh_from_db()

    cached = _usable_cached_optimum(revision)
    if cached is not None:
        return cached, True

    if revision.publication_status == ScenarioRevision.PublicationStatus.SOLVING:
        if not _release_stale_claim(revision):
            cached = _usable_cached_optimum(revision)

            if cached is not None:
                return cached, True

            raise InstanceResolutionInProgress

        revision.refresh_from_db()

    if revision.publication_status == ScenarioRevision.PublicationStatus.PUBLISHED:
        normalized = ScenarioRevision.objects.filter(
            pk                =revision.pk                                 ,
            publication_status=ScenarioRevision.PublicationStatus.PUBLISHED,
        ).update(
            publication_status=ScenarioRevision.PublicationStatus.FAILED,
            updated_at        =timezone.now()                           ,
        )

        revision.refresh_from_db()

        cached = _usable_cached_optimum(revision)
        if cached is not None:
            return cached, True

        if (
            not normalized and revision.publication_status == ScenarioRevision.PublicationStatus.SOLVING
        ):
            raise InstanceResolutionInProgress

    try:
        result = publish_revision(
            revision.pk,

            client            =client                    ,
            time_limit_seconds=settings.SOLVER_TIME_LIMIT,
            force             =False                     ,
        )
    except RevisionAlreadySolving as exc:
        cached = _usable_cached_optimum(revision)

        if cached is not None:
            return cached, True

        raise InstanceResolutionInProgress from exc
    except (RevisionAlreadyPublished, PublicationError) as exc:
        cached = _usable_cached_optimum(revision)

        if cached is not None:
            return cached, True

        raise InstanceResolutionFailed from exc

    revision.refresh_from_db()

    if (
            result  .status             != SolverResult.Status               .OPTIMAL   or
        not result  .verified                                                           or
            revision.publication_status != ScenarioRevision.PublicationStatus.PUBLISHED
    ):
        raise InstanceResolutionFailed

    return result, False


def provision_generated_instance(
    *,

    scenario_id : int,

    excluded_content_hashes: Collection[str] = (),
    seed_factory           : Callable[[], int]  | None = None,
    client                 : SolverClient | Any | None = None,
    max_attempts           : int                | None = None,
) -> ProvisionedInstance:
    try:
        scenario = Scenario.objects.only("id", "kind", "active").get(
            pk    =scenario_id,
            active=True       ,
        )
    except Scenario.DoesNotExist as exc:
        raise InstanceProvisionError("Cenario indisponivel.") from exc

    spec, content_hash = generate_candidate(
        kind                   =scenario.kind          ,
        excluded_content_hashes=excluded_content_hashes,
        seed_factory           =seed_factory           ,
        max_attempts           =max_attempts           ,
    )
    revision, _created = get_or_create_revision_for_spec(
        scenario_id =scenario.pk ,
        spec        =spec        ,
        content_hash=content_hash,
    )

    solver_result, cache_hit = ensure_verified_optimum(revision, client=client)

    return ProvisionedInstance(
        revision     =revision     ,
        solver_result=solver_result,
        cache_hit    =cache_hit    ,
    )
