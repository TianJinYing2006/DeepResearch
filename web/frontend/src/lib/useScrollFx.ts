import { useEffect, useState, type RefObject } from 'react'

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** 观察 scope 内全部 `.reveal`，进入视口后挂 `is-visible`（一次性；`--reveal-delay` 控制错峰）。
 *
 * `rootRef` 为滚动容器（落地页是独立 overlay 滚动），缺省视口。 */
export function useRevealAll(
  scopeRef: RefObject<HTMLElement | null>,
  rootRef?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const scope = scopeRef.current
    if (!scope) return
    const nodes = Array.from(scope.querySelectorAll<HTMLElement>('.reveal'))
    if (prefersReducedMotion()) {
      nodes.forEach((node) => node.classList.add('is-visible'))
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible')
            observer.unobserve(entry.target)
          }
        })
      },
      { root: rootRef?.current ?? null, rootMargin: '0px 0px -10% 0px', threshold: 0.12 },
    )
    nodes.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [scopeRef, rootRef])
}

export type ScrollState = {
  /** 滚动容器纵向偏移（px）。 */
  y: number
  /** 总进度 0–1。 */
  progress: number
}

/** 监听滚动容器（缺省 window）的纵向位置与总进度（rAF 节流）。 */
export function useScrollState(scrollerRef: RefObject<HTMLElement | null>): ScrollState {
  const [state, setState] = useState<ScrollState>({ y: 0, progress: 0 })
  useEffect(() => {
    const el = scrollerRef.current
    let raf = 0
    const compute = () => {
      if (el) {
        const max = el.scrollHeight - el.clientHeight
        setState({ y: el.scrollTop, progress: max > 0 ? Math.min(1, el.scrollTop / max) : 0 })
      } else {
        const max = document.documentElement.scrollHeight - window.innerHeight
        setState({ y: window.scrollY, progress: max > 0 ? Math.min(1, window.scrollY / max) : 0 })
      }
    }
    const onScroll = () => {
      if (!raf) {
        raf = requestAnimationFrame(() => {
          raf = 0
          compute()
        })
      }
    }
    const target: HTMLElement | Window = el ?? window
    target.addEventListener('scroll', onScroll, { passive: true })
    compute()
    return () => {
      target.removeEventListener('scroll', onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [scrollerRef])
  return state
}

/** 当前是否 reduced-motion（组件内需要条件渲染样式时使用）。 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion)
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return reduced
}

/** 打字机：逐字输出（reduced-motion 直接给全量文本）。 */
export function useTypewriter(text: string, speedMs = 90, startDelayMs = 250): string {
  const reduced = usePrefersReducedMotion()
  const [count, setCount] = useState(() => (prefersReducedMotion() ? text.length : 0))
  useEffect(() => {
    if (reduced) {
      setCount(text.length)
      return
    }
    setCount(0)
    let index = 0
    let timer = 0
    const start = window.setTimeout(function tick() {
      index += 1
      setCount(index)
      if (index < text.length) timer = window.setTimeout(tick, speedMs)
    }, startDelayMs)
    return () => {
      window.clearTimeout(start)
      window.clearTimeout(timer)
    }
  }, [text, speedMs, startDelayMs, reduced])
  return text.slice(0, count)
}
