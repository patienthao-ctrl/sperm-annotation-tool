<script setup lang="ts">
import { createRequestId } from '../utils/browserCompat'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useAuth } from '../stores/auth'
import { trainingExportApi as api, type DatasetSplit, type ExportJob, type ExportSettings, type ExportSummary, type SplitOptions } from '../api/trainingExportApi'

const props = defineProps<{ finalVersionId: string; mediaName: string; disabled?: boolean; hideTrigger?:boolean; annotationOnly?:boolean; extraVersionIds?: string[]; names?: Record<string, string> }>()
const emit = defineEmits<{ open: [value: boolean]; busy: [value: boolean] }>()
const opened = ref(false), busy = ref(false), error = ref('')
const summary = ref<ExportSummary | null>(null), job = ref<ExportJob | null>(null)
const format = ref<ExportSettings['format']>('yolo'), splitRatio = ref(.7), testRatio = ref(.15)
const splitMode = ref<'video' | 'frame'>('video'), includeVideos = ref(true)
// The other videos of this package, chosen before this dialog opens.
const extraIds = ref<string[]>(props.extraVersionIds ?? []), overrides = ref<Record<string, DatasetSplit>>({})
const planError = ref('')
const splitNames: Record<DatasetSplit, string> = { train: '训练', val: '验证', test: '测试' }
const presets = computed(() => splitMode.value === 'video' ? [[.7, .15], [.8, .1], [.8, 0]] : [[.7, 0], [.8, 0], [.9, 0]])
const preset = computed({
  get: () => `${splitRatio.value}/${testRatio.value}`,
  set: (value: string) => { const [train, test] = value.split('/').map(Number); splitRatio.value = train; testRatio.value = test },
})
const percent = (x: number) => `${Math.round(x * 100)}%`
const ids = computed(() => [props.finalVersionId, ...extraIds.value])
const splitOptions = computed<SplitOptions>(() => splitMode.value === 'frame'
  ? { splitRatio: splitRatio.value, splitMode: 'frame' }
  : { splitRatio: splitRatio.value, splitMode: 'video', testRatio: testRatio.value, videoSplits: Object.fromEntries(Object.entries(overrides.value).filter(([id]) => ids.value.includes(id))) })
const videoName = (id: string) => props.names?.[id] || (id === props.finalVersionId ? props.mediaName : id)
const videoCount = (split: DatasetSplit) => summary.value?.videos?.filter(v => v.split === split).length || 0
const dialog = ref<HTMLElement | null>(null)
const storageKey = computed(() => `training-export:${useAuth().user.value?.id}:${props.finalVersionId}`)
const working = computed(() => job.value?.state === 'queued' || job.value?.state === 'running')
const trainCount = computed(() => summary.value ? Math.max(1, Math.min(summary.value.frameCount - 1, Math.floor(summary.value.frameCount * splitRatio.value))) : 0)
let planSeq = 0
const intent = ref<{ settings: ExportSettings; key: string; jobId?: string } | null>(null)
type RetryPhase = 'preview' | 'create' | 'status' | 'download'
const retryPhase = ref<RetryPhase>('preview')
let timer: ReturnType<typeof setTimeout> | null = null
let alive = true, automaticDownload = false, polling = false
let priorFocus: HTMLElement | null = null

