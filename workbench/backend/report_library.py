"""Read-only, hash-checked local reports. No network, model or task mutations."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath


class LibraryError(ValueError):
    pass


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class ReportLibrary:
    def __init__(self, directory):
        self.root = Path(directory).resolve(strict=True)
        self.index_path = self.root / 'index.json'
        self.index = json.loads(self.index_path.read_text(encoding='utf-8'))
        if self.index.get('schema_version') != '1.0':
            raise LibraryError('不支持的本地财报库索引版本')
        self.records = self.index['reports']
        self.companies = json.loads((self.root / 'companies.json').read_text(encoding='utf-8'))['items']
        self.by_key, self.by_path = {}, {}
        for record in self.records:
            code, year = record.get('stock_code'), record.get('report_year')
            if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) or type(year) is not int or not 1990 <= year <= 2100:
                raise LibraryError('财报库公司代码或会计年度无效')
            if record.get('report_type') != 'annual' or not re.fullmatch(r'[a-f0-9]{64}', record.get('sha256', '')):
                raise LibraryError('财报库必须包含完整年报及有效 SHA-256')
            path = self.path_for(record)
            if path in self.by_path:
                raise LibraryError('财报库包含重复文件路径')
            self.by_path[path] = record
            self.by_key.setdefault((code, year), []).append(record)

    def path_for(self, record):
        relative = record['relative_path']
        if not isinstance(relative, str):
            raise LibraryError('财报库路径无效')
        parts = PurePosixPath(relative)
        if '\\' in relative or ':' in relative or parts.is_absolute() or '..' in parts.parts or not parts.parts or parts.parts[0] != 'reports':
            raise LibraryError('财报库文件路径越界')
        path = (self.root / relative).resolve()
        # Compare against the lexical root: a reports-directory junction must
        # not grant access outside the configured library.
        if not path.is_relative_to(self.root / 'reports'):
            raise LibraryError('财报库文件路径越界')
        return path

    def verified_metadata(self,value,expected_hash=None):
        path = Path(value).resolve()
        record = self.by_path.get(path)
        if record is None or self.path_for(record) != path:
            raise LibraryError('文件未收录于本地财报库')
        if expected_hash and expected_hash != record['sha256']:
            raise LibraryError('任务与财报库文件校验值不一致')
        if not path.is_file():
            raise LibraryError('本地财报文件缺失')
        return record

    def verified_path(self, value, expected_hash=None):
        record=self.verified_metadata(value,expected_hash)
        path=Path(value).resolve()
        if not path.is_file() or digest(path) != record['sha256']:
            raise LibraryError('本地财报缺失或 SHA-256 校验失败')
        with path.open('rb') as stream:
            if stream.read(5) != b'%PDF-':
                raise LibraryError('本地财报不是有效 PDF')
        return path

    def provide(self, code, year):
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) or type(year) is not int or not 1990 <= year <= 2100:
            raise LibraryError('请使用六位证券代码和整数会计年度')
        records = self.by_key.get((code, year), [])
        if not records:
            return None
        selected = [record for record in records if record.get('selected_for_comparison') is True]
        if len(selected) != 1:
            raise LibraryError(f'{code} 的 {year} 年财报版本不唯一，请人工选择')
        record = selected[0]
        path = self.verified_path(self.path_for(record))
        return {**record, 'local_path': str(path), 'manifest_path': str(self.index_path),
                'library_record': True, 'cache_status': 'verified_local_library',
                'library_snapshot': self.index.get('snapshot_version')}


@lru_cache(maxsize=8)
def _load(directory, index_stamp, companies_stamp):
    return ReportLibrary(directory)


def load_library(directory):
    root = Path(directory).resolve()
    if not root.exists():
        return None  # Existing installations without a library keep working.
    try:
        index, companies = (root / 'index.json').stat(), (root / 'companies.json').stat()
        return _load(str(root), (index.st_mtime_ns, index.st_size), (companies.st_mtime_ns, companies.st_size))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise LibraryError(f'本地财报库索引不可用：{exc}') from exc
