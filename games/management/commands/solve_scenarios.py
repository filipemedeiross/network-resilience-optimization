from __future__ import annotations

from datetime import timedelta

from django.utils                import timezone
from django.conf                 import settings
from django.core.management.base import BaseCommand, CommandError

from games.services.solver_client import SolverClient
from games.models                 import ScenarioRevision
from games.services.publication   import PublicationError       , \
                                         publish_revision       , \
                                         recover_stale_revisions


class Command(BaseCommand):
    help = "Resolve revisoes pelo servico MIP e publica apenas otimos verificados."

    def add_arguments(self, parser):
        parser.add_argument(
            "--slug",
            action="append"                              ,
            dest  ="slugs"                               ,
            help  ="Limita a um slug; pode ser repetido.",
        )
        parser.add_argument(
            "--time-limit",
            type   =float                                   ,
            default=settings.SOLVER_TIME_LIMIT              ,
            help   ="Limite do MIP em segundos por revisao.",
        )
        parser.add_argument(
            "--force",
            action="store_true"                               ,
            help  ="Resolve novamente revisoes ja publicadas.",
        )

    def handle(self, *args, **options):
        if options["time_limit"] <= 0:
            raise CommandError("--time-limit deve ser positivo.")

        if settings.SOLVER_CLAIM_TTL_SECONDS <= 0:
            raise CommandError("SOLVER_CLAIM_TTL_SECONDS deve ser positivo.")

        recovered = recover_stale_revisions(
            stale_before=timezone.now() - timedelta(seconds=settings.SOLVER_CLAIM_TTL_SECONDS)
        )

        if recovered:
            self.stdout.write(
                self.style.WARNING(
                    f"{recovered} reserva(s) de solver abandonada(s) recuperada(s)."
                )
            )

        statuses = [
            ScenarioRevision.PublicationStatus.DRAFT ,
            ScenarioRevision.PublicationStatus.FAILED,
        ]

        if options["force"]:
            statuses.append(ScenarioRevision.PublicationStatus.PUBLISHED)

        revisions = (
            ScenarioRevision
            .objects
            .select_related("scenario")
            .filter        (scenario__active=True, publication_status__in=statuses)
            .order_by      ("scenario__slug"     , "revision"                     )
        )

        if options["slugs"]:
            revisions = revisions.filter(scenario__slug__in=options["slugs"])

        revision_ids = list(
            revisions.values_list("id", flat=True)
        )

        if not revision_ids:
            self.stdout.write("Nenhuma revisao elegivel para resolver.")
            return

        client   = SolverClient()
        failures = 0

        for revision_id in revision_ids:
            try:
                result = publish_revision(
                    revision_id,
                    client            =client               ,
                    time_limit_seconds=options["time_limit"],
                    force             =options["force"     ],
                )
            except PublicationError as exc:
                failures += 1

                self.stderr.write(
                    self.style.ERROR(str(exc))
                )

                continue

            result.revision.refresh_from_db()

            message = (
                f"{result.revision}: status={result.status}, "
                f"verified={result.verified}, "
                f"publication={result.revision.publication_status}"
            )

            if (
                result.status == "optimal" and
                result.verified            and
                result.revision.publication_status == ScenarioRevision.PublicationStatus.PUBLISHED
            ):
                self.stdout.write(
                    self.style.SUCCESS(message)
                )
            else:
                failures += 1
                detail    = result.verification_error or result.termination_reason

                self.stderr.write(
                    self.style.ERROR(f"{message}: {detail}")
                )

        if failures:
            raise CommandError(f"{failures} revisao(oes) nao foram publicadas.")
