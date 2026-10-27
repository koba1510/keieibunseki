import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class FacilityGroup(models.Model):
    name = models.CharField("所属拠点名", max_length=120, unique=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Facility(models.Model):
    class Type(models.TextChoices):
        STORE = "STORE", "店舗"
        FACTORY = "FACTORY", "工場"
        HQ = "HQ", "本部"
        OTHER = "OTHER", "その他"

    class Ownership(models.TextChoices):
        DIRECT = "DIRECT", "直営"
        FC = "FC", "FC"
        NA = "NA", "対象外"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "営業中"
        CLOSED = "CLOSED", "閉店"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    official_name = models.CharField("正式名称", max_length=160)
    pos_store_code = models.CharField("POS店舗コード", max_length=32, null=True, blank=True, unique=True)
    type = models.CharField("種別", max_length=12, choices=Type.choices)
    facility_group = models.ForeignKey(FacilityGroup, verbose_name="所属拠点", on_delete=models.PROTECT)
    ownership = models.CharField("運営区分", max_length=12, choices=Ownership.choices, default=Ownership.NA)
    status = models.CharField("状態", max_length=12, choices=Status.choices, default=Status.ACTIVE)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["official_name"]
        indexes = [models.Index(fields=["type", "deleted_at"])]

    def __str__(self):
        return self.official_name


class FacilityAlias(models.Model):
    class SourceKind(models.TextChoices):
        SALES = "SALES", "売上"
        ROYALTY = "ROYALTY", "ロイヤリティ"
        SUPPLIES = "SUPPLIES", "備品"

    source_kind = models.CharField(max_length=12, choices=SourceKind.choices)
    source_name = models.CharField(max_length=200)
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="aliases")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_kind", "source_name"], name="unique_facility_alias")
        ]


class Employee(models.Model):
    employee_code = models.CharField("従業員コード", max_length=40, unique=True)
    name = models.CharField("氏名", max_length=120)
    hidden_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee_code"]

    def __str__(self):
        return f"{self.employee_code} {self.name}"

    def facility_for_month(self, month):
        assignment = self.assignments.filter(effective_month__lte=month).order_by("-effective_month").first()
        if assignment:
            return assignment.facility
        first = self.assignments.order_by("effective_month").first()
        return first.facility if first else None


class EmployeeAssignment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    effective_month = models.DateField("開始月")
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="employee_assignments")

    class Meta:
        ordering = ["-effective_month"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_month"], name="unique_employee_month_assignment")
        ]

    def clean(self):
        if self.effective_month.day != 1:
            raise ValidationError({"effective_month": "開始月は月初日で保存してください。"})


class MonthlySales(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="sales")
    month = models.DateField()
    sales_total = models.BigIntegerField()
    cleaning_sales = models.BigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["facility", "month"], name="unique_sales_month")]
        indexes = [models.Index(fields=["month"])]


class MonthlyRoyalty(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="royalties")
    month = models.DateField()
    fee_ex_tax = models.BigIntegerField()
    fee_tax = models.BigIntegerField()
    fee_tax_inclusive = models.BigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["facility", "month"], name="unique_royalty_month")]
        indexes = [models.Index(fields=["month"])]


class MonthlyPayroll(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payroll")
    month = models.DateField()
    total_payment = models.BigIntegerField()
    health_employee_deduction = models.BigIntegerField()
    care_employee_deduction = models.BigIntegerField()
    pension_employee_deduction = models.BigIntegerField()
    employer_social_estimate = models.BigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "month"], name="unique_payroll_month")]
        indexes = [models.Index(fields=["month"])]

    @property
    def labor_cost(self):
        return self.total_payment + self.employer_social_estimate


class MonthlySupplies(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="supplies")
    month = models.DateField()
    total_cost = models.BigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["facility", "month"], name="unique_supplies_month")]
        indexes = [models.Index(fields=["month"])]


class ImportJob(models.Model):
    class State(models.TextChoices):
        PREVIEWED = "PREVIEWED", "プレビュー済み"
        RUNNING = "RUNNING", "取込中"
        COMPLETED = "COMPLETED", "取込完了"
        FAILED = "FAILED", "取込失敗"

    idempotency_key = models.CharField(max_length=80, unique=True)
    data_kind = models.CharField(max_length=20)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PREVIEWED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    payload = models.JSONField(default=dict)
    result_summary = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class DatasetImport(models.Model):
    data_kind = models.CharField(max_length=20)
    month = models.DateField()
    imported_at = models.DateTimeField(auto_now=True)
    content_checksum = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default="COMPLETED")
    job = models.ForeignKey(ImportJob, on_delete=models.PROTECT, related_name="datasets")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["data_kind", "month"], name="unique_dataset_kind_month")
        ]
        indexes = [models.Index(fields=["month", "data_kind"])]


class BackupAudit(models.Model):
    action = models.CharField(max_length=20)
    path = models.CharField(max_length=500)
    actor = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
