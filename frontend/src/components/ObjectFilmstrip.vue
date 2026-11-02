<script setup lang="ts">
// 按对象检查（决策 0002）：同一对象逐帧裁剪缩略图 + 框面积曲线，替代逐帧看完全片。
// 缩略图由服务端从原始帧裁剪；读不到的帧明确标出，不用占位图冒充。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { trackApi } from '../api/trackApi'
import { useWorkspace } from '../stores/workspace'
import { applySizeToFrames, deleteFrames, linkedError, objectTarget } from '../stores/workspace/linkedEdits'

const props = defineProps<{ objectId: number }>()
const emit = defineEmits<{ close: [] }>()
const { selectedMedia, currentFrame, editingBlocked, seekVideo, frameToTime } = useWorkspace()
const PAGE = 40, TILE = 88
// 与审查清单“框大小异常”同一阈值：与本对象中位面积相比。
const HIGH = 1.6, LOW = .6

const target = computed(() => objectTarget(props.objectId))
const track = computed(() => target.value?.track ?? [])
const media = computed(() => selectedMedia.value)
const area = (b: { width: number; height: number }) => b.width * b.height
const median = computed(() => {
  const values = track.value.map(t => area(t.bbox)).sort((a, b) => a - b)
  return values.length ? values[Math.floor(values.length / 2)] : 0
})
const ratio = (b: { width: number; height: number }) => median.value > 0 ? area(b) / median.value : 1
const odd = (b: { width: number; height: number }) => ratio(b) >= HIGH || ratio(b) <= LOW
const oddCount = computed(() => track.value.filter(t => odd(t.bbox)).length)

const page = ref(0)
const pages = computed(() => Math.max(1, Math.ceil(track.value.length / PAGE)))
const pageItems = computed(() => track.value.slice(page.value * PAGE, (page.value + 1) * PAGE))
const sheet = ref<{ url: string; columns: number; missing: Set<number>; key: string } | null>(null)
const loading = ref(false)
const loadError = ref('')
const selected = ref(new Set<number>())
let controller: AbortController | null = null
let serial = 0

const pageKey = computed(() => `${media.value?.id}:${props.objectId}:${page.value}:` + pageItems.value.map(t => `${t.frameIndex}/${t.bbox.x.toFixed(3)},${t.bbox.y.toFixed(3)},${t.bbox.width.toFixed(3)},${t.bbox.height.toFixed(3)}`).join(';'))
async function load() {
  const m = media.value, items = pageItems.value, key = pageKey.value, mine = ++serial
  controller?.abort(); controller = null
  if (!m?.serverMediaId || !m.width || !m.height || !items.length) return
  if (sheet.value?.key === key) return
  const c = new AbortController(); controller = c
  loading.value = true; loadError.value = ''
  try {
    const result = await trackApi.objectCrops(m.serverMediaId, items.map(t => ({
      frameIndex: t.frameIndex,
      bbox: [t.bbox.x * m.width! / 100, t.bbox.y * m.height! / 100, (t.bbox.x + t.bbox.width) * m.width! / 100, (t.bbox.y + t.bbox.height) * m.height! / 100],
    })), TILE, c.signal)
    if (mine !== serial) return
    if (sheet.value) URL.revokeObjectURL(sheet.value.url)
    sheet.value = { url: URL.createObjectURL(result.blob), columns: result.columns, missing: result.missing, key }
  } catch (error) {
    if (mine !== serial || c.signal.aborted) return
    console.warn('[annotation.object_crops_failed]', { mediaId: m.id, objectId: props.objectId, error })
    loadError.value = error instanceof Error ? error.message : '读取缩略图失败'
  } finally { if (mine === serial) { loading.value = false; controller = null } }
}
watch(pageKey, load, { immediate: true })
watch(() => props.objectId, () => { page.value = 0; selected.value = new Set() })
onBeforeUnmount(() => { controller?.abort(); serial++; if (sheet.value) URL.revokeObjectURL(sheet.value.url) })

