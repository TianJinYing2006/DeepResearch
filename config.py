"""DeepResearch 全局配置。

所有部署相关的可调参数集中在此，便于从 .env 或环境变量覆盖。
"""
import os
from dataclasses import dataclass, field
from typing import Dict

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class LLMConfig:
    """三层 LLM 分级配置（借鉴 gpt-researcher 的 FAST/SMART/STRATEGIC）。

    W4（grill Q7）：分档配置——planner_model / critic_model 独立可配，
    默认均回落 STRATEGIC_MODEL（qwen-plus），保 W3 实测基线；
    strategic_model 保留为 planner_model 的兼容别名（router.strategic_* 继续服务 planner）。
    """
    base_url: str = field(default_factory=lambda: _env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    api_key: str = field(default_factory=lambda: _env("DASHSCOPE_API_KEY"))

    # 三层分级：fast（摘要/提取）、smart（分析/写作）、strategic（规划/裁决）
    fast_model: str = field(default_factory=lambda: _env("FAST_MODEL", "qwen-turbo"))
    smart_model: str = field(default_factory=lambda: _env("SMART_MODEL", "qwen-plus"))
    # W4 Q7：分档。优先各自 env，未设回落 STRATEGIC_MODEL（向后兼容单档配置）
    planner_model: str = field(default_factory=lambda: _env("PLANNER_MODEL", _env("STRATEGIC_MODEL", "qwen-plus")))
    critic_model: str = field(default_factory=lambda: _env("CRITIC_MODEL", _env("STRATEGIC_MODEL", "qwen-plus")))
    # W7 TBD-7 C：validator 可独立降档，默认回落 smart_model（未设时等价 qwen-plus）
    validator_model: str = field(default_factory=lambda: _env("VALIDATOR_MODEL", _env("SMART_MODEL", "qwen-plus")))
    # 兼容别名（W4 Q7）：strategic_model 语义 = planner_model，env 读 STRATEGIC_MODEL
    strategic_model: str = field(default_factory=lambda: _env("STRATEGIC_MODEL", "qwen-plus"))

    temperature: float = 0.2
    max_tokens: int = 4096
    #: F13（审计）：LLM 调用默认超时（秒）；0 = 不设（仅显式 timeout 生效）。
    #: 任务时限（worker/RunManager 注入）更紧时自动收窄，避免单次调用挂死整个节点。
    request_timeout_seconds: float = field(
        default_factory=lambda: float(_env("LLM_REQUEST_TIMEOUT_SECONDS", "180")))

    # W3（grill Q6）：分层定价表（元/1K tokens，取 output 最贵档做保守上界）。
    # ⚠️ 价格有时效，默认值以阿里云官网为准，失效请更新——不要相信任何写死的长期价。
    pricing: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "qwen-turbo": {"input": 0.0003, "output": 0.0006},
        # #310：quick 档现用 flash；价格以百炼官方按量计费为准（华北2，≤128K 档，
        # 2026-10-31 核对：输入 $0.022/M、输出 $0.216/M，折人民币约 0.00015/0.0015 元/1K）。
        # 阶梯计费（128K/256K 以上更贵）未建模——当前 prompt 均 <128K，属已知简化。
        "qwen-flash": {"input": 0.00015, "output": 0.0015},
        "qwen-plus": {"input": 0.0008, "output": 0.002},
        "qwen-max": {"input": 0.0024, "output": 0.0096},
        "deepseek-r1": {"input": 0.003, "output": 0.012},  # 预留 W4 强推理切换
    })


@dataclass
class SearchConfig:
    """网络搜索配置（可切换 Provider）。

    ``provider`` 取值见 :func:`research_engine.search.base.create_search_provider`
    （目前 ``bocha`` / ``tavily``）。⚠️ 该开关必须经工厂函数装配才生效 —— 直接
    ``BochaSearchProvider()`` 会绕过配置，使切换失效（W9 接线时修）。
    """
    provider: str = field(default_factory=lambda: _env("SEARCH_PROVIDER", "bocha"))
    bocha_api_key: str = field(default_factory=lambda: _env("BOCHA_API_KEY"))
    #: Tavily（1000 次/月免费，专为 Agent 设计）。博查额度耗尽时可切到此源。
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    #: 学术检索（arXiv）开关。出口不通或不需要学术源时可关掉，避免每跳都记一条降级。
    enable_arxiv: bool = field(
        default_factory=lambda: _env("ENABLE_ARXIV", "true").lower() != "false")
    max_results: int = 8
    # W4 Q3：Semantic Scholar 后处理管道（可选，缺 key 静默跳过；citationCount 只采不决策）
    semantic_scholar_api_key: str = field(default_factory=lambda: _env("SEMANTIC_SCHOLAR_API_KEY"))


