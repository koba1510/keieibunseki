import hashlib
import uuid
from datetime import date

from django.db import transaction
from django.utils import timezone

from .csv_adapters import PARSERS, CsvValidationError
from .models import (
    DatasetImport,
    Employee,
    Facility,
    FacilityAlias,
    ImportJob,
    MonthlyPayroll,
    MonthlyRoyalty,
    MonthlySales,
    MonthlySupplies,
)


def month_date(value: str) -> date:
    return date.fromisoformat(f"{value}-01")


def resolve_facility(row: dict, kind: str):
    code = row.get("code")
    if code:
        facility = Facility.objects.filter(pos_store_code=code, deleted_at__isnull=True).first()
        if facility:
            return facility
    source_name = row.get("source_name", "")
    alias = FacilityAlias.objects.filter(source_kind=kind, source_name=source_name).select_related("facility").first()
    if alias and alias.facility.deleted_at is None:
        return alias.facility
    return Facility.objects.filter(official_name=source_name, deleted_at__isnull=True).first()


def analyze_matches(payload: dict) -> dict:
    unmatched = []
    warnings = []
    kind = payload["kind"]
    if kind == "PAYROLL":
        for row in payload["rows"]:
            employee = Employee.objects.filter(employee_code=row["employee_code"]).first()
            if not employee:
                unmatched.append(
                    {"type": "employee", "code": row["employee_code"], "name": row["name"]}
                )
            elif employee.name != row["name"]:
                warnings.append(
                    f"{row['employee_code']}: 登録名「{employee.name}」とCSV名「{row['name']}」が異なります。"
                )
    else:
        alias_kind = {"SALES": "SALES", "ROYALTY": "ROYALTY", "SUPPLIES": "SUPPLIES"}[kind]
        seen = set()
        for row in payload["rows"]:
            key = (row.get("code"), row.get("source_name"))
            if key in seen:
                continue
            seen.add(key)
            if not resolve_facility(row, alias_kind):
                unmatched.append(
                    {"type": "facility", "code": row.get("code", ""), "name": row.get("source_name", "")}
                )
    return {"unmatched": unmatched, "warnings": warnings}


def preview_import(content: bytes, kind: str, user, selected_months=None, skip_employee_codes=None) -> ImportJob:
    if kind not in PARSERS:
        raise CsvValidationError("未対応のCSV種別です。")
    if kind == "SUPPLIES":
        payload = PARSERS[kind](content, selected_months)
    elif kind == "PAYROLL":
        payload = PARSERS[kind](content, skip_employee_codes=set(skip_employee_codes or []))
    else:
        payload = PARSERS[kind](content)
    matches = analyze_matches(payload)
    payload["checksum"] = hashlib.sha256(content).hexdigest()
    payload.update(matches)
    job = ImportJob.objects.create(
        idempotency_key=str(uuid.uuid4()),
        data_kind=kind,
        requested_by=user,
        payload=payload,
        result_summary={
            "months": payload["months"],
            "row_count": len(payload["rows"]),
            "unmatched_count": len(matches["unmatched"]),
        },
    )
    return job


def commit_import(job: ImportJob) -> dict:
    if job.state == ImportJob.State.COMPLETED:
        return job.result_summary or {}
    if job.state == ImportJob.State.RUNNING:
        raise CsvValidationError("この取込は処理中です。")
    matches = analyze_matches(job.payload)
    if matches["unmatched"]:
        raise CsvValidationError("未照合の施設または従業員があります。マスタ登録後に再実行してください。")
    job.state = ImportJob.State.RUNNING
    job.save(update_fields=["state"])
    payload = job.payload
    try:
        with transaction.atomic():
            months = [month_date(value) for value in payload["months"]]
            if job.data_kind == "SALES":
                MonthlySales.objects.filter(month__in=months).delete()
                objects = []
                for row in payload["rows"]:
                    objects.append(
                        MonthlySales(
                            facility=resolve_facility(row, "SALES"),
                            month=months[0],
                            sales_total=row["sales_total"],
                            cleaning_sales=row["cleaning_sales"],
                        )
                    )
                MonthlySales.objects.bulk_create(objects)
            elif job.data_kind == "ROYALTY":
                MonthlyRoyalty.objects.filter(month__in=months).delete()
                objects = []
                for row in payload["rows"]:
                    objects.append(
                        MonthlyRoyalty(
                            facility=resolve_facility(row, "ROYALTY"),
                            month=months[0],
                            fee_ex_tax=row["fee_ex_tax"],
                            fee_tax=row["fee_tax"],
                            fee_tax_inclusive=row["fee_tax_inclusive"],
                        )
                    )
                MonthlyRoyalty.objects.bulk_create(objects)
            elif job.data_kind == "PAYROLL":
                MonthlyPayroll.objects.filter(month__in=months).delete()
                objects = []
                for row in payload["rows"]:
                    employee = Employee.objects.get(employee_code=row["employee_code"])
                    if employee.hidden_at:
                        employee.hidden_at = None
                        employee.save(update_fields=["hidden_at"])
                    objects.append(
                        MonthlyPayroll(
                            employee=employee,
                            month=months[0],
                            total_payment=row["total_payment"],
                            health_employee_deduction=row["health_employee_deduction"],
                            care_employee_deduction=row["care_employee_deduction"],
                            pension_employee_deduction=row["pension_employee_deduction"],
                            employer_social_estimate=row["employer_social_estimate"],
                        )
                    )
                MonthlyPayroll.objects.bulk_create(objects)
            elif job.data_kind == "SUPPLIES":
                MonthlySupplies.objects.filter(month__in=months).delete()
                objects = []
                for row in payload["rows"]:
                    objects.append(
                        MonthlySupplies(
                            facility=resolve_facility(row, "SUPPLIES"),
                            month=month_date(row["month"]),
                            total_cost=row["total_cost"],
                        )
                    )
                MonthlySupplies.objects.bulk_create(objects)
            for month in months:
                DatasetImport.objects.update_or_create(
                    data_kind=job.data_kind,
                    month=month,
                    defaults={
                        "content_checksum": payload["checksum"],
                        "status": "COMPLETED",
                        "job": job,
                    },
                )
            summary = {"months": payload["months"], "row_count": len(payload["rows"]), "status": "completed"}
            job.state = ImportJob.State.COMPLETED
            job.finished_at = timezone.now()
            job.result_summary = summary
            job.save(update_fields=["state", "finished_at", "result_summary"])
            return summary
    except Exception as exc:
        job.state = ImportJob.State.FAILED
        job.finished_at = timezone.now()
        job.result_summary = {"status": "failed", "error": str(exc)}
        job.save(update_fields=["state", "finished_at", "result_summary"])
        raise
