"""Deterministic provenance selection; no semantic ranking, LLM calls or answer edits."""
import json
from agent.context import estimate_tokens
from knowledge.documents import sha

VERSION=1


def wire(value):return json.dumps(value,ensure_ascii=False)


def source_identity(e):
    keys=('file_id','file_sha256','source_block_id')
    if any(e.get(k) is None for k in keys):return None
    return [*(e[k] for k in keys),sha(e['text'])]


def select(context,evidence):
    """Candidate payloads stay local. Only exact delivered source/text matches travel."""
    if context.get('selection_version')!=VERSION:return context,None
    identities=[source_identity(e) for e in evidence]
    selected=[];excluded=0
    for candidate in context['candidates']:
        if candidate['sources'] and all(s['identity'] is not None and s['identity'] in identities for s in candidate['sources']):
            selected.append(candidate)
        else:excluded+=1
    public={k:context[k] for k in ('objective','fields','revision_instruction','previous_version','meaning')}
    public.update(previous_result_snippets=[r['text'] for r in selected],
                  omitted_groups=context['omitted_groups']+excluded,prior_dialogue=context['prior_dialogue'],
                  omitted_recent_messages=context['omitted_recent_messages'])
    audit={'version':VERSION,'action':context['action'],'result_background_enabled':context['include_results'],
           'recent_dialogue_enabled':context['include_dialogue'],
           'selected_groups':[{k:c[k] for k in ('job_id','batch','sources')} for c in selected],
           'excluded_groups_not_delivered':excluded,'omitted_groups_before_selection':context['omitted_groups'],
           'selected_dialogue_jobs':[m['job_id'] for m in public['prior_dialogue']],
           'omitted_recent_messages':context['omitted_recent_messages'],
           'history_characters':sum(len(s) for s in public['previous_result_snippets'])+sum(len(wire(m)) for m in public['prior_dialogue']),
           'meaning':'按来源文件、SHA、提取块及送达文字哈希精确匹配，非语义相关性判断。最近对话仅显式选择后发送；背景不是新证据。'}
    return public,audit


def transmission(inputs,evidence,messages):
    """Describe actual serialized components. Per-part estimates are not additive billing."""
    body=json.loads(messages[1]['content'])
    context=inputs.get('project_context')
    _,selection=select(context,evidence) if context is not None else (None,None)
    sections=[{'name':'system','characters':len(messages[0]['content']),'estimated_tokens':estimate_tokens(messages[0]['content'])}]
    for key,value in body.items():
        text=value if isinstance(value,str) else wire(value)
        sections.append({'name':key,'characters':len(text),'estimated_tokens':estimate_tokens(text)})
    return {'version':1,'estimated_input_tokens':estimate_tokens(wire(messages)),'sections':sections,
            'evidence':[{'citation_id':e['citation_id'],'name':e['name'],'file_sha256':e['file_sha256'],
                         'source_block_id':e.get('source_block_id'),'text_sha256':sha(e['text']),'characters':len(e['text'])} for e in evidence],
            'project_history':selection,
            'meaning':'实际请求构成的本地估算；各部分Token不能相加当账单，供应商只返回本次总用量。清单本身不发送给模型，未认证内容正确性。'}
