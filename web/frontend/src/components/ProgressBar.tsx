import { formatDuration } from '../lib/format'
import type { Progress } from '../lib/progress'
import { STAGES } from '../lib/progress'

interface ProgressBarProps {
  progress: Progress
  cancelling: boolean
}

export function ProgressBar({ progress, cancelling }: ProgressBarProps) {
  const percent = Math.max(0, Math.min(progress.percent, 100))
  const finished = progress.stageIndex >= STAGES.length

  return (
    <div>
      <div className="grid grid-cols-5 gap-1.5" aria-label="研究阶段">
        {STAGES.map((stage, index) => {
          const completed = finished || index < progress.stageIndex - 1
          const active = !finished && index === progress.stageIndex - 1
          return (
            <div key={stage.name} className="min-w-0">
              <div
                className={`mb-2 h-1.5 rounded-full transition-colors ${
                  completed
                    ? 'bg-stamp-blue/40'
                    : active
                      ? cancelling
                        ? 'bg-stamp-amber'
                        : 'bg-stamp-blue'
                      : 'bg-rule'
                }`}
              />
              <div
                className={`truncate text-center text-[11px] font-medium ${
                  completed || active ? 'text-ink' : 'text-ink-muted'
                }`}
              >
                {stage.name}
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-6 flex items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold text-ink-muted">当前阶段</p>
          <p className="mt-1 text-xl font-semibold text-ink">
            {cancelling ? '正在安全停止' : progress.stageName}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-2xl font-semibold tabular-nums text-stamp-blue">{percent}%</p>
          <p className="mt-1 text-xs text-ink-muted">{etaLabel(progress, cancelling)}</p>
        </div>
      </div>

      <div className="relative mt-4 h-2 overflow-hidden rounded-full bg-rule/40" role="progressbar"
           aria-label="研究进度" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}
           aria-valuetext={`${progress.stageName} ${percent}%`}>
        {/* R5（审计 U61）：只动画合成属性 transform（scaleX），不再动画 layout 的 width */}
        <div
          className={`h-full w-full origin-left rounded-full transition-transform duration-500 ${
            cancelling
              ? 'bg-stamp-amber'
              : 'bg-stamp-blue'
          }`}
          style={{ transform: `scaleX(${percent / 100})` }}
        />
        {/* 从左往右循环的高光渐变（shimmer）：只在**运行中**出现，表示「还在推进」。
            ⚠️ 它纯粹是动效，**不改变 percent** —— 总进度仍只用可确认的数据推导，
            不因为有了动画就假装知道剩余比例。完成后停止，避免闪烁干扰读数。 */}
        {!cancelling && percent < 100 && (
          <span className="pointer-events-none absolute inset-0 overflow-hidden rounded-full">
            <span className="absolute inset-y-0 left-0 w-1/3 animate-shimmer bg-gradient-to-r from-transparent via-white/45 to-transparent" />
          </span>
        )}
      </div>

      {progress.stageName === '检索' && (
        <p className="mt-3 text-xs text-ink-muted">
          检索跳数进度 <span className="font-mono text-ink">{progress.innerPercent}%</span>；总流程百分比只使用可确认的数据。
        </p>
      )}
    </div>
  )
}

function etaLabel(progress: Progress, cancelling: boolean): string {
  if (cancelling) return '当前节点完成后停止'
  if (progress.stageIndex >= STAGES.length) return '已完成'
  if (progress.etaSeconds === null) return progress.stageIndex === 0 ? '等待首个节点' : '剩余时间估算中'
  if (progress.etaSeconds <= 0) return '即将完成'
  return `约剩 ${formatDuration(progress.etaSeconds * 1000)}`
}
