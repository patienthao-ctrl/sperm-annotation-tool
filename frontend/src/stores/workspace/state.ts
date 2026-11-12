// 工作区共享状态：素材、当前帧、标注数据、追踪/删除/暂停等响应式状态与派生值；不依赖其他工作区模块。
import { computed, reactive, ref, shallowRef, watch } from 'vue'
import type { OverlapPairNotice } from '../../annotation/pauseDiagnostics'
import { savedTrackingCalibration } from '../../annotation/trackingCalibration'
import { describeTrackingProgress, type TrackingProgressInput, type TrackingProgressView } from '../../annotation/trackingProgress'
import type { Box } from '../../annotation/geometry'
import { createToastController, type Toast } from '../../annotation/notices'
import type { AnnotationObject, AnnotationTool, MediaAsset, TrackingFrameResult } from '../../types/annotation'
import { httpAnnotationApi } from '../../api/httpAnnotationApi'
import { trackApi, type DuplicateVideo, type PackageImportResult, type TrackStatusResponse, type NormalMotionSample, type TrackingWarningSummary, type TrackingFeedbackEvent, type TrackingFeedbackInput, type TrackingRestartInput } from '../../api/trackApi'
import { WorkspaceWrites, WorkspaceMergeConflict, type WorkspaceDraft } from '../../annotation/workspaceWrites'

const VIDEO_EXTENSIONS = new Set([
  '.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v', '.mpg', '.mpeg', '.wmv', '.flv',
  '.ts', '.m2ts', '.mts', '.3gp', '.ogv', '.ogg', '.asf', '.vob', '.divx', '.xvid',
])
export const isVideoFile = (file: File) => file.type.startsWith('video/') || VIDEO_EXTENSIONS.has(`.${file.name.split('.').pop()?.toLowerCase() || ''}`)
export const api = httpAnnotationApi

// ── mediaAssets 持久化：刷新后 id 不变，annotationsByMedia 才能匹配 ──
const loadMediaAssets = (): MediaAsset[] => {
  try {
    const raw = localStorage.getItem('mediaAssets')
    if (raw) return JSON.parse(raw)
  } catch {}
  return [
    {
      id: 'img-demo-001',
      name: 'microfluidic-sample.svg',
      type: 'image',
      url: '/demo/microfluidic-sample.svg',
      width: 1600,
      height: 900,
    },
  ]
}
export const mediaAssets = ref<MediaAsset[]>(loadMediaAssets())
// Read legacy closed-media IDs so upgrades retain their original annotation identity.
const loadClosedMediaFrontendIds = (): Record<string, string> => {
  try {
    return JSON.parse(localStorage.getItem('closedMediaFrontendIds') || '{}')
  } catch {
    return {}
  }
}
export const closedMediaFrontendIds = loadClosedMediaFrontendIds()
export const persistClosedMediaFrontendIds = () => {
  try { localStorage.setItem('closedMediaFrontendIds', JSON.stringify(closedMediaFrontendIds)) } catch {}
}
let mediaSaveTimer: ReturnType<typeof setTimeout> | null = null
watch(mediaAssets, (val) => {
  if (mediaSaveTimer) clearTimeout(mediaSaveTimer)
  mediaSaveTimer = setTimeout(() => {
    try {
      // URL.createObjectURL 生成的 blob: URL 在刷新后失效，持久化前剔除
      const persistable = val.map((m) => ({ ...m }))
      localStorage.setItem('mediaAssets', JSON.stringify(persistable))
    } catch {}
  }, 300)
}, { deep: true })

