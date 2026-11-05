<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import TrackingProgress from './TrackingProgress.vue'
import FolderOrganizer from './FolderOrganizer.vue'
import { useWorkspace } from '../stores/workspace'
import { useAuth } from '../stores/auth'
import { useAnnotationReview, reviewFilterKey, reviewFilterOptions, type ReviewFilterKey } from '../stores/annotationReview'
import { clearImportReport, importCounts, importCurrent, importItems, importPlan, importRunning, prepareImport, retryFailedImports, startImport } from '../stores/importQueue'
import type { MediaAsset } from '../types/annotation'

// Finding and switching videos only; editing state is never changed here.
const props = defineProps<{ busy: boolean; reviewing: boolean }>()
const emit = defineEmits<{ select: [id: string] }>()
const { mediaAssets, selectedMediaId, isTrackingMedia, trackingProgressViewFor, deleteMedia, workspaceRestoring, openFilePicker, packageInputRef } = useWorkspace()
const review = useAnnotationReview()
const auth = useAuth()

const search = ref(''), status = ref<ReviewFilterKey | ''>(''), mine = ref(false), organizing = ref(false), reportOpen = ref(false)
const folderInput = ref<HTMLInputElement | null>(null)
onMounted(() => { if (folderInput.value) folderInput.value.webkitdirectory = true })
function pickedFolder(event: Event) { const input = event.target as HTMLInputElement; prepareImport(input.files); input.value = '' }

const COLLAPSED_KEY = 'media-library-collapsed'
const readCollapsed = () => { try { return new Set<string>(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]')) } catch { return new Set<string>() } }
const collapsed = ref(readCollapsed())
function toggle(path: string) {
  const next = new Set(collapsed.value)
  if (next.has(path)) next.delete(path); else next.add(path)
  collapsed.value = next
  try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next])) } catch (error) { console.warn('[ui.preference_save_failed]', { preference: COLLAPSED_KEY, error }) }
}

const me = computed(() => Number(auth.user.value?.id))
const who = (media: MediaAsset) => media.serverMediaId ? review.people.value[media.serverMediaId] : undefined
const statusOf = (media: MediaAsset) => media.serverMediaId && review.summary.value[media.serverMediaId] ? review.summary.value[media.serverMediaId]! : null
const mineMatches = (media: MediaAsset) => { const person = who(media), state = statusOf(media); return state === 'passed' ? person?.passedBy === me.value : state === 'open' && person?.actorId === me.value }
const visible = computed(() => {
  const term = search.value.trim().toLowerCase()
  return mediaAssets.value.filter(media => (!term || `${media.folderPath ?? ''}/${media.name}`.toLowerCase().includes(term))
    && (!status.value || (media.serverMediaId && reviewFilterKey(media.serverMediaId) === status.value))
    && (!mine.value || mineMatches(media)))
})
const filtering = computed(() => !!search.value.trim() || !!status.value || mine.value)

