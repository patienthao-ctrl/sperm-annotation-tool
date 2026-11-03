import { withRequestTimeout } from '../utils/browserCompat'
import { notifyAuthExpired, tokenStore } from './http'
import type { TrackingFrameResult } from '../types/annotation'
import { validateTrackingResult } from '../annotation/trackingResult'

const jsonRequest = async <T>(path: string, init: RequestInit = {}): Promise<T> => withRequestTimeout(30000, async (signal) => {
  const res = await fetch(`/api${path}`, {
    ...init,
    signal,
    headers: {
      'Content-Type': 'application/json',
      ...(tokenStore.get() ? { Authorization: `Bearer ${tokenStore.get()}` } : {}),
      ...(init.headers ?? {}),
    },
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 401) notifyAuthExpired()
    const detail = (data as any)?.message || (data as any)?.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || `请求失败 (${res.status})`) as Error & { status: number; code?: string; requestId?: string }
    error.status = res.status
    error.code = data?.code || (typeof data?.detail === 'object' ? data.detail?.code : undefined)
    error.requestId = data?.requestId || res.headers.get('X-Request-ID') || undefined
    throw error
  }
  return data as T
}, init.signal)

export interface WorkspaceStamp {
  mediaId: string
  revision: number
  generationId?: string | null
  updatedBy?: number | null
  updatedByName?: string | null
  trackingStamp: number[] | null
  tracking: { userId: number | string | null; userName: string | null; status: 'queued' | 'running' } | null
}

export interface PackageImportResult {
  imported: (TrackUploadResponse & { objects: number; emptyFrames: number })[]
  skipped: { videoName: string; reason: string; mediaId?: string }[]
}

export interface TrackUploadResponse {
  mediaId: string
  videoName: string
  videoUrl: string
  width?: number
  height?: number
  duration?: number
  fps?: number
  frameCount?: number
  videoMimeType?: string
}

export interface DuplicateVideo {
  mediaId:string;videoName:string;canOverwrite:boolean;reason:string;workspaceRevision:number;media:TrackUploadResponse
}

export interface TrackRunResponse {
  taskId: string
  status: 'queued' | 'running' | 'success' | 'failed'
  maxFrames?: number
  trackFrames?: number
  seedFile?: string
}

export interface TrackingRestartInput {
  expectedRevision: number
  expectedPausedFrame: number
  startFrame: number
  confirmDiscardFuture: true
  generationId?: string
}
export interface TrackingRestartResponse {
  ok: true
  mediaId: string
  revision: number
  pausedAnomalies: []
  lastPausedContext: null
  cutoffFrame: number
  removedRows: number
  keptRows: number
  deletedFutureSeedFiles: 0
  overlayRegenerated: false
}


export interface TrackStatusResponse {
  jointTracking?: {state:'checked'|'failed';checkedFrames?:number;changedObjects?:number}
  endpointVerification?: {state:'checked'|'failed'|'insufficient';matches:Record<string,number>;checkedFrames:number}
  taskId: string
  status: 'queued' | 'running' | 'success' | 'failed' | 'paused'
  message?: string
  stage?: 'queued' | 'decoding' | 'preparing_model' | 'tracking' | 'checking' | 'saving_results' | 'paused' | 'completed'
  elapsedSeconds?: number
  /** Structured stage position for the progress bar (endpoint backward pass / combine). */
  phase?: 'backward' | 'combine'
  phaseFrom?: number
  phaseTo?: number
  phaseCurrent?: number
  paused?: boolean
  pausedFrame?: number
  pausedObjects?: Array<{
    object_id: number
    name?: string
    display_name?: string
    type: string
    reasons?: string[]
    details?: Record<string, unknown>
    currentBox?: number[] | null
    manualBaselineBox?: number[] | null
    manualBaselineFrame?: number | null
    metrics?: Record<string, number | null | undefined>
    reviewStartFrame?: number | null
    reviewEndFrame?: number | null
    reviewLookbackFrames?: number | null
    other_object_id?: number | null
    other_display_name?: string | null
    ratio?: number | null
    prevArea?: number | null
    currArea?: number | null
  }>
  anomalyLevels?: Record<string, string>
  processedFrames?: number
  lastProcessedFrame?: number
  reachedVideoEnd?: boolean
  warningSummary?: TrackingWarningSummary[]
}

