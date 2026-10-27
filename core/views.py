from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .analytics import add_months, compare_metric, dashboard_data, store_pnl_data
from .csv_adapters import CsvValidationError
from .forms import (
    AdminUserCreationForm,
    EmployeeAssignmentForm,
    EmployeeForm,
    FacilityAliasForm,
    FacilityForm,
    FacilityGroupForm,
)
from .import_service import analyze_matches, commit_import, preview_import
from .models import Employee, EmployeeAssignment, Facility, FacilityGroup, ImportJob


def parse_month(value, default):
    try:
        return date.fromisoformat(f"{value}-01") if value else default
    except ValueError:
        return default


@login_required
def dashboard(request):
    today = timezone.localdate().replace(day=1)
    end = parse_month(request.GET.get("to"), today)
    start = parse_month(request.GET.get("from"), add_months(end, -11))
    if start > end:
        start, end = end, start
    basis = request.GET.get("salesBasis", "total")
    facility_ids = request.GET.getlist("facilityIds")
    group_ids = request.GET.getlist("groupIds")
    context = dashboard_data(start, end, facility_ids, group_ids, basis)
    context.update(
        {
            "start": start,
            "end": end,
            "basis": basis,
            "facilities": Facility.objects.filter(deleted_at__isnull=True),
            "groups": FacilityGroup.objects.filter(deleted_at__isnull=True),
            "selected_facilities": facility_ids,
            "selected_groups": group_ids,
        }
    )
    return render(request, "core/dashboard.html", context)


@login_required
def facilities(request):
    include_deleted = request.GET.get("deleted") == "1"
    queryset = Facility.objects.select_related("facility_group")
    if not include_deleted:
        queryset = queryset.filter(deleted_at__isnull=True)
    query = request.GET.get("q", "")
    if query:
        queryset = queryset.filter(Q(official_name__icontains=query) | Q(pos_store_code__icontains=query))
    if request.GET.get("type"):
        queryset = queryset.filter(type=request.GET["type"])
    if request.GET.get("group"):
        queryset = queryset.filter(facility_group_id=request.GET["group"])
    ordering = request.GET.get("order", "official_name")
    if ordering not in {"official_name", "-official_name", "pos_store_code", "-pos_store_code", "type", "-type"}:
        ordering = "official_name"
    queryset = queryset.order_by(ordering)
    form = FacilityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        messages.success(request, "施設を登録しました。")
        return redirect("facility_detail", pk=item.pk)
    return render(
        request,
        "core/facilities.html",
        {
            "items": queryset,
            "form": form,
            "groups": FacilityGroup.objects.filter(deleted_at__isnull=True),
            "types": Facility.Type.choices,
            "include_deleted": include_deleted,
        },
    )


@login_required
def facility_detail(request, pk):
    item = get_object_or_404(Facility.objects.select_related("facility_group"), pk=pk)
    alias_form = FacilityAliasForm(request.POST or None)
    if request.method == "POST" and alias_form.is_valid():
        alias = alias_form.save(commit=False)
        alias.facility = item
        alias.save()
        messages.success(request, "CSV別名を登録しました。")
        return redirect("facility_detail", pk=item.pk)
    return render(request, "core/facility_detail.html", {"item": item, "alias_form": alias_form})


@login_required
def facility_edit(request, pk):
    item = get_object_or_404(Facility, pk=pk)
    form = FacilityForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "施設を更新しました。")
        return redirect("facility_detail", pk=item.pk)
    return render(request, "core/form.html", {"form": form, "title": "施設編集", "cancel_url": item.get_absolute_url() if hasattr(item, "get_absolute_url") else f"/facilities/{item.pk}/"})


@login_required
@require_POST
def facility_toggle_deleted(request, pk):
    item = get_object_or_404(Facility, pk=pk)
    item.deleted_at = None if item.deleted_at else timezone.now()
    item.save(update_fields=["deleted_at"])
    messages.success(request, "施設を復元しました。" if item.deleted_at is None else "施設を論理削除しました。")
    return redirect("facilities")


@login_required
def groups(request):
    form = FacilityGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "所属拠点を登録しました。")
        return redirect("groups")
    items = FacilityGroup.objects.filter(deleted_at__isnull=True).order_by("name")
    return render(request, "core/groups.html", {"form": form, "items": items})


