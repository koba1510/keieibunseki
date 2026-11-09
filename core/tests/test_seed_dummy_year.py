from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from core.models import (
    DatasetImport,
    Employee,
    EmployeeAssignment,
    Facility,
    FacilityGroup,
    MonthlyPayroll,
    MonthlyRoyalty,
    MonthlySales,
    MonthlySupplies,
)


class SeedDummyYearTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="strong-password-123",
        )
        group = FacilityGroup.objects.create(id="GRP001", name="本社")
        self.store = Facility.objects.create(
            id="FAC0001",
            official_name="上永谷店",
            pos_store_code="071",
            type=Facility.Type.STORE,
            facility_group=group,
            ownership=Facility.Ownership.FC,
        )
        employee = Employee.objects.create(employee_code="10001", name="山田")
        EmployeeAssignment.objects.create(
            employee=employee,
            effective_month=date(2025, 8, 1),
            facility=self.store,
        )
        MonthlySales.objects.create(
            facility=self.store,
            month=date(2026, 9, 1),
            sales_total=1_000_000,
            cleaning_sales=900_000,
        )
        MonthlySupplies.objects.create(
            facility=self.store,
            month=date(2026, 9, 1),
            total_cost=10_000,
        )
        MonthlyPayroll.objects.create(
            employee=employee,
            month=date(2026, 9, 1),
            total_payment=200_000,
            health_employee_deduction=10_000,
            care_employee_deduction=0,
            pension_employee_deduction=18_000,
            employer_social_estimate=40_000,
        )

    def test_seed_preserves_base_and_fills_prior_months(self):
        out = StringIO()
        call_command("seed_dummy_year", "--months", "3", stdout=out)

        self.assertEqual(
            MonthlySales.objects.get(facility=self.store, month=date(2026, 9, 1)).sales_total,
            1_000_000,
        )
        self.assertEqual(MonthlySales.objects.filter(facility=self.store).count(), 3)
        self.assertEqual(MonthlySupplies.objects.filter(facility=self.store).count(), 3)
        self.assertEqual(MonthlyPayroll.objects.count(), 3)
        self.assertEqual(MonthlyRoyalty.objects.filter(facility=self.store).count(), 3)
        self.assertTrue(
            DatasetImport.objects.filter(data_kind="SALES", month=date(2026, 8, 1)).exists()
        )
        august = MonthlySales.objects.get(facility=self.store, month=date(2026, 8, 1))
        self.assertNotEqual(august.sales_total, 0)
        self.assertIn("ダミー投入完了", out.getvalue())
