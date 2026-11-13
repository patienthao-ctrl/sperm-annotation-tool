<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { useAnnotationReview } from '../stores/annotationReview'
import { useAuth } from '../stores/auth'
import type { TargetResult } from '../api/annotationReviewApi'

// The review step's side panel: how many targets are still to answer, what the answers
// were, every target for jumping, and the pass with the one reason it cannot happen yet.
const props = defineProps<{
  mediaId: string | null; blocked: boolean; trackingPending: boolean; flush: () => Promise<unknown>
  lastEditor: string | null; nextVideo: string | null
  /** Check frames of the annotation step nobody scanned yet. */
  scanRemaining: number
  current: number | null
}>()
const emit = defineEmits<{ jump: [objectId: number]; nextVideo: []; toAnnotate: [] }>()
const review = useAnnotationReview()
const auth = useAuth()
const { state, busy, error, status } = review
const pending = ref(false)
let contextSerial = 0
watch([() => props.mediaId, () => auth.user.value?.id], () => { contextSerial++ }, { flush: 'sync' })
onBeforeUnmount(() => { contextSerial++ })

const round = computed(() => state.value?.status === 'open' ? state.value.review : null)
const results = computed(() => round.value?.results ?? {})
const changed = computed(() => new Set(round.value?.changedTargets ?? []))
const answered = computed(() => round.value?.checked ?? 0)
const total = computed(() => round.value?.total ?? 0)
const tally = computed(() => {
  const out: Record<TargetResult, number> = { tail: 0, head: 0, no: 0, unsure: 0 }
  for (const value of Object.values(results.value)) out[value]++
  return out
})
const unknown = computed(() => (round.value?.unknownFrames ?? []).reduce((n, [a, b]) => n + b - a + 1, 0) - (round.value?.emptyFrames ?? []).reduce((n, [a, b]) => n + b - a + 1, 0))
const why = computed(() => {
  if (!round.value) return ''
  if (answered.value < total.value) return `还有 ${total.value - answered.value} 个目标没确认`
  if (props.scanRemaining) return `第 1 步还有 ${props.scanRemaining} 张查漏帧没看`
  if (unknown.value > 0) return `还有 ${unknown.value} 帧没有标注，需要确认无目标`
  if (!round.value.playthroughAt) return '还差最后快速看一遍全片'
  return ''
})
const concurrentReviewer = computed(() => {
  const value = round.value
  if (!value || value.lastActorId == null) return null
  const me = Number(auth.user.value?.id)
  if (!Number.isFinite(me) || value.lastActorId === me || !value.lastActivityAt) return null
  const age = Date.now() - Date.parse(value.lastActivityAt)
  if (!Number.isFinite(age) || age < 0 || age > 10 * 60 * 1000) return null
  return { actor: value.lastActorName || `用户 #${value.lastActorId}`, minutes: Math.max(1, Math.floor(age / 60000)) }
})
const label: Record<TargetResult, string> = { tail: '有尾巴', head: '没尾巴', no: '不是', unsure: '看不清' }
const circumference = 2 * Math.PI * 26

async function perform(kind: 'start' | 'pass') {
  if (busy.value || pending.value || props.blocked || !props.mediaId) return
  const mediaId = props.mediaId, reviewId = round.value?.reviewId, serial = contextSerial
  if (kind === 'pass' && !reviewId) return
  pending.value = true
  try {
    await props.flush()
    if (serial !== contextSerial || mediaId !== props.mediaId) return
    if (kind === 'start') await review.start(mediaId); else await review.pass(mediaId, reviewId!)
  } catch (e) {
    if (serial === contextSerial) error.value = `标注保存失败，未记录本次审查操作：${(e as Error)?.message || '请重试'}`
  } finally { pending.value = false }
}
</script>

