import csv
import io
import re
from datetime import date


class CsvValidationError(ValueError):
    pass


def decode_csv(content: bytes) -> str:
    if len(content) > 10 * 1024 * 1024:
        raise CsvValidationError("ファイルサイズは10MB以下にしてください。")
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise CsvValidationError("文字コードはUTF-8またはCP932にしてください。")


def read_rows(content: bytes) -> list[list[str]]:
    text = decode_csv(content)
    rows = [[cell.strip().replace("\u3000", " ") for cell in row] for row in csv.reader(io.StringIO(text))]
    if not rows:
        raise CsvValidationError("CSVが空です。")
    return rows


def parse_money(value: str, blank_zero: bool = False) -> int:
    normalized = value.strip().replace(",", "").replace("¥", "").replace("￥", "")
    if normalized == "" and blank_zero:
        return 0
    if normalized == "":
        raise CsvValidationError("必須の金額が空欄です。")
    negative = normalized.startswith("(") and normalized.endswith(")")
    if negative:
        normalized = normalized[1:-1]
    try:
        amount = int(normalized)
    except ValueError as exc:
        raise CsvValidationError(f"金額「{value}」を整数円として読めません。") from exc
    return -amount if negative else amount


def parse_month_text(text: str) -> str:
    western = re.search(r"(20\d{2})\s*[/-年]\s*(1[0-2]|0?[1-9])", text)
    if western:
        return f"{int(western.group(1)):04d}-{int(western.group(2)):02d}"
    era = re.search(r"令和\s*(\d+)\s*年\s*(1[0-2]|0?[1-9])\s*月", text)
    if era:
        return f"{2018 + int(era.group(1)):04d}-{int(era.group(2)):02d}"
    raise CsvValidationError(f"対象年月を解釈できません: {text}")


def find_month(row: list[str]) -> str:
    for index, cell in enumerate(row):
        if "支払月" in cell and index + 1 < len(row) and row[index + 1]:
            return parse_month_text(row[index + 1])
        if re.search(r"20\d{2}\s*[/-年]\s*\d{1,2}", cell):
            return parse_month_text(cell)
    raise CsvValidationError("支払月が見つかりません。")


def split_store(value: str) -> tuple[str, str]:
    match = re.match(r"^\s*([0-9A-Za-z_-]+)\s+(.+?)\s*$", value)
    if not match:
        raise CsvValidationError(f"店舗コードと名称を読めません: {value}")
    return match.group(1), match.group(2)


def is_summary(value: str) -> bool:
    compact = value.replace(" ", "")
    return not compact or "合計" in compact or "注記" in compact or compact.startswith("※")


def parse_sales(content: bytes) -> dict:
    rows = read_rows(content)
    if len(rows) < 3:
        raise CsvValidationError("売上CSVは3行以上必要です。")
    month = find_month(rows[0])
    result = []
    seen = set()
    for row_number, row in enumerate(rows[2:], start=3):
        if not row or is_summary(row[0]):
            continue
        if len(row) < 3:
            raise CsvValidationError(f"{row_number}行目の列が不足しています。")
        code, name = split_store(row[0])
        if code in seen:
            raise CsvValidationError(f"店舗コード {code} が重複しています。")
        seen.add(code)
        result.append(
            {
                "code": code,
                "source_name": name,
                "sales_total": parse_money(row[1]),
                "cleaning_sales": parse_money(row[2]),
            }
        )
    if not result:
        raise CsvValidationError("売上明細がありません。")
    return {"kind": "SALES", "months": [month], "rows": result}


def parse_royalty(content: bytes) -> dict:
    rows = read_rows(content)
    if len(rows) < 4:
        raise CsvValidationError("ロイヤリティCSVは4行以上必要です。")
    month = find_month(rows[1])
    result = []
    seen = set()
    for row_number, row in enumerate(rows[3:], start=4):
        store = row[1] if len(row) > 1 else ""
        if is_summary(store):
            continue
        if len(row) < 12:
            raise CsvValidationError(f"{row_number}行目の列が不足しています。")
        code, name = split_store(store)
        if code in seen:
            raise CsvValidationError(f"店舗コード {code} が重複しています。")
        seen.add(code)
        fee = parse_money(row[10])
        tax = parse_money(row[11])
        result.append(
            {"code": code, "source_name": name, "fee_ex_tax": fee, "fee_tax": tax, "fee_tax_inclusive": fee + tax}
        )
    if not result:
        raise CsvValidationError("ロイヤリティ明細がありません。")
    return {"kind": "ROYALTY", "months": [month], "rows": result}


