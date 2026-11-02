import type { DoubtCategory, ReviewItem } from '../api/annotationReviewApi'
import type { AnnotationObject } from '../types/annotation'
import type { Box } from './geometry'

export const reviewLabels: Record<DoubtCategory, string> = {
  missing: '可能漏标', association: '可能重复或跟错', exit: '出画尾帧', position: '框偏离头部', size: '框大小异常', static: '静止目标', check: '自动检查失败',
}
export function reviewCategory(item: ReviewItem): DoubtCategory {
  if (item.category && item.category in reviewLabels) return item.category
  // Older responses may have long messages. Never render diagnostic prose as a label.
  if (item.labels?.includes('可能漏标')) return 'missing'
  if (item.labels?.includes('可能重复或跟错')) return 'association'
  if (item.labels?.some(x => x === '框偏离头部' || x === '检测头部与框不符' || x === '头部边界存疑')) return 'position'
  return 'check'
}
export function reviewLocationText(item: ReviewItem) {
  const sequence = item.index && item.countInFrame ? `${item.index}/${item.countInFrame}` : ''
  const span = item.endFrame - item.startFrame + 1
  const observations = item.observations?.length ?? span
  const extent = item.endFrame > item.startFrame
    ? observations < span ? `${observations} 个观测帧` : `连续 ${span} 帧`
    : ''
  return [item.region || '位置待定位', sequence, extent].filter(Boolean).join(' · ')
}
export function reviewTargetZoom(zoom: number, stageHeight: number, boxHeight: number, userZoomed: boolean) {
  const pixels = stageHeight * boxHeight / 100
  if (userZoomed || !Number.isFinite(pixels) || pixels <= 0 || pixels >= 40) return zoom
  return Math.max(zoom, Math.min(10, zoom * 80 / pixels))
}
// Follow-up re-checks highlight the corrected objects like an object doubt.
const objectItem = (item: ReviewItem) => item.kind === 'doubt' || item.kind === 'followup'
export function reviewTarget(item: ReviewItem, frame: number, objects: AnnotationObject[], width: number, height: number): Box | null {
  if (!objectItem(item) || frame < item.startFrame || frame > item.endFrame) return null
  const presentation = reviewPresentation(item, frame, objects, width, height)
  if (!['missing', 'static'].includes(reviewCategory(item))) return objects.find(o => o.objectId === presentation.objectIds[0])?.bbox ?? null
  const observation = item.observations?.find(o => o.frameIndex === frame)
  const box = observation?.candidates[0] ?? (frame === item.frame ? item.anchor ?? item.candidates?.[0] : undefined)
  if (!box || box.length !== 4 || !box.every(Number.isFinite) || box[2] <= box[0] || box[3] <= box[1] || width <= 0 || height <= 0) return null
  return { x: 100*box[0]/width, y: 100*box[1]/height, width: 100*(box[2]-box[0])/width, height: 100*(box[3]-box[1])/height }
}
export function reviewPresentation(item: ReviewItem | null, frame: number, objects: AnnotationObject[], width: number, height: number, stageWidth = width) {
  const empty = { objectIds: [] as number[], circles: [] as { key: string; left: string; top: string; size: number; label: string }[] }
  if (!item || !objectItem(item) || frame < item.startFrame || frame > item.endFrame) return empty
  const observation = item.observations?.find(o => o.frameIndex === frame)
  // With per-frame observations, absence means no finding here, not the span-wide union.
  const ids = observation?.objectIds ?? (item.observations ? [] : item.objectIds ?? (item.objectId != null ? [item.objectId] : []))
  const objectIds = [...new Set(ids)].filter(id => objects.some(o => o.objectId === id && o.source !== 'manual')).slice(0, 3)
  if (!['missing', 'static'].includes(reviewCategory(item)) || width <= 0 || height <= 0) return { ...empty, objectIds }
  const candidates = observation?.candidates ?? (frame === item.frame ? item.candidates ?? (item.anchor ? [item.anchor] : item.bbox ? [item.bbox] : []) : [])
  const seen = new Set<string>()
  const circles = candidates.filter(b => {
    if (b.length !== 4 || !b.every(Number.isFinite) || b[2] <= b[0] || b[3] <= b[1]) return false
    const key = b.join(','); if (seen.has(key)) return false; seen.add(key); return true
  }).slice(0, 3).map((b, i) => ({ key: `${item.id}:${frame}:candidate-${i}`, left: `${Math.max(0, Math.min(100, 50 * (b[0] + b[2]) / width))}%`, top: `${Math.max(0, Math.min(100, 50 * (b[1] + b[3]) / height))}%`, size: Math.max(28,(b[2]-b[0])*stageWidth/width), label: String(item.index ?? i+1) }))
  return { objectIds, circles }
}