<template>
  <section class="shell-card review-panel" aria-label="审查" data-testid="review-panel">
    <p v-if="!mediaId" class="review-note">选择一个视频开始审查。</p>
    <p v-else-if="!state && !error" class="review-note">正在读取…</p>
    <template v-else-if="state">
      <div v-if="status === 'none' && state.stage === 'scanning'" class="review-task" data-testid="review-waits-scan">
        <div class="review-eyebrow">第 1 步还没做完</div>
        <h3>还在查漏</h3>
        <p class="review-note">还有 {{ state.scanRemaining }} 张查漏帧没看。查完、点“提交审查”后才能开始审查。</p>
        <button class="btn-primary review-wide review-big" @click="emit('toAnnotate')">回到标注查漏</button>
      </div>
      <div v-else-if="status === 'none' || status === 'stale'" class="review-task">
        <div class="review-eyebrow">第 2 步 · 审查</div>
        <h3>{{ status === 'stale' ? '有改动，需要重新确认' : '逐个确认目标' }}</h3>
        <p class="review-note">{{ status === 'stale' ? '只需确认改动过的目标。' : '每个目标看一次，回答“这是精子吗？”' }}</p>
        <p v-if="state.notReady" class="review-warning" role="status">{{ state.notReady }}</p>
        <p v-if="trackingPending" class="review-warning" role="status">旧的 AI 追踪还在等待核对，请先回到标注页处理。</p>
        <button class="btn-primary review-wide review-big" data-testid="review-start" :disabled="busy || pending || blocked || trackingPending || !!state.notReady" @click="perform('start')">{{ busy ? '正在开始…' : '开始审查' }}</button>
      </div>

      <div v-else-if="status === 'passed' && state.review" class="review-task done">
        <div class="review-done"><AppIcon name="check" :size="18" />审查通过</div>
        <p class="review-note">可以导出训练数据集了。之后再改动，会提示重新确认改过的目标。</p>
        <p v-if="state.review.changes" class="review-changes" data-testid="review-changes">本轮修改：移动 {{ state.review.changes.moved }} · 新增 {{ state.review.changes.added }} · 删除 {{ state.review.changes.removed }}</p>
        <button v-if="nextVideo" class="btn-primary review-wide" data-testid="review-next-video" :title="nextVideo" @click="emit('nextVideo')">下一段待审查</button>
      </div>

      <template v-else-if="round">
        <div class="review-task">
          <div class="review-eyebrow">第 2 步 · 审查</div>
          <div class="review-ring" aria-live="polite">
            <svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" class="track" /><circle cx="32" cy="32" r="26" class="fill" :stroke-dasharray="`${circumference * answered / Math.max(1, total)} ${circumference}`" /></svg>
            <div><strong class="num" data-testid="review-progress">{{ answered }} <small>/ {{ total }}</small></strong><span>个目标已确认</span></div>
          </div>
          <div class="review-tally">
            <span v-for="key in (['tail','head','no','unsure'] as const)" :key="key"><i :class="key" />{{ label[key] }}<b class="num">{{ tally[key] }}</b></span>
          </div>
        </div>
        <p v-if="concurrentReviewer" class="review-warning" role="status" data-testid="review-concurrent">{{ concurrentReviewer.actor }} 也在审查这段视频（{{ concurrentReviewer.minutes }} 分钟前），同时修改可能互相覆盖。</p>
        <button v-if="changed.size" class="review-warning review-changed" data-testid="review-changed" @click="emit('jump', [...changed][0])">{{ changed.size }} 个目标确认后又被改动<template v-if="lastEditor">（最后保存：{{ lastEditor }}）</template>，需要重新确认。</button>
        <button v-if="scanRemaining" class="review-warning review-changed" @click="emit('toAnnotate')">第 1 步还有 {{ scanRemaining }} 张查漏帧没看，点此回去。</button>
        <details class="review-list">
          <summary>全部目标 <span class="num">{{ total }}</span><AppIcon name="next" :size="12" /></summary>
          <div class="review-rows">
            <button v-for="oid in round.targets ?? []" :key="oid" class="review-row" :class="{ on: oid === current }" @click="emit('jump', oid)">
              <span class="review-badge">{{ oid }}</span>
              <span v-if="changed.has(oid)" class="review-state changed">改动后待确认</span>
              <span v-else-if="results[oid]" class="review-state"><i :class="results[oid]" />{{ label[results[oid]] }}</span>
              <span v-else class="review-state todo">未确认</span>
            </button>
          </div>
        </details>
        <div class="review-pass">
          <button class="btn-primary review-wide review-big" data-testid="review-pass" :disabled="!!why || busy || pending || blocked" @click="perform('pass')"><AppIcon name="check" :size="15" />{{ busy ? '正在处理…' : '审查通过' }}</button>
          <small v-if="why" data-testid="review-pass-why">{{ why }}</small>
        </div>
      </template>
    </template>
    <div v-if="error" class="error-banner" role="alert">{{ error }} <button class="quiet-button" :disabled="busy" @click="review.load(mediaId)">重新读取</button></div>
  </section>
