<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import type { TargetResult } from '../api/annotationReviewApi'
import type { QueueEntry } from '../annotation/targetQueue'
import { motionAllowed } from '../utils/motion'

// One target (a whole track) at a time, filling the canvas area: a question, the target
// cropped from its first, middle and last frame plus an average aligned on the head, and
// the answers along the bottom. Only this target's box is drawn, the rest is dimmed; no model scores.
const props = defineProps<{
  entry: QueueEntry
  /** Cards still to answer, this one included (repeat cards are not announced). */
  remaining: number
  /** Box per frame in source pixels, xyxy, for this target. */
  boxes: Map<number, [number, number, number, number]>
  mediaWidth: number
  mediaHeight: number
  /** Blob of one source frame, in the view the canvas currently shows (raw or enhanced). */
  readFrame: (frameIndex: number) => Promise<Blob>
  answer: TargetResult | null
  /** Frames inside the target that still have no box. */
  missing?: number[]
  canGoBack: boolean
  busy: boolean
}>()
const emit = defineEmits<{ answer: [result: TargetResult]; split: [frame: number]; fix: [frame: number]; prev: [] }>()

const step = ref<'same' | 'pick' | 'what'>('what')
watch(() => [props.entry.objectId, props.entry.repeat, props.entry.ask], () => { step.value = props.entry.ask }, { immediate: true })

const frames = computed(() => [...props.boxes.keys()].sort((a, b) => a - b))
const trio = computed(() => {
  const f = frames.value
  return f.length ? [f[0], f[Math.floor(f.length / 2)], f[f.length - 1]] : []
})
const film = computed(() => {
  const at = props.entry.suspectFrame ?? trio.value[1] ?? 0
  return frames.value.filter(f => Math.abs(f - at) <= 3)
})
const tiles = computed(() => step.value === 'what' ? 4 : Math.max(1, film.value.length))

// Crop window: ±200 px at 4K, the size used for the training review sheets.
const radius = computed(() => 200 * Math.max(props.mediaWidth, props.mediaHeight) / 4096)
const stage = ref<HTMLElement | null>(null)
const tile = ref(160), columns = ref(4)
let observer: ResizeObserver | null = null
/** One row or more, whichever gives the largest tiles, so the area is filled rather than left black. */
function fit() {
  const el = stage.value
  if (!el) return
  const gap = 12, caption = 22, padding = 40, n = tiles.value
  const width = el.clientWidth - padding, height = el.clientHeight - 16
  let best = { size: 0, cols: n }
  for (let rows = 1; rows <= Math.min(3, n); rows++) {
    const cols = Math.ceil(n / rows)
    const size = Math.min((width - gap * (cols - 1)) / cols, (height - rows * caption - gap * (rows - 1)) / rows)
    if (size > best.size) best = { size, cols }
  }
  tile.value = Math.max(72, Math.floor(best.size)); columns.value = best.cols
}
onMounted(() => { observer = new ResizeObserver(fit); if (stage.value) observer.observe(stage.value); fit() })
onBeforeUnmount(() => observer?.disconnect())
watch(tiles, () => nextTick(fit))

const bitmaps = new Map<number, Promise<ImageBitmap>>()
watch(() => props.readFrame, () => bitmaps.clear())
const bitmap = (fi: number) => {
  let value = bitmaps.get(fi)
  if (!value) {
    value = props.readFrame(fi).then(blob => createImageBitmap(blob))
    value.catch(() => bitmaps.delete(fi))
    bitmaps.set(fi, value)
    if (bitmaps.size > 80) bitmaps.delete(bitmaps.keys().next().value!)
  }
  return value
}
const centre = (fi: number) => { const b = props.boxes.get(fi)!; return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2, Math.hypot(b[2] - b[0], b[3] - b[1])] as const }
function drawCrop(ctx: CanvasRenderingContext2D, image: ImageBitmap, fi: number, size: number) {
  const [cx, cy] = centre(fi), r = radius.value
  ctx.fillStyle = '#7f7f7f'; ctx.fillRect(0, 0, size, size)
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(image, cx - r, cy - r, 2 * r, 2 * r, 0, 0, size, size)
}
/** The target's own box as it is on that frame (the crop is centred on it), the rest dimmed:
 *  one thing to look at, and a box sitting between two heads is plain to see. */
