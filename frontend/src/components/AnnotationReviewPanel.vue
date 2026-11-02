<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ReviewZoomPreview from './ReviewZoomPreview.vue'
import { useAnnotationReview, reviewStatusLabel } from '../stores/annotationReview'
import { useAuth } from '../stores/auth'
import type { CheckResult, ItemKind, ReviewItem } from '../api/annotationReviewApi'
import { reviewCategory, reviewLabels, reviewLocationText } from '../annotation/reviewPresentation'

const props = defineProps<{
  mediaId: string | null; currentFrame: number; blocked: boolean; trackingPending: boolean; flush: () => Promise<unknown>
  previewSource: HTMLImageElement | HTMLVideoElement | null; previewFilter: string
  previewMediaWidth: number; previewMediaHeight: number; actionError?: string; actionBusy?: boolean
}>()
const emit = defineEmits<{ locate: [item: ReviewItem]; adopt: [item: ReviewItem]; 'add-head': [item: ReviewItem] }>()
const review = useAnnotationReview()
const auth = useAuth()
const { state, busy, error, status, nextItem } = review
const pending=ref(false)
let contextSerial=0
watch([()=>props.mediaId,()=>auth.user.value?.id],()=>{contextSerial++},{flush:'sync'})
onBeforeUnmount(()=>{contextSerial++})

// One short explanation per category, shown once instead of on every row.
const groups: { kind: ItemKind; category?: string; label: string; hint: string }[] = [
  { kind: 'unknown', label: '未标注帧', hint: '这些帧没有任何框。补标，或确认确实没有精子。' },
  { kind: 'doubt', category: 'missing', label: reviewLabels.missing, hint: '小圆圈提示可能漏标的位置；请核对是否有未框出的精子头。' },
  { kind: 'doubt', category: 'association', label: reviewLabels.association, hint: '强调的框可能重复或跟错目标，结合前后帧检查。' },
  { kind: 'doubt', category: 'exit', label: reviewLabels.exit, hint: '轨迹在画面边缘结束，最后几帧可能是精子离开后残留的框。' },
  { kind: 'doubt', category: 'position', label: reviewLabels.position, hint: '检查强调的框是否贴合精子头，可直接微调。' },
  { kind: 'doubt', category: 'size', label: reviewLabels.size, hint: '框比同一对象前后帧大或小很多，常见于杂质被包进框里，或框整体偏大。' },
  { kind: 'doubt', category: 'static', label: reviewLabels.static, hint: '位置几乎不动的目标，可能是杂质，也可能是不活动的精子。整段视频同一位置只问一次。' },
  { kind: 'doubt', category: 'check', label: reviewLabels.check, hint: '自动检查未完成，请人工核对本段画面。' },
  { kind: 'changed', label: '通过后的改动', hint: '上次审查通过后改过的帧，确认改得对。' },
  { kind: 'followup', label: '修正处前后复查', hint: '刚修正处的前后几帧常有同样的问题（框偏大、残留框），逐帧看一遍。' },
  { kind: 'count', label: '计数突变', hint: '框数和前后帧差 3 个以上，常见于漏标、重复框或跟错。' },
  { kind: 'coverage', label: '整帧查漏标', hint: '看完整幅画面，找没有框的精子头。' },
  { kind: 'sample', label: '随机抽检', hint: '随机挑的帧，检查每个框和有没有漏标。' },
]
const groupOf = (item: ReviewItem) => groups.find(g => g.kind === item.kind && (item.kind !== 'doubt' || g.category === reviewCategory(item)))!
const round = computed(() => state.value?.status === 'open' ? state.value.review : null)
const items = computed(() => round.value?.items ?? [])
const grouped = computed(() => groups.map(g => ({ ...g, items: items.value.filter(i => groupOf(i) === g) })).filter(g => g.items.length))
const remaining = computed(() => items.value.filter(i => !i.check).length)
const canPass = computed(() => !!round.value && remaining.value === 0)
const concurrentReviewer = computed(() => {
  const value = round.value
  if (!value || value.lastActorId == null) return null
  const me = Number(auth.user.value?.id)
  if (!Number.isFinite(me) || value.lastActorId === me || !value.lastActivityAt) return null
  const age = Date.now() - Date.parse(value.lastActivityAt)
  if (!Number.isFinite(age) || age < 0 || age > 10 * 60 * 1000) return null
  return { actor: value.lastActorId, minutes: Math.max(1, Math.floor(age / 60000)) }
})

