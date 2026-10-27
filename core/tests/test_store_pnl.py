from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.analytics import compare_metric, fiscal_year_bounds, store_pnl_data
from core.models import (
    DatasetImport,
    Facility,
    FacilityGroup,
    ImportJob,
    MonthlyRoyalty,
    MonthlySales,
    MonthlySupplies,
)


class StorePnlTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="admin@example.com", email="admin@example.com", password="strong-password-123"
        )
        self.group = FacilityGroup.objects.create(id="GRP001", name="本社")
        self.store = Facility.objects.create(
            id="FAC0001",
            official_name="上永谷店",
            pos_store_code="071",
            type=Facility.Type.STORE,
            facility_group=self.group,
            ownership=Facility.Ownership.FC,
        )
        user = self.user
        job = ImportJob.objects.create(
            idempotency_key="store-pnl",
            data_kind="SALES",
            state=ImportJob.State.COMPLETED,
            requested_by=user,
        )
        for kind in ("SALES", "PAYROLL", "SUPPLIES", "ROYALTY"):
            DatasetImport.objects.create(
                data_kind=kind, month=date(2026, 9, 1), content_checksum=kind, job=job
            )
        MonthlySales.objects.create(
            facility=self.store, month=date(2026, 9, 1), sales_total=1000000, cleaning_sales=900000
        )
        MonthlySupplies.objects.create(facility=self.store, month=date(2026, 9, 1), total_cost=10000)
        MonthlyRoyalty.objects.create(
            facility=self.store,
            month=date(2026, 9, 1),
            fee_ex_tax=100000,
            fee_tax=10000,
            fee_tax_inclusive=110000,
        )

    def test_fiscal_bounds(self):
        self.assertEqual(fiscal_year_bounds(date(2026, 9, 1)), (date(2026, 8, 1), date(2027, 7, 1)))
        self.assertEqual(fiscal_year_bounds(date(2027, 1, 1)), (date(2026, 8, 1), date(2027, 7, 1)))

    def test_compare_metric_zero_baseline(self):
        result = compare_metric(10, 0)
        self.assertEqual(result["status"], "zero_baseline")
        self.assertIsNone(result["rate"])

    def test_store_pnl_selected_month(self):
        data = store_pnl_data(self.store, date(2026, 9, 1))
        self.assertEqual(data["selected"]["sales"], 1000000)
        self.assertEqual(data["selected"]["royalty"], 110000)
        # labor imported but no payroll rows => 0
        self.assertEqual(data["selected"]["labor"], 0)
        self.assertEqual(data["selected"]["expense"], 120000)
        self.assertEqual(data["selected"]["balance"], 880000)

    def test_store_pnl_page(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("store_pnl"), {"facility": self.store.pk, "month": "2026-09", "period": "fiscal", "compare": "yoy"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "店舗別収支")
        self.assertContains(response, "上永谷店")
        self.assertContains(response, "前月比")
        self.assertContains(response, "前年同月比")

    def test_store_switch_keeps_month(self):
        other = Facility.objects.create(
            id="FAC0002",
            official_name="丸山台店",
            pos_store_code="075",
            type=Facility.Type.STORE,
            facility_group=self.group,
            ownership=Facility.Ownership.FC,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("store_pnl"), {"facility": other.pk, "month": "2026-09"})
        self.assertEqual(response.context["selected"], date(2026, 9, 1))
        self.assertEqual(response.context["facility"].pk, other.pk)
