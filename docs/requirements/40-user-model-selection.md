# 需求-40-user-model-selection（模型可选 / 用户自配置）

> **状态：草稿 · 未立项**（2026-10-31 登记，来源：用户提出「模型配置栏可以改为可选项或用户自配置」）。
> 未立项阶段只记录范围、边界与未决问题；**不排期、不写代码**，立项时按本模板展开实施。
> 飞书镜像：不建（未立项阶段不占飞书文档位；立项后按模板创建）。
> 关联 Issue：[#312](https://github.com/TianJinYing2006/DeepResearch/issues/312)

## 1. 元信息

| 项 | 值 |
|---|---|
| 编号 | 40 |
| 标题 | 模型可选 / 用户自配置（模型配置栏） |
| 优先级 | P1（候选，未立项） |
| 状态 | 草稿 |
| 负责人 | TianJinYing2006 |
| 关联 Issue | #312 |
| 关联 PR | 待立项后建 |
| 创建 / 更新 | 2026-10-31 |

## 2. 问题背景

- 用户诉求：**「模型配置栏可以改为可选项或用户自配置」**——希望在前端选择/配置本次研究使用的模型，而不是完全由服务端档位固定。
- 现状约束：运行档位（quick / standard / deep）把模型固化在 `web/backend/profiles.py`（quick=`qwen-flash`、standard/deep=`qwen-plus`），前端 `/api/options` 只下发档位、run 请求体只接受 `profile`（`web/backend/main.py:546`），用户无法表达「用哪个模型」。
- 现实痛点（非假想）：
  - 2026-10-17 / 2026-10-31 两次 **`qwen-turbo` 429 `limit_requests`** 导致 quick 档不可用（需求 36 §12.7、Issue #310），服务端只能改 profile/env 换模型，用户只能等；
  - 同一问题下用户对「质量 vs 成本」偏好不同：有人愿意用 `qwen-plus`/`qwen-max` 换质量，有人只要便宜快速——档位模型一刀切无法覆盖；
  - 切换模型会同时影响**预算、快照、幂等、审计、egress 白名单**，必须成套设计，不能只暴露一个下拉框。
- 产品化边界：当前处于 L3-A/B 封闭内测（ADR-0017），模型选择可做，但**不得越过**公开注册/付费/SLA 等禁止面，且新功能默认关闭（CONTRIBUTING §2）。

## 3. 需求分析

**目标**：在不破坏「P0 profile 固化」（前端只提交受控枚举值、底层参数服务端固定）的前提下，让用户**可选**本次 run 的模型；「自配置」仅作为管理员/CLI 的高级路径，不对普通前端开放。

**要解决的具体问题**：

1. 模型可知可选：前端展示可选模型及其档位/价格级/可用性，用户选择后服务端校验并生效；
2. 口径不漂移：模型选择必须进 run 快照与证据清单，失败归因、预算、幂等、egress 一并覆盖；
3. 可回退：不传模型 = 现有档位默认行为，旧客户端/旧 run 完全不受影响；
4. 可控灰度：开关默认关闭，internal test 先验证成本与质量，再评估是否默认开。

**成功定义（可量化）**：

- 用户可选择白名单内任一模型发起 run，run 详情可审计到「实际模型 + 定价口径 + 快照版本」；
- 未计价/未授权模型被服务端 400 拒绝，不产生任何外部调用；
- 单 run 成本上限仍由预算门禁硬约束（不因用户选贵模型而突破档位预算）；
- 旧请求（不带 `model`）行为、测试、评测口径不变。

## 4. 当前设计

| 位置 | 现状 | 痛点 |
|---|---|---|
| `web/backend/profiles.py:28` | `RuntimeProfile.model` 固化档位模型；`PROFILE_VERSION="v2"` | 用户无法选；改模型 = 改代码/env |
| `research_engine/runtime_profile.py:33` | `_PROFILE_MODEL_ROLES = {smart, strategic, critic, validator}`，`effective_llm_model()` 把这 4 个角色钉在 `profile.model`；`fast`（压缩/摘要）保持全局 `FAST_MODEL` | 模型→角色的映射是隐含规则，暴露选择器前必须显式化 |
| `web/backend/main.py:600` | `/api/options` 只下发 `profiles`、`default_profile` | 没有模型清单/价格/可用性可下发 |
| `web/backend/main.py:546` | 创建 run 的请求体只有 `profile`（服务端解析，档位外参数忽略并留痕） | 无 `model` 入参；加参数需同步幂等指纹 |
| `web/backend/main.py:1105` | `_request_fingerprint(topic, instructions, profile)` 做幂等去重 | 指纹不含模型 ⇒ 同 topic 换模型会被误判为重复请求 |
| `web/backend/main.py:1135` | `_effective_run_budget(profile)` + `_admission_limits()` 按档位预算准入 | 用户选更贵模型时预算口径需重算（不能白送额度） |
| `config.py:66` | `config.llm.pricing` 只覆盖 `qwen-turbo/flash/plus/max` 四档（元/1K tokens） | 白名单必须与定价表一一对应，缺价模型无法记账 |
| `web/backend/egress.py:28` | `build_egress_snapshot(profile)` 记录外发快照（模型/检索域） | 选自定义模型后 egress 快照必须显式包含，否则审计缺项 |
| `web/frontend/src/features/launch/LaunchPanels.tsx:116` | 「运行档位」分段切换器；注释明确「档位由服务端固定底层参数，客户端不可覆盖」 | 需要同风格的「模型」选择条（受控枚举），并保持 a11y/键盘交互 |

## 5. 优化方案

**方案 A（推荐，先做）—— 档位内受控模型选择（白名单）**

1. `/api/options` 增加 `models`：每项 `{value, label, tier, price_input, price_output, available, note}`；只下发服务端配置且已通过可用性探测的模型；默认值 = 当前档位模型。
2. `POST /api/research` 请求体增加可选 `model`（枚举白名单校验，非法值 400 `invalid_model`）；`model` 为空 ⇒ 档位默认，行为与现在一致。
3. run 快照：`run.request.profile.snapshot()` 增记 `model`，或独立 `model_snapshot` 字段；证据 manifest 沿用现有快照链路。
4. 幂等：`_request_fingerprint` 纳入 `model`；同 topic 不同模型视为不同请求。
5. 预算：若所选模型贵于档位默认，`_effective_run_budget` 取 `min(档位预算, 该模型按 token 预算估算的成本上限)`；缺价模型直接 400，不做「按最贵兜底」的隐式行为。
6. 归因：模型侧失败沿用 `llm_error`（D-04），文案带「模型/限流」可操作信息；重试沿用 #310 有界重试。
7. 降级：可选「模型不可用时回退档位默认」为显式开关（默认关），回退必须留 degradation 事件，不能静默。

**方案 B（管理员/CLI 路径）—— 自由模型自配置**

- 允许 CLI/eval/管理员在**受控环境**下指定模型名（写入 run 快照与 pricing 校验），前端不开放自由文本输入。
- 禁止：任意 `base_url`/endpoint、任意 provider 凭据、绕过白名单的 URL 拼接——安全与合规边界不放开。

**不做 / 边界**

- 不改判定口径与评测产物；不改终局质量门；不新增外部 provider（本次仍限百炼 DashScope 兼容端点）；
- 不暴露内部 endpoint/凭据；不允许用户自定义 `base_url`；
- 不把「模型选择」做成挡掉预算门禁的后门。

## 6. 设计策略

- **口径来源**：模型→角色映射显式化（`_PROFILE_MODEL_ROLES` 注释 + 文档化）；`fast` 是否跟随选择需在立项时拍板（当前保持全局 `FAST_MODEL`，避免 standard 被连带升价）。
- **版本化**：模型进入 run 快照后，是否 bump `PROFILE_VERSION`（v2→v3）在立项时决定；原则：**默认行为不变可不 bump，默认语义变化必须 bump**。
- **灰度**：环境开关 `DR_MODEL_SELECTION_ENABLED`（默认 false）；staging internal test 先验证成本/质量/429 表现。
- **契约**：旧客户端不传 `model` 完全兼容；错误码沿用 `invalid_model`（400）/`llm_error`（503）/预算类既有错误码。
- **安全**：白名单 + 价格表 + 审计三重约束；昂贵模型可限制为管理员可用。
- **可回滚**：关开关即回到现状；run 快照新增字段为向后兼容的附加键。

## 7. 验收标准（DoD）

- [ ] `/api/options` 下发模型白名单（含价格档与可用性），前端可选择；
- [ ] 不带 `model` 的请求行为与现在完全一致（回归测试）；
- [ ] 非法/未计价/未授权模型返回 400 `invalid_model`，且**不发起任何外部调用**；
- [ ] run 快照/审计可追溯「本次实际模型 + 定价 + 档位」；
- [ ] 幂等指纹包含模型；同 topic 换模型不被误去重；
- [ ] 预算门禁在选贵模型时仍硬约束（超限排队/拒绝，口径写清）；
- [ ] egress 快照包含所选模型；失败归因仍为 `llm_error`；
- [ ] 前端选择器有单元测试（键盘/a11y/运行中禁用）；
- [ ] 开关默认关，staging internal test 通过后才评估开启；
- [ ] 文档：需求 10 变更记录、`.env.example`、CHANGELOG、figures（如涉及数字）。

## 8. 影响范围与风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 成本失控 | 用户选 `qwen-max` 等贵模型 | 预算门禁按模型价重算；昂贵模型限管理员；开关默认关 |
| 质量口径混淆 | 不同模型跑同一评测题 | 评测仍走固定口径；模型写入快照，不跨模型比较指标 |
| 幂等误判 | 指纹不含模型 | 立项即纳入指纹，补回归用例 |
| 审计缺项 | egress/快照漏模型 | `build_egress_snapshot` + 快照断言测试 |
| 供应商侧 429/404 | 模型不可用 | 可用性探测 + 有界重试 + 可操作错误文案 |
| 越界（任意 endpoint） | 用户自配置被滥用 | 仅白名单枚举；不开放 base_url/凭据 |

**影响模块（预计）**：`web/backend/profiles.py`、`web/backend/main.py`、`web/backend/egress.py`、`research_engine/runtime_profile.py`、`config.py`（pricing）、前端 `LaunchPanels`/`App`/`types`。**不动**：判定口径、评测产物、冻结结果。

## 9. 测试策略

- 单测：白名单校验（合法/非法/缺价）、指纹含模型、预算/准入按模型价、快照与 egress 字段、旧请求兼容；
- 前端单测：选择器渲染/键盘/a11y/禁用态；`options` 缺省回退；
- 集成：staging internal test 跑一次带模型选择的 run（计费与快照核对）；
- 端到端：旧流程（不传 model）e2e 全绿；新流程加分用例；
- **不跑**评测回放（未改口径）；如需质量对比，单独立项按评测纪律做。

## 10. 变更记录

| 日期 | 类型 | 原因 | 改动摘要 | 关联 PR/commit |
|---|---|---|---|---|
| 2026-10-31 | 文档 | 用户提出「模型配置栏可以改为可选项或用户自配置」 | 建档为**草稿·未立项**：记录现状、方案 A/B、边界、DoD 与未决问题；Issue #312 | 本文档 / #312 |

## 11. 未决问题（立项前拍板）

1. **白名单范围**：只开 quick/standard 已用过的 `qwen-flash`/`qwen-plus`，还是含 `qwen-max`/`qwen-turbo`？（推荐：先 flash/plus，max 仅管理员）
2. **预算口径**：按「用户所选模型价格 × 档位 token 预算」动态算上限，还是保持档位预算不变（贵模型自然更早超限）？（推荐：前者，口径写清）
3. **`fast` 角色**：是否跟随用户选择？（推荐：不跟随，保持 `FAST_MODEL` 全局口径；如跟随需重新评估 standard 成本）
4. **版本化**：默认行为不变是否 bump `PROFILE_VERSION`？（推荐：不 bump；若默认值/语义变化则 bump）
5. **前端落点**：「运行档位」下方的第二段选择条，还是收进「高级设置」？（推荐：档位下方，默认收起高级项）
6. **是否做模型可用性探测**：`/api/options` 每次实时探测有成本/时延；建议缓存探测结果（短 TTL）+ 失败降级到静态白名单。