function mark(ctx: CanvasRenderingContext2D, fi: number, size: number) {
  const b = props.boxes.get(fi)!, s = size / (2 * radius.value), dpr = window.devicePixelRatio || 1, pad = 3 * dpr
  const w = Math.min(size - 4 * dpr, Math.max(12 * dpr, (b[2] - b[0]) * s + 2 * pad))
  const h = Math.min(size - 4 * dpr, Math.max(12 * dpr, (b[3] - b[1]) * s + 2 * pad))
  const x = (size - w) / 2, y = (size - h) / 2
  ctx.save()
  ctx.beginPath(); ctx.rect(0, 0, size, size); ctx.rect(x, y, w, h)
  ctx.fillStyle = 'rgba(0,0,0,.42)'; ctx.fill('evenodd')
  ctx.strokeStyle = '#ffc542'; ctx.lineWidth = 2.5 * dpr; ctx.lineJoin = 'round'
  ctx.strokeRect(x, y, w, h)
  ctx.restore()
}
const canvases = ref<Record<string, HTMLCanvasElement | null>>({})
const setCanvas = (key: string) => (el: unknown) => { canvases.value[key] = el as HTMLCanvasElement | null }
const loading = ref(false)
let drawSerial = 0
async function draw() {
  const serial = ++drawSerial
  await nextTick()
  const dpr = window.devicePixelRatio || 1, size = Math.round(tile.value * dpr)
  const paint = async (key: string, fi: number) => {
    const canvas = canvases.value[key]
    if (!canvas) return
    const image = await bitmap(fi)
    if (serial !== drawSerial) return
    canvas.width = canvas.height = size
    const ctx = canvas.getContext('2d')!
    drawCrop(ctx, image, fi, size); mark(ctx, fi, size)
  }
  loading.value = true
  try {
    if (step.value === 'what') {
      await Promise.all(trio.value.map((fi, i) => paint(`t${i}`, fi)))
      // Average aligned on the head over up to 48 frames: a faint tail adds up, passing debris fades.
      const canvas = canvases.value.avg
      const pick = frames.value.length <= 48 ? frames.value : Array.from({ length: 48 }, (_, i) => frames.value[Math.round(i * (frames.value.length - 1) / 47)])
      if (!canvas || !pick.length) return
      const work = document.createElement('canvas'); work.width = work.height = size
      const wctx = work.getContext('2d', { willReadFrequently: true })!, sum = new Float32Array(size * size)
      for (const fi of pick) {
        const image = await bitmap(fi)
        if (serial !== drawSerial) return
        drawCrop(wctx, image, fi, size)
        const data = wctx.getImageData(0, 0, size, size).data
        for (let i = 0; i < sum.length; i++) sum[i] += data[i * 4]
      }
      canvas.width = canvas.height = size
      const ctx = canvas.getContext('2d')!, out = ctx.createImageData(size, size)
      for (let i = 0; i < sum.length; i++) { const v = sum[i] / pick.length; out.data[i * 4] = out.data[i * 4 + 1] = out.data[i * 4 + 2] = v; out.data[i * 4 + 3] = 255 }
      ctx.putImageData(out, 0, 0); mark(ctx, trio.value[1], size)
    } else {
      await Promise.all(film.value.map(fi => paint(`f${fi}`, fi)))
    }
  } catch (error) {
    console.warn('[review.target_crop_failed]', { objectId: props.entry.objectId, error })
  } finally { if (serial === drawSerial) loading.value = false }
}
watch(() => [props.entry.objectId, props.entry.repeat, step.value, tile.value, props.readFrame, props.boxes], () => { void draw() }, { immediate: true })

const answers: { value: TargetResult; label: string; hint?: string }[] = [
  { value: 'tail', label: '是，有尾巴' }, { value: 'head', label: '是，没尾巴' },
  { value: 'no', label: '不是', hint: '杂质等，整条删除' }, { value: 'unsure', label: '看不清', hint: '拿不准就选它' },
]
const question = computed(() => step.value === 'same' ? '黄框里一直是同一个精子吗？' : step.value === 'pick' ? '从哪一张起黄框里换了？点那一张' : '黄框里是精子吗？')
const missingText = computed(() => {
  const list = props.missing ?? []
  return list.length <= 4 ? `第 ${list.map(f => f + 1).join('、')} 帧` : `第 ${list.slice(0, 3).map(f => f + 1).join('、')} 等 ${list.length} 帧`
})
function key(k: string) {
  if (props.busy) return false
  if (step.value === 'same') { if (k === '1') { step.value = 'what'; return true } if (k === '2') { step.value = 'pick'; return true } return false }
  if (step.value === 'what' && '1234'.includes(k) && k.length === 1) { emit('answer', answers[Number(k) - 1].value); return true }
  return false
}
defineExpose({ key })
</script>

