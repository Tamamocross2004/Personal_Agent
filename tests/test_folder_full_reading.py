"""New scope choices change delivery, never promote content correctness."""
import copy
import json
from pathlib import Path

import pytest
from docx import Document

from folder_assistant.full_reading import preview_full_reading,start_full_reading
from folder_assistant.exports import export_job
from folder_assistant.scope import scope_view
from test_folder_index import library,sync
from test_folder_answers import ask,finished,service


def test_docx_full_reading_delivers_all_selected_rows_not_only_retrieval_hits(library):
    docs,_,fid,index,embedding,_=library
    document=Document();table=document.add_table(rows=1,cols=2)
    table.rows[0].cells[0].text='装置';table.rows[0].cells[1].text='采样间隔'
    for n in range(9):
        cells=table.add_row().cells;cells[0].text=f'Node-{n}';cells[1].text=f'{n+7}ms'
    document.save(docs/'sampling.docx');generation=sync(library)
    selected=[d['file_id'] for d in index.store(fid).generation(generation)['files'] if d['name']=='sampling.docx']
    jobs,chat=service(library)
    original=finished(jobs,ask(library,jobs,query='整理全部采样间隔',file_ids=selected))
    assert len(original['evidence'])<10
    before=copy.deepcopy(original);export=Path(export_job(jobs,original['id'])['directory'])
    old_export={p.name:p.read_bytes() for p in export.iterdir() if p.is_file()}
    # Reindex, then remove source: the operation must retain the original extraction.
    (docs/'sampling.docx').unlink();sync(library)
    controls={'max_batches':8,'batch_mode':'fixed','batch_size':2}
    counts=(len(chat.calls),len(embedding.calls));history=copy.deepcopy(jobs.list())
    preview=preview_full_reading(jobs,original['id'],controls)
    assert preview['generation']==generation and preview['total_segments']==10
    assert preview['files'][0]['file_id']==selected[0] and len(preview['files'])==1
    assert preview['first_batch_end']==2 and preview['cloud_calls'] is False
    assert counts==(len(chat.calls),len(embedding.calls)) and jobs.list()==history
    full=finished(jobs,start_full_reading(jobs,original['id'],controls,preview['plan_hash']))
    assert full['coverage']['input_coverage_complete'] and full['coverage']['structured_coverage_complete']
    assert full['inputs']['generation']==generation and full['inputs']['file_ids']==selected
    assert full['inputs']['query']==original['inputs']['query'] and full['inputs']['start_offset']==0
    assert full['inputs']['strength']==original['inputs']['strength']
    assert 'resume_from' not in full['inputs'] and 'project_context' not in full['inputs']
    assert [e['segment'] for e in full['evidence'] if e['role']=='sequential']==list(range(10))
    assert all(e['name']=='sampling.docx' for e in full['evidence'])
    # Fake Chat only emits one excerpt each time. All sent does not mean all extracted.
    assert sum(len(b['result']['answer']['comparison']) for b in full['batches'])==5
    assert len(scope_view(jobs,full['id'])['chain'])==1
    assert jobs.get(original['id'])==before and len(embedding.calls)==counts[1]
    assert len(chat.calls)==counts[0]+5
    for messages,_ in chat.calls[counts[0]:]:
        assert 'project_context' not in json.loads(messages[1]['content'])
        assert '测试替身仅验证引用协议' not in messages[1]['content']
    assert {p.name:p.read_bytes() for p in export.iterdir() if p.is_file()}==old_export


@pytest.mark.parametrize('change',[{'budget':11000},{'output_tokens':1300},{'max_batches':4},
    {'batch_size':2},{'expected_fields':2},{'batch_mode':'fixed'}])
def test_changed_preview_controls_cannot_start_or_call(library,change):
    sync(library);jobs,chat=service(library);source=finished(jobs,ask(library,jobs))
    preview=preview_full_reading(jobs,source['id'],{})
    before=copy.deepcopy(jobs.list());calls=len(chat.calls)
    with pytest.raises(ValueError,match='计划已变化'):
        start_full_reading(jobs,source['id'],change,preview['plan_hash'])
    assert jobs.list()==before and len(chat.calls)==calls


