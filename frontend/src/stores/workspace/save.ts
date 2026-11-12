// 工作区保存：本机草稿缓存、构建工作区状态、写入队列与重试、版本冲突标记、导出备份与重新读取。
import { createRequestId } from '../../utils/browserCompat'
import { computed, watch } from 'vue'
import type { AnnotationObject, MediaAsset } from '../../types/annotation'
import { WorkspaceMergeConflict, sharedWorkspace, mergeWorkspace, workspaceEqual, alignWorkspaceIds, type WorkspaceDraft } from '../../annotation/workspaceWrites'
import { useAuth } from '../auth'
import { activeTool, annotationsByMedia, anomalyFrames, brightness, clone, collaborationErrors, contrast, currentFrame, currentMediaId, dedupeAnnotationObjects, deletedFrameObjects, deletedObjectIds, deletedTrackingIds, deletingMediaId, duplicateImport, lastPausedContext, mediaAssets, mediaImportBusy, messageOf, objectDeletionBusy, objectNameInput, pausedAnomalies, pendingDeletion, pendingFeedback, pendingRestart, runtime, saveError, saveQueues, saveState, saveTickets, selectedMedia, selectedMediaId, selectedObjectId, storageWarning, trackingFeedbackBusy, trackingFramesByMedia, trackingRestartBusy, trackingRestartError, videoDeletionHistory, workspaceGenerations, workspaceReadError, workspaceRestoring, workspaceViews, workspaceWrites, zoom } from './state'
import { applySharedWorkspace, savePersonalView } from './collaboration'
import { rememberRestart, retryObjectDeletion, retryTrackingAnomalyFeedback } from './trackingActions'
import { resetAnnotationViewForMedia } from './restore'

export const draftSuppressed=new Set<string>() // videos whose local draft is being discarded
export const persistLocalAnnotations = async (dirtyMedia: Set<string>) => {
  const actor=useAuth().user.value?.id
  const serverIds = new Set(mediaAssets.value.filter(m => m.serverMediaId).map(m => m.id))
  const compact = Object.fromEntries(Object.entries(annotationsByMedia.value).map(([mid, objects]) =>
    [mid, serverIds.has(mid) ? [] : objects]))
  for(const media of mediaAssets.value){
    if(actor!==useAuth().user.value?.id)return
    if(!media.serverMediaId||!dirtyMedia.has(media.id)||draftSuppressed.has(media.id))continue
    const base=workspaceWrites.getBaseline(media.serverMediaId)
    if(!base)continue
    const draft=sharedWorkspace(buildWorkspaceState(media.id))
    if(!workspaceEqual(base,draft))await workspaceWrites.saveDraft(media.serverMediaId,base,draft).catch(error=>{console.warn('[annotation.local_draft_failed]',error);storageWarning.value='本机草稿缓存不可用：修改仍直接保存到服务器，但刷新前未保存的修改无法恢复，请勿关闭页面'})
    // Back to exactly what the server has (e.g. undo then redo): an older draft from the
    // intermediate state must not survive, or reopening the video would replay it as unsaved work.
    else await workspaceWrites.clearDraft(media.serverMediaId)
  }
  if(actor===useAuth().user.value?.id)localStorage.setItem('annotationsByMedia', JSON.stringify(compact))
}

// 自动持久化到 localStorage（debounce 300ms）
let saveTimer: ReturnType<typeof setTimeout> | null = null
const localDirtyMedia=new Set<string>()

export const workspaceSaveTimers = new Map<string, ReturnType<typeof setTimeout>>()

export const latestManualBaselines = (media: MediaAsset, objects: AnnotationObject[]) => {
  if (!media.width || !media.height) return []
  const latest = new Map<number, AnnotationObject>()
  for (const obj of objects) {
    if (obj.source !== 'manual' || obj.objectId == null || !obj.bbox) continue
    const previous = latest.get(obj.objectId)
    if (!previous || (obj.frameIndex ?? 0) >= (previous.frameIndex ?? 0)) latest.set(obj.objectId, obj)
  }
  return [...latest.values()].map((obj) => ({
    objectId: obj.objectId as number,
    name: obj.name,
    source: 'manual' as const,
    frameIndex: obj.frameIndex ?? 0,
    bbox: [
      obj.bbox!.x / 100 * media.width!,
      obj.bbox!.y / 100 * media.height!,
      (obj.bbox!.x + obj.bbox!.width) / 100 * media.width!,
      (obj.bbox!.y + obj.bbox!.height) / 100 * media.height!,
    ] as [number, number, number, number],
  }))
}