type Row = { kind: 'folder'; path: string; name: string; depth: number; total: number; passed: number; ready: number } | { kind: 'video'; media: MediaAsset; depth: number }
interface Node { path: string; name: string; depth: number; folders: Map<string, Node>; videos: MediaAsset[] }
const UNFILED = '\u0000unfiled'
const hasFolders = computed(() => mediaAssets.value.some(media => media.folderPath))
const rows = computed<Row[]>(() => {
  if (!hasFolders.value) return visible.value.map(media => ({ kind: 'video', media, depth: 0 }))
  const root: Node = { path: '', name: '', depth: -1, folders: new Map(), videos: [] }
  for (const media of visible.value) {
    let node = root
    for (const part of (media.folderPath || UNFILED).split('/')) {
      if (!node.folders.has(part)) node.folders.set(part, { path: node.path ? `${node.path}/${part}` : part, name: part === UNFILED ? '未分类' : part, depth: node.depth + 1, folders: new Map(), videos: [] })
      node = node.folders.get(part)!
    }
    node.videos.push(media)
  }
  const all = (node: Node): MediaAsset[] => [...node.videos, ...[...node.folders.values()].flatMap(all)]
  const out: Row[] = []
  const walk = (node: Node) => {
    const children = [...node.folders.values()].sort((a, b) => a.path === UNFILED ? 1 : b.path === UNFILED ? -1 : a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
    for (const child of children) {
      const videos = all(child), keys = videos.map(media => media.serverMediaId ? reviewFilterKey(media.serverMediaId) : null)
      out.push({ kind: 'folder', path: child.path, name: child.name, depth: child.depth, total: videos.length, passed: keys.filter(key => key === 'passed').length, ready: keys.filter(key => key === 'ready').length })
      if (filtering.value || !collapsed.value.has(child.path)) { walk(child); out.push(...child.videos.map(media => ({ kind: 'video' as const, media, depth: child.depth + 1 }))) }
    }
  }
  walk(root)
  return out
})
const select = (media: MediaAsset) => { if (!props.busy) emit('select', media.id) }
const stateLabel: Record<string, string> = { waiting: '等待', uploading: '上传中', done: '成功', exists: '已存在', failed: '失败' }
</script>

<template>
  <div class="asset-panel annot-assets">
    <div v-if="!reviewing" class="asset-actions library-actions">
      <button class="btn-primary" :disabled="busy" @click="openFilePicker('video')"><AppIcon name="upload" :size="15" />导入视频</button>
      <button class="btn-secondary" data-testid="import-folder" title="选择一个文件夹，导入里面的全部视频和标注包，并按目录结构分类" @click="folderInput?.click()">文件夹</button>
      <button class="btn-secondary" :disabled="busy" title="导入别人用本工具导出的训练数据集 ZIP（导出时需勾选“包含原视频”），可多选，导入后在审查页检查和修改" @click="packageInputRef?.click()">标注包</button>
      <button class="btn-secondary" :disabled="busy" @click="openFilePicker('image')">图片</button>
      <input ref="folderInput" type="file" hidden data-testid="folder-input" @change="pickedFolder" />
    </div>
    <div v-if="importItems.length" class="library-import" data-testid="import-progress">
      <div class="library-import-head">
        <strong>{{ importRunning ? `导入中 ${importCounts.total - importCounts.left} / ${importCounts.total}` : '导入完成' }}</strong>
        <span>成功 {{ importCounts.done }} · 已存在 {{ importCounts.exists }} · 失败 {{ importCounts.failed }}</span>
      </div>
      <template v-if="importCurrent"><small class="library-import-file" :title="importCurrent.name">{{ importCurrent.name }}</small><progress :max="1" :value="importCurrent.progress" /></template>
      <div class="library-import-actions">
        <button class="quiet-button" data-testid="import-report-toggle" @click="reportOpen = !reportOpen">{{ reportOpen ? '收起明细' : '查看明细' }}</button>
        <button v-if="importCounts.failed && !importRunning" class="quiet-button" data-testid="import-retry" @click="retryFailedImports">重试失败项</button>
        <button v-if="!importRunning" class="quiet-button" @click="clearImportReport(); reportOpen = false">清除</button>
      </div>
      <ul v-if="reportOpen" class="library-import-report" data-testid="import-report">
        <li v-for="item in importItems" :key="item.id" :class="item.state"><b>{{ stateLabel[item.state] }}</b> <span :title="item.folderPath">{{ item.name }}</span><small v-if="item.message"> {{ item.message }}</small></li>
      </ul>
    </div>
    <div class="asset-search"><input v-model="search" class="input" placeholder="查找素材…" aria-label="查找素材" /></div>
    <div class="library-filters">
      <select v-model="status" aria-label="按状态筛选" data-testid="library-status"><option value="">全部状态</option><option v-for="option in reviewFilterOptions" :key="option.key" :value="option.key">{{ option.label }}</option></select>
      <label title="我点了“审查通过”的，以及我正在审查的"><input v-model="mine" type="checkbox" data-testid="library-mine" />我审查的</label>
      <button class="quiet-button" data-testid="library-organize" @click="organizing = true">整理</button>
    </div>
    <div class="asset-list" data-testid="library-tree">
      <template v-for="row in rows" :key="row.kind === 'folder' ? `f:${row.path}` : row.media.id">
        <button v-if="row.kind === 'folder'" class="library-folder" data-testid="library-folder" :style="{ paddingLeft: `${6 + row.depth * 12}px` }" :aria-expanded="filtering || !collapsed.has(row.path)" @click="toggle(row.path)">
          <span class="library-caret">{{ filtering || !collapsed.has(row.path) ? '▾' : '▸' }}</span><strong :title="row.name">{{ row.name }}</strong>
          <small>{{ row.total }} 段<template v-if="row.passed"> · 通过 {{ row.passed }}</template><template v-if="row.ready"> · 待审查 {{ row.ready }}</template></small>
        </button>
        <div v-else class="asset-card" :class="{ selected: selectedMediaId === row.media.id }" :style="row.depth ? { marginLeft: `${row.depth * 12}px` } : undefined" role="button" :tabindex="busy ? -1 : 0" :aria-disabled="busy" @click="select(row.media)" @keydown.enter="select(row.media)" @keydown.space.prevent.stop="select(row.media)">
          <span class="asset-type">{{ row.media.type === 'video' ? 'VID' : 'IMG' }}</span>
          <div>
            <strong :title="row.media.name">{{ row.media.name }}</strong>
            <small>{{ row.media.type === 'video' ? `${row.media.frameCount || '—'} 帧 · ${row.media.fps?.toFixed(1) || 30} fps` : `${row.media.width || '—'} × ${row.media.height || '—'}` }}<b v-if="statusOf(row.media)" class="asset-review" :class="statusOf(row.media)!">{{ review.label(row.media.serverMediaId, statusOf(row.media)!) }}<template v-if="who(row.media)?.actorName"> · {{ who(row.media)!.actorName }}</template></b><b v-if="isTrackingMedia(row.media.id)" class="asset-review asset-tracking" data-testid="asset-tracking">追踪中</b></small>
            <TrackingProgress v-if="isTrackingMedia(row.media.id)" variant="mini" :view="trackingProgressViewFor(row.media.id)" />
          </div>
          <button class="asset-delete icon-button" :disabled="busy || workspaceRestoring || isTrackingMedia(row.media.id)" :title="row.media.serverMediaId ? '删除素材（从服务器移入回收目录，页面上不能撤销）' : '删除素材（清除本机工作区内容，保留电脑原文件）'" :aria-label="`删除素材 ${row.media.name}`" @click.stop="deleteMedia(row.media.id)" @keydown.enter.stop @keydown.space.stop><AppIcon name="trash" :size="14" /></button>
        </div>
      </template>
      <p v-if="!visible.length" class="sidebar-empty">{{ filtering ? '没有匹配素材' : '导入视频或图片开始标注' }}</p>
    </div>
    <FolderOrganizer v-if="organizing" @close="organizing = false" />
    <Teleport to="body">
      <div v-if="importPlan" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" @keydown.esc.prevent="importPlan = null">
        <section class="app-dialog w-[520px]" role="dialog" aria-modal="true" aria-label="确认导入" data-testid="import-confirm">
          <h2>确认导入</h2>
          <p data-testid="import-summary">视频 {{ importPlan.items.filter(item => item.kind === 'video').length }} 段 · 标注包 {{ importPlan.items.filter(item => item.kind === 'package').length }} 个 · 跳过其他文件 {{ importPlan.skipped.length }} 个</p>
          <p v-if="importPlan.tooLarge.length" role="alert" class="text-[var(--danger)]">{{ importPlan.tooLarge.length }} 个文件超过大小上限，不会上传。</p>
          <label class="block my-3">放到文件夹（留空为未分类）<input v-model="importPlan.folder" class="input w-full" data-testid="import-folder-name" /></label>
          <label v-if="importPlan.hasSubfolders" class="flex gap-2 my-2"><input v-model="importPlan.keepSubfolders" type="checkbox" />保留子文件夹结构</label>
          <label v-if="importPlan.existing.length" class="flex gap-2 my-2"><input v-model="importPlan.uploadExisting" type="checkbox" data-testid="import-upload-existing" />{{ importPlan.existing.length }} 段服务器已有同名同大小的视频，默认跳过；仍然上传并比对内容</label>
          <p class="review-note">批量导入不会覆盖已有视频。上传时可以继续标注；关闭页面会中断，重新选择同一文件夹只传缺的。</p>
          <div class="mt-5 flex justify-end gap-3"><button class="btn-secondary" @click="importPlan = null">取消</button><button class="btn-primary" data-testid="import-start" :disabled="!importPlan.items.length" @click="startImport">开始导入</button></div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.library-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.library-actions button { flex: 1 1 auto; white-space: nowrap; }
.library-filters { display: flex; align-items: center; gap: 8px; padding: 0 12px 8px; font-size: var(--fs-xs, 12px); color: var(--muted); }
.library-filters select { min-width: 0; flex: 1; background: var(--surface-subtle); border: 1px solid var(--line); border-radius: var(--radius-sm, 5px); padding: 4px 6px; color: var(--text); }
.library-filters label { display: flex; align-items: center; gap: 4px; white-space: nowrap; }
.library-folder { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 6px; width: 100%; padding: 6px; border: 0; background: none; color: var(--text); text-align: left; cursor: pointer; border-radius: var(--radius-sm, 5px); }
.library-folder:hover { background: var(--surface-subtle); }
.library-folder strong { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.library-folder small { color: var(--muted); font-size: 11px; }
.library-caret { color: var(--muted); width: 12px; flex: none; }
.library-import { margin: 0 12px 8px; padding: 8px; display: flex; flex-direction: column; gap: 4px; background: var(--surface-subtle); border: 1px solid var(--line); border-radius: var(--radius-sm, 5px); font-size: 12px; }
.library-import-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2px 8px; }
.library-import-head span, .library-import-file { color: var(--muted); }
.library-import-file { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.library-import progress { width: 100%; }
.library-import-actions { display: flex; flex-wrap: wrap; gap: 4px; }
.library-import-report { max-height: 180px; overflow-y: auto; margin: 0; padding: 0; list-style: none; overflow-wrap: anywhere; }
.library-import-report li { padding: 3px 0; border-top: 1px solid var(--line); }
.library-import-report li.failed b { color: var(--danger); }
.library-import-report li.done b { color: var(--success); }
.library-import-report small { color: var(--muted); }
</style>