PAYROLL_LABELS = {
    "payment_base": "支給額合計",
    "other_total": "その他合計",
    "social_insurance_total": "社会保険料合計",
    "employment_insurance": "雇用保険料",
}


def parse_payroll(content: bytes, skip_employee_codes: set[str] | None = None) -> dict:
    rows = read_rows(content)
    if len(rows) < 3:
        raise CsvValidationError("給与CSVの行が不足しています。")
    month = parse_month_text(" ".join(rows[0]))
    headers = rows[1]
    skip_employee_codes = {code.strip() for code in (skip_employee_codes or set())}
    employees = []
    for column, header in enumerate(headers):
        match = re.search(r"(.+?)\s*[（(]\s*([^）)]+)\s*[）)]", header)
        if match:
            code = match.group(2).strip()
            if code in skip_employee_codes:
                continue
            employees.append({"column": column, "name": match.group(1).strip(), "employee_code": code})
    if not employees:
        raise CsvValidationError("2行目に従業員列がありません。")
    label_rows = {}
    for key, label in PAYROLL_LABELS.items():
        matches = [row for row in rows[2:] if any(cell.strip() == label for cell in row[:3])]
        if len(matches) != 1:
            raise CsvValidationError(f"給与項目「{label}」は1行だけ必要です（検出: {len(matches)}行）。")
        label_rows[key] = matches[0]
    result = []
    for employee in employees:
        column = employee.pop("column")
        values = {}
        for key, row in label_rows.items():
            if column >= len(row):
                raise CsvValidationError(f"{employee['employee_code']} の給与列が不足しています。")
            values[key] = parse_money(row[column], blank_zero=True)
        total_payment = values["payment_base"] + values["other_total"]
        employer_social = values["social_insurance_total"] + int(round(values["employment_insurance"] * 2.3))
        result.append(
            {
                **employee,
                "total_payment": total_payment,
                "health_employee_deduction": 0,
                "care_employee_deduction": 0,
                "pension_employee_deduction": 0,
                "employer_social_estimate": employer_social,
                "payment_base": values["payment_base"],
                "other_total": values["other_total"],
                "social_insurance_total": values["social_insurance_total"],
                "employment_insurance": values["employment_insurance"],
            }
        )
    return {"kind": "PAYROLL", "months": [month], "rows": result}


def parse_supplies(content: bytes, selected_months: list[str] | None = None) -> dict:
    rows = read_rows(content)
    if len(rows) < 5:
        raise CsvValidationError("備品CSVの行が不足しています。")
    max_columns = max(len(row) for row in rows[:3])
    month_columns = {}
    current_year = None
    current_month = None
    for column in range(4, max_columns):
        top = [rows[index][column] if column < len(rows[index]) else "" for index in range(3)]
        joined = " ".join(top)
        year_match = re.search(r"(20\d{2})\s*年?", joined)
        month_match = re.search(r"(1[0-2]|0?[1-9])\s*月", joined)
        if year_match:
            current_year = int(year_match.group(1))
        if month_match:
            current_month = int(month_match.group(1))
        if any(cell.strip() == "合計" for cell in top) and current_year and current_month:
            month_columns[f"{current_year:04d}-{current_month:02d}"] = column
    if not month_columns:
        raise CsvValidationError("1〜3行目から月別の「合計」列を特定できません。")
    months = selected_months or sorted(month_columns)
    invalid = sorted(set(months) - set(month_columns))
    if invalid:
        raise CsvValidationError(f"CSVに存在しない月です: {', '.join(invalid)}")
    detail_rows = []
    total_row = None
    for row in rows[3:]:
        name = row[3].strip() if len(row) > 3 else ""
        if not name:
            continue
        if "全体合計" in name or name == "合計":
            total_row = row
            continue
        detail_rows.append(row)
    if total_row is None:
        raise CsvValidationError("最下部の全体合計行がありません。")
    result = []
    for month in months:
        column = month_columns[month]
        calculated = 0
        for row in detail_rows:
            amount = parse_money(row[column] if column < len(row) else "", blank_zero=True)
            calculated += amount
            result.append({"month": month, "source_name": row[3].strip(), "total_cost": amount})
        expected = parse_money(total_row[column] if column < len(total_row) else "", blank_zero=True)
        if calculated != expected:
            raise CsvValidationError(
                f"{month}: 施設合計 {calculated:,}円、全体合計 {expected:,}円、差額 {calculated - expected:,}円"
            )
    return {"kind": "SUPPLIES", "months": months, "rows": result}


PARSERS = {
    "SALES": parse_sales,
    "ROYALTY": parse_royalty,
    "PAYROLL": parse_payroll,
    "SUPPLIES": parse_supplies,
}
