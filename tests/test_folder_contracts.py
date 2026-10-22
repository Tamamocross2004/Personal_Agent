"""Quote identity and record association checks; these do not certify semantic entailment."""
import copy
import json

import pytest

from folder_assistant.contracts import ContractError,validate
from folder_assistant.exports import export_job
from folder_assistant.jobs import FolderJobs
from test_folder_index import library,sync
from test_folder_answers import Chat,service,ask,finished


def finding(kind='direct',**values):
    return {'statement':'A reading is recorded.','kind':kind,'citations':['C1'],
            'support':[{'citation_id':'C1','quote':'Sensor-P: reading 0'}],**values}


def body(**values):
    return {'schema_version':2,'findings':[finding()],'unknowns':[],'comparison':[],**values}


EVIDENCE=[{'citation_id':'C1','name':'readings.txt','text':'Sensor-P: reading 0. Reviewed status not recorded.'},
          {'citation_id':'C2','name':'other.txt','text':'Sensor-P: reading 3. Reviewed status not recorded.'}]


def check(value,evidence=EVIDENCE):return validate(json.dumps(value,ensure_ascii=False),evidence)


def test_quote_must_be_delivered_and_listed_for_its_row():
    for change,code in [({'quote':'Sensor-P: reading 1'},'quote_not_delivered'),({'citation_id':'C2','quote':'reading 3'},'unlisted_quote')]:
        value=body();value['findings'][0]['support'][0].update(change)
        with pytest.raises(ContractError) as exc:check(value)
        assert exc.value.issues[0]['code']==code
    # Repeated words expose all positions; there is no invented unique match offset.
    _,report=check(body(findings=[finding(support=[{'citation_id':'C1','quote':'x'}])]),[{'citation_id':'C1','name':'repeat.txt','text':'x x'}])
    assert report['quotes'][0]['matches']==[{'start':0,'end':1},{'start':2,'end':3}]


def test_inference_requires_visible_assumptions_and_limits_but_does_not_claim_truth():
    with pytest.raises(ContractError,match='推断须'):check(body(findings=[finding('inference')]))
    value=body(findings=[finding('inference',assumptions=['Assume the device was sampled correctly.'],limitations=['Actual sampling procedure is not recorded.'])])
    answer,report=check(value)
    assert answer['findings'][0]['limitations'] and report['status']=='structure_checked'
    # Exact quotes alone do not prove this statement. Keep the distinction explicit in the API.
    unsupported=body(findings=[finding(statement='Review is certainly complete.')])
    answer,report=check(unsupported)
    assert answer['findings'][0]['statement']=='Review is certainly complete.'
    assert '不认证语义蕴含' in report['meaning']
    with pytest.raises(ContractError,match='含额外假设'):check(body(findings=[finding(assumptions=['Assume review was complete.'])]))


def test_difference_and_real_incompatibility_remain_distinct():
    quotes=[{'citation_id':'C1','quote':'reading 0'},{'citation_id':'C2','quote':'reading 3'}]
    relation={'field':'reading','common_scope':None,'comparability':'not_established','reason':'Sampling time is unspecified.'}
    value=body(findings=[finding('difference',citations=['C1','C2'],support=quotes,relation=relation)])
    assert check(value)[0]['findings'][0]['kind']=='difference'
    value['findings'][0]['kind']='conflict'
    with pytest.raises(ContractError,match='可比条件'):check(value)
    value['findings'][0]['relation'].update(common_scope='Same sensor, same recorded instant and unit',comparability='established')
    assert check(value)[0]['findings'][0]['kind']=='conflict'  # The declared scope still needs semantic review.


def test_two_sides_may_be_separate_quotes_in_one_actual_fragment():
    value=body(findings=[finding('conflict',support=[{'citation_id':'C1','quote':'enabled'},{'citation_id':'C1','quote':'disabled'}],
        relation={'field':'state','common_scope':'same object and time','comparability':'established','reason':'Mutually exclusive states under the declared scope.'})])
    assert check(value,[{'citation_id':'C1','name':'states.txt','text':'same time: enabled; same time: disabled'}])[0]['findings']
    value['findings'][0]['support'][1]['quote']='enabled'
    with pytest.raises(ContractError,match='双方原句'):check(value,[{'citation_id':'C1','text':'enabled'}])


def test_object_identity_is_not_automatically_replaced_by_source_name():
    row={'subject':'Sensor-P','subject_type':'entity','subject_quote':{'citation_id':'C1','quote':'Sensor-P'},
         'field':'reading','value':'0','value_quote':{'citation_id':'C1','quote':'0'},'status':'stated','citations':['C1']}
    assert check(body(comparison=[row]))[0]['comparison'][0]['subject']=='Sensor-P'
    invalid={**row,'subject':'readings.txt','subject_quote':{'citation_id':'C1','quote':'readings.txt'}}
    with pytest.raises(ContractError,match='实际送达'):check(body(comparison=[invalid]))
    source={**row,'subject':'readings.txt','subject_type':'source','subject_quote':None}
    _,report=check(body(comparison=[source,source]))
    assert report['status']=='needs_verification' and report['warnings'][0]['code']=='source_subject_collision'
    unknown={**row,'subject':None,'subject_type':'unknown','subject_quote':None}
    assert check(body(comparison=[unknown]))[0]['comparison'][0]['subject'] is None


