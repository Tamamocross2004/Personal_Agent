import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from knowledge import report_probe as probe


@pytest.mark.parametrize('explicit',[False,True])
def test_freeze_does_not_rewrite_existing_run_and_checks_source_and_suite(tmp_path,monkeypatch,explicit):
    source=tmp_path/'source'; kb=source/'knowledge'; kb.mkdir(parents=True)
    for path in (source/'documents',kb/'documents',kb/'vectors'): path.mkdir()
    (source/'documents'/'a.txt').write_text('原文',encoding='utf-8')
    with sqlite3.connect(kb/'knowledge.sqlite3') as db: db.execute('create table sample(value text)')
    suite=tmp_path/'suite.json'; suite.write_text(json.dumps({'cases':[], 'max_steps':12,'token_budget':80000}),encoding='utf-8')
    monkeypatch.setattr(probe,'SUITE',tmp_path/'unused-default.json' if explicit else suite)
    monkeypatch.setattr(probe,'KnowledgeStore',lambda s: SimpleNamespace(generation=lambda:{'id':'frozen'}))
    root=tmp_path/'new'; plan=probe.freeze(root,source,suite if explicit else None)
    probe.validate(root,plan)
    with pytest.raises(ValueError,match='预算变化'):
        probe.validate(root,{**plan,'token_budget':plan['token_budget']+1})
    original=(root/'plan.json').read_bytes()
    with pytest.raises(ValueError,match='覆盖'): probe.freeze(root,source)
    assert (root/'plan.json').read_bytes()==original
    (root/'documents'/'a.txt').write_text('改变',encoding='utf-8')
    with pytest.raises(ValueError,match='快照变化'): probe.validate(root,plan)
    (root/'documents'/'a.txt').write_text('原文',encoding='utf-8')
    suite.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='题集变化'): probe.validate(root,plan)


def test_paid_run_refuses_existing_attempt_without_replacing_results(tmp_path,monkeypatch):
    (tmp_path/'plan.json').write_text(json.dumps({'suite':{'cases':[]}}),encoding='utf-8')
    result=tmp_path/'research-results.json'; result.write_text('{"original":true}',encoding='utf-8')
    monkeypatch.setattr(probe,'validate',lambda *args:None)
    with pytest.raises(ValueError,match='禁止覆盖'): probe.research(tmp_path)
    assert result.read_text(encoding='utf-8')=='{"original":true}'
