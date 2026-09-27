"""P0 profile 固化：档位表与运行时作用域单测（零 LLM、零外部依赖）。

- 档位数值 = 需求 10 §5.6 初始封顶（校准只能收紧）；
- contextvar 隔离：并发/串行 run 各自生效、互不污染全局 config；
- 模型解析：档位覆盖 smart/strategic/critic/validator，fast 保持全局；
- 路由缓存：不同档位不同实例，同档位复用。
"""
from __future__ import annotations

import threading
import time

from config import config
from research_engine.llm.router import get_router
from research_engine.runtime_profile import (
    current_profile,
    effective_llm_model,
    effective_research_config,
    set_profile,
    use_profile,
)
from web.backend.profiles import DEFAULT_PROFILE, PROFILES, resolve_profile


def test_profile_table_matches_requirements():
    expected = {
        "quick": (6, 2, 60_000, 15 * 60, "qwen-turbo", 0.75),
        "standard": (12, 3, 120_000, 30 * 60, "qwen-plus", 1.25),
        "deep": (20, 4, 200_000, 60 * 60, "qwen-plus", 1.50),
    }
    for name, (hops, subs, tokens, timeout, model, budget) in expected.items():
        profile = PROFILES[name]
        assert profile.max_total_hops == hops
        assert profile.max_subquestions == subs
        assert profile.token_budget == tokens
        assert profile.timeout_seconds == timeout
        assert profile.model == model
        assert profile.run_budget_cny == budget
        assert profile.version == "v1"
    assert DEFAULT_PROFILE == "quick"


def test_resolve_unknown_profile_returns_none():
    assert resolve_profile("nope") is None
    assert resolve_profile("") is None
    assert resolve_profile(" Quick ") is PROFILES["quick"]


def test_effective_research_config_isolated_per_scope(monkeypatch):
    monkeypatch.setattr(config.research, "max_total_hops", 20)
    monkeypatch.setattr(config.research, "max_subquestions", 4)
    monkeypatch.setattr(config.research, "token_budget", 200_000)

    with use_profile(resolve_profile("quick")):
        quick = effective_research_config()
        assert (quick.max_total_hops, quick.max_subquestions) == (6, 2)
        assert quick.token_budget == 60_000

    with use_profile(resolve_profile("standard")):
        standard = effective_research_config()
        assert (standard.max_total_hops, standard.max_subquestions) == (12, 3)

    # 作用域退出后回落全局，且全局从未被改写
    fallback = effective_research_config()
    assert fallback.max_total_hops == 20
    assert config.research.max_total_hops == 20
    assert config.research.max_subquestions == 4


def test_effective_llm_model_roles(monkeypatch):
    monkeypatch.setattr(config.llm, "fast_model", "fast-env")
    monkeypatch.setattr(config.llm, "smart_model", "smart-env")
    monkeypatch.setattr(config.llm, "strategic_model", "strategic-env")
    monkeypatch.setattr(config.llm, "critic_model", "critic-env")
    monkeypatch.setattr(config.llm, "validator_model", "validator-env")

    with use_profile(resolve_profile("quick")):
        assert effective_llm_model("smart") == "qwen-turbo"
        assert effective_llm_model("strategic") == "qwen-turbo"
        assert effective_llm_model("critic") == "qwen-turbo"
        assert effective_llm_model("validator") == "qwen-turbo"
        assert effective_llm_model("fast") == "fast-env"  # 压缩档保持全局配置

    # 无档位（CLI / eval）→ 全局配置逐角色回落
    assert effective_llm_model("smart") == "smart-env"
    assert effective_llm_model("critic") == "critic-env"
    assert effective_llm_model("validator") == "validator-env"


def test_router_cached_per_effective_models():
    with use_profile(resolve_profile("quick")):
        quick_router = get_router()
        assert get_router() is quick_router
    with use_profile(resolve_profile("standard")):
        standard_router = get_router()
        assert standard_router is not quick_router
    with use_profile(resolve_profile("quick")):
        assert get_router() is quick_router


def test_profile_contextvar_is_per_thread():
    results: dict[str, int] = {}

    def work(name: str) -> None:
        with use_profile(resolve_profile(name)):
            time.sleep(0.02)
            results[name] = effective_research_config().max_total_hops

    threads = [threading.Thread(target=work, args=(name,))
               for name in ("quick", "standard", "deep")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == {"quick": 6, "standard": 12, "deep": 20}
    assert current_profile() is None


def test_set_profile_none_restores_global(monkeypatch):
    monkeypatch.setattr(config.research, "max_total_hops", 20)
    set_profile(resolve_profile("quick"))
    try:
        assert effective_research_config().max_total_hops == 6
    finally:
        set_profile(None)
    assert effective_research_config().max_total_hops == 20
