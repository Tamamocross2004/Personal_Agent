import copy,json
import pytest
from folder_assistant.verification import observations,evaluate_case,report,usage_summary,main


SOURCE={'name':'records.csv','file_sha256':'version-a','location':{'kind':'csv_record','record':2}}
E={'citation_id':'C1','name':'records.csv','file_sha256':'version-a','source_location':{'kind':'csv_record','record':2},'text':'Node-U reading 0'}
EXPECTED={'id':'one','subject':'Node-U','field':'reading','status':'stated','values':['0'],'source':SOURCE,'required_literals':['Node-U','0']}
ROW={'subject':'Node-U','field':'reading','status':'stated','value':'0','value_quote':{'citation_id':'C1','quote':'0'},'citations':['C1']}
def attempt(rows=None,**change):
    record={'finish_reason':'stop','evidence':[copy.deepcopy(E)],'result':{'answer':{'comparison':copy.deepcopy([ROW] if rows is None else rows),'findings':[]}}}
    record.update(change)
    return {'job_id':'job','batch':1,'job_status':'completed','confirmed_return':True,'record':record}
def check(items,rows=None):return evaluate_case({'id':'case','expected_rows':rows or [EXPECTED]},items)


def test_delivery_structure_and_fields_are_independent_and_inputs_unchanged():
    saved=attempt([]);before=copy.deepcopy(saved);value=check([saved])
    assert value['confirmed_delivered_literal_rows']==1 and value['responses'][0]['usable_structure']
    assert value['row_counts']['literal_and_source_matched']==0 and len(value['missing_or_different_rows'])==1
    assert saved==before and value['semantic_status']=='not_certified'


def test_duplicate_rows_do_not_cover_another_object():
    other={**EXPECTED,'id':'two','subject':'Node-V','values':['3']}
    value=check([attempt([ROW,ROW])],[EXPECTED,other])
    assert value['row_counts']['literal_and_source_matched']==1 and value['row_counts']['duplicate']==1
    assert value['missing_or_different_rows'][0]['id']=='two'


@pytest.mark.parametrize('key,value',[('file_sha256','version-b'),('name','elsewhere.csv'),('source_location',{'kind':'csv_record','record':3})])
def test_correct_value_from_wrong_location_is_not_matched(key,value):
    item=attempt();item['record']['evidence'][0][key]=value
    result=check([item]);assert result['row_counts']['source_mismatch']==1


def test_broad_citations_cannot_hide_wrong_value_quote_source():
    item=attempt();item['record']['evidence'].append({**E,'citation_id':'C2','source_location':{'kind':'csv_record','record':9}})
    row=item['record']['result']['answer']['comparison'][0];row['citations']=['C1','C2'];row['value_quote']['citation_id']='C2'
    assert check([item])['row_counts']['source_mismatch']==1


def test_blank_representation_options_are_explicit_and_zero_is_not_blank():
    expected={**EXPECTED,'status':'blank','values':[None,'']}
    for value in (None,''):
        row={**ROW,'status':'blank','value':value,'value_quote':None}
        assert check([attempt([row])],[expected])['row_counts']['literal_and_source_matched']==1
    assert check([attempt()],[expected])['row_counts']['value_or_status_mismatch']==1
    assert check([attempt([{**ROW,'value':'0.0'}])])['row_counts']['value_or_status_mismatch']==1


def test_truncation_is_delivered_but_not_salvaged_even_with_complete_structure():
    value=check([attempt(finish_reason='length')])
    assert value['confirmed_delivered_literal_rows']==1 and not value['responses'][0]['usable_structure']
    assert not value['actual_rows'] and value['missing_or_different_rows']
    item=attempt();item['confirmed_return']=False
    assert check([item])['confirmed_delivered_literal_rows']==0


def test_quote_namespaces_are_per_attempt_and_extra_fields_stay_visible():
    other={**EXPECTED,'id':'two','subject':'Node-V','values':['3'],'source':{**SOURCE,'file_sha256':'version-b'}}
    b=attempt([{**ROW,'subject':'Node-V','value':'3'},{**ROW,'field':'unasked'}]);b['job_id']='other'
    b['record']['evidence'][0]['file_sha256']='version-b'
    result=check([attempt(),b],[EXPECTED,other])
    assert result['row_counts']['literal_and_source_matched']==2 and result['row_counts']['unrequested_row']==1


def test_projection_preserves_failed_attempts_and_can_select_immutable_version_groups():
    piece=attempt()['record'];job={'id':'job','status':'failed','batches':[{'number':1,'status':'returned',**piece},{'number':2,'status':'failed'}],
        'usage_events':[{'id':'job-batch-1','operation':'llm','status':'returned'}]}
    before=copy.deepcopy(job);rows=observations(job)
    assert rows[0]['confirmed_return'] and not rows[1]['confirmed_return']
    assert len(observations(job,[1]))==1 and job==before


def test_no_batch_budget_refusal_is_preserved_as_an_unsent_attempt():
    job={'id':'budget','status':'completed','batches':[],'coverage':{'stop_reason':'token_budget'},'usage_events':[]}
    result=observations(job)
    assert len(result)==1 and not result[0]['confirmed_return']
    assert result[0]['record']['coverage']['stop_reason']=='token_budget'


def test_usage_deduplicates_and_unknown_is_not_zero_or_reasoning_double_count():
    e={'id':'a','operation':'llm','input_tokens':100,'output_tokens':40,'reasoning_tokens':20}
    value=usage_summary([e,e,{'id':'b','operation':'embedding','input_tokens':None}])
    assert value['known_tokens']==140 and value['unknown_usage_ids']==['b'] and value['calls_by_operation']['llm']==1
    with pytest.raises(ValueError):usage_summary([e,{**e,'input_tokens':101}])


def test_unregistered_cases_and_missing_cases_are_not_hidden():
    value=report({'cases':[{'id':'a','expected_rows':[EXPECTED]}]},{'cases':[{'id':'b','observations':[attempt()]}]})
    assert value['missing_case_ids']==['a'] and value['unregistered_case_ids']==['b']
    assert value['cases'][0]['missing_or_different_rows'] and not value['stage12_accepted']


def test_cli_exclusive_output_does_not_overwrite_inputs_or_reports(tmp_path,monkeypatch):
    plan=tmp_path/'plan.json';bundle=tmp_path/'bundle.json';out=tmp_path/'report.json'
    plan.write_text(json.dumps({'cases':[{'id':'a','expected_rows':[EXPECTED]}]}),'utf-8')
    bundle.write_text(json.dumps({'cases':[{'id':'a','observations':[attempt()]}]}),'utf-8')
    before=(plan.read_bytes(),bundle.read_bytes())
    monkeypatch.setattr('sys.argv',['verification','--plan',str(plan),'--bundle',str(bundle),'--output',str(out)]);main()
    saved=out.read_bytes()
    with pytest.raises(FileExistsError):main()
    assert out.read_bytes()==saved and (plan.read_bytes(),bundle.read_bytes())==before
