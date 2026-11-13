<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

// Overview of the whole frame while zoomed in: shows where the viewport is and
// pans the canvas scroller on click/drag. Display only; never touches boxes.
const props = defineProps<{
  source: HTMLImageElement | HTMLVideoElement | null
  scroller: HTMLElement | null
  stage: HTMLElement | null
  mediaWidth: number
  mediaHeight: number
  redrawKey: string
  /** In the side panel: always shown, stretched to the panel width. Otherwise it floats over the canvas. */
  docked?: boolean
  width?: number
}>()

const WIDTH = props.width ?? 176
const canvas = ref<HTMLCanvasElement | null>(null)
const view = ref({ x: 0, y: 0, w: 1, h: 1 })
const height = computed(() => Math.max(40, Math.round(WIDTH * (props.mediaHeight || 3) / (props.mediaWidth || 4))))
const zoomedIn = computed(() => view.value.w < 0.995 || view.value.h < 0.995)
let raf = 0, dragging = false

function measure() {
  raf = 0
  const sc = props.scroller, st = props.stage
  if (!sc || !st) return
  const s = st.getBoundingClientRect(), v = sc.getBoundingClientRect()
  if (!s.width || !s.height) return
  const left = v.left + sc.clientLeft, top = v.top + sc.clientTop
  const x0 = Math.max(0, (left - s.left) / s.width), y0 = Math.max(0, (top - s.top) / s.height)
  const x1 = Math.min(1, (left + sc.clientWidth - s.left) / s.width), y1 = Math.min(1, (top + sc.clientHeight - s.top) / s.height)
  view.value = { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) }
}
const schedule = () => { if (!raf) raf = requestAnimationFrame(measure) }

function draw() {
  const ctx = canvas.value?.getContext('2d'), src = props.source
  if (!ctx) return
  ctx.fillStyle = '#0b1218'; ctx.fillRect(0, 0, WIDTH, height.value)
  if (!src) return
  try { ctx.drawImage(src, 0, 0, WIDTH, height.value) } catch { /* not decoded yet */ }
}

function panTo(event: PointerEvent) {
  const sc = props.scroller, st = props.stage, c = canvas.value
  if (!sc || !st || !c) return
  const r = c.getBoundingClientRect()
  const nx = Math.min(1, Math.max(0, (event.clientX - r.left) / r.width)), ny = Math.min(1, Math.max(0, (event.clientY - r.top) / r.height))
  const s = st.getBoundingClientRect(), v = sc.getBoundingClientRect()
  sc.scrollBy(s.left + nx * s.width - (v.left + sc.clientWidth / 2), s.top + ny * s.height - (v.top + sc.clientHeight / 2))
}
function down(event: PointerEvent) { dragging = true; canvas.value?.setPointerCapture(event.pointerId); panTo(event) }
function move(event: PointerEvent) { if (dragging) panTo(event) }
function up(event: PointerEvent) { dragging = false; canvas.value?.releasePointerCapture(event.pointerId) }

let observed: HTMLElement | null = null, resize: ResizeObserver | null = null
function bindScroller(el: HTMLElement | null) {
  observed?.removeEventListener('scroll', schedule)
  resize?.disconnect()
  observed = el
  if (!el) return
  el.addEventListener('scroll', schedule, { passive: true })
  resize = new ResizeObserver(schedule)
  resize.observe(el)
  if (props.stage) resize.observe(props.stage)
}
let boundSource: HTMLImageElement | HTMLVideoElement | null = null
function bindSource(el: HTMLImageElement | HTMLVideoElement | null) {
  boundSource?.removeEventListener('load', draw); boundSource?.removeEventListener('seeked', draw)
  boundSource = el
  el?.addEventListener('load', draw); el?.addEventListener('seeked', draw)
}
watch(() => props.scroller, bindScroller)
watch(() => props.stage, () => bindScroller(props.scroller))
watch(() => props.source, el => { bindSource(el); void nextTick(draw) })
watch(() => [props.redrawKey, height.value], async () => { await nextTick(); draw(); schedule() })
onMounted(() => { bindScroller(props.scroller); bindSource(props.source); draw(); schedule() })
onUnmounted(() => { bindScroller(null); bindSource(null); if (raf) cancelAnimationFrame(raf) })
defineExpose({ refresh: schedule })
</script>

<template>
  <div v-show="docked || zoomedIn" class="annot-minimap" :class="{ docked }" data-testid="annotation-minimap" :style="docked ? undefined : { width: `${WIDTH + 2}px` }">
    <canvas ref="canvas" :width="WIDTH" :height="height" aria-label="全图小地图，点击或拖动移动视野" role="img" @pointerdown.prevent="down" @pointermove="move" @pointerup="up" @pointercancel="up" />
    <i :style="{ left: `${view.x * 100}%`, top: `${view.y * 100}%`, width: `${view.w * 100}%`, height: `${view.h * 100}%` }" />
  </div>
</template>

<style scoped>
.annot-minimap{position:absolute;right:12px;bottom:12px;z-index:32;border:1px solid #6b8290;border-radius:6px;overflow:hidden;background:#0b1218;box-shadow:0 6px 20px #0006}
.annot-minimap canvas{display:block;cursor:crosshair;touch-action:none}
.annot-minimap.docked{position:relative;right:auto;bottom:auto;z-index:auto;margin:0 12px 12px;box-shadow:none;border-color:var(--line-strong)}
.annot-minimap.docked canvas{width:100%;height:auto}
.annot-minimap.docked.focus-map{width:52px;margin:0;border-radius:4px;flex:none}
.annot-minimap i{position:absolute;border:1.5px solid #ffd36d;background:#ffd36d22;pointer-events:none;box-sizing:border-box}
</style>
