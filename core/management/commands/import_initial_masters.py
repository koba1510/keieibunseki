import csv
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Employee, EmployeeAssignment, Facility, FacilityAlias, FacilityGroup


def read_csv(path: Path) -> list[dict]:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise CommandError(f"文字コードを解釈できません: {path}")


def empty_to_none(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


class Command(BaseCommand):
    help = "拠点・施設・従業員の初期登録CSVを順に取り込みます。"

    def add_arguments(self, parser):
        parser.add_argument("--groups", required=True, help="01_拠点マスタ_初期登録.csv")
        parser.add_argument("--facilities", required=True, help="02_店舗施設マスタ_初期登録.csv")
        parser.add_argument("--employees", required=True, help="03_従業員マスタ_初期登録.csv")

    def handle(self, *args, **options):
        groups_path = Path(options["groups"])
        facilities_path = Path(options["facilities"])
        employees_path = Path(options["employees"])
        for path in (groups_path, facilities_path, employees_path):
            if not path.is_file():
                raise CommandError(f"CSVが見つかりません: {path}")

        group_rows = read_csv(groups_path)
        facility_rows = read_csv(facilities_path)
        employee_rows = read_csv(employees_path)
        errors = []

        if not group_rows or {"拠点ID", "拠点名"} - set(group_rows[0]):
            raise CommandError("拠点CSVは 拠点ID,拠点名 が必要です。")
        if not facility_rows or {
            "施設ID",
            "正式名称",
            "施設区分",
            "所属拠点ID",
            "POS店舗コード",
            "運営区分",
            "営業状態",
        } - set(facility_rows[0]):
            raise CommandError("施設CSVの必須列が不足しています。")
        if not employee_rows or {
            "従業員コード",
            "氏名",
            "初期施設ID",
            "所属開始月",
            "非表示",
        } - set(employee_rows[0]):
            raise CommandError("従業員CSVの必須列が不足しています。")

        groups = []
        seen_groups = set()
        for number, row in enumerate(group_rows, start=2):
            group_id = row["拠点ID"].strip()
            name = row["拠点名"].strip()
            if not group_id or not name:
                errors.append(f"拠点CSV {number}行目: 拠点ID/拠点名が空です")
                continue
            if group_id in seen_groups:
                errors.append(f"拠点CSV {number}行目: 拠点ID重複 {group_id}")
                continue
            seen_groups.add(group_id)
            groups.append((group_id, name))

        facilities = []
        seen_facilities = set()
        seen_pos = set()
        for number, row in enumerate(facility_rows, start=2):
            facility_id = row["施設ID"].strip()
            name = row["正式名称"].strip()
            facility_type = row["施設区分"].strip()
            group_id = row["所属拠点ID"].strip()
            pos = empty_to_none(row.get("POS店舗コード"))
            ownership = empty_to_none(row.get("運営区分")) or Facility.Ownership.NA
            status = empty_to_none(row.get("営業状態")) or Facility.Status.ACTIVE
            alias_name = empty_to_none(row.get("元CSV施設名"))
            if not facility_id or not name or not group_id:
                errors.append(f"施設CSV {number}行目: 施設ID/正式名称/所属拠点IDが空です")
                continue
            if facility_id in seen_facilities:
                errors.append(f"施設CSV {number}行目: 施設ID重複 {facility_id}")
                continue
            if facility_type not in Facility.Type.values:
                errors.append(f"施設CSV {number}行目: 施設区分が不正です ({facility_type})")
                continue
            if ownership not in Facility.Ownership.values:
                errors.append(f"施設CSV {number}行目: 運営区分が不正です ({ownership})")
                continue
            if status not in Facility.Status.values:
                errors.append(f"施設CSV {number}行目: 営業状態が不正です ({status})")
                continue
            if group_id not in seen_groups:
                errors.append(f"施設CSV {number}行目: 未知の所属拠点ID {group_id}")
                continue
            if pos:
                if pos in seen_pos:
                    errors.append(f"施設CSV {number}行目: POS店舗コード重複 {pos}")
                    continue
                seen_pos.add(pos)
            seen_facilities.add(facility_id)
            facilities.append((facility_id, name, facility_type, group_id, pos, ownership, status, alias_name))

        employees = []
        seen_employees = set()
        for number, row in enumerate(employee_rows, start=2):
            code = row["従業員コード"].strip()
            name = row["氏名"].strip()
            facility_id = row["初期施設ID"].strip()
            month_text = row["所属開始月"].strip()
            hidden_raw = row["非表示"].strip().lower()
            if not code or not name or not facility_id or not month_text:
                errors.append(f"従業員CSV {number}行目: 必須項目が空です")
                continue
            if code in seen_employees:
                errors.append(f"従業員CSV {number}行目: 従業員コード重複 {code}")
                continue
            if facility_id not in seen_facilities:
                errors.append(f"従業員CSV {number}行目: 未知の初期施設ID {facility_id}")
                continue
            try:
                month = date.fromisoformat(f"{month_text}-01")
            except ValueError:
                errors.append(f"従業員CSV {number}行目: 所属開始月はYYYY-MM形式が必要です")
                continue
            if hidden_raw not in {"true", "false", "1", "0", "yes", "no"}:
                errors.append(f"従業員CSV {number}行目: 非表示は true/false で指定してください")
                continue
            hidden = hidden_raw in {"true", "1", "yes"}
            seen_employees.add(code)
            employees.append((code, name, facility_id, month, hidden))

        if errors:
            raise CommandError("初期マスタ検証に失敗しました:\n" + "\n".join(errors))

        with transaction.atomic():
            for group_id, name in groups:
                FacilityGroup.objects.update_or_create(id=group_id, defaults={"name": name, "deleted_at": None})
            for facility_id, name, facility_type, group_id, pos, ownership, status, alias_name in facilities:
                facility, _ = Facility.objects.update_or_create(
                    id=facility_id,
                    defaults={
                        "official_name": name,
                        "pos_store_code": pos,
                        "type": facility_type,
                        "facility_group_id": group_id,
                        "ownership": ownership,
                        "status": status,
                        "deleted_at": None,
                    },
                )
                if alias_name:
                    for kind in FacilityAlias.SourceKind.values:
                        FacilityAlias.objects.update_or_create(
                            source_kind=kind,
                            source_name=alias_name,
                            defaults={"facility": facility},
                        )
            for code, name, facility_id, month, hidden in employees:
                employee, _ = Employee.objects.update_or_create(
                    employee_code=code,
                    defaults={
                        "name": name,
                        "hidden_at": timezone.now() if hidden else None,
                    },
                )
                EmployeeAssignment.objects.update_or_create(
                    employee=employee,
                    effective_month=month,
                    defaults={"facility_id": facility_id},
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"初期マスタ取込完了: 拠点{len(groups)} / 施設{len(facilities)} / 従業員{len(employees)}"
            )
        )
