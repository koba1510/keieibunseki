import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.models import BackupAudit


class Command(BaseCommand):
    help = "確認文字列を要求してDBバックアップを復元します。"

    def add_arguments(self, parser):
        parser.add_argument("backup")
        parser.add_argument("--confirm", required=True, help="RESTORE を指定")
        parser.add_argument("--actor", default="CLI")

    def handle(self, *args, **options):
        if options["confirm"] != "RESTORE":
            raise CommandError("復元には --confirm RESTORE が必要です。")
        source = Path(options["backup"]).resolve()
        if not source.is_file():
            raise CommandError("バックアップファイルが見つかりません。")
        database = settings.DATABASES["default"]
        if database["ENGINE"].endswith("sqlite3"):
            connection.close()
            shutil.copy2(source, database["NAME"])
        else:
            env = os.environ.copy()
            env["PGPASSWORD"] = database["PASSWORD"]
            command = [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--host",
                database["HOST"],
                "--port",
                str(database["PORT"]),
                "--username",
                database["USER"],
                "--dbname",
                database["NAME"],
                str(source),
            ]
            try:
                subprocess.run(command, env=env, check=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise CommandError(f"pg_restoreに失敗しました: {exc}") from exc
        BackupAudit.objects.create(action="RESTORE", path=str(source), actor=options["actor"])
        self.stdout.write(self.style.SUCCESS(f"{source} を復元しました。"))
