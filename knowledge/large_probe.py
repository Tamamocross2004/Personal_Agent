"""大语料固定协议对照；仅隔离基准索引，复用生产检索，不放宽生产导入限制。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import math
from pathlib import Path
import random
import statistics
import threading
import time

from agent.checkpoint import runtime_identity
from agent.config import Settings
from agent.context import estimate_tokens
from agent.memory import search_terms
from .documents import sha
from .embedding import Embedder
from .store import KnowledgeStore, now


SOURCES = [
    ('EcomRetrieval', '687de13dc7294d6fd9be10c6945f9e8fec8166b9', 'data/corpus-00000-of-00001-7f0e87850b5c9454.parquet'),
    ('EcomRetrieval', '687de13dc7294d6fd9be10c6945f9e8fec8166b9', 'data/queries-00000-of-00001-46d6d2b2f0ad8826.parquet'),
    ('EcomRetrieval-qrels', '39c90699b034ec22ac45b3abf5b0bbb5ffd421f9', 'data/dev-00000-of-00001-42566aaf5580f662.parquet'),
]


def write(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def read_data(root):
    import pyarrow.parquet as pq
    return [pq.read_table(root / path.split('/')[-1]).to_pylist() for _, _, path in SOURCES]


def select_queries(queries, qrels, seed, count):
    labels = {}
    for row in qrels:
        if row['score'] > 0:
            labels.setdefault(row['qid'], {})[row['pid']] = row['score']
    eligible = sorted((q for q in queries if q['id'] in labels), key=lambda q: q['id'])
    selected = random.Random(seed).sample(eligible, count)
    rng = random.Random(seed + 1)
    result = []
    for q in selected:
        order = [False, True]
        rng.shuffle(order)
        result.append({**q, 'relevant': labels[q['id']], 'order': order})
    return result


def metrics(ids, relevant, k=4):
    if len(ids) != len(set(ids)):
        raise ValueError('重复结果ID')
    top = ids[:k]
    matches = [rank for rank, pid in enumerate(top, 1) if pid in relevant]
    dcg = sum((2 ** relevant.get(pid, 0) - 1) / math.log2(rank + 1) for rank, pid in enumerate(top, 1))
    ideal = sum((2 ** grade - 1) / math.log2(rank + 1)
                for rank, grade in enumerate(sorted(relevant.values(), reverse=True)[:k], 1))
    return {'precision_at_4': len(matches) / k, 'recall_at_4': len(matches) / len(relevant),
            'hit_at_4': float(bool(matches)), 'mrr_at_4': 1 / matches[0] if matches else 0,
            'ndcg_at_4': dcg / ideal, 'recall_at_20': len(set(ids[:20]) & relevant.keys()) / len(relevant)}


def summarize(rows):
    result = {}
    for arm in ('baseline', 'rerank'):
        values = [row['arms'][arm] for row in rows]
        if not values:
            result[arm] = None
            continue
        times = sorted(v['wall_seconds'] for v in values)
        result[arm] = {key: statistics.mean(v['metrics'][key] for v in values) for key in values[0]['metrics']}
        result[arm].update(mean_seconds=statistics.mean(times), median_seconds=statistics.median(times),
                           p95_seconds=times[math.ceil(.95 * len(times)) - 1])
    if rows:
        deltas = [row['arms']['rerank']['metrics']['ndcg_at_4'] - row['arms']['baseline']['metrics']['ndcg_at_4'] for row in rows]
        rng = random.Random(20261021)
        bootstrap = sorted(statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(2000))
        result['paired_ndcg_delta'] = {'mean': statistics.mean(deltas), 'bootstrap_95_ci': [bootstrap[49], bootstrap[1949]],
                                      'improved': sum(x > 0 for x in deltas), 'worse': sum(x < 0 for x in deltas),
                                      'equal': sum(x == 0 for x in deltas)}
    return result


def prepare(root, downloads):
    import httpx
    root.mkdir(parents=True, exist_ok=False)
    data_root = root / 'dataset'
    data_root.mkdir()
    sources = []
    for repo, rev, name in SOURCES:
        url = f'https://huggingface.co/datasets/C-MTEB/{repo}/resolve/{rev}/{name}'
        cached = downloads / name.split('/')[-1] if downloads else None
        if cached and cached.is_file():
            content = cached.read_bytes()
        else:
            response = httpx.get(url, follow_redirects=True, timeout=120)
            response.raise_for_status()
            content = response.content
        target = data_root / name.split('/')[-1]
        target.write_bytes(content)
        sources.append({'url': url, 'file': target.name, 'sha256': sha(content), 'bytes': len(content)})
    corpus, queries, qrels = read_data(data_root)
    cases = select_queries(queries, qrels, 20261021, 100)
    ids = {row['id'] for row in corpus}
    if len(ids) != len(corpus) or any(not c['relevant'].keys() <= ids for c in cases):
        raise ValueError('数据集ID或标注不一致')
    if any(not row['text'].strip() or len(row['text']) > 1000 for row in corpus + queries):
        raise ValueError('本协议要求完整短文本，不静默截断或删除')
    settings = Settings()
    if settings.knowledge_rerank_candidates != 20:
        raise ValueError('本协议固定20候选')
    plan = {'dataset': 'C-MTEB/EcomRetrieval', 'sources': sources, 'seed': 20261021, 'cases': cases,
            'corpus_rows': len(corpus), 'characters': sum(len(c['text']) for c in corpus),
            'estimated_corpus_tokens': sum(estimate_tokens(c['text']) for c in corpus),
            'source_sha256': runtime_identity(), 'embedding': {'model': settings.embedding_model, 'dimensions': settings.embedding_dimensions,
                'base_url': settings.embedding_base_url},
            'rerank': {'model': settings.rerank_model, 'base_url': settings.rerank_base_url},
            'protocol': {'mode': 'hybrid', 'lexical_limit': 40, 'vector_limit': 40, 'rrf_constant': 60,
                'candidate_pool': 20, 'k': 4, 'embedding_batch': 32, 'embedding_workers': 4,
                'embedding_token_cap': 8000000, 'rerank_token_cap': 500000,
                'query_embedding': 'prewarmed for both arms', 'rerank_cache': 'cold per query',
                'labels': 'original positive qrels; unjudged treated as nonrelevant; no relabeling',
                'scope': 'presegmented benchmark passages; no chat generation or production import capacity claim'}}
    write(root / 'plan.json', plan)
    print(json.dumps({key: plan[key] for key in ('corpus_rows', 'characters', 'estimated_corpus_tokens')}, ensure_ascii=False), flush=True)


class CachedVectors:
    """按64条读取已持久化的向量，避免把十万条Python浮点列表同时放入内存。"""
    def __init__(self, store, hashes):
        self.store, self.hashes = store, hashes

    def __len__(self):
        return len(self.hashes)

    def __getitem__(self, index):
        keys = self.hashes[index] if isinstance(index, slice) else [self.hashes[index]]
        with self.store.connect() as db:
            values = [json.loads(db.execute('SELECT data FROM vectors WHERE signature=? AND text_hash=?',
                      (self.store.signature, h)).fetchone()[0]) for h in keys]
        return values if isinstance(index, slice) else values[0]


def embed_corpus(store, corpus, protocol, root):
    unique = {sha(row['text']): row['text'] for row in corpus}
    with store.connect() as db:
        cached = {r[0] for r in db.execute('SELECT text_hash FROM vectors WHERE signature=?', (store.signature,))}
    missing = [(h, text) for h, text in unique.items() if h not in cached]
    batch_size = protocol['embedding_batch']
    batches = [missing[i:i + batch_size] for i in range(0, len(missing), batch_size)]
    lock = threading.Lock()
    state = {'spent': sum(e.get('input_tokens') or 0 for e in store.events() if e.get('operation') != 'rerank'), 'reserved': 0}
    local = threading.local()

    def run(batch):
        reserve = max(512, 2 * sum(estimate_tokens(text) for _, text in batch))
        with lock:
            if state['spent'] + state['reserved'] + reserve > protocol['embedding_token_cap']:
                raise ValueError('全实验嵌入预算不足，未发送')
            state['reserved'] += reserve
        if not hasattr(local, 'embedder'):
            local.embedder = Embedder(store.settings, store.record_event)
        try:
            vectors, events = local.embedder.embed([text for _, text in batch], 'large-benchmark:corpus', reserve)
            with lock:
                state['spent'] += sum(e['input_tokens'] for e in events)
                if state['spent'] > protocol['embedding_token_cap']:
                    raise ValueError('实际用量超过预估预算，停止后续请求')
            with store.connect() as db:
                db.executemany('INSERT OR REPLACE INTO vectors VALUES (?,?,?)',
                               [(store.signature, h, json.dumps(v)) for (h, _), v in zip(batch, vectors)])
            return len(batch)
        finally:
            with lock:
                state['reserved'] -= reserve

    completed = len(unique) - len(missing)
    with ThreadPoolExecutor(max_workers=protocol['embedding_workers']) as pool:
        iterator = iter(batches)
        pending = set()
        for _ in range(protocol['embedding_workers']):
            batch = next(iterator, None)
            if batch:
                pending.add(pool.submit(run, batch))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            # 先检查所有已完成请求；失败时不追加新请求，已有在途请求仍保留日志。
            completed += sum(f.result() for f in done)
            write(root / 'progress.json', {'phase': 'embedding', 'unique_completed': completed,
                  'unique_total': len(unique), 'known_input_tokens': state['spent'], 'updated': now()})
            for _ in done:
                batch = next(iterator, None)
                if batch:
                    pending.add(pool.submit(run, batch))


def build_benchmark(store, corpus, plan, root):
    """基准原文已分段，ID即标注单位；不借此提供生产上传绕过入口。"""
    embed_corpus(store, corpus, plan['protocol'], root)
    generation = 'ecom-' + sha(json.dumps(plan['sources'], sort_keys=True))[:16]
    data = {'files': [], 'chunks': len(corpus), 'embedding': store.embedder.identity,
            'signature': store.signature, 'benchmark_only': True, 'dataset': plan['dataset'],
            'sources': plan['sources'], 'unit': 'one original passage per chunk; no splitting or adjacent context'}
    with store.connect() as db:
        db.execute('INSERT INTO generations VALUES (?,?,?,?)', (generation, 'building', now(), json.dumps(data)))
        for idx, row in enumerate(corpus):
            text = row['text']
            chunk = {'doc_id': row['id'], 'name': row['id'], 'text': text, 'page': 1, 'ordinal': 0,
                     'start': 0, 'end': len(text), 'text_sha256': sha(text), 'file_sha256': sha(text), 'page_sha256': sha(text)}
            db.execute('INSERT INTO chunks VALUES (?,?,?)', (generation, idx, json.dumps(chunk, ensure_ascii=False)))
            db.execute('INSERT INTO chunks_fts VALUES (?,?,?)', (generation, idx, ' '.join(search_terms(text, 4000))))
    write(root / 'progress.json', {'phase': 'vector_index', 'passages': len(corpus), 'updated': now()})
    store.vectors.build(store.root, generation, CachedVectors(store, [sha(row['text']) for row in corpus]), store.settings.embedding_dimensions)
    store._save_generation(generation, 'ready', data, activate=True)
    return generation


def live(root):
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    if runtime_identity() != plan['source_sha256']:
        raise ValueError('协议冻结后源码已变；请另建实验，不覆盖原协议')
    for source in plan['sources']:
        if sha((root / 'dataset' / source['file']).read_bytes()) != source['sha256']:
            raise ValueError('数据源哈希变化')
    if (root / 'results.json').exists():
        raise ValueError('已有实验结果，请使用新目录；不覆盖失败或完成的结果')
    settings = Settings(knowledge_path=root / 'knowledge', knowledge_enabled=True)
    if {'model': settings.embedding_model, 'dimensions': settings.embedding_dimensions, 'base_url': settings.embedding_base_url} != plan['embedding']:
        raise ValueError('嵌入配置变化')
    if {'model': settings.rerank_model, 'base_url': settings.rerank_base_url} != plan['rerank'] or settings.knowledge_rerank_candidates != 20:
        raise ValueError('重排配置变化')
    store = KnowledgeStore(settings)
    corpus, _, _ = read_data(root / 'dataset')
    results = {'cases': [], 'error_type': None, 'started': now(), 'plan_sha256': sha((root / 'plan.json').read_bytes())}
    write(root / 'results.json', results)
    try:
        results['generation'] = build_benchmark(store, corpus, plan, root)
        rerank_spent = 0
        for case in plan['cases']:
            # 真实查询嵌入预热单独计量；两臂均不承担首次查询缓存成本。
            remaining = plan['protocol']['embedding_token_cap'] - sum(e.get('input_tokens') or 0 for e in store.events() if e.get('operation') != 'rerank')
            store._cached_vectors([case['text']], 'large-benchmark:query-warmup', remaining)
            row = {'id': case['id'], 'query': case['text'], 'relevant': case['relevant'], 'arms': {}}
            for enabled in case['order']:
                arm = 'rerank' if enabled else 'baseline'
                budget = min(settings.knowledge_search_token_budget, plan['protocol']['rerank_token_cap'] - rerank_spent)
                started = time.perf_counter()
                result = store.search(case['text'], mode='hybrid', k=4, rerank=enabled, budget=budget)
                elapsed = time.perf_counter() - started
                if enabled:
                    if result['reranking']['cache_hit']:
                        raise ValueError('首次重排意外命中缓存')
                    rerank_spent += sum(e['input_tokens'] for e in result['usage'] if e.get('operation') == 'rerank')
                    if rerank_spent > plan['protocol']['rerank_token_cap']:
                        raise ValueError('实际重排用量超过预算，停止后续请求')
                direct = [h['name'] for h in result['hits'] if h['role'] == 'direct']
                candidates = [corpus[c['chunk_id']]['id'] for c in result['candidates']]
                measured = metrics(direct, case['relevant'])
                measured['recall_at_20'] = len(set(candidates) & case['relevant'].keys()) / len(case['relevant'])
                row['arms'][arm] = {'wall_seconds': elapsed, 'metrics': measured, 'direct_ids': direct,
                                    'candidate_ids': candidates, 'result': result}
                write(root / 'current-case.json', row)
            if row['arms']['baseline']['candidate_ids'] != row['arms']['rerank']['candidate_ids']:
                raise ValueError('两臂候选不一致')
            results['cases'].append(row)
            write(root / 'results.json', results)
            write(root / 'progress.json', {'phase': 'paired_queries', 'completed': len(results['cases']), 'total': len(plan['cases']), 'updated': now()})
    except Exception as exc:
        results['error_type'] = type(exc).__name__
        raise
    finally:
        results['finished'] = now()
        results['source_unchanged'] = runtime_identity() == plan['source_sha256']
        results['summary'] = summarize(results['cases'])
        results['status'] = store.status()
        results['complete'] = len(results['cases']) == len(plan['cases']) and results['error_type'] is None
        write(root / 'results.json', results)
        write(root / 'artifacts.json', {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in root.rglob('*')
              if p.is_file() and p.suffix in ('.json', '.jsonl', '.parquet')})
        print(json.dumps({'complete': results['complete'], 'cases': len(results['cases']), 'summary': results['summary'], 'error_type': results['error_type']}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--downloads', type=Path, help='可复用按固定版本下载的原始parquet，仍记录哈希')
    args = parser.parse_args()
    root = args.output.resolve()
    prepare(root, args.downloads) if args.prepare else live(root)


if __name__ == '__main__':
    main()