export interface TrackingWarningSummary {
  objectId: number
  reason: string
  count: number
  firstFrame: number
  lastFrame: number
  calibrated: boolean
}

export interface NormalMotionSample {
  objectId: number
  frameIndex: number
  reason: 'motion'
  decision: 'normal'
  calibrate: boolean
  features: { motionNormalized: number; referenceKind?: 'head' | 'bbox' }
}

export interface ObjectDeletionSummary {
  objectId: number
  name: string
  frameCount: number
  manualCount: number
  aiCount: number
  firstFrame: number | null
  lastFrame: number | null
  totalCount: number
}

export interface TrackingFeedbackInput {
  scope?: 'object' | 'frame'
  expectedRevision: number
  objectId: number
  frameIndex: number
  decision: 'normal' | 'corrected' | 'reset'
  calibrate: boolean
}

export interface TrackingFeedbackEvent {
  scope?: 'object' | 'frame'
  checkpointId?: string
  objectId: number
  frameIndex: number
  decision: 'normal' | 'corrected' | 'reset'
  overlapReferences?: Array<{
    objectIds: [number, number]
    frameIndex: number
    bboxes: [[number, number, number, number], [number, number, number, number]]
    source: 'confirmed-normal'
  }>
  geometryReference?: {
    checkpoint?: boolean
    objectId: number
    frameIndex: number
    bbox: [number, number, number, number]
    source: 'confirmed-normal'
  }
}

export interface TrackingFeedbackResponse {
  ok: boolean
  mediaId: string
  revision: number
  normalMotionSamples: NormalMotionSample[]
  trackingFeedbackEvents?: TrackingFeedbackEvent[]
  pausedAnomalies: unknown[]
  lastPausedContext: { mediaId: string; frameIndex: number } | null
}

export interface TrackResultFile {
  state?: 'not_generated' | 'available'
  format?: string
  media?: Record<string, unknown>
  tracking?: Record<string, unknown>
  frames: TrackingFrameResult[]
  count: number
}

export interface TrackMediaItem {
  mediaId: string
  directoryName: string
  sourceVideoName: string
  videoName: string
  videoUrl: string
  videoMimeType?: string
  hasTrackingResult: boolean | null
  trackingResultState: 'not_generated' | 'present' | 'missing'
  hasWorkspaceState?: boolean
  fps?: number
  width?: number
  height?: number
  frameCount?: number
  duration?: number
}

export interface TrackWorkspaceState {
  exists: boolean
  format?: 'annotation-workspace-v1'
  mediaId: string
  frontendMediaId?: string
  updatedAt?: string
  revision?: number
  expectedRevision?: number
  generationId?: string
  currentFrame?: number
  manualAnnotations?: unknown[]
  manualBaselines?: Array<{
    objectId: number
    name?: string
    source: 'manual'
    frameIndex: number
    bbox: [number, number, number, number]
  }>
  deletedTrackingIds?: string[]
  deletedObjectIds?: number[]
  deletedFrameObjects?: Array<{ objectId: number; frameIndex: number }>
  normalMotionSamples?: NormalMotionSample[]
  trackingFeedbackEvents?: TrackingFeedbackEvent[]
  anomalyFrames?: Array<{ frame_index: number; level: string; reasons: string[] }>
  pausedAnomalies?: unknown[]
  lastPausedContext?: { mediaId: string; frameIndex: number } | null
  display?: { brightness?: number; contrast?: number; zoom?: number }
  editor?: {
    activeTool?: 'select' | 'point' | 'bbox'
    objectNameInput?: string
    selectedObjectId?: string | null
  }
}

