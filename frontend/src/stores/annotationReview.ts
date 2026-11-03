import { computed, ref } from 'vue'
import { useAuth } from './auth'
import { annotationReviewApi as api, type CheckResult, type Failure, type ReviewState, type ReviewStatus } from '../api/annotationReviewApi'

// One review state for the selected server video, shared by the review panel, the
// frame strip, the header status and the export gate.
const state = ref<ReviewState | null>(null)
const summary = ref<Record<string, ReviewStatus>>({})
const busy = ref(false)
const error = ref('')
let loadSeq = 0

const status = computed<ReviewStatus | null>(() => state.value?.status ?? null)
const openRound = computed(() => state.value?.status === 'open' ? state.value.review : null)
// Frames this round asks a person to check, and the result recorded for each checked one.
const targetFrames = computed<number[]>(() => (openRound.value?.frames ?? []).flatMap(([start, end]) => Array.from({ length: end - start + 1 }, (_, i) => start + i)))
const checkedFrames = computed(() => {
  const out = new Map<number, CheckResult>()
  for (const [start, end, result] of openRound.value?.checkedFrames ?? []) for (let f = start; f <= end; f++) out.set(f, result)
  return out
})
/** First unchecked frame after ``from`` (wrapping), or null when all are checked. */
function nextUnchecked(from: number) {
  const todo = targetFrames.value.filter(f => !checkedFrames.value.has(f))
  return todo.find(f => f > from) ?? todo[0] ?? null
}

function describe(e: unknown) {
  const f = e as Failure
  return `${f?.message || '审查请求失败，请重试'}${f?.requestId ? `（记录号 ${f.requestId}）` : ''}`
}

function apply(next: ReviewState) {
  state.value = next
  summary.value = { ...summary.value, [next.mediaId]: next.status }
}

async function load(mediaId: string | null | undefined) {
  const seq = ++loadSeq
  if (!mediaId) { state.value = null; return }
  if (state.value?.mediaId !== mediaId) state.value = null
  try {
    const next = await api.status(mediaId)
    if (seq === loadSeq) { apply(next); error.value = '' }
  } catch (e) {
    if (seq === loadSeq) error.value = describe(e)
  }
}

async function run(mediaId: string, reviewId: string | null, action: () => Promise<ReviewState>) {
  if (busy.value || state.value?.mediaId !== mediaId || (reviewId && state.value?.review?.reviewId !== reviewId)) return false
  const seq = ++loadSeq, actor = useAuth().user.value?.id
  const current = () => seq === loadSeq && actor === useAuth().user.value?.id && state.value?.mediaId === mediaId && (!reviewId || state.value?.review?.reviewId === reviewId)
  busy.value = true; error.value = ''
  try {
    const next = await action()
    if (!current()) return false
    apply(next); return true
  } catch (e) {
    console.warn('[annotation_review.action_failed]', {mediaId,reviewId,error:e})
    if (current()) error.value = describe(e)
    return false
  } finally { busy.value = false }
}

async function loadSummary() {
  try { summary.value = (await api.summary()).items }
  catch (e) { console.warn('[annotation_review.summary_failed]', e) }
}

export function useAnnotationReview() {
  return {
    state, summary, busy, error, status, targetFrames, checkedFrames, nextUnchecked,
    load, loadSummary,
    start: (mediaId: string) => run(mediaId, null, () => api.start(mediaId)),
    check: (mediaId: string, reviewId: string, frame: number, result: CheckResult) => run(mediaId, reviewId, () => api.check(mediaId, reviewId, frame, result)),
    pass: (mediaId: string, reviewId: string) => run(mediaId, reviewId, () => api.pass(mediaId, reviewId)),
  }
}

export const reviewStatusLabel: Record<ReviewStatus, string> = {
  none: '待审查', open: '审查中', passed: '审查通过', stale: '需重新审查',
}