def test_no_silent_upgrade_empty_retrieval_and_bounded_partial_work(library):
    docs,_,_,_,embedding,_=library
    (docs/'a.txt').write_text('\n'.join(f'记录{n} 温度 {n}K' for n in range(12)),encoding='utf-8')
    sync(library);jobs,chat=service(library)
    original=finished(jobs,ask(library,jobs,query='不存在的全部字段'))
    assert not chat.calls and original['inputs']['strategy']=='retrieval'
    assert original['result']['content_status']=='insufficient_evidence'
    count=len(embedding.calls)
    preview=preview_full_reading(jobs,original['id'],{'max_batches':1,'batch_mode':'fixed','batch_size':2})
    full=finished(jobs,start_full_reading(jobs,original['id'],{'max_batches':1,'batch_mode':'fixed','batch_size':2},preview['plan_hash']))
    assert len(chat.calls)==1 and len(embedding.calls)==count
    assert full['coverage']['remaining_segments']==10 and full['result']['content_status']=='partial'
    assert full['inputs']['budget']==10000 and full['inputs']['output_tokens']==1200
    assert full['review_status']=='not_reviewed'
    with pytest.raises(ValueError,match='已结束的局部问答'):
        preview_full_reading(jobs,full['id'],{})


def test_auth_terminal_source_and_hash_guards(library,tmp_path):
    sync(library);jobs,chat=service(library);source=finished(jobs,ask(library,jobs))
    preview=preview_full_reading(jobs,source['id'],{});calls=len(chat.calls)
    with pytest.raises(ValueError,match='先预览'):start_full_reading(jobs,source['id'],{},None)
    with pytest.raises(ValueError,match='先预览'):
        jobs.start(library[2],'answer',{'full_reading_from':source['id'],'strategy':'exhaustive'})
    for status in ('queued','running'):
        jobs.patch(source['id'],status=status)
        with pytest.raises(ValueError,match='已结束的局部问答'):preview_full_reading(jobs,source['id'],{})
    jobs.patch(source['id'],status=source['status'])
    with pytest.raises(ValueError,match='不能拆小重做'):preview_full_reading(jobs,source['id'],{'retry_split':True})
    other=tmp_path/'other';other.mkdir();foreign=library[1].grant(other)['id']
    with pytest.raises(ValueError,match='已结束的局部问答'):
        jobs.prepare_answer(foreign,{'full_reading_from':source['id'],'strategy':'exhaustive'})
    library[1].revoke(library[2])
    with pytest.raises(ValueError):preview_full_reading(jobs,source['id'],{})
    with pytest.raises(ValueError):start_full_reading(jobs,source['id'],{},preview['plan_hash'])
    assert len(chat.calls)==calls and jobs.get(source['id'])==source


def test_tiny_budget_preview_refuses_without_calls_and_unknown_source_usage_is_visible(library):
    sync(library);jobs,chat=service(library);chat.unknown=True
    source=finished(jobs,ask(library,jobs));calls=len(chat.calls)
    preview=preview_full_reading(jobs,source['id'],{'budget':256,'batch_mode':'fixed'})
    assert preview['source_usage_unknown'] and not preview['services']
    assert preview['decision']['stop_reason']=='token_budget' and not preview['first_batch']
    assert len(chat.calls)==calls and jobs.get(source['id'])==source


def test_model_configuration_changes_invalidate_preview(library):
    sync(library);jobs,chat=service(library);source=finished(jobs,ask(library,jobs))
    preview=preview_full_reading(jobs,source['id'],{});calls=len(chat.calls)
    jobs.index.settings=jobs.index.settings.model_copy(update={'agent_call_max_tokens':512})
    with pytest.raises(ValueError,match='计划已变化'):start_full_reading(jobs,source['id'],{},preview['plan_hash'])
    assert len(chat.calls)==calls
