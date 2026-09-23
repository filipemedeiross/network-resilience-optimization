from __future__ import annotations

import uuid

from django.db.models       import F, Q
from django.db              import models
from django.core.exceptions import ValidationError


class Scenario(models.Model):
    class Kind(models.TextChoices):
        WATER    = "water"   , "Rede de agua"
        MILITARY = "military", "Rede militar"

    slug  = models.SlugField(max_length=120, unique =True        )
    title = models.CharField(max_length=200)
    kind  = models.CharField(max_length=20 , choices=Kind.choices)

    active     = models.BooleanField (default     =True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now    =True)

    class Meta:
        ordering = ("title", "slug")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_kind = (
                type(self)
                .objects
                .filter     (pk=self.pk       )
                .values_list("kind", flat=True)
                .first      ()
            )

            if (
                previous_kind is not None  and
                previous_kind != self.kind and
                self.revisions.exists()
            ):
                raise ValidationError(
                    {
                        "kind" : "O tipo nao pode mudar depois que o cenario possui revisoes.",
                    }
                )

        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class ScenarioRevision(models.Model):
    class PublicationStatus(models.TextChoices):
        DRAFT     = "draft"    , "Rascunho"
        SOLVING   = "solving"  , "Em resolucao"
        PUBLISHED = "published", "Publicado"
        FAILED    = "failed"   , "Falhou"

    IMMUTABLE_FIELDS = (
        "scenario_id"        ,
        "revision"           ,
        "topology"           ,
        "rules"              ,
        "presentation"       ,
        "schema_version"     ,
        "generator_version"  ,
        "formulation_version",
        "seed"               ,
        "content_hash"       ,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    scenario = models.ForeignKey(
        Scenario,
        on_delete   =models.PROTECT,
        related_name="revisions"   ,
    )
    revision = models.PositiveIntegerField()

    topology     = models.JSONField()
    rules        = models.JSONField(default=dict)
    presentation = models.JSONField(default=dict)

    schema_version = models.PositiveSmallIntegerField(default=1)
    seed           = models.BigIntegerField          ()

    generator_version   = models.CharField(max_length=64, default="1")
    formulation_version = models.CharField(max_length=64, default="1")
    content_hash        = models.CharField(max_length=64, db_index=True, editable=False)

    publication_status = models.CharField(
        max_length=20  ,
        db_index  =True,
        choices   =PublicationStatus.choices,
        default   =PublicationStatus.DRAFT  ,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now    =True)

    class Meta:
        ordering    = (
            "scenario_id",
            "-revision"  ,
        )

        constraints = [
            models.UniqueConstraint(
                fields=("scenario", "revision")        ,
                name  ="games_unique_scenario_revision",
            ),
            models.UniqueConstraint(
                fields=("scenario", "content_hash")   ,
                name  ="games_unique_scenario_content",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1)      ,
                name     ="games_revision_gte_one",
            ),
        ]

    def save(self, *args, **kwargs):
        from games.services.evaluation import validate_scenario
        from games.services.canonical  import revision_content_hash

        validate_scenario(
            self.scenario.kind,
            self.topology     ,
            self.rules        ,
        )

        expected_hash = revision_content_hash(
            topology           =self.topology           ,
            rules              =self.rules              ,
            schema_version     =self.schema_version     ,
            formulation_version=self.formulation_version,
        )

        if self.content_hash and self.content_hash != expected_hash:
            raise ValidationError(
                {
                    "content_hash" : "O hash nao corresponde ao conteudo semantico.",
                }
            )

        self.content_hash = expected_hash

        if not self._state.adding:
            previous = (
                type   (self)
                .objects
                .filter(pk=self.pk            )
                .values(*self.IMMUTABLE_FIELDS)
                .first ()
            )

            if previous is not None:
                changed = [
                    field
                    for field in self.IMMUTABLE_FIELDS
                    if  getattr(self, field) != previous[field]
                ]

                if changed:
                    raise ValidationError(
                        {
                            field : "Uma revisao publicada ou persistida e imutavel; crie uma nova revisao."
                            for field in changed
                        }
                    )

        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.scenario.slug} r{self.revision}"


