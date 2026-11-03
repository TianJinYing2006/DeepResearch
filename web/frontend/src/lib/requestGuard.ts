/** 请求代次守卫（审计 #341 / 已登记 U26）：丢弃过期响应。
 *
 * 为什么抽成与 React 无关的纯函数：HistoryPanel 的竞态（快速切筛选 / 输入搜索 /
 * 连点「加载更多」时迟到响应覆盖新结果）无法在仓库现有 vitest（node 环境、无
 * jsdom）里用组件测试覆盖，因此把「代次」抽成小对象，单测直接验证
 * 「旧 token 在 next() 之后失效」这一行为。
 */
export type RequestGuard = {
  /** 开始新请求：返回本次请求 token，并使此前所有 token 失效。 */
  next: () => number
  /** 该 token 是否仍是最近一次请求（false ⇒ 结果必须丢弃）。 */
  isCurrent: (token: number) => boolean
  /** 使所有在途 token 失效（不发起新请求时使用，如卸载 / 外部重置）。 */
  invalidate: () => void
}

export function createRequestGuard(): RequestGuard {
  let generation = 0
  return {
    next: () => {
      generation += 1
      return generation
    },
    isCurrent: (token) => token === generation,
    invalidate: () => {
      generation += 1
    },
  }
}
