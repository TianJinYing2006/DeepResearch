import { cloneElement, Fragment, isValidElement, useDeferredValue, useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** 单段渲染的字符上限。

 依据：报告是服务端生成的一次性 Markdown（实测数千~数万字），`react-markdown`
 需要整篇 parse + 建 AST 再渲染，**同步**占用主线程。一次性喂进去几万字时，
 页面会在这段时间里完全无响应（点不动停止按钮）。分段 + `useDeferredValue`
 让首屏先出、后续按需追加，把一次长任务拆成几次短任务。 */
const CHUNK_CHARS = 20_000

/** 在**安全边界**处切分：优先空行，且绝不切在围栏代码块内部。

 直接按字符数硬切会把 ``` 代码块拦腰截断 ⇒ 后半段被当成普通段落渲染出来，
 报告看起来「格式崩了」。这里扫一遍行，跟踪围栏开关，只在围栏外取最后一个
 不超过 limit 的空行位置。 */
function sliceAtSafeBoundary(text: string, limit: number): number {
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

/** 取 React 节点的纯文本（用于「无引用段落」示警判定）。 */
function nodeText(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join('')
  if (typeof node === 'object' && 'props' in node) {
    return nodeText((node as { props?: { children?: ReactNode } }).props?.children)
  }
  return ''
}

type ReportViewProps = {
  report: string
  /** 提供回调时，把正文中的引用编号渲染成可定位到证据栏的按钮。 */
  onCitationClick?: (citationNumber: number) => void
  citationCount?: number
}

function citationButton(number: number, label: string, onCitationClick: (citationNumber: number) => void, keySuffix: string) {
  return (
    <button
      key={`citation-ref-${number}-${keySuffix}`}
      type="button"
      className="inline rounded px-1 text-stamp-blue underline decoration-stamp-blue/30 underline-offset-2 hover:bg-stamp-blue/10 focus-visible:ring-2 focus-visible:ring-stamp-blue/30"
      aria-label={`查看引用 ${number}`}
      data-testid={`citation-ref-${number}`}
      onClick={() => onCitationClick(number)}
    >
      {label}
    </button>
  )
}

/** 把报告正文里的引用标记变成可聚焦的证据入口，代码块和链接内容保持原样。 */
function decorateCitationNodes(
  node: ReactNode,
  onCitationClick: ((citationNumber: number) => void) | undefined,
  citationCount: number | undefined,
): ReactNode {
  if (!onCitationClick) return node
  if (typeof node === 'string') {
    const marker = /\[(来源:\s*)?(\d+(?:\s*[,，]\s*\d+)*)\]/g
    const parts: ReactNode[] = []
    let cursor = 0
    let match: RegExpExecArray | null
    let partIndex = 0
    while ((match = marker.exec(node)) !== null) {
      const currentMatch = match
      const numbers = currentMatch[2].split(/[,，]/).map((value) => Number(value.trim()))
      const valid = numbers.every((number) => number > 0 && (citationCount === undefined || number <= citationCount))
      if (!valid) continue
      if (currentMatch.index > cursor) parts.push(node.slice(cursor, currentMatch.index))
      if (numbers.length === 1) {
        parts.push(citationButton(numbers[0], currentMatch[0], onCitationClick, String(currentMatch.index)))
      } else {
        parts.push(
          <span key={`citation-group-${currentMatch.index}-${partIndex}`}>
            [{currentMatch[1] ?? ''}
            {numbers.map((number, index) => (
              <Fragment key={`${number}-${index}`}>
                {index > 0 ? ', ' : ''}
                {citationButton(number, String(number), onCitationClick, `${currentMatch.index}-${index}`)}
              </Fragment>
            ))}]
          </span>,
        )
      }
      cursor = currentMatch.index + currentMatch[0].length
      partIndex += 1
    }
    if (parts.length === 0) return node
    if (cursor < node.length) parts.push(node.slice(cursor))
    return parts
  }
  if (Array.isArray(node)) {
    return node.map((child, index) => (
      <Fragment key={index}>{decorateCitationNodes(child, onCitationClick, citationCount)}</Fragment>
    ))
  }
  if (isValidElement<{ children?: ReactNode }>(node)) {
    const elementType = typeof node.type === 'string' ? node.type : ''
    if (elementType === 'code' || elementType === 'pre' || elementType === 'a' || elementType === 'button') return node
    return cloneElement(node, undefined, decorateCitationNodes(node.props.children, onCitationClick, citationCount))
  }
  return node
}

export function ReportView({ report, onCitationClick, citationCount }: ReportViewProps) {
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
            // 方向 B 签名：无引用支撑的实质段落用点状下划线示警（不改写正文、不伪造结论）
            p: ({ children }: { children?: ReactNode }) => {
              const text = nodeText(children).trim()
              const unsourced = text.length >= 30 && !text.includes('[来源:')
              return <p className={unsourced ? 'claim-unsourced' : undefined}>{decorateCitationNodes(children, onCitationClick, citationCount)}</p>
            },
            li: ({ children }: { children?: ReactNode }) => (
              <li>{decorateCitationNodes(children, onCitationClick, citationCount)}</li>
            ),
            blockquote: ({ children }: { children?: ReactNode }) => (
              <blockquote>{decorateCitationNodes(children, onCitationClick, citationCount)}</blockquote>
            ),
          }}
        >
          {deferred}
        </ReactMarkdown>
      </article>
      {remaining > 0 && (
        <div className="mx-auto mt-6 max-w-4xl rounded-lg border border-rule bg-rule/30 p-4 text-center">
          <p className="text-xs leading-5 text-ink-muted">
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
