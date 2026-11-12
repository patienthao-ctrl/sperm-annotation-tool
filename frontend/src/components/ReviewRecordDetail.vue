<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { annotationReviewApi, type Failure, type ReviewRecordDetail } from '../api/annotationReviewApi'

// Opened from a row of the review records: who answered which targets (or, for older rounds,
// confirmed which frames) and what the round changed.
const props = defineProps<{ reviewId: string; open: boolean }>()
const detail = ref<ReviewRecordDetail | null>(null)
const loading = ref(false)
const error = ref('')

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await annotationReviewApi.record(props.reviewId)
  } catch (e) {
    const failure = e as Failure
    error.value = `明细读取失败：${failure?.message || '请重试'}${failure?.requestId ? `（记录号 ${failure.requestId}）` : ''}`
  } finally {
    loading.value = false
  }
}
onMounted(load)

// A long round can run to thousands of change spans; the list stays readable and says what it leaves out.
const SHOWN_CHANGES = 200, SHOWN_SPANS = 12
const changes = computed(() => detail.value?.changes.slice(0, SHOWN_CHANGES) ?? [])
const hiddenChanges = computed(() => Math.max(0, (detail.value?.changes.length ?? 0) - SHOWN_CHANGES))
const frameText = (a: number, b: number) => a === b ? `第 ${a + 1} 帧` : `第 ${a + 1}–${b + 1} 帧`
const spansText = (spans: [number, number][]) => spans.slice(0, SHOWN_SPANS).map(([a, b]) => a === b ? `${a + 1}` : `${a + 1}–${b + 1}`).join('、') + (spans.length > SHOWN_SPANS ? ` 等 ${spans.length} 段` : '')
const objects = (ids: number[]) => ids.join('、')
const byTarget = computed(() => detail.value?.unit === 'targets')
const verdictLabel: Record<string, string> = { tail: '有尾巴', head: '没尾巴', no: '不是精子', unsure: '看不清' }
const formatTime = (value: string) => { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString() }
</script>

<template>
  <div class="record-detail" data-testid="review-record-detail">
    <p v-if="loading && !detail" class="record-detail-note" role="status">正在读取明细…</p>
    <div v-else-if="error" class="error-banner" role="alert">{{ error }} <button class="quiet-button" :disabled="loading" @click="load">重试</button></div>
    <template v-else-if="detail">
      <section :aria-label="byTarget ? '目标判定' : '逐帧确认'">
        <h4>{{ byTarget ? '目标判定' : '逐帧确认' }}</h4>
        <p v-if="!detail.confirmations.length" class="record-detail-note">{{ byTarget ? '还没有人判定过目标。' : '还没有人确认过帧。' }}</p>
        <p v-if="detail.legacy" class="record-detail-note">这一轮来自旧版清单，按检查项记录，没有逐帧帧号。</p>
        <ul v-if="detail.confirmations.length">
          <li v-for="item in detail.confirmations" :key="`${item.actor.id}-${item.result}`">
            <strong>{{ item.actor.name }}</strong>
            <span v-if="item.result in verdictLabel">判为“{{ verdictLabel[item.result] }}” {{ item.frames }} 个目标</span>
            <span v-else>{{ item.result === 'empty' ? '确认无目标' : '确认' }} {{ item.frames }} {{ detail.legacy ? '项' : '帧' }}</span>
            <span v-if="item.targets?.length" class="record-detail-frames">目标 {{ objects(item.targets.slice(0, 30)) }}{{ item.targets.length > 30 ? ` 等 ${item.targets.length} 个` : '' }}</span>
            <span v-if="item.spans.length" class="record-detail-frames">第 {{ spansText(item.spans) }} 帧</span>
            <small>最后确认 {{ formatTime(item.lastAt) }}</small>
          </li>
        </ul>
      </section>
      <section aria-label="本轮修改明细">
        <h4>本轮修改</h4>
        <p v-if="!changes.length" class="record-detail-note">{{ open ? '审查通过时统计。' : '这一轮没有修改标注。' }}</p>
        <ul v-else>
          <li v-for="change in changes" :key="change.start">
            <strong>{{ frameText(change.start, change.end) }}</strong>
            <span v-if="change.moved.length">移动对象 {{ objects(change.moved) }}</span>
            <span v-if="change.added.length">新增对象 {{ objects(change.added) }}</span>
            <span v-if="change.removed.length">删除对象 {{ objects(change.removed) }}</span>
            <small v-if="change.checkedBy">确认人 {{ change.checkedBy.name }}</small>
          </li>
        </ul>
        <p v-if="hiddenChanges" class="record-detail-note">只列出前 {{ SHOWN_CHANGES }} 段，还有 {{ hiddenChanges }} 段未显示。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.record-detail { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px 28px; padding: 2px 0 4px; font-size: var(--fs-xs); animation: rise-in var(--dur-base) var(--ease-out); }
.record-detail .error-banner { grid-column: 1 / -1; }
.record-detail h4 { margin: 0 0 6px; font-size: var(--fs-xs); font-weight: 650; color: var(--muted); letter-spacing: .04em; }
.record-detail ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 5px; max-height: 240px; overflow: auto; overscroll-behavior: contain; }
.record-detail li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; line-height: 1.6; }
.record-detail li strong { font-weight: 600; font-variant-numeric: tabular-nums; }
.record-detail li small { color: var(--muted); font-size: var(--fs-2xs); }
.record-detail-frames { font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.record-detail-note { margin: 0; color: var(--muted); line-height: 1.6; }
</style>