export const buildWorkspaceState = (mediaId: string, useCurrentUiState = false): WorkspaceDraft => {
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media) throw new Error('素材不存在')
  const includeUiState = useCurrentUiState || mediaId === currentMediaId.value
  const manualAnnotations = dedupeAnnotationObjects(
    (annotationsByMedia.value[mediaId] ?? []).filter((obj) => obj.source === 'manual'),
  )
  const state = {
    format: 'annotation-workspace-v1' as const,
    generationId:workspaceGenerations.get(mediaId),
    frontendMediaId: media.id,
    updatedAt: new Date().toISOString(),
    currentFrame: includeUiState ? currentFrame.value : 0,
    manualAnnotations: JSON.parse(JSON.stringify(manualAnnotations)),
    manualBaselines: latestManualBaselines(media, manualAnnotations),
    deletedTrackingIds: [...(deletedTrackingIds.value[mediaId] ?? new Set<string>())],
    deletedObjectIds: [...(deletedObjectIds.value[mediaId] ?? [])],
    deletedFrameObjects: clone(deletedFrameObjects.value[mediaId] ?? []),
    anomalyFrames: includeUiState ? JSON.parse(JSON.stringify(anomalyFrames.value)) : [],
    pausedAnomalies: includeUiState ? JSON.parse(JSON.stringify(pausedAnomalies.value)) : [],
    lastPausedContext: includeUiState && lastPausedContext.value?.mediaId === mediaId
      ? { mediaId, frameIndex: lastPausedContext.value.frameIndex }
      : null,
    display: {
      brightness: includeUiState ? brightness.value : 100,
      contrast: includeUiState ? contrast.value : 100,
      zoom: includeUiState ? zoom.value : 1,
    },
    editor: {
      activeTool: includeUiState ? (activeTool.value === 'head' || activeTool.value === 'smart' ? 'bbox' : activeTool.value) : 'select',
      objectNameInput: includeUiState ? objectNameInput.value : 'rare sperm',
      selectedObjectId: includeUiState ? selectedObjectId.value : null,
    },
  }
  if (!includeUiState) Object.assign(state, workspaceViews.get(mediaId) || {})
  else workspaceViews.set(mediaId, { currentFrame: state.currentFrame, display: state.display, editor: state.editor, anomalyFrames: state.anomalyFrames, pausedAnomalies: state.pausedAnomalies, lastPausedContext: state.lastPausedContext })
  return state
}

export const persistWorkspaceState = async (mediaId: string, useCurrentUiState = false) => {
  const actor=useAuth().user.value?.id
  if (deletingMediaId.value === mediaId || pendingDeletion.value || pendingFeedback.value || pendingRestart.value) return
  const media = mediaAssets.value.find(item => item.id === mediaId)
  if (!media?.serverMediaId || runtime.workspaceRestoreInProgress || (workspaceRestoring.value && mediaId === currentMediaId.value)) return
  if (workspaceReadError.value) throw new Error(workspaceReadError.value)
  const state = buildWorkspaceState(mediaId, useCurrentUiState)
  if(mediaId===currentMediaId.value)savePersonalView(mediaId)
  const ticket = (saveTickets.get(mediaId) || 0) + 1
  saveTickets.set(mediaId, ticket)
  if (mediaId === currentMediaId.value) saveState.value = 'saving'
  const prior = saveQueues.get(mediaId) || Promise.resolve()
  const task = prior.catch(() => {}).then(() => workspaceWrites.write(media.serverMediaId!, 'workspace', state))
  saveQueues.set(mediaId, task)
  try {
    const result=await task
    if(actor!==useAuth().user.value?.id)return
    if(result && 'workspace' in result && result.workspace && mediaId===currentMediaId.value){
      const merged=mergeWorkspace(alignWorkspaceIds(sharedWorkspace(state),result.workspace as WorkspaceDraft),alignWorkspaceIds(sharedWorkspace(buildWorkspaceState(mediaId)),result.workspace as WorkspaceDraft),result.workspace as WorkspaceDraft)
      applySharedWorkspace(mediaId,merged)
    }
    if (mediaId === currentMediaId.value && ticket === saveTickets.get(mediaId)) { saveState.value = 'saved'; saveError.value = '' }
  } catch (error) {
    if(actor!==useAuth().user.value?.id)throw error
    if(error instanceof WorkspaceMergeConflict){
      let latest=error
      if(mediaId===currentMediaId.value)try{mergeWorkspace(error.base,sharedWorkspace(buildWorkspaceState(mediaId)),error.remote)}catch(updated){if(updated instanceof WorkspaceMergeConflict)latest=updated}
      await workspaceWrites.retainConflict(media.serverMediaId!,latest)
      collaborationErrors.value={...collaborationErrors.value,[mediaId]:latest}
    }else markVersionConflict(error)
    console.error('[annotation.workspace_save_failed]', { mediaId, ticket, error })
    if (mediaId === currentMediaId.value && ticket === saveTickets.get(mediaId)) {
      saveState.value = 'error'; saveError.value = messageOf(error, '保存失败，请重试')
    }
    throw error
  } finally { if (saveQueues.get(mediaId) === task) saveQueues.delete(mediaId) }
}

export const scheduleWorkspaceStateSave = (mediaId: string) => {
  if (mediaImportBusy.value || duplicateImport.value || deletingMediaId.value === mediaId || pendingDeletion.value || pendingFeedback.value || pendingRestart.value || trackingRestartBusy.value || objectDeletionBusy.value || trackingFeedbackBusy.value || workspaceReadError.value) return
  if (!mediaId || runtime.workspaceRestoreInProgress || workspaceRestoring.value || !mediaAssets.value.find(m => m.id === mediaId)?.serverMediaId) return
  if (mediaId === currentMediaId.value) saveState.value = 'saving'
  const previous = workspaceSaveTimers.get(mediaId)
  if (previous) clearTimeout(previous)
  workspaceSaveTimers.set(mediaId, setTimeout(() => {
    workspaceSaveTimers.delete(mediaId)
    void persistWorkspaceState(mediaId).catch((error) => console.warn('工作区状态保存失败：', error))
  }, 800))
}

