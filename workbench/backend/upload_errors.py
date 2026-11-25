"""Public upload failures: actionable Chinese copy, never raw exceptions."""
from __future__ import annotations


FAILURES = {
    "DOCUMENT_TYPE_UNRECOGNIZED": ("未识别为可分析的财务报告", "未在文件中识别到年度报告、半年度报告或受支持公告的内容。", "请重新选择报告文件；如果这确实是一份财报，请尝试文字版 PDF，或使用扫描件人工复核功能。", False),
    "PDF_TEXT_UNREADABLE": ("无法提取可识别的文字", "当前读取范围内没有足够的可读文字，文件可能是扫描件、图片或空白页面。", "请上传文字版 PDF；扫描件可在“扫描件人工复核”中选择对应页面进行 OCR，并核对识别结果。", False),
    "INVALID_PDF": ("文件不是有效的 PDF", "文件内容不符合 PDF 格式要求，仅修改扩展名不能转换文件格式。", "请将原文件导出为 PDF，再重新上传。", False),
    "PDF_DAMAGED": ("PDF 无法正常读取", "文件内容可能不完整、已损坏，或使用了当前解析器不支持的格式。", "请先确认文件能正常打开，再重新下载或导出一份 PDF 上传。", False),
    "PDF_ENCRYPTED": ("PDF 需要密码或受加密保护", "解析器无法读取受保护的文件内容。", "请上传你有权访问、且可直接读取的 PDF 版本。", False),
    "FILE_TOO_LARGE": ("文件超过大小限制", "单个 PDF 最大支持 80 MB。", "请压缩 PDF 或选择较小的完整报告文件。", False),
    "FILE_TYPE_UNSUPPORTED": ("不支持这种文件格式", "当前上传入口仅支持 PDF 文件。", "请将原文件导出为 PDF，再重新选择。", False),
    "NO_FINANCIAL_DATA": ("未提取到可用于分析的财务数据", "报告内容未形成可用的财务分析结果，可能缺少正文、财务报表或当前解析器支持的数据。", "请检查是否为完整报告；扫描件请使用 OCR 人工复核。", False),
    "ANNOUNCEMENT_UNSUPPORTED": ("该公告暂不支持事件分析", "已识别为公告，但未识别到当前支持的事件类型。", "请上传受支持的重大事件公告，或上传年报、半年报进行财务分析。", False),
    "FILE_UNAVAILABLE": ("暂时无法读取上传文件", "已保存的文件不存在或当前没有读取权限。", "请检查文件访问权限，然后重新上传。", False),
    "ANALYSIS_TIMEOUT": ("分析等待超时", "本次处理未能在规定时间内返回结果。", "请稍后重试；如果文件是扫描件，可尝试文字版 PDF。", True),
    "SERVICE_UNAVAILABLE": ("分析服务暂时不可用", "处理所需的服务或依赖暂时无法使用。", "请确认后端及解析依赖正常，再重试分析。", True),
    "SYSTEM_ERROR": ("文件处理时发生异常", "暂时无法确定本次失败的具体原因，不能据此判断文件不是财报。", "可重试一次；如果仍失败，请保留诊断信息用于排查。", True),
}


def public_failure(code: str, stage: str = "文件校验") -> dict:
    code = code if code in FAILURES else "SYSTEM_ERROR"
    title, message, suggestion, retryable = FAILURES[code]
    return dict(code=code, title=title, message=message, suggestion=suggestion,
                retryable=retryable, stage=stage)


class UploadAnalysisError(RuntimeError):
    def __init__(self, code: str, stage: str = "文件识别"):
        self.code, self.stage = code, stage
        super().__init__(public_failure(code)["message"])


def validate_upload_result(payload: dict) -> None:
    """Reject unsupported/empty responses before identity enrichment or AI calls.

    A real report with some missing metrics remains a successful partial result.
    """
    if not isinstance(payload, dict):
        raise UploadAnalysisError("SYSTEM_ERROR")
    codes = {item.get("code") for item in payload.get("warnings", []) if isinstance(item, dict)}
    if payload.get("status") == "unsupported":
        raise UploadAnalysisError("PDF_TEXT_UNREADABLE" if "PDF_TEXT_UNREADABLE" in codes
                                  else "DOCUMENT_TYPE_UNRECOGNIZED")
    analysis = payload.get("analysis")
    if payload.get("document_type") == "announcement":
        if not isinstance(analysis, dict):
            raise UploadAnalysisError("SYSTEM_ERROR")
        if not analysis.get("event") or not analysis["event"].get("event_type"):
            raise UploadAnalysisError("ANNOUNCEMENT_UNSUPPORTED")
        return
    if not isinstance(analysis, dict):
        raise UploadAnalysisError("SYSTEM_ERROR")
    if payload.get("status") == "failed" or not analysis.get("documents") or not (
            analysis.get("facts") or analysis.get("metrics")):
        raise UploadAnalysisError("NO_FINANCIAL_DATA", "财务数据提取")


def classify_upload_exception(exc: Exception, stage: str) -> dict:
    if isinstance(exc, UploadAnalysisError):
        return public_failure(exc.code, exc.stage)
    chain, current = [], exc
    while current is not None and len(chain) < 6:
        chain.append(current)
        current = current.__cause__ or current.__context__
    names = {type(item).__name__ for item in chain}
    # Match known parser failures, not arbitrary user-supplied text.
    if names & {"PDFPasswordIncorrect", "PDFEncryptionError", "PasswordError"}:
        code = "PDF_ENCRYPTED"
    elif isinstance(exc, (FileNotFoundError, PermissionError)):
        code = "FILE_UNAVAILABLE"
    elif any(isinstance(item, TimeoutError) for item in chain) or names & {"ReadTimeout", "ConnectTimeout", "APITimeoutError"}:
        code = "ANALYSIS_TIMEOUT"
    elif names & {"PDFSyntaxError", "PSEOF", "PSSyntaxError", "PDFNoValidXRef", "PdfminerException", "FileDataError", "EmptyFileError"}:
        code = "PDF_DAMAGED"
    elif type(exc).__name__ == "DocumentReadError":
        text = str(exc)
        code = ("INVALID_PDF" if "no PDF signature" in text else
                "FILE_TOO_LARGE" if "size limit" in text else
                "SERVICE_UNAVAILABLE" if "BACKEND_UNAVAILABLE" in text or "OCR_UNAVAILABLE" in text else
                "PDF_DAMAGED" if "PDF_READ_FAILED" in text else "SYSTEM_ERROR")
    elif names & {"ConnectionError", "ConnectError", "APIConnectionError"}:
        code = "SERVICE_UNAVAILABLE"
    else:
        code = "SYSTEM_ERROR"
    return public_failure(code, stage)