// The card always shows one item: the one last located, else the next unchecked one.
const selectedId = ref<string | null>(null)
const current = computed(() => items.value.find(i => i.id === selectedId.value) ?? nextItem.value)
watch(() => round.value?.reviewId, () => { selectedId.value = null })

const frames = (i: ReviewItem) => i.startFrame === i.endFrame ? `第 ${i.startFrame + 1} 帧` : `第 ${i.startFrame + 1}–${i.endFrame + 1} 帧`
function objectsText(i: ReviewItem) {
  const ids = i.objectIds ?? (i.objectId != null ? [i.objectId] : [])
  if (!ids.length) return ''
  return ids.length <= 4 ? `对象 ${ids.join('、')}` : `对象 ${ids.slice(0, 3).join('、')} 等 ${ids.length} 个`
}
const missing = (i: ReviewItem) => i.kind === 'doubt' && ['missing', 'static'].includes(reviewCategory(i))
const rowText = (i: ReviewItem) => i.source === 'track_ended'
  ? `对象 ${i.objectId} · 第 ${i.frame + 1} 帧起消失`
  : missing(i) ? reviewLocationText(i) : i.kind === 'doubt' ? objectsText(i) : i.kind === 'count' ? i.message : ''
const sameFrameLocations = computed(() => current.value && missing(current.value) ? items.value.filter(i => missing(i) && i.frame === current.value!.frame).sort((a,b) => (a.index ?? 0)-(b.index ?? 0)) : [])
const currentLocation = computed(() => sameFrameLocations.value.findIndex(i => i.id === current.value?.id))
function nearbyLocation(delta: number) { const item = sameFrameLocations.value[currentLocation.value+delta]; if (item) locate(item) }
const resultLabel: Record<CheckResult, string> = { ok: '无误', fixed: '已修正', empty: '无目标', not_target: '不是精子' }
const checkedLabel = (i: ReviewItem) => i.check ? (missing(i) && i.check.result === 'ok' ? '已有框' : resultLabel[i.check.result]) : ''
// One plain sentence per card; the buttons carry the choices.
function question(i: ReviewItem) {
  if (i.source === 'track_ended') return `对象 ${i.objectId} 从这一帧起没有框了。它还在画面里吗？灰色虚线为上一帧位置；若仍在画面内，请补框。`
  if (i.source === 'edge_exit') return `对象 ${i.objectId} 在画面边缘结束。逐帧看${frames(i)}：精子已离开的帧请删除残留框，被边缘截断的框请收缩。`
  if (i.source === 'size_jump' || i.source === 'oversized') return `对象 ${i.objectId}：${i.message}。检查框是否包进了杂质或偏大，收缩后可把尺寸应用到相邻帧。`
  if (i.kind === 'followup') return `${objectsText(i) ? `${objectsText(i)}：` : ''}刚修正的帧前后（${frames(i)}）常有同样的问题，逐帧看一遍。`
  if (missing(i)) {
    if (reviewCategory(i) === 'static') return '这个目标几乎不动：可能是杂质，也可能是不动的精子。'
    const seen = i.source === 'disagreement' ? '正反向追踪对这个头的位置不一致，这里没有框' : i.source === 'detector' ? '检测器在这里看到一个头，但没有框' : `SAM3 反向追踪在 ${i.observations?.length ?? 1} 帧看到一个头，但没有框`
    return `${seen}${i.detectorAgrees && i.source !== 'detector' ? '（检测器也看到了）' : ''}。`
  }
  const objects = i.kind === 'doubt' ? objectsText(i) : ''
  return i.kind === 'count' ? i.message : objects ? `${objects}：${groupOf(i).hint}` : groupOf(i).hint
}
type CardAction = { key: 1 | 2 | 3; label: string; title: string; primary?: boolean; testid?: string; run: () => void }
const actions = computed<CardAction[]>(() => {
  const i = current.value
  if (!i) return []
  if (i.kind === 'unknown') return [
    { key: 1, label: '确认无目标', title: '这些帧确实没有精子', primary: true, run: () => mark(i, 'empty') },
    { key: 2, label: '已补标', title: '已经在画面上补好框', run: () => mark(i, 'fixed') },
  ]
  if (missing(i)) return [
    adoptsTrack(i)
      ? { key: 1, label: `是精子，采用轨迹（${i.observations?.length ?? 0} 帧）`, testid: 'review-adopt-track', primary: true, run: () => emit('adopt', i),
          title: `补上 SAM3 反向追踪的框，编号 #${i.adoptObjectId ?? '新'}；可在对象操作中“删除全视频中此对象”撤回` }
      : { key: 1, label: '是精子，补这一帧', testid: 'review-add-head', primary: true, run: () => emit('add-head', i),
          title: `在第 ${i.frame + 1} 帧的候选位置自动补一个头部框；其他帧若也缺需逐帧补` },
    { key: 2, label: '不是精子', title: '记住这个位置，以后不再提示', run: () => mark(i, 'not_target') },
    { key: 3, label: '已有框', title: '画面上已经框好这个精子（重复识别，或你刚手动补好）', run: () => mark(i, 'ok') },
  ]
  return [
    { key: 1, label: '无误', title: '检查过，没有问题', primary: true, run: () => mark(i, 'ok') },
    { key: 2, label: '已修正', title: '先在画面上改好，再点这里', run: () => mark(i, 'fixed') },
  ]
})
const adoptsTrack = (i: ReviewItem) => i.kind === 'doubt' && reviewCategory(i) === 'missing' && ['reverse', 'disagreement'].includes(i.source ?? '')
const zoomPreview = computed(() => {
  const item = current.value
  if (!item || item.kind !== 'doubt') return null
  if (!(missing(item) || item.source === 'track_ended' || item.source === 'edge_exit')) return null
  const observation = item.observations?.find(o => o.frameIndex === props.currentFrame)
  const box = observation?.candidates?.[0] ?? item.anchor ?? item.lastBox
  if (!box?.length) return null
  const frame = observation ? props.currentFrame : item.frame
  const sequence = item.index && item.countInFrame ? `${item.index}/${item.countInFrame}` : ''
  const position = [item.region, sequence].filter(Boolean).join(' · ')
  return { box, label: `第 ${frame + 1} 帧${position ? ` · ${position}` : ''}${observation ? '' : ' 的位置'}` }
})

