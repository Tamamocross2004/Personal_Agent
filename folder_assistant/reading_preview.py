"""Local preflight shared by new full reading and explicit continuation."""
from .batching import select_batch
from .field_plan import prepare_request


def preview_first(plan,inputs,offset,agent_cap):
    batch=None;decision={'stop_reason':'empty_scope','effective_output_tokens':0}
    planning={'status':'not_requested','uses_call':False}
    first_request=None;services=[]
    pending=inputs.get('plan_fields') and 'field_specs' not in inputs
    if inputs.get('plan_fields'):
        planning={'status':'pending' if pending else 'inherited','uses_call':bool(pending)}
    if offset>=inputs['range_end']:
        return batch,decision,{'planning':planning,'first_request':None,'services':[],'first_batch_provisional':False}
    if pending:
        request=prepare_request(inputs['query'],inputs['output_tokens'],agent_cap,inputs['budget'])
        planning.update({k:request[k] for k in ('estimated_input_tokens','max_output_tokens')})
        if request['max_output_tokens']<128:
            planning['status']='budget_insufficient'
            decision.update(stop_reason='token_budget')
            return batch,decision,{'planning':planning,'first_request':None,'services':[],'first_batch_provisional':False}
        first_request='field_plan';services=['llm']
    planning['data_call_slots']=max(0,inputs['batch_call_limit']-int(bool(pending)))
    if not planning['data_call_slots']:
        decision.update(stop_reason='call_limit')
    else:
        # Pending intent/output cost is unknown. This is a provisional data range,
        # never a promise to send it or an estimate of the provider's actual bill.
        batch,decision=select_batch(plan,inputs,offset,1,inputs['budget'],[],agent_cap)
        if decision['effective_output_tokens']<128:
            batch=None;decision={**decision,'stop_reason':'token_budget'}
        if batch and not pending:first_request='evidence';services=['llm']
    return batch,decision,{'planning':planning,'first_request':first_request,'services':services,
                           'first_batch_provisional':bool(pending and batch)}
