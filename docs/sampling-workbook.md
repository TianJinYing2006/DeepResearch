# W5 两级抽检工作单（设计评审 Q6 拍板）

> 抽检人：于晏（单人，判定以标注规范为准；reviewer 字段可补二审）  
> 日期：2026-09-06

## 📖 报告正文阅读入口（点击直接读，无需碰 JSON）

| 报告 | 阅读页 | 机器覆盖度 | 早停标签 |
|---|---|---|---|
| q_014（易/计算） | [docs/sampling/q_014.md](sampling/q_014.md) | 75% | ⚠️早停 |
| q_007（中/单轮） | [docs/sampling/q_007.md](sampling/q_007.md) | 33% | ⚠️早停 |
| q_016（中/学术） | [docs/sampling/q_016.md](sampling/q_016.md) | 75% | ⚠️早停 |
| q_013（难/多轮） | [docs/sampling/q_013.md](sampling/q_013.md) | 0% | ⚠️早停 |
| q_001（锚点） | [docs/sampling/q_001.md](sampling/q_001.md) | 100% | ✔️正常 |

每份阅读页 = 报告全文 + 3 条精读引用（claim / source / 来源原文片段 / 机器 note）+ 人工判定栏。判定结果告诉我，我回填工作单与 eval-report。  

## 一、报告级抽检（5 份完整报告通读）

| 报告 | 难度/类型 | 报告字数 | 引用数 | 机器覆盖度 | 停因/轮数 | 早停标签 |
|---|---|---|---|---|---|---|
| [raw](research_engine/eval/results/run_20260906_184156/raw/q_014.raw.json) q_014 | 易/计算 | 6265 | 56 | 75% | critic/4 | ⚠️早停 |
| [raw](research_engine/eval/results/run_20260906_184156/raw/q_007.raw.json) q_007 | 中/单轮 | 7618 | 93 | 33% | critic/4 | ⚠️早停 |
| [raw](research_engine/eval/results/run_20260906_184156/raw/q_016.raw.json) q_016 | 中/学术 | 5683 | 65 | 75% | critic/4 | ⚠️早停 |
| [raw](research_engine/eval/results/run_20260906_184156/raw/q_013.raw.json) q_013 | 难/多轮 | 5447 | 81 | 0% | critic/4 | ⚠️早停 |
| [raw](research_engine/eval/results/run_20260906_184156/raw/q_001.raw.json) q_001 | 难/计算 | 9509 | 27 | 100% | critic/4 | ✔️正常 |

**判定项（每份报告作答）：**

1. 报告质量观感：结构清晰/信息充分/有无明显事实错误？（0-10 分 + 一句点评）
2. 反思质量面：早停标签是否合理——当时信息真的够吗？（合理/偏早/偏晚）
3. 关键引用精读（每份抽 2~3 条，见下）：该论断是否被来源支持？

### 报告级精读引用（每份 3 条）

**q_014** 精读引用：
- [1] ❌ claim: ”，U+3000–U+303F 等）同样为 3 字节
      source: http://news.558idc.com/125096.html | note: 来源[3]仅说明'中文标点符号也占3字节'，但未指定U+3000–U+303F（CJK标点符号块）；且U+3000（全角
      人工判定（来源查到/论断忠实/存疑）：___
- [2] ✅ claim: ”，U+3000–U+303F 等）同样为 3 字节
      source: https://m.chaicp.com/list/2284.html | note: source[4]摘要称'中文字符数量约为2,000至4,000字左右'，faithful；[21][28]均复述，su
      人工判定（来源查到/论断忠实/存疑）：___
- [3] ❌ claim: 超大字符集中的汉字（如扩展 B 区 U+3400–U+4DBF、扩展 C/D/E/F/G 区等）属于辅助平面（Supplementary Planes），需用 UTF-16 代理对表示，在 UTF-8
      source: https://www.toutiao.com/article/7070882177503838759/ | note: 来源[1]全文讨论ASCII/GB2312/Unicode下字节与字符换算，未出现'历史参考价值''tokenizer实
      人工判定（来源查到/论断忠实/存疑）：___

**q_007** 精读引用：
- [1] ✅ claim: 其核心设计是显式状态机（Explicit State Machine）：所有节点共享一个可序列化的 State 对象，每次节点执行后更新该状态，支持检查点（checkpointing）、回滚与持久化
      source: https://modelengine.csdn.net/690b1cf25511483559e26cdf.html | note: 来源[2]摘要'注重任务协作与智能体角色分工'，'团队即接口'是其哲学凝练；[3][5][14]均强调'角色'与'任务'
      人工判定（来源查到/论断忠实/存疑）：___
