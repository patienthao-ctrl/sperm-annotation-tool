import { withRequestTimeout } from '../utils/browserCompat'
import { tokenStore } from './http'

export type DatasetSplit = 'train' | 'val' | 'test'
export interface SplitOptions {
  splitRatio: number
  splitMode?: 'frame' | 'video'
  testRatio?: number
  videoSplits?: Record<string, DatasetSplit>
}
export interface ExportSettings extends SplitOptions {
  finalVersionIds: string[]
  format: 'yolo' | 'coco' | 'both'
  includeSourceVideos?: boolean
}
export interface ExportSummary {
  frameCount: number
  objectCount: number
  emptyFrames: number
  classNames: string[]
  splitMode?: 'frame' | 'video'
  splitFrames?: Record<DatasetSplit, number>
  videos?: { finalVersionId: string; frameCount: number; objectCount: number; split: DatasetSplit | null }[]
}
export interface ExportJob {
  exportId: string
  state: 'queued' | 'running' | 'ready' | 'failed' | 'invalidated'
  completedFrames: number
  totalFrames: number
  errorCode: string | null
  errorMessage: string | null
  downloadUrl: string | null
  manifest: { counts: ExportSummary & { train: number; val: number; test?: number } } | null
}

async function request<T>(path: string, method = 'GET', body?: unknown, key?: string): Promise<T> {
  try {
    return await withRequestTimeout(30000, async (signal) => {
      const response = await fetch('/api/datasets' + path, {
        method,
        signal,
        headers: {
          Authorization: `Bearer ${tokenStore.get()}`,
          'Content-Type': 'application/json',
          ...(key ? { 'X-Review-Contract': '2', 'Idempotency-Key': key } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw {
          message: data.message || `导出请求失败（${response.status}）`,
          code: data.code,
          status: response.status,
          requestId: data.requestId || response.headers.get('X-Request-ID'),
        }
      }
      return (await response.json()) as T
    })
  } catch (error) {
    console.error('[dataset.request_failed]', { path, method, error })
    throw error
  }
}

export interface ExportListItem {
  exportId: string; state: ExportJob['state'] | string; completedFrames: number; totalFrames: number
  errorMessage: string | null; createdAt: string; updatedAt: string; videoCount: number; mediaIds: string[]
}

export const trainingExportApi = {
  list: () => request<{ items: ExportListItem[]; exported: Record<string, string> }>('/exports'),
  remove: (id: string) => request<{ deleted: boolean }>(`/exports/${id}`, 'DELETE'),
  annotationSnapshot: (mediaId:string, body:unknown, key:string) => request<{finalVersionId:string}>(`/annotations/${mediaId}/snapshot`, 'POST', body, key),
  preview: (ids: string[], split?: SplitOptions) => request<ExportSummary>('/exports/preview', 'POST', { finalVersionIds: ids, ...split }),
  create: (settings: ExportSettings, key: string) => request<ExportJob>('/exports', 'POST', settings, key),
  status: (id: string) => request<ExportJob>(`/exports/${id}`),
}
