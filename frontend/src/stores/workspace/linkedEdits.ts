// 改帧联动（决策 0002）：改了一帧后，把同一对象相邻帧的同类问题一起处理。
// 区间操作改的是普通工作区标注，照常保存；本帧撤销只管单帧，所以另留一步“撤销联动操作”。
import { computed, ref } from 'vue'
import type { AnnotationObject } from '../../types/annotation'
import { trackApi } from '../../api/trackApi'
import { editableRange, LINKED_SPAN, LINKED_SPAN_MAX, neighbourSuspects, plausibleNext, tailFrames, type TrackBox, withSize } from '../../annotation/linkedEdits'
import { annotationsByMedia, clone, currentFrame, currentMediaId, deletedFrameObjects, editingBlocked, isObjectDeleted, selectedMedia, selectedObject, selectedObjectId, statusMessage, type FrameDeletion } from './state'
import { isInteracting } from './editing'
import { frameToTime } from './frames'
import { scheduleWorkspaceStateSave } from './save'

export const linkedSpan = ref(LINKED_SPAN)
export const linkedBusy = ref(false)
export const linkedError = ref('')
type LinkedUndo = { mediaId: string; objectId: number; frames: number[]; objects: AnnotationObject[]; deletions: FrameDeletion[]; after: string; label: string }
const lastLinked = ref<LinkedUndo | null>(null)
let fillController: AbortController | null = null

const span = () => Math.max(1, Math.min(LINKED_SPAN_MAX, Math.round(Number(linkedSpan.value) || LINKED_SPAN)))
const objectTrack = (mediaId: string, objectId: number): TrackBox[] =>
  (annotationsByMedia.value[mediaId] ?? [])
    .filter(o => o.objectId === objectId && o.bbox && !isObjectDeleted(mediaId, objectId, o.frameIndex ?? 0))
    // Boxes set by these range edits stay adjustable; only boxes a person drew or fixed stop a range.
    .map(o => ({ frameIndex: o.frameIndex ?? 0, bbox: o.bbox!, manual: o.source === 'manual' && !o.origin?.startsWith('linked_') && o.frameIndex !== currentFrame.value }))

/** One video object's whole track around the current frame; bbox is its box here, if any. */
export function objectTarget(objectId: number) {
  const media = selectedMedia.value
  if (media?.type !== 'video' || !media.width || !media.height) return null
  const here = (annotationsByMedia.value[media.id] ?? []).find(o => o.objectId === objectId && (o.frameIndex ?? 0) === currentFrame.value && o.bbox
    && !isObjectDeleted(media.id, objectId, currentFrame.value))
  const track = objectTrack(media.id, objectId)
  if (!track.length) return null
  const frame = currentFrame.value, n = span()
  return {
    objectId, name: here?.name ?? (annotationsByMedia.value[media.id] ?? []).find(o => o.objectId === objectId)?.name ?? `rare sperm ${objectId}`,
    frame, bbox: here?.bbox ?? null, track,
    suspects: neighbourSuspects(track, frame, n, media.width, media.height), range: editableRange(track, frame, n),
    tail: tailFrames(track, frame),
    canFillBefore: !!here && frame > 0 && !track.some(t => t.frameIndex === frame - 1),
    canFillAfter: !!here && frame < (media.frameCount ?? 1) - 1 && !track.some(t => t.frameIndex === frame + 1),
  }
}
/** Selected video object; null when range edits do not apply. */
export const linkedTarget = computed(() => {
  const obj = selectedObject.value
  return obj?.bbox && obj.objectId != null ? objectTarget(obj.objectId) : null
})
// What the touched frames hold for this object; saving may replace arrays, so compare content.
const touchedState = (mediaId: string, objectId: number, frames: number[]) => {
  const set = new Set(frames)
  return JSON.stringify([
    (annotationsByMedia.value[mediaId] ?? []).filter(o => o.objectId === objectId && set.has(o.frameIndex ?? 0))
      .map(o => [o.frameIndex, o.bbox?.x, o.bbox?.y, o.bbox?.width, o.bbox?.height]).sort(),
    (deletedFrameObjects.value[mediaId] ?? []).filter(d => d.objectId === objectId && set.has(d.frameIndex)).map(d => d.frameIndex).sort(),
  ])
}
/** Offered only while those frames still hold exactly what the range edit left. */
export const canUndoLinked = computed(() => {
  const last = lastLinked.value
  return !!last && last.mediaId === currentMediaId.value && touchedState(last.mediaId, last.objectId, last.frames) === last.after
})
export const linkedUndoLabel = computed(() => lastLinked.value?.label ?? '')

