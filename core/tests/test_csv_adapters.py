from django.test import SimpleTestCase

from core.csv_adapters import (
    CsvValidationError,
    decode_csv,
    parse_money,
    parse_month_text,
    parse_payroll,
    parse_royalty,
    parse_sales,
    parse_supplies,
)


def csv_bytes(rows, encoding="utf-8-sig"):
    text = "\n".join(",".join(f'"{cell}"' if "," in str(cell) else str(cell) for cell in row) for row in rows)
    return text.encode(encoding)


class CsvAdapterTests(SimpleTestCase):
    def test_utf8_bom(self):
        self.assertEqual(decode_csv("店舗".encode("utf-8-sig")), "店舗")

    def test_cp932(self):
        self.assertEqual(decode_csv("店舗".encode("cp932")), "店舗")

    def test_money_commas(self):
        self.assertEqual(parse_money("1,234"), 1234)

    def test_money_parentheses(self):
        self.assertEqual(parse_money("(500)"), -500)

    def test_reiwa_month(self):
        self.assertEqual(parse_month_text("令和8年8月分給与"), "2026-08")

    def test_sales_extracts_leading_zero_store_code(self):
        data = csv_bytes(
            [
                ["支払月", "2026/09"],
                ["店舗", "売上", "クリーニング"],
                ["071 上永谷店", "1,200", "900"],
                ["合計", "1200", "900"],
            ]
        )
        parsed = parse_sales(data)
        self.assertEqual(parsed["months"], ["2026-09"])
        self.assertEqual(parsed["rows"][0]["code"], "071")
        self.assertEqual(parsed["rows"][0]["cleaning_sales"], 900)

    def test_sales_rejects_duplicate_code(self):
        data = csv_bytes(
            [
                ["支払月", "2026/09"],
                ["店舗", "売上", "クリーニング"],
                ["071 上永谷店", "1", "1"],
                ["071 上永谷店", "2", "2"],
            ]
        )
        with self.assertRaises(CsvValidationError):
            parse_sales(data)

    def test_royalty_uses_k_plus_l_not_m(self):
        row = ["", "071 上永谷店", "", "", "", "", "", "", "", "99", "1000", "100", "99999"]
        parsed = parse_royalty(
            csv_bytes([["title"], ["支払月", "2026/09"], ["h"] * 13, row, ["", "合計（1件）"]])
        )
        self.assertEqual(parsed["rows"][0]["fee_tax_inclusive"], 1100)

    def test_royalty_rejects_short_detail(self):
        with self.assertRaises(CsvValidationError):
            parse_royalty(csv_bytes([["x"], ["支払月", "2026/09"], ["h"], ["", "071 店舗"]]))

    def payroll_fixture(self):
        return csv_bytes(
            [
                ["令和8年8月分給与", ""],
                ["", "匿名 太郎（00003）"],
                ["支給額合計", "300000"],
                ["健康保険料", "15000"],
                ["介護保険料", "2000"],
                ["厚生年金保険", "27000"],
                ["雇用保険料", "1000"],
            ]
        )

    def test_payroll_keeps_leading_zero(self):
        parsed = parse_payroll(self.payroll_fixture())
        self.assertEqual(parsed["rows"][0]["employee_code"], "00003")

    def test_payroll_social_estimate_excludes_employment_insurance(self):
        parsed = parse_payroll(self.payroll_fixture())
        self.assertEqual(parsed["rows"][0]["employer_social_estimate"], 44000)

    def test_payroll_rejects_duplicate_required_label(self):
        data = self.payroll_fixture().decode("utf-8-sig") + "\n健康保険料,15000"
        with self.assertRaises(CsvValidationError):
            parse_payroll(data.encode("utf-8"))

    def supplies_fixture(self, total="300"):
        return csv_bytes(
            [
                ["", "", "", "", "2026年", "", "", ""],
                ["", "", "", "", "8月", "", "", ""],
                ["", "", "", "", "事務用品", "衛生用品", "工場", "合計"],
                ["1", "店舗", "拠点", "A店", "", "", "", "100"],
                ["2", "店舗", "拠点", "B店", "", "", "", "200"],
                ["", "", "", "全体合計", "", "", "", total],
            ]
        )

    def test_supplies_blank_is_zero_and_total_matches(self):
        parsed = parse_supplies(self.supplies_fixture())
        self.assertEqual(parsed["months"], ["2026-08"])
        self.assertEqual(sum(row["total_cost"] for row in parsed["rows"]), 300)

    def test_supplies_total_mismatch_rejects_everything(self):
        with self.assertRaisesMessage(CsvValidationError, "差額"):
            parse_supplies(self.supplies_fixture("299"))

    def test_supplies_rejects_unknown_selected_month(self):
        with self.assertRaises(CsvValidationError):
            parse_supplies(self.supplies_fixture(), ["2026-09"])
