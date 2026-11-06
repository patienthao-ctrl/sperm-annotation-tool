// Batch import: one upload at a time in the background, never locks the library, never
// overwrites a video, and reports every file. The queue lives in page memory; after the
// page closes, picking the same folder again skips what already arrived (same name and size).
import { computed, reactive, ref } from 'vue'
import { trackApi } from '../api/trackApi'
import { isVideoFile, mediaAssets, showToast } from './workspace/state'
import { loadServerMedia } from './workspace/media'
import { useAnnotationReview } from './annotationReview'

export type ImportState = 'waiting' | 'uploading' | 'done' | 'exists' | 'failed'
export interface ImportItem {
  id: number; file: File; kind: 'video' | 'package'; name: string; subPath: string
  folderPath: string; state: ImportState; progress: number; message: string
}
export interface ImportPlan {
  items: ImportItem[]; skipped: string[]; existing: ImportItem[]; tooLarge: ImportItem[]
  folder: string; keepSubfolders: boolean; hasSubfolders: boolean; uploadExisting: boolean
}
const GIB = 1024 ** 3, MAX_VIDEO = 2 * GIB, MAX_PACKAGE = 8 * GIB, RETRIES = 2
const JUNK = new Set(['thumbs.db', 'desktop.ini'])
let nextId = 1

export const importPlan = ref<ImportPlan | null>(null)
export const importItems = ref<ImportItem[]>([])
export const importRunning = ref(false)
export const importCounts = computed(() => {
  const count = (...states: ImportState[]) => importItems.value.filter(item => states.includes(item.state)).length
  return { total: importItems.value.length, done: count('done'), exists: count('exists'), failed: count('failed'), left: count('waiting', 'uploading') }
})
export const importCurrent = computed(() => importItems.value.find(item => item.state === 'uploading') ?? null)

const relativeParts = (file: File) => (file.webkitRelativePath || file.name).split('/')
const joinPath = (...parts: string[]) => parts.map(part => part.trim()).filter(Boolean).join('/')

/** Sort picked files into a plan the user confirms; nothing is uploaded yet. */
export function prepareImport(files: FileList | File[] | null) {
  const picked = Array.from(files ?? [])
  if (!picked.length) return
  const items: ImportItem[] = [], skipped: string[] = []
  for (const file of picked) {
    const name = file.name, lower = name.toLowerCase(), parts = relativeParts(file)
    const kind = lower.endsWith('.zip') ? 'package' : isVideoFile(file) ? 'video' : null
    if (!kind || name.startsWith('.') || JUNK.has(lower) || !file.size) { skipped.push(parts.join('/')); continue }
    items.push(reactive<ImportItem>({ id: nextId++, file, kind, name, subPath: parts.slice(1, -1).join('/'), folderPath: '', state: 'waiting', progress: 0, message: '' }))
  }
  const known = new Set(mediaAssets.value.filter(media => media.sizeBytes).map(media => `${media.serverVideoName}|${media.sizeBytes}`))
  const existing = items.filter(item => item.kind === 'video' && known.has(`${item.name}|${item.file.size}`))
  const tooLarge = items.filter(item => item.file.size > (item.kind === 'video' ? MAX_VIDEO : MAX_PACKAGE))
  const fromFolder = !!picked[0]!.webkitRelativePath
  importPlan.value = {
    items, skipped, existing, tooLarge, keepSubfolders: true, uploadExisting: false,
    folder: fromFolder ? relativeParts(picked[0]!)[0]! : '', hasSubfolders: items.some(item => item.subPath),
  }
}

export function startImport() {
  const plan = importPlan.value
  if (!plan) return
  importPlan.value = null
  const add: ImportItem[] = []
  for (const item of plan.items) {
    const sub = plan.keepSubfolders ? item.subPath : ''
    item.folderPath = item.kind === 'package' ? joinPath(plan.folder, sub, item.name.replace(/\.zip$/i, '')) : joinPath(plan.folder, sub)
    if (plan.tooLarge.includes(item)) { item.state = 'failed'; item.message = `超过 ${item.kind === 'video' ? '2' : '8'} GiB 上限，没有上传` }
    else if (!plan.uploadExisting && plan.existing.includes(item)) { item.state = 'exists'; item.message = '服务器已有同名同大小的视频，已跳过' }
    add.push(item)
  }
  // A finished earlier batch stays visible until the next one starts.
  importItems.value = [...importItems.value.filter(item => item.state === 'waiting' || item.state === 'uploading'), ...add]
  void run()
}

export function retryFailedImports() {
  for (const item of importItems.value) if (item.state === 'failed' && !item.message.includes('上限')) { item.state = 'waiting'; item.progress = 0; item.message = '' }
  void run()
}
export const clearImportReport = () => { importItems.value = importItems.value.filter(item => item.state === 'waiting' || item.state === 'uploading') }

async function uploadOne(item: ImportItem) {
  const onProgress = (fraction: number) => { item.progress = fraction }
  if (item.kind === 'video') {
    const { status, data } = await trackApi.uploadWithProgress('/api/track/upload', item.file, item.folderPath, onProgress)
    if (status === 409 && data?.code === 'DUPLICATE_VIDEO') {
      item.state = 'exists'; item.message = `服务器已有同样内容的视频「${data.duplicate?.videoName ?? ''}」，未覆盖`; return
    }
    if (status < 200 || status >= 300) throw Object.assign(new Error(data?.message || data?.detail || `上传失败（${status}）`), { status })
    item.state = 'done'; item.message = '已导入'
    return
  }
  const { status, data } = await trackApi.uploadWithProgress('/api/track/import-package', item.file, item.folderPath, onProgress)
  if (status < 200 || status >= 300) throw Object.assign(new Error(data?.detail || data?.message || `标注包导入失败（${status}）`), { status })
  const imported = data.imported?.length ?? 0, skipped: { videoName: string; reason: string }[] = data.skipped ?? []
  item.state = imported || !skipped.length ? 'done' : 'exists'
  item.message = [`导入 ${imported} 段`, ...skipped.map(entry => `${entry.videoName} 未导入：${entry.reason}`)].join('；')
}

async function run() {
  if (importRunning.value) return
  importRunning.value = true
  const review = useAnnotationReview()
  try {
    for (;;) {
      const item = importItems.value.find(entry => entry.state === 'waiting')
      if (!item) break
      item.state = 'uploading'; item.progress = 0
      for (let attempt = 0; ; attempt++) {
        try { await uploadOne(item); break }
        catch (error: any) {
          // A refusal will not change on retry; a lost connection or server hiccup may.
          const refused = error?.status >= 400 && error?.status < 500
          if (refused || attempt >= RETRIES) {
            item.state = 'failed'; item.message = error?.message || '上传失败'
            console.error('[annotation.batch_import_failed]', { file: item.name, status: error?.status, error })
            break
          }
          console.warn('[annotation.batch_import_retry]', { file: item.name, attempt: attempt + 1 })
        }
      }
      if ((item.state as ImportState) === 'done') { await loadServerMedia({ keepView: true }); await review.loadSummary() }
    }
  } finally { importRunning.value = false }
  const { done, exists, failed } = importCounts.value
  showToast(`导入完成：成功 ${done}，已存在 ${exists}，失败 ${failed}`, failed ? undefined : 'success')
}