def test_actual_parsed_rows_cannot_cross_bind_objects_and_values(library):
    docs,_,fid,index,_,_=library
    from openpyxl import Workbook
    workbook=Workbook();sheet=workbook.active;sheet.title='Readings';sheet.append(['probe','reading']);sheet.append(['Probe-7',0]);sheet.append(['Probe-8','=AVERAGE(4,8)']);workbook.save(docs/'readings.xlsx');workbook.close()
    sync(library);retrieved=index.search(fid,'probe',mode='keyword')
    evidence=[{'citation_id':'C'+str(i+1),**hit} for i,hit in enumerate(retrieved['hits'])]
    first=next(e for e in evidence if e['source_location'].get('row')==2)
    second=next(e for e in evidence if e['source_location'].get('row')==3)
    q=lambda e,text:{'citation_id':e['citation_id'],'quote':text}
    rows=[{'subject':'Probe-7','subject_type':'entity','subject_quote':q(first,'Probe-7'),'field':'reading','value':'0','value_quote':q(first,'0'),'status':'stated','citations':[first['citation_id']]},
          {'subject':'Probe-8','subject_type':'entity','subject_quote':q(second,'Probe-8'),'field':'reading','value':None,'value_quote':None,'status':'unknown','citations':[second['citation_id']]}]
    value=body(findings=[{'statement':'Values require verification.','kind':'unknown','citations':[],'support':[]}],comparison=rows)
    answer,_=check(value,evidence)
    assert [r['subject'] for r in answer['comparison']]==['Probe-7','Probe-8']
    assert answer['comparison'][1]['value'] is None
    value['comparison'][1].update(value='0',value_quote=q(first,'0'),status='stated',citations=[first['citation_id'],second['citation_id']])
    with pytest.raises(ContractError) as exc:check(value,evidence)
    assert exc.value.issues[0]['code']=='cross_record_binding'


def test_missing_values_not_converted_to_zero_and_units_not_invented():
    row={'subject':None,'subject_type':'unknown','subject_quote':None,'field':'reading','value':'0 kg',
         'value_quote':{'citation_id':'C1','quote':'0'},'status':'stated','citations':['C1']}
    with pytest.raises(ContractError,match='原文写法'):check(body(comparison=[row]))
    row.update(status='unknown',value='0',value_quote=None)
    with pytest.raises(ContractError,match='空白或未知'):check(body(comparison=[row]))


def test_legacy_response_is_not_accepted_for_a_new_request(library):
    class OldChat(Chat):
        def chat(self,messages,**kw):
            response=super().chat(messages,**kw)
            data=json.loads(response.choices[0].message.content);data.pop('schema_version')
            response.choices[0].message.content=json.dumps(data)
            return response
    sync(library);jobs,chat=service(library,OldChat());job=finished(jobs,ask(library,jobs))
    assert job['result']['content_status']=='needs_verification' and job['result']['answer'] is None
    assert job['result']['contract_checks']['issues'][0]['code']=='invalid_schema'
    assert job['model_output'] and len(chat.calls)==1


def test_stored_legacy_answers_are_never_revalidated_or_rewritten(library):
    _,folders,fid,index,embedding,_=library
    generation=sync(library);jobs=FolderJobs(index)
    job={'id':'f'*32,'folder_id':fid,'kind':'answer','status':'completed','inputs':{'query':'legacy','generation':generation,
         'source_snapshot':[]},'result':{'answer':{'findings':[{'statement':'saved legacy','kind':'direct','citations':[]}],
         'unknowns':[],'comparison':[]},'review_status':'not_reviewed'}}
    jobs.save(job);result=export_job(jobs,job['id']);output=folders.root/'outputs'/job['id'];before=(output/'summary.md').read_bytes();calls=len(embedding.calls)
    other=FolderJobs(index)
    assert other.get(job['id'])==job and export_job(other,job['id'])['cached']
    assert (output/'summary.md').read_bytes()==before and len(embedding.calls)==calls
    assert (output/'comparison.csv').read_text('utf-8-sig').count(',')==4  # Original five-column format.


def test_new_answer_export_preserves_object_quotes_and_inference_limits(library):
    import csv
    import io
    docs,folders,_,_,_,_=library
    (docs/'a.txt').write_bytes(b'Rotor-K reading=0. Review process not recorded.')
    class AnchoredChat(Chat):
        def chat(self,messages,**kw):
            response=super().chat(messages,**kw)
            evidence=json.loads(messages[1]['content'])['evidence'][0];cid=evidence['citation_id']
            q=lambda text:{'citation_id':cid,'quote':text}
            value=body(findings=[{'statement':'Conditional interpretation only.','kind':'inference','citations':[cid],
                'support':[q(evidence['text'])],'assumptions':['Assume the counter is accurate.'],'limitations':['No review process is recorded.']}],
                comparison=[{'subject':'Rotor-K','subject_type':'entity','subject_quote':q('Rotor-K'),'field':'reading','value':'0',
                             'value_quote':q('0'),'status':'stated','citations':[cid]}])
            response.choices[0].message.content=json.dumps(value)
            return response
    sync(library);jobs,chat=service(library,AnchoredChat());job=finished(jobs,ask(library,jobs,query='reading'))
    assert job['result']['content_status']=='unreviewed' and job['result']['review_status']=='not_reviewed'
    exported=export_job(jobs,job['id']);output=folders.root/'outputs'/job['id']
    rows=list(csv.reader(io.StringIO((output/'comparison.csv').read_text('utf-8-sig'))))
    assert rows[1][0]=='Rotor-K' and rows[1][5]=='entity' and json.loads(rows[1][6])['quote']=='Rotor-K'
    assert rows[1][2]=='0' and 'a.txt' in rows[1][4]
    summary=(output/'summary.md').read_text('utf-8')
    assert '假设（未证实）' in summary and 'No review process is recorded.' in summary
    assert json.loads((output/'record.json').read_text('utf-8'))['model_output']==job['model_output']
    assert len(chat.calls)==1