const tileStyle = (index: number) => {
  const s = sheet.value
  if (!s || s.key !== pageKey.value) return {}
  return { backgroundImage: `url(${s.url})`, backgroundPosition: `-${(index % s.columns) * TILE}px -${Math.floor(index / s.columns) * TILE}px` }
}
const missingTile = (index: number) => !!sheet.value && sheet.value.key === pageKey.value && sheet.value.missing.has(index)
function toggle(frame: number) {
  const next = new Set(selected.value)
  next.has(frame) ? next.delete(frame) : next.add(frame)
  selected.value = next
}
function selectOdd() { selected.value = new Set(track.value.filter(t => odd(t.bbox)).map(t => t.frameIndex)) }
function go(frame: number) { seekVideo(frameToTime(frame)) }
function goToPageOf(frame: number) {
  const index = track.value.findIndex(t => t.frameIndex === frame)
  if (index >= 0) page.value = Math.floor(index / PAGE)
}
function removeSelected() {
  const frames = [...selected.value]
  if (!frames.length || !window.confirm(`删除对象 #${props.objectId} 在所选 ${frames.length} 帧的框？可点“撤销联动操作”恢复。`)) return
  deleteFrames(frames, props.objectId)
  if (!linkedError.value) selected.value = new Set()
}
function resizeSelected() {
  applySizeToFrames([...selected.value], props.objectId)
  if (!linkedError.value) selected.value = new Set()
}
const hasCurrent = computed(() => !!target.value?.bbox)

// Area curve over the whole track: x = frame, y = area / median (clamped to 0–3).
const W = 600, H = 70
const curve = computed(() => {
  const t = track.value
  if (t.length < 2) return null
  const first = t[0].frameIndex, last = t[t.length - 1].frameIndex, spanFrames = Math.max(1, last - first)
  const x = (f: number) => (f - first) / spanFrames * W, y = (r: number) => H - Math.min(3, r) / 3 * H
  return {
    points: t.map(p => `${x(p.frameIndex).toFixed(1)},${y(ratio(p.bbox)).toFixed(1)}`).join(' '),
    odd: t.filter(p => odd(p.bbox)).map(p => ({ frame: p.frameIndex, cx: x(p.frameIndex), cy: y(ratio(p.bbox)) })),
    high: y(HIGH), low: y(LOW), one: y(1),
    current: currentFrame.value >= first && currentFrame.value <= last ? x(currentFrame.value) : null,
    first, last, frameAt: (px: number) => Math.round(first + px / W * spanFrames),
  }
})
function curveClick(event: MouseEvent) {
  const c = curve.value, el = event.currentTarget as SVGElement
  if (!c) return
  const rect = el.getBoundingClientRect()
  const frame = c.frameAt((event.clientX - rect.left) / rect.width * W)
  const nearest = track.value.reduce((best, t) => Math.abs(t.frameIndex - frame) < Math.abs(best.frameIndex - frame) ? t : best, track.value[0])
  go(nearest.frameIndex); goToPageOf(nearest.frameIndex)
}
// The page's global shortcuts skip while [data-object-filmstrip] exists; Esc closes here.
const dialog = ref<HTMLElement | null>(null)
function keys(event: KeyboardEvent) { if (event.key === 'Escape') { event.preventDefault(); emit('close') } }
onMounted(() => { window.addEventListener('keydown', keys); void nextTick(() => dialog.value?.focus()) })
onBeforeUnmount(() => window.removeEventListener('keydown', keys))
</script>

