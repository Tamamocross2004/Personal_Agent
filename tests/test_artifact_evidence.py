"""产物版本与网页覆盖约束：离线契约测试，不以模型回答措辞作为答案。"""
import json

import pytest

from agent.artifacts import MAX_BYTES, read_json_artifact
from agent.evidence import Claim, EvidenceLedger, audit_mapping, write_json
from agent.review import build_review_messages
from tools.evidence import make_read_evidence
from tools.registry import ToolRegistry
from tools.session import ResearchSession


def setup(tmp_path):
    session = ResearchSession()
    session.evidence = EvidenceLedger(tmp_path, '检查本次文件')
    return session, ToolRegistry([make_read_evidence(session)])


@pytest.mark.parametrize('value,kind', [(2.75,'float'), ('2.75','str'), (True,'bool'), (None,'NoneType')])
def test_file_types_are_observed_independently_of_requested_types(tmp_path,value,kind):
    (tmp_path/'measurement.json').write_text(json.dumps({'reading':value}),encoding='utf-8')
    session, reg = setup(tmp_path)
    result = reg.execute('read_evidence',{'artifact_filename':'measurement.json'})
    assert not result.is_error
    source = json.loads(result.content)['id']
    record = session.evidence.records[source]
    snapshot = json.loads(record['text'])
    assert snapshot['field_types']=={'reading':kind}
    claim = Claim(quote='已检查产物字段类型',kind='observed',
                  supports=[{'source_id':source,'excerpt':f'"reading": "{kind}"'}])
    assert session.evidence.validate(claim.quote,[claim])[0]['semantic_support']=='not_certified'
    for unsupported in ('direct','calculated'):
        with pytest.raises(ValueError):
            session.evidence.validate(claim.quote,[claim.model_copy(update={'kind':unsupported})])


def test_modified_or_deleted_file_invalidates_mapping_and_read_only_audit(tmp_path):
    path = tmp_path/'sensor.json'
    path.write_text('{"count":7}',encoding='utf-8')
    session,reg=setup(tmp_path)
    source=json.loads(reg.execute('read_evidence',{'artifact_filename':path.name}).content)['id']
    assert session.evidence.artifact(path.name)==source  # 相同版本不重复占账本
    claim=Claim(quote='文件有count字段',kind='observed',
                supports=[{'source_id':source,'excerpt':'"count": "int"'}])
    report=tmp_path/'report.md'
    report.write_text(claim.quote,encoding='utf-8')
    mapping=session.evidence.prepare(report,claim.quote,[claim],complete=True)
    write_json(report.with_suffix('.evidence.json'),mapping)
    assert audit_mapping(tmp_path)['structural_links_valid']
    path.write_text('{"count":"7"}',encoding='utf-8')
    with pytest.raises(ValueError,match='版本已变化'):
        audit_mapping(tmp_path)
    assert session.evidence.prepare(report,claim.quote,None,complete=True)['status']=='stale'
    old_read=json.loads(reg.execute('read_evidence',{'source_id':source}).content)
    assert old_read['current_version_status']=='changed'
    new_source=session.evidence.artifact(path.name)
    assert new_source!=source
    path.unlink()
    with pytest.raises(ValueError,match='已失效'):
        session.evidence.validate(claim.quote,[Claim.model_validate({**claim.model_dump(),
            'supports':[{'source_id':new_source,'excerpt':'"count": "str"'}]})])


def test_historical_snapshot_remains_a_reading_record_but_not_current_file_proof(tmp_path):
    path=tmp_path/'catalog.json'
    path.write_text('{"edition":"alpha"}',encoding='utf-8')
    session,reg=setup(tmp_path)
    source=session.evidence.artifact(path.name)
    claim=Claim(quote='初次记录的edition为alpha',kind='observed',artifact_version='snapshot',
                limits='仅描述该记录时刻的文件，不代表当前内容或真实版本发布。',
                supports=[{'source_id':source,'excerpt':'alpha'}])
    path.write_text('{"edition":"beta"}',encoding='utf-8')
    report=tmp_path/'report.md'
    report.write_text(claim.quote,encoding='utf-8')
    mapping=session.evidence.prepare(report,claim.quote,[claim],complete=True)
    write_json(report.with_suffix('.evidence.json'),mapping)
    assert audit_mapping(tmp_path)['structural_links_valid']
    assert session.evidence.prepare(report,claim.quote,None,complete=True)['status']=='mapped'
    with pytest.raises(ValueError,match='版本已变化'):
        session.evidence.validate(claim.quote,[claim.model_copy(update={'artifact_version':'current'})])
    with pytest.raises(ValueError,match='snapshot仅用于'):
        session.evidence.validate(claim.quote,[claim.model_copy(update={'limits':''})])
    path.unlink()
    assert audit_mapping(tmp_path)['structural_links_valid']
    read=json.loads(reg.execute('read_evidence',{'source_id':source}).content)
    assert read['current_version_status']=='unavailable'
    assert read['recorded_at'] and 'alpha' in read['excerpt']


@pytest.mark.parametrize('filename',['../outside.json','sub/data.json','..\\outside.json',
                                    'C:data.json','evidence.json','report.review.json'])
def test_artifact_paths_and_internal_records_are_not_read(tmp_path,filename):
    session,reg=setup(tmp_path)
    assert reg.execute('read_evidence',{'artifact_filename':filename}).is_error
    assert not any(r['kind']=='artifact' for r in session.evidence.records.values())


