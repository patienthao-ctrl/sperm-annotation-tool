// 切换素材/账号时的工作区恢复与视图重置。
import { nextTick, watch } from 'vue'
import type { AnnotationObject } from '../../types/annotation'
import { trackApi } from '../../api/trackApi'
import { WorkspaceMergeConflict, type WorkspaceDraft } from '../../annotation/workspaceWrites'
import { useAuth } from '../auth'
import { activeTool, annotationsByMedia, anomalyFrames, anomalyObjectIds, anomalyPanelVisible, brightness, collaborationErrors, contrast, currentFrame, currentMediaId, currentTime, dedupeAnnotationObjects, deletedFrameObjects, deletedObjectIds, deletedTrackingIds, exactFrameLoading, frameError, frameInput, isObjectDeleted, isVideo, lastPausedContext, maxFrameIndex, mediaAssets, messageOf, normalMotionSamples, objectDeletionError, objectNameInput, pausedAnomalies, pendingDeletion, pendingFeedback, pendingRestart, runtime, saveError, saveQueues, saveState, selectedMediaId, selectedObjectId, showToast, trackingErrors, trackingFeedbackError, trackingFeedbackEvents, trackingFinishedByMedia, type TrackingJob, trackingJobs, trackingMedia, trackingProgressByMedia, trackingRestartBusy, trackingRestartError, trackingRestartStorageWarning, trackingStatusByMedia, trackingWarningSummary, videoDeletionHistory, videoDuration, videoFps, videoRef, workspaceGenerations, workspaceReadError, workspaceRestoring, workspaceWrites, zoom } from './state'
import { histories, historyRevision } from './history'
import { cancelAnnotationGesture, tempBbox } from './editing'
import { frameCache, frameToTime, loadExactFrame, prefetchClip, requestedFrame, revokeExactFrameUrl, stopFallbackPlayback, videoPlaybackFallback } from './frames'
import { controlsCacheKey, persistWorkspaceState, rememberControls, workspaceSaveTimers } from './save'
import { savePersonalView, viewKey } from './collaboration'
import { restoreRestart } from './trackingActions'
import { clearResetCache } from './media'
import { loadTrackingResult, normalizePauseNotice } from './trackingResult'
import { trackingJournalKey } from './tracking'
import { afterTrackingLoaded } from './prelabel'

