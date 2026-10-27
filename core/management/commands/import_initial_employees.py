import csv
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Employee, EmployeeAssignment, Facility


class Command(BaseCommand):
    help = "employee_code,name,facility_id,effective_month 列のCSVから初期従業員を移行します。"

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **options):
        path = Path(options["csv_path"])
        if not path.is_file():
            raise CommandError("CSVファイルが見つかりません。")
        rows = None
        for encoding in ("utf-8-sig", "cp932"):
            try:
                with path.open(encoding=encoding, newline="") as handle:
                    rows = list(csv.DictReader(handle))
                break
            except UnicodeDecodeError:
                continue
        required = {"employee_code", "name", "facility_id", "effective_month"}
        if rows is None or not rows or not required.issubset(rows[0]):
            raise CommandError(f"必須列: {', '.join(sorted(required))}")
        validated = []
        errors = []
        seen = set()
        for number, row in enumerate(rows, start=2):
            code = row["employee_code"].strip()
            if not code or code in seen:
                errors.append(f"{number}行目: 従業員コードが空欄または重複")
                continue
            seen.add(code)
            facility = Facility.objects.filter(pk=row["facility_id"].strip(), deleted_at__isnull=True).first()
            if not facility:
                errors.append(f"{number}行目: 施設ID {row['facility_id']} を確認できません")
                continue
            try:
                month = date.fromisoformat(f"{row['effective_month'].strip()}-01")
            except ValueError:
                errors.append(f"{number}行目: effective_month はYYYY-MM形式が必要")
                continue
            validated.append((code, row["name"].strip(), facility, month))
        if errors:
            raise CommandError("移行前検証に失敗しました:\n" + "\n".join(errors))
        with transaction.atomic():
            for code, name, facility, month in validated:
                employee, _ = Employee.objects.update_or_create(employee_code=code, defaults={"name": name})
                EmployeeAssignment.objects.update_or_create(
                    employee=employee, effective_month=month, defaults={"facility": facility}
                )
        self.stdout.write(self.style.SUCCESS(f"{len(validated)}人を移行しました。"))
