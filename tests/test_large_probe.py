"""实验的标注、配对计量和索引适配契约；不用假向量证明检索质量。"""
import math
import json
import os

import pytest

from knowledge import large_probe
from knowledge.documents import sha
from knowledge.embedding import Embedder, EmbeddingFailure
from knowledge.vectors import _install_manifest_compat, MilvusVectors
from test_knowledge import kb


def test_metrics_use_rank_and_original_graded_labels():
    m = large_probe.metrics(['x', 'a', 'b', 'z'], {'a': 2, 'b': 1})
    assert m['precision_at_4'] == .5
    assert m['recall_at_4'] == 1
    assert m['mrr_at_4'] == .5
    assert m['ndcg_at_4'] == pytest.approx((3 / math.log2(3) + 1 / 2) / (3 + 1 / math.log2(3)))
    assert large_probe.metrics(['x'], {'a': 1})['hit_at_4'] == 0
    with pytest.raises(ValueError):
        large_probe.metrics(['a', 'a'], {'a': 1})


def test_selection_fixed_without_retrieval_scores_and_keeps_qrels():
    queries = [{'id': str(i), 'text': f'question{i}'} for i in range(20)]
    qrels = [{'qid': str(i), 'pid': str(i + 100), 'score': 1} for i in range(20)]
    before = list(qrels)
    a = large_probe.select_queries(queries, qrels, 12, 10)
    assert a == large_probe.select_queries(list(reversed(queries)), qrels, 12, 10)
    assert len({c['id'] for c in a}) == 10 and qrels == before
    assert all(c['relevant'] == {str(int(c['id']) + 100): 1} for c in a)
    assert {tuple(c['order']) for c in a} == {(True, False), (False, True)}


def test_paired_summary_keeps_worse_cases_and_empty_is_unknown():
    def arm(ids):
        return {'metrics': large_probe.metrics(ids, {'a': 1}), 'wall_seconds': 1}
    rows = [{'arms': {'baseline': arm(['a']), 'rerank': arm(['x', 'a'])}},
            {'arms': {'baseline': arm(['x']), 'rerank': arm(['a'])}}]
    summary = large_probe.summarize(rows)
    assert summary['paired_ndcg_delta']['improved'] == 1
    assert summary['paired_ndcg_delta']['worse'] == 1
    assert summary['rerank']['mrr_at_4'] == .75
    assert large_probe.summarize([]) == {'baseline': None, 'rerank': None}


def test_benchmark_adapter_preserves_passage_ids_and_cache(kb, monkeypatch, tmp_path):
    store, _, _ = kb
    corpus = [{'id': 'external-10', 'text': 'alpha'}, {'id': 'external-21', 'text': 'beta'}]
    store._cached_vectors([c['text'] for c in corpus], 'test', 10000)
    monkeypatch.setattr(large_probe, 'embed_corpus', lambda *a: None)
    plan = {'sources': [{'sha256': 'fixed'}], 'dataset': 'fixture', 'protocol': {}}
    generation = large_probe.build_benchmark(store, corpus, plan, tmp_path)
    result = store.search('alpha', mode='keyword')
    assert result['hits'][0]['name'] == 'external-10'
    assert result['hits'][0]['generation'] == generation
    assert all(h['role'] == 'direct' for h in result['hits'])
    values = large_probe.CachedVectors(store, [sha(c['text']) for c in corpus])
    assert len(values) == 2 and len(values[0]) == 2 and len(values[:1]) == 1


def test_budget_rejects_before_dispatch(kb, tmp_path):
    store, _, _ = kb
    with pytest.raises(ValueError, match='预算'):
        large_probe.embed_corpus(store, [{'id': '1', 'text': 'alpha'}],
            {'embedding_batch': 32, 'embedding_workers': 1, 'embedding_token_cap': 1}, tmp_path)
    assert store.events() == []


def test_unknown_usage_failure_stops_without_next_batch(kb, monkeypatch, tmp_path):
    store, client, _ = kb
    client.failure = True
    monkeypatch.setattr(large_probe, 'Embedder', lambda settings, record: Embedder(settings, record, client))
    corpus = [{'id': str(i), 'text': f'passage {i}'} for i in range(65)]
    with pytest.raises(EmbeddingFailure):
        large_probe.embed_corpus(store, corpus,
            {'embedding_batch': 32, 'embedding_workers': 1, 'embedding_token_cap': 100000}, tmp_path)
    assert len(client.calls) == 1
    assert len(store.events()) == 1
    assert store.events()[0]['status'] == 'failed'
    assert store.events()[0]['input_tokens'] is None


def test_duplicate_texts_share_embeddings_but_keep_corpus_ids(kb, monkeypatch, tmp_path):
    store, client, _ = kb
    monkeypatch.setattr(large_probe, 'Embedder', lambda settings, record: Embedder(settings, record, client))
    corpus = [{'id': str(i), 'text': 'same passage'} for i in range(65)]
    large_probe.embed_corpus(store, corpus,
        {'embedding_batch': 32, 'embedding_workers': 1, 'embedding_token_cap': 100000}, tmp_path)
    assert len(client.calls) == 1 and len(client.calls[0]['input']) == 1
    vectors = large_probe.CachedVectors(store, [sha(c['text']) for c in corpus])
    assert len(vectors) == 65 and vectors[0] == vectors[64]


def test_windows_manifest_replaces_existing_file_without_global_patch(tmp_path, monkeypatch):
    from milvus_lite.storage import manifest
    original_rename = os.rename
    monkeypatch.setattr(manifest, 'os', os)
    _install_manifest_compat(platform='nt')
    item = manifest.Manifest(str(tmp_path))
    item.current_seq = 10
    item.save()
    item.current_seq = 20
    item.save()
    assert manifest.Manifest.load(str(tmp_path)).current_seq == 20
    assert json.loads((tmp_path / 'manifest.json.prev').read_text())['current_seq'] == 10
    assert os.rename is original_rename
    assert not (tmp_path / 'manifest.json.tmp').exists()


def test_real_vector_index_multiple_flushes_reopens(tmp_path, monkeypatch):
    from milvus_lite.engine import collection
    # 缩小内存表只触发真实落盘路径，不伪造搜索结果。
    monkeypatch.setattr(collection, 'MEMTABLE_SIZE_LIMIT', 64)
    vectors = [[1., 0.] for _ in range(129)] + [[0., 1.]]
    adapter = MilvusVectors()
    adapter.build(tmp_path, 'flush-regression', vectors, 2)
    assert adapter.search(tmp_path, 'flush-regression', [0., 1.], 1) == [129]