- [2] ✅ claim: 其核心设计是显式状态机（Explicit State Machine）：所有节点共享一个可序列化的 State 对象，每次节点执行后更新该状态，支持检查点（checkpointing）、回滚与持久化
      source: https://juejin.cn/post/7643751135157780532 | note: 来源[5]摘要'AutoGen为对话驱动'，'对话即协议'是其核心哲学；[7]称'通过多个可对话的代理构建LLM应用'；
      人工判定（来源查到/论断忠实/存疑）：___
- [3] ✅ claim: 这种设计使流程逻辑高度透明、可追溯、可中断恢复，天然适配需严格步骤控制、审计合规或长周期任务（如金融审批流、医疗决策路径）的场景
      source: https://blog.csdn.net/leafff123/article/details/154174038 | note: 来源[1]摘要'适用于需严格控制步骤和状态持久化的场景'，'流程即契约'是其哲学凝练；[2]称'强调状态管理和流程可控性
      人工判定（来源查到/论断忠实/存疑）：___

**q_016** 精读引用：
- [1] ❌ claim: 其不依赖监督微调数据，仅需构建结构化知识库即可启动，适用于冷启动阶段或标注成本极高的专业领域（如法律文书解析、医疗诉讼支持）
      source: https://www.cnblogs.com/alexa2077/p/archive/2024/05/14 | note: source 2（博客园文章）摘要中未提及'高成本循环'或引用23，该 claim 错误归因，属张冠李戴。
      人工判定（来源查到/论断忠实/存疑）：___
- [2] ❌ claim: 其不依赖监督微调数据，仅需构建结构化知识库即可启动，适用于冷启动阶段或标注成本极高的专业领域（如法律文书解析、医疗诉讼支持）
      source: https://ubook.reader.qq.com/book-read/52248403/9 | note: source 6 未提出该 warning，归因错误；且内容与 source 7 的RAG时间匹配设计冲突。
      人工判定（来源查到/论断忠实/存疑）：___
- [3] ❌ claim: 其不依赖监督微调数据，仅需构建结构化知识库即可启动，适用于冷启动阶段或标注成本极高的专业领域（如法律文书解析、医疗诉讼支持）
      source: https://juejin.cn/post/7647054707223511059 | note: source 21（掘金文章）摘要未提及'高成本循环'或 source 23，该 claim 错误归因。
      人工判定（来源查到/论断忠实/存疑）：___

**q_013** 精读引用：
- [1] ✅ claim: P2P 模式通信轮次更少、资源利用率潜在更高，但缺乏全局视图导致复杂任务管理开销上升
      source: https://xie.infoq.cn/article/ad0d77c3420f31da8b6363f08 | note: 来源[4]虽未用'无银弹'一词，但指出两种模式各有优劣（主从'易于管理但单点故障'，P2P'可扩展性高但复杂任务管理复杂
      人工判定（来源查到/论断忠实/存疑）：___
- [2] ✅ claim: P2P 模式通信轮次更少、资源利用率潜在更高，但缺乏全局视图导致复杂任务管理开销上升
      source: https://max.book118.com/html/2024/0513/7000001050006106.shtm | note: 来源[5]称'研究关注两种模式在协作效率、故障隔离能力及系统可观测性上的表现，但未提供具体数据'，结合其对各自优缺点的陈
      人工判定（来源查到/论断忠实/存疑）：___
- [3] ✅ claim: P2P 模式通信轮次更少、资源利用率潜在更高，但缺乏全局视图导致复杂任务管理开销上升
      source: https://juejin.cn/post/7618784480569770024 | note: 来源[35]全面对比两种模式优劣，并称'需根据需求权衡选择'，隐含无银弹；其指出的各类挑战（单点故障、可观测性弱、状态一
      人工判定（来源查到/论断忠实/存疑）：___

**q_001** 精读引用：
- [1] ✅ claim: Transformer: 自注意力 $QK^\top$ 项主导 → $O(L^2)$
      source: https://blog.csdn.net/Silentambition/article/details/127325749 | note: 来源[2]指出'Mamba 的复杂度为 O(n)'，并说明'在长序列下具有显著优势'；Selective Scan 是 
      人工判定（来源查到/论断忠实/存疑）：___
