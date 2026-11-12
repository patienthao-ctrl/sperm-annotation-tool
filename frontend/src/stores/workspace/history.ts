// 本帧撤销/重做栈。
import { computed, ref } from 'vue'
import type { AnnotationObject } from '../../types/annotation'
import { annotationsByMedia, canUndoVideoDeletion, clone, currentFrame, currentMediaId, currentObjects, deletedFrameObjects, deletedObjectIds, deletedTrackingIds, editingBlocked, type FrameDeletion, lastPausedContext, pausedAnomalies, runtime, selectedObjectId, statusMessage, videoDeletionHistory } from './state'
import { cancelHeadBox, isInteracting } from './editing'
import { rememberControls, scheduleWorkspaceStateSave } from './save'
import { applyVideoDeletion, undoVideoObjectDeletion } from './trackingActions'
import { dropUndoneTrackAdd, latestTrackAdd, redoTrackAdd, undoTrackAdd } from './prelabel'

type HistoryState = { objects: AnnotationObject[]; deleted: string[]; frameDeleted: FrameDeletion[]; sequence: number; pause?: { items: typeof pausedAnomalies.value; context: typeof lastPausedContext.value } }
export const histories = new Map<string, { undo: HistoryState[]; redo: HistoryState[] }>()
export const historyRevision = ref(0)
const history = () => {
  const key = `${currentMediaId.value}:${currentFrame.value}`
  if (!histories.has(key)) histories.set(key, { undo: [], redo: [] })
  return histories.get(key)!
}
const captureHistory = (): HistoryState => ({
  objects: JSON.parse(JSON.stringify(currentObjects.value)),
  deleted: [...(deletedTrackingIds.value[currentMediaId.value] ?? [])],
  frameDeleted: clone(deletedFrameObjects.value[currentMediaId.value] ?? []),
  sequence: ++runtime.historySequence,
  pause: lastPausedContext.value?.mediaId === currentMediaId.value && lastPausedContext.value.frameIndex === currentFrame.value ? { items: clone(pausedAnomalies.value), context: clone(lastPausedContext.value) } : undefined,
})
export const snapshotUndo = () => {
  if (videoDeletionHistory.value[currentMediaId.value]?.undone) { delete videoDeletionHistory.value[currentMediaId.value]; rememberControls(currentMediaId.value) }
  dropUndoneTrackAdd(currentMediaId.value) // A new edit replaces what redo could bring back.
  const h = history(); h.undo.push(captureHistory()); h.redo.length = 0
  if (h.undo.length > 50) h.undo.shift()
  historyRevision.value++
}
export const canUndo = computed(() => { void historyRevision.value; const add = latestTrackAdd(currentMediaId.value); return history().undo.length > 0 || canUndoVideoDeletion.value || (!!add && !add.undone) })
export const canRedo = computed(() => { void historyRevision.value; return history().redo.length > 0 || !!videoDeletionHistory.value[currentMediaId.value]?.undone || !!latestTrackAdd(currentMediaId.value)?.undone })
const restoreHistory = (state: HistoryState) => {
  const mid = currentMediaId.value, fi = currentFrame.value
  const affected = new Set([...currentObjects.value, ...state.objects].map(o => o.id))
  const rest = (annotationsByMedia.value[mid] ?? []).filter(o => (o.frameIndex ?? 0) !== fi)
  annotationsByMedia.value = { ...annotationsByMedia.value, [mid]: [...rest, ...state.objects.filter(o => !deletedObjectIds.value[mid]?.includes(o.objectId!))] }
  // Undo is frame-local: never restore deletion flags for unrelated frames.
  const otherDeleted = [...(deletedTrackingIds.value[mid] ?? [])].filter(id => !affected.has(id))
  deletedTrackingIds.value[mid] = new Set([...otherDeleted, ...state.deleted.filter(id => affected.has(id))])
  deletedFrameObjects.value[mid] = [...(deletedFrameObjects.value[mid] ?? []).filter(d => d.frameIndex !== fi), ...state.frameDeleted.filter(d => d.frameIndex === fi)]
  if (state.pause) { pausedAnomalies.value = clone(state.pause.items).filter(item => !deletedObjectIds.value[mid]?.includes(item.objectId)); lastPausedContext.value = pausedAnomalies.value.length ? clone(state.pause.context) : null }
  if (!state.objects.some(o => o.id === selectedObjectId.value)) selectedObjectId.value = null
  historyRevision.value++; scheduleWorkspaceStateSave(mid)
}
export const undo = () => {
  cancelHeadBox()
  if (editingBlocked.value || isInteracting()) return
  const h = history()
  const deletion = videoDeletionHistory.value[currentMediaId.value]
  const add = latestTrackAdd(currentMediaId.value)
  if (add && !add.undone && add.sequence >= (h.undo[h.undo.length - 1]?.sequence ?? 0) && add.sequence >= (deletion && !deletion.undone ? deletion.sequence : 0)) { undoTrackAdd(currentMediaId.value); historyRevision.value++; return }
  if (deletion && !deletion.undone && deletion.sequence >= (h.undo[h.undo.length - 1]?.sequence ?? 0)) { void undoVideoObjectDeletion(); return }
  if (!h.undo.length) return
  h.redo.push(captureHistory()); restoreHistory(h.undo.pop()!); statusMessage.value = '已撤销本帧操作'
}
export const redo = () => {
  cancelHeadBox()
  if (editingBlocked.value || isInteracting()) return
  const h = history()
  const deletion = videoDeletionHistory.value[currentMediaId.value]
  const add = latestTrackAdd(currentMediaId.value)
  if (add?.undone && add.sequence >= (h.redo[h.redo.length - 1]?.sequence ?? 0)) { redoTrackAdd(currentMediaId.value); historyRevision.value++; return }
  if (deletion?.undone && deletion.sequence >= (h.redo[h.redo.length - 1]?.sequence ?? 0)) { void applyVideoDeletion('redo', deletion); return }
  if (!h.redo.length) return
  h.undo.push(captureHistory()); restoreHistory(h.redo.pop()!); statusMessage.value = '已重做本帧操作'
}
