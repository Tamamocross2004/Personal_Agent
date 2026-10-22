import json
import threading
from types import SimpleNamespace as NS

import pytest
from folder_assistant.reading import ReadingPlan,read_batch,neighbors
from folder_assistant.exports import export_job
from folder_assistant.jobs import FolderJobs
from test_folder_index import library,sync
from test_folder_answers import finished


class BatchChat:
    def __init__(self):self.calls=[];self.hook=None;self.fail_at=None;self.invalid=False;self.unknown=False
    def chat(self,messages,**kwargs):
        self.calls.append((messages,kwargs))
        if self.hook:self.hook()
        if len(self.calls)==self.fail_at:raise RuntimeError('private')
        data=json.loads(messages[1]['content']);e=data['evidence'][0];cid=e['citation_id']
        body={'schema_version':3,'findings':[{'statement':'测试替身仅验证分批协议','kind':'direct','citations':[cid],
                'support':[{'citation_id':cid,'quote':e['text']}]}],'unknowns':[],'comparison':[]}
        if self.invalid:body['findings'][0]['support'][0]['quote']='fabricated quote'
        return NS(model='fake',usage=NS(prompt_tokens=100,completion_tokens=None if self.unknown else 40),
                  choices=[NS(message=NS(content=json.dumps(body)),finish_reason='stop')])


def setup_reading(library,rows=15):
    docs,folders,fid,index,_,_=library
    (docs/'a.txt').unlink()
    (docs/'table.csv').write_text('device,cycle,unit\n'+''.join(f'Unit-{i:03},{i+2},day\n' for i in range(rows)),encoding='utf-8')
    generation=sync(library)
    chat=BatchChat();index.settings=index.settings.model_copy(update={'deepseek_api_key':'fake'})
    jobs=FolderJobs(index,llm_factory=lambda c:chat)
    return generation,jobs,chat


def start(library,jobs,**kwargs):
    return jobs.start(library[2],'answer',{'query':'整理所选范围','budget':20000,'mode':'keyword','file_ids':None,
                                        'strategy':'exhaustive','max_batches':1,'batch_mode':'fixed',**kwargs})


def test_batches_continue_pinned_scope_no_omission_or_repeated_main_segments(library,tmp_path):
    generation,jobs,chat=setup_reading(library)
    first=finished(jobs,start(library,jobs))
    assert first['coverage']['returned_until']==6 and first['coverage']['remaining_segments']==10
    assert first['result']['content_status']=='partial'
    (library[0]/'table.csv').write_text('replacement\n',encoding='utf-8');sync(library)
    second=finished(jobs,start(library,jobs,resume_from=first['id'],max_batches=8,query='改题不会混入'))
    assert second['inputs']['generation']==generation and second['inputs']['query']=='整理所选范围'
    assert second['coverage']['input_coverage_complete'] and second['coverage']['structured_coverage_complete']
    sent=[h['segment'] for job in (first,second) for h in job['evidence'] if h['role']=='sequential']
    assert sent==list(range(16)) and len(chat.calls)==3
    assert all('reading_scope' in json.loads(messages[1]['content']) for messages,_ in chat.calls)
    with pytest.raises(ValueError,match='没有剩余'):start(library,jobs,resume_from=second['id'])
    library[1].revoke(library[2])
    count=len(chat.calls)
    out=export_job(jobs,second['id'])
    record=json.loads((__import__('pathlib').Path(out['directory'])/'record.json').read_text('utf-8'))
    assert record['coverage']['input_coverage_complete']
    assert len(record['batches'])==2 and len(chat.calls)==count


def test_budget_refusal_does_not_claim_sent_or_trigger_model(library):
    _,jobs,chat=setup_reading(library)
    result=finished(jobs,start(library,jobs,budget=256))
    assert not chat.calls and result['evidence']==[] and result['batches']==[]
    assert result['coverage']['returned_until']==0 and result['coverage']['stop_reason']=='token_budget'


@pytest.mark.parametrize('flag',['invalid','unknown','fail_at'])
def test_failed_or_invalid_batch_stops_without_automatic_retry(library,flag):
    _,jobs,chat=setup_reading(library)
    setattr(chat,flag,1 if flag=='fail_at' else True)
    result=finished(jobs,start(library,jobs,max_batches=8))
    assert len(chat.calls)==1
    assert not result['coverage']['structured_coverage_complete']
    if flag=='invalid':
        assert result['result']['content_status']=='partial'
        assert result['batches'][0]['model_output']
        assert result['coverage']['returned_until']==6
        assert result['coverage']['next_offset']==0 and result['coverage']['remaining_segments']==16
    else:assert result['status']=='failed'
    assert result['batches'][0]['status'] in ('failed','returned')
    if flag=='fail_at':
        assert result['evidence']==[] and result['coverage']['returned_until']==0
        assert result['batches'][0]['evidence']  # intended input retained separately


