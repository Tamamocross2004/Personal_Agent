import copy
import json
from pathlib import Path
import pytest

from folder_assistant.scope import scope_view,preview_next
from folder_assistant.exports import export_job
from test_folder_index import library,sync
from test_folder_reading import setup_reading,start
from test_folder_answers import finished,service,ask


def test_chain_deduplicates_recovered_main_segments_preserves_failures_and_excludes_sibling(library):
    _,jobs,chat=setup_reading(library)
    chat.invalid=True;parent=finished(jobs,start(library,jobs))
    frozen=copy.deepcopy(parent);chat.invalid=False
    retry=finished(jobs,start(library,jobs,resume_from=parent['id'],retry_split=True,max_batches=3))
    before=copy.deepcopy(jobs.list());calls=len(chat.calls)
    view=scope_view(jobs,retry['id'])
    assert view['counts']=={'not_sent':10,'delivery_unknown':0,'response_incomplete':0,'structure_checked':6}
    assert view['recovered_segments']==6 and view['confirmed_segments']==6
    assert all(s['failed_attempts']==1 and len(s['attempts'])==2 for s in view['segments'][:6])
    assert len(view['chain'])==2 and sum(c['known_tokens'] for c in view['chain'])==420
    assert view['chain'][0]['batches'][0]['usable_structure'] is False
    assert jobs.list()==before and jobs.get(parent['id'])==frozen and len(chat.calls)==calls
    sibling=finished(jobs,start(library,jobs,resume_from=parent['id'],batch_size=2))
    branch=scope_view(jobs,sibling['id'])
    assert branch['counts']['structure_checked']==2 and branch['counts']['response_incomplete']==4
    assert retry['id'] not in [c['task_id'] for c in branch['chain']]
    # A completed retry of a bounded interval isn't full-corpus completion.
    assert view['total_segments']==16 and view['counts']['not_sent']==10


def test_unknown_delivery_and_no_inference_from_prefix_or_context(library):
    _,jobs,chat=setup_reading(library)
    chat.fail_at=1;failed=finished(jobs,start(library,jobs))
    view=scope_view(jobs,failed['id'])
    assert view['counts']['delivery_unknown']==6 and view['confirmed_segments']==0
    assert view['counts']['not_sent']==10
    # Fake legacy counters aren't evidence; don't promote them or revalidate a response.
    failed['coverage'].update(returned_until=16,structured_segments=16)
    failed['batches']=[];jobs.save(failed)
    assert scope_view(jobs,failed['id'])['counts']['not_sent']==16
    chat.fail_at=None;first=finished(jobs,start(library,jobs))
    continuation=finished(jobs,start(library,jobs,resume_from=first['id'],batch_size=1))
    view=scope_view(jobs,continuation['id'])
    assert view['confirmed_segments']==7 and view['counts']['structure_checked']==7
    assert view['chain'][1]['batches'][0]['context_segments']==1


def test_paging_and_real_locations_and_invalid_boundaries(library):
    _,jobs,_=setup_reading(library,rows=65)
    value=finished(jobs,start(library,jobs))
    first=scope_view(jobs,value['id'],0,50);second=scope_view(jobs,value['id'],50,50)
    assert first['next_offset']==50 and len(first['segments'])==50
    assert [s['segment'] for s in second['segments']]==list(range(50,66))
    assert second['segments'][0]['source_location']['record']==51
    assert all('text' not in s for s in first['segments'])
    for offset,limit in [(-1,50),(0,101),(0,0),(67,50)]:
        with pytest.raises(ValueError):scope_view(jobs,value['id'],offset,limit)


