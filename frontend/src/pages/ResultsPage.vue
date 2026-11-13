<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import ReviewRecordDetail from '../components/ReviewRecordDetail.vue'
import StatisticsDataExport from '../components/StatisticsDataExport.vue'
import { annotationReviewApi, type Failure, type ReviewRecord } from '../api/annotationReviewApi'
import { listResults } from '../api/httpAnnotationApi'

interface DbRow {
  id: number
  batch_id?: string | null
  user_id: number
  media_id: string
  media_name?: string | null
  media_type?: string | null
  frame_index: number
  timestamp_ms?: number | null
  object_name?: string | null
  source?: string | null
  username?: string | null
  created_at: string
}

interface ResultGroup {
  key: string
  mediaId: string
  mediaName: string
  mediaType: string
  frameIndex: number | null
  timestampMs: number | null
  objects: string[]
  username: string
  savedAt: string
}

const rawRows = ref<DbRow[]>([])
const loading = ref(false)
const errorMessage = ref('')
const selectedMediaId = ref('')
const search = ref(''), page = ref(1)
const pageSize = 50
const tab = ref<'annotation' | 'review'>('annotation')
const annotationsLoaded = ref(false)
const reviewRows = ref<ReviewRecord[]>([])
const reviewsLoaded = ref(false)
const reviewsLoading = ref(false)
const reviewsError = ref('')
const expandedReview = ref('')

const mediaIdentity = (row: DbRow) =>
  `${row.media_type || 'video'}:${row.media_id}`
const reviewIdentity = (row: ReviewRecord) => `video:${row.mediaId}`

const mediaOptions = computed(() => {
  const map = new Map<string, { id: string; name: string; type: string }>()
  for (const row of rawRows.value) {
    const identity = mediaIdentity(row)
    if (!map.has(identity)) {
      map.set(identity, {
        id: identity,
        name: row.media_name || row.media_id,
        type: row.media_type || 'video',
      })
    }
  }
  // Same identity as an annotation row, so the filter carries over between the two lists.
  for (const row of reviewRows.value) {
    const identity = reviewIdentity(row)
    if (!map.has(identity)) map.set(identity, { id: identity, name: row.mediaName, type: 'video' })
  }
  return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name))
})

const filteredRows = computed(() => {
  return selectedMediaId.value
    ? rawRows.value.filter((row) => mediaIdentity(row) === selectedMediaId.value)
    : rawRows.value
})

const groupedRows = computed<ResultGroup[]>(() => {
  const groups = new Map<string, ResultGroup>()
  for (const row of filteredRows.value) {
    // 标注结果展示当前逻辑状态，而不是重复展示每次 Tracking 产生的保存批次。
    // Use stable media identity: distinct videos may legitimately share a filename.
    const key = `${mediaIdentity(row)}:${row.frame_index}:${row.user_id}`
    const existing = groups.get(key)
    if (existing) {
      if (row.object_name && !existing.objects.includes(row.object_name)) existing.objects.push(row.object_name)
      if (row.created_at > existing.savedAt) existing.savedAt = row.created_at
      continue
    }
    groups.set(key, {
      key,
      mediaId: row.media_id,
      mediaName: row.media_name || row.media_id,
      mediaType: row.media_type || 'video',
      frameIndex: row.media_type === 'video' ? row.frame_index : null,
      timestampMs: row.media_type === 'video' ? (row.timestamp_ms ?? 0) : null,
      objects: row.object_name ? [row.object_name] : [],
      username: row.username || '-',
      savedAt: row.created_at,
    })
  }
  const term = search.value.toLowerCase()
  return Array.from(groups.values()).filter(row => `${row.mediaName} ${row.username} ${row.objects.join(' ')}`.toLowerCase().includes(term))
})

