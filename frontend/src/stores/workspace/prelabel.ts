// 自动预标：打开没有 AI 结果的视频时，用检测器逐帧识别并连成目标；标注员在查漏帧上补漏。
import { computed, ref } from 'vue'
import type { AnnotationObject } from '../../types/annotation'
import { trackApi, type PrelabelSummary } from '../../api/trackApi'
import { createRequestId } from '../../utils/browserCompat'
import { useAuth } from '../auth'
import {
  annotationsByMedia, currentFrame, currentMediaId, editingBlocked, getNextObjectId, maxFrameIndex, mediaAssets,
  messageOf, objectNameInput, runtime, selectedObjectId, showToast, statusMessage, trackingFinishedByMedia,
  trackingMedia, trackingProgressByMedia, trackingResultState, trackingStatusByMedia,
} from './state'
import { numberedObjectName } from './editing'
import { scheduleWorkspaceStateSave } from './save'

export const prelabelByMedia = ref<Record<string, PrelabelSummary>>({})
export const prelabelErrors = ref<Record<string, string>>({})
const prelabelJobs = new Map<string, { key: string; taskId?: string }>()
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export const currentPrelabel = computed(() => prelabelByMedia.value[currentMediaId.value] ?? null)
export const checkFrames = computed(() => currentPrelabel.value?.checkFrames ?? [])
export const scannedCount = computed(() => checkFrames.value.filter((item) => item.scannedAt).length)
export const scanComplete = computed(() => checkFrames.value.length > 0 && scannedCount.value === checkFrames.value.length)
export const currentCheckFrame = computed(() => checkFrames.value.find((item) => item.frame === currentFrame.value) ?? null)
export const nextUnscannedFrame = computed(() => checkFrames.value.find((item) => !item.scannedAt)?.frame ?? null)

export const loadPrelabel = async (mediaId: string) => {
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media?.serverMediaId || media.type !== 'video') return null
  const summary = await trackApi.prelabelSummary(media.serverMediaId)
  prelabelByMedia.value = { ...prelabelByMedia.value, [mediaId]: summary }
  return summary
}

/** Called after the tracking result of the current video was read. */
export const afterTrackingLoaded = async (mediaId: string) => {
  const summary = await loadPrelabel(mediaId).catch((error) => {
    console.warn('[annotation.prelabel_summary_failed]', { mediaId, error })
    return null
  })
  if (summary?.state === 'none' && trackingResultState.value[mediaId] === 'not_generated' && !trackingMedia.has(mediaId)) void startPrelabel(mediaId)
}

export const startPrelabel = async (mediaId: string) => {
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media?.serverMediaId || media.type !== 'video' || trackingMedia.has(mediaId)) return
  const actor = useAuth().user.value?.id
  const end = Math.max(0, (media.frameCount ?? maxFrameIndex.value + 1) - 1)
  const progress = (patch: Record<string, unknown> = {}) => {
    trackingStatusByMedia.value[mediaId] = { ...trackingStatusByMedia.value[mediaId], startFrame: 0, endFrame: end, endpoint: false, combine: false, prelabel: true, ...patch }
  }
  delete prelabelErrors.value[mediaId]
  delete trackingFinishedByMedia.value[mediaId]
  trackingMedia.add(mediaId)
  progress({ status: 'queued', stage: 'queued' })
  let succeeded = false
  try {
    let job = prelabelJobs.get(mediaId)
    if (!job) { job = { key: createRequestId() }; prelabelJobs.set(mediaId, job) }
    if (!job.taskId) job.taskId = (await trackApi.startPrelabel(media.serverMediaId, job.key)).taskId
    for (;;) {
      const status = await trackApi.getStatus(job.taskId)
      if (actor !== useAuth().user.value?.id) return
      progress({ status: status.status, stage: status.stage, lastProcessedFrame: status.lastProcessedFrame, elapsedSeconds: status.elapsedSeconds })
      trackingProgressByMedia.value[mediaId] = status.status === 'queued' ? '排队中' : status.stage === 'linking' ? '正在连成目标' : '正在识别精子头'
      if (status.status === 'success') break
      if (status.status === 'failed') { prelabelJobs.delete(mediaId); throw new Error(status.message || '自动预标失败') }
      await sleep(500)
    }
    prelabelJobs.delete(mediaId)
    progress({ loadingResult: true })
    const { loadTrackingResult } = await import('./trackingResult')
    await loadTrackingResult(mediaId, true, true, true)
    const summary = await loadPrelabel(mediaId)
    succeeded = true
    if (mediaId === currentMediaId.value) {
      const count = summary?.tracks?.length ?? 0
      showToast(count ? `自动预标完成：找到 ${count} 个目标` : '自动预标完成：没有找到精子头', 'success')
    }
  } catch (error) {
    const code = (error as { code?: string })?.code
    if (code === 'PRELABEL_EXISTS') {
      prelabelJobs.delete(mediaId)
      const { loadTrackingResult } = await import('./trackingResult')
      await loadTrackingResult(mediaId, true).catch(() => undefined)
      return
    }
    if (Number((error as { status?: number })?.status) === 404) prelabelJobs.delete(mediaId)
    prelabelErrors.value[mediaId] = messageOf(error, '自动预标失败')
    console.error('[annotation.prelabel_failed]', { mediaId, error })
  } finally {
    trackingMedia.delete(mediaId)
    delete trackingStatusByMedia.value[mediaId]
    delete trackingProgressByMedia.value[mediaId]
    // No "finished" flash: the card turns straight into the scan task and a toast says how many were found.
    void succeeded
  }
}
export const retryPrelabel = () => startPrelabel(currentMediaId.value)

