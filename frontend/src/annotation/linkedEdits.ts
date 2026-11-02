// 改帧联动（决策 0002）：同一对象的错误往往连续几帧出现。
// 纯计算：找出与当前框差别明显的相邻帧，并给出可批量修改的连续帧段。
import type { Box } from './geometry'

export interface TrackBox { frameIndex: number; bbox: Box; manual: boolean }
export const LINKED_SPAN = 5
export const LINKED_SPAN_MAX = 30
// 尺寸（对角线）相差超过 30%，或中心偏移超过半个头宽，视为“差别明显”。
export const LINKED_SIZE_DIFF = .3
export const LINKED_CENTER_DIFF = .5

// Local copy: this module is also compiled alone for node tests (type-only imports).
const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v))
const diag = (b: Box, w: number, h: number) => Math.hypot(b.width * w / 100, b.height * h / 100)
const centre = (b: Box, w: number, h: number) => [(b.x + b.width / 2) * w / 100, (b.y + b.height / 2) * h / 100]

export function differs(a: Box, ref: Box, mediaWidth: number, mediaHeight: number) {
  const size = diag(a, mediaWidth, mediaHeight) / Math.max(1e-9, diag(ref, mediaWidth, mediaHeight))
  const [ax, ay] = centre(a, mediaWidth, mediaHeight), [rx, ry] = centre(ref, mediaWidth, mediaHeight)
  const headWidth = ref.width * mediaWidth / 100
  return Math.abs(size - 1) > LINKED_SIZE_DIFF || Math.hypot(ax - rx, ay - ry) > LINKED_CENTER_DIFF * headWidth
}

/** 当前帧前后 span 帧内、与当前框差别明显的帧（人工框不算，人工可能有意留边）。 */
export function neighbourSuspects(track: TrackBox[], frame: number, span: number, mediaWidth: number, mediaHeight: number) {
  const ref = track.find(t => t.frameIndex === frame)
  if (!ref || mediaWidth <= 0 || mediaHeight <= 0) return []
  return track.filter(t => t.frameIndex !== frame && !t.manual && Math.abs(t.frameIndex - frame) <= span && differs(t.bbox, ref.bbox, mediaWidth, mediaHeight))
    .map(t => t.frameIndex).sort((a, b) => a - b)
}

/** 当前帧两侧连续有框的帧段（不含当前帧），最多 span 帧；遇到缺框或人工框即停。 */
export function editableRange(track: TrackBox[], frame: number, span: number) {
  const by = new Map(track.map(t => [t.frameIndex, t]))
  const walk = (step: number) => {
    const out: number[] = []
    for (let fi = frame + step; Math.abs(fi - frame) <= span; fi += step) {
      const t = by.get(fi)
      if (!t || t.manual) break
      out.push(fi)
    }
    return out
  }
  return { before: walk(-1).reverse(), after: walk(1) }
}

/** 从当前帧起到连续轨迹结束（之后第一处缺框为止）的帧。 */
export function tailFrames(track: TrackBox[], frame: number) {
  const frames = new Set(track.map(t => t.frameIndex))
  const out: number[] = []
  for (let fi = frame; frames.has(fi); fi++) out.push(fi)
  return out
}

/** 保持中心，只换宽高；超出画面时整体平移回画面内。 */
export function withSize(box: Box, width: number, height: number): Box {
  width = Math.min(100, width); height = Math.min(100, height)
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2
  return { x: clamp(cx - width / 2, 0, 100 - width), y: clamp(cy - height / 2, 0, 100 - height), width, height }
}

/** 逐帧补框时，检测结果必须仍是同一个头：中心不超过上一框一个对角线，尺寸在 0.5–2 倍之间。 */
export function plausibleNext(prev: Box, next: Box, mediaWidth: number, mediaHeight: number) {
  const d = diag(prev, mediaWidth, mediaHeight), ratio = diag(next, mediaWidth, mediaHeight) / Math.max(1e-9, d)
  const [px, py] = centre(prev, mediaWidth, mediaHeight), [nx, ny] = centre(next, mediaWidth, mediaHeight)
  return ratio >= .5 && ratio <= 2 && Math.hypot(nx - px, ny - py) <= d
}
