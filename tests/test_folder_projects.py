import copy,json
from pathlib import Path
import pytest
from folder_assistant.projects import Projects
from folder_assistant.project_exports import export_version
from test_folder_index import library,sync
from test_folder_answers import service


def setup(library):
    docs=library[0];(docs/'a.txt').write_text('alpha original',encoding='utf-8');(docs/'b.txt').write_text('beta original',encoding='utf-8')
    sync(library);jobs,chat=service(library);projects=Projects(jobs)
    p=projects.create(library[2],'资料项目','整理资料原值',['摘录'])
    return projects,jobs,chat,p


def request(p,**kw):
    return {'action':'update','revision':p['revision'],'expected_head':p['head'],'budget':20000,
            'max_batches':8,'batch_mode':'fixed','batch_size':1,'output_tokens':1200,**kw}


def run(projects,jobs,p,**kw):
    req=request(p,**kw);preview=projects.preview(p['id'],req)
    projects.start(p['id'],req,preview['plan_hash'])
    for thread in list(jobs.threads.values()):thread.join(15)
    value=projects.get(p['id']);assert not value['pending']
    return value,projects.version(value['head']),preview


def test_initial_edition_followup_and_revision_preserve_other_results(library):
    projects,jobs,chat,p=setup(library)
    p,v,preview=run(projects,jobs,p)
    assert v['status']=='structured' and len(v['entries'])==2 and v['coverage']['usable_segments']==2
    assert preview['target_names']==['a.txt','b.txt']
    assert projects._plan(p,request(p,instruction='旧的修订要求'))['instruction']==''
    before=copy.deepcopy(v);old_jobs=copy.deepcopy(jobs.list())
    asked=projects.followup(p['id'],v['id'],'alpha',{'budget':10000})
    jobs.threads[asked['id']].join(15)
    assert projects.get(p['id'])['head']==v['id']
    sent=json.loads(chat.calls[-1][0][1]['content'])
    assert sent['project_context']['previous_version']==v['id']
    assert sent['project_context']['fields']==['摘录'] and sent['project_context']['previous_result_snippets']
    again=projects.followup(p['id'],v['id'],'beta',{'budget':10000});jobs.threads[again['id']].join(15)
    assert json.loads(chat.calls[-1][0][1]['content'])['project_context']['prior_dialogue'][0]['question']=='alpha'
    a=next(d['file_id'] for d in v['files'] if d['name']=='a.txt')
    p,new,_=run(projects,jobs,p,action='revise',file_ids=[a],instruction='只重新整理选定文件的原文')
    assert len(new['entries'])==2 and new['parent_id']==v['id']
    assert len(set(e['job_id'] for e in new['entries']))==2
    assert projects.version(v['id'])==before
    assert all(jobs.get(j['id'])==j for j in old_jobs)


def test_incremental_reuses_exact_unchanged_files_and_retracts_deleted_sources(library):
    projects,jobs,chat,p=setup(library);p,v,_=run(projects,jobs,p)
    original_export=Path(export_version(projects,v['id'])['directory']);old_bytes={f.name:f.read_bytes() for f in original_export.iterdir() if f.is_file()}
    (library[0]/'a.txt').write_text('alpha revised',encoding='utf-8');sync(library)
    p,new,pre=run(projects,jobs,p)
    assert pre['target_names']==['a.txt'] and pre['carried_groups']==1
    assert len(new['entries'])==2 and new['status']=='structured'
    assert any(g['generation']!=new['generation'] for g in new['groups'])  # Carried evidence keeps its real original index.
    (library[0]/'b.txt').unlink();sync(library)
    calls=len(chat.calls);p,deleted,pre=run(projects,jobs,p)
    assert not pre['has_work'] and pre['changes']['removed'] and len(chat.calls)==calls
    assert len(deleted['files'])==1 and len(deleted['entries'])==1
    assert all(e['name']=='a.txt' for g in deleted['groups'] for e in g['evidence'])
    assert export_version(projects,v['id'])['cached']
    assert {f.name:f.read_bytes() for f in original_export.iterdir() if f.is_file()}==old_bytes


def test_mixed_file_batch_dependencies_expand_update_scope(library):
    projects,jobs,_,p=setup(library);p,_,_=run(projects,jobs,p,batch_size=6)
    (library[0]/'b.txt').write_text('beta changed',encoding='utf-8');sync(library)
    pre=projects.preview(p['id'],request(p))
    assert set(pre['target_names'])=={'a.txt','b.txt'} and pre['carried_groups']==0


