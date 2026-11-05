// Rules for transient toasts and for unresolved notices. Kept free of Vue so the timing
// and collapse rules can be tested directly. See docs/WORKFLOW.md (提示与告警).
export type ToastTone = 'info' | 'success' | 'warning' | 'error'
export interface Toast { id: number; message: string; tone: ToastTone }

// How long each tone stays on screen, in ms. Failures get longer because they must be read.
export const TOAST_DURATION: Record<ToastTone, number> = { info: 3000, success: 3000, warning: 4500, error: 6000 }

export function createToastController(
  onChange: (toast: Toast | null) => void,
  timers: { set: (run: () => void, ms: number) => unknown; clear: (handle: unknown) => void } = { set: (run, ms) => setTimeout(run, ms), clear: handle => clearTimeout(handle as ReturnType<typeof setTimeout>) },
) {
  let current: Toast | null = null, handle: unknown = null, nextId = 1
  const dismiss = () => {
    if (handle !== null) timers.clear(handle)
    handle = null; current = null; onChange(null)
  }
  const show = (message: string, tone: ToastTone = 'info') => {
    // Repeating the visible message must not extend it: restarting the timer on every call
    // (held-down shortcuts, repeated failures) is how a toast could stay up indefinitely.
    if (current && current.message === message && current.tone === tone) return
    if (handle !== null) timers.clear(handle)
    current = { id: nextId++, message, tone }
    handle = timers.set(dismiss, TOAST_DURATION[tone])
    onChange(current)
  }
  return { show, dismiss }
}

// Collapsing an unresolved notice only hides its text. The key names the exact failure, so a
// different failure reopens, and a resolved failure is forgotten so that a recurrence reopens.
export const retainActiveNotices = (collapsed: string[], active: string[]) => collapsed.filter(key => active.includes(key))
export const collapseNotice = (collapsed: string[], key: string) => key && !collapsed.includes(key) ? [...collapsed, key] : collapsed
