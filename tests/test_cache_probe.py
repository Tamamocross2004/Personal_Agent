"""缓存实验各组必须独立，避免前一变体预热后一变体。"""

from types import SimpleNamespace

from agent import cache_probe
from fakes import ScriptedLLM, completion


def test_probe_isolates_variants_and_warms_matching_baselines(monkeypatch):
    llm = ScriptedLLM([completion("好") for _ in range(9)])
    monkeypatch.setattr(cache_probe, "Settings", lambda: None)
    monkeypatch.setattr(
        cache_probe, "load_llm_config", lambda **kw: SimpleNamespace(provider="fake", model="fake")
    )
    monkeypatch.setattr(cache_probe, "LLMClient", lambda cfg: llm)
    assert cache_probe.main(["--sleep", "0"]) == 0
    requests = llm.requests
    prefixes = []
    for start in (0, 3, 6):
        cold, warm, variant = requests[start:start + 3]
        prefixes.append(cold["messages"][0]["content"])
        assert cold == warm
        assert variant["messages"][:len(cold["messages"])] == cold["messages"]
    assert len(set(prefixes)) == 3
    assert requests[2]["tool_choice"] == "none"
    assert requests[5]["tools"] is None
    assert requests[8]["tools"] == requests[6]["tools"]
    assert "tool_choice" not in requests[8]
    assert len(requests[8]["messages"]) == len(requests[6]["messages"]) + 1