const ready = (objectId?: number) => {
  linkedError.value = ''
  if (editingBlocked.value || isInteracting() || linkedBusy.value) { linkedError.value = '当前画面正在处理其他操作，请稍后再试'; return null }
  return objectId == null ? linkedTarget.value : objectTarget(objectId)
}
// Capture the touched object's boxes and deletion marks on the frames about to change.
const capture = (mediaId: string, objectId: number, frames: number[]) => {
  const set = new Set(frames)
  return {
    objects: clone((annotationsByMedia.value[mediaId] ?? []).filter(o => o.objectId === objectId && set.has(o.frameIndex ?? 0))),
    deletions: clone((deletedFrameObjects.value[mediaId] ?? []).filter(d => d.objectId === objectId && set.has(d.frameIndex))),
  }
}
const commit = (mediaId: string, objectId: number, frames: number[], before: ReturnType<typeof capture>, next: AnnotationObject[], label: string) => {
  annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: next }
  lastLinked.value = { mediaId, objectId, frames, ...before, after: touchedState(mediaId, objectId, frames), label }
  statusMessage.value = `${label}，可点“撤销联动操作”恢复`
  scheduleWorkspaceStateSave(mediaId)
}

const framesText = (frames: number[]) => frames.length === 1 ? `第 ${frames[0] + 1} 帧`
  : `第 ${frames[0] + 1}–${frames[frames.length - 1] + 1} 帧中的 ${frames.length} 帧`

/** 把当前框的宽高应用到指定帧上同一对象的框（保留各帧中心）。 */
export function applySizeToFrames(frameList: number[], objectId?: number) {
  const target = ready(objectId)
  if (!target) return
  if (!target.bbox) { linkedError.value = '当前帧没有这个对象的框，先跳到尺寸正确的一帧'; return }
  const present = new Set(target.track.map(t => t.frameIndex))
  const frames = [...new Set(frameList)].filter(fi => fi !== target.frame && present.has(fi)).sort((a, b) => a - b)
  if (!frames.length) { linkedError.value = '所选帧没有可调整的框'; return }
  const mediaId = currentMediaId.value, set = new Set(frames), { width, height } = target.bbox
  const before = capture(mediaId, target.objectId, frames)
  const next = (annotationsByMedia.value[mediaId] ?? []).map(o => o.objectId === target.objectId && o.bbox && set.has(o.frameIndex ?? 0)
    && !isObjectDeleted(mediaId, target.objectId, o.frameIndex ?? 0)
    ? { ...o, bbox: withSize(o.bbox, width, height), source: 'manual' as const, origin: o.origin ?? 'linked_resize' as const } : o)
  console.info('[annotation.linked_resize]', { mediaId, objectId: target.objectId, frames })
  commit(mediaId, target.objectId, frames, before, next, `已把对象 #${target.objectId} 的框尺寸应用到${framesText(frames)}`)
}

/** 相邻帧：两侧连续有框的帧段，遇缺框或人工框停止。 */
export function applySizeToNeighbours() {
  const target = linkedTarget.value
  const frames = target ? [...target.range.before, ...target.range.after] : []
  if (target && !frames.length) { linkedError.value = '相邻帧没有可调整的框（遇到缺框或人工框会停下）'; return }
  applySizeToFrames(frames)
}

/** 删除指定帧上同一对象的框（出画后的残留框等）。 */
export function deleteFrames(frameList: number[], objectId?: number) {
  const target = ready(objectId)
  if (!target) return
  const present = new Set(target.track.map(t => t.frameIndex))
  const frames = [...new Set(frameList)].filter(fi => present.has(fi)).sort((a, b) => a - b)
  if (!frames.length) return
  const mediaId = currentMediaId.value, set = new Set(frames)
  const before = capture(mediaId, target.objectId, frames)
  const next = (annotationsByMedia.value[mediaId] ?? []).filter(o => !(o.objectId === target.objectId && set.has(o.frameIndex ?? 0)))
  deletedFrameObjects.value[mediaId] = [
    ...(deletedFrameObjects.value[mediaId] ?? []).filter(d => !(d.objectId === target.objectId && set.has(d.frameIndex))),
    ...frames.map(frameIndex => ({ objectId: target.objectId, frameIndex })),
  ]
  if (set.has(target.frame)) selectedObjectId.value = null
  console.info('[annotation.linked_delete]', { mediaId, objectId: target.objectId, frames })
  commit(mediaId, target.objectId, frames, before, next, `已删除对象 #${target.objectId} ${framesText(frames)}的框`)
}

/** 删除当前帧起到连续轨迹结束的框。 */
export function deleteTail() {
  const target = linkedTarget.value
  if (target?.tail.length) deleteFrames(target.tail)
}

