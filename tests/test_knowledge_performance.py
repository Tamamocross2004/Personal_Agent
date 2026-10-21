"""有界正文读取应保持片段顺序、邻接和旧代际语义。"""
from test_knowledge import kb


def test_fetch_only_candidates_and_neighbors(kb, monkeypatch):
    store, _, vectors = kb
    store.build([('a.txt', ('alpha sentence. ' * 2000).encode()), ('b.txt', b'different document')])
    vectors.candidates = [10, 20]
    loaded = []
    original = store._chunks
    def capture(generation, indices):
        result = original(generation, indices)
        loaded.extend(result)
        return result
    monkeypatch.setattr(store, '_chunks', capture)
    result = store.search('question', mode='vector', k=2)
    assert [(h['chunk_id'], h['role']) for h in result['hits']] == [
        (10, 'direct'), (20, 'direct'), (9, 'adjacent'), (11, 'adjacent'), (19, 'adjacent'), (21, 'adjacent')]
    assert set(loaded) == {9, 10, 11, 19, 20, 21}


def test_keyword_empty_does_not_decode_unrelated_bodies(kb, monkeypatch):
    store, _, _ = kb
    store.build([('a.txt', b'alpha original')])
    loaded = []
    original = store._chunks
    def capture(generation, indices):
        loaded.extend(indices)
        return original(generation, indices)
    monkeypatch.setattr(store, '_chunks', capture)
    assert store.search('nonexistentword', mode='keyword')['hits'] == []
    assert loaded == []
