import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from financial_engine import (analyze_announcement, classify_disclosure,
                              discover_event_announcements, read_pdf_document)
from financial_engine.announcements import _extract_amounts


def line(text, y, *, method="pdf_text", confidence=None):
    return {"text": text, "page": 1, "bbox": [90.0, y, 510.0, y + 15],
            "method": method, "confidence": confidence}


def document(lines):
    return {"document_id": "testhash12345678", "file_sha256": "a" * 64,
            "file_name": "sample.pdf", "page_count": 1, "pages_read": 1,
            "status": "text_extracted", "warnings": [],
            "pages": [{"page": 1, "page_type": "text", "read_method": "pdf_text", "lines": lines}]}


class AnnouncementTests(unittest.TestCase):
    def test_split_bid_amount_and_unsigned_contract(self):
        source = document([
            line("A 股代码：601868 A 股简称：中国能建", 20),
            line("中国能源建设股份有限公司", 35),
            line("重大工程中标的公告", 50),
            line("中标江阴苏龙2×66万千瓦四期扩建项目工程EPC总承包项目，中", 100),
            line("标金额约为50.0508亿元人民币。项目建设内容包括新建2×", 126),
            line("目前相关方尚未正式签署合同，上述项目仍存在不确定性。", 160),
            line("2025 年 6 月 17 日", 200),
        ])
        with patch("financial_engine.announcements.read_pdf_document", return_value=source):
            result = analyze_announcement("unused.pdf")
        amount = result["event"]["amount_candidates"][0]
        self.assertEqual((amount["role"], amount["amount"], amount["currency"]),
                         ("bid_amount", "5005080000", "CNY"))
        self.assertEqual(amount["sources"][0]["bbox"], [90.0, 126, 510.0, 141])
        self.assertEqual([item["bbox"][1] for item in amount["label_sources"]], [100, 126])
        self.assertEqual(result["event"]["event_status"], "contract_unsigned")
        self.assertEqual(result["event"]["stock_code"], "601868")
        self.assertEqual(result["event"]["reported_identity"]["announcement_date"], "2025-06-17")
        self.assertFalse(result["event"]["amounts_are_audited_financial_facts"])

    def test_fund_size_and_company_commitment_are_distinct_and_not_converted(self):
        source = document([
            line("关于全资子公司参与投资产业投资基金的公告", 50),
            line("基金目标规模为15亿美元（最终规模将根据实际情况确定）", 100),
            line("香港时代拟认缴出资额不高于2.25亿美元。", 125),
            line("基金目标规模为15亿美元，香港时代拟认缴出资额不高于2.25亿美元。", 150),
        ])
        with patch("financial_engine.announcements.read_pdf_document", return_value=source):
            result = analyze_announcement("unused.pdf")
        amounts = {item["role"]: item for item in result["event"]["amount_candidates"]}
        self.assertEqual(len(amounts), 2)
        self.assertEqual((amounts["fund_target_size"]["amount"], amounts["fund_target_size"]["currency"]),
                         ("1500000000", "USD"))
        self.assertEqual((amounts["company_commitment"]["amount"], amounts["company_commitment"]["currency"]),
                         ("225000000", "USD"))
        self.assertEqual(len(amounts["fund_target_size"]["sources"]), 2)
        self.assertEqual(len(amounts["company_commitment"]["sources"]), 2)

    def test_multiple_different_amounts_same_role_require_review(self):
        source = document([line("重大工程中标公告", 50), line("中标金额为10亿元人民币。", 100),
                           line("中标金额调整为11亿元人民币。", 125)])
        with patch("financial_engine.announcements.read_pdf_document", return_value=source):
            result = analyze_announcement("unused.pdf")
        self.assertEqual(result["review_status"], "requires_human_review")
        self.assertIn("MULTIPLE_AMOUNTS_SAME_ROLE", [item["code"] for item in result["warnings"]])

    def test_large_decimal_amount_is_exact_and_zero_requires_review(self):
        source = document([line("重大工程中标公告", 50),
                           line("中标金额为123456789012345678901234567890.12元人民币。", 100)])
        with patch("financial_engine.announcements.read_pdf_document", return_value=source):
            result = analyze_announcement("unused.pdf")
        self.assertEqual(result["event"]["amount_candidates"][0]["amount"],
                         "123456789012345678901234567890.12")
        zero = document([line("重大工程中标公告", 50), line("中标金额为0元人民币。", 100)])
        with patch("financial_engine.announcements.read_pdf_document", return_value=zero):
            result = analyze_announcement("unused.pdf")
        self.assertEqual(result["review_status"], "requires_human_review")
        self.assertIn("ZERO_EVENT_AMOUNT", [warning["code"] for warning in result["warnings"]])

    def test_non_announcement_is_rejected(self):
        source = document([line("2025年年度报告", 50), line("营业收入 1,000万元", 100)])
        with patch("financial_engine.announcements.read_pdf_document", return_value=source):
            result = analyze_announcement("unused.pdf")
        self.assertIsNone(result["event"])
        self.assertEqual(result["routing"]["route"], "annual_parser")

    def test_catalogue_pdf_identity_conflict_is_not_silently_merged(self):
        source = document([line("证券代码：601868", 20), line("重大工程中标公告", 50),
                           line("中标金额为10亿元人民币。", 100)])
        with patch("financial_engine.announcements.read_pdf_document", return_value=source):
            result = analyze_announcement("unused.pdf", metadata={"stock_code": "300750"})
        self.assertIsNone(result["event"]["stock_code"])
        self.assertEqual(result["event"]["identity_quality"], "conflict")
        self.assertIn("SECURITY_CODE_CONFLICT", [warning["code"] for warning in result["warnings"]])

    def test_official_catalogue_discovery_filters_and_local_date(self):
        security = {"stock_code": "601868", "org_id": "GSD123", "exchange_column": "sse"}
        timestamp = int(datetime(2025, 6, 16, 16, tzinfo=timezone.utc).timestamp() * 1000)
        def record(identifier, title):
            return {"secCode": "601868", "secName": "中国能建", "announcementId": identifier,
                    "announcementTitle": title, "announcementTime": timestamp,
                    "adjunctUrl": f"finalpage/2025-06-17/{identifier}.PDF"}
        payload = {"announcements": [record("1", "中国能源建设重大工程<em>中标</em>公告"),
                                     record("2", "投资者关系管理制度公告")], "hasMore": False}
        with patch("financial_engine.disclosure._post_form", return_value=payload):
            found = discover_event_announcements(security, "2025-06-17", "2025-06-17",
                                                 keywords=("中标",))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["published_date_cn"], "2025-06-17")
        self.assertEqual(found[0]["title"], "中国能源建设重大工程中标公告")

    def test_image_only_pdf_uses_injected_ocr_and_maps_pixel_box_to_pdf_points(self):
        from PIL import Image
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project) as directory:
            path = Path(directory) / "scan.pdf"
            image = Image.new("RGB", (600, 800), "white")
            image.save(path, "PDF", resolution=72)
            image.close()
            def fake_ocr(pil_image):
                self.assertGreaterEqual(pil_image.width, 600)
                return [{"text": "重大工程中标公告", "confidence": 0.99,
                         "box": [[100, 100], [300, 100], [300, 130], [100, 130]]}]
            result = read_pdf_document(path, ocr_engine=fake_ocr, dpi=72)
            disabled = read_pdf_document(path, enable_ocr=False)
        self.assertEqual(result["pages"][0]["page_type"], "scan_candidate")
        self.assertEqual(result["pages"][0]["read_method"], "ocr")
        self.assertEqual(result["pages"][0]["lines"][0]["bbox"], [100.0, 100.0, 300.0, 130.0])
        self.assertEqual(classify_disclosure(result)["route"], "announcement_parser")
        self.assertEqual(disabled["status"], "needs_review")
        self.assertEqual(disabled["pages"][0]["read_method"], "ocr_disabled")


if __name__ == "__main__":
    unittest.main()