watch(busy, value => emit('busy', value))
watch(opened, async value => {
  emit('open', value)
  if (value) {
    priorFocus = document.activeElement as HTMLElement
    await nextTick()
    dialog.value?.querySelector<HTMLElement>('button')?.focus()
  } else priorFocus?.focus()
})
function stopPolling() { if (timer) clearTimeout(timer); timer = null }
function persist() {
  try { if (intent.value) localStorage.setItem(storageKey.value, JSON.stringify(intent.value)) }
  catch (e) { console.warn('[dataset.recovery_storage_unavailable]', e) }
}
function fail(e: unknown, phase: RetryPhase) {
  const f = e as { message?: string; requestId?: string }
  error.value = `${f.message || '连接中断，请重试'}${f.requestId ? `（记录号 ${f.requestId}）` : ''}`
  retryPhase.value = phase
  console.error('[dataset.operation_failed]', { phase, finalVersionId: props.finalVersionId, exportId: job.value?.exportId, error: e })
}
function modeChanged() {
  if (splitMode.value === 'video') { splitRatio.value = .7; testRatio.value = .15 }
  else { splitRatio.value = .8; testRatio.value = 0 }
}
async function refreshPlan() {
  if (!opened.value) return
  const seq = ++planSeq
  try {
    const next = await api.preview(ids.value, splitOptions.value)
    if (seq === planSeq) { summary.value = next; planError.value = '' }
  } catch (e) {
    if (seq !== planSeq) return
    const f = e as { message?: string }
    planError.value = f.message || '无法预览划分，请重试'
    console.error('[dataset.plan_failed]', { ids: ids.value, error: e })
  }
}
watch([ids, splitOptions], () => { if (opened.value && summary.value) void refreshPlan() })
function setOverride(id: string, value: string) {
  const next = { ...overrides.value }
  if (value) next[id] = value as DatasetSplit; else delete next[id]
  overrides.value = next
}
async function open() {
  if (props.disabled || busy.value) return
  opened.value = true; error.value = ''; planError.value = ''; busy.value = true
  try {
    try {
      const stored = JSON.parse(localStorage.getItem(storageKey.value) || 'null')
      // Only the same set of videos continues an earlier job.
      if ([...(stored?.settings?.finalVersionIds ?? [])].sort().join() === [...ids.value].sort().join()) intent.value = stored
    } catch (e) { console.warn('[dataset.restore_failed]', e) }
    if (intent.value) {
      const saved = intent.value.settings
      format.value = saved.format; splitMode.value = saved.splitMode || 'frame'
      splitRatio.value = saved.splitRatio; testRatio.value = saved.testRatio || 0
      overrides.value = { ...(saved.videoSplits || {}) }
      includeVideos.value = !!saved.includeSourceVideos
    }
    summary.value = await api.preview(ids.value, splitOptions.value)
    if (intent.value) {
      if (intent.value.jobId) {
        try { job.value = await api.status(intent.value.jobId) }
        catch (e) {
          if ((e as {status?: number}).status !== 404) throw e
          console.warn('[dataset.expired_job]', {jobId: intent.value.jobId})
          intent.value = null; job.value = null; localStorage.removeItem(storageKey.value)
        }
      }
      else { error.value = '上次创建导出的响应尚未确认，可使用原请求重试。'; retryPhase.value = 'create' }
    }
  } catch (e) { fail(e, 'preview') }
  finally { busy.value = false }
  if (working.value) schedule()
}
function close() {
  if (busy.value) return
  opened.value = false; automaticDownload = false
}
function schedule() {
  stopPolling()
  if (alive && working.value) timer = setTimeout(() => { void refreshStatus() }, 1000)
}
async function refreshStatus() {
  if (!job.value || polling) return
  const id = job.value.exportId
  polling = true
  try {
    const next = await api.status(id)
    if (!alive || job.value?.exportId !== id) return
    job.value = next; error.value = ''
    if (next.state === 'ready' && automaticDownload && opened.value) {
      automaticDownload = false
      await download()
    }
    schedule()
  } catch (e) { if (alive) fail(e, 'status') }
  finally { polling = false }
}
async function create(retry = false) {
  if (busy.value || !summary.value || working.value) return
  error.value = ''; busy.value = true
  if (!retry) {
    intent.value = { settings: { finalVersionIds: ids.value, format: format.value, ...splitOptions.value, includeSourceVideos: includeVideos.value }, key: createRequestId() }
    job.value = null; persist()
  }
  if (!intent.value) { busy.value = false; return }
  try {
    job.value = await api.create(intent.value.settings, intent.value.key)
    intent.value.jobId = job.value.exportId; persist()
    automaticDownload = true
  } catch (e) { fail(e, 'create') }
  finally { busy.value = false }
  if (job.value?.state === 'ready') { automaticDownload = false; await download() }
  else schedule()
}
async function download() {
  if (!job.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    // The browser saves the file itself, so its size is not limited by page memory or a
    // timeout, but it cannot report a refusal: ask first and show any problem here.
    const current = await api.status(job.value.exportId)
    job.value = current
    if (current.state !== 'ready' || !current.downloadUrl) return
    const link = document.createElement('a'), count = intent.value?.settings.finalVersionIds.length || 1
    link.href = current.downloadUrl; link.download = `${props.mediaName}${count > 1 ? `等${count}个视频` : ''}-训练数据集-${current.exportId}.zip`
    link.click()
  } catch (e) { fail(e, 'download') }
  finally { busy.value = false }
}
async function retry() {
  error.value = ''
  if (retryPhase.value === 'create') await create(true)
  else if (retryPhase.value === 'status') await refreshStatus()
  else if (retryPhase.value === 'download') await download()
  else await open()
}
function keys(e: KeyboardEvent) {
  if (!opened.value) return
  if (e.key === 'Escape') { e.preventDefault(); close() }
  if (e.key === 'Tab' && dialog.value) {
    const nodes = Array.from(dialog.value.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled)'))
    const first = nodes[0], last = nodes[nodes.length - 1]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
  }
}
defineExpose({open})
window.addEventListener('keydown', keys)
onUnmounted(() => { alive = false; stopPolling(); emit('open', false); emit('busy', false); window.removeEventListener('keydown', keys) })
</script>

