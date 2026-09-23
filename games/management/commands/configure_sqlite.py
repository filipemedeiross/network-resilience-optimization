from django.db                   import connection
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Ativa WAL e configuracoes seguras de concorrencia no SQLite."

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este comando so se aplica ao SQLite.")

        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")

            mode = cursor.fetchone()[0]

            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON"   )
            cursor.execute("PRAGMA busy_timeout=20000")

        if str(mode).lower() != "wal":
            raise CommandError(f"SQLite nao ativou WAL; modo atual: {mode}.")

        self.stdout.write(
            self.style.SUCCESS(
                "SQLite configurado com journal_mode=wal."
            )
        )
