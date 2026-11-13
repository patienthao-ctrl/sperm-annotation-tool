<script setup lang="ts">
// Short help: the three steps and this page's keys. The full guide stays one click away as a download.
import { computed, nextTick, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { guide, helpOpen, shortcuts, steps } from '../help/guide'
import { motionAllowed } from '../utils/motion'

const props = defineProps<{ page: string }>()
const icons = ['box', 'check', 'upload']
const keyTab = ref<'annotate' | 'review'>('annotate')
watch(helpOpen, open => { if (open) keyTab.value = props.page === '/review' ? 'review' : 'annotate' })
const keys = computed(() => shortcuts[keyTab.value])
/** "Ctrl/⌘ + Shift + Z" → chips; "/" and "+" between keys stay as small separators. */
const chips = (text: string) => text.split(/(\s+[+/]\s+)/).filter(Boolean).map(part => ({ sep: /^\s+[+/]\s+$/.test(part), text: part.trim() }))

const panel = ref<HTMLElement | null>(null)
let returnFocus: HTMLElement | null = null
watch(helpOpen, async open => {
  if (open) { returnFocus = document.activeElement as HTMLElement | null; await nextTick(); panel.value?.querySelector<HTMLElement>('.help-foot .btn-primary')?.focus() }
  else { returnFocus?.focus?.(); returnFocus = null }
})
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); helpOpen.value = false; return }
  if (event.key !== 'Tab') return
  const nodes = Array.from(panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled)') || [])
  const first = nodes[0], last = nodes[nodes.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
function download() {
  const url = URL.createObjectURL(new Blob([guide], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a'); link.href = url; link.download = '微流控标注工作台-使用说明.md'; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="help" :css="motionAllowed">
      <div v-if="helpOpen" class="help-mask" @mousedown.self="helpOpen = false">
        <section ref="panel" class="help-panel" role="dialog" aria-modal="true" aria-labelledby="help-title" data-testid="help-dialog" @keydown="keydown">
          <header class="help-head">
            <h2 id="help-title">怎么用</h2>
            <button class="icon-button help-close" aria-label="关闭帮助" @click="helpOpen = false"><AppIcon name="close" :size="16" /></button>
          </header>
          <ol class="help-steps">
            <li v-for="(step, i) in steps" :key="step.title" :style="{ '--d': `${i * 60}ms` }">
              <span class="help-step-icon"><AppIcon :name="icons[i] || 'check'" :size="18" /></span>
              <span class="help-step-no">{{ i + 1 }}</span>
              <strong>{{ step.title }}</strong>
              <p>{{ step.text }}</p>
            </li>
          </ol>
          <div class="help-keys-head">
            <h3>快捷键</h3>
            <div class="help-tabs" role="tablist" :style="{ '--i': keyTab === 'review' ? 1 : 0 }"><i aria-hidden="true" />
              <button role="tab" :aria-selected="keyTab === 'annotate'" :class="{ on: keyTab === 'annotate' }" @click="keyTab = 'annotate'">标注</button>
              <button role="tab" :aria-selected="keyTab === 'review'" :class="{ on: keyTab === 'review' }" @click="keyTab = 'review'">审查</button>
            </div>
          </div>
          <Transition name="help-swap" mode="out-in" :css="motionAllowed">
            <dl :key="keyTab" class="help-keys">
              <div v-for="row in keys" :key="row.keys"><dt><template v-for="(chip, n) in chips(row.keys)" :key="n"><span v-if="chip.sep" class="help-sep">{{ chip.text }}</span><kbd v-else>{{ chip.text }}</kbd></template></dt><dd>{{ row.action }}</dd></div>
            </dl>
          </Transition>
          <footer class="help-foot">
            <button class="quiet-button" @click="download"><AppIcon name="upload" :size="14" />下载完整说明</button>
            <button class="btn-primary" @click="helpOpen = false">知道了</button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.help-mask{position:fixed;inset:0;z-index:900;display:grid;place-items:center;padding:20px;background:var(--mask);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
.help-panel{width:min(640px,100%);max-height:calc(100vh - 40px);overflow:auto;padding:22px 24px 18px;border-radius:var(--radius-xl);background:var(--surface-raised);color:var(--text);border:1px solid var(--line);box-shadow:var(--shadow-lg)}
.help-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.help-head h2{margin:0;font-size:var(--fs-xl);font-weight:700;letter-spacing:.02em}
.help-steps{list-style:none;margin:0 0 22px;padding:0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.help-steps li{position:relative;display:flex;flex-direction:column;gap:6px;padding:14px 14px 13px;border-radius:var(--radius-lg);background:var(--surface-subtle);border:1px solid var(--line);animation:help-rise var(--dur-slow) var(--ease-out) both;animation-delay:var(--d)}
.help-step-icon{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent)}
.help-step-no{position:absolute;right:12px;top:10px;font:600 var(--fs-xs) var(--font-mono);color:var(--muted);opacity:.7}
.help-steps strong{font-size:var(--fs-md)}
.help-steps p{margin:0;font-size:var(--fs-xs);line-height:1.6;color:var(--muted)}
.help-keys-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.help-keys-head h3{margin:0;font-size:var(--fs-md);font-weight:650}
.help-tabs{position:relative;display:grid;grid-template-columns:1fr 1fr;padding:2px;border-radius:var(--radius-md);background:var(--surface-subtle);box-shadow:inset 0 0 0 1px var(--line)}
.help-tabs i{position:absolute;left:2px;top:2px;bottom:2px;width:calc(50% - 2px);border-radius:var(--radius-sm);background:var(--surface-raised);box-shadow:var(--shadow-sm);transform:translateX(calc(var(--i) * 100%));transition:transform var(--dur-base) var(--ease-glide)}
.help-tabs button{position:relative;z-index:1;padding:4px 14px;font-size:var(--fs-xs);font-weight:600;color:var(--muted);transition:color var(--dur-fast) var(--ease-out)}
.help-tabs button.on{color:var(--text)}
.help-keys{margin:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 20px}
.help-keys>div{display:flex;align-items:center;gap:10px;min-height:32px;padding:3px 0;border-bottom:1px solid var(--line)}
.help-keys dt{flex:none;min-width:116px;display:flex;align-items:center;gap:3px;flex-wrap:wrap}
.help-keys kbd{display:inline-grid;place-items:center;min-width:22px;height:22px;padding:0 6px;border-radius:5px;border:1px solid var(--control-line);border-bottom-width:2px;background:var(--surface);font:600 11px var(--font-mono);color:var(--text)}
.help-sep{font-size:var(--fs-2xs);color:var(--muted)}
.help-keys dd{margin:0;font-size:var(--fs-xs);color:var(--text);line-height:1.45}
.help-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:18px}
.help-foot .quiet-button{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:var(--fs-xs)}
@keyframes help-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.help-enter-active,.help-leave-active{transition:opacity var(--dur-base) var(--ease-out)}
.help-enter-active .help-panel,.help-leave-active .help-panel{transition:transform var(--dur-base) var(--ease-out),opacity var(--dur-base) var(--ease-out)}
.help-enter-from,.help-leave-to{opacity:0}
.help-enter-from .help-panel{transform:translateY(8px) scale(.98);opacity:0}
.help-leave-to .help-panel{transform:scale(.985);opacity:0}
.help-swap-enter-active,.help-swap-leave-active{transition:opacity var(--dur-fast) var(--ease-out)}
.help-swap-enter-from,.help-swap-leave-to{opacity:0}
@media(max-width:620px){.help-steps{grid-template-columns:minmax(0,1fr)}.help-keys{grid-template-columns:minmax(0,1fr)}}
@media(prefers-reduced-motion:reduce){.help-steps li{animation:none}}
</style>
