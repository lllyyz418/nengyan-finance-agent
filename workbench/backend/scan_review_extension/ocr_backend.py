"""Lazy CPU OCR adapter; model and runtime files stay in the host backend data directory."""
from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.RLock()
_engines = {}


def make_ocr(model_dir: str | Path):
    folder = Path(model_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)

    def run(image):
        with _lock:
            if str(folder) not in _engines:
                try:
                    from rapidocr import RapidOCR
                except ImportError as exc:
                    raise RuntimeError("OCR_UNAVAILABLE：请在后端 Python 环境安装 requirements-scan.txt。") from exc
                # Explicit path prevents implicit model writes to site-packages/C drive.
                _engines[str(folder)] = RapidOCR(params={"Global.model_root_dir": str(folder),
                                                         "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                                                         "EngineConfig.onnxruntime.inter_op_num_threads": 1})
            result = _engines[str(folder)](image)
            boxes, texts, scores = result.boxes, result.txts, result.scores
            if boxes is None or texts is None or scores is None:
                return []
            return [{"box": box, "text": text, "confidence": score}
                    for box, text, score in zip(boxes, texts, scores)]
    return run
