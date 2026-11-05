<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { useAnnotationReview } from '../stores/annotationReview'
import { useAuth } from '../stores/auth'
import type { CheckResult } from '../api/annotationReviewApi'

// Every frame of the round is checked by a person: look, fix in place, confirm, next.
const props = defineProps<{
  mediaId: string | null; currentFrame: number; frameCount: number; objectCount: number; frameReady: boolean
  blocked: boolean; trackingPending: boolean; flush: () => Promise<unknown>; lastEditor: string | null
}>()
const emit = defineEmits<{ seek: [frame: number] }>()
const review = useAnnotationReview()
const auth = useAuth()
const { state, busy, error, status, targetFrames, checkedFrames, nextUnchecked } = review
const pending=ref(false)
let contextSerial=0
watch([()=>props.mediaId,()=>auth.user.value?.id],()=>{contextSerial++},{flush:'sync'})
onBeforeUnmount(()=>{contextSerial++})

const round = computed(() => state.value?.status === 'open' ? state.value.review : null)
const remaining = computed(() => round.value ? round.value.total - round.value.checked : 0)
const canPass = computed(() => !!round.value && remaining.value === 0)
const concurrentReviewer = computed(() => {
  const value = round.value
  if (!value || value.lastActorId == null) return null
  const me = Number(auth.user.value?.id)
  if (!Number.isFinite(me) || value.lastActorId === me || !value.lastActivityAt) return null
  const age = Date.now() - Date.parse(value.lastActivityAt)
  if (!Number.isFinite(age) || age < 0 || age > 10 * 60 * 1000) return null
  return { actor: value.lastActorName || `用户 #${value.lastActorId}`, minutes: Math.max(1, Math.floor(age / 60000)) }
})
const firstChanged = computed(() => round.value?.changedFrames[0]?.[0] ?? null)
const changedCount = computed(() => (round.value?.changedFrames ?? []).reduce((total, [start, end]) => total + end - start + 1, 0))

const targets = computed(() => new Set(targetFrames.value))
const inRound = computed(() => targets.value.has(props.currentFrame))
const currentResult = computed(() => checkedFrames.value.get(props.currentFrame) ?? null)
const resultLabel: Record<CheckResult, string> = { ok: '已确认', empty: '已确认无目标' }
const empty = computed(() => props.objectCount === 0)
const confirmLabel = computed(() => empty.value ? '确认无目标，下一帧' : '确认本帧，下一帧')
const canConfirm = computed(() => !!round.value && inRound.value && props.frameReady && !busy.value && !pending.value && !props.blocked)
// A few unchecked stretches to jump to; the frame strip shows all of them.
const uncheckedSpans = computed(() => {
  const out: [number, number][] = []
  for (const f of targetFrames.value) {
    if (checkedFrames.value.has(f)) continue
    const last = out[out.length - 1]
    if (last && last[1] === f - 1) last[1] = f; else out.push([f, f])
  }
  return out
})
const spanText = ([a, b]: [number, number]) => a === b ? `第 ${a + 1} 帧` : `第 ${a + 1}–${b + 1} 帧`

async function perform(kind: 'start'|'check'|'pass') {
  if(busy.value||pending.value||props.blocked||!props.mediaId)return
  const mediaId=props.mediaId,reviewId=round.value?.reviewId,serial=contextSerial,frame=props.currentFrame
  if(kind!=='start'&&!reviewId)return
  if(kind==='check'&&!canConfirm.value)return
  const result:CheckResult=empty.value?'empty':'ok'
  const current=()=>serial===contextSerial && mediaId===props.mediaId && (kind==='start' || reviewId===round.value?.reviewId)
  pending.value=true
  try {
    await props.flush()
    if(!current()||(kind==='check'&&frame!==props.currentFrame))return
    const ok=kind==='start'?await review.start(mediaId):kind==='pass'?await review.pass(mediaId,reviewId!):await review.check(mediaId,reviewId!,frame,result)
    if(!ok||!current()||kind==='pass')return
    const next=nextUnchecked(kind==='start'?-1:frame)
    if(next!=null)emit('seek',next)
  } catch(e) {
    if(current())error.value=`标注保存失败，未记录本次审查操作：${(e as Error)?.message||'请重试'}`
  } finally {pending.value=false}
}
const start=()=>perform('start')
const confirm=()=>perform('check')
const pass=()=>perform('pass')
function next() {
  const frame = nextUnchecked(props.currentFrame)
  if (frame != null && round.value) emit('seek', frame)
}
defineExpose({ confirm, next })
</script>

