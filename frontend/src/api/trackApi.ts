import { tokenStore } from './http'
import type { TrackingFrameResult } from '../types/annotation'

const jsonRequest = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(tokenStore.get() ? { Authorization: `Bearer ${tokenStore.get()}` } : {}),
      ...(init.headers ?? {}),
    },
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data as any)?.message || (data as any)?.detail || `请求失败 (${res.status})`)
  return data as T
}

export interface TrackUploadResponse {
  mediaId: string
  videoName: string
  videoUrl: string
  width?: number
  height?: number
  duration?: number
  fps?: number
}

export interface TrackRunResponse {
  taskId: string
  status: 'queued' | 'running' | 'success' | 'failed'
  maxFrames?: number
  trackFrames?: number
  seedFile?: string
}

export interface TrackStatusResponse {
  taskId: string
  status: 'queued' | 'running' | 'success' | 'failed' | 'paused'
  message?: string
  paused?: boolean
  pausedFrame?: number
  pausedObjects?: Array<{ object_id: number; type: string; ratio: number; prevArea: number; currArea: number }>
  anomalyLevels?: Record<string, string>
  processedFrames?: number
}

export interface TrackResultFile {
  format?: string
  media?: Record<string, unknown>
  tracking?: Record<string, unknown>
  frames: TrackingFrameResult[]
}

export const trackApi = {
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
    if (!res.ok) throw new Error((data as any)?.message || (data as any)?.detail || `视频上传失败 (${res.status})`)
    return data as TrackUploadResponse
  },

  async saveFrameAnnotations(input: {
    mediaId: string
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

  async run(input: {
    mediaId: string
    mediaName: string
    mediaWidth: number
    mediaHeight: number
    startFrame: number
    maxFrames?: number
    annotations: unknown[]
  }) {
    return jsonRequest<TrackRunResponse>('/track', { method: 'POST', body: JSON.stringify(input) })
  },

  async getStatus(taskId: string) {
    return jsonRequest<TrackStatusResponse>(`/track/status/${encodeURIComponent(taskId)}`)
  },

  async getResult(mediaId: string, frameIndex?: number) {
    const query = frameIndex == null ? '' : `?frameIndex=${encodeURIComponent(frameIndex)}`
    return jsonRequest<TrackResultFile | TrackingFrameResult>(`/track/result/${encodeURIComponent(mediaId)}${query}`)
  },

  async listMedia() {
    return jsonRequest<{ items: Array<{ mediaId: string; videoName: string; videoUrl: string; hasTrackingResult: boolean; fps?: number; width?: number; height?: number; frameCount?: number }> }>('/track/media')
  },

  async deleteMedia(mediaId: string) {
    return jsonRequest<{ deleted: boolean }>(`/track/media/${encodeURIComponent(mediaId)}`, { method: 'DELETE' })
  },
}
