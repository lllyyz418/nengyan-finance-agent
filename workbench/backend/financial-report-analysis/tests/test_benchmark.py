import os
import unittest
from pathlib import Path

from benchmarks.check import evaluate


class ManualBenchmarkTests(unittest.TestCase):
    def test_original_pdfs_match_manually_checked_values(self):
        sample_dir = Path(os.environ.get("AI_FINANCE_SAMPLE_DIR", Path(__file__).resolve().parents[3]))
        if not all((sample_dir / name).is_file() for name in ("byd.pdf", "宁德时代.pdf", "中国能建.pdf")):
            raise unittest.SkipTest("Three original PDF samples are required for the manual benchmark")
        result = evaluate(sample_dir)
        self.assertEqual(result["counts"]["error"], 0, result["outcomes"])
        self.assertEqual(sum(result["counts"].values()), 135)
        self.assertGreater(result["counts"]["pass"], 0)


if __name__ == "__main__":
    unittest.main()