<template>
  <Teleport to="body"><div class="dialog-mask" data-object-filmstrip @click.self="emit('close')">
    <section ref="dialog" class="app-dialog filmstrip-dialog" role="dialog" aria-modal="true" aria-labelledby="filmstrip-title" data-testid="object-filmstrip" tabindex="-1">
      <header class="filmstrip-head">
        <h2 id="filmstrip-title">按对象检查 · 对象 #{{ objectId }}</h2>
        <span class="filmstrip-meta">{{ track.length }} 帧有框<template v-if="oddCount"> · <b class="filmstrip-odd-text">{{ oddCount }} 帧面积异常</b></template></span>
        <button class="quiet-button" aria-label="关闭" @click="emit('close')">关闭</button>
      </header>
      <p v-if="!track.length" class="filmstrip-note">这个对象已经没有框了。</p>
      <template v-else>
        <figure v-if="curve" class="filmstrip-curve">
          <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" role="img" :aria-label="`框面积曲线，第 ${curve.first + 1}–${curve.last + 1} 帧`" @click="curveClick">
            <rect x="0" :y="curve.high" :width="W" :height="Math.max(0, curve.low - curve.high)" class="band" />
            <line x1="0" :x2="W" :y1="curve.one" :y2="curve.one" class="median" />
            <polyline :points="curve.points" class="line" />
            <circle v-for="p in curve.odd" :key="p.frame" :cx="p.cx" :cy="p.cy" r="3" class="odd" />
            <line v-if="curve.current !== null" :x1="curve.current" :x2="curve.current" y1="0" :y2="H" class="now" />
          </svg>
          <figcaption>框面积 / 本对象中位面积；阴影为正常范围（0.6–1.6 倍），红点为异常帧。点曲线跳到该帧。</figcaption>
        </figure>
        <div class="filmstrip-tools">
          <button class="btn-secondary" :disabled="!oddCount" @click="selectOdd">选中全部异常帧</button>
          <button class="btn-secondary" :disabled="!selected.size" @click="selected = new Set()">清除选择</button>
          <span class="filmstrip-meta">已选 {{ selected.size }} 帧</span>
          <button class="btn-secondary" data-testid="filmstrip-resize" :disabled="!selected.size || !hasCurrent || editingBlocked" :title="hasCurrent ? `把第 ${currentFrame + 1} 帧的框尺寸应用到所选帧（保留各帧中心）` : '先跳到尺寸正确的一帧'" @click="resizeSelected">所选帧用第 {{ currentFrame + 1 }} 帧的尺寸</button>
          <button class="btn-secondary danger" data-testid="filmstrip-delete" :disabled="!selected.size || editingBlocked" @click="removeSelected">删除所选帧的框</button>
        </div>
        <p v-if="linkedError" class="filmstrip-error" role="alert">{{ linkedError }}</p>
        <p v-if="loadError" class="filmstrip-error" role="alert">{{ loadError }} <button class="quiet-button" @click="sheet = null; load()">重试</button></p>
        <div class="filmstrip-grid" :class="{ loading }" :aria-busy="loading">
          <div v-for="(t, i) in pageItems" :key="t.frameIndex" class="tile" :class="{ odd: odd(t.bbox), selected: selected.has(t.frameIndex), current: t.frameIndex === currentFrame, missing: missingTile(i) }">
            <button class="tile-image" :style="tileStyle(i)" :title="`跳到第 ${t.frameIndex + 1} 帧（面积 ${ratio(t.bbox).toFixed(1)} 倍）`" @click="go(t.frameIndex)"><span v-if="missingTile(i)">读取失败</span></button>
            <label class="tile-label"><input type="checkbox" :checked="selected.has(t.frameIndex)" @change="toggle(t.frameIndex)" />{{ t.frameIndex + 1 }}</label>
          </div>
        </div>
        <nav v-if="pages > 1" class="filmstrip-pages" aria-label="翻页">
          <button class="quiet-button" :disabled="page === 0" @click="page--">上一页</button>
          <span>{{ page + 1 }} / {{ pages }}</span>
          <button class="quiet-button" :disabled="page >= pages - 1" @click="page++">下一页</button>
        </nav>
      </template>
    </section>
  </div></Teleport>
</template>

<style scoped>
.filmstrip-dialog { width: min(980px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto; display: grid; gap: 8px }
.filmstrip-head { display: flex; align-items: center; gap: 12px }
.filmstrip-head h2 { margin: 0; font-size: 16px }
.filmstrip-head .quiet-button { margin-left: auto }
.filmstrip-meta, .filmstrip-note { font-size: 12px; color: var(--muted) }
.filmstrip-odd-text { color: var(--danger) }
.filmstrip-curve { margin: 0 }
.filmstrip-curve svg { width: 100%; height: 70px; cursor: pointer; background: var(--surface-subtle); border-radius: 6px }
.filmstrip-curve figcaption { font-size: 11px; color: var(--muted) }
.band { fill: var(--accent-soft) }
.median { stroke: var(--muted); stroke-dasharray: 3 3; stroke-width: 1 }
.line { fill: none; stroke: var(--accent); stroke-width: 1.5; vector-effect: non-scaling-stroke }
.odd { fill: var(--danger) }
.now { stroke: var(--text); stroke-width: 1; vector-effect: non-scaling-stroke }
.filmstrip-tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center }
.filmstrip-error { margin: 0; background: var(--warning-soft); color: var(--warning); border-radius: 6px; padding: 6px 8px; font-size: 12px }
.filmstrip-grid { display: grid; grid-template-columns: repeat(auto-fill, 92px); gap: 6px }
.filmstrip-grid.loading { opacity: .6 }
.tile { display: grid; gap: 2px; border: 2px solid transparent; border-radius: 6px; padding: 1px }
.tile.odd { border-color: var(--danger) }
.tile.selected { background: var(--accent-soft) }
.tile.current .tile-label { font-weight: 700 }
.tile-image { width: 88px; height: 88px; padding: 0; border: 0; border-radius: 4px; background-color: #202020; background-repeat: no-repeat; cursor: pointer; color: #ddd; font-size: 11px }
.tile-label { display: flex; align-items: center; gap: 3px; font-size: 11px; font-variant-numeric: tabular-nums }
.filmstrip-pages { display: flex; gap: 8px; align-items: center; justify-content: center; font-size: 12px }
</style>