const reviewPeople = (row: ReviewRecord) => row.reviewers.length ? row.reviewers.map(person => person.name).join('、') : row.startedBy.name
const reviewOutcome = (row: ReviewRecord) => {
  if (row.state === 'open') return { label: '审查中', tone: 'open', note: row.mediaDeleted ? '素材已删除' : '最新进度以审查页为准' }
  if (row.mediaDeleted && !row.standing) return { label: '审查通过', tone: 'past', note: '素材已删除' }
  if (row.standing === 'stale') return { label: '审查通过', tone: 'stale', note: '之后标注有改动，需重新审查' }
  if (row.standing === 'superseded') return { label: '审查通过', tone: 'past', note: '已有后续轮次' }
  return { label: '审查通过', tone: 'passed', note: '当前有效' }
}
const changeSummary = (changes: NonNullable<ReviewRecord['changes']>) =>
  ([['移动', changes.moved], ['新增', changes.added], ['删除', changes.removed]] as const).filter(([, count]) => count).map(([name, count]) => `${name} ${count}`).join(' · ')
const filteredReviews = computed(() => {
  const term = search.value.toLowerCase()
  return reviewRows.value.filter(row => {
    if (selectedMediaId.value && reviewIdentity(row) !== selectedMediaId.value) return false
    const people = [row.startedBy.name, row.passedBy?.name, ...row.reviewers.map(person => person.name)]
    return `${row.mediaName} ${people.join(' ')} 第 ${row.round} 轮 ${reviewOutcome(row).label}`.toLowerCase().includes(term)
  })
})

