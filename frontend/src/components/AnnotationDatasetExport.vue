<script setup lang="ts">
import { askConfirm } from '../utils/confirmDialog'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { addLeaveGuard } from '../router'
import { useWorkspace } from '../stores/workspace'
import { useAuth } from '../stores/auth'
import { createRequestId } from '../utils/browserCompat'
import { annotationReviewApi } from '../api/annotationReviewApi'
import { trainingExportApi, type ExportListItem } from '../api/trainingExportApi'
import { useAnnotationReview } from '../stores/annotationReview'
import TrainingDatasetExport from './TrainingDatasetExport.vue'

// Step 1 picks reviewed videos, step 2 (TrainingDatasetExport) sets the package options.
// Every video still has to pass the server's review gate when its snapshot is fixed.
const MAX_VIDEOS = 100
const { selectedMedia, currentMediaId, persistWorkspaceState, mediaAssets, submissionLocks, refreshLibrary } = useWorkspace()
const review = useAnnotationReview()
const auth = useAuth()
const me = computed(() => Number(auth.user.value?.id))

const visible = ref(false), preparing = ref(false), progress = ref(0), error = ref(''), failures = ref<{ name: string; reason: string }[]>([])
const folder = ref(''), onlyMine = ref(true), onlyNew = ref(true), chosen = ref<string[]>([])
const jobs = ref<ExportListItem[]>([]), exported = ref<Record<string, string>>({}), jobError = ref('')
const snapshots = ref<string[]>([]), names = ref<Record<string, string>>({}), exportOpen = ref(false), exportBusy = ref(false)
const exporter = ref<InstanceType<typeof TrainingDatasetExport> | null>(null)
const dialog = ref<HTMLElement | null>(null)
let priorFocus: HTMLElement | null = null, poll: ReturnType<typeof setTimeout> | null = null
const lock = createRequestId()

const statusOf = (id: string) => review.summary.value[id]
const passedVideos = computed(() => mediaAssets.value.filter(media => media.serverMediaId && statusOf(media.serverMediaId) === 'passed'))
const staleCount = computed(() => mediaAssets.value.filter(media => media.serverMediaId && statusOf(media.serverMediaId) === 'stale' && inFolder(media.folderPath)).length)
const inFolder = (path?: string) => !folder.value || (folder.value === '\u0000' ? !path : path === folder.value || !!path?.startsWith(`${folder.value}/`))
const passedAt = (id: string) => review.people.value[id]?.passedAt
const isExported = (id: string) => { const at = exported.value[id], passed = passedAt(id); return !!at && (!passed || Date.parse(at) >= Date.parse(passed)) }
const currentId = computed(() => selectedMedia.value?.serverMediaId)
const candidates = computed(() => passedVideos.value.filter(media => {
  const id = media.serverMediaId!
  // The open video stays listed so exporting just this one never needs a filter change.
  return id === currentId.value || (inFolder(media.folderPath) && (!onlyMine.value || review.people.value[id]?.passedBy === me.value) && (!onlyNew.value || !isExported(id)))
}))
const folders = computed(() => {
  const out = new Set<string>()
  for (const media of passedVideos.value) { const parts = (media.folderPath ?? '').split('/').filter(Boolean); parts.forEach((_, i) => out.add(parts.slice(0, i + 1).join('/'))) }
  return [...out].sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }))
})
const allChosen = computed(() => candidates.value.length > 0 && candidates.value.every(media => chosen.value.includes(media.serverMediaId!)))
const toggleAll = () => { chosen.value = allChosen.value ? [] : candidates.value.map(media => media.serverMediaId!) }
const chosenFrames = computed(() => mediaAssets.value.filter(media => media.serverMediaId && chosen.value.includes(media.serverMediaId)).reduce((total, media) => total + (media.frameCount || 0), 0))
const nameOf = (id: string) => mediaAssets.value.find(media => media.serverMediaId === id)?.name || id
const passedDate = (id: string) => { const at = passedAt(id); return at ? new Date(at).toLocaleDateString() : '' }
const working = computed(() => jobs.value.some(job => job.state === 'queued' || job.state === 'running'))
const blocking = computed(() => preparing.value || exportBusy.value)

