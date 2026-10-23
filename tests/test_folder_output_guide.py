"""Independent schema evaluation and real job boundaries with deterministic providers."""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from folder_assistant.contracts import ContractError,request_schema,validate
from knowledge.documents import sha
from test_folder_contracts import body,finding,EVIDENCE
from test_folder_index import library,sync
from test_folder_answers import Chat,service,ask,finished


def row(**change):
    return {'subject':'Sensor-P','subject_type':'entity','subject_quote':{'citation_id':'C1','quote':'Sensor-P'},
            'field':'reading','value':'0','value_quote':{'citation_id':'C1','quote':'0'},
            'status':'stated','citations':['C1'],**change}


@pytest.mark.parametrize('status',['stated','blank','unknown'])
@pytest.mark.parametrize('subject_type',['entity','source','unknown'])
def test_schema_and_existing_validator_accept_valid_condition_combinations(status,subject_type):
    r=row(status=status,subject_type=subject_type)
    if status!='stated':r.update(value=None,value_quote=None)
    if subject_type!='entity':r.update(subject='readings.txt' if subject_type=='source' else None,subject_quote=None)
    value=body(comparison=[r]);schema=request_schema()
    Draft202012Validator.check_schema(schema);Draft202012Validator(schema).validate(value)
    assert validate(json.dumps(value),EVIDENCE)[0]['comparison'][0]==r


@pytest.mark.parametrize('change',[
    {'status':'blank'},{'status':'unknown'}, {'value_quote':None},
    {'subject_type':'unknown'}, {'subject_type':'source'}, {'subject':None},
    {'value':'   '},{'citations':[]},
])
def test_conditional_hints_reject_existing_cross_field_failures_without_altering_acceptance(change):
    value=body(comparison=[row(**change)])
    assert list(Draft202012Validator(request_schema()).iter_errors(value))
    with pytest.raises(ContractError):validate(json.dumps(value),EVIDENCE)


def test_finding_conditions_keep_valid_duplicates_defaults_and_whitespace_assumptions():
    relation={'field':'reading','common_scope':'same declared sample','comparability':'established','reason':'declared basis only'}
    support=[{'citation_id':'C1','quote':'reading 0'},{'citation_id':'C2','quote':'reading 3'}]
    values=[finding(assumptions=['  ']),finding('unknown',citations=[],support=[]),
            finding('inference',assumptions=['a'],limitations=['b']),
            finding('difference',citations=['C1','C2'],support=support+support[:1],relation=relation),
            finding('conflict',citations=['C1','C2'],support=support,relation=relation)]
    for item in values:
        value=body(findings=[item]);Draft202012Validator(request_schema()).validate(value)
        validate(json.dumps(value),EVIDENCE)
    invalid=[finding('inference'),finding('inference',assumptions=[' '],limitations=['b']),
             finding(assumptions=['a']),finding('direct',relation=relation),finding('difference'),
             finding('conflict',citations=['C1','C2'],support=support,relation={**relation,'common_scope':None})]
    for item in invalid:
        value=body(findings=[item]);assert list(Draft202012Validator(request_schema()).iter_errors(value))
        with pytest.raises(ContractError):validate(json.dumps(value),EVIDENCE)


def test_schema_hints_cannot_replace_actual_evidence_or_distinct_quote_checks():
    value=body(comparison=[row(value='99',value_quote={'citation_id':'C1','quote':'99'})])
    Draft202012Validator(request_schema()).validate(value)
    with pytest.raises(ContractError):validate(json.dumps(value),EVIDENCE)
    # Every call builds an independent schema, not shared mutable prompt state.
    changed=request_schema();changed['$defs']['Comparison']['allOf'].clear()
    assert request_schema()['$defs']['Comparison']['allOf']


class OutputChat(Chat):
    def __init__(self,mode):super().__init__();self.mode=mode
    def chat(self,messages,**kwargs):
        result=super().chat(messages,**kwargs)
        if self.mode in ('invalid_structure','output_limit'):result.choices[0].message.content='{"schema_version":3,'
        if self.mode=='output_limit':result.choices[0].finish_reason='length'
        if self.mode=='unfinished_response':result.choices[0].finish_reason='content_filter'
        if self.mode=='contract_violation':
            raw=json.loads(result.choices[0].message.content);raw['comparison'][0].update(status='blank')
            result.choices[0].message.content=json.dumps(raw)
        return result


@pytest.mark.parametrize('mode',['invalid_structure','output_limit','contract_violation','unfinished_response','structure_checked'])
def test_job_preserves_raw_one_call_diagnostic_and_no_automatic_retry(library,mode):
    sync(library);chat=OutputChat(mode);jobs,_=service(library,chat)
    value=finished(jobs,ask(library,jobs))
    assert len(chat.calls)==1 and jobs.used_tokens(value['id'])==120
    assert value['output_diagnostics']['code']==mode
    assert value['output_diagnostics']['semantic_status']=='not_certified'
    assert value['request']['system_sha256']==sha(chat.calls[0][0][0]['content'])
    from folder_assistant.contracts import GUIDE_VERSION
    assert value['request']['output_guide_version']==GUIDE_VERSION
    if mode in ('invalid_structure','output_limit','contract_violation'):
        assert value['result']['answer'] is None and value['output_diagnostics']['issues']
    if mode=='output_limit':
        assert value['model_output']=='{"schema_version":3,'
        assert value['output_diagnostics']['issues'][0]['code']=='invalid_schema'
    before=copy.deepcopy(value);assert jobs.get(value['id'])==before and len(chat.calls)==1


def test_unknown_usage_does_not_run_validation_or_hide_response(library):
    sync(library);chat=OutputChat('output_limit');chat.unknown=True;jobs,_=service(library,chat)
    value=finished(jobs,ask(library,jobs))
    assert value['status']=='failed' and len(chat.calls)==1 and value['model_output']
    assert value['output_diagnostics']['usage_status']=='unknown'
    assert value['output_diagnostics']['validation']=='not_run' and 'result' not in value


def test_complete_json_with_length_still_stops_cursor_and_export_preserves_diagnosis(library):
    from pathlib import Path
    from folder_assistant.exports import export_job
    from test_folder_reading import setup_reading,start
    _,jobs,chat=setup_reading(library)
    original=chat.chat
    def limited(*args,**kwargs):
        value=original(*args,**kwargs);value.choices[0].finish_reason='length';return value
    chat.chat=limited
    value=finished(jobs,start(library,jobs,max_batches=8))
    assert len(chat.calls)==1 and value['coverage']['next_offset']==0
    assert value['batches'][0]['result']['answer']  # Valid structure is still unfinished.
    assert value['batches'][0]['output_diagnostics']['code']=='output_limit'
    assert value['batches'][0]['output_diagnostics']['validation']=='checked'
    out=Path(export_job(jobs,value['id'])['directory']);before=(out/'record.json').read_bytes()
    assert '输出达到上限' in (out/'summary.md').read_text('utf-8')
    assert json.loads(before)['batches'][0]['output_diagnostics']['code']=='output_limit'
    assert export_job(jobs,value['id'])['cached'] and (out/'record.json').read_bytes()==before