<template>
  <button v-if="!hideTrigger" class="c-primary c-wide" data-testid="export-training" :disabled="disabled || busy" @click="open">
    {{ working ? '查看数据集导出进度' : '导出训练数据集' }}
  </button>
  <Teleport to="body">
    <div v-if="opened" class="dialog-host training-export-host">
      <div class="c-modal-mask" @click.self="close">
        <section ref="dialog" class="c-modal training-export-dialog" role="dialog" aria-modal="true" aria-labelledby="training-export-title">
          <h2 id="training-export-title">{{ annotationOnly?'导出训练数据集':'导出已确认训练数据集' }}</h2>
          <p class="training-export-sub">{{ mediaName }}{{ extraIds.length ? ` 等 ${ids.length} 个视频` : '' }} · {{ annotationOnly?'审查通过的标注':'已确认的最终版本' }}</p>
          <div v-if="summary" class="training-export-summary">
            <span><b>{{ summary.frameCount }}</b> 帧</span>
            <span><b>{{ summary.objectCount }}</b> 框</span>
            <span><b>{{ summary.emptyFrames }}</b> 空帧</span>
            <span class="training-export-classes" :title="`类别：${summary.classNames.join(' · ')}`">{{ summary.classNames.join(' · ') }}</span>
          </div>
          <div v-if="error" role="alert" class="confirmation-error">
            <span>{{ error }}</span><button :disabled="busy" @click="retry">重试导出请求</button>
          </div>
          <template v-if="summary">
            <div class="training-export-options">
              <label class="training-export-field">格式
                <select v-model="format" aria-label="数据集格式" :disabled="busy || working || !!error">
                  <option value="yolo">YOLO</option>
                  <option value="coco">COCO</option>
                  <option value="both">YOLO + COCO</option>
                </select>
              </label>
              <label class="training-export-field" title="按视频：同一视频的帧只进一个集合，验证/测试反映模型在没见过的视频上的效果。按帧：相邻帧几乎相同，验证指标偏高，仅用于调试。">划分
                <select v-model="splitMode" aria-label="划分方式" :disabled="busy || working || !!error" @change="modeChanged">
                  <option value="video">按视频（推荐）</option>
                  <option value="frame">按帧（调试）</option>
                </select>
              </label>
              <label class="training-export-field">{{ splitMode === 'video' ? '训练/验证/测试' : '训练/验证' }}
                <select v-model="preset" aria-label="训练验证比例" :disabled="busy || working || !!error">
                  <option v-for="[train, test] in presets" :key="`${train}/${test}`" :value="`${train}/${test}`">
                    {{ percent(train) }} / {{ percent(1 - train - test) }}{{ splitMode === 'video' ? ` / ${percent(test)}` : '' }}
                  </option>
                </select>
              </label>
            </div>
            <div v-if="splitMode === 'video' && summary.videos && summary.videos.length > 1" class="training-export-videos">
              <strong>每个视频的去向</strong>
              <label v-for="v in summary.videos" :key="v.finalVersionId">
                <span>{{ videoName(v.finalVersionId) }} · {{ v.frameCount }} 帧</span>
                <select :aria-label="`${videoName(v.finalVersionId)} 的划分`" :value="overrides[v.finalVersionId] || ''" :disabled="busy || working || !!error" @change="setOverride(v.finalVersionId, ($event.target as HTMLSelectElement).value)">
                  <option value="">自动：{{ v.split ? splitNames[v.split] : '' }}</option>
                  <option value="train">训练</option><option value="val">验证</option><option value="test">测试</option>
                </select>
              </label>
            </div>
            <p v-if="planError" role="alert" class="confirmation-error">{{ planError }}</p>
            <p v-if="splitMode === 'video' && summary.splitFrames" class="training-export-plan" title="自动划分按视频内容固定，重复或分批导出时去向不变；视频少时比例会有偏差，可手动指定。">
              训练 {{ summary.splitFrames.train }} · 验证 {{ summary.splitFrames.val }} · 测试 {{ summary.splitFrames.test }} 帧
              <template v-if="summary.videos?.length === 1">
                <select :aria-label="`${videoName(summary.videos[0].finalVersionId)} 的划分`" :value="overrides[summary.videos[0].finalVersionId] || ''" :disabled="busy || working || !!error" @change="setOverride(summary.videos[0].finalVersionId, ($event.target as HTMLSelectElement).value)">
                  <option value="">整段自动进{{ summary.videos[0].split ? splitNames[summary.videos[0].split] : '' }}</option>
                  <option value="train">整段进训练</option><option value="val">整段进验证</option><option value="test">整段进测试</option>
                </select>
              </template>
            </p>
            <p v-else-if="summary" class="training-export-plan">训练 {{ trainCount }} · 验证 {{ summary.frameCount - trainCount }} 帧</p>
            <label class="training-export-check" title="原视频放在 videos/，ZIP 可在“导入标注包”里导回标注工具复查和修改。">
              <input v-model="includeVideos" type="checkbox" :disabled="busy || working || !!error" />
              <span>包含原视频 <small>可导回标注工具复查，体积较大</small></span>
            </label>
          </template>
          <div v-if="job" class="training-export-job" aria-live="polite">
            <template v-if="working">
              <strong>{{ job.state === 'queued' ? '等待导出' : '正在提取图像和生成标签' }}</strong>
              <progress :max="job.totalFrames" :value="job.completedFrames" />
              <span>{{ job.completedFrames }} / {{ job.totalFrames }} 帧 · 关闭弹窗后后台继续</span>
            </template>
            <template v-else-if="job.state === 'ready'">
              <strong>数据集已生成（{{ intent?.settings.format.toUpperCase() }}）</strong>
              <p>训练 {{ job.manifest?.counts.train }} 帧 · 验证 {{ job.manifest?.counts.val }} 帧<template v-if="job.manifest?.counts.test"> · 测试 {{ job.manifest?.counts.test }} 帧</template></p>
            </template>
            <template v-else><strong>导出未完成</strong><p role="alert">{{ job.errorMessage }}</p></template>
            <small class="training-export-id">任务号：{{ job.exportId }}</small>
          </div>
          <p v-if="!summary && !error">正在校验最终版本…</p>
          <div class="training-export-actions">
            <button :disabled="busy" @click="close">{{ working ? '后台继续' : '关闭' }}</button>
            <button v-if="summary && !working && job?.state !== 'invalidated'" class="c-primary" :disabled="busy || !!planError || (!!error && (retryPhase === 'create' || retryPhase === 'preview'))" @click="create(false)">
              {{ busy ? '正在处理…' : job ? '重新生成 ZIP' : '生成并下载 ZIP' }}
            </button>
            <button v-if="job?.state === 'ready'" class="c-primary" :disabled="busy" @click="download">下载 ZIP</button>
          </div>
        </section>
      </div>
    </div>
  </Teleport>