@login_required
@require_POST
def group_delete(request, pk):
    item = get_object_or_404(FacilityGroup, pk=pk)
    if item.facility_set.filter(deleted_at__isnull=True).exists():
        messages.error(request, "使用中の所属拠点は削除できません。")
    else:
        item.deleted_at = timezone.now()
        item.save(update_fields=["deleted_at"])
        messages.success(request, "所属拠点を削除しました。")
    return redirect("groups")


@login_required
def employees(request):
    queryset = Employee.objects.all()
    if request.GET.get("hidden") != "1":
        queryset = queryset.filter(hidden_at__isnull=True)
    query = request.GET.get("q", "")
    if query:
        queryset = queryset.filter(Q(name__icontains=query) | Q(employee_code__icontains=query))
    facility_id = request.GET.get("facility")
    if facility_id:
        queryset = queryset.filter(assignments__facility_id=facility_id).distinct()
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        messages.success(request, "従業員を登録しました。所属履歴を追加してください。")
        return redirect("employee_detail", pk=item.pk)
    page = Paginator(queryset.order_by("employee_code"), 50).get_page(request.GET.get("page"))
    return render(
        request,
        "core/employees.html",
        {"page": page, "form": form, "facilities": Facility.objects.filter(deleted_at__isnull=True)},
    )


@login_required
def employee_detail(request, pk):
    item = get_object_or_404(Employee, pk=pk)
    form = EmployeeAssignmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        EmployeeAssignment.objects.update_or_create(
            employee=item,
            effective_month=form.cleaned_data["effective_month"].replace(day=1),
            defaults={"facility": form.cleaned_data["facility"]},
        )
        messages.success(request, "所属履歴を保存しました。過去給与の計上先は自動再集計されます。")
        return redirect("employee_detail", pk=item.pk)
    return render(request, "core/employee_detail.html", {"item": item, "form": form})


@login_required
@require_POST
def employee_toggle_hidden(request, pk):
    item = get_object_or_404(Employee, pk=pk)
    item.hidden_at = None if item.hidden_at else timezone.now()
    item.save(update_fields=["hidden_at"])
    return redirect("employee_detail", pk=pk)


@login_required
@require_POST
def assignment_delete(request, pk, assignment_pk):
    item = get_object_or_404(Employee, pk=pk)
    assignment = get_object_or_404(EmployeeAssignment, pk=assignment_pk, employee=item)
    if item.assignments.count() <= 1:
        messages.error(request, "最初の所属履歴は削除できません。")
    else:
        assignment.delete()
        messages.success(request, "所属履歴を削除しました。")
    return redirect("employee_detail", pk=pk)


@login_required
def assignment_impact(request, pk):
    item = get_object_or_404(Employee, pk=pk)
    month = parse_month(request.GET.get("month"), timezone.localdate().replace(day=1))
    payroll = item.payroll.filter(month__gte=month).order_by("month")
    return JsonResponse(
        {
            "employee": item.name,
            "affected_months": [row.month.strftime("%Y-%m") for row in payroll],
            "count": payroll.count(),
        }
    )


@login_required
def imports(request):
    if request.method == "POST":
        uploaded = request.FILES.get("file")
        kind = request.POST.get("kind", "")
        if not uploaded:
            messages.error(request, "CSVファイルを選択してください。")
        else:
            selected = [value.strip() for value in request.POST.get("selected_months", "").split(",") if value.strip()]
            try:
                job = preview_import(uploaded.read(), kind, request.user, selected or None)
                return redirect("import_job", pk=job.pk)
            except CsvValidationError as exc:
                messages.error(request, str(exc))
    return render(request, "core/imports.html")


@login_required
def import_job(request, pk):
    job = get_object_or_404(ImportJob, pk=pk, requested_by=request.user)
    matches = analyze_matches(job.payload)
    return render(
        request,
        "core/import_job.html",
        {"job": job, "current_unmatched": matches["unmatched"], "current_warnings": matches["warnings"]},
    )


@login_required
@require_POST
def import_commit(request, pk):
    job = get_object_or_404(ImportJob, pk=pk, requested_by=request.user)
    try:
        commit_import(job)
        messages.success(request, "CSV取込が完了しました。")
    except Exception as exc:
        messages.error(request, f"取込に失敗しました: {exc}")
    return redirect("import_job", pk=pk)


