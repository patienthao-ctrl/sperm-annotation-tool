// Turns the tracking task status into the stepped progress shown in the AI card,
// header, frame strip and media list. Every number comes from frames the backend
// reports as processed; there is no blended "overall percent" and no time estimate.

export type TrackingStepKey = 'forward' | 'backward' | 'combine' | 'detect' | 'link'
export type TrackingStepState = 'pending' | 'active' | 'done'
export type TrackingProgressMode = 'queued' | 'preparing' | 'running' | 'finishing'

export interface TrackingProgressInput {
  status?: 'queued' | 'running' | 'success' | 'failed' | 'paused'
  stage?: string
  phase?: string
  phaseFrom?: number
  phaseTo?: number
  phaseCurrent?: number
  lastProcessedFrame?: number
  elapsedSeconds?: number
  /** First frame of the forward pass (0-based). */
  startFrame: number
  /** Last frame the run must reach (0-based). */
  endFrame: number
  /** The run walks back from a human tail frame, then combines both directions. */
  endpoint: boolean
  combine: boolean
  /** The task finished and the browser is reading the published result. */
  loadingResult?: boolean
  /** Detector prelabel: every frame is detected, then boxes are linked into tracks. */
  prelabel?: boolean
}

export interface TrackingStep {
  key: TrackingStepKey
  label: string
  state: TrackingStepState
  /** 0–1 from processed frames; null while the stage has no frame count yet. */
  fraction: number | null
}

export interface TrackingProgressView {
  mode: TrackingProgressMode
  steps: TrackingStep[]
  /** Short stage name, e.g. 正向追踪. */
  title: string
  /** Real frame position, e.g. 第 120 / 300 帧. */
  detail: string
  /** Compact header text, e.g. 正向 120/300. */
  compact: string
  /** Frame the model is on now (0-based), for the frame-strip scan head. */
  frame: number | null
  direction: 1 | -1
  /** Frames already covered by the active stage (inclusive, 0-based). */
  covered: [number, number] | null
  elapsed: string
}

const LABELS: Record<TrackingStepKey, string> = { forward: '正向追踪', backward: '反向核验', combine: '综合标注', detect: '逐帧识别', link: '连成轨迹' }
const SHORT: Record<TrackingStepKey, string> = { forward: '正向', backward: '反向', combine: '综合', detect: '识别', link: '连接' }

const clamp01 = (value: number) => Math.min(1, Math.max(0, value))
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const ratio = (done: number, total: number) => (total > 0 ? clamp01(done / total) : 1)

