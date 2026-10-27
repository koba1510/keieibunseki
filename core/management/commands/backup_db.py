import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import BackupAudit


class Command(BaseCommand):
    help = "DBを別ディレクトリへバックアップし、90日超のバックアップを削除します。"

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default=os.getenv("BACKUP_DIR", "backups"))
        parser.add_argument("--actor", default="CLI")

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"]).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        database = settings.DATABASES["default"]
        if database["ENGINE"].endswith("sqlite3"):
            target = output_dir / f"keieibunseki-{timestamp}.sqlite3"
            shutil.copy2(database["NAME"], target)
        else:
            target = output_dir / f"keieibunseki-{timestamp}.dump"
            env = os.environ.copy()
            env["PGPASSWORD"] = database["PASSWORD"]
            command = [
                "pg_dump",
                "--format=custom",
                "--host",
                database["HOST"],
                "--port",
                str(database["PORT"]),
                "--username",
                database["USER"],
                "--file",
                str(target),
                database["NAME"],
            ]
            try:
                subprocess.run(command, env=env, check=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise CommandError(f"pg_dumpに失敗しました: {exc}") from exc
        cutoff = timezone.now() - timedelta(days=90)
        for path in output_dir.glob("keieibunseki-*"):
            if timezone.datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone()) < cutoff:
                path.unlink()
        BackupAudit.objects.create(action="BACKUP", path=str(target), actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(str(target)))
