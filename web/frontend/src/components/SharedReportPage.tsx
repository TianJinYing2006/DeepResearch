import { useEffect, useState } from 'react'
import { readErrorMessage } from '../lib/api'
import { ReportView } from './ReportView'

type Props = { token: string }

type SharedReport = { topic: string; markdown: string; permanent: boolean; expires_at: string | null }

/** 需求 26：免登录只读分享页（/s/<token>）——自包含、无第三方资源、无操作按钮。 */
export default function SharedReportPage({ token }: Props) {
  const [report, setReport] = useState<SharedReport | null>(null)
  const [invalid, setInvalid] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch(`/api/share/${token}`)
      .then(async (response) => {
        if (cancelled) return
        if (response.status === 404) {
          setInvalid(true)
          return
        }
        if (!response.ok) {
          setError(await readErrorMessage(response))
          return
        }
        setReport((await response.json()) as SharedReport)
      })
      .catch(() => {
        if (!cancelled) setError('网络错误，请重试')
      })
    return () => { cancelled = true }
  }, [token])

  return (
    <div className="min-h-dvh bg-paper">
      <header className="border-b border-rule/70 bg-paper">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-5 py-4 text-[11px] text-ink-muted sm:px-8">
          <span className="font-semibold text-ink">DeepResearch · 只读分享</span>
          <span>可审计研究工作台</span>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-5 py-8 sm:px-8">
        {invalid && (
          <section className="surface-card p-8 text-center" data-testid="share-invalid">
            <p className="text-lg font-semibold text-ink">链接无效或已过期</p>
            <p className="mt-2 text-sm text-ink-muted">请联系分享者重新生成链接。</p>
          </section>
        )}
        {error && (
          <section className="surface-card p-8 text-center" data-testid="share-page-error">
            <p className="text-sm text-stamp-red" role="alert">{error}</p>
          </section>
        )}
        {!invalid && !error && !report && (
          <section className="surface-card p-8 text-center text-sm text-ink-muted">加载中…</section>
        )}
        {report && (
          <>
            <h1 className="text-xl font-semibold tracking-tight text-balance text-ink"
                data-testid="share-topic">{report.topic}</h1>
            <p className="mt-1.5 text-[11px] text-ink-muted">
              {report.permanent
                ? '永久有效 · 只读分享'
                : `有效期至 ${report.expires_at?.slice(0, 10) ?? '—'} · 只读分享`}
            </p>
            <article className="surface-card mt-5 px-5 py-6 sm:px-8 sm:py-8">
              <ReportView report={report.markdown} />
            </article>
          </>
        )}
      </main>
      <footer className="border-t border-rule/70 px-5 py-6 text-center text-[11px] text-ink-muted">
        由 DeepResearch 生成 · 本页为只读快照，内容以分享者提供的报告为准
      </footer>
    </div>
  )
}
