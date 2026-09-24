import { useDeferredValue, useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** 单段渲染的字符上限。

 依据：报告是服务端生成的一次性 Markdown（实测数千~数万字），`react-markdown`
 需要整篇 parse + 建 AST 再渲染，**同步**占用主线程。一次性喂进去几万字时，
 页面会在这段时间里完全无响应（点不动停止按钮）。分段 + `useDeferredValue`
 让首屏先出、后续按需追加，把一次长任务拆成几次短任务。 */
export const CHUNK_CHARS = 20_000

/** 在**安全边界**处切分：优先空行，且绝不切在围栏代码块内部。

 直接按字符数硬切会把 ``` 代码块拦腰截断 ⇒ 后半段被当成普通段落渲染出来，
 报告看起来「格式崩了」。这里扫一遍行，跟踪围栏开关，只在围栏外取最后一个
 不超过 limit 的空行位置。 */
export function sliceAtSafeBoundary(text: string, limit: number): number {
  if (text.length <= limit) return text.length
  let inFence = false
  let blankCut = 0
  let lineCut = 0
  let consumed = 0
  for (const line of text.split('\n')) {
    const lineEnd = consumed + line.length + 1
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence
    if (lineEnd <= limit) {
      lineCut = Math.min(lineEnd, text.length)
      if (!inFence && line.trim() === '') blankCut = lineCut
    } else {
      break
    }
    consumed = lineEnd
  }
  if (blankCut > 0) return blankCut
  if (lineCut > 0) return lineCut
  return Math.min(limit, text.length)
}

export function ReportView({ report }: { report: string }) {
  const [chunks, setChunks] = useState(1)

  // 换了一份报告（重试 / 新研究）⇒ 分段必须重置，否则新报告会沿用旧的展开量
  useEffect(() => setChunks(1), [report])

  const { visible, remaining } = useMemo(() => {
    if (report.length <= CHUNK_CHARS) return { visible: report, remaining: 0 }
    const cut = sliceAtSafeBoundary(report, chunks * CHUNK_CHARS)
    return { visible: report.slice(0, cut), remaining: report.length - cut }
  }, [report, chunks])

  // 让 React 把这段渲染标为「可延后」：期间其它交互（停止按钮等）仍能响应
  const deferred = useDeferredValue(visible)

  return (
    <div>
      <article className="report-prose mx-auto max-w-4xl">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            // 宽表在窄屏上会顶破布局 ⇒ 包一层可横向滚动的容器（不裁内容）
            table: ({ children }: { children?: ReactNode }) => (
              <div className="my-5 overflow-x-auto">
                <table>{children}</table>
              </div>
            ),
          }}
        >
          {deferred}
        </ReactMarkdown>
      </article>
      {remaining > 0 && (
        <div className="mx-auto mt-6 max-w-4xl rounded-xl border border-white/10 bg-white/[0.03] p-4 text-center">
          <p className="text-xs leading-5 text-slate-500">
            为避免一次性解析超长 Markdown 卡住页面，已按段落分段渲染（已显示 {visible.length.toLocaleString('zh-CN')} / {report.length.toLocaleString('zh-CN')} 字符）。
          </p>
          <button
            className="secondary-button mt-3 !px-3 !py-2"
            type="button"
            onClick={() => setChunks((value) => value + 1)}
          >
            继续渲染剩余 {remaining.toLocaleString('zh-CN')} 字符
          </button>
        </div>
      )}
    </div>
  )
}