class SolverResult(models.Model):
    class Status(models.TextChoices):
        OPTIMAL     = "optimal"    , "Otimo"
        FEASIBLE    = "feasible"   , "Viavel"
        INFEASIBLE  = "infeasible" , "Inviavel"
        NO_SOLUTION = "no_solution", "Sem solucao"
        ERROR       = "error"      , "Erro"

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique     =True, default=uuid.uuid4, editable=False)

    revision = models.ForeignKey(
        ScenarioRevision,
        on_delete   =models.PROTECT  ,
        related_name="solver_results",
    )

    status             = models.CharField(max_length=20  , choices=Status.choices)
    termination_reason = models.CharField(max_length=200 , blank  =True          )
    solution           = models.JSONField(default   =dict)

    objective_value = models.FloatField          (null   =True, blank=True)
    best_bound      = models.FloatField          (null   =True, blank=True)
    gap             = models.FloatField          (null   =True, blank=True)
    duration_ms     = models.PositiveIntegerField(default=0   )

    api_version         = models.PositiveSmallIntegerField(default=1 )
    solver_name         = models.CharField(max_length=100, blank=True)
    solver_version      = models.CharField(max_length=100, blank=True)
    formulation_version = models.CharField(max_length=64 )

    verified           = models.BooleanField(default=False, db_index=True)
    verification_error = models.TextField   (blank  =True )

    input_hash = models.CharField    (max_length  =64  , blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

        indexes = [
            models.Index(
                fields=("revision", "status", "verified"),
                name  ="games_result_lookup_idx"         ,
            )
        ]

        constraints = [
            models.CheckConstraint(
                condition=Q(gap__isnull=True) | Q(gap__gte=0),
                name     ="games_solver_gap_nonnegative"     ,
            )
        ]

    def __str__(self) -> str:
        return f"{self.revision} - {self.status}"


class Play(models.Model):
    class Status(models.TextChoices):
        ACTIVE    = "active"   , "Ativa"
        COMPLETED = "completed", "Concluida"
        REVEALED  = "revealed" , "Solucao revelada"

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visitor_id = models.UUIDField(db_index   =True)

    revision      = models.ForeignKey(
        ScenarioRevision,
        on_delete   =models.PROTECT,
        related_name="plays"       ,
    )
    solver_result = models.ForeignKey(
        "SolverResult",
        on_delete   =models.PROTECT,
        related_name="plays"       ,
    )

    status = models.CharField(
        max_length=20  ,
        db_index  =True,
        choices   =Status.choices,
        default   =Status.ACTIVE ,
    )

    revealed = models.BooleanField        (default=False)
    version  = models.PositiveIntegerField(default=0    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now    =True)

    class Meta:
        ordering = ("-updated_at",)

        indexes  = [
            models.Index(
                fields=("visitor_id", "status"),
                name  ="games_play_visitor_idx",
            )
        ]

    def save(self, *args, **kwargs):
        if self.solver_result_id:
            result = self.solver_result

            if result.revision_id != self.revision_id:
                raise ValidationError(
                    {"solver_result" : "O gabarito pertence a outra revisao."}
                )

            if result.status != SolverResult.Status.OPTIMAL or not result.verified:
                raise ValidationError(
                    {"solver_result" : "A partida exige um gabarito otimo verificado."}
                )

        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return str(self.id)


class Attempt(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField()

    play = models.ForeignKey(
        Play,
        on_delete   =models.PROTECT,
        related_name="attempts"    ,
    )

    expected_version  = models.PositiveIntegerField()
    committed_version = models.PositiveIntegerField()

    selection = models.JSONField(default=dict)
    result    = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering    = ("created_at",)

        constraints = [
            models.UniqueConstraint(
                fields=("play", "request_id")        ,
                name  ="games_unique_attempt_request",
            ),

            models.CheckConstraint(
                condition=Q(committed_version=F("expected_version") + 1),
                name     ="games_attempt_version_increment"             ,
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Tentativas sao append-only e nao podem ser alteradas.")

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Tentativas sao append-only e nao podem ser removidas.")

    def __str__(self) -> str:
        return f"{self.play_id}:{self.committed_version}"