@pytest.mark.parametrize('problem',['missing','cycle','different_scope'])
def test_broken_chain_warns_without_guessing_missing_history(library,problem):
    _,jobs,_=setup_reading(library)
    parent=finished(jobs,start(library,jobs))
    child=finished(jobs,start(library,jobs,resume_from=parent['id']))
    if problem=='missing':child['inputs']['resume_from']='absent';jobs.save(child)
    elif problem=='cycle':parent['inputs']['resume_from']=child['id'];jobs.save(parent)
    else:parent['inputs']['query']='different scope';jobs.save(parent)
    view=scope_view(jobs,child['id'])
    assert view['warnings'] and not view['chain_complete']
    if problem!='cycle':assert view['confirmed_segments']==6  # Child evidence only, not inherited twelve.


def test_preview_uses_same_resolution_as_start_no_calls_and_fixed_generation(library):
    generation,jobs,chat=setup_reading(library)
    chat.invalid=True;parent=finished(jobs,start(library,jobs));chat.invalid=False
    (library[0]/'table.csv').write_text('replacement\n',encoding='utf-8');sync(library)
    before=copy.deepcopy(jobs.list());calls=len(chat.calls)
    controls={'budget':20000,'max_batches':8,'output_tokens':2400,'batch_mode':'adaptive','batch_size':6,'expected_fields':2,'retry_split':True}
    preview=preview_next(jobs,parent['id'],controls)
    assert preview['generation']==generation and preview['operation_end']==6
    assert preview['operation_start']==0 and preview['first_batch_end']<=3
    assert preview['services']==['llm'] and not preview['cloud_calls']
    assert jobs.list()==before and len(chat.calls)==calls
    tiny=preview_next(jobs,parent['id'],{**controls,'batch_mode':'fixed','budget':256})
    assert tiny['services']==[] and tiny['first_batch_end']==0
    retry=finished(jobs,start(library,jobs,resume_from=parent['id'],**controls))
    assert retry['batches'][0]['next_offset']==preview['first_batch_end']
    assert retry['inputs']['range_end']==preview['operation_end']
    assert retry['inputs']['strength']==preview['strength']
    no_space=preview_next(jobs,retry['id'],{**controls,'retry_split':False,'output_tokens':128})
    assert not no_space['services'] and no_space['first_batch_end']==no_space['operation_start']
    library[1].revoke(library[2])
    assert scope_view(jobs,retry['id'])['chain']
    with pytest.raises(ValueError):preview_next(jobs,retry['id'],controls)


def test_scope_export_is_a_snapshot_and_cached_exports_are_not_upgraded(library):
    _,jobs,chat=setup_reading(library)
    chat.invalid=True;first=finished(jobs,start(library,jobs));chat.invalid=False
    original=Path(export_job(jobs,first['id'])['directory'])
    hashes={p.name:p.read_bytes() for p in original.iterdir() if p.is_file()}
    next_job=finished(jobs,start(library,jobs,resume_from=first['id'],retry_split=True,max_batches=8))
    directory=Path(export_job(jobs,next_job['id'])['directory'])
    projection=json.loads((directory/'sources.json').read_text('utf-8'))['scope_view']
    assert len(projection['chain'])==2 and projection['segments']==[]
    assert projection['recovered_segments']==6
    assert json.loads((directory/'record.json').read_text('utf-8'))==next_job
    export_job(jobs,first['id'])
    assert {p.name:p.read_bytes() for p in original.iterdir() if p.is_file()}==hashes


def test_limited_retrieval_never_claims_full_text_read_rate(library):
    sync(library);jobs,chat=service(library)
    value=finished(jobs,ask(library,jobs))
    view=scope_view(jobs,value['id'])
    assert view['mode']=='limited_retrieval' and view['counts'] is None
    assert view['confirmed_evidence']==len(value['evidence']) and view['segments']==[]
    assert view['semantic_status']=='not_certified'


def test_stopped_after_return_is_received_without_usable_structure(library):
    _,jobs,chat=setup_reading(library)
    chat.hook=lambda:jobs.stop(jobs.list()[0]['id'])
    value=finished(jobs,start(library,jobs))
    view=scope_view(jobs,value['id'])
    assert value['status']=='stopped' and view['counts']['response_incomplete']==6
    assert view['counts']['structure_checked']==0