- [2] ✅ claim: Transformer: 自注意力 $QK^\top$ 项主导 → $O(L^2)$
      source: https://blog.51cto.com/u_16213416/12249749 | note: 来源[3]称'Mamba 通过状态空间模型实现线性复杂度'，Selective Scan 是其具体算法实现，线性复杂度必
      人工判定（来源查到/论断忠实/存疑）：___
- [3] ✅ claim: Transformer: 自注意力 $QK^\top$ 项主导 → $O(L^2)$
      source: https://m.sohu.com/a/415945461_114877/?pvid=000115_3w_a | note: 来源[5]明确称'Transformer在处理长序列时存在O(n²)的计算复杂度问题'，并关联到自注意力机制，虽未提 $
      人工判定（来源查到/论断忠实/存疑）：___

## 二、引用级抽检（跨报告 12 条核验引用真实性）

| # | 报告 | 机器口径 | claim | source | 人工判定 |
|---|---|---|---|---|---|
| 1 | q_010 | ✅ | > 注：该分布未在研究发现中给出精确数值，但多源交叉印证了该量级与区间 | https://time.geekbang.org/column/article | 真实/存疑/不实 |
| 2 | q_009 | ✅ | [1][2][3][4][5][12][13][14][16][19] 等均以“大模型”（LLM）为默认对象展开论述，仅 | https://www.php.cn/faq/2134237.html | 真实/存疑/不实 |
| 3 | q_012 | ✅ | 内置CoT的OpenAI o1-preview模型表现更差（57.7%） | https://www.sohu.com/a/823497439_610300 | 真实/存疑/不实 |
| 4 | q_018 | ✅ | 研究发现未明确说明默认是否 deep copy 状态对象，但强调其支持确定性工具调用与检查点恢复 | https://arxiv.org/abs/2607.19297v1 | 真实/存疑/不实 |
| 5 | q_004 | ❌ | 向量 RAG（如基于稠密向量检索的 BERT/ColBERT 或嵌入模型）将文本切片为语义向量，依赖近似最近邻（ANN） | https://m.blog.csdn.net/m0_59164520/arti | 真实/存疑/不实 |
| 6 | q_002 | ✅ | 1. 图构建（Graph Construction）：非简单实体抽取，而是融合LLM驱动的Schema-informed | https://blog.csdn.net/uncle_ll/article/d | 真实/存疑/不实 |
| 7 | q_018 | ✅ | 结合 LangChain 生态实践，典型实现采用 copy.deepcopy() 或基于 Pydantic 的结构化克隆 | https://arxiv.org/abs/2607.19297v1 | 真实/存疑/不实 |
| 8 | q_015 | ❌ | 另部署输出分类器识别响应中是否泄露系统提示、内部状态或执行越权操作 | https://blog.csdn.net/qq_46987323/articl | 真实/存疑/不实 |
| 9 | q_008 | ❌ | GraphRAG Evaluation Suites（隐含于行业实践）：虽无统一公开基准，但 GraphRAG 架构（如 | https://modelengine.csdn.net/690c5346551 | 真实/存疑/不实 |
| 10 | q_018 | ✅ | 循环（Cycles）：StateGraph 明确支持图中存在循环边（即节点可返回自身或上游节点），这是其区别于 DAG  | https://devpress.csdn.net/aibjcy/69142b8 | 真实/存疑/不实 |
| 11 | q_018 | ✅ | 结合 LangChain 生态实践，典型实现采用 copy.deepcopy() 或基于 Pydantic 的结构化克隆 | https://max.book118.com/html/2019/0928/6 | 真实/存疑/不实 |
| 12 | q_002 | ❌ | ”），此类多跳、条件耦合、因果/时序敏感型问题，向量RAG因缺乏关系路径建模能力而准确率骤降 | https://blog.csdn.net/wddxwdwl/article/d | 真实/存疑/不实 |

**人工口径汇总**：真实 ___ 条 / 存疑 ___ 条 / 不实 ___ 条  

## 三、锚点桶复核（q_001：W4 已验证通过的重跑对照）

- W4 时该 query 答案通过校验（verified=True）；本次 v1.0 重跑覆盖度 100%、引用 27 条。
- 复核点：本次报告结论与 W4 结论是否一致（真回归 vs 数据漂移）？
- 人工判定：___