@login_required
def users(request):
    form = AdminUserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "管理者ユーザーを作成しました。")
        return redirect("users")
    return render(request, "core/users.html", {"form": form, "items": get_user_model().objects.order_by("email")})


@login_required
def api_me(request):
    return JsonResponse({"id": request.user.pk, "email": request.user.email, "is_staff": request.user.is_staff})


@login_required
def api_dashboard(request):
    today = timezone.localdate().replace(day=1)
    end = parse_month(request.GET.get("to"), today)
    start = parse_month(request.GET.get("from"), add_months(end, -11))
    data = dashboard_data(start, end, request.GET.getlist("facilityIds"), request.GET.getlist("groupIds"), request.GET.get("salesBasis", "total"))
    return JsonResponse(
        {
            "totals": data["totals"],
            "is_provisional": data["is_provisional"],
            "unassigned_labor": data["unassigned_labor"],
            "monthly": [
                {**row, "month": row["month"].strftime("%Y-%m")} for row in data["monthly"]
            ],
        }
    )


@login_required
def api_facility_metrics(request, pk):
    facility = get_object_or_404(Facility, pk=pk)
    today = timezone.localdate().replace(day=1)
    end = parse_month(request.GET.get("to"), today)
    start = parse_month(request.GET.get("from"), add_months(end, -11))
    data = dashboard_data(start, end, [str(facility.pk)], None, request.GET.get("salesBasis", "total"))
    return JsonResponse(
        {
            "facility": {"id": str(facility.pk), "name": facility.official_name, "type": facility.type},
            "rows": [
                {
                    "month": row["month"].strftime("%Y-%m"),
                    "sales": row["sales"],
                    "labor": row["labor"],
                    "supplies": row["supplies"],
                    "royalty": row["royalty"],
                    "balance": row["balance"],
                    "provisional": row["provisional"],
                }
                for row in data["rows"]
            ],
        }
    )


@login_required
def api_import_job(request, pk):
    job = get_object_or_404(ImportJob, pk=pk, requested_by=request.user)
    return JsonResponse(
        {
            "id": job.pk,
            "state": job.state,
            "data_kind": job.data_kind,
            "result": job.result_summary,
        }
    )


@login_required
def store_pnl(request):
    stores = list(
        Facility.objects.filter(deleted_at__isnull=True, type=Facility.Type.STORE)
        .select_related("facility_group")
        .order_by("official_name")
    )
    if not stores:
        return render(request, "core/store_pnl.html", {"stores": [], "payload": None})
    facility_id = request.GET.get("facility") or stores[0].pk
    facility = next((item for item in stores if str(item.pk) == str(facility_id)), stores[0])
    today = timezone.localdate().replace(day=1)
    selected = parse_month(request.GET.get("month"), today)
    period = request.GET.get("period", "rolling12")
    if period not in {"rolling12", "fiscal"}:
        period = "rolling12"
    compare = request.GET.get("compare", "mom")
    if compare not in {"mom", "yoy"}:
        compare = "mom"
    payload = store_pnl_data(facility, selected)
    baseline = payload["previous"] if compare == "mom" else payload["year_ago"]
    metrics = ["sales", "labor", "supplies", "royalty", "expense", "balance"]
    comparison_rows = []
    for key in metrics:
        current_value = payload["selected"].get(key)
        baseline_value = baseline.get(key) if baseline else None
        compared = compare_metric(current_value, baseline_value)
        if compared["rate"] is not None:
            compared["rate_percent"] = compared["rate"] * 100
        else:
            compared["rate_percent"] = None
        comparison_rows.append(
            {
                "key": key,
                "label": {
                    "sales": "売上合計",
                    "labor": "人件費",
                    "supplies": "備品費",
                    "royalty": "FCロイヤリティ",
                    "expense": "支出合計",
                    "balance": "差引収支",
                }[key],
                "current": current_value,
                "baseline": baseline_value,
                **compared,
            }
        )
    prev_month = add_months(selected, -1)
    next_month = add_months(selected, 1)
    return render(
        request,
        "core/store_pnl.html",
        {
            "stores": stores,
            "facility": facility,
            "selected": selected,
            "prev_month": prev_month,
            "next_month": next_month,
            "period": period,
            "compare": compare,
            "payload": payload,
            "baseline": baseline,
            "comparison_rows": comparison_rows,
                    },
    )
