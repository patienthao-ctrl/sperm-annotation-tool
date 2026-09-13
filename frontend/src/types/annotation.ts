export type MediaType = 'image' | 'video'
export type AnnotationTool = 'point' | 'bbox'
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
  /** 后端为该视频建立的独立目录/资源 ID。 */
  serverMediaId?: string
  serverVideoName?: string
}

export interface AnnotationObject {
  id: string
  objectId?: number
  name: string
  source: ObjectSource
  confidence?: number
  point?: { x: number; y: number }
  bbox?: { x: number; y: number; width: number; height: number }
  /** 视频标注固定为第一帧，因此当前前端始终为 0。 */
  frameIndex?: number
  timestampMs?: number
  anomaly?: { type: string; ratio: number; prevArea: number; currArea: number }
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
