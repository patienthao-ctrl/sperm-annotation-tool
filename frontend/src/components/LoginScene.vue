<script setup lang="ts">
// Login backdrop: a microscope field that opens, comes into focus, lets a few points of light
// travel along gently curving paths leaving a dotted track (one dot per frame, as a detection is
// linked frame to frame), locks yellow brackets onto them as a scan line passes, then slows to a
// stop and stays as a still picture. No body or tail is drawn: points and their tracks only.
// Everything is a pure function of time, so a resize or theme change redraws the frozen frame.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { motionAllowed } from '../utils/motion'

const props = defineProps<{ theme: 'light' | 'dark' }>()
const emit = defineEmits<{ settled: [] }>()
const canvas = ref<HTMLCanvasElement | null>(null)

const END = 3.6                 // Seconds of animation; the picture is still from here on.
const SLOW_FROM = 2.55, SLOW_TO = 3.45
const SIM_END = SLOW_FROM + (SLOW_TO - SLOW_FROM) / 2
/** Simulated time: runs at full speed, then eases to a stop between SLOW_FROM and SLOW_TO. */
const sim = (t: number) => {
  if (t <= SLOW_FROM) return t
  const u = Math.min(1, (t - SLOW_FROM) / (SLOW_TO - SLOW_FROM))
  return SLOW_FROM + (SLOW_TO - SLOW_FROM) * (u - u * u / 2)
}
const clamp = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v))
const ease = (u: number) => 1 - Math.pow(1 - clamp(u), 3)
const span = (t: number, a: number, b: number) => ease((t - a) / (b - a))

// Fixed seed: the still picture is a designed composition, the same every time.
function rng(seed: number) {
  return () => { seed |= 0; seed = seed + 0x6d2b79f5 | 0; let x = Math.imul(seed ^ seed >>> 15, 1 | seed); x = x + Math.imul(x ^ x >>> 7, 61 | x) ^ x; return ((x ^ x >>> 14) >>> 0) / 4294967296 }
}
const random = rng(20261112)
// Positions in field radii, centre (0, 0).
const debris = Array.from({ length: 150 }, () => {
  const a = random() * Math.PI * 2, r = Math.sqrt(random()) * 0.97
  return { x: Math.cos(a) * r, y: Math.sin(a) * r, size: 0.0035 + random() * 0.006, alpha: 0.18 + random() * 0.4, jx: random() * 6, jy: random() * 6 }
})
type Swimmer = { x: number; y: number; dx: number; dy: number; speed: number; amp: number; freq: number; phase: number }
// Where each one comes to rest (spread over the field) and the way it swims; the start follows.
const swimmers: Swimmer[] = [
  [-0.55, -0.22, 0.94, -0.34], [-0.18, 0.52, 0.55, 0.83], [0.16, -0.56, -0.28, -0.96], [0.56, -0.08, 0.97, 0.24],
  [0.04, 0.10, -0.92, 0.39], [0.40, 0.50, 0.30, -0.95], [-0.62, 0.30, -0.71, 0.70],
].map(([ex, ey, dx, dy]) => {
  const speed = 0.13 + random() * 0.06
  return { x: ex - dx * speed * SIM_END, y: ey - dy * speed * SIM_END, dx, dy, speed, amp: 0.05 + random() * 0.04, freq: 0.22 + random() * 0.12, phase: random() * 6 }
})
const TRAIL = 2.2  // Simulated seconds of path behind each point.
const DOT_STEP = 1 / 12  // One track dot per frame of a 12 fps recording.
const at = (s: Swimmer, time: number) => {
  const along = s.speed * time, wave = s.amp * Math.sin(time * s.freq * Math.PI * 2 + s.phase)
  return { x: s.x + s.dx * along - s.dy * wave, y: s.y + s.dy * along + s.dx * wave }
}

const palette = {
  dark: { page: '#061114', inner: '#0c2428', outer: '#071619', dot: '158,230,225', grid: 'rgba(158,230,225,.07)', rim: 'rgba(158,230,225,.32)', trail: '116,196,193', head: '#e9fffd', bracket: '#ffc542', scan: '158,230,225', hud: 'rgba(158,230,225,.6)', vignette: 'rgba(3,10,12,.75)' },
  light: { page: '#eef3f4', inner: '#f7fafa', outer: '#e2eaeb', dot: '31,51,59', grid: 'rgba(19,109,112,.09)', rim: 'rgba(19,109,112,.36)', trail: '19,109,112', head: '#0b3f42', bracket: '#c98a12', scan: '19,109,112', hud: 'rgba(31,51,59,.55)', vignette: 'rgba(196,210,212,.45)' },
}