const restoreWorkspaceState = async (mediaId: string, isCurrent: () => boolean): Promise<number | null> => {
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media?.serverMediaId) return null
  try {
    try {
      const controls = JSON.parse(sessionStorage.getItem(controlsCacheKey(media.serverMediaId)) || 'null')
      if (controls?.history) { videoDeletionHistory.value[mediaId] = controls.history; runtime.historySequence = Math.max(runtime.historySequence, controls.history.sequence) }
      if (controls?.deletion) pendingDeletion.value = { ...controls.deletion, mediaId }
      if (controls?.feedback) pendingFeedback.value = { ...controls.feedback, mediaId }
    } catch (error) { console.warn('[annotation.controls_restore_failed]', { mediaId, error }) }
    await saveQueues.get(mediaId)?.catch(() => {})
    if (!isCurrent()) return null
    let state = await trackApi.getWorkspaceState(media.serverMediaId)
    if (!isCurrent()) return null
    const generationKey=`annotation-generation:${media.serverMediaId}`
    if(state.generationId&&localStorage.getItem(generationKey)!==state.generationId){
      await workspaceWrites.discardForReset(media.serverMediaId,state.revision||0)
      if (!isCurrent()) return null
      clearResetCache(media.serverMediaId,state.revision||0)
      if(pendingDeletion.value?.mediaId===mediaId)pendingDeletion.value=null
      if(pendingFeedback.value?.mediaId===mediaId)pendingFeedback.value=null
      localStorage.setItem(generationKey,state.generationId)
      showToast('视频已覆盖重新标注，已清除旧工作区缓存')
    }else{
      await workspaceWrites.replay(media.serverMediaId)
      if (!isCurrent()) return null
      state = await trackApi.getWorkspaceState(media.serverMediaId)
      if (!isCurrent()) return null
    }
    workspaceGenerations.set(mediaId,state.generationId)
    workspaceWrites.setBaseline(media.serverMediaId,state)
    try {
      const restored=await workspaceWrites.restoreDraft(media.serverMediaId,state)
      if(restored)state=await trackApi.getWorkspaceState(media.serverMediaId)
    }catch(error){
      if(error instanceof WorkspaceMergeConflict)collaborationErrors.value={...collaborationErrors.value,[mediaId]:error}
      else throw error
    }
    if(!isCurrent())return null
    workspaceWrites.setBaseline(media.serverMediaId,state)
    let personal:Pick<WorkspaceDraft,'currentFrame'|'display'|'editor'>={currentFrame:0,display:{brightness:100,contrast:100,zoom:1},editor:{activeTool:'bbox'}}
    try{personal=JSON.parse(sessionStorage.getItem(viewKey(mediaId))||'null')??personal}catch{}
    state={...state,...personal}
    workspaceReadError.value = ''
    deletedObjectIds.value[mediaId] = state.deletedObjectIds ?? []
    deletedFrameObjects.value[mediaId] = state.deletedFrameObjects ?? []
    normalMotionSamples.value[mediaId] = state.normalMotionSamples ?? []
    trackingFeedbackEvents.value[mediaId] = state.trackingFeedbackEvents ?? []
    if (pendingDeletion.value?.mediaId === mediaId) {
      const intent = pendingDeletion.value
      const deleted = state.deletedObjectIds?.includes(intent.record.objectId)
      if ((intent.action === 'undo') === !deleted) videoDeletionHistory.value[mediaId] = { ...intent.record, undone: intent.action === 'undo', sequence: ++runtime.historySequence }
      pendingDeletion.value = null
    }
    if (pendingFeedback.value?.mediaId === mediaId) pendingFeedback.value = null
    rememberControls(mediaId)
    if (!state.exists || mediaId !== currentMediaId.value) return null
    runtime.workspaceRestoreInProgress = true
    const restoredManual = (collaborationErrors.value[mediaId]?.local.manualAnnotations ?? state.manualAnnotations ?? [])
      .filter((value): value is AnnotationObject => !!value && typeof value === 'object')
      .map((value) => ({ ...value, source: 'manual' as const }))
    const retainedAi = (annotationsByMedia.value[mediaId] ?? []).filter((obj) => obj.source === 'ai')
    annotationsByMedia.value = {
      ...annotationsByMedia.value,
      [mediaId]: dedupeAnnotationObjects([...retainedAi, ...restoredManual]).filter(o => !isObjectDeleted(mediaId, o.objectId, o.frameIndex ?? 0)),
    }
    deletedTrackingIds.value[mediaId] = new Set((state.deletedTrackingIds ?? []).map(String))
    anomalyFrames.value = Array.isArray(state.anomalyFrames) ? state.anomalyFrames : []
    pausedAnomalies.value = Array.isArray(state.pausedAnomalies)
      ? (state.pausedAnomalies as typeof pausedAnomalies.value).map(normalizePauseNotice)
      : []
    lastPausedContext.value = state.lastPausedContext
      ? { mediaId, frameIndex: Number(state.lastPausedContext.frameIndex) }
      : null
    brightness.value = Number(state.display?.brightness) || 100
    contrast.value = Number(state.display?.contrast) || 100
    zoom.value = Number(state.display?.zoom) || 1
    if (state.editor?.activeTool && ['select', 'point', 'bbox'].includes(state.editor.activeTool)) {
      // Saved drawing tools come back as the combined tool: click finds a head, drag draws.
      activeTool.value = state.editor.activeTool === 'select' ? 'select' : 'smart'
    }
    if (typeof state.editor?.objectNameInput === 'string') objectNameInput.value = state.editor.objectNameInput
    selectedObjectId.value = typeof state.editor?.selectedObjectId === 'string'
      ? state.editor.selectedObjectId
      : null
    anomalyPanelVisible.value = pausedAnomalies.value.length > 0
    const frame = Math.max(0, Math.min(maxFrameIndex.value, Number(state.currentFrame) || 0))
    return frame
  } catch (error) {
    if (!isCurrent()) return null
    workspaceReadError.value = '工作区读取失败，请重新加载素材后再编辑，避免覆盖服务器状态'
    saveState.value = 'error'; saveError.value = workspaceReadError.value
    console.error('[annotation.workspace_load_failed]', { mediaId, error })
    return null
  } finally {
    if (isCurrent()) runtime.workspaceRestoreInProgress = false
  }
}