def test_stop_after_inflight_response_saves_batch_and_never_next_call(library):
    _,jobs,chat=setup_reading(library)
    entered=threading.Event();release=threading.Event()
    chat.hook=lambda:(entered.set(),release.wait(10))
    job=start(library,jobs,max_batches=8);assert entered.wait(10)
    jobs.stop(job['id']);release.set();result=finished(jobs,job)
    assert result['status']=='stopped' and len(chat.calls)==1
    assert result['batches'][0]['model_output'] and result['coverage']['returned_until']==6


def test_explicit_continuation_reprocesses_invalid_batch_without_skipping_or_double_counting(library):
    _,jobs,chat=setup_reading(library)
    chat.invalid=True;first=finished(jobs,start(library,jobs,max_batches=8))
    assert len(chat.calls)==1 and first['coverage']['structured_segments']==0
    chat.invalid=False
    second=finished(jobs,start(library,jobs,resume_from=first['id'],max_batches=8))
    assert second['batches'][0]['start_offset']==0
    assert second['coverage']['structured_segments']==16
    assert second['coverage']['input_coverage_complete'] and second['coverage']['remaining_segments']==0
    assert len(chat.calls)==4  # Exactly one invalid attempt plus three explicit new batches.


def test_reading_context_locators_long_rows_and_history_neighbors(library):
    docs,folders,fid,index,embedding,_=library
    (docs/'long.csv').write_text('name,note\nA,"'+('长😀'*900)+'"\nB,last\n',encoding='utf-8')
    (docs/'notes.md').write_text('# 范围说明\n'+''.join(f'第{i}段条件\n' for i in range(9)),encoding='utf-8')
    generation=sync(library);plan=ReadingPlan(index,fid,generation)
    longdoc=next(d for d in plan.documents if d['name']=='long.csv')
    rows=[s for s in plan.segments if s['name']=='long.csv' and s['source_location']['record']==2]
    assert len(rows)>1
    original=longdoc['pages'][1]['text']
    assert ''.join(s['text'] for s in rows)==original
    assert all(original[s['start']:s['end']]==s['text'] for s in rows)
    note=next(s for s in plan.segments if s['name']=='notes.md' and s['source_location']['line']==7)
    assert any(h['role']=='explicit_markdown_heading' for h in plan.batch(note['segment'],1)['hits'])
    before=len(embedding.calls)
    result=read_batch(index,fid,generation,[longdoc['file_id']],offset=3,limit=1)
    assert any(h['role']=='table_origin_unverified_header' for h in result['hits'])
    assert all(h['name']=='long.csv' for h in result['hits'])
    folders.revoke(fid)
    view=neighbors(index,fid,generation,longdoc['id'],longdoc['pages'][1]['block']['id'])
    assert len(view['blocks'])==3 and not view['cloud_calls'] and len(embedding.calls)==before
    with pytest.raises(ValueError):read_batch(index,fid,generation)


def test_stop_during_embedding_does_not_send_subsequent_batches(library):
    docs,folders,fid,index,embedding,_=library
    (docs/'many.txt').write_text(''.join(f'不同原文{i}\n' for i in range(40)),encoding='utf-8')
    checks={'stop':False}
    def check():
        if checks['stop']:raise ValueError('用户停止')
    embedding.hook=lambda:checks.update(stop=True)
    with pytest.raises(ValueError,match='停止'):index.sync(fid,folders.scan(fid)['id'],check=check)
    assert len(embedding.calls)==1 and index.store(fid).generation() is None


def test_pdf_table_like_text_keeps_page_boundaries_without_inventing_cross_page_header(library):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter()
    for lines in (['device pressure','Unit-A 12kPa'],['Unit-B 18kPa']):
        page=writer.add_blank_page(width=400,height=400)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 20 350 Td '+' 0 -20 Td '.join(f'({line}) Tj' for line in lines)+' ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(library[0]/'two-pages.pdf');generation=sync(library)
    index=library[3];plan=ReadingPlan(index,library[2],generation)
    last=next(s for s in plan.segments if 'Unit-B' in s['text'])
    assert last['source_location']['page']==2
    batch=plan.batch(last['segment'],1)
    assert all(h['role']=='sequential' for h in batch['hits'])
    previous=neighbors(index,library[2],generation,last['doc_id'],last['source_block_id'])
    assert {b['location']['page'] for b in previous['blocks']}=={1,2}
    assert any('Unit-A 12kPa' in b['text'] for b in previous['blocks'])
