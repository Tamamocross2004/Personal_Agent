import copy,json
from pathlib import Path
import pytest

from agent.context import estimate_tokens
from folder_assistant.answers import prepare_messages
from folder_assistant.project_context import select,source_identity
from folder_assistant.exports import export_job
from test_folder_index import library,sync
from test_folder_projects import setup,run,request
from test_folder_answers import finished


def follow(projects,jobs,p,v,query,**controls):
    pre=projects.preview_followup(p['id'],v['id'],query,controls)
    job=finished(jobs,projects.followup(p['id'],v['id'],query,{**controls,'plan_hash':pre['plan_hash']}))
    return job,pre


def test_incremental_request_never_resends_unchanged_answer_or_dialogue(library):
    projects,jobs,chat,p=setup(library);p,v,_=run(projects,jobs,p)
    a,_=follow(projects,jobs,p,v,'alpha')
    old=copy.deepcopy(v);saved_jobs=copy.deepcopy(jobs.list())
    (library[0]/'b.txt').write_text('beta replacement','utf-8');sync(library)
    p,new,pre=run(projects,jobs,p)
    assert pre['target_names']==['b.txt'] and pre['carried_groups']==1
    for batch in jobs.get(new['job_id'])['batches']:
        req=batch['request'];raw=json.dumps(req['messages'],ensure_ascii=False)
        assert 'alpha original' not in raw and 'beta original' not in raw
        body=json.loads(req['messages'][1]['content'])
        assert body['project_context']['previous_result_snippets']==[] and body['project_context']['prior_dialogue']==[]
        history=req['transmission']['project_history']
        assert history['history_characters']==0 and not history['result_background_enabled']
        assert req['estimated_input_tokens']==estimate_tokens(json.dumps(req['messages'],ensure_ascii=False))
    assert projects.version(v['id'])==old and all(jobs.get(j['id'])==j for j in saved_jobs)
    assert any(g['generation']!=new['generation'] for g in new['groups'])


def test_default_followup_is_exact_source_related_and_recent_dialogue_opt_in(library):
    projects,jobs,chat,p=setup(library);p,v,_=run(projects,jobs,p)
    a,pre_a=follow(projects,jobs,p,v,'alpha')
    b,pre_b=follow(projects,jobs,p,v,'beta')
    body=json.loads(b['request']['messages'][1]['content']);ctx=body['project_context']
    assert ctx['previous_result_snippets'] and not ctx['prior_dialogue']
    assert 'alpha original' not in json.dumps(b['request']['messages'],ensure_ascii=False)
    assert 'beta original' in json.dumps(ctx,ensure_ascii=False)
    history=b['request']['transmission']['project_history']
    assert len(history['selected_groups'])==1 and history['excluded_groups_not_delivered']==1
    assert b['request']['transmission']==pre_b['transmission']
    before=len(chat.calls)
    pre=projects.preview_followup(p['id'],v['id'],'beta',{'include_dialogue':True})
    assert len(chat.calls)==before and len(pre['transmission']['project_history']['selected_dialogue_jobs'])==2
    enabled,_=follow(projects,jobs,p,v,'beta',include_dialogue=True)
    assert 'alpha original' in json.dumps(enabled['request']['messages'],ensure_ascii=False)
    assert enabled['request']['transmission']['project_history']['recent_dialogue_enabled']


def test_background_can_be_disabled_without_changing_evidence_or_original_results(library):
    projects,jobs,chat,p=setup(library);p,v,_=run(projects,jobs,p)
    before=copy.deepcopy(v)
    yes,_=follow(projects,jobs,p,v,'alpha')
    no,_=follow(projects,jobs,p,v,'alpha',include_results=False)
    assert no['evidence']==yes['evidence']
    body=json.loads(no['request']['messages'][1]['content'])
    assert not body['project_context']['previous_result_snippets']
    assert no['request']['estimated_input_tokens']<yes['request']['estimated_input_tokens']
    assert projects.version(v['id'])==before and projects.get(p['id'])['head']==v['id']