export const trackApi = {
  headBox: (mediaId: string, frameIndex: number, x: number, y: number, signal?: AbortSignal) =>
    jsonRequest<{bbox:number[];confidence:number;frameIndex:number;width:number;height:number}>(`/track/head-box/${encodeURIComponent(mediaId)}`, {method:'POST', body:JSON.stringify({frameIndex,x,y}), signal}),
  async uploadVideo(file: File): Promise<TrackUploadResponse> {
    const form = new FormData()
    form.append('file', file, file.name)
    const res = await fetch('/api/track/upload', {
      method: 'POST',
      headers: {
        ...(tokenStore.get() ? { Authorization: `Bearer ${tokenStore.get()}` } : {}),
      },
      body: form,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      if (res.status === 401) notifyAuthExpired()
      throw Object.assign(new Error((data as any)?.message || (data as any)?.detail || `视频上传失败 (${res.status})`), {status:res.status,code:data.code,duplicate:data.duplicate as DuplicateVideo|undefined})
    }
    return data as TrackUploadResponse
  },

  async importPackage(file: File): Promise<PackageImportResult> {
    const form = new FormData()
    form.append('file', file, file.name)
    const res = await fetch('/api/track/import-package', {
      method: 'POST',
      headers: { ...(tokenStore.get() ? { Authorization: `Bearer ${tokenStore.get()}` } : {}) },
      body: form,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      if (res.status === 401) notifyAuthExpired()
      throw Object.assign(new Error((data as any)?.detail || `标注包导入失败 (${res.status})`), {status:res.status})
    }
    if (!Array.isArray((data as any).imported) || !Array.isArray((data as any).skipped)) throw new Error('服务器未返回有效导入结果，请刷新素材列表确认')
    return data as PackageImportResult
  },

  async saveFrameAnnotations(input: {
    mediaId: string
    generationId?: string
    mediaName: string
    mediaWidth: number
    mediaHeight: number
    frameIndex: number
    timestampMs: number
    annotations: unknown[]
  }) {
    return jsonRequest<{ filename: string; path?: string; frameIndex: number }>('/track/annotations', {
      method: 'POST', body: JSON.stringify(input),
    })
  },


  async rewind(input: { mediaId: string; startFrame: number; generationId?: string }) {
    return jsonRequest<{
      ok: boolean
      mediaId: string
      cutoffFrame: number
      keptRows: number
      removedRows: number
      deletedFutureSeedFiles: number
      overlayRegenerated: boolean
    }>('/track/rewind', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  },

  async restartBranch(mediaId: string, input: TrackingRestartInput, requestKey: string, signal?: AbortSignal) {
    const result = await jsonRequest<TrackingRestartResponse>(`/track/restart-branch/${encodeURIComponent(mediaId)}`, {
      method: 'POST', signal, headers: { 'Idempotency-Key': requestKey }, body: JSON.stringify(input),
    })
    if (result.ok !== true || result.mediaId !== mediaId || result.cutoffFrame !== input.startFrame
      || !Number.isSafeInteger(result.revision) || result.revision < 0
      || !Array.isArray(result.pausedAnomalies) || result.pausedAnomalies.length || result.lastPausedContext !== null
      || !Number.isSafeInteger(result.removedRows) || result.removedRows < 0
      || !Number.isSafeInteger(result.keptRows) || result.keptRows < 0) {
      throw new Error('服务器未返回有效分支回退结果，请重试原请求')
    }
    return result
  },

  async run(input: {
    mediaId: string
    generationId?: string
    mediaName: string
    mediaWidth: number
    mediaHeight: number
    startFrame: number
    maxFrames?: number
    edgeDiscovery?: boolean; reviewMode?: boolean
    terminalAnnotations?: unknown[]
    combineResults?: boolean
    annotations: unknown[]
  }, requestKey?: string) {
    const result=await jsonRequest<TrackRunResponse>('/track', { method: 'POST', headers: requestKey ? {'Idempotency-Key': requestKey} : {}, body: JSON.stringify(input) })
    if(typeof result.taskId!=='string'||!result.taskId)throw new Error('服务器未返回有效追踪任务，请重试原追踪请求')
    return result
  },

  async getStatus(taskId: string) {
    const result=await jsonRequest<TrackStatusResponse>(`/track/status/${encodeURIComponent(taskId)}`)
    if(!['queued','running','success','failed','paused'].includes(result.status))throw new Error('服务器未返回有效追踪状态，请重新查询原任务')
    const e=result.endpointVerification
    if(e && (!['checked','failed','insufficient'].includes(e.state)||!e.matches||typeof e.matches!=='object'||Array.isArray(e.matches)||!Object.values(e.matches).every(v=>Number.isSafeInteger(v)&&v>0)||!Number.isSafeInteger(e.checkedFrames)||e.checkedFrames<0))throw new Error('首尾核验状态结构无效，请重新查询原任务')
    const joint=result.jointTracking
    if(joint && (!['checked','failed'].includes(joint.state)||(joint.state==='checked'&&(!Number.isSafeInteger(joint.checkedFrames)||joint.checkedFrames!<0))))throw new Error('综合标注状态无效，请重新查询原任务')
    return result
  },

  async getFrameBlob(mediaId: string, frameIndex: number): Promise<Blob> {
    return withRequestTimeout(20000, async (signal) => {
      const res = await fetch(`/api/track/frame/${encodeURIComponent(mediaId)}/${encodeURIComponent(frameIndex)}`, {
        signal,
        headers: {
          ...(tokenStore.get() ? { Authorization: `Bearer ${tokenStore.get()}` } : {}),
        },
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      if (res.status === 401) notifyAuthExpired()
      throw new Error((data as any)?.message || (data as any)?.detail || `读取第 ${frameIndex} 帧失败 (${res.status})`)
    }
    return res.blob()
    })
  },

  async getResult(mediaId: string, frameIndex?: number, required = false) {
    const params = new URLSearchParams()
    if (frameIndex != null) params.set('frameIndex', String(frameIndex))
    if (required) params.set('required', 'true')
    const query = params.toString() ? `?${params}` : ''
    const result = await jsonRequest<unknown>(`/track/result/${encodeURIComponent(mediaId)}${query}`)
    validateTrackingResult(result)
    return result
  },

  async getWorkspaceStamp(mediaId: string) {
    const result = await jsonRequest<WorkspaceStamp>(`/track/workspace/${encodeURIComponent(mediaId)}/stamp`)
    if (!Number.isInteger(result.revision) || result.revision < 0) throw new Error('服务器未返回有效工作区版本')
    return result
  },

  async getWorkspaceState(mediaId: string) {
    return jsonRequest<TrackWorkspaceState>(`/track/workspace/${encodeURIComponent(mediaId)}`)
  },

  async saveWorkspaceState(mediaId: string, state: Omit<TrackWorkspaceState, 'exists' | 'mediaId'>, requestKey?: string) {
    return jsonRequest<{
      ok: boolean
      mediaId: string
      filename: string
      manualAnnotationCount: number
      manualBaselineCount: number
      revision: number
    }>(`/track/workspace/${encodeURIComponent(mediaId)}`, {
      method: 'PUT',
      headers: requestKey ? { 'Idempotency-Key': requestKey } : {},
      body: JSON.stringify(state),
    })
  },

  async getObjectDeletionSummary(mediaId: string, objectId: number) {
    return jsonRequest<ObjectDeletionSummary>(`/track/deletion-preview/${encodeURIComponent(mediaId)}/${objectId}`)
  },

  async submitFeedback(mediaId: string, input: TrackingFeedbackInput, requestKey: string) {
    return jsonRequest<TrackingFeedbackResponse>(`/track/feedback/${encodeURIComponent(mediaId)}`, {
      method: 'POST', headers: { 'Idempotency-Key': requestKey }, body: JSON.stringify(input),
    })
  },

  async deleteMedia(mediaId: string) {
    return jsonRequest<{ deleted: boolean; mediaId: string }>(`/track/media/${encodeURIComponent(mediaId)}`, { method: 'DELETE' })
  },

  async listMedia() {
    return jsonRequest<{ items: TrackMediaItem[] }>('/track/media')
  },

}