watch([visible, preparing, exportOpen, exportBusy], () => { if (visible.value || preparing.value || exportOpen.value || exportBusy.value) submissionLocks.value.add(lock); else submissionLocks.value.delete(lock) })
watch(visible, async value => { if (value) { priorFocus = document.activeElement as HTMLElement; await nextTick(); dialog.value?.querySelector<HTMLElement>('button:not(:disabled),input:not(:disabled)')?.focus() } else { stopPoll(); priorFocus?.focus() } })
const removeGuard = addLeaveGuard(async () => !preparing.value && !exportBusy.value)
function beforeUnload(e: BeforeUnloadEvent) { if (preparing.value || exportBusy.value) { e.preventDefault(); e.returnValue = '' } }
window.addEventListener('beforeunload', beforeUnload)
onUnmounted(() => { removeGuard(); stopPoll(); submissionLocks.value.delete(lock); window.removeEventListener('beforeunload', beforeUnload) })

function stopPoll() { if (poll) clearTimeout(poll); poll = null }
async function loadJobs() {
  stopPoll()
  try { const result = await trainingExportApi.list(); jobs.value = result.items; exported.value = result.exported; jobError.value = '' }
  catch (e: any) { jobError.value = e?.message || '导出任务读取失败'; console.error('[dataset.job_list_failed]', e) }
  if (visible.value && working.value) poll = setTimeout(() => { void loadJobs() }, 3000)
}
async function open() {
  error.value = ''; failures.value = []; visible.value = true
  try { await persistWorkspaceState(currentMediaId.value, true) }
  catch (e: any) { error.value = e?.message || '当前视频保存失败，请先处理保存问题'; console.error('[dataset.flush_failed]', e) }
  await Promise.all([refreshLibrary(), loadJobs()])
  const current = currentId.value
  chosen.value = current && statusOf(current) === 'passed' ? [current] : []
}
/** A lost response is replayed with its original key; the server answers with the same snapshot. */
async function fixSnapshot(mediaId: string, body: unknown) {
  const key = createRequestId()
  try { return await trainingExportApi.annotationSnapshot(mediaId, body, key) }
  catch (e: any) { if (e?.status) throw e; console.warn('[dataset.snapshot_retry]', { mediaId }); return await trainingExportApi.annotationSnapshot(mediaId, body, key) }
}
async function next() {
  if (chosen.value.length > MAX_VIDEOS) { error.value = `一次最多导出 ${MAX_VIDEOS} 段，请分批导出`; return }
  preparing.value = true; progress.value = 0; error.value = ''; failures.value = []
  const ids: string[] = [], labels: Record<string, string> = {}
  for (const mediaId of chosen.value) {
    try {
      const preview = await annotationReviewApi.exportPreview(mediaId)
      if (preview.unknownFrames) throw new Error(`还有 ${preview.unknownFrames} 帧未确认，请回到审查处理`)
      const { finalVersionId } = await fixSnapshot(mediaId, { expectedSourceRevision: preview.sourceRevision, confirmComplete: true, explicitEmptyFrameRanges: [] })
      ids.push(finalVersionId); labels[finalVersionId] = nameOf(mediaId)
    } catch (e: any) {
      failures.value.push({ name: nameOf(mediaId), reason: e?.message || '读取失败，请重试' })
      console.error('[dataset.annotation_snapshot_failed]', { mediaId, error: e })
    }
    progress.value++
  }
  preparing.value = false
  chosen.value = chosen.value.filter(id => failures.value.some(item => item.name === nameOf(id)))
  if (failures.value.length && ids.length) error.value = `${ids.length} 段已准备好，下面 ${failures.value.length} 段没有通过，继续导出时不包含它们`
  if (!ids.length) return
  names.value = labels; snapshots.value = ids; pendingFailures = failures.value.length > 0
  if (pendingFailures) return
  await goToSettings()
}
let pendingFailures = false
async function goToSettings() {
  pendingFailures = false; visible.value = false
  await nextTick(); await exporter.value?.open()
}
const canContinue = computed(() => snapshots.value.length > 0 && failures.value.length > 0 && !preparing.value)
async function download(job: ExportListItem) {
  jobError.value = ''
  try {
    const current = await trainingExportApi.status(job.exportId)
    if (current.state !== 'ready' || !current.downloadUrl) { await loadJobs(); jobError.value = current.errorMessage || '这个包暂时不能下载'; return }
    const link = document.createElement('a')
    link.href = current.downloadUrl; link.download = `${job.mediaIds[0] ? nameOf(job.mediaIds[0]) : '数据集'}${job.videoCount > 1 ? `等${job.videoCount}段` : ''}-训练数据集-${job.exportId}.zip`
    link.click()
  } catch (e: any) { jobError.value = e?.message || '下载失败，请重试'; console.error('[dataset.download_failed]', e) }
}
async function remove(job: ExportListItem) {
  if (!(await askConfirm({ title: '删除这个 ZIP？', body: '已下载到电脑的文件不受影响。', confirm: '删除', danger: true }))) return
  try { await trainingExportApi.remove(job.exportId); await loadJobs() }
  catch (e: any) { jobError.value = e?.message || '删除失败，请重试'; console.error('[dataset.delete_failed]', e) }
}
const jobLabel = (job: ExportListItem) => ({ queued: '排队中', running: `生成中 ${job.completedFrames} / ${job.totalFrames} 帧`, ready: `已完成 ${new Date(job.updatedAt).toLocaleString()}`, failed: `未完成：${job.errorMessage || '请重新生成'}`, invalidated: `已失效：${job.errorMessage || ''}` } as Record<string, string>)[job.state] || job.state
function closeExporter(value: boolean) { exportOpen.value = value; if (!value) void refreshLibrary() }
function keys(e: KeyboardEvent) { if (e.key === 'Escape' && !preparing.value) visible.value = false }
</script>
<template>
  <button class="btn-primary" data-testid="annotation-export" :title="passedVideos.length ? '选择审查通过的视频导出' : '审查通过后才能导出训练数据集'" :disabled="preparing || !passedVideos.length" @click="open">导出训练数据集</button>
  <TrainingDatasetExport v-if="snapshots.length" :key="snapshots.join()" ref="exporter" hide-trigger annotation-only :final-version-id="snapshots[0]!" :extra-version-ids="snapshots.slice(1)" :names="names" :media-name="names[snapshots[0]!] || '视频'" @open="closeExporter" @busy="exportBusy = $event" />
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" @keydown="keys">
      <section ref="dialog" role="dialog" aria-modal="true" aria-label="选择要导出的视频" class="app-dialog batch-export" data-testid="batch-export">
        <h2>选择要导出的视频</h2>
        <div class="batch-export-filters">
          <select v-model="folder" aria-label="按文件夹筛选" data-testid="batch-export-folder"><option value="">全部文件夹</option><option value="&#0;">未分类</option><option v-for="item in folders" :key="item" :value="item">{{ item }}</option></select>
          <label><input v-model="onlyMine" type="checkbox" data-testid="batch-export-mine" />我审查通过的</label>
          <label><input v-model="onlyNew" type="checkbox" data-testid="batch-export-new" />只看未导出</label>
        </div>
        <p v-if="error" role="alert" class="text-[var(--danger)]">{{ error }}</p>
        <label class="batch-export-row batch-export-all"><input type="checkbox" data-testid="batch-export-all" :checked="allChosen" :disabled="!candidates.length" @change="toggleAll" /><span>全选 {{ candidates.length }} 段</span><em>已选 {{ chosen.length }} 段 · {{ chosenFrames }} 帧</em></label>
        <div class="batch-export-list" data-testid="batch-export-list">
          <label v-for="media in candidates" :key="media.id" class="batch-export-row"><input v-model="chosen" type="checkbox" :value="media.serverMediaId" /><span>{{ media.name }}</span><em>审查:{{ review.people.value[media.serverMediaId!]?.actorName || '—' }} {{ passedDate(media.serverMediaId!) }}<template v-if="isExported(media.serverMediaId!)"> · 已导出</template></em></label>
          <p v-if="!candidates.length" class="review-note">没有符合条件的视频，可以取消上面的勾选。</p>
        </div>
        <p v-if="staleCount" class="review-note" data-testid="batch-export-stale">另有 {{ staleCount }} 段需重新审查，不能导出</p>
        <ul v-if="failures.length" class="batch-export-failures" data-testid="batch-export-failures"><li v-for="item in failures" :key="item.name">{{ item.name }}：{{ item.reason }}</li></ul>
        <div class="batch-export-actions">
          <span v-if="preparing" aria-live="polite">正在准备 {{ progress }} / {{ chosen.length }} 段，请不要关闭页面</span>
          <button class="btn-secondary" data-testid="batch-export-close" :disabled="preparing" @click="visible = false">关闭</button>
          <button v-if="canContinue" class="btn-secondary" data-testid="batch-export-continue" @click="goToSettings">继续导出其余 {{ snapshots.length }} 段</button>
          <button class="btn-primary" data-testid="batch-export-next" :disabled="preparing || !chosen.length" @click="next">下一步：导出设置</button>
        </div>
        <h3>我的导出任务</h3>
        <p v-if="jobError" role="alert" class="text-[var(--danger)]" data-testid="batch-job-error">{{ jobError }}</p>
        <ul class="batch-export-jobs" data-testid="batch-export-jobs">
          <li v-for="job in jobs" :key="job.exportId"><span><b>{{ job.videoCount }} 段</b> {{ job.mediaIds[0] ? nameOf(job.mediaIds[0]) : '' }}<template v-if="job.videoCount > 1"> 等</template><small>{{ jobLabel(job) }}</small></span>
            <button v-if="job.state === 'ready'" class="quiet-button" data-testid="batch-job-download" @click="download(job)">下载</button>
            <button v-if="job.state !== 'queued' && job.state !== 'running'" class="quiet-button" data-testid="batch-job-delete" @click="remove(job)">删除</button></li>
          <li v-if="!jobs.length" class="review-note">还没有导出任务。</li>
        </ul>
      </section>
    </div>
  </Teleport>
