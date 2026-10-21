"""运行期自动保存的来源、版本、原文与中断边界，不访问模型服务。"""
import hashlib
import json
from pathlib import Path

import pytest

from agent.checkpoint import CheckpointStore
from agent.config import Settings
from agent.evidence import audit_mapping, sha
from agent.report_state import PROVENANCE, fallback_document, fallback_status
from fakes import ScriptedLLM, completion
from test_checkpoint import Crash
from main import research, resume_research


RAW = '存储阶段尚未进行。\r\n这里保留模型停止前的文字。'


def stopped_task(root, *, settings=None):
    # 预算内派发后，实际用量超估；保留这次已收到的截断原文，不额外调用收尾。
    model = ScriptedLLM([completion('{}'), completion(RAW,finish_reason='length',prompt_tokens=80000)])
    result = research(model, settings or Settings(_env_file=None, memory_enabled=False),
                      '不联网，列出本次尚未完成的事项。', 5, 80000, task_dir=root)
    return result, model


def test_no_budget_does_not_save_program_notice_as_model_report(tmp_path):
    model=ScriptedLLM([])
    result=research(model,Settings(_env_file=None,memory_enabled=False),
                    '不联网，说明材料。',5,1,task_dir=tmp_path)
    assert result.stop_reason=='budget' and result.answer=='' and not model.requests
    assert not (tmp_path/'report.md').exists() and not (tmp_path/PROVENANCE).exists()