<template>
  <section class="shell-card review-panel" aria-label="审查" data-testid="review-panel">
    <div class="shell-card-head">
      <AppIcon name="check" :size="16" /><strong>审查</strong>
      <span v-if="status" class="review-status" :class="status" data-testid="review-status">{{ review.label(mediaId, status) }}</span>
    </div>
    <p v-if="!mediaId" class="review-note">选择一个视频开始审查。</p>
    <p v-else-if="!state && !error" class="review-note">正在读取…</p>
    <template v-else-if="state">
      <template v-if="status === 'none' || status === 'stale'">
        <p class="review-note">{{ status === 'stale' ? '通过后标注又改过，只需逐帧检查改动过的帧。' : '逐帧检查整段视频：每个框是否贴合精子头，有没有漏标、多框或跟错。发现错误直接在画面上改，再确认这一帧。' }}</p>
        <p v-if="state.notReady" class="review-warning" role="status">{{ state.notReady }}</p>
        <p v-if="trackingPending" class="review-warning" role="status">AI 追踪还在等待核对，请先回到标注页处理。</p>
        <button class="btn-primary review-wide" data-testid="review-start" :disabled="busy || pending || blocked || trackingPending || !!state.notReady" @click="start">{{ busy ? '正在开始…' : status === 'stale' ? '开始重新审查' : '开始审查' }}</button>
      </template>

      <template v-else-if="status === 'passed' && state.review">
        <p class="review-note">已通过，可以导出训练数据集。之后再改标注需要重新审查。</p>
        <p v-if="state.review.changes" class="review-changes" data-testid="review-changes">本轮修改：移动 {{ state.review.changes.moved }} · 新增 {{ state.review.changes.added }} · 删除 {{ state.review.changes.removed }}</p>
      </template>

      <template v-else-if="round">
        <div class="review-progress" aria-live="polite">
          <progress :max="Math.max(1, round.total)" :value="round.checked" /><span data-testid="review-progress">{{ round.checked }} / {{ round.total }} 帧</span>
        </div>
        <p v-if="concurrentReviewer" class="review-warning" role="status" data-testid="review-concurrent">{{ concurrentReviewer.actor }} 也在审查这段视频（最近一次操作 {{ concurrentReviewer.minutes }} 分钟前），同时修改可能互相覆盖。</p>
        <button v-if="firstChanged != null" class="review-warning review-changed" data-testid="review-changed" @click="emit('seek', firstChanged)">{{ changedCount > 1 ? `${changedCount} 帧在确认后被改动，从第 ${firstChanged + 1} 帧起` : `第 ${firstChanged + 1} 帧在确认后被改动` }}<template v-if="lastEditor">（最后保存：{{ lastEditor }}）</template>，需要重新确认。点此定位。</button>

        <div class="review-current" data-testid="review-current" :data-frame="currentFrame">
          <div class="review-current-head">
            <span class="review-kind">第 {{ currentFrame + 1 }} 帧</span>
            <span class="review-result" :class="currentResult ?? (inRound ? 'todo' : 'outside')" data-testid="review-frame-state">{{ currentResult ? resultLabel[currentResult] : inRound ? '未检查' : '不在本轮' }}</span>
          </div>
          <template v-if="inRound">
            <p class="review-question">{{ empty ? '本帧没有任何框。画面里有精子就补标；确实没有精子，确认无目标。' : `本帧 ${objectCount} 个框。逐个看是否贴合精子头，再看有没有漏标、多框或跟错；有错直接在画面上改好再确认。` }}</p>
            <p v-if="!frameReady" class="review-warning" role="status">画面尚未加载完成，加载后才能确认。</p>
            <button class="btn-primary review-wide review-confirm" data-testid="review-confirm" :aria-label="confirmLabel" :title="`${confirmLabel}（快捷键 1）`" :disabled="!canConfirm" @click="confirm">
              <span>{{ confirmLabel }}</span><kbd aria-hidden="true">1</kbd>
            </button>
            <p v-if="currentResult" class="review-checked" data-testid="review-checked">已确认过；修改后需要再次确认。</p>
          </template>
          <p v-else class="review-question">这一帧没有改动，不需要检查。</p>
          <button v-if="remaining" class="quiet-button review-next" data-testid="review-next" title="下一未检查帧（快捷键 N）" @click="next"><AppIcon name="next" :size="14" />下一未检查帧<kbd aria-hidden="true">N</kbd></button>
        </div>

        <button class="btn-primary review-wide" data-testid="review-pass" :disabled="!canPass || busy || pending || blocked" @click="pass">{{ busy ? '正在处理…' : canPass ? '审查通过' : `还剩 ${remaining} 帧` }}</button>

        <div v-if="uncheckedSpans.length" class="review-spans" data-testid="review-unchecked">
          <small>未检查</small>
          <button v-for="span in uncheckedSpans.slice(0, 8)" :key="span[0]" class="review-span" @click="emit('seek', span[0])">{{ spanText(span) }}</button>
          <small v-if="uncheckedSpans.length > 8">等 {{ uncheckedSpans.length }} 段</small>
        </div>
      </template>
    </template>
    <div v-if="error" class="error-banner" role="alert">{{ error }} <button class="quiet-button" :disabled="busy" @click="review.load(mediaId)">重新读取</button></div>
  </section>