export const cancelScheduledSave = (mediaId: string) => {
  const timer = workspaceSaveTimers.get(mediaId)
  if (timer) clearTimeout(timer)
  workspaceSaveTimers.delete(mediaId)
}
export const workspaceRecoveryRequired = computed(()=>!!workspaceReadError.value)
export const saveRecoveryLabel = computed(()=>workspaceRecoveryRequired.value?'重新读取工作区':pendingDeletion.value?'重试本次删除/撤销':pendingFeedback.value?'重试异常确认':'重试保存')
export const retryWorkspaceSave = async () => {
  cancelScheduledSave(currentMediaId.value)
  if(pendingDeletion.value){await retryObjectDeletion();return}
  if(pendingFeedback.value){await retryTrackingAnomalyFeedback();return}
  await persistWorkspaceState(currentMediaId.value,true)
}
export const downloadWorkspaceBackup = () => {
  const mediaId=currentMediaId.value
  // Explicit reload keeps a local backup of geometry and pending intent.
  const payload={workspace:buildWorkspaceState(mediaId,true),objects:annotationsByMedia.value[mediaId]||[],tracking:trackingFramesByMedia.value[mediaId]||[],pendingDeletion:pendingDeletion.value,pendingFeedback:pendingFeedback.value,pendingRestart:pendingRestart.value}
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}))
  const anchor=document.createElement('a');anchor.href=url;anchor.download='annotation-recovery-'+createRequestId()+'.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
export const reloadWorkspaceFromServer = async () => {
  const mediaId=currentMediaId.value,serverId=selectedMedia.value?.serverMediaId
  if(serverId){
    const base=workspaceWrites.getBaseline(serverId)
    if(base)await workspaceWrites.saveDraft(serverId,base,sharedWorkspace(buildWorkspaceState(mediaId,true)))
    else sessionStorage.setItem(`annotation-recovery:${useAuth().user.value?.id}:${serverId}`,JSON.stringify(buildWorkspaceState(mediaId,true)))
  }
  cancelScheduledSave(mediaId)
  if (pendingRestart.value?.phase === 'conflict' && pendingRestart.value.mediaId === mediaId) { rememberRestart(pendingRestart.value, true); pendingRestart.value = null; trackingRestartError.value = '' }
  annotationsByMedia.value={...annotationsByMedia.value,[mediaId]:(annotationsByMedia.value[mediaId]||[]).filter(o=>o.source==='manual')}
  trackingFramesByMedia.value[mediaId]=[]
  await resetAnnotationViewForMedia(mediaId)
}
export const controlsCacheKey = (serverMediaId: string) => {
  let actor = 'unknown'
  try { actor = String(JSON.parse(localStorage.getItem('rare-sperm-auth') || '{}').id || actor) } catch {}
  return `annotation-controls:${actor}:${serverMediaId}`
}
export const rememberControls = (mediaId: string) => {
  const media = mediaAssets.value.find(m => m.id === mediaId)
  if (!media?.serverMediaId) return
  try {
    sessionStorage.setItem(controlsCacheKey(media.serverMediaId), JSON.stringify({
      deletion: pendingDeletion.value?.mediaId === mediaId ? pendingDeletion.value : null,
      feedback: pendingFeedback.value?.mediaId === mediaId ? pendingFeedback.value : null,
      history: videoDeletionHistory.value[mediaId] ?? null,
    }))
  } catch (error) { console.warn('[annotation.controls_cache_failed]', { mediaId, error }) }
}
export const definiteRejection = (error: unknown) => {
  const status = Number((error as { status?: number })?.status)
  return status >= 400 && status < 500 && status !== 408 && status !== 429
}
export const markVersionConflict = (error: unknown) => {
  if (error instanceof Error && /工作区版本|重试标识/.test(error.message)) {
    workspaceReadError.value = `${error.message}；请重新加载素材`
    saveState.value = 'error'; saveError.value = workspaceReadError.value
  }
}

export const setupSaveWatchers = () => {
  watch(annotationsByMedia, (value,previous) => {
    for(const id of Object.keys(value))if(value[id]!==previous?.[id])localDirtyMedia.add(id)
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(async () => {
      const actor=useAuth().user.value?.id,dirty=new Set(localDirtyMedia)
      localDirtyMedia.clear()
      try { await persistLocalAnnotations(dirty) } catch (error) { console.error('[annotation.local_save_failed]', error); if(actor===useAuth().user.value?.id)saveError.value = '本机缓存写入失败，请保持页面打开并检查存储空间' }
      if (actor===useAuth().user.value?.id && !runtime.workspaceRestoreInProgress) scheduleWorkspaceStateSave(selectedMediaId.value)
    }, 300)
  })
}