def test_revision_background_scopes_per_batch_and_continuation_drops_old_results(library):
    projects,jobs,_,p=setup(library);p,v,_=run(projects,jobs,p)
    ids=[d['file_id'] for d in v['files']]
    p,revised,_=run(projects,jobs,p,action='revise',file_ids=ids,instruction='重新整理原文',max_batches=1)
    batch=jobs.get(revised['job_id'])['batches'][0];sent=batch['request']['transmission']
    assert len(sent['project_history']['selected_groups'])==1
    candidate_names={s['name'] for g in sent['project_history']['selected_groups'] for s in g['sources']}
    assert candidate_names=={e['name'] for e in batch['evidence']}
    p,continued,_=run(projects,jobs,p,action='continue')
    assert jobs.get(continued['job_id'])['batches'][0]['request']['transmission']['project_history']['history_characters']==0


def test_exact_text_hash_and_block_identity_prevent_stale_or_partial_background(library):
    projects,jobs,_,p=setup(library);p,v,_=run(projects,jobs,p)
    ctx=projects._context(p,v,'',action='followup');evidence=v['groups'][0]['evidence']
    selected,audit=select(ctx,evidence);assert selected['previous_result_snippets']
    for key,value in [('file_sha256','different'),('source_block_id','elsewhere'),('text',evidence[0]['text'][:3])]:
        changed=copy.deepcopy(evidence);changed[0][key]=value
        result,_=select(ctx,changed);assert not result['previous_result_snippets']
    assert not select(ctx,[{k:v for k,v in e.items() if k!='file_id'} for e in evidence])[0]['previous_result_snippets']
    frozen=copy.deepcopy(ctx);prepare_messages({'query':'x','project_context':ctx},evidence)
    assert ctx==frozen


def test_mixed_file_history_is_not_partially_rewritten_to_look_relevant(library):
    projects,jobs,_,p=setup(library);p,v,_=run(projects,jobs,p,batch_size=6)
    ctx=projects._context(p,v,'',action='followup');evidence=v['groups'][0]['evidence']
    assert select(ctx,evidence)[0]['previous_result_snippets']
    assert not select(ctx,evidence[:1])[0]['previous_result_snippets']


def test_previews_are_local_plan_bound_and_actual_trace_is_exported(library):
    projects,jobs,chat,p=setup(library);p,v,_=run(projects,jobs,p)
    before=len(chat.calls);embeddings=len(library[4].calls)
    pre=projects.preview_followup(p['id'],v['id'],'alpha',{})
    assert not pre['cloud_calls'] and len(chat.calls)==before and len(library[4].calls)==embeddings
    with pytest.raises(ValueError,match='预览'):
        projects.followup(p['id'],v['id'],'alpha',{'include_results':False,'plan_hash':pre['plan_hash']})
    assert len(chat.calls)==before
    job,_=follow(projects,jobs,p,v,'alpha')
    out=Path(export_job(jobs,job['id'])['directory']);old=(out/'record.json').read_bytes()
    assert '发送构成' in (out/'summary.md').read_text('utf-8')
    assert json.loads(old)['request']['transmission']==job['request']['transmission']
    assert export_job(jobs,job['id'])['cached'] and (out/'record.json').read_bytes()==old


def test_no_match_never_calls_chat_and_never_fills_from_old_answers(library):
    projects,jobs,chat,p=setup(library);p,v,_=run(projects,jobs,p);before=len(chat.calls)
    result,pre=follow(projects,jobs,p,v,'nonexistentuniqueword')
    assert not pre['will_call_model'] and len(chat.calls)==before
    assert result['result']['content_status']=='insufficient_evidence' and 'request' not in result


def test_large_history_remains_bounded_and_omissions_are_reported(library):
    projects,jobs,_,p=setup(library);p,v,_=run(projects,jobs,p)
    # Large prior answers may be unavailable as background; never truncate their text.
    j=jobs.get(v['entries'][0]['job_id']);j['batches'][0]['result']['answer']['findings'][0]['statement']='x'*7000;jobs.save(j)
    ctx=projects._context(p,v,'',action='followup')
    assert ctx['omitted_groups']>=1
    assert sum(len(json.dumps(c,ensure_ascii=False)) for c in ctx['candidates'])<=6000
    assert all('x'*7000 not in c['text'] for c in ctx['candidates'])
    for name in ('include_results','include_dialogue'):
        with pytest.raises(ValueError):projects.preview_followup(p['id'],v['id'],'alpha',{name:'yes'})


def test_legacy_request_context_and_saved_old_records_are_not_rewritten():
    ctx={'objective':'legacy','previous_result_snippets':['unmodified old background']}
    assert select(ctx,[])==(ctx,None)