<template>
  <section class="target-review" data-testid="target-review" aria-label="逐个确认目标">
    <header class="tr-head">
      <h2>{{ question }}</h2>
      <span class="tr-meta"><span class="num">还剩 {{ remaining }} 个</span><span class="tr-id" title="目标编号">{{ entry.objectId }}</span></span>
    </header>
    <div ref="stage" class="tr-stage" :class="{ loading }">
      <!-- out-in mounts the new tiles only after the old ones left: draw again once they are in. -->
      <Transition name="tr-swap" mode="out-in" :css="motionAllowed" @after-enter="draw">
        <div :key="`${entry.objectId}:${entry.repeat}:${step}`" class="tr-tiles" :style="{ '--tile': `${tile}px`, '--cols': columns }">
          <template v-if="step === 'what'">
            <figure v-for="(fi, i) in trio" :key="`t${i}`"><canvas :ref="setCanvas(`t${i}`)" /><figcaption>{{ ['开始', '中间', '结束'][i] }} · 第 {{ fi + 1 }} 帧</figcaption></figure>
            <figure class="avg"><canvas :ref="setCanvas('avg')" /><figcaption>{{ Math.min(frames.length, 48) }} 帧叠加</figcaption></figure>
          </template>
          <template v-else>
            <component :is="step === 'pick' ? 'button' : 'figure'" v-for="fi in film" :key="`f${fi}`" class="tr-film" :class="{ pick: step === 'pick' }" :aria-label="step === 'pick' ? `从第 ${fi + 1} 帧起分开` : undefined" @click="step === 'pick' && fi !== frames[0] && emit('split', fi)">
              <canvas :ref="setCanvas(`f${fi}`)" /><figcaption>第 {{ fi + 1 }} 帧</figcaption>
            </component>
          </template>
        </div>
      </Transition>
    </div>
    <footer class="tr-foot">
      <p v-if="missing?.length" class="tr-missing" role="status"><AppIcon name="alert" :size="14" />{{ missingText }}没有框<button class="quiet-button" @click="emit('fix', missing[0])">去补</button></p>
      <div v-if="step === 'what'" class="tr-answers">
        <button v-for="(item, i) in answers" :key="item.value" class="tr-answer" :class="[item.value, { on: answer === item.value }]" :disabled="busy" :data-testid="`answer-${item.value}`" @click="emit('answer', item.value)">
          <i class="dot" aria-hidden="true" /><span><b>{{ item.label }}</b><small v-if="item.hint">{{ item.hint }}</small></span><kbd>{{ i + 1 }}</kbd>
        </button>
      </div>
      <div v-else-if="step === 'same'" class="tr-answers two">
        <button class="tr-answer" :disabled="busy" data-testid="same-yes" @click="step = 'what'"><span><b>是同一个</b></span><kbd>1</kbd></button>
        <button class="tr-answer" :disabled="busy" data-testid="same-no" @click="step = 'pick'"><span><b>不是</b><small>中间换成了别的</small></span><kbd>2</kbd></button>
      </div>
      <div class="tr-links">
        <button class="quiet-button" :disabled="busy || (step !== 'pick' && !canGoBack)" @click="step === 'pick' ? step = 'same' : emit('prev')"><AppIcon name="prev" :size="14" />{{ step === 'pick' ? '返回' : '上一个' }}</button>
        <button class="quiet-button" @click="emit('fix', step === 'what' ? trio[1] : (entry.suspectFrame ?? trio[1]))"><AppIcon name="box" :size="14" />框不对？在画面中修改<kbd>Tab</kbd></button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
/* A flex item in place of the canvas scroller, below the toolbar row. Columns are capped so large
   tiles can never widen the stage and feed back into an even larger tile size. */