let restoreSerial = 0
export const resetAnnotationViewForMedia = async (mediaId = currentMediaId.value) => {
  if (mediaId !== currentMediaId.value) return
  const serial = ++restoreSerial
  const actor = useAuth().user.value?.id
  const isCurrent = () => serial === restoreSerial && mediaId === currentMediaId.value && actor === useAuth().user.value?.id
  runtime.restartController?.abort(); runtime.restartController = null; runtime.restartEpoch++
  trackingRestartBusy.value = false; pendingRestart.value = null; trackingRestartError.value = ''; trackingRestartStorageWarning.value = ''
  cancelAnnotationGesture(); frameCache.clear(); runtime.exactFrameRequestSerial++; runtime.seekSerial++
  exactFrameLoading.value = false; runtime.isSeekingVideo = false; requestedFrame.value = null; frameError.value = ''
  workspaceRestoring.value = true
  saveState.value = 'idle'; saveError.value = ''
  trackingWarningSummary.value = []
  objectDeletionError.value = ''; trackingFeedbackError.value = ''
  revokeExactFrameUrl()
  selectedObjectId.value = null
  currentFrame.value = 0
  currentTime.value = 0
  frameInput.value = 0
  tempBbox.value = null
  runtime.bboxStart = null
  activeTool.value = 'smart'
  anomalyObjectIds.value = []
  anomalyFrames.value = []
  pausedAnomalies.value = []
  lastPausedContext.value = null
  zoom.value = 1
  videoPlaybackFallback.value = false
  stopFallbackPlayback()
  // A local image has no server workspace to reload. In particular, the
  // sample image after a real login must not inherit the account-change lock.
  if (!isVideo.value) workspaceReadError.value = ''
  if (isVideo.value && mediaId === currentMediaId.value) {
    // 后端上传元数据在 <video> loadedmetadata/error 之前已可用。先采用
    // 它，避免不同 FPS 的 AVI 逐帧预览仍沿用默认 30 FPS 计算时间轴。
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (media?.fps && media.fps > 0) videoFps.value = media.fps
    if (media?.frameCount && media.fps) videoDuration.value = media.frameCount / media.fps
    const restoredFrame = await restoreWorkspaceState(mediaId, isCurrent)
    if (!isCurrent()) return
    const targetFrame = restoredFrame ?? 0
    if (workspaceReadError.value) { workspaceRestoring.value = false; return }
    await nextTick()
    if (!isCurrent()) return
    revokeExactFrameUrl()
    const video = videoRef.value
    if (video) {
      try { video.pause(); video.currentTime = frameToTime(targetFrame) } catch {}
    }
    await loadExactFrame(targetFrame, mediaId)
    if (!isCurrent()) return
    prefetchClip(mediaId)
    try { await loadTrackingResult(mediaId, true, true) }
    catch(error) {
      if(isCurrent()) {
        workspaceReadError.value=`追踪结果读取失败：${messageOf(error,'服务器响应异常')}。请重新读取工作区后再编辑，避免将未知数据当成空帧`
        saveState.value='error';saveError.value=workspaceReadError.value
        workspaceRestoring.value=false
      }
      return
    }
    if (!isCurrent()) return
    void afterTrackingLoaded(mediaId)
    try{
      const job=JSON.parse(sessionStorage.getItem(trackingJournalKey(mediaId))||'null') as TrackingJob|null
      // A loop still polling this video in the background already owns its job.
      if(job?.key&&job.mediaId===mediaId&&job.input.mediaId===media?.serverMediaId&&!trackingMedia.has(mediaId)){
        trackingJobs.set(mediaId,job)
        trackingErrors.value[mediaId]={frameIndex:job.startFrame,phase:'恢复任务状态',message:'上次追踪结果尚未确认，请查询原任务，避免重复启动。'}
      }
    }catch(error){console.warn('[annotation.tracking_restore_failed]',{mediaId,error})}
    restoreRestart(mediaId)
  }
  if (isCurrent()) workspaceRestoring.value = false
}

export const setupRestoreWatchers = () => {

  // Switching assets happens before the new asset resets the shared editor
  // refs. Persist the old asset with the still-current UI values so its frame,
  // anomaly panel and display/editor state are not overwritten by defaults.
  watch(selectedMediaId, (nextId, previousId) => {
    try { localStorage.setItem('annotation-selected-media', nextId) } catch (error) { console.warn('[annotation.selection_save_failed]', error) }
    if (!previousId || previousId === nextId) return
    void persistWorkspaceState(previousId, true).catch((error) => console.warn('切换素材前保存工作区状态失败：', error))
  })

  watch(
    [currentFrame, brightness, contrast, zoom, activeTool, objectNameInput, selectedObjectId],
    () => {
      // A view reset while switching videos or modes must not overwrite the saved view.
      if (!runtime.workspaceRestoreInProgress && !workspaceRestoring.value) savePersonalView(currentMediaId.value)
    },
    { deep: true },
  )

  watch(() => useAuth().user.value?.id, (next, previous) => {
    if (next === previous) return
    restoreSerial++; runtime.trackingLoadSerial++
    runtime.workspaceRestoreInProgress = false
    for (const timer of workspaceSaveTimers.values()) clearTimeout(timer)
    workspaceSaveTimers.clear()
    pendingDeletion.value = null; pendingFeedback.value = null
    runtime.restartController?.abort(); runtime.restartController = null; runtime.restartEpoch++
    pendingRestart.value = null; trackingRestartBusy.value = false; trackingRestartError.value = ''; trackingRestartStorageWarning.value = ''
    videoDeletionHistory.value = {}; histories.clear(); historyRevision.value++
    objectDeletionError.value = ''; trackingFeedbackError.value = ''
    trackingJobs.clear();trackingMedia.clear();trackingProgressByMedia.value={};trackingStatusByMedia.value={};trackingFinishedByMedia.value={};trackingErrors.value={}
    workspaceReadError.value = '账号已切换，请重新加载素材'
  })
}
