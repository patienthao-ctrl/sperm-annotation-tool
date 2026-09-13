import type { AnnotationObject, EffectResult, MediaType } from '../types/annotation'

/**
 * 前端与后端之间的“接口边界”。
 *
 * 这个文件只定义数据契约，不执行任何 HTTP 请求，也不依赖 FastAPI、Python、
 * PyTorch 或 SAM3.1。当前页面使用 mockAnnotationApi 独立运行。
 *
 * 后续接后端时，可以新增一个 httpAnnotationApi.ts，实现同一个 AnnotationApi
 * 接口，再在组件中把 api 替换掉即可；页面业务逻辑无需改动。
 */
export interface UploadMediaRequest {
  file: File
}

export interface UploadMediaResponse {
  mediaId: string
  objectKey: string
}

export interface SaveManualAnnotationRequest {
  mediaId: string
  mediaType: MediaType
  /** 视频固定为 0；图片可省略。 */
  mediaName?: string      // ← 加这行
  mediaWidth?: number     // ← 加这行
  mediaHeight?: number    // ← 加这行
  frameIndex?: number
  /** 视频第一帧固定为 0；图片可省略。 */
  timestampMs?: number
  objects: AnnotationObject[]
  annotationVersion: string
}

export interface SegmentRequest {
  mediaId: string
  mediaType: MediaType
  prompt: string
  frameIndex?: number
  timestampMs?: number
  seedObjects?: AnnotationObject[]
}

export interface TrackRequest {
  mediaId: string
  prompt: string
  /** 当前产品规则：视频 Tracking 从第一帧开始。 */
  startFrame: number
  endFrame: number
  seedObject: AnnotationObject
}

export interface TaskResponse {
  taskId: string
  status: 'queued' | 'running' | 'success' | 'failed'
  progress: number
  message?: string
}

export interface AnnotationApi {
  uploadMedia(input: UploadMediaRequest): Promise<UploadMediaResponse>
  saveManualAnnotation(input: SaveManualAnnotationRequest): Promise<{ id: string }>
  segment(input: SegmentRequest): Promise<{ objects: AnnotationObject[] }>
  track(input: TrackRequest): Promise<{ taskId: string }>
  getTask(taskId: string): Promise<TaskResponse>
  listEffects(): Promise<{ items: EffectResult[] }>
  getEffect(effectId: string): Promise<EffectResult>
}

/**
 * 独立运行模式：所有接口均为本地 Mock。
 * 页面启动不需要后端、不需要 .env、不需要网络，也不会发送任何 API 请求。
 */
export const mockAnnotationApi: AnnotationApi = {
  async uploadMedia({ file }) {
    await delay(180)
    return {
      mediaId: `media-${Date.now()}`,
      objectKey: `demo/${file.name}`,
    }
  },

  async saveManualAnnotation() {
    await delay(180)
    return { id: `annotation-${Date.now()}` }
  },

  async segment(input) {
    await delay(650)
    const isVideo = input.mediaType === 'video'
    return {
      objects: [
        {
          id: `ai-${Date.now()}`,
          name: input.prompt.trim() || 'rare sperm',
          source: 'ai',
          confidence: 0.96,
          point: { x: 51, y: 46 },
          bbox: { x: 43, y: 39, width: 15, height: 14 },
          frameIndex: isVideo ? 0 : undefined,
          timestampMs: isVideo ? 0 : undefined,
        },
      ],
    }
  },

  async track() {
    await delay(500)
    return { taskId: `task-${Date.now()}` }
  },

  async getTask(taskId) {
    return {
      taskId,
      status: 'success',
      progress: 100,
      message: '前端 Demo Tracking 已完成（Mock）',
    }
  },

  async listEffects() {
    return {
      items: [
        {
          id: 'effect-demo-001',
          sourceMediaId: 'video-demo-001',
          sourceMediaName: 'microfluidic-demo.mp4',
          resultName: 'Demo 视频 · 稀有精子识别效果',
          resultVideoUrl: '/demo/microfluidic-demo.mp4',
          createdAt: '2026-09-05T08:20:00.000Z',
          status: 'completed',
          summary: { detectedObjects: 8, extractedCandidates: 3, durationSeconds: 6 },
        },
        {
          id: 'effect-demo-002',
          sourceMediaId: 'video-demo-001',
          sourceMediaName: 'microfluidic-demo.mp4',
          resultName: 'Demo 视频 · Track 复核版',
          resultVideoUrl: '/demo/microfluidic-demo.mp4',
          createdAt: '2026-09-04T15:10:00.000Z',
          status: 'completed',
          summary: { detectedObjects: 5, extractedCandidates: 2, durationSeconds: 6 },
        },
      ],
    }
  },

  async getEffect(effectId) {
    const result = await this.listEffects()
    return result.items.find((item) => item.id === effectId) ?? result.items[0]
  },
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}
