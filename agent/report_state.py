"""自动保存报告的来源记录；只描述保存事实，不认证模型文字。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


PROVENANCE = 'report.provenance.json'


def fallback_document(answer: str, stop_reason: str) -> tuple[str, dict]:
    saved_at = datetime.now(timezone.utc).isoformat()
    header = ('# 自动保存的未完成结果\n\n'
              '> 保存说明（由程序生成）：本文件已自动保存。以下为任务停止前的模型原文，'
              '保存时未经过报告审稿，不代表完整交付。原文中的文件或审稿状态是模型生成时的描述。\n\n'
              f'- 停止原因：`{stop_reason}`\n- 自动保存时间（UTC）：{saved_at}\n\n'
              '---\n\n## 停止前的模型原文\n\n')
    content = header + answer
    digest = lambda text: hashlib.sha256(text.encode('utf-8')).hexdigest()
    return content, {'schema_version': 1, 'kind': 'automatic_fallback', 'saved_by': 'runtime',
        'saved_at': saved_at, 'stop_reason': stop_reason, 'complete_at_save': False,
        'review_status_at_save': 'not_reviewed', 'source_answer_sha256': digest(answer),
        'report_bytes_sha256': digest(content)}


def fallback_status(root: Path) -> dict:
    """有界只读核对来源记录与当前文件；缺失不推断成自动保存。"""
    path = root / PROVENANCE
    if not path.exists():
        return {}
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError('来源记录不是有界普通文件')
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1
                or data.get('kind') != 'automatic_fallback' or data.get('saved_by') != 'runtime'
                or data.get('complete_at_save') is not False or data.get('review_status_at_save') != 'not_reviewed'
                or data.get('stop_reason') not in ('budget', 'max_steps', 'loop_detected')
                or not isinstance(data.get('saved_at'), str)):
            raise ValueError('来源记录格式无效')
        for key in ('source_answer_sha256', 'report_bytes_sha256'):
            value = data.get(key)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('来源摘要无效')
        public = {k: data[k] for k in ('kind', 'saved_by', 'saved_at', 'stop_reason', 'complete_at_save',
                  'review_status_at_save', 'source_answer_sha256', 'report_bytes_sha256')}
        report = root / 'report.md'
        if report.is_symlink() or not report.is_file() or report.stat().st_size > 20_000_000:
            return {**public, 'status': 'stale'}
        current = hashlib.sha256(report.read_bytes()).hexdigest() == data['report_bytes_sha256']
        return {**public, 'status': 'current' if current else 'stale'}
    except (OSError, ValueError, UnicodeError):
        return {'status': 'unavailable'}
