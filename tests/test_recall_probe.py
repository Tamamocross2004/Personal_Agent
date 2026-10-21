from pathlib import Path

import pytest

from agent.config import Settings
from knowledge.recall_probe import WarmVectors
from test_knowledge import kb


def test_default_recall_limits_unchanged_and_bounded():
    settings = Settings(_env_file=None)
    assert settings.knowledge_lexical_candidates == settings.knowledge_vector_candidates == 40
    for key in ['knowledge_lexical_candidates', 'knowledge_vector_candidates']:
        for value in [7, 201]:
            with pytest.raises(ValueError): Settings(_env_file=None, **{key: value})


def test_configured_recall_limits_reach_real_query_paths(kb):
    store, _, vectors = kb
    store.build([(f'{i}.txt', f'alpha document {i}'.encode()) for i in range(30)])
    store.settings = store.settings.model_copy(update={'knowledge_lexical_candidates': 8, 'knowledge_vector_candidates': 16})
    found = store.search('alpha', mode='hybrid')
    assert found['lexical_candidates'] == 8
    assert found['vector_candidates'] == 16


def test_warm_session_cannot_cross_root_or_generation(tmp_path):
    class Client:
        def search(self, **kwargs): return [[{'id': 12}]]
    adapter = WarmVectors(Client(), tmp_path, 'generation-a')
    assert adapter.search(tmp_path, 'generation-a', [1., 0.], 1) == [12]
    with pytest.raises(ValueError): adapter.search(tmp_path, 'generation-b', [1., 0.], 1)
    with pytest.raises(ValueError): adapter.search(tmp_path / 'other', 'generation-a', [1., 0.], 1)