.target-review { position:relative; flex:1; width:100%; min-height:0; display:grid; grid-template-columns:minmax(0,1fr); grid-template-rows:auto minmax(0,1fr) auto; background:var(--canvas); color:#e6eef0; }
.tr-head { display:flex; align-items:center; gap:12px; padding:14px 20px 6px; }
.tr-head h2 { margin:0; font-size:var(--fs-xl); font-weight:650; letter-spacing:-.01em; }
.tr-meta { margin-left:auto; display:flex; align-items:center; gap:10px; color:#8aa0a8; font-size:var(--fs-xs); }
.tr-meta .num { font-variant-numeric:tabular-nums; }
.tr-id { min-width:30px; text-align:center; padding:1px 7px; border-radius:6px; border:1px solid #33464f; font:600 11px var(--font-mono); color:#c8d6db; }
.tr-stage { position:relative; min-height:0; min-width:0; overflow:hidden; padding:8px 20px; display:flex; align-items:center; justify-content:center; }
.tr-stage.loading::after { content:''; position:absolute; top:10px; right:22px; width:14px; height:14px; border-radius:50%; border:2px solid #4b6470; border-top-color:#9ee6e1; animation:tr-spin .8s linear infinite; }
.tr-tiles { display:grid; grid-template-columns:repeat(var(--cols), var(--tile)); gap:12px; align-items:start; justify-content:center; }
.tr-tiles figure, .tr-film { margin:0; padding:0; border:0; background:none; display:flex; flex-direction:column; align-items:center; gap:4px; color:#8aa0a8; font-size:var(--fs-xs); }
.tr-tiles canvas { width:var(--tile); height:var(--tile); border-radius:10px; display:block; background:#7f7f7f; }
.tr-tiles .avg canvas { box-shadow:0 0 0 1px #ffffff2e; }
.tr-film.pick { cursor:pointer; border-radius:12px; }
.tr-film.pick:hover canvas, .tr-film.pick:focus-visible canvas { box-shadow:0 0 0 3px #ff8a9e; }
.tr-foot { padding:10px 20px 16px; display:grid; gap:10px; background:linear-gradient(transparent, #0006); }
.tr-answers { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; width:100%; max-width:980px; margin:0 auto; }
.tr-answers.two { grid-template-columns:repeat(2,minmax(0,1fr)); max-width:560px; }
.tr-answer { display:flex; align-items:center; gap:10px; text-align:left; padding:12px 14px; border-radius:12px; border:1.5px solid #33464f; background:#121c21; color:#e6eef0; cursor:pointer; transition:background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out); }
.tr-answer:hover:not(:disabled) { border-color:#9ee6e1; background:#173033; }
.tr-answer:active:not(:disabled) { transform:scale(.98); }
.tr-answer b { display:block; font-size:var(--fs-md); }
.tr-answer small { color:#8aa0a8; font-size:var(--fs-2xs); }
.tr-answer kbd { margin-left:auto; font-size:var(--fs-2xs); opacity:.7; border:1px solid currentColor; border-radius:4px; padding:0 4px; }
.tr-answer .dot { width:9px; height:9px; border-radius:50%; flex:none; }
.tr-answer.tail .dot { background:#3ddc84; } .tr-answer.head .dot { background:#5cc8c2; } .tr-answer.no .dot { background:#ff6b85; } .tr-answer.unsure .dot { background:#efc372; }
.tr-answer.on { border-color:#9ee6e1; background:#173033; }
.tr-missing { margin:0 auto; display:flex; align-items:center; gap:8px; padding:6px 12px; border-radius:999px; background:#3a2c10; color:#ffd78a; font-size:var(--fs-sm); }
.tr-missing .quiet-button { color:#ffd78a; display:inline-flex; align-items:center; gap:6px; }
.tr-missing kbd { font-size:var(--fs-2xs); opacity:.7; border:1px solid currentColor; border-radius:4px; padding:0 4px; }
.tr-links { display:flex; justify-content:space-between; max-width:980px; width:100%; margin:0 auto; }
.tr-links .quiet-button { color:#8aa0a8; display:inline-flex; align-items:center; gap:6px; }
.tr-links kbd { font-size:var(--fs-2xs); opacity:.7; border:1px solid currentColor; border-radius:4px; padding:0 4px; }
.tr-swap-enter-active, .tr-swap-leave-active { transition:opacity var(--dur-base) var(--ease-out), transform var(--dur-base) var(--ease-out); }
.tr-swap-enter-from { opacity:0; transform:translateX(10px); }
.tr-swap-leave-to { opacity:0; transform:translateX(-8px); }
@keyframes tr-spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion:reduce) { .tr-stage.loading::after { animation:none; } }
@media (max-width:760px) { .tr-answers { grid-template-columns:repeat(2,minmax(0,1fr)); } .tr-head h2 { font-size:var(--fs-lg); } }
</style>
