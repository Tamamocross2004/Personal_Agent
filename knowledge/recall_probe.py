"""新问题上的固定召回配置对照；显式隔离索引会话，不自动调整生产默认。"""
import argparse
import json
from pathlib import Path
import time

from agent.checkpoint import runtime_identity
from agent.config import Settings
from .documents import sha
from .large_probe import metrics, write
from .store import KnowledgeStore
from .vectors import client_for


class WarmVectors:
    """仅该隔离实验作用域持有client；退出外层with必然释放索引与OS锁。"""
    def __init__(self, client, root, generation):
        self.client, self.root, self.generation = client, Path(root).resolve(), generation

    def search(self, root, generation, vector, limit):
        if Path(root).resolve() != self.root or generation != self.generation:
            raise ValueError('实验索引会话不能跨目录或代际复用')
        rows = self.client.search(collection_name='chunks', data=[vector], limit=limit)[0]
        return [int(row['id']) for row in rows]


def prepare(root, knowledge):
    root.mkdir(parents=True, exist_ok=False)
    suite = json.loads(Path(__file__).with_name('recall_cases.json').read_text(encoding='utf-8'))
    settings = Settings(knowledge_path=knowledge.resolve())
    store = KnowledgeStore(settings); generation = store.generation()
    if not generation or generation['chunks'] != 100902 or generation.get('sources') != suite['sources']:
        raise ValueError('必须使用相同完整基准的隔离索引')
    if not generation.get('benchmark_only'): raise ValueError('不持有正式知识库的长期文件锁')
    if store.embedder.identity['model'] != suite['protocol']['embedding_model']['model']:
        raise ValueError('嵌入模型变化')
    if store.reranker.identity['model'] != suite['protocol']['rerank_model']['model']:
        raise ValueError('重排模型变化')
    write(root / 'plan.json', {'suite': suite, 'knowledge': str(store.root), 'generation': generation['id'],
        'source_sha256': runtime_identity(), 'embedding': store.embedder.identity, 'rerank': store.reranker.identity})


def live(root):
    if (root / 'results.json').exists(): raise ValueError('不覆盖既有完整或失败结果')
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8')); suite = plan['suite']
    if runtime_identity() != plan['source_sha256']: raise ValueError('源码变化')
    base = Settings(knowledge_path=Path(plan['knowledge']), knowledge_enabled=True)
    store = KnowledgeStore(base)
    if store.embedder.identity != plan['embedding'] or store.reranker.identity != plan['rerank']:
        raise ValueError('模型身份变化')
    before = {e['id'] for e in store.events()}
    result = {'cases': [], 'error_type': None, 'index_setup_seconds': None}
    write(root / 'results.json', result)
    spent = {'embedding': 0, 'rerank': 0}
    try:
        started = time.perf_counter()
        with client_for(store.root, plan['generation']) as client:
            client.load_collection('chunks')
            result['index_setup_seconds'] = time.perf_counter() - started
            vectors = WarmVectors(client, store.root, plan['generation'])
            stores = {p['name']: KnowledgeStore(base.model_copy(update={
                'knowledge_lexical_candidates': p['lexical'], 'knowledge_vector_candidates': p['vector'],
                'knowledge_rerank_candidates': p['pool']}), vectors=vectors) for p in suite['profiles']}
            for case in suite['cases']:
                _, usage, _ = store._cached_vectors([case['text']], 'recall:query-warmup',
                    suite['protocol']['embedding_budget'] - spent['embedding'])
                spent['embedding'] += sum(e['input_tokens'] for e in usage)
                row = {'id': case['id'], 'query': case['text'], 'relevant': case['relevant'], 'arms': {}}
                for name, enabled in case['order']:
                    key = name + ('/rerank' if enabled else '/rrf')
                    started = time.perf_counter()
                    found = stores[name].search(case['text'], generation=plan['generation'], k=4, rerank=enabled,
                        budget=min(base.knowledge_search_token_budget, suite['protocol']['rerank_budget'] - spent['rerank']))
                    elapsed = time.perf_counter() - started
                    spent['rerank'] += sum(e['input_tokens'] + (e.get('output_tokens') or 0) for e in found['usage'])
                    if spent['rerank'] > suite['protocol']['rerank_budget']:
                        raise ValueError('实际重排用量超过预算')
                    direct = [h['name'] for h in found['hits'] if h['role'] == 'direct']
                    measured = metrics(direct, case['relevant']); measured.pop('recall_at_20')
                    # 名称即基准pid，但候选只返回chunk索引，使用有界正文读取恢复同一ID。
                    chunks = stores[name]._chunks(plan['generation'], [c['chunk_id'] for c in found['candidates']])
                    candidate_ids = [chunks[c['chunk_id']]['name'] for c in found['candidates']]
                    measured['candidate_recall'] = len(set(candidate_ids) & case['relevant'].keys()) / len(case['relevant'])
                    row['arms'][key] = {'seconds': elapsed, 'metrics': measured, 'candidate_ids': candidate_ids,
                                        'direct_ids': direct, 'result': found}
                    write(root / 'current-case.json', row)
                for profile in suite['profiles']:
                    name = profile['name']
                    if row['arms'][name + '/rrf']['candidate_ids'] != row['arms'][name + '/rerank']['candidate_ids']:
                        raise ValueError('同配置两臂候选不一致')
                result['cases'].append(row); write(root / 'results.json', result)
                write(root / 'progress.json', {'completed': len(result['cases']), 'total': len(suite['cases'])})
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        raise
    finally:
        result['source_unchanged'] = runtime_identity() == plan['source_sha256']
        result['complete'] = len(result['cases']) == len(suite['cases']) and result['error_type'] is None
        result['new_events'] = [e for e in store.events() if e['id'] not in before]
        result['spent_known_tokens'] = spent
        result['summary'] = {}
        for profile in suite['profiles']:
            for arm in ('rrf', 'rerank'):
                key = profile['name'] + '/' + arm
                values = [row['arms'][key] for row in result['cases']]
                result['summary'][key] = {m: sum(v['metrics'][m] for v in values) / len(values)
                    for m in values[0]['metrics']} if values else None
                if values:
                    result['summary'][key]['mean_seconds'] = sum(v['seconds'] for v in values) / len(values)
                    result['summary'][key]['cases'] = len(values)
        write(root / 'results.json', result)
        print(json.dumps({'complete': result['complete'], 'summary': result['summary'], 'error_type': result['error_type']}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'live'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--knowledge', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.knowledge is None: parser.error('prepare需要--knowledge隔离基准库')
        prepare(args.output.resolve(), args.knowledge)
    else: live(args.output.resolve())


if __name__ == '__main__': main()