@dataclass
class CodeExecConfig:
    """W4 Q2 代码执行沙箱配置（三层纵深：subprocess -I -E -S + AST 白名单 + Audit Hook）。

    Windows 现实：resource 模块 Unix-only，timeout 是唯一可靠 kill；Job Object 走可选开关。
    """
    timeout: int = 15                      # wall-clock 超时（秒），Windows 唯一可靠 kill 手段
    max_output_bytes: int = 128 * 1024     # stdout/stderr 各截断上限，超限标 [TRUNCATED]
    concurrency: int = 2                   # code 同时执行上限（信号量默认 2，可配 4；防 CPU 密集抢占主进程）
    use_job_object: bool = field(default_factory=lambda: _env("CODE_EXEC_USE_JOB", "false").lower() == "true")  # Windows Job Object 可选增强
    #: 审计 F03：当前模板是固定 n=8192 的 FLOPs 示例，不消费 query 的数值/单位/公式，
    #: 「运行成功」≠「回答了问题」——作为事实证据会污染报告（且高置信度优先入池）。
    #: 在结构化计算协议（指定目标/输入/单位/公式 + 受限脚本）落地前默认关闭；
    #: 打开仅用于调试工具链（旧 W4/W8 行为），不应用于真实研究。
    evidence_enabled: bool = field(
        default_factory=lambda: _env("CODE_EXEC_EVIDENCE_ENABLED", "false").lower() == "true")


@dataclass
class RAGConfig:
    """RAG 配置（需求 23：三层数据 / 分块 v2 / 双路召回 + RRF / 云端 rerank / 证据预算）。"""
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL", "http://127.0.0.1:6333"))
    collection: str = "deepresearch_docs"
    embedding_model: str = "text-embedding-v3"
    chunk_size: int = 800          # 兼容保留：字符级资源口径（旧分块器/兜底）
    chunk_overlap: int = 100       # 兼容保留（v2 的 overlap 以 token 计，见 chunk_overlap_tokens）
    top_k: int = 5                 # 证据组装后最终入 prompt 的条数
    # ---- 需求 23：分块 v2（结构保真；尺寸用 token 预算，字符仅用于限额）----
    chunk_tokens: int = field(default_factory=lambda: int(_env("RAG_CHUNK_TOKENS", "400")))
    chunk_overlap_tokens: int = field(
        default_factory=lambda: int(_env("RAG_CHUNK_OVERLAP_TOKENS", "60")))
    chunker_version: str = "v2"
    # ---- 需求 23：双路召回 + RRF 融合（参数为初始值，由评测调整）----
    recall_per_route: int = field(default_factory=lambda: int(_env("RAG_RECALL_PER_ROUTE", "20")))
    rrf_k: int = field(default_factory=lambda: int(_env("RAG_RRF_K", "60")))
    max_per_doc: int = field(default_factory=lambda: int(_env("RAG_MAX_PER_DOC", "2")))
    evidence_max_tokens: int = field(
        default_factory=lambda: int(_env("RAG_EVIDENCE_MAX_TOKENS", "1500")))
    # ---- 需求 23：云端 rerank（DashScope gte-rerank；fail-open）----
    use_rerank: bool = field(default_factory=lambda: _env("DR_RAG_RERANK", "false").lower() == "true")
    rerank_model: str = field(default_factory=lambda: _env("DR_RAG_RERANK_MODEL", "gte-rerank-v2"))
    rerank_url: str = field(default_factory=lambda: _env(
        "DR_RAG_RERANK_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"))
    rerank_timeout_seconds: float = field(
        default_factory=lambda: float(_env("DR_RAG_RERANK_TIMEOUT", "8")))
    # P0-8a 解析限额（防资源耗尽 / 压缩炸弹；超限抛 IngestLimitExceeded）
    max_pages: int = field(default_factory=lambda: int(_env("RAG_MAX_PAGES", "200")))
    max_chars: int = field(default_factory=lambda: int(_env("RAG_MAX_CHARS", "2000000")))
    max_chunks: int = field(default_factory=lambda: int(_env("RAG_MAX_CHUNKS", "2000")))
    parse_timeout_seconds: float = field(
        default_factory=lambda: float(_env("RAG_PARSE_TIMEOUT_SECONDS", "60")))


