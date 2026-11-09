"""既存の月次実績を基準に、各店舗・従業員のダミー月次データを1年分投入する。"""

from __future__ import annotations

import hashlib
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.analytics import add_months, month_range
from core.models import (
    DatasetImport,
    ImportJob,
    MonthlyPayroll,
    MonthlyRoyalty,
    MonthlySales,
    MonthlySupplies,
)


# 9月=1.0 を基準にした季節係数（コインランドリー／クリーニング想定）
SEASONAL_FACTORS = {
    1: 1.06,
    2: 0.97,
    3: 1.02,
    4: 0.98,
    5: 0.93,
    6: 0.88,
    7: 0.90,
    8: 0.94,
    9: 1.00,
    10: 1.07,
    11: 1.11,
    12: 1.14,
}


def _noise(seed: str, amplitude: float = 0.08) -> float:
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    unit = int(digest[:8], 16) / 0xFFFFFFFF
    return 1.0 + (unit * 2 - 1) * amplitude


def _scale(amount: int, factor: float) -> int:
    return max(0, int(round(amount * factor)))


class Command(BaseCommand):
    help = (
        "既存の最新月次データ（既定: 売上・給与・備品）を基準に、"
        "直近12か月分のダミーデータを各店舗／従業員へ投入します。"
        "基準月そのものは既定で上書きしません。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-month",
            default=None,
            help="基準月 YYYY-MM（省略時は既存データの最新月）",
        )
        parser.add_argument(
            "--months",
            type=int,
            default=12,
            help="投入する月数（基準月を含む、既定12）",
        )
        parser.add_argument(
            "--overwrite-base",
            action="store_true",
            help="基準月の実績もダミー再生成で上書きする",
        )
        parser.add_argument(
            "--royalty-rate",
            type=float,
            default=0.05,
            help="売上合計に対するロイヤリティ税込比率（既定0.05）",
        )

    def handle(self, *args, **options):
        months_count = options["months"]
        if months_count < 2:
            raise CommandError("--months は2以上にしてください。")

        base_month = self._resolve_base_month(options["base_month"])
        start = add_months(base_month, -(months_count - 1))
        target_months = list(month_range(start, base_month))
        overwrite_base = options["overwrite_base"]
        royalty_rate = options["royalty_rate"]

        sales_base = {
            row.facility_id: row
            for row in MonthlySales.objects.filter(month=base_month).select_related("facility")
        }
        supplies_base = {
            row.facility_id: row
            for row in MonthlySupplies.objects.filter(month=base_month).select_related("facility")
        }
        payroll_base = list(
            MonthlyPayroll.objects.filter(month=base_month).select_related("employee")
        )

        if not sales_base and not supplies_base and not payroll_base:
            raise CommandError(f"基準月 {base_month:%Y-%m} に既存データがありません。")

        user = get_user_model().objects.filter(is_superuser=True).order_by("id").first()
        if user is None:
            raise CommandError("管理者ユーザーが必要です。先に create_initial_admin を実行してください。")

        with transaction.atomic():
            job = ImportJob.objects.create(
                idempotency_key=f"seed-dummy-year-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                data_kind="DUMMY_YEAR",
                state=ImportJob.State.COMPLETED,
                requested_by=user,
                payload={
                    "base_month": base_month.isoformat(),
                    "months": [m.isoformat() for m in target_months],
                    "royalty_rate": royalty_rate,
                },
                result_summary={},
                finished_at=timezone.now(),
            )

            sales_created = self._seed_sales(
                target_months, base_month, sales_base, overwrite_base, job
            )
            supplies_created = self._seed_supplies(
                target_months, base_month, supplies_base, overwrite_base, job
            )
            payroll_created = self._seed_payroll(
                target_months, base_month, payroll_base, overwrite_base, job
            )
            royalty_created = self._seed_royalty(
                target_months, base_month, sales_base, royalty_rate, overwrite_base, job
            )

            job.result_summary = {
                "base_month": base_month.isoformat(),
                "months": [m.isoformat() for m in target_months],
                "sales_rows": sales_created,
                "supplies_rows": supplies_created,
                "payroll_rows": payroll_created,
                "royalty_rows": royalty_created,
            }
            job.save(update_fields=["result_summary"])

        self.stdout.write(
            self.style.SUCCESS(
                "ダミー投入完了: "
                f"{start:%Y-%m}〜{base_month:%Y-%m} / "
                f"売上{sales_created} 備品{supplies_created} "
                f"給与{payroll_created} ロイヤリティ{royalty_created}"
            )
        )

    def _resolve_base_month(self, value: str | None) -> date:
        if value:
            try:
                year, month = value.split("-")
                return date(int(year), int(month), 1)
            except ValueError as exc:
                raise CommandError("--base-month は YYYY-MM 形式で指定してください。") from exc

        candidates = []
        for model in (MonthlySales, MonthlySupplies, MonthlyPayroll):
            latest = model.objects.order_by("-month").values_list("month", flat=True).first()
            if latest:
                candidates.append(latest)
        if not candidates:
            raise CommandError("既存の月次データが見つかりません。")
        return max(candidates)

    def _mark_imported(self, kind: str, month: date, job: ImportJob) -> None:
        checksum = hashlib.sha256(f"dummy:{kind}:{month.isoformat()}".encode()).hexdigest()
        DatasetImport.objects.update_or_create(
            data_kind=kind,
            month=month,
            defaults={
                "content_checksum": checksum,
                "status": "COMPLETED",
                "job": job,
            },
        )

    def _seed_sales(self, months, base_month, base_rows, overwrite_base, job) -> int:
        created = 0
        for month in months:
            if month == base_month and not overwrite_base:
                self._mark_imported("SALES", month, job)
                continue
            MonthlySales.objects.filter(month=month).delete()
            objects = []
            season = SEASONAL_FACTORS[month.month]
            for facility_id, row in base_rows.items():
                factor = season * _noise(f"sales:{facility_id}:{month.isoformat()}")
                objects.append(
                    MonthlySales(
                        facility_id=facility_id,
                        month=month,
                        sales_total=_scale(row.sales_total, factor),
                        cleaning_sales=_scale(row.cleaning_sales, factor),
                    )
                )
            MonthlySales.objects.bulk_create(objects)
            self._mark_imported("SALES", month, job)
            created += len(objects)
        return created

    def _seed_supplies(self, months, base_month, base_rows, overwrite_base, job) -> int:
        created = 0
        for month in months:
            if month == base_month and not overwrite_base:
                self._mark_imported("SUPPLIES", month, job)
                continue
            MonthlySupplies.objects.filter(month=month).delete()
            objects = []
            season = SEASONAL_FACTORS[month.month]
            for facility_id, row in base_rows.items():
                # 備品は売上ほど季節連動しない想定で振幅を抑える
                factor = (0.7 + 0.3 * season) * _noise(
                    f"supplies:{facility_id}:{month.isoformat()}", amplitude=0.15
                )
                objects.append(
                    MonthlySupplies(
                        facility_id=facility_id,
                        month=month,
                        total_cost=_scale(row.total_cost, factor),
                    )
                )
            MonthlySupplies.objects.bulk_create(objects)
            self._mark_imported("SUPPLIES", month, job)
            created += len(objects)
        return created

    def _seed_payroll(self, months, base_month, base_rows, overwrite_base, job) -> int:
        created = 0
        for month in months:
            if month == base_month and not overwrite_base:
                self._mark_imported("PAYROLL", month, job)
                continue
            MonthlyPayroll.objects.filter(month=month).delete()
            objects = []
            season = SEASONAL_FACTORS[month.month]
            for row in base_rows:
                # 人件費は小幅変動
                factor = (0.85 + 0.15 * season) * _noise(
                    f"payroll:{row.employee_id}:{month.isoformat()}", amplitude=0.05
                )
                objects.append(
                    MonthlyPayroll(
                        employee_id=row.employee_id,
                        month=month,
                        total_payment=_scale(row.total_payment, factor),
                        health_employee_deduction=_scale(row.health_employee_deduction, factor),
                        care_employee_deduction=_scale(row.care_employee_deduction, factor),
                        pension_employee_deduction=_scale(row.pension_employee_deduction, factor),
                        employer_social_estimate=_scale(row.employer_social_estimate, factor),
                    )
                )
            MonthlyPayroll.objects.bulk_create(objects)
            self._mark_imported("PAYROLL", month, job)
            created += len(objects)
        return created

    def _seed_royalty(self, months, base_month, sales_base, royalty_rate, overwrite_base, job) -> int:
        """売上がある店舗について、税込ロイヤリティを売上×比率で生成する。"""
        created = 0
        for month in months:
            if month == base_month and not overwrite_base and MonthlyRoyalty.objects.filter(month=month).exists():
                self._mark_imported("ROYALTY", month, job)
                continue
            MonthlyRoyalty.objects.filter(month=month).delete()
            objects = []
            season = SEASONAL_FACTORS[month.month]
            for facility_id, sales_row in sales_base.items():
                factor = season * _noise(f"sales:{facility_id}:{month.isoformat()}")
                sales_total = (
                    sales_row.sales_total
                    if month == base_month and not overwrite_base
                    else _scale(sales_row.sales_total, factor)
                )
                if month == base_month and not overwrite_base:
                    # 基準月の売上はDB上の実値を使う
                    live = MonthlySales.objects.filter(facility_id=facility_id, month=month).first()
                    if live:
                        sales_total = live.sales_total
                inclusive = _scale(sales_total, royalty_rate)
                ex_tax = int(round(inclusive / 1.1))
                tax = inclusive - ex_tax
                objects.append(
                    MonthlyRoyalty(
                        facility_id=facility_id,
                        month=month,
                        fee_ex_tax=ex_tax,
                        fee_tax=tax,
                        fee_tax_inclusive=inclusive,
                    )
                )
            MonthlyRoyalty.objects.bulk_create(objects)
            self._mark_imported("ROYALTY", month, job)
            created += len(objects)
        return created
