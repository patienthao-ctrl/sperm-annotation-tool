import { withRequestTimeout } from '../utils/browserCompat'
import { tokenStore } from './http'

export type ReviewStatus = 'none' | 'open' | 'passed' | 'stale'
export type CheckResult = 'ok' | 'empty'
/** One answer per target (a whole track). 'no' is followed by deleting the track. */
export type TargetResult = 'tail' | 'head' | 'unsure' | 'no'
/** How far a video without a review round has got. */
export type ReviewStage = 'unstarted' | 'annotating' | 'scanning' | 'ready'
/** Who the list shows beside a video, and who passed it. */
export interface ReviewPerson { actorId: number | null; actorName: string | null; passedBy: number | null; passedAt: string | null }
export interface ReviewChanges {
  added: number; removed: number; moved: number; reviewers: number[]
  frames: { frameIndex: number; added: number[]; removed: number[]; moved: number[]; checkedBy: number | null }[]
}
export interface ReviewRound {
  reviewId: string; round: number; state: 'open' | 'passed'; startedBy: number; startedAt: string
  lastActorId: number; lastActorName: string | null; lastActivityAt: string
  /** Frames confirmed earlier whose content has changed since: they count as unchecked. */
  changedFrames: [number, number][]
  /** Frames to check in this round as inclusive [start, end] spans; checkedFrames adds the result. */
  frames: [number, number][]; checkedFrames: [number, number, CheckResult][]
  checked: number; total: number; passedBy: number | null; passedAt: string | null; changes: ReviewChanges | null
  /** Target rounds (decision 0006): objectIds to judge, answers by objectId, targets changed after their answer. */
  policy?: string; targets?: number[]; results?: Record<string, TargetResult>; changedTargets?: number[]
  /** Frames without any annotation, still to confirm empty; and those confirmed. */
  unknownFrames?: [number, number][]; emptyFrames?: [number, number][]
  playthroughBy?: number | null; playthroughAt?: string | null
  repeats?: Record<string, TargetResult>; consistency?: { pairs: number; same: number }
}
export interface ReviewState {
  mediaId: string; status: ReviewStatus; stage?: ReviewStage | null; currentRevision: string | null; unknownFrames: number; notReady?: string; scanRemaining?: number
  review: ReviewRound | null
  lastPassed: { reviewId: string; round: number; passedBy: number; passedAt: string } | null
}
export interface RecordPerson { id: number; name: string }
/** One review round as the records page lists it; read from the database, never from tracking files. */
export interface ReviewRecord {
  reviewId: string; mediaId: string; mediaName: string; mediaDeleted: boolean; round: number; state: 'open' | 'passed'
  /** What a passed round means today; null while open or once the video is gone. */
  standing: 'current' | 'stale' | 'superseded' | null
  /** From the old doubt/sampling checklist: counts are items, not frames. */
  legacy: boolean
  startedBy: RecordPerson; startedAt: string; passedBy: RecordPerson | null; passedAt: string | null; lastActivityAt: string
  /** What checked/total count: whole targets since decision 0006, frames before. */
  unit?: 'targets' | 'frames'
  checked: number; total: number; emptyFrames: number
  verdicts?: { tail: number; head: number; unsure: number; no: number } | null
  reviewers: (RecordPerson & { frames: number })[]
  changes: { added: number; removed: number; moved: number; frames: number } | null
}
export interface ReviewRecordDetail {
  reviewId: string; legacy: boolean; unit?: 'targets' | 'frames'
  confirmations: { actor: RecordPerson; result: CheckResult | TargetResult; frames: number; spans: [number, number][]; targets?: number[]; lastAt: string }[]
  changes: { start: number; end: number; added: number[]; removed: number[]; moved: number[]; checkedBy: RecordPerson | null }[]
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
  check: (id: string, reviewId: string, frame: number, result: CheckResult) => request<ReviewState>(`${media(id)}/frames/${frame}`, 'PUT', { reviewId, result }),
  checkTarget: (id: string, reviewId: string, objectId: number, result: TargetResult, repeat = false) => request<ReviewState>(`${media(id)}/targets/${objectId}`, 'PUT', { reviewId, result, repeat }),
  playthrough: (id: string, reviewId: string) => request<ReviewState>(media(id) + '/playthrough', 'POST', { reviewId }),
  pass: (id: string, reviewId: string) => request<ReviewState>(media(id) + '/pass', 'POST', { reviewId }),
  summary: () => request<{ items: Record<string, ReviewStatus>; stages: Record<string, ReviewStage>; people: Record<string, ReviewPerson> }>('/annotation-review/summary'),
  records: () => request<{ items: ReviewRecord[]; total: number }>('/annotation-review/records'),
  record: (reviewId: string) => request<ReviewRecordDetail>(`/annotation-review/records/${encodeURIComponent(reviewId)}`),
  resetAnnotations: (id: string, revision: number, key: string) => request<{ ok: boolean; mediaId: string; revision: number; generationId: string }>(media(id) + '/reset-annotations', 'POST', { expectedRevision: revision, confirmDiscard: true }, key),
  exportPreview: (id: string) => request<CompletionPreview>(`/datasets/annotations/${encodeURIComponent(id)}/preview`),
}