function locate(item: ReviewItem) { selectedId.value = item.id; emit('locate', item) }
async function perform(kind: 'start'|'check'|'pass', item?: ReviewItem, result?: CheckResult) {
  if(busy.value||props.actionBusy||pending.value||props.blocked||!props.mediaId)return
  const mediaId=props.mediaId,reviewId=round.value?.reviewId,serial=contextSerial
  if(kind!=='start'&&!reviewId)return
  const current=()=>serial===contextSerial && mediaId===props.mediaId && (kind==='start' || reviewId===round.value?.reviewId)
  pending.value=true
  try {
    await props.flush()
    if(!current())return
    const ok=kind==='start'?await review.start(mediaId):kind==='pass'?await review.pass(mediaId,reviewId!):await review.check(mediaId,reviewId!,item!,result!)
    if(ok&&current()&&nextItem.value)locate(nextItem.value)
  } catch(e) {
    if(current())error.value=`标注保存失败，未记录本次审查操作：${(e as Error)?.message||'请重试'}`
  } finally {pending.value=false}
}
const start=()=>perform('start')
const mark=(item:ReviewItem,result:CheckResult)=>perform('check',item,result)
const pass=()=>perform('pass')
function actionable() { return !busy.value && !props.actionBusy && !pending.value && !props.blocked && current.value }
function run(key: CardAction['key']) {
  if (!actionable()) return
  actions.value.find(a => a.key === key)?.run()
}
const primary = () => run(1)
const secondary = () => run(2)
const tertiary = () => run(3)
function next() {
  const all = items.value
  if (!all.length) return
  const index = all.findIndex(i => i.id === current.value?.id)
  const ordered = [...all.slice(index + 1), ...all.slice(0, Math.max(0, index + 1))]
  const item = ordered.find(i => !i.check) ?? ordered[0]
  if (item) locate(item)
}
defineExpose({ primary, secondary, tertiary, next })
</script>

