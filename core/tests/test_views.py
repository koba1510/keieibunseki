from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Employee, EmployeeAssignment, Facility, FacilityGroup


class ViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="admin@example.com", email="admin@example.com", password="strong-password-123"
        )
        self.group = FacilityGroup.objects.create(id="GRP001", name="本部")
        self.facility = Facility.objects.create(
            id="FAC0062",
            official_name="本部",
            type=Facility.Type.HQ,
            facility_group=self.group,
            ownership=Facility.Ownership.NA,
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next=/dashboard/")

    def test_login_and_dashboard(self):
        self.assertTrue(self.client.login(username="admin@example.com", password="strong-password-123"))
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ダッシュボード")

    def test_api_me_requires_and_returns_current_user(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "admin@example.com")

    def test_facility_create(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("facilities"),
            {
                "id": "FAC0001",
                "official_name": "上永谷店",
                "pos_store_code": "071",
                "type": "STORE",
                "facility_group": self.group.pk,
                "ownership": "FC",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Facility.objects.filter(pos_store_code="071").exists())

    def test_first_assignment_cannot_be_deleted(self):
        self.client.force_login(self.user)
        employee = Employee.objects.create(employee_code="00003", name="匿名")
        assignment = EmployeeAssignment.objects.create(
            employee=employee, effective_month=date(2026, 8, 1), facility=self.facility
        )
        self.client.post(reverse("assignment_delete", args=[employee.pk, assignment.pk]))
        self.assertTrue(EmployeeAssignment.objects.filter(pk=assignment.pk).exists())

    def test_import_rejects_missing_file(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("imports"), {"kind": "SALES"}, follow=True)
        self.assertContains(response, "CSVファイルを選択してください")