let rememberedMedia = ''
try { rememberedMedia = localStorage.getItem('annotation-selected-media') || '' } catch {}
export const selectedMediaId = ref(mediaAssets.value.find(m => m.id === rememberedMedia)?.id ?? mediaAssets.value[0]?.id ?? '')
export const activeTool = ref<AnnotationTool>('smart')
export const objectNameInput = ref('rare sperm')
export const selectedObjectId = ref<string | null>(null)
export const currentFrame = ref(0)
export const currentTime = ref(0)
export const videoDuration = ref(0)
export const videoFps = ref(30)
export const playbackRate = ref(1)
export const frameInput = ref(0)
export const isPlaying = ref(false)
// Tracking locks only the video being tracked: other videos stay editable and
// can queue their own jobs while the server runs GPU tasks one at a time.
export const segmentBusy = ref(false)
export const isAiBusy = computed(() => segmentBusy.value || trackingMedia.has(currentMediaId.value) || trackingJobs.has(currentMediaId.value))
export const deletingMediaId = ref<string | null>(null)
export const mediaDeleteError = ref('')
export const trackingFrameCount = ref(5)
export const statusMessage = ref('就绪')
export const saveState = ref<'idle'|'saving'|'saved'|'error'>('idle')
export const saveError = ref('')
export const collaborationErrors=shallowRef<Record<string,WorkspaceMergeConflict>>({})
export const collaborationConflict=computed(()=>collaborationErrors.value[currentMediaId.value]??null)
export const collaborationBusy=ref(false)
// Another account's tracking job on the open video: editing waits for it, like our own.
export const remoteTracking=ref<Record<string,{userName:string|null;status:string}>>({})
export const currentRemoteTracking=computed(()=>trackingMedia.has(currentMediaId.value)?null:remoteTracking.value[currentMediaId.value]??null)
export const lastEditors=ref<Record<string,string|null>>({})
export const messageOf = (error: unknown, fallback: string) => typeof (error as {message?:unknown})?.message === 'string' ? (error as {message:string}).message : fallback
export type TrackingJob = {mediaId:string;startFrame:number;input:Parameters<typeof trackApi.run>[0];key:string;taskId?:string;maxFrames?:number}
export const trackingJobs = reactive(new Map<string, TrackingJob>())
export const trackingMedia = reactive(new Set<string>()) // videos whose tracking loop runs in this tab
export const trackingProgressByMedia = ref<Record<string,string>>({})
export const trackingErrors = ref<Record<string,{message:string;phase:string;frameIndex:number;endpointMode?:boolean}>>({})
export const trackingError = computed(()=>trackingErrors.value[currentMediaId.value])
export const trackingRetryBusy = computed(()=>trackingMedia.has(currentMediaId.value))
export const trackingProgress = computed(()=>trackingProgressByMedia.value[currentMediaId.value] ?? '')
// Structured status behind the stepped progress bar (AI card, header, frame strip, media list).
export const trackingStatusByMedia = ref<Record<string,TrackingProgressInput>>({})
// Videos whose run just completed: the bar briefly shows its finished state before leaving.
// One-button enhancement (median background, gain 8, as on the training review sheets). Per browser.
// On by default every time the page opens; turning it off only lasts for this tab.
export const enhancedView = ref((() => { try { return sessionStorage.getItem('annotation-enhanced') !== '0' } catch { return true } })())
// 'not_generated': the video has no AI result yet, so the detector prelabel may run.
export const trackingResultState = ref<Record<string,'available'|'not_generated'>>({})
export const trackingFinishedByMedia = ref<Record<string,true>>({})
export const trackingProgressViewFor = (mediaId: string): TrackingProgressView | null => {
  const input = trackingStatusByMedia.value[mediaId]
  return input ? describeTrackingProgress(input) : null
}
export const currentTrackingProgress = computed(()=>trackingProgressViewFor(currentMediaId.value))
export const currentTrackingFinished = computed(()=>!!trackingFinishedByMedia.value[currentMediaId.value])
export const isTrackingMedia = (mediaId: string) => trackingMedia.has(mediaId) || trackingJobs.has(mediaId)
export const saveQueues = new Map<string, Promise<unknown>>()
export const workspaceViews = new Map<string, Record<string, unknown>>()
export const saveTickets = new Map<string, number>()
export const workspaceWrites = new WorkspaceWrites()
export const storageWarning = ref('')
workspaceWrites.onStorageWarning = message => { storageWarning.value = message }
export const workspaceReadError = ref('')
export type TrackingRestartIntent = { actorId: string; mediaId: string; serverMediaId: string; key: string; body: TrackingRestartInput; phase: 'restart' | 'start' | 'conflict' }
export const pendingRestart = ref<TrackingRestartIntent | null>(null)
export const trackingRestartBusy = ref(false), trackingRestartError = ref(''), trackingRestartStorageWarning = ref('')
export const trackingRestartPendingAction = computed(() => pendingRestart.value?.mediaId === currentMediaId.value ? pendingRestart.value : null)
export const zoom = ref(1)
export const toast = shallowRef<Toast | null>(null)
const toastController = createToastController(next => { toast.value = next })
export const showToast = toastController.show
export const dismissToast = toastController.dismiss