</template>
<style scoped>
.batch-export { width: 640px; max-width: 100%; max-height: 90vh; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.batch-export h2 { margin-bottom: 4px; }
.batch-export h3 { margin-top: 8px; font-size: 14px; font-weight: 650; }
.batch-export-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; font-size: 13px; }
.batch-export-filters label { display: flex; align-items: center; gap: 5px; }
.batch-export-filters select { flex: 1 1 160px; min-width: 0; background: var(--surface-subtle); border: 1px solid var(--line); border-radius: 5px; padding: 6px 8px; color: var(--text); }
.batch-export-list { max-height: 240px; overflow-y: auto; border: 1px solid var(--line); border-radius: 5px; padding: 4px 8px; }
.batch-export-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 13px; }
.batch-export-row span { flex: 1; overflow-wrap: anywhere; }
.batch-export-row em { color: var(--muted); font-style: normal; font-size: 12px; }
.batch-export-all { padding: 0 8px; }
.batch-export-failures { margin: 0; padding-left: 16px; color: var(--danger); font-size: 12px; }
.batch-export-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; font-size: 12px; }
.batch-export-actions span { flex: 1 1 100%; color: var(--muted); }
.batch-export-jobs { margin: 0; padding: 0; list-style: none; font-size: 13px; }
.batch-export-jobs li { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-top: 1px solid var(--line); }
.batch-export-jobs li > span { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.batch-export-jobs small { display: block; color: var(--muted); font-size: 12px; }
</style>
