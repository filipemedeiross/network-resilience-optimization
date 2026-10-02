from __future__ import annotations

from django.db.models            import Max
from django.db                   import transaction
from django.core.management.base import BaseCommand, CommandError

from games.services.canonical  import revision_content_hash
from games.services.generation import generate_known_scenarios
from games.models              import Scenario, ScenarioRevision


class Command(BaseCommand):
    help = "Cria o catalogo de cenarios."

    def add_arguments(self, parser):
        parser.add_argument(
            "--instances-per-kind",

            type   =int,
            default=0  ,
            metavar="N",
            help   =(
                "Quantidade de instancias deterministicas a preaquecer"
                "por tipo; zero cria apenas o catalogo (padrao: 0)."
            ),
        )

    def handle(self, *args, **options):
        instances_per_kind = options["instances_per_kind"]

        if instances_per_kind < 0:
            raise CommandError("--instances-per-kind nao pode ser negativo.")

        scenarios_by_slug      = {}
        created_scenario_count = 0

        for prototype in generate_known_scenarios(instances_per_kind=1):
            scenario, scenario_created = Scenario.objects.update_or_create(
                slug    =prototype.slug,
                defaults={
                    "title"  : prototype.title,
                    "kind"   : prototype.kind ,
                    "active" : True           ,
                },
            )

            scenarios_by_slug[prototype.slug] = scenario

            created_scenario_count += int(scenario_created)

        if instances_per_kind == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "Catalogo concluido: 2 cenario(s), nenhuma instancia "
                    "preaquecida; as redes serao geradas sob demanda."
                )
            )

            return

        specs_with_hashes                    = []
        hashes_by_slug : dict[str, set[str]] = {}

        for spec in generate_known_scenarios(
            instances_per_kind=instances_per_kind
        ):
            content_hash = revision_content_hash(
                topology           =spec.topology           ,
                rules              =spec.rules              ,
                schema_version     =spec.schema_version     ,
                formulation_version=spec.formulation_version,
            )

            hashes = hashes_by_slug.setdefault(spec.slug, set())

            if content_hash in hashes:
                raise CommandError(
                    f"O pool gerado para {spec.slug} contem instancias duplicadas."
                )

            hashes           .add   (content_hash        )
            specs_with_hashes.append((spec, content_hash))

        created_count = 0
        reused_count  = 0
        for spec, content_hash in specs_with_hashes:
            with transaction.atomic():
                scenario = Scenario.objects.select_for_update().get(
                    pk=scenarios_by_slug[spec.slug].pk
                )

                existing = scenario.revisions.filter(
                    content_hash=content_hash
                ).first()

                if existing is not None:
                    reused_count += 1

                    action   = "reutilizada"
                    revision = existing
                else:
                    latest   = scenario.revisions.aggregate(
                        value=Max("revision")
                    )["value"] or 0

                    revision = ScenarioRevision.objects.create(
                        scenario=scenario  ,
                        revision=latest + 1,
                        topology=spec.topology,
                        rules   =spec.rules   ,

                        presentation       =spec.presentation       ,
                        schema_version     =spec.schema_version     ,
                        generator_version  =spec.generator_version  ,
                        formulation_version=spec.formulation_version,
                        seed               =spec.seed               ,

                        content_hash=content_hash,
                    )

                    created_count += 1
                    action         = "criada"

            self.stdout.write(
                self.style.SUCCESS(
                    f"{spec.slug} seed={spec.seed}: revisao {revision.revision} {action}."
                )
            )

        total_instances = len(specs_with_hashes)
        scenario_count  = len(hashes_by_slug   )

        self.stdout.write(
            self.style.SUCCESS(
                f"Carga concluida: {total_instances} instancia(s) deterministica(s), "
                f"{instances_per_kind} por tipo em {scenario_count} cenario(s); "
                f"{created_count} revisao(oes) criada(s), {reused_count} reutilizada(s), "
                f"{created_scenario_count} cenario(s) novo(s)."
            )
        )