export const imageRef = ref<HTMLImageElement | null>(null)
export const videoRef = ref<HTMLVideoElement | null>(null)
export const exactFrameImageRef = ref<HTMLImageElement | null>(null)
export const exactFrameUrl = ref<string | null>(null)
export const exactFrameLoading = ref(false)
export const annotationHitRef = ref<HTMLDivElement | null>(null)
export const fileInputRef = ref<HTMLInputElement | null>(null)
export const videoInputRef = ref<HTMLInputElement | null>(null)
export const packageInputRef = ref<HTMLInputElement | null>(null)
export const packageImportReport = ref<PackageImportResult | null>(null)
export const effectFolderInputRef = ref<HTMLInputElement | null>(null)

// 从 localStorage 恢复标注数据
const loadAnnotations = (): Record<string, AnnotationObject[]> => {
  try {
    const raw = localStorage.getItem('annotationsByMedia')
    if (raw) return JSON.parse(raw)
  } catch {}
  return {
    'img-demo-001': [],
    'video-demo-001': [],
  }
}
// Every edit replaces its media array. Avoid deep proxies/watch traversal of
// tens of thousands of immutable AI boxes on each frame navigation or commit.
export const annotationsByMedia = shallowRef<Record<string, AnnotationObject[]>>(loadAnnotations())
export const workspaceGenerations = new Map<string,string|undefined>()

// ── 撤销/重做栈 ──
export type FrameDeletion = { objectId: number; frameIndex: number }

// SAM3 逐帧结果缓存。key=前端 media.id，value=服务器 tracking_result.json 的 frames。
export const trackingFramesByMedia = ref<Record<string, TrackingFrameResult[]>>({})
/** 记录前端删除的 AI tracking 对象 id，避免重新加载后又出现 */
export const deletedTrackingIds = ref<Record<string, Set<string>>>({})
export const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
export const deletedObjectIds = ref<Record<string, number[]>>({})
export const deletedFrameObjects = ref<Record<string, FrameDeletion[]>>({})
export const normalMotionSamples = ref<Record<string, NormalMotionSample[]>>({})
export const trackingFeedbackEvents = ref<Record<string, TrackingFeedbackEvent[]>>({})
export const trackingWarningSummary = ref<TrackingWarningSummary[]>([])
export const objectDeletionBusy = ref(false)
export const objectDeletionError = ref('')
export const trackingFeedbackBusy = ref(false)
export const trackingFeedbackError = ref('')
export type VideoDeletion = { objectId: number; name: string; frameCount: number; objects: AnnotationObject[]; sequence: number; undone: boolean; pause?: { item: typeof pausedAnomalies.value[number]; context: NonNullable<typeof lastPausedContext.value> } }
export const videoDeletionHistory = ref<Record<string, VideoDeletion>>({})
type DeletionIntent = { mediaId: string; serverMediaId: string; key: string; action: 'delete' | 'undo' | 'redo'; record: VideoDeletion; state: WorkspaceDraft }
export const pendingDeletion = ref<DeletionIntent | null>(null)
type FeedbackIntent = { mediaId: string; serverMediaId: string; key: string; body: TrackingFeedbackInput }
export const pendingFeedback = ref<FeedbackIntent | null>(null)
export const objectDeletionPendingAction = computed(() => pendingDeletion.value ? { objectId: pendingDeletion.value.record.objectId, action: pendingDeletion.value.action } : null)
export const trackingFeedbackPendingAction = computed(() => pendingFeedback.value ? { objectId: pendingFeedback.value.body.objectId, decision: pendingFeedback.value.body.decision, learn: pendingFeedback.value.body.calibrate, scope:pendingFeedback.value.body.scope ?? 'object' } : null)
export const trackingFeedbackPending = computed(() => !!pendingFeedback.value)
export const canUndoVideoDeletion = computed(() => !!videoDeletionHistory.value[currentMediaId.value] && !videoDeletionHistory.value[currentMediaId.value].undone)
export const lastVideoObjectDeletion = computed(() => canUndoVideoDeletion.value ? videoDeletionHistory.value[currentMediaId.value] : null)
export const trackingCalibrationSummary = computed(() => savedTrackingCalibration(
  normalMotionSamples.value[currentMediaId.value] ?? [],
  trackingFeedbackEvents.value[currentMediaId.value] ?? [],
  deletedObjectIds.value[currentMediaId.value] ?? [],
))
export const isObjectDeleted = (mediaId: string, objectId: number | undefined, frameIndex: number) => objectId != null && (
  deletedObjectIds.value[mediaId]?.includes(objectId) || deletedFrameObjects.value[mediaId]?.some(d => d.objectId === objectId && d.frameIndex === frameIndex)
)

