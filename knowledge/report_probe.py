"""冻结新报告诊断后单次执行；保留失败，不自动重采样或计为阶段12通过。"""
import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sqlite3

from agent.checkpoint import runtime_identity
from agent.config import load_llm_config
from .documents import parse, sha
from .large_probe import write
from .long_probe import configured
from .store import KnowledgeStore


SUITE = Path(__file__).with_name('report_cases.json')


def settings_for(root):
    return configured(root).model_copy(update={'knowledge_lexical_candidates':40,
        'knowledge_vector_candidates':40,'knowledge_rerank_candidates':20})


def configuration(settings):
    model=load_llm_config(settings=settings)
    return {'model':{k:getattr(model,k) for k in ('provider','model','base_url','temperature','max_tokens','thinking','max_retries')},
            'embedding_model':settings.embedding_model,'embedding_dimensions':settings.embedding_dimensions,
            'embedding_base_url':settings.embedding_base_url,'embedding_timeout':settings.embedding_timeout,
            'rerank_model':settings.rerank_model,'rerank_enabled':settings.knowledge_rerank_enabled,
            'rerank_base_url':settings.rerank_base_url,'rerank_timeout':settings.rerank_timeout,
            'lexical':settings.knowledge_lexical_candidates,'vector':settings.knowledge_vector_candidates,
            'pool':settings.knowledge_rerank_candidates,'search_budget':settings.knowledge_search_token_budget,
            'weights':[settings.token_weight_cached,settings.token_weight_output],
            'generation_max_tokens':settings.agent_call_max_tokens,'review_enabled':settings.report_review_enabled,
            'review_max_calls':settings.report_review_max_calls,'review_max_tokens':settings.report_review_max_tokens,
            'memory_enabled':settings.memory_enabled}


def freeze(root,source,suite_path=None):
    if root.exists(): raise ValueError('目标目录已存在，禁止覆盖')
    suite_path=(suite_path or SUITE).resolve()
    suite=json.loads(suite_path.read_text(encoding='utf-8'))
    # SQLite在线只读备份，避免复制WAL造成不一致；不沿用旧模型日志/报告。
    root.mkdir(parents=True)
    target=root/'knowledge'; target.mkdir()
    with closing(sqlite3.connect((source/'knowledge'/'knowledge.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as src:
        with closing(sqlite3.connect(target/'knowledge.sqlite3')) as dst: src.backup(dst)
    shutil.copytree(source/'knowledge'/'vectors',target/'vectors')
    shutil.copytree(source/'knowledge'/'documents',target/'documents')
    shutil.copytree(source/'documents',root/'documents')
    settings=settings_for(root)
    sources={p.name:sha(p.read_bytes()) for p in (root/'documents').iterdir() if p.is_file()}
    for case in suite['cases']:
        for anchor in case.get('anchors',[]):
            pages=parse((root/'documents'/anchor['name']).read_bytes(),anchor['name'])
            if anchor['contains'] not in pages[anchor['page']-1]['text']: raise ValueError('冻结原文定位不匹配')
    plan={'suite':suite,'suite_file':suite_path.as_posix(),'suite_sha256':sha(suite_path.read_bytes()),'sources':sources,
          'source_sha256':runtime_identity(),'configuration':configuration(settings),
          'generation':KnowledgeStore(settings).generation()['id'],
          'max_steps':suite['max_steps'],'token_budget':suite['token_budget'],
          'no_web':True,'no_memory':True,'reviewer':'coding_assistant',
          'purpose':'development only; questions differ from previous five, no paired gain claim'}
    write(root/'plan.json',plan)
    return plan


def validate(root,plan):
    suite_path=Path(plan.get('suite_file',SUITE))
    if runtime_identity()!=plan['source_sha256'] or sha(suite_path.read_bytes())!=plan['suite_sha256']:
        raise ValueError('源码或题集变化；禁止混合版本')
    settings=settings_for(root)
    if configuration(settings)!=plan['configuration']: raise ValueError('配置变化')
    if KnowledgeStore(settings).generation()['id']!=plan['generation']: raise ValueError('代际变化')
    for name,digest in plan['sources'].items():
        if sha((root/'documents'/name).read_bytes())!=digest: raise ValueError('原文快照变化')
    if plan['suite']!=json.loads(suite_path.read_text(encoding='utf-8')): raise ValueError('冻结题集变化')
    if any(plan[k]!=plan['suite'][k] for k in ('max_steps','token_budget')):
        raise ValueError('固定步数或预算变化')
    return settings


def research(root):
    from agent.llm import LLMClient
    from main import research as run

    plan=json.loads((root/'plan.json').read_text(encoding='utf-8'))
    settings=validate(root,plan)
    output=root/'research-results.json'
    if output.exists() or any((root/c['id']).exists() for c in plan['suite']['cases']):
        raise ValueError('已有尝试记录；禁止覆盖或自动重试')
    results={'tasks':[],'error_type':None,'stage12_accepted':False}
    write(output,results)
    try:
        for case in plan['suite']['cases']:
            validate(root,plan)
            task=root/case['id']
            result=asdict(run(LLMClient(load_llm_config(settings=settings)),settings,case['question'],
                plan['max_steps'],plan['token_budget'],task_dir=task,historical_context=''))
            result.pop('messages',None)
            results['tasks'].append({'id':case['id'],'question':case['question'],'result':result})
            write(output,results)
            events=[json.loads(line) for line in (task/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
            if result['stop_reason']=='needs_verification' or any(e.get('kind')=='llm_error' or (
                    e.get('kind')=='llm_call' and not e.get('usage_reported',False)) for e in events):
                raise ValueError('存在请求或用量不确定状态，停止该批；保留已有记录')
    except Exception as exc:
        results['error_type']=type(exc).__name__
        raise
    finally:
        results['source_unchanged']=runtime_identity()==plan['source_sha256']
        results['complete']=len(results['tasks'])==len(plan['suite']['cases']) and results['error_type'] is None
        write(output,results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['freeze','research'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--suite',type=Path,help='仅freeze指定新题集；research始终使用已冻结题集')
    args=parser.parse_args(); root=args.output.resolve()
    if args.action=='freeze':
        if args.source is None: parser.error('freeze需要原官方长文档目录--source')
        freeze(root,args.source.resolve(),args.suite)
    else:
        if args.suite is not None: parser.error('research不能替换已冻结题集')
        research(root)


if __name__=='__main__': main()