</template>

<style src="./export-dialog.css"></style>
<style scoped>
.training-export-host { min-height: 0; padding: 0; }
.training-export-dialog { width: 520px; }
.training-export-sub { color: var(--muted); margin-bottom: 12px; overflow-wrap: anywhere; }
.training-export-summary { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 16px; padding: 10px 12px; margin-bottom: 14px; background: var(--surface-subtle); border-radius: 5px; color: var(--muted); }
.training-export-summary b { color: var(--accent); font-size: 17px; }
.training-export-classes { flex: 1; min-width: 0; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.training-export-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.training-export-field { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: 12px; }
.training-export-field select, .training-export-plan select { background: var(--surface-subtle); border: 1px solid var(--line); border-radius: 5px; padding: 6px 8px; color: var(--text); font-size: 13px; min-width: 0; }
.training-export-plan { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin: 12px 0 0; color: var(--text); }
.training-export-job { display: flex; flex-direction: column; gap: 10px; background: var(--surface-subtle); border: 1px solid var(--line); border-radius: 5px; padding: 12px; margin-top: 14px; }
.training-export-job p { margin-bottom: 0; }
.training-export-id { overflow-wrap: anywhere; }
.training-export-videos { display: flex; flex-direction: column; gap: 6px; max-height: 200px; overflow-y: auto; padding: 10px 12px; margin: 12px 0 0; background: var(--surface-subtle); border: 1px solid var(--line); border-radius: 5px; }
.training-export-videos em { color: var(--muted); font-style: normal; }
.training-export-videos label { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--text); }
.training-export-videos label span { flex: 1; overflow-wrap: anywhere; }
.training-export-videos select { background: var(--surface); border: 1px solid var(--line); border-radius: 5px; padding: 4px 8px; color: var(--text); }
.training-export-check { display: flex; gap: 8px; align-items: center; margin: 12px 0 0; color: var(--text); }
.training-export-check small { color: var(--muted); margin-left: 4px; }
.training-export-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; margin-top: 16px; }
@media (max-width: 560px) { .training-export-options { grid-template-columns: 1fr; } }
</style>