export const scanBusy = ref(false)
/** The annotator looked over the current check frame; returns the next frame to look at, if any. */
export const markFrameScanned = async () => {
  const mediaId = currentMediaId.value, media = mediaAssets.value.find((item) => item.id === mediaId)
  const frame = currentFrame.value
  if (!media?.serverMediaId || !currentCheckFrame.value || scanBusy.value) return null
  scanBusy.value = true
  try {
    const summary = await trackApi.recordScan(media.serverMediaId, frame)
    prelabelByMedia.value = { ...prelabelByMedia.value, [mediaId]: summary }
    return summary.checkFrames?.find((item) => !item.scannedAt)?.frame ?? null
  } catch (error) {
    showToast(messageOf(error, '没有记录上，请重试'), 'error')
    return null
  } finally {
    scanBusy.value = false
  }
}

// One click adds the same head on every frame it was found; undo removes all of them at once.
type TrackAdd = { objectId: number; objects: AnnotationObject[]; sequence: number; undone: boolean }
export const trackAdds = ref<Record<string, TrackAdd>>({})
export const extendBusy = ref(false)
let extendRequest: AbortController | null = null
export const cancelExtend = () => { extendRequest?.abort(); extendRequest = null; extendBusy.value = false }

export const extendAt = async (point: { x: number; y: number }) => {
  const mediaId = currentMediaId.value, media = mediaAssets.value.find((item) => item.id === mediaId)
  if (editingBlocked.value || extendBusy.value || !media) return
  if (!media.serverMediaId || media.type !== 'video') { showToast('图片请按住拖动画框', 'info'); return }
  const frame = currentFrame.value, controller = new AbortController(), width = media.width || 1, height = media.height || 1
  extendRequest = controller; extendBusy.value = true
  try {
    const result = await trackApi.extendHead(media.serverMediaId, frame, Math.min(width - 1, point.x * width / 100), Math.min(height - 1, point.y * height / 100), controller.signal)
    if (extendRequest !== controller || controller.signal.aborted || mediaId !== currentMediaId.value || frame !== currentFrame.value || editingBlocked.value) return
    if (result.width !== width || result.height !== height || !result.boxes.length) throw new Error('检测结果无效，未添加；可以按住拖动手动画框')
    const objectId = getNextObjectId(mediaId)
    const name = numberedObjectName(objectNameInput.value.trim() || 'rare sperm', objectId)
    const stamp = Date.now()
    const fps = media.fps || 0
    const objects: AnnotationObject[] = result.boxes.map((item) => {
      const [x1, y1, x2, y2] = item.bbox
      if (![x1, y1, x2, y2].every(Number.isFinite) || x2 <= x1 || y2 <= y1) throw new Error('检测结果无效，未添加；可以按住拖动手动画框')
      const creationId = `manual-${stamp}-${objectId}-${item.frameIndex}`
      return {
        id: creationId, creationId, objectId, name, source: 'manual' as const,
        bbox: { x: x1 / width * 100, y: y1 / height * 100, width: (x2 - x1) / width * 100, height: (y2 - y1) / height * 100 },
        frameIndex: item.frameIndex, timestampMs: fps > 0 ? Math.round(item.frameIndex * 1000 / fps) : 0,
      }
    })
    annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: [...(annotationsByMedia.value[mediaId] ?? []), ...objects] }
    trackAdds.value = { ...trackAdds.value, [mediaId]: { objectId, objects, sequence: ++runtime.historySequence, undone: false } }
    selectedObjectId.value = objects.find((item) => item.frameIndex === frame)?.id ?? null
    statusMessage.value = `已补上 ${name}`
    showToast(objects.length > 1 ? `已补上，${objects.length} 帧都加好了` : '已补上（其他帧没找到这个头，可以逐帧补）', 'success')
    scheduleWorkspaceStateSave(mediaId)
  } catch (error) {
    if (extendRequest !== controller || controller.signal.aborted) return
    const code = (error as { code?: string })?.code, status = Number((error as { status?: number })?.status)
    showToast(code === 'ALREADY_BOXED' ? '这里已经有框了' : status === 422 ? '这里没找到精子头 · 按住拖动可以手动画框' : messageOf(error, '没能补上，请重试或按住拖动画框'), code === 'ALREADY_BOXED' || status === 422 ? 'info' : 'error')
  } finally {
    if (extendRequest === controller) cancelExtend()
  }
}