export function formatElapsed(seconds?: number): string {
  if (!finite(seconds) || seconds < 0) return ''
  const total = Math.floor(seconds), m = Math.floor(total / 60), s = total % 60
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

export function describeTrackingProgress(input: TrackingProgressInput): TrackingProgressView {
  if (input.prelabel) return describePrelabel(input)
  const keys: TrackingStepKey[] = ['forward']
  if (input.endpoint) keys.push('backward')
  if (input.endpoint && input.combine) keys.push('combine')
  const start = Math.max(0, input.startFrame), end = Math.max(start, input.endFrame)
  const elapsed = formatElapsed(input.elapsedSeconds)
  const build = (active: TrackingStepKey | null, fraction: number | null, allDone = false): TrackingStep[] => {
    const at = active ? keys.indexOf(active) : -1
    return keys.map((key, i) => ({
      key, label: LABELS[key],
      state: allDone || (at >= 0 && i < at) ? 'done' : i === at ? 'active' : 'pending',
      fraction: allDone || (at >= 0 && i < at) ? 1 : i === at ? fraction : 0,
    }))
  }
  const view = (mode: TrackingProgressMode, steps: TrackingStep[], title: string, detail: string, compact: string, extra: Partial<TrackingProgressView> = {}): TrackingProgressView =>
    ({ mode, steps, title, detail, compact, frame: null, direction: 1, covered: null, elapsed, ...extra })

  if (input.loadingResult || input.status === 'success')
    return view('finishing', build(null, 1, true), '正在载入结果', '全部阶段已完成，正在把结果放到画面上', '载入结果')
  if (input.status === 'queued' || input.stage === 'queued')
    return view('queued', build(null, 0), '排队中', '其他视频的追踪完成后自动开始，可先标注其他视频', '排队中')

  const phase = input.stage === 'checking' && (input.phase === 'backward' || input.phase === 'combine') && keys.includes(input.phase) ? input.phase : null
  if (phase === 'backward' && finite(input.phaseFrom) && finite(input.phaseTo) && finite(input.phaseCurrent)) {
    const from = input.phaseFrom, to = input.phaseTo, current = Math.min(from, Math.max(to, input.phaseCurrent))
    const total = from - to, done = from - current
    return view('running', build('backward', ratio(done, total)), LABELS.backward, `从尾帧往回，已核验 ${done} / ${total} 帧`, `${SHORT.backward} ${done}/${total}`,
      { frame: current, direction: -1, covered: [current, from] })
  }
  if (phase === 'combine' && finite(input.phaseFrom) && finite(input.phaseTo) && finite(input.phaseCurrent)) {
    const from = input.phaseFrom, to = input.phaseTo, current = Math.min(to, Math.max(from, input.phaseCurrent))
    const total = to - from + 1, done = current - from + 1
    return view('running', build('combine', ratio(done, total)), LABELS.combine, `第 ${current + 1} / ${to + 1} 帧`, `${SHORT.combine} ${done}/${total}`,
      { frame: current, covered: [from, current] })
  }
  if (input.stage === 'decoding' || input.stage === 'preparing_model' || !input.stage)
    return view('preparing', build('forward', null), !input.stage ? '正在启动追踪' : input.stage === 'preparing_model' ? '正在准备模型' : '正在读取原始帧', '马上开始逐帧追踪', '准备中')
  if (input.stage === 'saving_results' || input.stage === 'checking')
    return view('running', build('forward', 1), LABELS.forward, input.stage === 'saving_results' ? '正在保存本段结果' : '正在核验疑点片段', `${SHORT.forward} 保存`,
      { covered: [start, end] })
  const last = finite(input.lastProcessedFrame) ? Math.min(end, Math.max(start, input.lastProcessedFrame)) : start
  const total = end - start + 1, done = last - start + 1
  return view('running', build('forward', ratio(done, total)), LABELS.forward, `第 ${last + 1} / ${end + 1} 帧`, `${SHORT.forward} ${last + 1}/${end + 1}`,
    { frame: last, covered: [start, last] })
}

function describePrelabel(input: TrackingProgressInput): TrackingProgressView {
  const end = Math.max(0, input.endFrame), total = end + 1
  const elapsed = formatElapsed(input.elapsedSeconds)
  const steps = (detect: TrackingStepState, fraction: number | null, link: TrackingStepState): TrackingStep[] => [
    { key: 'detect', label: LABELS.detect, state: detect, fraction: detect === 'done' ? 1 : fraction },
    { key: 'link', label: LABELS.link, state: link, fraction: link === 'done' ? 1 : link === 'active' ? null : 0 },
  ]
  const base = { frame: null, direction: 1 as const, covered: null, elapsed }
  if (input.loadingResult || input.status === 'success')
    return { ...base, mode: 'finishing', steps: steps('done', 1, 'done'), title: '正在载入结果', detail: '识别完成，正在把框放到画面上', compact: '载入结果' }
  if (input.status === 'queued' || input.stage === 'queued')
    return { ...base, mode: 'queued', steps: steps('pending', 0, 'pending'), title: '排队中', detail: '其他视频的 AI 任务完成后自动开始，可先看其他视频', compact: '排队中' }
  if (input.stage === 'linking')
    return { ...base, mode: 'running', steps: steps('done', 1, 'active'), title: LABELS.link, detail: '正在把各帧的框连成目标', compact: '连接中', covered: [0, end] }
  if (!finite(input.lastProcessedFrame))
    return { ...base, mode: 'preparing', steps: steps('active', null, 'pending'), title: '正在准备模型', detail: '马上开始逐帧识别', compact: '准备中' }
  const last = Math.min(end, Math.max(0, input.lastProcessedFrame)), done = last + 1
  return { ...base, mode: 'running', steps: steps('active', ratio(done, total), 'pending'), title: LABELS.detect,
    detail: `第 ${done} / ${total} 帧`, compact: `${SHORT.detect} ${done}/${total}`, frame: last, covered: [0, last] }
}