/** 从当前框出发，逐帧用头部检测向前或向后补框；结果不是同一个头、已有框或检测失败即停。 */
export async function fillAlong(direction: -1 | 1) {
  const target = ready(), media = selectedMedia.value
  if (!target?.bbox || !media?.serverMediaId || !media.width || !media.height) {
    if (target) linkedError.value = '逐帧补框只用于已导入服务器的视频'
    return
  }
  const mediaId = media.id, objectId = target.objectId, existing = new Set(target.track.map(t => t.frameIndex))
  const controller = new AbortController(), last = (media.frameCount ?? 1) - 1
  fillController = controller; linkedBusy.value = true
  const added: AnnotationObject[] = []
  let prev = target.bbox, stopReason = ''
  try {
    for (let k = 1; k <= span(); k++) {
      const frame = target.frame + direction * k
      if (frame < 0 || frame > last) { stopReason = '已到视频端点'; break }
      if (existing.has(frame)) { stopReason = `第 ${frame + 1} 帧已有框`; break }
      if (isObjectDeleted(mediaId, objectId, frame)) { stopReason = `第 ${frame + 1} 帧此对象已删除`; break }
      const cx = (prev.x + prev.width / 2) * media.width / 100, cy = (prev.y + prev.height / 2) * media.height / 100
      let result
      try { result = await trackApi.headBox(media.serverMediaId, frame, Math.min(media.width - 1, cx), Math.min(media.height - 1, cy), controller.signal) }
      catch (error) {
        if (controller.signal.aborted) return
        stopReason = `第 ${frame + 1} 帧没有找到头部（${error instanceof Error ? error.message : '检测失败'}）`; break
      }
      if (controller.signal.aborted || currentMediaId.value !== mediaId) return
      const [x1, y1, x2, y2] = result.bbox || []
      if (result.frameIndex !== frame || result.width !== media.width || result.height !== media.height || ![x1, y1, x2, y2].every(Number.isFinite) || x2 <= x1 || y2 <= y1) { stopReason = `第 ${frame + 1} 帧检测结果无效`; break }
      const box = { x: x1 / media.width * 100, y: y1 / media.height * 100, width: (x2 - x1) / media.width * 100, height: (y2 - y1) / media.height * 100 }
      if (!plausibleNext(prev, box, media.width, media.height)) { stopReason = `第 ${frame + 1} 帧找到的头部离得太远或大小不符`; break }
      added.push({ id: `manual-linked-${Date.now()}-${frame}`, objectId, name: target.name, source: 'manual', origin: 'linked_fill', bbox: box, frameIndex: frame, timestampMs: Math.round(frameToTime(frame) * 1000) })
      prev = box
    }
  } finally { if (fillController === controller) { fillController = null; linkedBusy.value = false } }
  if (editingBlocked.value) { linkedError.value = '画面状态已变化，未补框，请重试'; return }
  // Detection is async: never duplicate a box someone added (or deleted) meanwhile.
  const occupied = new Set((annotationsByMedia.value[mediaId] ?? []).filter(o => o.objectId === objectId).map(o => o.frameIndex ?? 0))
  const fresh = added.filter(o => !occupied.has(o.frameIndex!) && !isObjectDeleted(mediaId, objectId, o.frameIndex!))
  if (fresh.length < added.length) stopReason = stopReason || '部分帧在检测期间已被修改，已跳过'
  added.splice(0, added.length, ...fresh)
  if (!added.length) { linkedError.value = stopReason || '没有补上任何框'; return }
  const frames = added.map(o => o.frameIndex!).sort((a, b) => a - b)
  const before = capture(mediaId, objectId, frames)
  console.info('[annotation.linked_fill]', { mediaId, objectId, frames, stopReason })
  commit(mediaId, objectId, frames, before, [...(annotationsByMedia.value[mediaId] ?? []), ...added],
    `已为对象 #${objectId} 补框第 ${frames[0] + 1}–${frames[frames.length - 1] + 1} 帧（${frames.length} 帧${stopReason ? `；${stopReason}，已停止` : ''}）`)
}

export function cancelLinkedFill() { fillController?.abort(); fillController = null; linkedBusy.value = false }

export function undoLinked() {
  const last = lastLinked.value
  if (!canUndoLinked.value || !last || editingBlocked.value || isInteracting()) return
  const set = new Set(last.frames)
  const rest = (annotationsByMedia.value[last.mediaId] ?? []).filter(o => !(o.objectId === last.objectId && set.has(o.frameIndex ?? 0)))
  annotationsByMedia.value = { ...annotationsByMedia.value, [last.mediaId]: [...rest, ...clone(last.objects)] }
  deletedFrameObjects.value[last.mediaId] = [
    ...(deletedFrameObjects.value[last.mediaId] ?? []).filter(d => !(d.objectId === last.objectId && set.has(d.frameIndex))),
    ...clone(last.deletions),
  ]
  lastLinked.value = null
  statusMessage.value = '已撤销联动操作'
  scheduleWorkspaceStateSave(last.mediaId)
}
