from collections import defaultdict
from datetime import date

from .models import (
    DatasetImport,
    EmployeeAssignment,
    Facility,
    MonthlyPayroll,
    MonthlyRoyalty,
    MonthlySales,
    MonthlySupplies,
)


def add_months(value: date, count: int) -> date:
    absolute = value.year * 12 + value.month - 1 + count
    return date(absolute // 12, absolute % 12 + 1, 1)


def month_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current = add_months(current, 1)


def assignment_map(payroll_rows):
    result = {}
    employees = {row.employee_id for row in payroll_rows}
    histories = defaultdict(list)
    for item in EmployeeAssignment.objects.filter(employee_id__in=employees).select_related("facility"):
        histories[item.employee_id].append(item)
    for items in histories.values():
        items.sort(key=lambda item: item.effective_month)
    for row in payroll_rows:
        items = histories.get(row.employee_id, [])
        earlier = [item for item in items if item.effective_month <= row.month]
        result[row.id] = earlier[-1] if earlier else (items[0] if items else None)
    return result


def dashboard_data(start: date, end: date, facility_ids=None, group_ids=None, sales_basis="total"):
    facilities = Facility.objects.filter(deleted_at__isnull=True)
    if facility_ids:
        facilities = facilities.filter(id__in=facility_ids)
    if group_ids:
        facilities = facilities.filter(facility_group_id__in=group_ids)
    facilities = list(facilities.select_related("facility_group"))
    facility_by_id = {item.id: item for item in facilities}
    ids = list(facility_by_id)
    data = {
        (facility.id, month): {
            "facility": facility,
            "month": month,
            "sales": None,
            "cleaning_sales": None,
            "labor": None,
            "supplies": None,
            "royalty": None,
            "provisional": True,
        }
        for facility in facilities
        for month in month_range(start, end)
    }
    for row in MonthlySales.objects.filter(facility_id__in=ids, month__range=(start, end)):
        cell = data[(row.facility_id, row.month)]
        cell["sales"] = row.sales_total
        cell["cleaning_sales"] = row.cleaning_sales
    for row in MonthlySupplies.objects.filter(facility_id__in=ids, month__range=(start, end)):
        data[(row.facility_id, row.month)]["supplies"] = row.total_cost
    for row in MonthlyRoyalty.objects.filter(facility_id__in=ids, month__range=(start, end)):
        data[(row.facility_id, row.month)]["royalty"] = row.fee_tax_inclusive
    payroll = list(MonthlyPayroll.objects.filter(month__range=(start, end)).select_related("employee"))
    assignments = assignment_map(payroll)
    unassigned_labor = 0
    labor_keys = set()
    for row in payroll:
        assignment = assignments[row.id]
        amount = row.labor_cost
        if assignment and assignment.facility_id in facility_by_id:
            key = (assignment.facility_id, row.month)
            if key in data:
                if key not in labor_keys:
                    data[key]["labor"] = 0
                    labor_keys.add(key)
                data[key]["labor"] += amount
        elif assignment is None:
            unassigned_labor += amount
    imported = {
        (row.data_kind, row.month)
        for row in DatasetImport.objects.filter(month__range=(start, end), status="COMPLETED")
    }
    for cell in data.values():
        facility = cell["facility"]
        month = cell["month"]
        is_store = facility.type == Facility.Type.STORE
        if is_store and ("SALES", month) in imported and cell["sales"] is None:
            cell["sales"] = 0
            cell["cleaning_sales"] = 0
        if ("PAYROLL", month) in imported and cell["labor"] is None:
            cell["labor"] = 0
        if ("SUPPLIES", month) in imported and cell["supplies"] is None:
            cell["supplies"] = 0
        if is_store:
            if facility.ownership == Facility.Ownership.DIRECT:
                cell["royalty"] = 0 if ("ROYALTY", month) in imported else None
            elif facility.ownership == Facility.Ownership.FC:
                # 取込済み月でも明細欠落は0円にせず未確定のまま残す
                if ("ROYALTY", month) not in imported:
                    cell["royalty"] = None
            else:
                cell["royalty"] = 0 if ("ROYALTY", month) in imported else None
        else:
            cell["sales"] = None
            cell["cleaning_sales"] = None
            cell["royalty"] = None
    totals = {"sales": 0, "labor": 0, "supplies": 0, "royalty": 0, "balance": 0}
    monthly = []
    ranking = defaultdict(lambda: {"sales": 0, "balance": 0, "facility": None})
    by_type = defaultdict(lambda: {"sales": 0, "cost": 0, "balance": 0})
    for cell in data.values():
        facility = cell["facility"]
        selected_sales = cell["sales"] if sales_basis == "total" else cell["cleaning_sales"]
        if facility.type == Facility.Type.STORE:
            values = [selected_sales, cell["labor"], cell["supplies"], cell["royalty"]]
            cell["provisional"] = any(value is None for value in values)
            cell["balance"] = None if selected_sales is None else selected_sales - sum(value or 0 for value in values[1:])
        else:
            values = [cell["labor"], cell["supplies"]]
            cell["provisional"] = any(value is None for value in values)
            cell["balance"] = None if cell["provisional"] else -( (cell["labor"] or 0) + (cell["supplies"] or 0) )
        totals["sales"] += selected_sales or 0
        totals["labor"] += cell["labor"] or 0
        totals["supplies"] += cell["supplies"] or 0
        totals["royalty"] += cell["royalty"] or 0
        totals["balance"] += cell["balance"] or 0
        bucket = by_type[facility.get_type_display()]
        bucket["sales"] += selected_sales or 0
        bucket["cost"] += (cell["labor"] or 0) + (cell["supplies"] or 0) + (cell["royalty"] or 0)
        bucket["balance"] += cell["balance"] or 0
        if facility.type == Facility.Type.STORE:
            rank = ranking[facility.id]
            rank["facility"] = facility
            rank["sales"] += selected_sales or 0
            rank["balance"] += cell["balance"] or 0
    for month in month_range(start, end):
        cells = [cell for cell in data.values() if cell["month"] == month]
        monthly.append(
            {
                "month": month,
                "sales": sum(
                    (cell["sales"] if sales_basis == "total" else cell["cleaning_sales"]) or 0 for cell in cells
                ),
                "cost": sum(
                    (cell["labor"] or 0) + (cell["supplies"] or 0) + (cell["royalty"] or 0) for cell in cells
                ),
                "provisional": (not cells) or any(cell["provisional"] for cell in cells),
            }
        )
        monthly[-1]["balance"] = monthly[-1]["sales"] - monthly[-1]["cost"]
    return {
        "totals": totals,
        "monthly": monthly,
        "by_type": dict(by_type),
        "ranking": sorted(ranking.values(), key=lambda item: item["balance"], reverse=True),
        "rows": sorted(data.values(), key=lambda item: (item["month"], item["facility"].official_name)),
        "unassigned_labor": unassigned_labor,
        "is_provisional": any(cell["provisional"] for cell in data.values()) if data else True,
    }
