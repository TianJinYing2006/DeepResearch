import { useEffect, useRef } from 'react'

const INK = [23, 27, 25] as const
const BLUE = [30, 90, 216] as const

function hash2(x: number, y: number): number {
  // ⚠️ 必须用 Math.imul：普通 `*` 乘积会超出 2^53，低位被舍入 → 哈希退化（曾导致全图低于阈值）
  let h = Math.imul(x, 374761393) + Math.imul(y, 668265263)
  h = Math.imul(h ^ (h >>> 13), 1274126177)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295
}

function valueNoise(x: number, y: number): number {
  const xi = Math.floor(x)
  const yi = Math.floor(y)
  const xf = x - xi
  const yf = y - yi
  const u = xf * xf * (3 - 2 * xf)
  const v = yf * yf * (3 - 2 * yf)
  const a = hash2(xi, yi)
  const b = hash2(xi + 1, yi)
  const c = hash2(xi, yi + 1)
  const d = hash2(xi + 1, yi + 1)
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v
}

function fbm(x: number, y: number, octaves: number): number {
  let sum = 0
  let amp = 0.5
  let freq = 1
  let norm = 0
  for (let i = 0; i < octaves; i += 1) {
    sum += amp * valueNoise(x * freq, y * freq)
    norm += amp
    freq *= 2
    amp *= 0.5
  }
  return sum / norm
}

/** 墨板流沙（参考 LinkResume /login 黑板的 paper shader，自研实现）：
 *
 * 域扭曲 fbm 噪声场 → 阈值化为蓝色流带（溶解边缘带白噪），黑域撒白尘；
 * 低分辨率噪声缓冲 + `image-rendering: pixelated` 放大 → 沙粒质感且开销可控。
 * `prefers-reduced-motion` 下只画一帧静态画面。 */
export default function InkFlowCanvas({ className = '' }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = ref.current
    const parent = canvas?.parentElement
    if (!canvas || !parent) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    let raf = 0
    let last = 0
    const frameMs = 1000 / 24
    let width = 0
    let height = 0
    let image: ImageData | null = null

    const resize = () => {
      const rect = parent.getBoundingClientRect()
      width = Math.max(80, Math.round(rect.width * 0.7))
      height = Math.max(80, Math.round(rect.height * 0.7))
      canvas.width = width
      canvas.height = height
      image = new ImageData(width, height)
    }

    const draw = (time: number) => {
      if (!image) return
      const t = time / 1000
      const data = image.data
      for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
          const nx = x * 0.016
          const ny = y * 0.016
          const warp = fbm(nx * 0.55 + t * 0.02, ny * 0.55 - t * 0.014, 2)
          const field = fbm(nx + warp * 2.2 + t * 0.025, ny + warp * 1.6 - t * 0.018, 3)
          const jitter = (hash2(x * 13, y * 17) - 0.5) * 0.1
          const v = field + jitter
          const i = (y * width + x) * 4
          if (v > 0.58) {
            data[i] = BLUE[0]
            data[i + 1] = BLUE[1]
            data[i + 2] = BLUE[2]
            data[i + 3] = 255
          } else if (v > 0.5) {
            // 溶解边缘：蓝色渐入 + 细密白沙
            const froth = hash2(x * 7 + 3, y * 11 + 5)
            if (froth > 0.55) {
              data[i] = 255
              data[i + 1] = 255
              data[i + 2] = 255
              data[i + 3] = 225
            } else {
              const depth = (v - 0.5) / 0.08
              data[i] = BLUE[0]
              data[i + 1] = BLUE[1]
              data[i + 2] = BLUE[2]
              data[i + 3] = Math.round(90 + depth * 150)
            }
          } else {
            data[i] = INK[0]
            data[i + 1] = INK[1]
            data[i + 2] = INK[2]
            data[i + 3] = 255
            if (hash2(x * 5 + 1, y * 5 + 9) > 0.997) {
              data[i] = 255
              data[i + 1] = 255
              data[i + 2] = 255
              data[i + 3] = 140
            }
          }
        }
      }
      canvas.getContext('2d')?.putImageData(image, 0, 0)
    }

    const loop = (time: number) => {
      raf = requestAnimationFrame(loop)
      if (time - last < frameMs) return
      last = time
      draw(time)
    }

    resize()
    if (reduced) {
      draw(0)
      return
    }
    raf = requestAnimationFrame(loop)
    const onResize = () => resize()
    window.addEventListener('resize', onResize)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
    }
  }, [])

  return (
    <canvas ref={ref} aria-hidden="true"
            className={`pointer-events-none [image-rendering:pixelated] ${className}`} />
  )
}
