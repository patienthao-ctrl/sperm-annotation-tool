<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { answerConfirm, confirmRequest } from '../utils/confirmDialog'
import { motionAllowed } from '../utils/motion'

const cancelButton = ref<HTMLButtonElement | null>(null), okButton = ref<HTMLButtonElement | null>(null)
let returnFocus: HTMLElement | null = null
watch(confirmRequest, async (request, before) => {
  if (request && !before) returnFocus = document.activeElement as HTMLElement | null
  if (request) {
    await nextTick()
    // A destructive question starts on Cancel, so a stray Enter never deletes anything.
    ;(request.danger ? cancelButton.value : okButton.value)?.focus()
  } else {
    returnFocus?.focus?.(); returnFocus = null
  }
})
function keys(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); answerConfirm(false); return }
  if (event.key === 'Tab') {
    event.preventDefault()
    ;(document.activeElement === cancelButton.value ? okButton.value : cancelButton.value)?.focus()
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="confirm" :css="motionAllowed">
      <div v-if="confirmRequest" class="confirm-backdrop" @mousedown.self="answerConfirm(false)">
        <section class="confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" :aria-describedby="confirmRequest.body ? 'confirm-body' : undefined" data-testid="confirm-dialog" @keydown="keys">
          <span class="confirm-icon" :class="{ danger: confirmRequest.danger }" aria-hidden="true"><AppIcon :name="confirmRequest.danger ? 'trash' : 'alert'" :size="20" /></span>
          <h2 id="confirm-title">{{ confirmRequest.title }}</h2>
          <p v-if="confirmRequest.body" id="confirm-body">{{ confirmRequest.body }}</p>
          <div class="confirm-actions">
            <button ref="cancelButton" type="button" class="confirm-button" data-testid="confirm-cancel" @click="answerConfirm(false)">{{ confirmRequest.cancel || '取消' }}</button>
            <button ref="okButton" type="button" class="confirm-button primary" :class="{ danger: confirmRequest.danger }" data-testid="confirm-ok" @click="answerConfirm(true)">{{ confirmRequest.confirm || '确定' }}</button>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.confirm-backdrop { position:fixed; inset:0; z-index:1000; display:grid; place-items:center; padding:16px; background:var(--mask); -webkit-backdrop-filter:blur(3px); backdrop-filter:blur(3px); }
.confirm-card { width:min(360px, 100%); padding:24px 22px 18px; border-radius:var(--radius-xl); background:var(--surface-raised); color:var(--text); box-shadow:var(--shadow-lg); border:1px solid var(--line); display:grid; justify-items:center; text-align:center; gap:8px; }
.confirm-icon { width:44px; height:44px; border-radius:50%; display:grid; place-items:center; margin-bottom:4px; background:var(--accent-soft); color:var(--accent); }
.confirm-icon.danger { background:var(--danger-soft); color:var(--danger); }
.confirm-card h2 { margin:0; font-size:var(--fs-lg); font-weight:650; line-height:1.4; word-break:break-all; }
.confirm-card p { margin:0; color:var(--muted); font-size:var(--fs-sm); line-height:1.5; }
.confirm-actions { width:100%; display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:12px; }
.confirm-button { height:38px; border-radius:var(--radius-md); border:1px solid var(--control-line); background:var(--surface); color:var(--text); font-size:var(--fs-md); font-weight:600; cursor:pointer; transition:background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out); }
.confirm-button:hover { background:var(--surface-hover); }
.confirm-button:active { transform:scale(.98); }
.confirm-button:focus-visible { outline:2px solid var(--accent-ring); outline-offset:2px; }
.confirm-button.primary { background:var(--accent); border-color:var(--accent); color:var(--on-accent); }
.confirm-button.primary:hover { background:var(--accent-hover); }
.confirm-button.primary.danger { background:var(--danger); border-color:var(--danger); color:#fff; }
.confirm-button.primary.danger:hover { filter:brightness(1.08); }
:global(:root[data-theme=dark]) .confirm-button.primary.danger { color:#2a0f16; }
.confirm-enter-active, .confirm-leave-active { transition:opacity var(--dur-base) var(--ease-out); }
.confirm-enter-active .confirm-card, .confirm-leave-active .confirm-card { transition:transform var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out); }
.confirm-enter-from, .confirm-leave-to { opacity:0; }
.confirm-enter-from .confirm-card { transform:translateY(6px) scale(.97); opacity:0; }
.confirm-leave-to .confirm-card { transform:scale(.98); opacity:0; }
</style>