function layout(w: number, h: number) {
  if (w >= 860) return { cx: w * 0.36, cy: h * 0.5, r: Math.min(w * 0.27, h * 0.38) }
  const r = Math.min(w * 0.38, h * 0.16)  // Below the brand line, above the card (see .login-spacer).
  return { cx: w / 2, cy: 84 + r, r }
}

function render(t: number) {
  const el = canvas.value
  if (!el) return
  const dpr = Math.min(2, window.devicePixelRatio || 1), w = el.clientWidth, h = el.clientHeight
  if (!w || !h) return
  if (el.width !== Math.round(w * dpr) || el.height !== Math.round(h * dpr)) { el.width = Math.round(w * dpr); el.height = Math.round(h * dpr) }
  const ctx = el.getContext('2d')!, c = palette[props.theme], { cx, cy, r } = layout(w, h), s = sim(t)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = c.page; ctx.fillRect(0, 0, w, h)
  const open = span(t, 0, 0.95), focus = span(t, 0.45, 1.65), px = (v: number) => v * r

  // Field of view: opens like an iris.
  ctx.save()
  ctx.beginPath(); ctx.arc(cx, cy, r * (0.04 + 0.96 * open), 0, Math.PI * 2); ctx.clip()
  const bg = ctx.createRadialGradient(cx, cy - r * 0.2, r * 0.1, cx, cy, r)
  bg.addColorStop(0, c.inner); bg.addColorStop(1, c.outer)
  ctx.fillStyle = bg; ctx.fillRect(cx - r, cy - r, 2 * r, 2 * r)

  // Counting-chamber grid, visible once in focus.
  ctx.globalAlpha = focus; ctx.strokeStyle = c.grid; ctx.lineWidth = 1
  ctx.beginPath()
  for (let k = -5; k <= 5; k++) { const v = cx + k * r / 5; ctx.moveTo(v, cy - r); ctx.lineTo(v, cy + r); const u = cy + k * r / 5; ctx.moveTo(cx - r, u); ctx.lineTo(cx + r, u) }
  ctx.stroke(); ctx.globalAlpha = 1

  // Debris: soft and large out of focus, sharp points once focused; a slight drift stops with time.
  for (const d of debris) {
    const x = cx + px(d.x + 0.004 * Math.sin(s * 1.7 + d.jx)), y = cy + px(d.y + 0.004 * Math.cos(s * 1.3 + d.jy))
    const size = px(d.size) * (1 + 5 * (1 - focus))
    ctx.fillStyle = `rgba(${c.dot},${d.alpha * (0.22 + 0.78 * focus) / (1 + 2 * (1 - focus))})`
    ctx.beginPath(); ctx.arc(x, y, Math.max(0.6, size), 0, Math.PI * 2); ctx.fill()
  }

  // Swimmers: a bright point and its dotted track, older dots smaller and fainter.
  const heads: { x: number; y: number; lock: number }[] = []
  const scale = Math.max(1, r / 300)
  for (const sw of swimmers) {
    const from = Math.max(0, s - TRAIL)
    ctx.strokeStyle = `rgba(${c.trail},${0.16 * focus})`; ctx.lineWidth = 0.8
    ctx.beginPath()
    for (let i = 0; i <= 40; i++) { const p = at(sw, from + (s - from) * i / 40); if (i) ctx.lineTo(cx + px(p.x), cy + px(p.y)); else ctx.moveTo(cx + px(p.x), cy + px(p.y)) }
    ctx.stroke()
    for (let time = Math.ceil(from / DOT_STEP) * DOT_STEP; time < s - DOT_STEP / 2; time += DOT_STEP) {
      const p = at(sw, time), k = 1 - (s - time) / TRAIL
      ctx.fillStyle = `rgba(${c.trail},${(0.08 + 0.62 * k * k) * focus})`
      ctx.beginPath(); ctx.arc(cx + px(p.x), cy + px(p.y), (0.7 + 1.1 * k) * scale, 0, Math.PI * 2); ctx.fill()
    }
    const head = at(sw, s), hx = cx + px(head.x), hy = cy + px(head.y)
    const glow = ctx.createRadialGradient(hx, hy, 0, hx, hy, 9)
    glow.addColorStop(0, `rgba(${c.trail},${0.45 * focus})`); glow.addColorStop(1, `rgba(${c.trail},0)`)
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(hx, hy, 9, 0, Math.PI * 2); ctx.fill()
    ctx.fillStyle = c.head; ctx.globalAlpha = focus; ctx.beginPath(); ctx.arc(hx, hy, Math.max(2.4, r * 0.009), 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1
    // The scan line reaches this height at...
    heads.push({ x: hx, y: hy, lock: 1.7 + 0.95 * clamp((head.y + 1) / 2) })
  }

  // Scan line sweeping down; brackets lock on as it passes.
  const sweep = (t - 1.7) / 0.95
  if (sweep > 0 && sweep < 1.15) {
    const y = cy - r + 2 * r * sweep, fade = 1 - clamp((sweep - 0.9) / 0.25)
    const band = ctx.createLinearGradient(0, y - r * 0.18, 0, y)
    band.addColorStop(0, `rgba(${c.scan},0)`); band.addColorStop(1, `rgba(${c.scan},${0.16 * fade})`)
    ctx.fillStyle = band; ctx.fillRect(cx - r, y - r * 0.18, 2 * r, r * 0.18)
    ctx.strokeStyle = `rgba(${c.scan},${0.5 * fade})`; ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(cx - r, y); ctx.lineTo(cx + r, y); ctx.stroke()
  }
  let locked = 0
  for (const head of heads) {
    const u = clamp((t - head.lock) / 0.28)
    if (u <= 0) continue
    locked++
    const grow = 1 + 0.7 * (1 - ease(u)), half = Math.max(9, r * 0.034) * grow, arm = half * 0.55
    ctx.strokeStyle = c.bracket; ctx.globalAlpha = ease(u); ctx.lineWidth = 1.8; ctx.lineCap = 'square'
    ctx.beginPath()
    for (const [sx, sy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      const x = head.x + sx * half, y = head.y + sy * half
      ctx.moveTo(x, y - sy * arm); ctx.lineTo(x, y); ctx.lineTo(x - sx * arm, y)
    }
    ctx.stroke(); ctx.globalAlpha = 1
  }
  // Soft edge inside the field.
  const edge = ctx.createRadialGradient(cx, cy, r * 0.86, cx, cy, r)
  edge.addColorStop(0, 'rgba(0,0,0,0)'); edge.addColorStop(1, c.vignette)
  ctx.fillStyle = edge; ctx.fillRect(cx - r, cy - r, 2 * r, 2 * r)
  ctx.restore()

  // Focus ring: ticks turn while focusing and come to rest with the picture.
  const turn = 0.9 * (1 - span(t, 0.2, SLOW_TO))
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(turn); ctx.globalAlpha = open
  ctx.strokeStyle = c.rim; ctx.lineWidth = 1
  ctx.beginPath(); ctx.arc(0, 0, r + 1, 0, Math.PI * 2); ctx.stroke()
  ctx.beginPath()
  for (let k = 0; k < 120; k++) {
    const a = k / 120 * Math.PI * 2, long = k % 10 === 0, l = long ? 10 : 4
    ctx.moveTo(Math.cos(a) * (r + 6), Math.sin(a) * (r + 6)); ctx.lineTo(Math.cos(a) * (r + 6 + l), Math.sin(a) * (r + 6 + l))
  }
  ctx.stroke(); ctx.restore()

  // Readout under the field: frame counter stops, target count fills.
  if (w >= 520) {
    ctx.globalAlpha = span(t, 0.6, 1.2); ctx.fillStyle = c.hud
    ctx.font = `500 11px ${getComputedStyle(el).getPropertyValue('--font-mono') || 'monospace'}`
    ctx.textAlign = 'left'; ctx.fillText(`FRAME ${String(Math.round(s * 24)).padStart(4, '0')}`, cx - r * 0.62, cy + r + 34)
    ctx.textAlign = 'right'; ctx.fillText(`TARGETS ${String(locked).padStart(2, '0')}`, cx + r * 0.62, cy + r + 34)
    ctx.globalAlpha = 1
  }
}

let frame = 0, startedAt = 0, settled = false
function loop(now: number) {
  if (!startedAt) startedAt = now
  const t = (now - startedAt) / 1000
  render(Math.min(t, END))
  if (t < END) frame = requestAnimationFrame(loop)
  else { frame = 0; settled = true; emit('settled') }
}
const still = () => { settled = true; render(END); emit('settled') }
const redraw = () => { if (settled) render(END) }
onMounted(() => {
  if (!motionAllowed.value || document.hidden) still()
  else frame = requestAnimationFrame(loop)
  window.addEventListener('resize', redraw)
})
onBeforeUnmount(() => { if (frame) cancelAnimationFrame(frame); window.removeEventListener('resize', redraw) })
watch(() => props.theme, redraw)
watch(motionAllowed, allowed => { if (!allowed && frame) { cancelAnimationFrame(frame); frame = 0; still() } })
</script>

<template>
  <canvas ref="canvas" class="login-scene" aria-hidden="true" />
</template>

<style scoped>
.login-scene { position:fixed; inset:0; width:100%; height:100%; display:block; }
</style>