@dataclass
class ResearchConfig:
    """研究流程配置（成本/质量旋钮，借鉴 dzhng 的 breadth/depth）。"""
    max_depth: int = 5          # 多跳检索最大深度（防死循环）；W1 改为全局 max_total_hops 后作为单子问题语义上限保留
    breadth: int = 3            # 每轮生成的搜索查询数
    max_subquestions: int = 4   # Planner 最多分解的子问题数
    max_concurrent: int = 3     # 并行检索数（W1 不启用，留 W2 与 Send 并行）
    min_sources_for_crosscheck: int = 2  # 多源印证所需最少独立来源数

    # ---- W1 新增：全局预算 / 硬闸（呼应 grill Q4/Q5/Q6）----
    max_total_hops: int = 20    # 全局总跳数上限；与旧 max_depth×max_subquestions=5×4 精确等价（Q4=A）
    # ⚠️ 语义变更（W9 后）：动态化前它是**实际生效**的每子问题跳数上限；动态化后它
    # 退化为「子问题数不可得时的静态兜底」。真正生效值是
    # `ceil(max_total_hops / 实际子问题数)`（见 graph.effective_per_subq_hop_cap）。
    # 默认配置下两者相等（ceil(20/4)=5）⇒ 与 W1 的 Q5=A 防饿死语义逐跳等价。
    # 用 ceil 而非 floor：floor 会让「每子问题上限 × 子问题数」小于总预算
    # （如 8 个子问题 → 2×8=16 < 20），导致预算没用完就被 cap 提前掐停。
    per_subq_hop_cap: int = 5   # 每子问题跳数上限的静态兜底（max_total_hops/max_subquestions，Q5=A）
    max_replan: int = 1         # revise 触发 Planner.replan 的最大次数，硬上限防空转（Q2-B 兜底）
    token_budget: int = 200_000 # LLM token 总预算，作为硬闸停止条件之一（Q6-B）；正常等价预算下不先于 hop 触发
    # ---- F13（审计）：预算准入与压缩边界 ----
    #: 为写作/校验预留的 token 额度：研究循环（critic 硬闸）在
    #: `token_budget - token_budget_reserve` 处提前停止，保证报告/验证仍有预算；
    #: `token_budget` 仍是**每次外部调用的绝对准入线**（耗尽即不再发起调用）。
    token_budget_reserve: int = field(
        default_factory=lambda: int(_env("TOKEN_BUDGET_RESERVE", "40000")))
    #: A3（2026-10-07）：**校验地板**——写/压缩阶段的 LLM 调用在剩余预算低于该值时停止
    #: （压缩可降级保留原文），保证 validator 至少还有额度执行。
    #: ⚠️ 地板只保证「校验有机会跑」，**不保证「校验跑得完」**：A3 落地后 mini 锚点实测
    #: q_002（112 条引用）仍 112 条全部 budget_rejected —— 根因是需求 19 的分批把整段
    #: findings_text 在**每批重复发送**，7 批 ≈ 111k 远超地板（2026-10-07 的旧注释按
    #: 53 条引用 / 4 批 ≈ 55-60k 推算，已被实测证伪）。
    #: 真正的修正是 A4（validator 按批分片上下文，需求 28）：q_002 校验需求
    #: 111k → 32.7k（batch=32；含 repair 后二次校验共约两轮 ≈ 65k）。
    #: 本值是否上调、上调多少，按 A4 落地后的实测缺口决定（见 Issue #154）。
    validation_floor: int = field(
        default_factory=lambda: int(_env("VALIDATION_FLOOR", "30000")))
    #: 压缩触发：findings 总字符超过该值也触发（不再只看条数——token 体积口径）
    compress_trigger_chars: int = field(
        default_factory=lambda: int(_env("COMPRESS_TRIGGER_CHARS", "60000")))
    #: 单次 compress 的 LLM 调用组数上限（超出部分保持原文，单节点调用数有界）
    compress_max_groups: int = field(
        default_factory=lambda: int(_env("COMPRESS_MAX_GROUPS", "40")))


