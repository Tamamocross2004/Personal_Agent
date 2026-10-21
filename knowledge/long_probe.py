"""固定真实长文档的检索与研究诊断；不把定位覆盖认证为语义正确。"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import random
import shutil
import time

from agent.checkpoint import runtime_identity
from agent.config import Settings, load_llm_config
from .documents import parse, sha
from .store import KnowledgeStore
from .large_probe import write


def coverage(hits, facts):
    result = {}
    for key, fact in facts.items():
        cursor = fact['start']
        for start, end in sorted((h['start'], h['end']) for h in hits
                                 if h['name'] == fact['name'] and h['page'] == fact['page']):
            if start <= cursor:
                cursor = max(cursor, end)
        result[key] = cursor >= fact['end']
    return result


def configured(root):
    return Settings(knowledge_path=root / 'knowledge', knowledge_enabled=True, memory_enabled=False,
                    knowledge_build_token_budget=600000, knowledge_rerank_enabled=True,
                    knowledge_chunk_size=700, knowledge_chunk_overlap=120)


def check_sources(root, suite):
    for source in suite['sources']:
        file = root / 'documents' / source['name']
        if sha(file.read_bytes()) != source['sha256']:
            raise ValueError('文档快照变化')
    pages = {s['name']: parse((root / 'documents' / s['name']).read_bytes(), s['name']) for s in suite['sources']}
    for fact in suite['facts'].values():
        text = pages[fact['name']][fact['page'] - 1]['text'][fact['start']:fact['end']]
        if sha(text) != fact['text_sha256']:
            raise ValueError('事实区间变化')


def prepare(root, documents):
    root.mkdir(parents=True, exist_ok=False)
    (root / 'documents').mkdir()
    suite = json.loads(Path(__file__).with_name('long_cases.json').read_text(encoding='utf-8'))
    for source in suite['sources']:
        shutil.copyfile(documents / source['name'], root / 'documents' / source['name'])
    check_sources(root, suite)
    settings = configured(root)
    plan = {'suite': suite, 'source_sha256': runtime_identity(), 'configuration': {
        'embedding_model': settings.embedding_model, 'dimensions': settings.embedding_dimensions,
        'rerank_model': settings.rerank_model, 'candidate_pool': settings.knowledge_rerank_candidates},
        'purpose': 'development diagnostic; no stage12 acceptance'}
    if settings.knowledge_rerank_candidates != 20:
        raise ValueError('固定20候选')
    write(root / 'plan.json', plan)


def retrieve(root):
    if (root / 'retrieval-results.json').exists():
        raise ValueError('不覆盖已有结果')
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    if runtime_identity() != plan['source_sha256']:
        raise ValueError('源码变化；拒绝混合版本')
    suite = plan['suite']; check_sources(root, suite)
    settings = configured(root)
    identity = {'embedding_model': settings.embedding_model, 'dimensions': settings.embedding_dimensions,
                'rerank_model': settings.rerank_model, 'candidate_pool': settings.knowledge_rerank_candidates}
    if identity != plan['configuration']: raise ValueError('检索配置变化')
    store = KnowledgeStore(settings); result = {'cases': [], 'error_type': None}
    write(root / 'retrieval-results.json', result)
    rng = random.Random(suite['protocol']['seed'])
    try:
        result['build'] = store.build([(s['name'], (root / 'documents' / s['name']).read_bytes()) for s in suite['sources']])
        spent = 0
        for case in suite['cases']:
            _, warmup, _ = store._cached_vectors([case['query']], 'long:query-warmup', suite['protocol']['retrieval_token_cap'] - spent)
            spent += sum(e['input_tokens'] for e in warmup)
            row = {'id': case['id'], 'query': case['query'], 'facts': case['facts'], 'arms': {}}
            order = [False, True]; rng.shuffle(order); row['order'] = order
            for enabled in order:
                started = time.perf_counter()
                found = store.search(case['query'], rerank=enabled, k=4,
                                     budget=suite['protocol']['retrieval_token_cap'] - spent)
                spent += sum(e['input_tokens'] for e in found['usage'])
                if spent > suite['protocol']['retrieval_token_cap']: raise ValueError('实际检索用量超过预算')
                wanted = {key: suite['facts'][key] for key in case['facts']}
                covered = coverage(found['hits'], wanted)
                row['arms']['rerank' if enabled else 'baseline'] = {
                    'seconds': time.perf_counter() - started, 'coverage': covered,
                    'all_facts_delivered': all(covered.values()) if covered else None, 'result': found}
            a = row['arms']['baseline']['result']; b = row['arms']['rerank']['result']
            if a['generation'] != b['generation'] or [c['chunk_id'] for c in a['candidates']] != [c['chunk_id'] for c in b['candidates']]:
                raise ValueError('配对候选不一致')
            result['cases'].append(row); write(root / 'retrieval-results.json', result)
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        raise
    finally:
        result['source_unchanged'] = runtime_identity() == plan['source_sha256']
        result['complete'] = len(result['cases']) == len(suite['cases']) and result['error_type'] is None
        result['status'] = store.status()
        result['summary'] = {}
        for arm in ('baseline', 'rerank'):
            known = [r['arms'][arm] for r in result['cases'] if r['facts']]
            result['summary'][arm] = {'answerable_queries': len(known),
                'all_facts_delivered': sum(r['all_facts_delivered'] for r in known),
                'facts_delivered': sum(sum(r['coverage'].values()) for r in known),
                'facts_total': sum(len(r['coverage']) for r in known)}
        write(root / 'retrieval-results.json', result)
        print(json.dumps({'complete': result['complete'], 'summary': result['summary'], 'error_type': result['error_type']}, ensure_ascii=False))


def freeze_research(root):
    if (root / 'research-plan.json').exists(): raise ValueError('不覆盖研究协议')
    old = json.loads((root / 'plan.json').read_text(encoding='utf-8')); check_sources(root, old['suite'])
    settings = configured(root); current = KnowledgeStore(settings).generation()
    if not current or current['status'] != 'ready': raise ValueError('知识库尚未就绪')
    plan = {'source_sha256': runtime_identity(), 'suite': old['suite'], 'generation': current['id'],
        'model': load_llm_config(settings=settings).model, 'max_steps': 15, 'token_budget': 80000,
        'rerank': True, 'candidate_pool': 20, 'reviewer_kind': 'coding_assistant', 'stage12_accepted': False}
    write(root / 'research-plan.json', plan)


def research(root):
    from agent.llm import LLMClient
    from main import research as run
    plan = json.loads((root / 'research-plan.json').read_text(encoding='utf-8'))
    if runtime_identity() != plan['source_sha256']: raise ValueError('研究源码变化')
    if (root / 'research-results.json').exists(): raise ValueError('不覆盖研究结果')
    settings = configured(root)
    if settings.knowledge_rerank_candidates != 20 or load_llm_config(settings=settings).model != plan['model']:
        raise ValueError('研究配置变化')
    current = KnowledgeStore(settings).generation()
    if current['id'] != plan['generation']: raise ValueError('研究代际变化')
    results = {'tasks': [], 'error_type': None}
    write(root / 'research-results.json', results)
    try:
        for case in plan['suite']['research']:
            task = root / case['id']
            result = asdict(run(LLMClient(load_llm_config(settings=settings)), settings, case['question'],
                plan['max_steps'], plan['token_budget'], task_dir=task, historical_context=''))
            result.pop('messages', None)
            results['tasks'].append({'id': case['id'], 'question': case['question'], 'result': result})
            write(root / 'research-results.json', results)
    except Exception as exc:
        results['error_type'] = type(exc).__name__
        raise
    finally:
        results['source_unchanged'] = runtime_identity() == plan['source_sha256']
        results['complete'] = len(results['tasks']) == len(plan['suite']['research']) and results['error_type'] is None
        results['stage12_accepted'] = False
        results['status'] = KnowledgeStore(settings).status()
        write(root / 'research-results.json', results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'retrieve', 'freeze-research', 'research'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--documents', type=Path)
    args = parser.parse_args(); root = args.output.resolve()
    if args.action == 'prepare':
        if args.documents is None: parser.error('prepare需要--documents原始快照目录')
        prepare(root, args.documents)
    elif args.action == 'retrieve': retrieve(root)
    elif args.action == 'freeze-research': freeze_research(root)
    else: research(root)


if __name__ == '__main__': main()
