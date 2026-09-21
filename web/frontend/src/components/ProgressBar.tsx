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
                    ? 'bg-emerald-400'
                    : active
                      ? cancelling
                        ? 'bg-amber-300'
                        : 'bg-cyan-300'
                      : 'bg-white/10'
                }`}
              />
              <div
                className={`truncate text-center text-[11px] font-medium ${
                  completed || active ? 'text-slate-200' : 'text-slate-600'
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
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">当前阶段</p>
          <p className="mt-1 text-xl font-semibold text-white">
            {cancelling ? '正在安全停止' : progress.stageName}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-2xl font-semibold tabular-nums text-emerald-300">{percent}%</p>
          <p className="mt-1 text-xs text-slate-500">{etaLabel(progress, cancelling)}</p>
        </div>
      </div>

      <div className="mt-4 h-2 overflow-hidden rounded-full bg-black/25" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            cancelling
              ? 'bg-gradient-to-r from-amber-400 to-orange-300'
              : 'bg-gradient-to-r from-emerald-500 via-emerald-300 to-cyan-300'
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>

      {progress.stageName === '检索' && (
        <p className="mt-3 text-xs text-slate-500">
          检索跳数进度 <span className="font-mono text-slate-300">{progress.innerPercent}%</span>；总流程百分比只使用可确认的数据。
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
  return `约剩 ${formatEta(progress.etaSeconds)}`
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder === 0 ? `${minutes} 分钟` : `${minutes} 分 ${remainder} 秒`
}
