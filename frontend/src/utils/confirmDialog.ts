// One in-app confirmation at a time, in place of window.confirm: short, styled like the app,
// and usable from stores. `askConfirm` resolves true only when the person presses the confirm button.
import { ref } from 'vue'

export interface ConfirmOptions {
  title: string
  /** One short line at most; leave it out when the title says enough. */
  body?: string
  confirm?: string
  cancel?: string
  danger?: boolean
}
export const confirmRequest = ref<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null)

export function askConfirm(options: ConfirmOptions): Promise<boolean> {
  confirmRequest.value?.resolve(false)  // A new question replaces an unanswered one, which counts as cancelled.
  return new Promise(resolve => { confirmRequest.value = { ...options, resolve } })
}

export function answerConfirm(ok: boolean) {
  const request = confirmRequest.value
  confirmRequest.value = null
  request?.resolve(ok)
}
