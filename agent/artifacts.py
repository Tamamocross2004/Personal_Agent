"""有界读取任务 JSON 产物；只记录文件事实，不执行内容或认证业务语义。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


INTERNAL_JSON = {'messages.json', 'evidence.json', 'task_contract.json', 'memory_context.json', 'plan.json'}
MAX_BYTES = 65536


def artifact_path(root: Path, filename: str) -> Path:
    if (not filename or '/' in filename or '\\' in filename or ':' in filename
            or Path(filename).name != filename or not filename.endswith('.json')
            or filename in INTERNAL_JSON or filename.endswith(('.evidence.json', '.review.json', '.quality.json'))):
        raise ValueError('只能读取任务目录内的 JSON 产物文件名，不能读取内部记录或路径')
    path = root / filename
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('产物不存在或不是任务目录内的普通文件')
    return path


def read_json_artifact(root: Path, filename: str, limit: int = 4000) -> dict:
    path = artifact_path(root, filename)
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('JSON 产物超过有界读取上限 65536 字节，未检查内容')
    text = raw.decode('utf-8-sig', errors='replace')
    item = {'path': filename, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'content': text[:limit], 'possibly_truncated': len(text) > limit}
    # 采样与类型信息采用相同可见范围，不从未展示部分推断字段。
    if not item['possibly_truncated']:
        try:
            def invalid_constant(value):
                raise ValueError('非标准 JSON 常量：' + value)
            value = json.loads(raw.decode('utf-8-sig'), parse_constant=invalid_constant)
            item['json_type'] = type(value).__name__
            if isinstance(value, dict):
                item['field_types'] = {k: type(v).__name__ for k, v in list(value.items())[:32]}
                item['omitted_field_types'] = max(0, len(value) - 32)
        except (ValueError, UnicodeError):
            item['parse_error'] = 'invalid_json'
    return item


def artifact_version_status(root: Path, record: dict) -> str:
    try:
        current = read_json_artifact(root, record['artifact_path'], limit=0)
    except (OSError, ValueError):
        return 'unavailable'
    return 'current' if current['sha256'] == record['artifact_sha256'] else 'changed'


def check_artifact_version(root: Path, record: dict) -> None:
    status = artifact_version_status(root,record)
    label = f"{record.get('id','')}（{record['artifact_path']}）"
    if status == 'unavailable':
        raise ValueError(f'产物证据{label}已失效：文件缺失、不可读或超过上限，请重新读取产物')
    if status == 'changed':
        raise ValueError(f'产物{label}版本已变化，不能证明当前文件；请重读并更新关联。'
                         '若仅描述当时读取的历史快照，使用artifact_version=snapshot并填写limits，不能据此声明当前内容。')