</template>

<style scoped>
.review-panel { display: flex; flex-direction: column; gap: 10px; min-height: 0; flex: 1 1 0; overflow-y: auto; overscroll-behavior: contain; }
.review-status { margin-left: auto; font-size: 12px; font-weight: 600; padding: 1px 9px; border-radius: 10px; background: var(--surface-subtle); color: var(--muted); }
.review-status.open { background: var(--accent-soft); color: var(--accent); }
.review-status.passed { background: var(--b-soft); color: var(--success); }
.review-status.stale { background: var(--warning-soft); color: var(--warning); }
.review-note { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
.review-warning { margin: 0; background: var(--warning-soft); color: var(--warning); border-radius: 6px; padding: 6px 8px; font-size: 12px; }
.review-changes { margin: 0; font-size: 12px; color: var(--text); }
.review-wide { width: 100%; }
.review-progress { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.review-progress progress { flex: 1; height: 6px; appearance: none; -webkit-appearance: none; border: 0; border-radius: 3px; background: var(--line); overflow: hidden; }
.review-progress progress::-webkit-progress-bar { background: var(--line); border-radius: 3px; }
.review-progress progress::-webkit-progress-value { background: linear-gradient(90deg, var(--accent), var(--success)); border-radius: 3px; transition: width var(--dur-slow) var(--ease-out); }
.review-progress progress::-moz-progress-bar { background: var(--accent); border-radius: 3px; }
.review-current { border: 1px solid var(--accent); border-radius: var(--radius-md); padding: 12px; background: var(--surface); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow-sm); }
.review-current-head { display: flex; align-items: baseline; gap: 8px; }
.review-kind { font-size: 13px; font-weight: 650; color: var(--text); font-variant-numeric: tabular-nums; }
.review-question { margin: 0; font-size: 13px; color: var(--text); line-height: 1.5; }
.review-confirm { display: flex; align-items: center; justify-content: center; gap: 6px; min-height: 36px; }
.review-confirm kbd, .review-next kbd { flex: none; padding: 0 4px; font-size: 10px; border-color: currentColor; color: inherit; opacity: .55; }
.review-next { display: inline-flex; align-items: center; justify-content: center; gap: 4px; padding: 4px 8px; font-size: 12px; }
.review-result { margin-left: auto; font-size: 11px; color: var(--success); }
.review-result.todo { color: var(--accent); }
.review-result.outside { color: var(--muted); }
.review-checked { margin: 0; font-size: 12px; color: var(--muted); }
.review-spans { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; font-size: 12px; }
.review-spans small { color: var(--muted); }
.review-span { padding: 2px 6px; border: 1px solid var(--line); border-radius: 5px; background: var(--surface); color: var(--text); font-size: 12px; font-variant-numeric: tabular-nums; cursor: pointer; }
.review-span:hover { background: var(--surface-hover); }
.review-changed { width: 100%; border: 0; text-align: left; cursor: pointer; }
</style>