</template>

<style scoped>
.review-panel { display:flex; flex-direction:column; gap:12px; min-height:0; flex:1 1 0; overflow-y:auto; overscroll-behavior:contain; animation:slide-in-right var(--dur-base) var(--ease-out); }
.review-task { display:grid; gap:10px; padding:14px; border-radius:var(--radius-lg); background:var(--accent-soft); }
.review-task h3 { margin:0; font-size:var(--fs-xl); letter-spacing:-.01em; }
.review-eyebrow { font-size:var(--fs-xs); font-weight:600; color:var(--accent); padding-right:30px; }
.review-note { margin:0; font-size:var(--fs-xs); color:var(--muted); line-height:1.5; }
.review-warning { margin:0; background:var(--warning-soft); color:var(--warning); border-radius:var(--radius-sm); padding:6px 8px; font-size:var(--fs-xs); animation:rise-in var(--dur-base) var(--ease-out); }
.review-changed { width:100%; border:0; text-align:left; cursor:pointer; }
.review-changes { margin:0; font-size:var(--fs-xs); color:var(--text); }
.review-wide { width:100%; }
.review-big { display:flex; align-items:center; justify-content:center; gap:8px; min-height:40px; font-size:var(--fs-md); }
.review-done { display:flex; align-items:center; gap:8px; font-size:var(--fs-lg); font-weight:650; color:var(--success); }
.review-ring { display:flex; align-items:center; gap:14px; }
.review-ring svg { width:64px; height:64px; transform:rotate(-90deg); flex:none; }
.review-ring circle { fill:none; stroke-width:7; }
.review-ring .track { stroke:var(--surface); }
.review-ring .fill { stroke:var(--accent); stroke-linecap:round; transition:stroke-dasharray var(--dur-slow) var(--ease-out); }
.review-ring strong { display:block; font-size:var(--fs-2xl); line-height:1.1; }
.review-ring strong small { font-size:var(--fs-sm); color:var(--muted); font-weight:500; }
.review-ring span { font-size:var(--fs-xs); color:var(--muted); }
.review-tally { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
.review-tally span { display:flex; align-items:center; gap:6px; padding:5px 8px; border-radius:var(--radius-sm); background:var(--surface); font-size:var(--fs-xs); color:var(--muted); }
.review-tally b { margin-left:auto; color:var(--text); }
i.tail, i.head, i.no, i.unsure { width:8px; height:8px; border-radius:50%; flex:none; display:inline-block; }
i.tail { background:#1d9a57; } i.head { background:#14918c; } i.no { background:#c2415a; } i.unsure { background:#c08a1e; }
.review-list { border:1px solid var(--line); border-radius:var(--radius-md); padding:8px 10px; }
.review-list summary { cursor:pointer; list-style:none; display:flex; align-items:center; gap:6px; font-weight:600; font-size:var(--fs-sm); }
.review-list summary::-webkit-details-marker { display:none; }
.review-list summary .num { color:var(--muted); font-weight:500; }
.review-list summary svg { margin-left:auto; transition:transform var(--dur-fast) var(--ease-out); }
.review-list[open] summary svg { transform:rotate(90deg); }
.review-rows { max-height:240px; overflow:auto; margin-top:6px; display:grid; gap:2px; }
.review-row { display:flex; align-items:center; gap:8px; padding:4px 6px; border:0; border-radius:var(--radius-sm); background:none; cursor:pointer; color:var(--text); font-size:var(--fs-xs); }
.review-row:hover { background:var(--surface-hover); }
.review-row.on { background:var(--accent-soft); }
.review-badge { min-width:30px; text-align:center; padding:0 5px; border:1px solid var(--line); border-radius:5px; font:600 11px var(--font-mono); }
.review-state { margin-left:auto; display:flex; align-items:center; gap:5px; color:var(--muted); }
.review-state.todo { color:var(--accent); } .review-state.changed { color:var(--warning); }
.review-pass { margin-top:auto; display:grid; gap:4px; text-align:center; }
.review-pass small { color:var(--muted); font-size:var(--fs-xs); }
</style>