const formatTimestamp = (ms: number | null) => {
  if (ms == null) return '-'
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const millis = ms % 1000
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`
}

const formatSavedAt = (value: string) => {
  const normalized = value?.includes('T') ? value : String(value || '').replace(' ', 'T')
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const load = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await listResults('default')
    rawRows.value = (res.items ?? [])
      .map((row) => row as DbRow)
      .filter((row) => row.source === 'manual')
    annotationsLoaded.value = true
  } catch (error: any) {
    errorMessage.value = error?.message || '读取数据库标注记录失败'
    console.error('[records.load_failed]', error)
  } finally {
    loading.value = false
  }
}

// Kept separate from the annotation list: one failing must not read as the other being empty.
const loadReviews = async () => {
  reviewsLoading.value = true
  reviewsError.value = ''
  try {
    reviewRows.value = (await annotationReviewApi.records()).items
    reviewsLoaded.value = true
  } catch (error) {
    const failure = error as Failure
    reviewsError.value = `审查记录读取失败：${failure?.message || '请重试'}${failure?.requestId ? `（记录号 ${failure.requestId}）` : ''}`
    console.error('[records.reviews_load_failed]', error)
  } finally {
    reviewsLoading.value = false
  }
}
const refresh = () => { expandedReview.value = ''; void load(); void loadReviews() }
const busy = computed(() => loading.value || reviewsLoading.value)

const activeRows = computed<unknown[]>(() => tab.value === 'review' ? filteredReviews.value : groupedRows.value)
const pageCount = computed(() => Math.max(1, Math.ceil(activeRows.value.length / pageSize)))
const pageSlice = <T,>(rows: T[]) => rows.slice((page.value-1)*pageSize,page.value*pageSize)
const visibleRows = computed(() => pageSlice(groupedRows.value))
const visibleReviews = computed(() => pageSlice(filteredReviews.value))
watch([search,selectedMediaId,tab],()=>{page.value=1})
watch(pageCount,n=>{page.value=Math.min(page.value,n)})
onMounted(refresh)
</script>

<template>
  <section class="records-page">
    <header class="records-heading"><div><h2>标注与审查记录</h2><p>谁标了什么、谁审过、改了哪些。</p></div><div class="records-heading-actions"><button class="btn-secondary" :disabled="busy" @click="refresh">{{ busy?'读取中…':'刷新记录' }}</button><StatisticsDataExport /></div></header>
    <div class="panel records-panel"><div class="records-tabs" role="tablist" aria-label="记录类型"><button role="tab" :aria-selected="tab==='annotation'" data-testid="records-tab-annotation" @click="tab='annotation'">标注记录<small v-if="annotationsLoaded">{{ groupedRows.length }}</small></button><button role="tab" :aria-selected="tab==='review'" data-testid="records-tab-review" @click="tab='review'">审查记录<small v-if="reviewsLoaded">{{ filteredReviews.length }}</small></button></div><div class="records-filters"><label class="records-search"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="10" cy="10" r="6.5"/><path d="m15 15 5 5"/></svg><input v-model="search" class="input" :aria-label="tab==='review'?'搜索审查记录':'搜索标注记录'" :placeholder="tab==='review'?'搜索素材或审查人…':'搜索素材、对象或标注人…'"/></label><select v-model="selectedMediaId" class="input" aria-label="按素材筛选"><option value="">全部素材</option><option v-for="media in mediaOptions" :key="media.id" :value="media.id">{{ media.name }} · {{ media.id.slice(-8) }}</option></select><span>{{ tab==='review'?'按素材 / 审查轮次汇总':'按素材 / 帧 / 标注人汇总' }}</span></div>
    <div class="records-stats"><div><AppIcon name="folder" :size="15"/><span>素材 <strong>{{ mediaOptions.length }}</strong></span></div><div v-if="tab==='review'"><AppIcon name="list" :size="15"/><span>当前筛选 <strong>{{ filteredReviews.length }}</strong> 轮审查</span></div><div v-else><AppIcon name="list" :size="15"/><span>当前筛选 <strong>{{ groupedRows.length }}</strong> 条帧记录</span></div></div>
    <div v-if="tab==='annotation'&&errorMessage" class="error-banner" role="alert">{{ errorMessage }}</div>
    <div v-if="tab==='review'&&reviewsError" class="error-banner" role="alert" data-testid="review-records-error">{{ reviewsError }}</div>
    <div v-if="tab==='review'" class="records-table" data-testid="review-records"><table class="review-records"><thead><tr><th>素材</th><th>轮次</th><th>结果</th><th>审查人</th><th>已确认</th><th>本轮修改</th><th>最近操作时间</th><th><span class="sr-only">明细</span></th></tr></thead><tbody><template v-for="row in visibleReviews" :key="row.reviewId"><tr><td><strong>{{ row.mediaName }}</strong><small>视频 · {{ row.mediaId.slice(-10) }}</small></td><td>第 {{ row.round }} 轮<small>{{ row.round>1?'重新审查':'首轮' }}</small></td><td><span class="record-outcome" :class="reviewOutcome(row).tone">{{ reviewOutcome(row).label }}</span><small>{{ reviewOutcome(row).note }}</small></td><td>{{ reviewPeople(row) }}<small v-if="row.passedBy">通过人 {{ row.passedBy.name }}</small><small v-else-if="!row.reviewers.length">已开始，尚未确认</small></td><td>{{ row.checked }} / {{ row.total }} {{ row.legacy?'项':row.unit==='targets'?'个目标':'帧' }}<small v-if="row.legacy">旧版清单</small><small v-else-if="row.verdicts">有尾 {{ row.verdicts.tail }} · 无尾 {{ row.verdicts.head }} · 看不清 {{ row.verdicts.unsure }} · 不是 {{ row.verdicts.no }}</small><small v-else-if="row.emptyFrames">其中 {{ row.emptyFrames }} 帧无目标</small></td><td><template v-if="row.changes&&(row.changes.moved||row.changes.added||row.changes.removed)">{{ changeSummary(row.changes) }}<small v-if="row.changes.frames">涉及 {{ row.changes.frames }} 帧</small></template><span v-else-if="row.changes" class="record-muted">没有修改</span><span v-else class="record-muted">通过时统计</span></td><td>{{ formatSavedAt(row.lastActivityAt) }}<small>开始 {{ formatSavedAt(row.startedAt) }}</small></td><td><button class="quiet-button record-toggle" :aria-expanded="expandedReview===row.reviewId" data-testid="review-record-toggle" @click="expandedReview=expandedReview===row.reviewId?'':row.reviewId"><AppIcon name="next" :size="13" />明细</button></td></tr><tr v-if="expandedReview===row.reviewId" class="record-detail-row"><td colspan="8"><ReviewRecordDetail :review-id="row.reviewId" :open="row.state==='open'" /></td></tr></template><tr v-if="!visibleReviews.length"><td colspan="8" class="records-empty"><AppIcon :name="reviewsLoading?'list':search||selectedMediaId?'zoom':'folder'" :size="28"/><span>{{ reviewsLoading?'正在读取记录…':reviewsError&&!reviewsLoaded?'审查记录尚未读取成功，可点击“刷新记录”重试':search||selectedMediaId?'没有匹配的记录':'还没有审查记录' }}</span></td></tr></tbody></table></div>
    <div v-else class="records-table"><table><thead><tr><th>素材</th><th>帧号</th><th>人工标注对象</th><th>标注人</th><th>最近入库时间</th></tr></thead><tbody><tr v-for="row in visibleRows" :key="row.key"><td><strong>{{ row.mediaName }}</strong><small>{{ row.mediaType==='video'?'视频':'图片' }} · {{ row.mediaId.slice(-10) }}</small></td><td>{{ row.frameIndex===null?'—':row.frameIndex+1 }}<small v-if="row.timestampMs!==null">{{ formatTimestamp(row.timestampMs) }}</small></td><td><div class="record-tags"><span v-for="name in row.objects" :key="name">{{ name }}</span><span v-if="!row.objects.length">—</span></div></td><td>{{ row.username }}</td><td>{{ formatSavedAt(row.savedAt) }}</td></tr><tr v-if="!visibleRows.length"><td colspan="5" class="records-empty"><AppIcon :name="loading?'list':search||selectedMediaId?'zoom':'folder'" :size="28"/><span>{{ loading?'正在读取记录…':search||selectedMediaId?'没有匹配的记录':'暂无已入库的人工标注记录' }}</span></td></tr></tbody></table></div>
    <footer class="records-pagination"><small>第 {{ page }} / {{ pageCount }} 页 · 每页 {{ pageSize }} 条</small><button class="btn-secondary" :disabled="page===1" @click="page--">上一页</button><button class="btn-secondary" :disabled="page===pageCount" @click="page++">下一页</button></footer></div>
  </section>
</template>
<style scoped>
.records-page{animation:rise-in var(--dur-slow) var(--ease-out);max-width:1600px;min-width:0;min-height:420px;height:calc(100vh - var(--app-header-height) - var(--page-gutter) - var(--page-gutter) - 16px);margin:8px auto;display:flex;flex-direction:column;gap:18px}
.records-heading{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;flex-shrink:0}.records-heading>div:first-child{min-width:0}
.records-heading-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.records-heading h2{font-size:22px;font-weight:700;margin:0;line-height:1.4;letter-spacing:.02em}
.records-heading p{font-size:13px;line-height:1.7;color:var(--muted);margin:7px 0 0;max-width:70ch}
.records-panel{overflow:hidden;min-width:0;min-height:0;flex:1;display:flex;flex-direction:column;border-radius:var(--radius-lg)}
.records-tabs{display:flex;gap:4px;padding:8px 12px 0;border-bottom:1px solid var(--line);flex-shrink:0}
.records-tabs button{display:inline-flex;align-items:center;gap:6px;padding:7px 12px 8px;margin-bottom:-1px;font-size:13px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;border-radius:var(--radius-sm) var(--radius-sm) 0 0;white-space:nowrap}
.records-tabs button:hover{color:var(--text);background:var(--surface-hover)}
.records-tabs button[aria-selected=true]{color:var(--accent);border-bottom-color:var(--accent)}
.records-tabs small{min-width:18px;padding:0 5px;border-radius:9px;background:var(--surface-subtle);color:var(--muted);font-size:11px;font-weight:600;text-align:center;font-variant-numeric:tabular-nums}
.records-tabs button[aria-selected=true] small{background:var(--accent-soft);color:var(--accent)}
.records-filters{display:flex;flex-wrap:wrap;gap:12px;padding:16px;align-items:center;border-bottom:1px solid var(--line);flex-shrink:0}
.records-search{position:relative;display:flex;align-items:center;flex:1;min-width:220px;max-width:430px}.records-search>svg{position:absolute;left:11px;color:var(--muted);pointer-events:none}
.records-filters .records-search input{width:100%;padding-left:36px;min-height:38px;font-size:13px}.records-filters select{width:260px;max-width:100%;min-height:38px;font-size:13px}
.records-filters>span{margin-left:auto;font-size:12px;color:var(--muted);line-height:1.6}
.records-stats{display:flex;flex-wrap:wrap;gap:8px 22px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--surface-subtle);flex-shrink:0}
.records-stats>div{display:flex;gap:7px;align-items:center;color:var(--muted)}.records-stats span{font-size:12px}.records-stats strong{color:var(--text);font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;margin-left:3px}.records-stats svg{color:var(--accent)}
.records-stats p{margin:0 0 0 auto;color:var(--muted);font-size:12px;line-height:1.6}
.records-table{overflow:auto;flex:1;min-height:0;overscroll-behavior:contain;animation:fade-in var(--dur-base) var(--ease-standard)}
table{width:100%;min-width:760px;font-size:13px;border-collapse:collapse}th{position:sticky;top:0;z-index:1;background:var(--surface-subtle);color:var(--muted);text-align:left;font-size:12px;font-weight:600;letter-spacing:.04em;white-space:nowrap;box-shadow:inset 0 -1px 0 var(--line)}
th,td{padding:14px 18px;border-bottom:1px solid var(--line)}td{vertical-align:top;line-height:1.65}td:first-child{min-width:190px;max-width:330px}td strong{font-weight:600;overflow-wrap:anywhere}td small{display:block;color:var(--muted);font-size:11px;margin-top:4px}td:nth-child(2),td:last-child{white-space:nowrap}tbody tr{transition:background-color var(--dur-fast)}tbody tr:hover{background:var(--surface-subtle)}td:nth-child(2){font-variant-numeric:tabular-nums}
.record-tags{display:flex;gap:5px;flex-wrap:wrap;min-width:170px;max-width:400px}.record-tags span{background:var(--accent-soft);color:var(--accent);border-radius:999px;padding:1px 9px;font-size:12px;font-weight:550;overflow-wrap:anywhere}
.review-records td:nth-child(2){white-space:nowrap}.review-records td:nth-child(5),.review-records td:nth-child(6),.review-records td:nth-child(7){white-space:nowrap;font-variant-numeric:tabular-nums}.review-records td:last-child{padding-left:0;padding-right:10px}
.record-outcome{font-size:12px;font-weight:600;padding:1px 9px;border-radius:10px;white-space:nowrap;background:var(--surface-subtle);color:var(--muted)}.record-outcome.open{background:var(--review-accent-soft);color:var(--review-accent)}.record-outcome.passed{background:var(--success-soft);color:var(--success)}.record-outcome.stale{background:var(--warning-soft);color:var(--warning)}
.record-muted{color:var(--muted)}
.record-toggle svg{transition:transform var(--dur-fast) var(--ease-standard)}.record-toggle[aria-expanded=true] svg{transform:rotate(90deg)}
tbody .record-detail-row,tbody .record-detail-row:hover{background:var(--surface-subtle)}.review-records .record-detail-row td{padding:10px 18px 14px;white-space:normal;font-variant-numeric:normal}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.records-empty{text-align:center;padding:72px 20px;color:var(--muted)}.records-empty svg{display:block;margin:0 auto 12px;opacity:.4}.records-empty span{font-size:13px}.records-pagination{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px 16px;flex-shrink:0}.records-pagination small{margin-right:auto;color:var(--muted);font-size:12px;line-height:1.6}
@media(max-width:1100px){.records-filters>span{display:none}.records-stats p{flex-basis:100%;margin-left:0}}
@media(max-width:680px){.records-page{height:auto;min-height:0;margin:2px 0;gap:14px}.records-heading{gap:12px}.records-heading h2{font-size:20px}.records-heading p{font-size:12px}.records-heading-actions{width:100%}.records-panel{display:block}.records-filters{padding:12px;gap:10px}.records-search{flex-basis:100%;min-width:0;max-width:none}.records-filters select{width:100%}.records-stats{padding:10px 12px;gap:6px 16px}.records-table{max-height:62vh;min-height:220px}.records-empty{padding-left:14px}.records-pagination{padding:12px;gap:7px}.records-pagination small{width:100%;margin-right:0}th,td{padding:12px 14px}}
@media(max-height:600px) and (min-width:681px){.records-page{height:auto;min-height:0}.records-table{flex:none;max-height:45vh;min-height:220px}}
</style>
