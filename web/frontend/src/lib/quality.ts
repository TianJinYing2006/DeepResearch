/** 质量判定模型（验收点②）：把「任务完成」与「质量通过」拆成**两个独立维度**。
 *
 * 为什么需要它：
 * 现状 `statusPresentation(status)` 只有一维 —— `done` 的标签是「研究完成」。
 * 但**跑完 ≠ 引用通过**：需求 28 实测「重型题写作后只剩 1,517 token，112 条引用**全部未校验**」，
 * 这种运行同样会走到 `done`。若界面只说「研究完成」，用户会把「没查过」读成「查过了且没问题」——
 * 这正是本项目最忌讳的那类假信号（参见 docs/requirements/28-validator-sharded-context.md）。
 *
 * 口径**不在前端发明**，直接对齐后端权威定义（注意：本文件不得出现后端包名的字面量，
 * `tools/check_frontend_boundary.py` 会拦 —— 故此处只写文件名）：
 * - 后端 `repair.py:13,36-39`：**只有「确证失败」可删**（`verified=False` 且
 *   `verification_failed=False`）；UNKNOWN（`verification_failed=True`）必须保留 ⇒ 两者不同质。
 * - 后端 `render.py:93-122`：三口径 —— 存在性 / 忠实度（严格，`verified` = 存在且忠实）
 *   / 宽松口径（`verified_relaxed` = 存在且（忠实或多源印证））。
 *
 * 两个维度**正交**，不可互相推导：
 * - 任务维度 `taskComplete`：是否正常跑完（取消 / 超时 / 失败都不是跑完）；
 * - 质量维度 `QualityVerdict`：引用核验到什么程度。
 * 取消的运行可以已有部分结论；跑完的运行可以一条都没校验。
 */
import type { CitationResult, RunFinishedEvent, ResearchResult } from '../types/agui'

/** 单条引用的核验结论（四态，与后端三态一一对应）。 */
export type CitationVerdict =
  /** 严格通过：存在且忠实（`verified`）。 */
  | 'verified'
  /** 宽松通过：存在且（忠实或多源印证），仅作解释性附注，不等于严格通过。 */
  | 'relaxed'
  /** 确证失败：查过了，结论不成立（`verified=False` 且未被标为未完成）。 */
  | 'refuted'
  /** 校验未完成（UNKNOWN）：LLM 失败 / 无反馈 / 结论错位 ⇒ **不得当作通过**。 */
  | 'unknown'

export function citationVerdict(citation: CitationResult): CitationVerdict {
  // 顺序即语义：`verification_failed` 必须先判 —— 后端可能出现
  // 「verification_failed=true 且 note 里同时写了别的」的组合，UNKNOWN 不可降格成失败或通过。
  if (citation.verification_failed) return 'unknown'
  if (citation.verified) return 'verified'
  if (citation.verified_relaxed) return 'relaxed'
  return 'refuted'
}

export type QualityKind =
  /** 输出待审核：命中内容安全预检，正文脱敏、复核前不开放查看与导出。 */
  | 'under-review'
  /** 确证失败：至少一条引用被查实不成立。 */
  | 'refuted'
  /** 校验未完成：至少一条引用是 UNKNOWN（含「预算耗尽导致整批未校验」这一实测形态）。 */
  | 'incomplete'
  /** 仅宽松口径通过：没有确证失败，但严格口径未全过。 */
  | 'relaxed'
  /** 引用通过：严格口径全过。 */
  | 'passed'
  /** 无引用可核：本轮没有生成可校验引用。 */
  | 'no-citations'
  /** 尚未产生质量结论：运行中、或终局但无结果载荷。 */
  | 'pending'

export type QualityTone = 'ok' | 'warn' | 'bad' | 'neutral'

export type QualityCounts = {
  total: number
  verified: number
  relaxed: number
  refuted: number
  unknown: number
  /** 存在性通过数（严格口径的分母）。 */
  existence: number
}

export type QualityVerdict = {
  kind: QualityKind
  /** 面向用户的中文标签（与需求用语一致：引用通过 / 确证失败 / 校验未完成 / 输出待审核）。 */
  label: string
  tone: QualityTone
  /** 一句话结论。 */
  headline: string
  /** 展开说明：口径与下一步该做什么。 */
  detail: string
  counts: QualityCounts
  /** 任务维度（正交）：是否正常跑完。 */
  taskComplete: boolean
}

function countCitations(citations: CitationResult[]): QualityCounts {
  const counts: QualityCounts = {
    total: citations.length,
    verified: 0,
    relaxed: 0,
    refuted: 0,
    unknown: 0,
    existence: 0,
  }
  for (const citation of citations) {
    switch (citationVerdict(citation)) {
      case 'verified':
        counts.verified += 1
        break
      case 'relaxed':
        counts.relaxed += 1
        break
      case 'refuted':
        counts.refuted += 1
        break
      case 'unknown':
        counts.unknown += 1
        break
    }
    if (citation.existence) counts.existence += 1
  }
  return counts
}