def test_real_budget_fallback_keeps_raw_answer_and_records_actual_save(tmp_path, capsys):
    result, model = stopped_task(tmp_path)
    assert result.stop_reason == 'budget' and result.answer == RAW and len(model.requests) == 2
    data = json.loads((tmp_path / PROVENANCE).read_text(encoding='utf-8'))
    raw_file = (tmp_path / 'report.md').read_bytes()
    assert raw_file.endswith(RAW.encode('utf-8'))
    assert data['source_answer_sha256'] == sha(RAW)
    assert data['report_bytes_sha256'] == hashlib.sha256(raw_file).hexdigest()
    assert data['complete_at_save'] is False and data['review_status_at_save'] == 'not_reviewed'
    assert fallback_status(tmp_path)['status'] == 'current'
    review = json.loads((tmp_path / 'report.review.json').read_text(encoding='utf-8'))
    assert review['status'] == 'not_reviewed'
    assert review['report_sha256'] == sha((tmp_path / 'report.md').read_text(encoding='utf-8'))
    assert audit_mapping(tmp_path)['status'] == 'draft'
    events = [json.loads(line) for line in (tmp_path / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    notices = [e for e in events if e.get('action') == 'automatic_fallback_saved']
    assert len(notices) == 1 and notices[0]['kind'] == 'notice'
    assert events.index(notices[0]) > next(i for i, e in enumerate(events) if e['kind'] == 'run_end')
    assert sum(e['kind'] == 'llm_call' for e in events) == 2
    assert CheckpointStore(tmp_path).read()['status'] == 'complete'
    # 回放呈现程序后续保存事实，不改写先前模型回答。
    capsys.readouterr()
    from agent.replay import main as replay
    assert replay([str(tmp_path)]) == 0
    assert '自动保存' in capsys.readouterr().out


@pytest.mark.parametrize('reason', ['budget', 'max_steps', 'loop_detected'])
def test_save_description_depends_on_run_state_not_answer_keywords(tmp_path, reason):
    # 任意文本同样受来源标注；没有已知失败题目的词句检测。
    document, record = fallback_document('任意内容 α\n', reason)
    (tmp_path / 'report.md').write_bytes(document.encode('utf-8'))
    (tmp_path / PROVENANCE).write_text(json.dumps(record), encoding='utf-8')
    assert document.endswith('任意内容 α\n')
    assert fallback_status(tmp_path)['stop_reason'] == reason
    assert record['source_answer_sha256'] == sha('任意内容 α\n')


def test_formal_report_is_not_relabelled_or_overwritten_when_steps_run_out(tmp_path):
    content = '# 独立工具稿\n\n业务状态未知。'
    model = ScriptedLLM([completion('{}'), completion(tool_calls=[('w', 'write_report', {
        'content': content, 'complete': False})]), completion('停止前的文字')])
    result = research(model, Settings(_env_file=None, memory_enabled=False), '不联网。', 1, 80000, task_dir=tmp_path)
    assert result.stop_reason == 'max_steps'
    assert (tmp_path / 'report.md').read_text(encoding='utf-8') == content
    assert not (tmp_path / PROVENANCE).exists() and fallback_status(tmp_path) == {}


@pytest.mark.parametrize('change', ['changed', 'deleted', 'corrupt', 'wrong_schema'])
def test_metadata_never_certifies_changed_or_unreadable_reports(tmp_path, change):
    stopped_task(tmp_path)
    if change == 'changed': (tmp_path / 'report.md').write_text('新文件', encoding='utf-8')
    elif change == 'deleted': (tmp_path / 'report.md').unlink()
    elif change == 'corrupt': (tmp_path / PROVENANCE).write_text('[', encoding='utf-8')
    else:
        path = tmp_path / PROVENANCE
        data = json.loads(path.read_text(encoding='utf-8')); data['complete_at_save'] = True
        path.write_text(json.dumps(data), encoding='utf-8')
    assert fallback_status(tmp_path)['status'] == ('stale' if change in ('changed', 'deleted') else 'unavailable')


def test_legacy_text_alone_never_infers_runtime_provenance(tmp_path):
    (tmp_path / 'report.md').write_text('自动保存的未完成结果', encoding='utf-8')
    assert fallback_status(tmp_path) == {}


def test_interrupted_metadata_write_stays_uncertain_and_cannot_auto_resume(tmp_path, monkeypatch):
    import agent.evidence as evidence
    save = evidence.write_json
    def crash(path, data):
        save(path, data)
        if path.name == PROVENANCE: raise Crash()
    monkeypatch.setattr(evidence, 'write_json', crash)
    with pytest.raises(Crash): stopped_task(tmp_path)
    checkpoint = CheckpointStore(tmp_path)
    assert checkpoint.read()['pending']['kind'] == 'fallback'
    assert not checkpoint.status()['resumable']


def test_resume_after_committed_fallback_never_rewrites_or_replays_save(tmp_path, monkeypatch):
    import main
    calls = []
    original = main.save_research
    def once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1: raise Crash()
        return original(*args, **kwargs)
    monkeypatch.setattr(main, 'save_research', once)
    root = tmp_path / 'task'
    with pytest.raises(Crash):
        stopped_task(root, settings=Settings(_env_file=None, memory_enabled=True, memory_db_path=tmp_path / 'memory.db'))
    before = (root / 'report.md').read_bytes(), (root / PROVENANCE).read_bytes()
    assert CheckpointStore(root).status()['resumable']
    replacement = ScriptedLLM([])
    result = resume_research(root, llm=replacement)
    assert result.stop_reason == 'budget' and result.answer == RAW and not replacement.requests
    assert ((root / 'report.md').read_bytes(), (root / PROVENANCE).read_bytes()) == before
    events = [json.loads(line) for line in (root / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert sum(e.get('action') == 'automatic_fallback_saved' for e in events) == 1


def test_desktop_reads_versioned_provenance_and_internal_evidence_is_blocked(tmp_path):
    from desktop_app.manager import Manager
    from agent.artifacts import read_json_artifact
    manager = Manager(tmp_path, start=False, settings=Settings(_env_file=None, deepseek_api_key='test-only'))
    try:
        sid = manager.store.create_session()['id']
        row = manager.submit(sid, '不联网。', {'provider': 'deepseek', 'model': 'deepseek-flash',
                             'max_steps': 5, 'budget': 1000, 'memory': False})
        root = Path(row['task_dir'])
        result, _ = stopped_task(root)
        manager.store.set_run(row['id'], 'partial', {'stop_reason': result.stop_reason, 'answer': result.answer})
        assert manager.detail(row['id'])['provenance']['status'] == 'current'
        with pytest.raises(ValueError, match='内部记录'):
            read_json_artifact(root, PROVENANCE)
        (root / 'report.md').write_text('后续改稿', encoding='utf-8')
        assert manager.detail(row['id'])['provenance']['status'] == 'stale'
    finally: manager.close()