export const dropUndoneTrackAdd = (mediaId: string) => {
  if (trackAdds.value[mediaId]?.undone) { const next = { ...trackAdds.value }; delete next[mediaId]; trackAdds.value = next }
}
/** Undo/redo of the latest multi-frame add, when it is newer than the frame-local history. */
export const latestTrackAdd = (mediaId: string) => trackAdds.value[mediaId]
export const undoTrackAdd = (mediaId: string) => {
  const add = trackAdds.value[mediaId]
  if (!add || add.undone) return false
  const ids = new Set(add.objects.map((item) => item.id))
  annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: (annotationsByMedia.value[mediaId] ?? []).filter((item) => !ids.has(item.id)) }
  trackAdds.value = { ...trackAdds.value, [mediaId]: { ...add, undone: true } }
  if (ids.has(selectedObjectId.value ?? '')) selectedObjectId.value = null
  statusMessage.value = '已撤销刚补上的目标（所有帧）'
  scheduleWorkspaceStateSave(mediaId)
  return true
}
export const redoTrackAdd = (mediaId: string) => {
  const add = trackAdds.value[mediaId]
  if (!add?.undone) return false
  annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: [...(annotationsByMedia.value[mediaId] ?? []), ...add.objects] }
  trackAdds.value = { ...trackAdds.value, [mediaId]: { ...add, undone: false } }
  statusMessage.value = '已重做补上的目标'
  scheduleWorkspaceStateSave(mediaId)
  return true
}

// Videos labelled with the old SAM3 flow keep their boxes until someone explicitly starts over.
export const reprelabelBusy = ref(false)
export const reprelabelCurrent = async () => {
  const mediaId = currentMediaId.value, media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media?.serverMediaId || reprelabelBusy.value || trackingMedia.has(mediaId)) return
  reprelabelBusy.value = true
  try {
    const [{ persistWorkspaceState }, { annotationReviewApi }, { clearResetCache }, { resetAnnotationViewForMedia }] =
      await Promise.all([import('./save'), import('../../api/annotationReviewApi'), import('./media'), import('./restore')])
    await persistWorkspaceState(mediaId, true)
    const stamp = await trackApi.getWorkspaceStamp(media.serverMediaId)
    const result = await annotationReviewApi.resetAnnotations(media.serverMediaId, stamp.revision, createRequestId())
    if (!result.ok || result.mediaId !== media.serverMediaId) throw new Error('服务器未确认清空')
    clearResetCache(media.serverMediaId, result.revision)
    const next = { ...prelabelByMedia.value }; delete next[mediaId]; prelabelByMedia.value = next
    trackingResultState.value = { ...trackingResultState.value, [mediaId]: 'not_generated' }
    await resetAnnotationViewForMedia(mediaId)
  } catch (error) {
    showToast(messageOf(error, '没能清空旧标注，请重试'), 'error')
  } finally {
    reprelabelBusy.value = false
  }
}