/** 任务维度：是否**正常跑完**。取消 / 超时 / 失败都不算跑完，与质量高低无关。 */
export function isTaskComplete(finished: RunFinishedEvent | undefined): boolean {
  if (!finished) return false
  return !finished.cancelled && finished.stop_reason === 'completed'
}

const KIND_LABELS: Record<QualityKind, string> = {
  'under-review': '输出待审核',
  refuted: '确证失败',
  incomplete: '校验未完成',
  relaxed: '引用宽松通过',
  passed: '引用通过',
  'no-citations': '无引用可核',
  pending: '尚未校验',
}

const KIND_TONES: Record<QualityKind, QualityTone> = {
  'under-review': 'warn',
  refuted: 'bad',
  incomplete: 'warn',
  relaxed: 'warn',
  passed: 'ok',
  'no-citations': 'neutral',
  pending: 'neutral',
}

function verdictOf(kind: QualityKind, counts: QualityCounts, taskComplete: boolean): QualityVerdict {
  return {
    kind,
    label: KIND_LABELS[kind],
    tone: KIND_TONES[kind],
    ...describe(kind, counts),
    counts,
    taskComplete,
  }
}

function describe(kind: QualityKind, counts: QualityCounts): { headline: string; detail: string } {
  const { total, verified, existence, refuted, unknown, relaxed } = counts
  switch (kind) {
    case 'under-review':
      return {
        headline: '输出待人工复核',
        detail: '报告命中内容安全预检，正文已脱敏：复核通过或申诉处理前不开放查看与导出。此时不评价引用质量。',
      }
    case 'refuted':
      return {
        headline: `确证失败 ${refuted} 条`,
        detail:
          `严格口径 ${verified}/${existence} 条通过（存在性 ${existence}/${total}）。` +
          (unknown > 0 ? `另有 ${unknown} 条校验未完成。` : '') +
          '「确证失败」是查过且结论不成立，与「未校验」不同质 —— 见报告末尾未通过附录。',
      }
    case 'incomplete':
      return {
        headline: `校验未完成 ${unknown} 条`,
        detail:
          `${unknown}/${total} 条引用未得出核验结论（校验阶段未完成，常见于预算在写作后被耗尽）。` +
          '**这不等于通过**：这些引用既未确认成立、也未确认不成立。可重跑或改用更高预算档位复核。',
      }
    case 'relaxed':
      return {
        headline: `宽松口径通过，严格口径 ${verified}/${existence}`,
        detail:
          `严格口径要求「存在且忠实」；另 ${relaxed} 条为「存在且（忠实或多源印证）」。` +
          '宽松口径只作解释性附注，不等于严格通过。',
      }
    case 'passed':
      return {
        headline: `全部通过（${verified}/${total}）`,
        detail: `严格口径 ${verified}/${existence} 条通过，存在性 ${existence}/${total} 条通过。未发现确证失败或未完成校验。`,
      }
    case 'no-citations':
      return {
        headline: '本轮没有可校验引用',
        detail: '报告可能在引用校验前停止，或本轮未生成可校验引用。此时无法给出质量结论。',
      }
    case 'pending':
    default:
      return {
        headline: '质量结论尚未产生',
        detail: '引用校验在报告生成后进行；运行结束前不给出质量判断。',
      }
  }
}

/**
 * 质量维度判定。严重度优先级：待审核 > 确证失败 > 校验未完成 > 宽松 > 通过。
 *
 * 为什么「确证失败」排在「校验未完成」之前：前者是**已查实的坏消息**，后者是**未知**。
 * 未知需要复核，但已查实的不成立更需要用户先看到。两者计数都完整保留在 `counts` 里，
 * 界面可以同时呈现，不做信息丢失。
 */
export function qualityVerdict(
  result: ResearchResult | null | undefined,
  finished: RunFinishedEvent | undefined,
): QualityVerdict {
  const counts = countCitations(result?.citations ?? [])
  const taskComplete = isTaskComplete(finished)

  if (finished?.output_under_review) return verdictOf('under-review', counts, taskComplete)
  if (!result) return verdictOf('pending', counts, taskComplete)
  if (counts.total === 0) return verdictOf('no-citations', counts, taskComplete)
  if (counts.refuted > 0) return verdictOf('refuted', counts, taskComplete)
  if (counts.unknown > 0) return verdictOf('incomplete', counts, taskComplete)
  if (counts.verified === counts.total) return verdictOf('passed', counts, taskComplete)
  return verdictOf('relaxed', counts, taskComplete)
}
