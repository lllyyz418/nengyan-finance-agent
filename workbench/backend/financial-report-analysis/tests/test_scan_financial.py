import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from financial_engine import analyze_scanned_financial_pages, read_pdf_document


def _line(text, x, y, confidence=0.99):
    return {"text": text, "page": 1, "bbox": [x, y, x + 70, y + 10],
            "method": "ocr", "confidence": confidence}


def _document(*, prior="500.00", growth="100.00%", title="测试股份有限公司2025年年度报告"):
    lines = [
        _line(title, 100, 20), _line("主要会计数据和财务指标", 50, 60),
        _line("单位：万元", 450, 80), _line("2025年", 200, 100),
        _line("2024年", 300, 100), _line("营业收入", 50, 120),
        _line("1,000.00", 200, 120), _line(prior, 300, 120),
        _line(growth, 400, 120),
    ]
    return {"document_id": "a" * 16, "file_sha256": "a" * 64,
            "file_name": "scan.pdf", "page_count": 1, "warnings": [],
            "pages": [{"page": 1, "read_method": "ocr", "lines": lines}]}


class ScanFinancialTests(unittest.TestCase):
    def test_selected_page_keeps_original_pdf_page_number(self):
        from PIL import Image
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project) as folder:
            path = Path(folder) / "two_pages.pdf"
            first = Image.new("RGB", (400, 500), "white")
            second = Image.new("RGB", (400, 500), "white")
            first.save(path, "PDF", resolution=72, save_all=True, append_images=[second])
            first.close()
            second.close()
            def fake_ocr(_image):
                return [{"text": "主要会计数据和财务指标", "confidence": 0.99,
                         "box": [[20, 20], [200, 20], [200, 40], [20, 40]]}]
            result = read_pdf_document(path, page_numbers=[2], max_pages=1,
                                       ocr_engine=fake_ocr, dpi=72)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["pages_read"], 1)
        self.assertEqual(result["pages"][0]["page"], 2)
        self.assertEqual(result["pages"][0]["lines"][0]["page"], 2)
        self.assertFalse(any(item["code"] == "PAGE_LIMIT" for item in result["warnings"]))

    def test_exact_amount_unit_and_disclosed_growth(self):
        with patch("financial_engine.scan_financial.read_pdf_document", return_value=_document()):
            result = analyze_scanned_financial_pages("unused.pdf", page_numbers=[1],
                                                      company_name="测试股份有限公司", report_year=2025)
        candidate = result["candidates"][0]
        self.assertEqual((candidate["value"], candidate["previous_value"]),
                         ("10000000.00", "5000000.00"))
        self.assertEqual(candidate["calculated_yoy_percent"], "100.00")
        self.assertEqual(candidate["status"], "machine_checked")
        self.assertEqual(candidate["sources"]["current"]["bbox"], [200, 120, 270, 130])
        self.assertEqual(result["review_status"], "requires_human_review")

    def test_disclosed_growth_conflict_and_identity_conflict_withhold_check(self):
        with patch("financial_engine.scan_financial.read_pdf_document",
                   return_value=_document(growth="99.00%", title="其他股份有限公司2025年年度报告")):
            result = analyze_scanned_financial_pages("unused.pdf", page_numbers=[1],
                                                      company_name="测试股份有限公司", report_year=2025)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["status"], "needs_review")
        self.assertFalse(candidate["checks"]["reported_yoy_percentage"])
        self.assertFalse(result["identity_claim"]["verified_from_pdf"])
        self.assertIn("OCR_IDENTITY_UNVERIFIED", [warning["code"] for warning in result["warnings"]])
        self.assertIn("OCR_YOY_MISMATCH", [warning["code"] for warning in result["warnings"]])

    def test_missing_unit_does_not_create_amount_candidate(self):
        document = _document()
        document["pages"][0]["lines"] = [line for line in document["pages"][0]["lines"]
                                          if "单位" not in line["text"]]
        with patch("financial_engine.scan_financial.read_pdf_document", return_value=document):
            result = analyze_scanned_financial_pages("unused.pdf", page_numbers=[1],
                                                      company_name="测试股份有限公司", report_year=2025)
        self.assertFalse(result["candidates"])
        self.assertIn("OCR_UNIT_MISSING", [warning["code"] for warning in result["warnings"]])


if __name__ == "__main__":
    unittest.main()
