import { withRequestTimeout } from '../utils/browserCompat'
import { tokenStore } from './http'

export type ReviewStatus = 'none' | 'open' | 'passed' | 'stale'
export type ItemKind = 'doubt' | 'count' | 'coverage' | 'unknown' | 'changed' | 'sample' | 'followup'
export type CheckResult = 'ok' | 'fixed' | 'empty' | 'not_target'
export type DoubtCategory = 'missing' | 'association' | 'exit' | 'position' | 'size' | 'static' | 'check'
export type ReviewSource = 'reverse' | 'disagreement' | 'detector' | 'track_ended' | 'size_jump' | 'oversized' | 'edge_exit'
export interface ReviewItem {
  id: string; kind: ItemKind; startFrame: number; endFrame: number; frame: number; message: string
  objectId?: number | null; objectIds?: number[]; labels?: string[]; candidates?: number[][]; bbox?: number[] | null; escalation?: boolean
  category?: DoubtCategory; source?: ReviewSource; tailObjectId?: number; adoptObjectId?: number; detectorAgrees?: boolean
  lastFrame?: number; lastBox?: number[]; ratio?: number; followupOf?: string
  anchor?: number[]; region?: string; index?: number; countInFrame?: number
  observations?: { frameIndex: number; objectIds: number[]; candidates: number[][] }[]
  check: { result: CheckResult; actorId: number; checkedAt: string } | null
}
export interface ReviewChanges {
  added: number; removed: number; moved: number; reviewers: number[]
  frames: { frameIndex: number; added: number[]; removed: number[]; moved: number[]; checkedBy: number | null }[]
}
export interface ReviewRound {
  reviewId: string; round: number; state: 'open' | 'passed'; startedBy: number; startedAt: string; escalated: boolean
  lastActorId: number; lastActivityAt: string
  items: ReviewItem[]; checked: number; total: number; passedBy: number | null; passedAt: string | null; changes: ReviewChanges | null
}
export interface ReviewState {
  mediaId: string; status: ReviewStatus; currentRevision: string | null; unknownFrames: number; notReady?: string
  review: ReviewRound | null
  lastPassed: { reviewId: string; round: number; passedBy: number; passedAt: string } | null
}
export interface CompletionPreview { sourceRevision: string; frameCount: number; objectFrames: number; emptyFrames: number; unknownFrames: number; unknownFrameRanges: { start: number; end: number }[] }
export interface Failure { message: string; status: number; code?: string; requestId?: string }

async function request<T>(path: string, method = 'GET', body?: unknown, key?: string): Promise<T> {
  return withRequestTimeout(30000, async (signal) => {
    const response = await fetch('/api' + path, {
      method, signal,
      headers: { Authorization: `Bearer ${tokenStore.get()}`, 'Content-Type': 'application/json', ...(key ? { 'X-Review-Contract': '2', 'Idempotency-Key': key } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      const failure: Failure = { status: response.status, message: data.message || (typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`), code: data.code, requestId: data.requestId || response.headers.get('X-Request-ID') || undefined }
      console.error('[annotation_review.request_failed]', { method, path, ...failure })
      throw failure
    }
    return data as T
  })
}

const media = (id: string) => `/annotation-review/media/${encodeURIComponent(id)}`
export const annotationReviewApi = {
  status: (id: string) => request<ReviewState>(media(id)),
  start: (id: string) => request<ReviewState>(media(id) + '/start', 'POST'),
  check: (id: string, reviewId: string, itemId: string, result: CheckResult) => request<ReviewState>(`${media(id)}/items/${encodeURIComponent(itemId)}`, 'PUT', { reviewId, result }),
  pass: (id: string, reviewId: string) => request<ReviewState>(media(id) + '/pass', 'POST', { reviewId }),
  summary: () => request<{ items: Record<string, ReviewStatus> }>('/annotation-review/summary'),
  resetAnnotations: (id: string, revision: number, key: string) => request<{ ok: boolean; mediaId: string; revision: number; generationId: string }>(media(id) + '/reset-annotations', 'POST', { expectedRevision: revision, confirmDiscard: true }, key),
  exportPreview: (id: string) => request<CompletionPreview>(`/datasets/annotations/${encodeURIComponent(id)}/preview`),
}
