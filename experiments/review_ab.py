"""固定报告的审稿A/B实验。freeze不联网；run显式调用项目Key，不改生产审稿。"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time
import types

from agent.config import PROJECT_ROOT, Settings, load_llm_config
from agent.context import estimate_messages, estimate_tokens
from agent.evidence import Claim, EvidenceLedger, write_json
from agent.loop import _cached_tokens, _cache_usage_reported
from agent.review import build_review_messages
from tools.session import ResearchSession

CASES = Path(__file__).with_name('review_ab_cases.json')
REVISIONS = {'baseline':'92c612c', 'challenger':'e5d2575'}
POLICY = {'repeats':2, 'seed':20261020, 'max_tokens':2048, 'equivalent_budget':300000,
          'temperature':0.3, 'max_retries':0, 'network_failure':'stop; no automatic resampling',
          'scope':'same user payload; historical reviewer system/schema/parsing only'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def source(revision):
    return subprocess.check_output(['git','show',revision+':agent/review.py'],cwd=PROJECT_ROOT).decode('utf-8')


def reviewers():
    result={}
    for arm, revision in REVISIONS.items():
        name='experiments._review_ab_'+arm
        module=types.ModuleType(name)
        module.__file__=f'<git:{revision}:agent/review.py>'
        sys.modules[name]=module
        exec(compile(source(revision),module.__file__,'exec'),module.__dict__)
        result[arm]=module
    return result


def identity():
    files=[Path(__file__),CASES,PROJECT_ROOT/'main.py',PROJECT_ROOT/'tests/test_review_ab.py']
    for directory in ('agent','tools'):
        files+=sorted((PROJECT_ROOT/directory).glob('*.py'))
    return {'files':{p.relative_to(PROJECT_ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in files},
            'reviewers':{arm:hashlib.sha256(source(revision).encode()).hexdigest()
                         for arm,revision in REVISIONS.items()}, 'policy':POLICY}


def report(quote):
    return ('# 给定材料核对\n\n## 摘要\n材料核对结果如下。\n\n## 方法与范围\n仅依据给定虚构材料，未联网或现场核验。'
            '\n\n## 正文\n'+quote+'\n\n## 结论\n结论限于给定材料。'
            '\n\n## 局限性\n未做外部核验。\n\n## 参考资料\n用户提供的虚构材料。')


def prepare(out):
    suite=read(CASES)
    if suite.get('version')!=1 or len(suite['groups'])!=4:
        raise ValueError('unexpected suite')
    modules=reviewers()
    definitions=[]
    for group in suite['groups']:
        for kind in ('correct','incorrect','ambiguous'):
            case_id=group['id']+'-'+kind
            task=out/'fixtures'/case_id
            task.mkdir(parents=True)
            question='仅依据以下材料核对报告，不联网，不补造事实。\n'+group['material']
            text=report(group[kind])
            ledger=EvidenceLedger(task,question)
            claim=Claim(quote=group[kind],kind='inference',
                        assumptions='只在给定材料范围内讨论；不假定额外规则或事实。',
                        supports=[{'source_id':'user-1','excerpt':group['material']}])
            mapping=ledger.prepare(task/'report.md',text,[claim],complete=True)
            (task/'report.md').write_text(text,encoding='utf-8')
            write_json(task/'report.evidence.json',mapping)
            session=ResearchSession(evidence=ledger)
            messages,partial=build_review_messages(text,task,session,question=question)
            if partial:
                raise ValueError('shared input incomplete')
            payload=json.loads(messages[1]['content'])
            # 两版共有旧字段；新增的空reasoning/premises不是本实验自变量。
            for item in payload['claim_evidence']['items']:
                item['claim'].pop('reasoning',None)
                item['claim'].pop('premises',None)
            payload['evidence_packet_estimated_tokens']=estimate_tokens(json.dumps(payload['claim_evidence'],ensure_ascii=False))
            shared=json.dumps(payload,ensure_ascii=False)
            requests={}
            for arm,module in modules.items():
                arm_messages,_=module.build_review_messages(text,task,session,question=question)
                requests[arm]=[arm_messages[0],{'role':'user','content':shared}]
            assert requests['baseline'][1]==requests['challenger'][1]
            definitions.append({'id':case_id,'kind':kind,'group':group['id'],
                'rubric':group['rubric'][kind],'report':text,'focus':group[kind],
                'payload_sha256':digest(payload),'packet':payload['claim_evidence'],
                'requests':requests})
    return definitions


def schedule(definitions):
    pairs=[(repeat,d['id']) for repeat in range(1,POLICY['repeats']+1) for d in definitions]
    random.Random(POLICY['seed']).shuffle(pairs)
    trials=[]
    for index,(repeat,case_id) in enumerate(pairs):
        arms=('baseline','challenger') if index%2==0 else ('challenger','baseline')
        for arm in arms:
            trials.append({'id':f'{len(trials)+1:03d}', 'case_id':case_id,'repeat':repeat,'arm':arm})
    return trials


def freeze(out):
    out.mkdir(parents=True,exist_ok=False)
    definitions=prepare(out)
    plan={'version':1,'identity':identity(),'definitions':definitions,'trials':schedule(definitions),
          'revisions':REVISIONS,'suite_sha256':digest(read(CASES)),
          'git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT_ROOT,text=True).strip(),
          'limits':'diagnostic authored cases; not blind or statistically independent; labels/rubrics not sent'}
    plan['lock_sha256']=digest(plan)
    write_json(out/'lock.json',plan)
    return plan


def verify(plan):
    if digest({k:v for k,v in plan.items() if k!='lock_sha256'})!=plan.get('lock_sha256'):
        raise ValueError('frozen input changed')
    if identity()!=plan['identity']:
        raise ValueError('source or policy changed')
    for d in plan['definitions']:
        if d['requests']['baseline'][1]!=d['requests']['challenger'][1]:
            raise ValueError('paired evidence mismatch')
        if digest(json.loads(d['requests']['baseline'][1]['content']))!=d['payload_sha256']:
            raise ValueError('payload mismatch')


def run(lock,out,client=None):
    plan=read(lock)
    verify(plan)
    out.mkdir(parents=True,exist_ok=False)
    cfg=replace(load_llm_config(provider='deepseek',model='deepseek-flash',settings=Settings()),
                temperature=POLICY['temperature'],max_retries=POLICY['max_retries'])
    if client is None:
        from agent.llm import LLMClient
        client=LLMClient(cfg)
    modules=reviewers()
    definitions={d['id']:d for d in plan['definitions']}
    state={'state':'running','lock_sha256':plan['lock_sha256'], 'trials':[],
           'config':{'provider':cfg.provider,'model':cfg.model,'temperature':cfg.temperature,'thinking':cfg.thinking,
                     'max_tokens':POLICY['max_tokens'],'max_retries':cfg.max_retries,
                     'key_source':'project .env; never serialized'}, 'equivalent_tokens':0}
    write_json(out/'results.json',state)
    try:
        for trial in plan['trials']:
            verify(plan)
            definition=definitions[trial['case_id']]
            messages=definition['requests'][trial['arm']]
            item={**trial,'status':'started','payload_sha256':definition['payload_sha256']}
            if state['equivalent_tokens']+estimate_messages(messages)+2*POLICY['max_tokens']>=POLICY['equivalent_budget']:
                item['status']='skipped_budget'
                state['trials'].append(item)
                continue
            state['trials'].append(item)
            write_json(out/'results.json',state)
            attempts=getattr(client,'request_attempts',0)
            started=time.perf_counter()
            try:
                response=client.chat(messages,tools=None,response_format={'type':'json_object'},max_tokens=POLICY['max_tokens'])
                item['seconds']=round(time.perf_counter()-started,3)
                item['request_attempts']=getattr(client,'request_attempts',attempts+1)-attempts
                response_path=out/(trial['id']+'-response.json')
                write_json(response_path,response.model_dump(mode='json'))
                item['raw_response_sha256']=hashlib.sha256(response_path.read_bytes()).hexdigest()
                item['response_model']=response.model
                usage=response.usage
                if usage is None:
                    item['status']='usage_unknown'
                    state['state']='stopped_usage_unknown'
                    break
                p,c=usage.prompt_tokens,usage.completion_tokens
                hit=min(_cached_tokens(usage),p)
                item['usage']={'prompt_tokens':p,'completion_tokens':c,'cached_tokens':hit,
                               'cache_reported':_cache_usage_reported(usage),
                               'equivalent_tokens':round(p-hit+.1*hit+2*c)}
                state['equivalent_tokens']+=item['usage']['equivalent_tokens']
                if not response.choices:
                    item.update(status='invalid',error_type='MissingChoice')
                    continue
                choice=response.choices[0]
                item['finish_reason']=choice.finish_reason
                raw=choice.message.content or ''
                if choice.finish_reason!='stop':
                    item['status']='truncated'
                    continue
                try:
                    if trial['arm']=='baseline':
                        issues=modules['baseline'].parse_verdict(raw,definition['report'])
                        support={'status':'not_available_in_baseline'}
                    else:
                        issues,support=modules['challenger'].parse_review(raw,definition['report'],definition['packet'])
                    item.update(status='incomplete' if support['status']=='incomplete' else 'complete',
                                issues=issues,support_review=support)
                except ValueError as exc:
                    item.update(status='invalid',error_type=type(exc).__name__)
            except KeyboardInterrupt:
                item['status']='interrupted_unknown_outcome'
                state['state']='interrupted'
                break
            except Exception as exc:
                item.update(status='failed_unknown_outcome',error_type=type(exc).__name__,
                            request_attempts=getattr(client,'request_attempts',attempts+1)-attempts)
                state['state']='stopped_call_failure'
                break
            finally:
                write_json(out/'results.json',state)
        else:
            state['state']='complete'
        verify(plan)
    finally:
        existing={t['id'] for t in state['trials']}
        state['trials'] += [{**t,'status':'not_run'} for t in plan['trials'] if t['id'] not in existing]
        state['request_attempts']=getattr(client,'request_attempts',None)
        write_json(out/'results.json',state)
    return state


def verify_outputs(out,state):
    for trial in state['trials']:
        if trial.get('raw_response_sha256'):
            path=Path(out)/(trial['id']+'-response.json')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=trial['raw_response_sha256']:
                raise ValueError('raw response changed')


def summarize(plan,state,decisions,result_root=None):
    verify(plan)
    scheduled={t['id']:t for t in plan['trials']}
    expected={t['id']:t for t in state['trials']}
    if (state.get('lock_sha256')!=plan['lock_sha256'] or set(expected)!=set(scheduled)
            or len(state['trials'])!=len(scheduled)
            or any(any(expected[i].get(k)!=value for k,value in t.items()) for i,t in scheduled.items())):
        raise ValueError('results do not match frozen trial schedule')
    if result_root is not None:
        verify_outputs(result_root,state)
    if len(decisions)!=len(expected) or len({d['id'] for d in decisions})!=len(expected):
        raise ValueError('review every trial exactly once')
    defs={d['id']:d for d in plan['definitions']}
    allowed={'correct':{'clean','false_alarm','inconclusive','not_completed'},
             'incorrect':{'detects_error','misses_error','inconclusive','not_completed'},
             'ambiguous':{'preserves_ambiguity','forces_resolution','inconclusive','not_completed'}}
    stats={arm:{'trials':0,'format_complete':0,'outcomes':{},'prompt_tokens':0,'completion_tokens':0,
                'cached_tokens':0,'cache_unreported_calls':0,'equivalent_tokens':0,'seconds':0} for arm in REVISIONS}
    for d in decisions:
        trial=expected[d['id']]
        definition=defs[trial['case_id']]
        if d.get('reviewer_kind')!='coding_assistant' or not d.get('notes') or d.get('outcome') not in allowed[definition['kind']]:
            raise ValueError('missing reviewer or invalid outcome')
        if d.get('trial_sha256')!=digest(trial):
            raise ValueError('review not bound to exact trial result')
        s=stats[trial['arm']]
        s['trials']+=1
        s['format_complete']+=trial['status']=='complete'
        s['outcomes'][d['outcome']]=s['outcomes'].get(d['outcome'],0)+1
        for key in ('prompt_tokens','completion_tokens','cached_tokens','equivalent_tokens'):
            s[key]+=trial.get('usage',{}).get(key,0)
        s['cache_unreported_calls']+=bool(trial.get('usage')) and not trial['usage']['cache_reported']
        s['seconds']+=trial.get('seconds',0)
    for s in stats.values():
        s['seconds']=round(s['seconds'],3)
    return {'reviewer_kind':'coding_assistant','blind_review':False,'metrics':stats,
            'raw_outputs_verified':result_root is not None,
            'limitations':'small authored cases, two repeats; reviewer prompt/schema/parser only; no significance or end-to-end acceptance claim'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze'); f.add_argument('--out',type=Path,required=True)
    r=sub.add_parser('run'); r.add_argument('--lock',type=Path,required=True); r.add_argument('--out',type=Path,required=True)
    a=sub.add_parser('summarize'); a.add_argument('--lock',type=Path,required=True); a.add_argument('--results',type=Path,required=True)
    a.add_argument('--decisions',type=Path,required=True); a.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.command=='freeze':
        plan=freeze(args.out); print(json.dumps({'trials':len(plan['trials']),'lock':str(args.out/'lock.json')})); return 0
    if args.command=='run':
        state=run(args.lock,args.out); print(json.dumps({'state':state['state'],'trials':len(state['trials'])})); return 0 if state['state']=='complete' else 1
    value=summarize(read(args.lock),read(args.results),read(args.decisions),args.results.parent)
    if args.out.exists(): raise ValueError('summary exists; choose new output')
    write_json(args.out,value); print(json.dumps(value,ensure_ascii=False)); return 0


if __name__=='__main__':
    raise SystemExit(main())
