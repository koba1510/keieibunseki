import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "環境変数から初期管理者を冪等に作成します。"

    def add_arguments(self, parser):
        parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL"))
        parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD"))

    def handle(self, *args, **options):
        email = options["email"]
        password = options["password"]
        if not email or not password:
            raise CommandError("--email/--password または ADMIN_EMAIL/ADMIN_PASSWORD が必要です。")
        if len(password) < 12:
            raise CommandError("初期パスワードは12文字以上にしてください。")
        user, created = get_user_model().objects.get_or_create(
            username=email.lower(),
            defaults={"email": email.lower(), "is_staff": True, "is_superuser": True},
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"管理者 {email} を作成しました。"))
        else:
            self.stdout.write(f"管理者 {email} は既に存在します。")