def test_partial_output_remains_partial_and_continuation_appends_without_duplicates(library):
    projects,jobs,chat,p=setup(library)
    p,v,_=run(projects,jobs,p,max_batches=1)
    assert v['status']=='partial' and v['coverage']['usable_segments']==1
    chat.partial=True;p,failed,_=run(projects,jobs,p,action='continue')
    assert failed['status']=='partial' and len(failed['entries'])==1
    chat.partial=False;p,done,_=run(projects,jobs,p,action='continue')
    assert done['status']=='structured' and len(done['entries'])==2
    assert len({(e['job_id'],e['batch']) for e in done['entries']})==2
    assert done['semantic_status']=='not_certified' and done['review_status']=='not_reviewed'


def test_stale_plan_and_configuration_changes_fail_before_paid_calls(library):
    projects,jobs,chat,p=setup(library);req=request(p);pre=projects.preview(p['id'],req)
    (library[0]/'a.txt').write_text('new data',encoding='utf-8');sync(library)
    with pytest.raises(ValueError,match='范围已变化'):projects.start(p['id'],req,pre['plan_hash'])
    assert not chat.calls
    p,v,_=run(projects,jobs,p)
    p=projects.update(p['id'],p['revision'],'新标题',p['config']['objective'],p['config']['fields'])
    assert not projects.preview(p['id'],request(p))['has_work']
    p=projects.update(p['id'],p['revision'],'新标题','另一个整理目标',['摘录','原值'])
    pre=projects.preview(p['id'],request(p))
    assert pre['changes']['config_changed'] and len(pre['target_files'])==2 and pre['carried_groups']==0
    with pytest.raises(ValueError,match='设置变化'):projects.preview(p['id'],request(p,action='revise',file_ids=[v['files'][0]['file_id']],instruction='重新整理'))


def test_revocation_allows_old_views_and_export_but_blocks_new_work(library):
    projects,jobs,chat,p=setup(library);p,v,_=run(projects,jobs,p)
    library[1].revoke(library[2]);calls=len(chat.calls)
    assert projects.get(p['id'])['head']==v['id'] and projects.version(v['id'])==v
    assert export_version(projects,v['id'])['files']
    with pytest.raises(ValueError):projects.preview(p['id'],request(p))
    with pytest.raises(ValueError):projects.followup(p['id'],v['id'],'alpha',{})
    assert len(chat.calls)==calls


def test_failed_start_does_not_publish_a_version_or_block_project(library):
    projects,jobs,chat,p=setup(library)
    req=request(p,budget=0)
    with pytest.raises(ValueError):projects.preview(p['id'],req)
    assert projects.get(p['id'])['head'] is None and not chat.calls
    with pytest.raises(ValueError):projects.config('a','b',['x','x'])


def test_project_export_values_and_source_versions_are_write_once(library):
    import csv,io
    from openpyxl import load_workbook
    projects,jobs,_,p=setup(library);p,v,_=run(projects,jobs,p)
    directory=Path(export_version(projects,v['id'])['directory'])
    rows=list(csv.reader(io.StringIO((directory/'comparison.csv').read_text('utf-8-sig'))))
    assert [r[2] for r in rows[1:]]==['alpha original','beta original']
    assert all('SHA256' in r[4] and r[8] for r in rows[1:])
    book=load_workbook(directory/'comparison.xlsx',read_only=True);assert book['comparison']['C2'].value=='alpha original';book.close()
    saved=json.loads((directory/'record.json').read_text('utf-8'))
    assert saved['version']['id']==v['id'] and len(saved['groups'])==2
    (directory/'summary.md').write_text('user edit',encoding='utf-8')
    with pytest.raises(ValueError,match='未覆盖'):export_version(projects,v['id'])


def test_unknown_usage_stops_continuation_and_interrupted_preparation_never_retries(library):
    projects,jobs,chat,p=setup(library);chat.unknown=True
    p,v,_=run(projects,jobs,p)
    assert v['status']=='partial' and not v['entries']
    calls=len(chat.calls)
    with pytest.raises(ValueError,match='用量未知'):projects.preview(p['id'],request(p,action='continue'))
    # Persist a preparation interrupted before a job exists, as after a process crash.
    op={'id':'b'*32,'plan':projects._plan(p,request(p)),'job_id':None,'status':'prepared'}
    raw=projects._read('folder_projects',p['id']);raw['pending']=op['id']
    with projects.folders.connect() as db:
        projects._write(db,'folder_project_operations',op);projects._write(db,'folder_projects',raw)
    restarted=Projects(jobs).get(p['id'])
    assert restarted['head']==v['id'] and not restarted['pending'] and len(chat.calls)==calls


def test_failed_job_submission_releases_preparation_without_publishing(library,monkeypatch):
    projects,jobs,chat,p=setup(library);req=request(p);pre=projects.preview(p['id'],req)
    monkeypatch.setattr(jobs,'start',lambda *a,**kw:(_ for _ in ()).throw(ValueError('busy')))
    with pytest.raises(ValueError,match='busy'):projects.start(p['id'],req,pre['plan_hash'])
    assert projects.get(p['id'])['head'] is None and not projects.get(p['id'])['pending'] and not chat.calls