// 异常物体 ID 列表（面积突变等），用于高亮
export const anomalyObjectIds = ref<number[]>([])
// 异常帧列表（给时间轴标记用）
export const anomalyFrames = ref<Array<{ frame_index: number; level: string; reasons: string[] }>>([])
// 当前暂停的可读诊断，供人工在画面旁直接判断如何修框/续追。
export const pausedAnomalies = ref<Array<{
  objectId: number
  displayName: string
  title: string
  summary: string
  metrics: string[]
  baselineFrame?: number
  geometryReferenceFrame?: number
  geometryReferenceSource?: 'manual' | 'confirmed-normal'
  triggerSummary?: string
  maskTrigger?: boolean
  overlapPairs?: OverlapPairNotice[]
  acceptsOverlap?: boolean
  pauseStartFrame?: number
  decisionFrame?: number
  diagnostic?: NonNullable<TrackStatusResponse['pausedObjects']>[number]
  acceptsGeometry: boolean
  reviewRange?: string
  reviewNotice?: string
  suggestion: string
  canLearn: boolean
  rawReasons: string[]
  resolved?: boolean
}>>([])
export const anomalyPanelVisible = ref(false)
export const closeAnomalyPanel = () => { anomalyPanelVisible.value = false }
export const lastPausedContext = ref<{ mediaId: string; frameIndex: number } | null>(null)

export const selectedMedia = computed(() => mediaAssets.value.find((item) => item.id === selectedMediaId.value) ?? mediaAssets.value[0] ?? null)
export const isVideo = computed(() => selectedMedia.value?.type === 'video')
export const maxFrameIndex = computed(() => {
  const media = selectedMedia.value
  if (media?.frameCount && media.frameCount > 0) return Math.max(0, media.frameCount - 1)
  return Math.max(0, Math.ceil(videoDuration.value * videoFps.value) - 1)
})

/** 视频按真实 currentFrame 标注；Tracking 从当前人工标注帧开始。 */
export const currentMediaId = computed(() => selectedMediaId.value)
// The pause banner describes the tracking event, not the frame the user is
// currently browsing afterwards. Keep it pinned to the backend pausedFrame.
export const trackingPausedFrame = computed(() => {
  const context = lastPausedContext.value
  return context?.mediaId === currentMediaId.value ? context.frameIndex : null
})

const annotationIdentity = (obj: AnnotationObject) =>
  `${obj.frameIndex ?? 0}:${obj.objectId ?? obj.id}`

export const dedupeAnnotationObjects = (objects: AnnotationObject[]) => {
  const unique = new Map<string, AnnotationObject>()
  for (const obj of objects) {
    const key = annotationIdentity(obj)
    const previous = unique.get(key)
    // Manual state is authoritative over an AI object on the same frame/ID.
    if (!previous || obj.source === 'manual' || previous.source !== 'manual') unique.set(key, obj)
  }
  return [...unique.values()]
}