@dataclass
class OtelConfig:
    """OTel 可观测配置（需求 33：Langfuse → OTel 原生迁移，替换 W3 LangfuseConfig）。

    三态开关：endpoint 齐备且 enabled != false 即自动启用；`DR_OTEL_ENABLED=false` 强制禁用；
    缺 endpoint 自动降级（零外发）。与 web/backend/otel.py 同 env（OTEL_EXPORTER_OTLP_ENDPOINT）。
    """
    endpoint: str = field(default_factory=lambda: _env("OTEL_EXPORTER_OTLP_ENDPOINT"))
    enabled: str = field(default_factory=lambda: _env("DR_OTEL_ENABLED", "auto").lower())
    service_name: str = field(default_factory=lambda: _env("DR_OTEL_SERVICE_NAME", "research-engine"))
    trace_url_template: str = field(default_factory=lambda: _env("DR_OTEL_TRACE_URL_TEMPLATE"))
    mask_sensitive: bool = field(default_factory=lambda: _env("DR_OTEL_MASK_SENSITIVE", "false").lower() == "true")


@dataclass
class MailConfig:
    """需求 24：邮件通道（SMTP-first，任意服务商；stdlib 实现）。

    ``host`` / ``sender`` 为空 = 未配置：``/api/auth/forgot`` 返回结构化
    ``mail_unavailable``（503），管理员 CLI 兜底路径不变。
    """

    host: str = field(default_factory=lambda: _env("DR_SMTP_HOST"))
    port: int = field(default_factory=lambda: int(_env("DR_SMTP_PORT", "465")))
    user: str = field(default_factory=lambda: _env("DR_SMTP_USER"))
    password: str = field(default_factory=lambda: _env("DR_SMTP_PASSWORD"))
    sender: str = field(default_factory=lambda: _env("DR_SMTP_FROM"))
    tls: str = field(default_factory=lambda: _env("DR_SMTP_TLS", "ssl").lower())
    base_url: str = field(default_factory=lambda: _env("DR_MAIL_BASE_URL", "http://localhost:5173"))
    reset_cooldown_seconds: int = field(
        default_factory=lambda: int(_env("DR_RESET_COOLDOWN_SECONDS", "60")))

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)


@dataclass
class ObservabilityConfig:
    """需求 25：错误追踪（Sentry 协议；DSN 空 = 关闭）。

    只依赖 Sentry 协议：DSN 可指向自托管 GlitchTip（默认，数据不出境）/
    阿里云 ARMS RUM / Sentry Cloud（需数据出境评审）。PII 策略见
    ``web/backend/observability.py``（不采内容、清洗凭据）。
    """

    sentry_dsn: str = field(default_factory=lambda: _env("DR_SENTRY_DSN"))
    sentry_dsn_frontend: str = field(default_factory=lambda: _env("DR_SENTRY_DSN_FRONTEND"))
    sentry_environment: str = field(default_factory=lambda: _env("DR_SENTRY_ENVIRONMENT", "staging"))
    sentry_release: str = field(default_factory=lambda: _env("DR_SENTRY_RELEASE"))
    sentry_traces_sample_rate: float = field(
        default_factory=lambda: float(_env("DR_SENTRY_TRACES_SAMPLE_RATE", "0")))

    @property
    def enabled(self) -> bool:
        return bool(self.sentry_dsn)


@dataclass
class ExperimentConfig:
    """W7 TBD-8 受控单变量实验开关。默认全开 = 保持当前行为；全关 = v1.1 基线。"""

    critic_gap_enabled: bool = field(
        default_factory=lambda: _env("CRITIC_GAP_ENABLED", "true").lower() == "true"
    )
    validator_fixes_enabled: bool = field(
        default_factory=lambda: _env("VALIDATOR_FIXES_ENABLED", "true").lower() == "true"
    )
    writer_sectioned_feed_enabled: bool = field(
        default_factory=lambda: _env("WRITER_SECTIONED_FEED_ENABLED", "true").lower() == "true"
    )
    validator_trim_enabled: bool = field(
        default_factory=lambda: _env("VALIDATOR_TRIM_ENABLED", "true").lower() == "true"
    )
    # W7 F3: keep assertive-claim filtering independently switchable. When
    # unset, follow VALIDATOR_FIXES_ENABLED so the legacy all-off arm keeps its denominator.
    validator_assertive_filter_enabled: bool = field(
        default_factory=lambda: _env(
            "VALIDATOR_ASSERTIVE_FILTER_ENABLED",
            _env("VALIDATOR_FIXES_ENABLED", "true"),
        ).lower() == "true"
    )


