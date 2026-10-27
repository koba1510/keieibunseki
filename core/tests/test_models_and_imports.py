from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from core.analytics import dashboard_data
from core.csv_adapters import CsvValidationError
from core.import_service import commit_import, preview_import
from core.models import (
    DatasetImport,
    Employee,
    EmployeeAssignment,
    Facility,
    FacilityAlias,
    FacilityGroup,
    ImportJob,
    MonthlyPayroll,
    MonthlySales,
)


def csv_bytes(rows):
    return ("\n".join(",".join(f'"{cell}"' if "," in str(cell) else str(cell) for cell in row) for row in rows)).encode(
        "utf-8-sig"
    )


class DomainTests(TestCase):
    def setUp(self):
        self.group = FacilityGroup.objects.create(id="GRP001", name="第1拠点")
        self.store = Facility.objects.create(
            id="FAC0001",
            official_name="上永谷店",
            pos_store_code="071",
            type=Facility.Type.STORE,
            facility_group=self.group,
            ownership=Facility.Ownership.FC,
        )
        self.other_store = Facility.objects.create(
            id="FAC0002",
            official_name="別店舗",
            pos_store_code="072",
            type=Facility.Type.STORE,
            facility_group=self.group,
            ownership=Facility.Ownership.DIRECT,
        )
        self.employee = Employee.objects.create(employee_code="00003", name="匿名 太郎")

    def test_employee_code_remains_string(self):
        self.assertEqual(Employee.objects.get(pk=self.employee.pk).employee_code, "00003")

    def test_assignment_uses_latest_prior_month(self):
        EmployeeAssignment.objects.create(employee=self.employee, effective_month=date(2026, 8, 1), facility=self.store)
        EmployeeAssignment.objects.create(
            employee=self.employee, effective_month=date(2026, 10, 1), facility=self.other_store
        )
        self.assertEqual(self.employee.facility_for_month(date(2026, 9, 1)), self.store)
        self.assertEqual(self.employee.facility_for_month(date(2026, 10, 1)), self.other_store)

    def test_assignment_applies_first_record_retroactively(self):
        EmployeeAssignment.objects.create(employee=self.employee, effective_month=date(2026, 8, 1), facility=self.store)
        self.assertEqual(self.employee.facility_for_month(date(2026, 7, 1)), self.store)

    def test_assignment_same_month_is_unique(self):
        EmployeeAssignment.objects.create(employee=self.employee, effective_month=date(2026, 8, 1), facility=self.store)
        with self.assertRaises(IntegrityError):
            EmployeeAssignment.objects.create(
                employee=self.employee, effective_month=date(2026, 8, 1), facility=self.other_store
            )

    def test_alias_is_scoped_by_source_kind(self):
        FacilityAlias.objects.create(source_kind="SALES", source_name="上永谷", facility=self.store)
        FacilityAlias.objects.create(source_kind="SUPPLIES", source_name="上永谷", facility=self.store)
        self.assertEqual(FacilityAlias.objects.count(), 2)

    def sales_file(self, amount="1000", code="071", name="上永谷店"):
        return csv_bytes(
            [
                ["支払月", "2026/09"],
                ["店舗", "売上", "クリーニング"],
                [f"{code} {name}", amount, "800"],
            ]
        )

    def test_preview_does_not_change_monthly_data(self):
        user = get_user_model().objects.create_user(username="a@example.com")
        job = preview_import(self.sales_file(), "SALES", user)
        self.assertEqual(job.state, ImportJob.State.PREVIEWED)
        self.assertEqual(MonthlySales.objects.count(), 0)

    def test_commit_writes_data_and_dataset_marker(self):
        user = get_user_model().objects.create_user(username="a@example.com")
        job = preview_import(self.sales_file(), "SALES", user)
        result = commit_import(job)
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(MonthlySales.objects.get().sales_total, 1000)
        self.assertTrue(DatasetImport.objects.filter(data_kind="SALES", month=date(2026, 9, 1)).exists())

    def test_same_month_reimport_replaces_old_rows(self):
        user = get_user_model().objects.create_user(username="a@example.com")
        commit_import(preview_import(self.sales_file(), "SALES", user))
        commit_import(preview_import(self.sales_file("2500"), "SALES", user))
        self.assertEqual(MonthlySales.objects.count(), 1)
        self.assertEqual(MonthlySales.objects.get().sales_total, 2500)

    def test_reimport_does_not_change_other_month(self):
        MonthlySales.objects.create(
            facility=self.store, month=date(2026, 8, 1), sales_total=500, cleaning_sales=400
        )
        user = get_user_model().objects.create_user(username="a@example.com")
        commit_import(preview_import(self.sales_file(), "SALES", user))
        self.assertEqual(MonthlySales.objects.get(month=date(2026, 8, 1)).sales_total, 500)

    def test_completed_job_is_idempotent(self):
        user = get_user_model().objects.create_user(username="a@example.com")
        job = preview_import(self.sales_file(), "SALES", user)
        commit_import(job)
        commit_import(ImportJob.objects.get(pk=job.pk))
        self.assertEqual(MonthlySales.objects.count(), 1)

    def test_unmatched_master_blocks_commit(self):
        user = get_user_model().objects.create_user(username="a@example.com")
        job = preview_import(self.sales_file(code="999", name="不明店"), "SALES", user)
        with self.assertRaises(CsvValidationError):
            commit_import(job)
        self.assertEqual(MonthlySales.objects.count(), 0)

    def test_assignment_change_moves_cost_without_changing_total(self):
        assignment = EmployeeAssignment.objects.create(
            employee=self.employee, effective_month=date(2026, 8, 1), facility=self.store
        )
        MonthlyPayroll.objects.create(
            employee=self.employee,
            month=date(2026, 9, 1),
            total_payment=300000,
            health_employee_deduction=10000,
            care_employee_deduction=2000,
            pension_employee_deduction=20000,
            employer_social_estimate=32000,
        )
        user = get_user_model().objects.create_user(username="marker@example.com")
        job = ImportJob.objects.create(
            idempotency_key="payroll-marker",
            data_kind="PAYROLL",
            state=ImportJob.State.COMPLETED,
            requested_by=user,
        )
        DatasetImport.objects.create(
            data_kind="PAYROLL",
            month=date(2026, 9, 1),
            content_checksum="x",
            job=job,
        )
        before = dashboard_data(date(2026, 9, 1), date(2026, 9, 1))
        assignment.facility = self.other_store
        assignment.save()
        after = dashboard_data(date(2026, 9, 1), date(2026, 9, 1))
        self.assertEqual(before["totals"]["labor"], after["totals"]["labor"])
        before_cost = {row["facility"].id: row["labor"] for row in before["rows"]}
        after_cost = {row["facility"].id: row["labor"] for row in after["rows"]}
        self.assertEqual(before_cost[self.store.id], 332000)
        self.assertEqual(after_cost[self.other_store.id], 332000)

    def test_hidden_employee_is_restored_on_payroll_commit(self):
        self.employee.hidden_at = timezone.now()
        self.employee.save()
        EmployeeAssignment.objects.create(employee=self.employee, effective_month=date(2026, 8, 1), facility=self.store)
        payroll = csv_bytes(
            [
                ["令和8年8月分給与", ""],
                ["", "匿名 太郎（00003）"],
                ["支給額合計", "300000"],
                ["その他合計", "0"],
                ["社会保険料合計", "32000"],
                ["雇用保険料", "0"],
            ]
        )
        user = get_user_model().objects.create_user(username="a@example.com")
        commit_import(preview_import(payroll, "PAYROLL", user))
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.hidden_at)

    def test_non_store_does_not_require_sales_or_royalty(self):
        factory = Facility.objects.create(
            id="FAC0099",
            official_name="工場",
            type=Facility.Type.FACTORY,
            facility_group=self.group,
            ownership=Facility.Ownership.NA,
        )
        user = get_user_model().objects.create_user(username="factory@example.com")
        job = ImportJob.objects.create(
            idempotency_key="factory-marker",
            data_kind="PAYROLL",
            state=ImportJob.State.COMPLETED,
            requested_by=user,
        )
        DatasetImport.objects.create(data_kind="PAYROLL", month=date(2026, 9, 1), content_checksum="x", job=job)
        DatasetImport.objects.create(data_kind="SUPPLIES", month=date(2026, 9, 1), content_checksum="y", job=job)
        MonthlySupplies = __import__("core.models", fromlist=["MonthlySupplies"]).MonthlySupplies
        MonthlySupplies.objects.create(facility=factory, month=date(2026, 9, 1), total_cost=1000)
        result = dashboard_data(date(2026, 9, 1), date(2026, 9, 1), [str(factory.pk)])
        row = result["rows"][0]
        self.assertIsNone(row["sales"])
        self.assertEqual(row["supplies"], 1000)
        self.assertFalse(row["provisional"])

    def test_fc_missing_royalty_row_stays_unimported_after_month_import(self):
        user = get_user_model().objects.create_user(username="royalty@example.com")
        job = ImportJob.objects.create(
            idempotency_key="royalty-marker",
            data_kind="ROYALTY",
            state=ImportJob.State.COMPLETED,
            requested_by=user,
        )
        DatasetImport.objects.create(data_kind="ROYALTY", month=date(2026, 9, 1), content_checksum="r", job=job)
        DatasetImport.objects.create(data_kind="SALES", month=date(2026, 9, 1), content_checksum="s", job=job)
        DatasetImport.objects.create(data_kind="PAYROLL", month=date(2026, 9, 1), content_checksum="p", job=job)
        DatasetImport.objects.create(data_kind="SUPPLIES", month=date(2026, 9, 1), content_checksum="u", job=job)
        MonthlySales.objects.create(facility=self.store, month=date(2026, 9, 1), sales_total=1000, cleaning_sales=800)
        result = dashboard_data(date(2026, 9, 1), date(2026, 9, 1), [str(self.store.pk)])
        row = result["rows"][0]
        self.assertIsNone(row["royalty"])
        self.assertTrue(row["provisional"])