def test_invalid_and_large_json_and_truncation_do_not_invent_field_types(tmp_path):
    path=tmp_path/'data.json'
    for content in ('{"x":NaN}', '{"x":', '{"x":"\ufffd"}'):
        path.write_text(content,encoding='utf-8')
        item=read_json_artifact(tmp_path,path.name)
        assert ('parse_error' in item)==(content!='{"x":"\ufffd"}')
    path.write_bytes(b'{"x":"\xff"}')
    assert read_json_artifact(tmp_path,path.name)['parse_error']=='invalid_json'
    path.write_text(json.dumps({'description':'a'*5000}),encoding='utf-8')
    item=read_json_artifact(tmp_path,path.name)
    assert item['possibly_truncated'] and 'field_types' not in item
    path.write_bytes(b' '*(MAX_BYTES+1))
    with pytest.raises(ValueError,match='超过'):
        read_json_artifact(tmp_path,path.name)


def web(ledger,start,end,version='v1',size=8):
    return ledger.add('web','abcdefgh'[start:end],url='https://example.org/manual',
                      start=start,end=end,page_text_sha256=version,page_characters=size)


def test_whole_document_requires_same_version_complete_delivery(tmp_path):
    ledger=EvidenceLedger(tmp_path,'读取手册')
    source=web(ledger,0,4)
    claim=Claim(quote='手册的描述',kind='direct',scope='whole_document',
                supports=[{'source_id':source,'excerpt':'abcd'}])
    with pytest.raises(ValueError,match='未完整送达'):
        ledger.validate(claim.quote,[claim])
    assert ledger.validate(claim.quote,[claim.model_copy(update={'scope':'excerpt'})])
    web(ledger,4,8,version='v2')
    with pytest.raises(ValueError,match='未完整送达'):
        ledger.validate(claim.quote,[claim])
    web(ledger,3,8)  # 同版本范围并集才可补齐，重叠不重复计数
    assert ledger.validate(claim.quote,[claim])[0]['semantic_support']=='not_certified'


def test_legacy_claims_restore_without_scope_and_non_web_scope_rejected(tmp_path):
    ledger=EvidenceLedger(tmp_path,'用户材料')
    claim=Claim(quote='用户材料',kind='direct',supports=[{'source_id':'user-1','excerpt':'用户材料'}])
    path=tmp_path/'report.md'
    mapping=ledger.prepare(path,claim.quote,[claim],complete=True)
    del mapping['claims'][0]['scope']
    write_json(path.with_suffix('.evidence.json'),mapping)
    assert ledger.prepare(path,claim.quote,None,complete=True)['status']=='mapped'
    with pytest.raises(ValueError,match='仅用于'):
        ledger.validate(claim.quote,[claim.model_copy(update={'scope':'whole_document'})])


def test_review_and_claim_snapshot_share_file_hash_and_types(tmp_path):
    session,_=setup(tmp_path)
    (tmp_path/'output.json').write_text('{"temperature":18.25}',encoding='utf-8')
    source=session.evidence.artifact('output.json')
    messages,_=build_review_messages('生成 output.json',tmp_path,session)
    sample=json.loads(messages[1]['content'])['json_samples'][0]
    snapshot=json.loads(session.evidence.records[source]['text'])
    assert sample==snapshot
    assert sample['field_types']=={'temperature':'float'}


def test_symlink_artifact_is_not_read(tmp_path):
    target=tmp_path/'actual.json'
    target.write_text('{"x":1}',encoding='utf-8')
    link=tmp_path/'linked.json'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('当前系统没有创建符号链接权限')
    with pytest.raises(ValueError,match='普通文件'):
        read_json_artifact(tmp_path,link.name)


def test_missing_web_version_never_certifies_complete_delivery(tmp_path):
    ledger=EvidenceLedger(tmp_path,'阅读')
    source=web(ledger,0,8,version=None)
    assert not ledger.web_coverage(ledger.records[source])['full_page_delivered']


def test_agent_can_read_artifact_then_save_a_version_bound_observed_claim(tmp_path):
    from agent.loop import Agent
    from fakes import ScriptedLLM, completion
    from tools.report import make_write_report
    session,reg=setup(tmp_path)
    (tmp_path/'inventory.json').write_text('{"available":true}',encoding='utf-8')
    report='# 检查\n\n## 摘要\n文件available字段为布尔类型。\n\n## 方法与范围\n仅检查文件。\n\n## 正文\n不核验库存实物。\n\n## 结论\n仅确认文件类型。\n\n## 局限性\n不保证业务事实。\n\n## 参考资料\n本次inventory.json。'
    reg=ToolRegistry([make_read_evidence(session),make_write_report(tmp_path,session)])
    llm=ScriptedLLM([completion('无需规划'),
        completion(tool_calls=[('a','read_evidence',{'artifact_filename':'inventory.json'})]),
        completion(tool_calls=[('b','write_report',{'content':report,'claims':[{
            'quote':'文件available字段为布尔类型。','kind':'observed',
            'supports':[{'source_id':'artifact-1','excerpt':'"available": "bool"'}]}]})]),
        completion('完成文件检查')])
    Agent(llm,reg,task_dir=tmp_path,research_session=session,review_reports=False).run('检查inventory.json类型')
    assert audit_mapping(tmp_path)['source_kinds']==['artifact']
    assert any('artifact-1' in m.get('content','') for m in llm.requests[-1]['messages'] if m['role']=='tool')