@dataclass
class WebSnapshotConfig:
    """需求 34 E1：网页快照抓取与落盘（TBD-1~3 拍板口径，2026-10-11）。

    - `enabled`：功能总开关（`DR_WEB_SNAPSHOT_ENABLED=false` 完全回到「标题 + 摘要」行为）；
    - 预算三闸（run 级）：次数 ≤15 / 原始字节 ≤5MB / 抓取耗时 ≤90s，每次**尝试**都计数（含重试）；
    - 文本上限按 **UTF-8 字节数**（TBD-1 拍板）：快照入库 64KB（超限截断并标记）；
      finding 正文另行取头 4000 字符（对齐裁判层既有 4000 截断阈值，控制 prompt 体积；
      全文在快照中，E2/E3 由快照回查，不依赖 finding 正文）；
    - robots：遵守（TBD-2② 拍板）；无法完成检查 ⇒ 仅抓 `approved_domains` 明确批准来源（默认拒绝）。
    """

    enabled: bool = field(
        default_factory=lambda: _env("DR_WEB_SNAPSHOT_ENABLED", "true").lower() != "false")
    top_n: int = field(default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_TOP_N", "3")))
    finding_max_chars: int = field(
        default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_FINDING_MAX_CHARS", "4000")))
    max_text_bytes: int = field(
        default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_MAX_TEXT_BYTES", str(64 * 1024))))
    max_fetches_per_run: int = field(
        default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_MAX_FETCHES", "15")))
    max_bytes_per_run: int = field(
        default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_MAX_BYTES", str(5 * 1024 * 1024))))
    max_seconds_per_run: float = field(
        default_factory=lambda: float(_env("DR_WEB_SNAPSHOT_MAX_SECONDS", "90")))
    connect_timeout_seconds: float = field(
        default_factory=lambda: float(_env("DR_WEB_SNAPSHOT_CONNECT_TIMEOUT", "3")))
    read_timeout_seconds: float = field(
        default_factory=lambda: float(_env("DR_WEB_SNAPSHOT_READ_TIMEOUT", "10")))
    max_response_bytes: int = field(
        default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_MAX_RESPONSE_BYTES", str(2 * 1024 * 1024))))
    max_retries: int = field(default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_MAX_RETRIES", "2")))
    per_domain_interval_seconds: float = field(
        default_factory=lambda: float(_env("DR_WEB_SNAPSHOT_DOMAIN_INTERVAL", "0.2")))
    max_domain_failures: int = field(
        default_factory=lambda: int(_env("DR_WEB_SNAPSHOT_DOMAIN_FAILURES", "2")))
    #: robots 检查不可完成时唯一允许的来源白名单（逗号分隔 hostname；默认空 = 默认拒绝）
    approved_domains: set = field(default_factory=lambda: {
        d.strip().lower() for d in _env("DR_WEB_FETCH_APPROVED_DOMAINS").split(",") if d.strip()
    })
    user_agent: str = field(
        default_factory=lambda: _env("DR_WEB_SNAPSHOT_USER_AGENT", "iKaojuBot/1.0"))


@dataclass
class ReportStreamConfig:
    """需求 35 R4：报告正文流式（TBD-6 拍板，2026-10-12）。

    - `enabled`：总开关（`DR_REPORT_STREAM_ENABLED=false` 完全回到节点级事件，无增量）；
    - `flush_chars`：``REPORT_DELTA`` 落库/发帧的**持久化粒度**（每累计 N 字符合并一条
      事件进 `run_events`，支持 `Last-Event-ID` 断线重连；收尾/重置前强制冲刷）。
    """

    enabled: bool = field(
        default_factory=lambda: _env("DR_REPORT_STREAM_ENABLED", "true").lower() != "false")
    flush_chars: int = field(
        default_factory=lambda: max(1, int(_env("DR_REPORT_STREAM_FLUSH_CHARS", "400"))))


@dataclass
class HitlConfig:
    """需求 36：HITL 交互闭环开关与等待策略（§12.2 / TBD-3、TBD-5）。

    **默认全部关闭**（§12.1「保留默认关闭」；关闭态节点序列/事件序列与历史一致，
    保护 W8 基线与评测可比性）。灰度按 §12.4：先内部单开关观察，再扩大。

    - `clarify_enabled`：提交后、规划前的澄清提问（≤max_clarify_rounds 轮）；
    - `plan_approval_enabled`：plan 产出后停等用户批准/自然语言修改（≤max_approval_rounds 轮）；
    - `followup_enabled`：报告后追问（Phase 3 使用）；
    - TTL：等待态到期——澄清到期按空回答继续（留痕）；审批到期**不自动批准**
      （`TIMED_OUT` + `stop_reason=approval_expired`）。
    - 仅队列模式（Worker）支持：API 进程在非 queue 模式启动时强制关闭（见 main.py）。
    """

    clarify_enabled: bool = field(
        default_factory=lambda: _env_bool("DR_HITL_CLARIFY", False))
    plan_approval_enabled: bool = field(
        default_factory=lambda: _env_bool("DR_HITL_PLAN_APPROVAL", False))
    followup_enabled: bool = field(
        default_factory=lambda: _env_bool("DR_HITL_FOLLOWUP", False))
    clarify_ttl_seconds: int = field(
        default_factory=lambda: max(60, _env_int("DR_HITL_CLARIFY_TTL_SECONDS", 86400)))
    approval_ttl_seconds: int = field(
        default_factory=lambda: max(60, _env_int("DR_HITL_APPROVAL_TTL_SECONDS", 86400)))
    max_clarify_rounds: int = field(
        default_factory=lambda: max(1, _env_int("DR_HITL_MAX_CLARIFY_ROUNDS", 2)))
    max_approval_rounds: int = field(
        default_factory=lambda: max(1, _env_int("DR_HITL_MAX_APPROVAL_ROUNDS", 2)))


@dataclass
class McpConfig:
    """需求 37 M1：消费外部 MCP（TBD-1/TBD-2/TBD-7，2026-10-14 拍板）。

    **默认关闭**（关闭态主链路与评测路径零漂移）；开启是一次显式运营决策
    （含数据外发披露，见 run 级 egress 快照）。两开关进 `config_snapshot`，
    eval 强制关闭（`research_engine/eval/run.py`）。

    `DR_MCP_SERVERS`（JSON 数组）元素 schema（TBD-2）：
      - `name`：server 唯一名（记账/审计/来源标签用）；
      - `url`：Streamable HTTP 终点（默认仅 HTTPS；私网需显式放行）；
      - `tools`：允许调用的**查询型**工具名列表（v1 不做 LLM function-calling）；
      - `timeout`：单次调用超时秒数（默认 15，v1 上限 15，越界 fail-fast）；
      - `max_result_bytes` / `max_findings`：响应字节上限 / 单 server 入池条数上限；
      - `allow_private` + `private_reason`：私网段显式放行（必须带理由，进审计）。
    解析与 URL 安全校验在 `research_engine.mcp.consumer`（启用时构建 fail-fast）；
    本类仅持有开关与原始串，避免 config 模块引入校验/网络依赖。
    """

    consume_enabled: bool = field(
        default_factory=lambda: _env_bool("DR_MCP_CONSUME_ENABLED", False))
    server_enabled: bool = field(
        default_factory=lambda: _env_bool("DR_MCP_SERVER_ENABLED", False))
    #: 对外 Server 名（工具面标识 + service principal `mcp:<name>`；TBD-10）
    server_name: str = field(
        default_factory=lambda: (_env("DR_MCP_SERVER_NAME", "ikaoju").strip()
                                 or "ikaoju"))
    servers_json: str = field(default_factory=lambda: _env("DR_MCP_SERVERS", ""))


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    otel: OtelConfig = field(default_factory=OtelConfig)
    code_exec: CodeExecConfig = field(default_factory=CodeExecConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    mail: MailConfig = field(default_factory=MailConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    web_snapshot: WebSnapshotConfig = field(default_factory=WebSnapshotConfig)
    report_stream: ReportStreamConfig = field(default_factory=ReportStreamConfig)
    hitl: HitlConfig = field(default_factory=HitlConfig)
    mcp: McpConfig = field(default_factory=McpConfig)


config = Config()
