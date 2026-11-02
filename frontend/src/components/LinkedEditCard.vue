<script setup lang="ts">
// 改帧联动卡片：选中视频对象时，提示前后帧的同类问题，并提供区间操作（决策 0002）。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ObjectFilmstrip from './ObjectFilmstrip.vue'
import { useWorkspace } from '../stores/workspace'
import { LINKED_SPAN_MAX } from '../annotation/linkedEdits'
import {
  applySizeToNeighbours, canUndoLinked, cancelLinkedFill, deleteTail, fillAlong, linkedBusy, linkedError, linkedSpan,
  linkedTarget, linkedUndoLabel, undoLinked,
} from '../stores/workspace/linkedEdits'

// compact: review mode, where the inspector height belongs to the checklist; start as one summary line.
const props = defineProps<{ compact?: boolean }>()
const { editingBlocked, currentMediaId, seekVideo, frameToTime } = useWorkspace()
const expanded = ref(!props.compact)
watch(() => props.compact, value => { expanded.value = !value })
const target = linkedTarget
const disabled = computed(() => editingBlocked.value || linkedBusy.value)
const span = (frames: number[]) => frames.length ? `第 ${frames[0] + 1}–${frames[frames.length - 1] + 1} 帧` : ''
const neighbours = computed(() => target.value ? [...target.value.range.before, ...target.value.range.after] : [])
const suspectText = computed(() => {
  const s = target.value?.suspects ?? []
  if (!s.length) return `前后 ${linkedSpan.value} 帧的框与本帧一致`
  const list = s.length <= 4 ? s.map(f => f + 1).join('、') : `${s.slice(0, 3).map(f => f + 1).join('、')} 等`
  return `前后还有 ${s.length} 帧的框大小或位置与本帧差别明显（第 ${list} 帧），可能有同样的问题`
})
function confirmDeleteTail() {
  const t = target.value
  if (!t || !t.tail.length) return
  if (t.tail.length > 1 && !window.confirm(`删除对象 #${t.objectId} ${span(t.tail)}的框（共 ${t.tail.length} 帧）？可点“撤销联动操作”恢复。`)) return
  deleteTail()
}
const filmstripObject = ref<number | null>(null)
watch(currentMediaId, () => { cancelLinkedFill(); filmstripObject.value = null })
onBeforeUnmount(cancelLinkedFill)
</script>

<template>
  <section v-if="target || canUndoLinked" class="shell-card linked-card" :class="{ compact }" aria-label="前后帧联动" data-testid="linked-edits">
    <button class="shell-card-head linked-head" :aria-expanded="expanded" data-testid="linked-toggle" @click="expanded = !expanded">
      <strong>前后帧联动</strong><span v-if="target" class="linked-object">对象 #{{ target.objectId }}</span>
      <span v-if="target && !expanded" class="linked-summary" :class="{ 'linked-warn': target.suspects.length }">{{ target.suspects.length ? `${target.suspects.length} 帧差异明显` : '前后一致' }}</span>
      <span class="linked-chevron" aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
    </button>
    <template v-if="target && expanded">
      <p class="shell-card-note" :class="{ 'linked-warn': target.suspects.length }" data-testid="linked-suspects">{{ suspectText }}</p>
      <div v-if="target.suspects.length" class="linked-jumps">
        <button v-for="f in target.suspects.slice(0, 6)" :key="f" class="quiet-button" :disabled="disabled" @click="seekVideo(frameToTime(f))">{{ f + 1 }}</button>
      </div>
      <label class="linked-span">范围：前后 <input v-model.number="linkedSpan" type="number" min="1" :max="LINKED_SPAN_MAX" aria-label="联动范围帧数" :disabled="disabled" /> 帧</label>
      <div class="linked-actions">
        <button class="btn-secondary" data-testid="linked-open-filmstrip" title="把这个对象每一帧的框裁成缩略图排开，并画出框面积曲线" @click="filmstripObject = target.objectId">按对象检查（缩略图）</button>
        <button class="btn-secondary" data-testid="linked-apply-size" :disabled="disabled || !neighbours.length" :title="neighbours.length ? `保留各帧中心，只改宽高；遇到缺框或人工框停下` : '相邻帧没有可调整的框'" @click="applySizeToNeighbours()">尺寸应用到{{ span(neighbours) || '相邻帧' }}</button>
        <button class="btn-secondary" data-testid="linked-delete-tail" :disabled="disabled || !target.tail.length" title="精子已离开画面时，删除本帧起到轨迹结束的残留框" @click="confirmDeleteTail">删除本帧到轨迹结束（{{ target.tail.length }} 帧）</button>
        <button class="btn-secondary" data-testid="linked-fill-before" :disabled="disabled || !target.canFillBefore" title="用头部检测逐帧向前补框，找不到同一个头就停" @click="fillAlong(-1)">向前补框</button>
        <button class="btn-secondary" data-testid="linked-fill-after" :disabled="disabled || !target.canFillAfter" title="用头部检测逐帧向后补框，找不到同一个头就停" @click="fillAlong(1)">向后补框</button>
      </div>
      <p v-if="linkedBusy" class="shell-card-note" role="status">正在逐帧检测… <button class="quiet-button" @click="cancelLinkedFill">停止</button></p>
    </template>
    <p v-if="linkedError" class="linked-error" role="alert">{{ linkedError }}</p>
    <button v-if="canUndoLinked" class="quiet-button" data-testid="linked-undo" :disabled="disabled" :title="linkedUndoLabel" @click="undoLinked">撤销联动操作</button>
  </section>
  <ObjectFilmstrip v-if="filmstripObject !== null" :object-id="filmstripObject" @close="filmstripObject = null" />
</template>

<style scoped>
.linked-card { display: grid; gap: 6px; flex: 0 0 auto }
/* Review: the checklist keeps most of the inspector height; the expanded card scrolls inside. */
.linked-card.compact { max-height: 40%; overflow-y: auto; overscroll-behavior: contain }
.linked-head { width: 100%; display: flex; align-items: center; gap: 6px; background: none; border: 0; padding: 0; cursor: pointer; color: inherit; text-align: left }
.linked-summary { margin-left: auto; font-size: 12px; color: var(--muted) }
.linked-chevron { margin-left: auto; color: var(--muted); font-size: 12px }
.linked-summary + .linked-chevron { margin-left: 4px }
.linked-jumps .quiet-button { min-width: 28px; padding: 1px 6px; border: 1px solid var(--line); border-radius: 4px }
.linked-object { color: var(--muted); font-size: 12px }
.linked-warn { color: var(--warning) }
.linked-error { margin: 0; background: var(--warning-soft); color: var(--warning); border-radius: 6px; padding: 6px 8px; font-size: 12px }
.linked-jumps { display: flex; flex-wrap: wrap; gap: 4px }
.linked-span { font-size: 12px; display: flex; align-items: center; gap: 4px }
.linked-span input { width: 52px; padding: 1px 6px; border: 1px solid var(--control-line); border-radius: 4px; background: var(--surface); color: var(--text) }
.linked-actions { display: grid; gap: 4px }
.linked-actions .btn-secondary { justify-content: flex-start; text-align: left; white-space: normal }
</style>
