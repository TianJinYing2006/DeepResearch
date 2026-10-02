/**
 * 下载文件名工具（需求 17 / #77）。
 *
 * 走后端导出后，文件名由 `Content-Disposition` 决定（同源 fetch 可直接读该头）。
 * RFC 6266：`filename*`（UTF-8 percent-encoded）优先；`filename`（ASCII 回退）兜底。
 */
export function filenameFromDisposition(
  header: string | null | undefined,
): string | null {
  if (!header) return null
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      return null
    }
  }
  const quoted = /filename\s*=\s*"([^"]+)"/i.exec(header)
  if (quoted) return quoted[1].trim()
  const bare = /filename\s*=\s*([^;]+)/i.exec(header)
  return bare ? bare[1].trim() : null
}