/** 全局 objectId 计数器：从现有最大 objectId 继续分配 */
export const getNextObjectId = (mediaId?: string): number => {
  let maxId = 0
  for (const [mid, objs] of Object.entries(annotationsByMedia.value)) {
    if (mediaId && mid !== mediaId) continue
    for (const o of objs) {
      if (typeof o.objectId === 'number' && o.objectId > maxId) maxId = o.objectId
    }
  }
  for (const [mid, ids] of Object.entries(deletedObjectIds.value)) if (!mediaId || mid === mediaId) for (const id of ids) maxId = Math.max(maxId, id)
  for (const [mid, items] of Object.entries(deletedFrameObjects.value)) if (!mediaId || mid === mediaId) for (const item of items) maxId = Math.max(maxId, item.objectId)
  return maxId + 1
}
// Index membership only when annotations change; ordinary frame navigation does not scan the whole video.
const objectsByFrame = computed(() => {
  const index = new Map<number, AnnotationObject[]>()
  for (const obj of annotationsByMedia.value[currentMediaId.value] ?? []) {
    const fi = obj.frameIndex ?? 0
    if (isObjectDeleted(currentMediaId.value, obj.objectId, fi)) continue
    if (!index.has(fi)) index.set(fi, [])
    index.get(fi)!.push(obj)
  }
  return index
})
export const currentObjects = computed(() => isVideo.value
  ? objectsByFrame.value.get(currentFrame.value) ?? []
  : annotationsByMedia.value[currentMediaId.value] ?? [])
export const dragPreview = ref<{ id: string; bbox: Box } | null>(null)
export const displayObjects = computed(() => dragPreview.value
  ? currentObjects.value.map(o => o.id === dragPreview.value!.id ? { ...o, bbox: dragPreview.value!.bbox } : o)
  : currentObjects.value)
export const submissionLocks=ref(new Set<string>())
export const submissionBusy=computed(()=>submissionLocks.value.size>0)
export const mediaImportBusy = ref(false)
export const duplicateImport = ref<(DuplicateVideo & {key:string;pending:boolean;error:string})|null>(null)
export const importError = ref('')
export const editingBlocked = computed(() => !!collaborationConflict.value || collaborationBusy.value || !!currentRemoteTracking.value || submissionBusy.value || mediaImportBusy.value || !!duplicateImport.value || objectDeletionBusy.value || trackingFeedbackBusy.value || trackingRestartBusy.value || !!pendingRestart.value || !!pendingDeletion.value || !!pendingFeedback.value || !!workspaceReadError.value || isAiBusy.value || !!deletingMediaId.value || exactFrameLoading.value || isPlaying.value || workspaceRestoring.value || !!frameError.value)
export const workspaceRestoring = ref(false)
export const selectedObject = computed(() => currentObjects.value.find((item) => item.id === selectedObjectId.value) ?? null)
export const frameError = ref('')

// 亮度/对比度
export const brightness = ref(100)
export const contrast = ref(100)
export const mediaFilterStyle = computed(() => `brightness(${brightness.value}%) contrast(${contrast.value}%)`)
export const resetMediaFilter = () => { brightness.value = 100; contrast.value = 100 }

// 标注进度统计
export const annotatedFrameCount = computed(() => {
  const mediaId = currentMediaId.value
  return new Set((annotationsByMedia.value[mediaId] ?? []).map((o) => o.frameIndex ?? 0)).size
})

// Counters and flags that several workspace modules both read and reset.
export const runtime = {
  workspaceRestoreInProgress: false,
  exactFrameRequestSerial: 0,
  historySequence: 0,
  restartController: null as AbortController | null,
  restartEpoch: 0,
  seekSerial: 0,
  isSeekingVideo: false,
  bboxStart: null as { x: number; y: number } | null,
  trackingLoadSerial: 0,
}