<template>
  <section class="shell-card review-panel" aria-label="审查清单" data-testid="review-panel">
    <div class="shell-card-head">
      <AppIcon name="check" :size="16" /><strong>审查</strong>
      <span v-if="status" class="review-status" :class="status" data-testid="review-status">{{ reviewStatusLabel[status] }}</span>
    </div>
    <p v-if="!mediaId" class="review-note">选择一个视频开始审查。</p>
    <p v-else-if="!state && !error" class="review-note">正在读取…</p>
    <template v-else-if="state">
      <template v-if="status === 'none' || status === 'stale'">
        <p class="review-note">{{ status === 'stale' ? '通过后标注又改过，只需检查改动的地方。' : '系统会挑出最可能出错的帧，逐个确认；发现错误直接在画面上改。' }}</p>
        <p v-if="state.notReady" class="review-warning" role="status">{{ state.notReady }}</p>
        <p v-if="trackingPending" class="review-warning" role="status">AI 追踪还在等待核对，请先回到标注页处理。</p>
        <button class="btn-primary review-wide" data-testid="review-start" :disabled="busy || actionBusy || pending || blocked || trackingPending || !!state.notReady" @click="start">{{ busy ? '正在生成清单…' : status === 'stale' ? '开始重新审查' : '开始审查' }}</button>
      </template>

      <template v-else-if="status === 'passed' && state.review">
        <p class="review-note">已通过，可以导出训练数据集。之后再改标注需要重新审查。</p>
        <p v-if="state.review.changes" class="review-changes" data-testid="review-changes">本轮修改：移动 {{ state.review.changes.moved }} · 新增 {{ state.review.changes.added }} · 删除 {{ state.review.changes.removed }}</p>
      </template>

      <template v-else-if="round">
        <div class="review-progress" aria-live="polite">
          <progress :max="round.total" :value="round.checked" /><span data-testid="review-progress">{{ round.checked }} / {{ round.total }}</span>
        </div>
        <p v-if="concurrentReviewer" class="review-warning" role="status" data-testid="review-concurrent">用户 #{{ concurrentReviewer.actor }} 也在审查这段视频（最近一次操作 {{ concurrentReviewer.minutes }} 分钟前），同时修改可能互相覆盖。</p>

        <div v-if="current" class="review-current" data-testid="review-current" :data-item="current.id">
          <div class="review-current-head">
            <span class="review-kind">{{ groupOf(current).label }}</span>
            <button class="review-frame" :title="`跳到${frames(current)}`" @click="locate(current)">{{ frames(current) }}</button>
            <span v-if="current.check" class="review-result" :class="current.check.result">{{ checkedLabel(current) }}</span>
          </div>
          <p class="review-question">{{ question(current) }}</p>
          <figure v-if="zoomPreview" class="review-zoom" data-testid="review-zoom-preview">
            <ReviewZoomPreview :source="previewSource" :box="zoomPreview.box" :size="200" :media-width="previewMediaWidth" :media-height="previewMediaHeight" :filter="previewFilter" :frame-key="`${current.id}:${currentFrame}:${zoomPreview.label}`" />
            <figcaption data-testid="review-location-text">{{ zoomPreview.label }}</figcaption>
          </figure>
          <div v-if="missing(current) || objectsText(current) || current.source === 'track_ended'" class="review-locate">
            <span v-if="!zoomPreview && missing(current)" class="review-locate-text" data-testid="review-location-text">{{ reviewLocationText(current) }}</span>
            <button v-if="sameFrameLocations.length > 1" class="icon-button" aria-label="上一处" title="本帧上一处" :disabled="currentLocation <= 0" @click="nearbyLocation(-1)"><AppIcon name="prev" :size="15" /></button>
            <button class="quiet-button" data-testid="review-relocate" @click="locate(current)"><AppIcon name="point" :size="14" />{{ missing(current) ? '定位此处' : '定位对象' }}</button>
            <button v-if="sameFrameLocations.length > 1" class="icon-button" aria-label="下一处" title="本帧下一处" :disabled="currentLocation >= sameFrameLocations.length-1" @click="nearbyLocation(1)"><AppIcon name="next" :size="15" /></button>
          </div>
          <div class="review-actions">
            <button v-for="a in actions" :key="a.key" :class="[a.primary ? 'btn-primary' : 'btn-secondary', { wide: a.primary && actions.length > 2 }]" :data-testid="a.testid" :aria-label="a.label" :title="`${a.title}（快捷键 ${a.key}）`" :disabled="busy || actionBusy || pending || blocked" @click="run(a.key)">
              <span>{{ a.label }}</span><kbd aria-hidden="true">{{ a.key }}</kbd>
            </button>
          </div>
          <p v-if="current.check" class="review-checked" data-testid="review-checked">已判为：{{ checkedLabel(current) }}，可改判</p>
          <p v-if="actionError" class="review-warning" role="alert" data-testid="review-action-error">{{ actionError }}</p>
        </div>

        <button class="btn-primary review-wide" data-testid="review-pass" :disabled="!canPass || busy || actionBusy || pending || blocked" @click="pass">{{ busy ? '正在处理…' : canPass ? '审查通过' : `还剩 ${remaining} 项` }}</button>

        <div class="review-groups">
          <details v-for="g in grouped" :key="g.category || g.kind" class="review-group" :data-review-group="g.category || g.kind" :open="g.items.some(i => !i.check) || g.items.some(i => i.id === current?.id)">
            <summary><span>{{ g.label }}</span><small>{{ g.items.filter(i => i.check).length }} / {{ g.items.length }}</small></summary>
            <div v-for="item in g.items" :key="item.id" class="review-row" :class="{ done: item.check, current: item.id === current?.id }" :data-review-item="item.id" role="button" tabindex="0" :aria-current="item.id === current?.id" @click="locate(item)" @keydown.enter.prevent="locate(item)" @keydown.space.prevent="locate(item)">
              <i class="review-dot" :class="item.check?.result" aria-hidden="true" />
              <span class="review-row-frame">{{ frames(item) }}</span>
              <span class="review-row-text">{{ rowText(item) }}</span>
              <small v-if="item.check">{{ checkedLabel(item) }}</small>
              <button v-if="item.check" class="review-override" title="改判这一项" @click.stop="locate(item)">改判</button>
            </div>
          </details>
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
.review-current { border: 1px solid var(--accent); border-radius: var(--radius-md); padding: 12px; background: var(--surface); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow-sm); animation: rise-in var(--dur-base) var(--ease-out); }
.review-current-head { display: flex; align-items: baseline; gap: 8px; }
.review-kind { font-size: 12px; font-weight: 650; letter-spacing: .02em; color: var(--accent); }
.review-frame { font-size: 13px; font-weight: 600; color: var(--text); background: none; border: 0; padding: 0; cursor: pointer; text-decoration: underline dotted; text-underline-offset: 3px; }
.review-question { margin: 0; font-size: 13px; color: var(--text); line-height: 1.5; }
.review-zoom { margin: 0; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.review-zoom figcaption { font-size: 12px; color: var(--muted); }
.review-locate { display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: -4px; }
.review-locate-text { margin-right: auto; font-size: 12px; color: var(--muted); }
.review-locate .quiet-button { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; font-size: 12px; }
.review-locate .icon-button { width: 28px; height: 28px; }
.review-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.review-actions button { display: flex; align-items: center; justify-content: center; gap: 6px; min-width: 0; min-height: 36px; }
.review-actions button.wide { grid-column: 1 / -1; }
.review-actions button span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.review-actions kbd { flex: none; padding: 0 4px; font-size: 10px; border-color: currentColor; color: inherit; opacity: .55; }
.review-result { margin-left: auto; font-size: 11px; color: var(--success); }
.review-result.fixed { color: var(--warning); }
.review-checked { margin: 0; font-size: 12px; color: var(--muted); }
.review-override { flex: none; padding: 2px 5px; border: 1px solid var(--line); border-radius: 5px; background: var(--surface); color: var(--text); font-size: 11px; }
.review-groups { display: flex; flex-direction: column; gap: 6px; flex: none; min-height: 0; }
.review-group summary { display: flex; justify-content: space-between; align-items: center; cursor: pointer; font-size: 12px; font-weight: 600; color: var(--text); padding: 4px 2px; }
.review-group summary small { font-weight: 400; color: var(--muted); font-variant-numeric: tabular-nums; }
.review-row { display: flex; align-items: center; gap: 8px; width: 100%; min-height: 30px; padding: 4px 6px; border: 0; border-radius: 6px; background: none; text-align: left; font-size: 12px; color: var(--text); cursor: pointer; }
.review-row:hover { background: var(--surface-hover); }
.review-row.current { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
.review-row.done { color: var(--muted); }
.review-row-frame { flex: none; font-variant-numeric: tabular-nums; }
.review-row-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.review-row small { flex: none; color: var(--muted); }
.review-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--muted); }
.review-dot.ok, .review-dot.empty { background: var(--success); border-color: var(--success); }
.review-dot.fixed { background: var(--warning); border-color: var(--warning); }
</style>
