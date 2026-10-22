"""One short real request per level. Parameter acceptance, not a quality benchmark."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from agent.config import Settings,load_llm_config
from agent.strength import capability
from agent.llm import LLMClient
from knowledge.documents import sha


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()
    if root==PROJECT/'workspace' or not root.is_relative_to(PROJECT/'workspace'):raise ValueError('请使用workspace内新建的独立子目录')
    settings=Settings();base=load_llm_config(settings=settings,strength='default')
    if not capability(base.provider,base.model,base.base_url)['supported']:raise ValueError('当前模型接口尚未验证思考强度支持')
    root.mkdir(parents=True,exist_ok=False)
    runtime=['agent/config.py','agent/llm.py','agent/strength.py','scripts/model_strength_check.py']
    messages=[{'role':'user','content':'仅回答2+2的数值。'}]
    plan={'provider':base.provider,'model':base.model,'endpoint':base.base_url,'levels':['low','medium','high'],
          'messages':messages,'max_tokens_per_request':256,'timeout_seconds':30,'max_retries':0,'max_calls':3,
          'max_known_tokens':3000,'purpose':'参数接受与响应检查；不衡量强度导致的质量变化',
          'runtime_sha256':{p:sha((PROJECT/p).read_bytes()) for p in runtime}}
    (root/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),'utf-8')
    report={'status':'running','calls':[],'plan_sha256':sha((root/'plan.json').read_bytes()),'known_tokens':0,'cny_total':None}
    def save():
        temp=root/'result.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');temp.replace(root/'result.json')
    save()
    for level in plan['levels']:
        config=replace(load_llm_config(settings=settings,strength=level),timeout=30,max_retries=0)
        row={'strength':level,'reasoning_effort':config.reasoning_effort,'thinking':config.thinking,'status':'started','request_attempts':0}
        report['calls'].append(row);save()
        client=LLMClient(config)
        try:
            response=client.chat(messages,max_tokens=256)
            (root/(level+'-response.json')).write_text(response.model_dump_json(indent=2),'utf-8')
            usage=response.usage;prompt=getattr(usage,'prompt_tokens',None);output=getattr(usage,'completion_tokens',None)
            valid=lambda v:type(v) is int and v>=0
            row.update(status='returned',input_tokens=prompt if valid(prompt) else None,output_tokens=output if valid(output) else None,
                       returned_model=response.model,finish_reason=response.choices[0].finish_reason,
                       content=response.choices[0].message.content,
                       reasoning_content_returned=bool(getattr(response.choices[0].message,'reasoning_content',None)))
            if row['input_tokens'] is None or row['output_tokens'] is None:raise ValueError('用量未知，停止后续请求')
            report['known_tokens']+=prompt+output
            if report['known_tokens']>plan['max_known_tokens']:raise ValueError('实际用量超过预登记上限，停止后续请求')
        except Exception as exc:
            row.update(status='failed' if row['status']=='started' else row['status'],error_type=type(exc).__name__)
            report['status']='stopped';raise
        finally:
            row['request_attempts']=client.request_attempts;save()
    report['status']='completed';save()
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
