export type MediaType = 'image' | 'video'
export type AnnotationTool = 'select' | 'point' | 'bbox' | 'head'
export type ObjectSource = 'manual' | 'ai'
export type TaskStatus = 'queued' | 'running' | 'success' | 'failed'

export interface TrackingFrameObject {
  id: string
  objectId?: number
  name: string
  source: 'sam3' | 'ai' | 'manual'
  confidence?: number
  bbox?: [number, number, number, number]
  frameIndex: number
  timestampMs?: number
  anomaly?: { type: string; ratio: number; prevArea: number; currArea: number }
}

export interface TrackingFrameResult {
  frameIndex: number
  timestampMs: number
  annotations: TrackingFrameObject[]
}

export interface MediaAsset {
  id: string
  name: string
  type: MediaType
  url: string
  width?: number
  height?: number
  duration?: number
  fps?: number
  sizeBytes?: number
  frameCount?: number
  /** 后端为该视频建立的独立目录/资源 ID。 */
  serverMediaId?: string
  serverVideoName?: string
  frameFallback?: boolean
}

export interface AnnotationObject {
  /** Independent terminal-local ID before unique trajectory reconciliation. */
  endpointOriginalObjectId?: number
  endpointAnchorHash?: string
  id: string
  objectId?: number
  name: string
  source: ObjectSource
  /** Review-only provenance; never changes geometry or the stable objectId. */
  origin?: 'review_adopted'
  reviewAdoptionKey?: string
  creationId?: string
  collaborationOriginalObjectId?: number
  confidence?: number
  point?: { x: number; y: number }
  bbox?: { x: number; y: number; width: number; height: number }
  /** 原始视频帧号，从 0 开始；界面显示从 1 开始。 */
  frameIndex?: number
  timestampMs?: number
  anomaly?: { type: string; ratio: number; prevArea: number; currArea: number }
  anomaly_level?: 'normal' | 'warning' | 'anomaly' | 'disappeared'
  anomaly_reasons?: string[]
  anomaly_details?: Record<string, unknown>
}

export interface FrameAnnotations {
  frameIndex: number
  timestampMs: number
  objects: AnnotationObject[]
}

export interface SavedAnnotationFile {
  mediaId: string
  mediaName: string
  mediaType: MediaType
  frameIndex?: number
  timestampMs?: number
  savedAt: string
  filename: string
  /** 后端保存批次号，用于结果页去重，避免重复挂载时再次追加同一批记录。 */
  batchId?: string
  username?: string | null
  objects: AnnotationObject[]
}

export interface EffectResult {
  id: string
  sourceMediaId: string
  sourceMediaName: string
  resultName: string
  resultVideoUrl: string
  createdAt: string
  status: 'completed' | 'processing' | 'failed'
  summary: {
    detectedObjects: number
    extractedCandidates: number
    durationSeconds: number
  }
}

export interface AnnotationTask {
  taskId: string
  status: TaskStatus
  progress: number
  message?: string
}
