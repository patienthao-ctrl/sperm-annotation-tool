// 追踪相关专用写入：从较早帧重追、整视频删除对象及撤销/重做、异常确认反馈；均保留原键重放。
import { createRequestId } from '../../utils/browserCompat'
import type { AnnotationObject } from '../../types/annotation'
import { trackApi, type ObjectDeletionSummary, type TrackingFeedbackResponse } from '../../api/trackApi'
import { useAuth } from '../auth'
import { annotationsByMedia, anomalyFrames, anomalyObjectIds, anomalyPanelVisible, clone, currentFrame, currentMediaId, currentObjects, dedupeAnnotationObjects, deletedFrameObjects, deletedObjectIds, editingBlocked, exactFrameLoading, frameError, lastPausedContext, mediaAssets, messageOf, normalMotionSamples, objectDeletionBusy, objectDeletionError, pausedAnomalies, pendingDeletion, pendingFeedback, pendingRestart, runtime, saveError, saveState, selectedMedia, selectedObjectId, showToast, statusMessage, submissionBusy, trackingErrors, trackingFeedbackBusy, trackingFeedbackError, trackingFeedbackEvents, trackingFeedbackPending, trackingFramesByMedia, trackingJobs, trackingPausedFrame, trackingRestartBusy, trackingRestartError, type TrackingRestartIntent, trackingRestartStorageWarning, type VideoDeletion, videoDeletionHistory, workspaceGenerations, workspaceReadError, workspaceRestoring, workspaceWrites } from './state'
import { histories, historyRevision } from './history'
import { isInteracting } from './editing'
import { frameToTime, pausePlayback, seekVideo } from './frames'
import { buildWorkspaceState, cancelScheduledSave, definiteRejection, latestManualBaselines, markVersionConflict, persistWorkspaceState, rememberControls } from './save'
import { loadTrackingResult, normalizePauseNotice } from './trackingResult'
import { performTracking, runAiTrack } from './tracking'

export const cancelTrackingRestartRequest = () => { runtime.restartController?.abort(); runtime.restartController = null; runtime.restartEpoch++; trackingRestartBusy.value = false }
const restartJournalKey = (actorId: string, serverMediaId: string) => `annotation-restart:${actorId}:${serverMediaId}`
export const rememberRestart = (intent: TrackingRestartIntent, remove = false) => {
  try {
    const key = restartJournalKey(intent.actorId, intent.serverMediaId)
    if (remove) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, JSON.stringify(intent))
  } catch (error) {
    trackingRestartStorageWarning.value = '浏览器无法保存重追恢复记录。请保留当前页面重试；刷新或关闭页面后无法保证恢复此次请求。'
    console.error('[annotation.restart_journal_failed]', { mediaId: intent.serverMediaId, error })
  }
}
export const restoreRestart = (mediaId: string) => {
  const media = mediaAssets.value.find(item => item.id === mediaId)
  const actorId = useAuth().user.value?.id
  if (!media?.serverMediaId || !actorId) return
  try {
    const intent = JSON.parse(sessionStorage.getItem(restartJournalKey(actorId, media.serverMediaId)) || 'null') as TrackingRestartIntent | null
    if (!intent) return
    if (intent.actorId !== actorId || intent.serverMediaId !== media.serverMediaId || !intent.key
      || !['restart', 'start', 'conflict'].includes(intent.phase) || !Number.isSafeInteger(intent.body?.expectedRevision)
      || !Number.isSafeInteger(intent.body?.startFrame) || !Number.isSafeInteger(intent.body?.expectedPausedFrame)
      || intent.body.startFrame < 0 || intent.body.startFrame >= intent.body.expectedPausedFrame || intent.body.confirmDiscardFuture !== true) throw new Error('回退重追恢复记录损坏')
    intent.mediaId = mediaId
    if (trackingJobs.has(mediaId)) { rememberRestart(intent, true); return }
    pendingRestart.value = intent
    trackingRestartError.value = intent.phase === 'conflict' ? '回退时工作区发生冲突，请备份并重新读取工作区后再选择起点。'
      : intent.phase === 'start' ? '旧分支已替换，新追踪尚未开始。请继续从已确认的起点追踪。'
        : '上次回退请求结果尚未确认，请重试原请求；不能重新创建或替换起点。'
    if (intent.phase === 'conflict') workspaceReadError.value = trackingRestartError.value
  } catch (error) {
    trackingRestartError.value = '回退重追恢复记录无法读取，请保留页面并联系管理员检查。'
    workspaceReadError.value = trackingRestartError.value
    console.error('[annotation.restart_restore_failed]', { mediaId: media.serverMediaId, error })
  }
}
export const retryTrackingRestart = async (): Promise<boolean> => {
  const intent = pendingRestart.value
  if (!intent || trackingRestartBusy.value || intent.phase === 'conflict' || currentMediaId.value !== intent.mediaId || useAuth().user.value?.id !== intent.actorId) return false
  const serial = ++runtime.restartEpoch, actorId = intent.actorId
  runtime.restartController?.abort(); runtime.restartController = new AbortController()
  const isCurrent = () => serial === runtime.restartEpoch && actorId === useAuth().user.value?.id && currentMediaId.value === intent.mediaId && pendingRestart.value === intent
  trackingRestartBusy.value = true; trackingRestartError.value = ''
  try {
    if (intent.phase === 'restart') {
      const result = await trackApi.restartBranch(intent.serverMediaId, intent.body, intent.key, runtime.restartController.signal)
      if (!isCurrent()) return false
      workspaceWrites.setRevision(intent.serverMediaId, result.revision)
      intent.phase = 'start'; rememberRestart(intent)
      console.info('[annotation.restart_committed]', { mediaId: intent.serverMediaId, startFrame: intent.body.startFrame, pausedFrame: intent.body.expectedPausedFrame, revision: result.revision, removedRows: result.removedRows })
    }
    if (!isCurrent()) return false
    // Re-tracking changes the annotation; a review pass simply becomes stale.
    if (submissionBusy.value) throw new Error('当前有导出操作尚未结束，请先完成或关闭导出，再继续原起点追踪')
    const start = intent.body.startFrame
    // Human edits anywhere in the video survive replacing an AI branch.
    annotationsByMedia.value = { ...annotationsByMedia.value, [intent.mediaId]: (annotationsByMedia.value[intent.mediaId] || []).filter(object => object.source === 'manual' || (object.frameIndex ?? 0) <= start) }
    trackingFramesByMedia.value[intent.mediaId] = (trackingFramesByMedia.value[intent.mediaId] || []).filter(frame => frame.frameIndex <= start)
    pausedAnomalies.value = []; lastPausedContext.value = null; anomalyObjectIds.value = []; anomalyPanelVisible.value = false
    anomalyFrames.value = anomalyFrames.value.filter(frame => frame.frame_index <= start)
    delete videoDeletionHistory.value[intent.mediaId]
    for (const key of histories.keys()) if (key.startsWith(`${intent.mediaId}:`)) histories.delete(key)
    historyRevision.value++; rememberControls(intent.mediaId)
    if (currentFrame.value !== start || frameError.value) await seekVideo(frameToTime(start))
    if (!isCurrent() || currentFrame.value !== start || frameError.value || exactFrameLoading.value) throw new Error('分支回退已保存，但起始帧未读取成功；请重试从原起点继续追踪')
    await performTracking(false, intent)
    if (pendingRestart.value === intent && isCurrent()) {
      const failure = trackingErrors.value[intent.mediaId]
      trackingRestartError.value = failure ? `${failure.phase}失败：${failure.message}。已替换的分支保留，请继续原起点。` : '新追踪尚未开始，请重试从原起点继续追踪。'
      delete trackingErrors.value[intent.mediaId]
      return false
    }
    return true
  } catch (error) {
    if (!isCurrent()) return false
    const requestId = (error as {requestId?: string}).requestId
    trackingRestartError.value = `${messageOf(error, '回退重追失败，请重试原请求')}${requestId ? `（记录号 ${requestId}）` : ''}`
    if (definiteRejection(error)) {
      intent.phase = 'conflict'; rememberRestart(intent)
      workspaceReadError.value = `${trackingRestartError.value}；请备份并重新读取工作区，不能自动覆盖新状态`
      saveState.value = 'error'; saveError.value = workspaceReadError.value
    }
    console.error('[annotation.restart_failed]', { mediaId: intent.serverMediaId, startFrame: intent.body.startFrame, pausedFrame: intent.body.expectedPausedFrame, phase: intent.phase, requestId, error })
    return false
  } finally {
    if (serial === runtime.restartEpoch && actorId === useAuth().user.value?.id) trackingRestartBusy.value = false
  }
}
export const restartTrackingFromEarlierFrame = async (startFrame: number, pausedFrame: number): Promise<boolean> => {
  const media = selectedMedia.value, actorId = useAuth().user.value?.id
  if (!media?.serverMediaId || !actorId || editingBlocked.value || isInteracting() || startFrame !== currentFrame.value
    || pausedFrame !== trackingPausedFrame.value || startFrame >= pausedFrame || !currentObjects.value.some(object => object.bbox)) return false
  const serial = ++runtime.restartEpoch
  const isCurrent = () => serial === runtime.restartEpoch && actorId === useAuth().user.value?.id && media.id === currentMediaId.value
  trackingRestartBusy.value = true; trackingRestartError.value = ''; trackingRestartStorageWarning.value = ''
  try {
    cancelScheduledSave(media.id)
    await persistWorkspaceState(media.id)
    if (!isCurrent()) return false
    const expectedRevision = workspaceWrites.getRevision(media.serverMediaId)
    if (expectedRevision == null) throw new Error('工作区版本尚未读取，请重新读取素材后再重追')
    pendingRestart.value = { actorId, mediaId: media.id, serverMediaId: media.serverMediaId, key: createRequestId(), phase: 'restart', body: { expectedRevision, expectedPausedFrame: pausedFrame, startFrame, confirmDiscardFuture: true, ...(workspaceGenerations.get(media.id) ? {generationId: workspaceGenerations.get(media.id)} : {}) } }
    rememberRestart(pendingRestart.value)
  } catch (error) {
    if (isCurrent()) { trackingRestartError.value = messageOf(error, '请先重试保存当前修正，再重新追踪'); markVersionConflict(error); console.error('[annotation.restart_prepare_failed]', {mediaId:media.serverMediaId,startFrame,error}) }
    return false
  } finally { if (isCurrent()) trackingRestartBusy.value = false }
  return retryTrackingRestart()
}
export const getObjectDeletionSummary = async (objectId: number): Promise<ObjectDeletionSummary> => {
  const media = selectedMedia.value
  if (!media?.serverMediaId || objectDeletionBusy.value || trackingFeedbackPending.value) throw new Error('当前无法读取删除范围')
  if (pendingDeletion.value) throw new Error('请先重试上次删除操作')
  objectDeletionBusy.value = true
  try {
    cancelScheduledSave(media.id)
    await persistWorkspaceState(media.id)
    return await trackApi.getObjectDeletionSummary(media.serverMediaId, objectId)
  } finally { objectDeletionBusy.value = false }
}
export const retryObjectDeletion = async (): Promise<boolean> => {
  const intent = pendingDeletion.value
  if (!intent || objectDeletionBusy.value) return false
  objectDeletionBusy.value = true; objectDeletionError.value = ''
  try {
    await workspaceWrites.write(intent.serverMediaId, 'workspace', intent.state, intent.key)
    const mid = intent.mediaId, record = intent.record
    deletedObjectIds.value[mid] = [...(intent.state.deletedObjectIds ?? [])]
    deletedFrameObjects.value[mid] = clone(intent.state.deletedFrameObjects ?? [])
    const others = (annotationsByMedia.value[mid] ?? []).filter(o => o.objectId !== record.objectId)
    annotationsByMedia.value = { ...annotationsByMedia.value, [mid]: intent.action === 'undo' ? dedupeAnnotationObjects([...others, ...record.objects]) : others }
    videoDeletionHistory.value[mid] = { ...record, sequence: ++runtime.historySequence, undone: intent.action === 'undo' }
    if (mid === currentMediaId.value) {
      pausedAnomalies.value = (intent.state.pausedAnomalies ?? []) as typeof pausedAnomalies.value
      lastPausedContext.value = intent.state.lastPausedContext ?? null
      selectedObjectId.value = null
      saveState.value = 'saved'; saveError.value = ''; workspaceReadError.value = ''
    }
    pendingDeletion.value = null; rememberControls(mid); historyRevision.value++
    console.info('[annotation.object_deletion_saved]', { mediaId: intent.serverMediaId, objectId: record.objectId, action: intent.action, key: intent.key })
    statusMessage.value = intent.action === 'undo' ? `已恢复 ${record.name} 在全视频中的标注` : `已删除 ${record.name} 在全视频中的标注，可撤销`
    showToast(statusMessage.value,'success')
    if (intent.action === 'undo' && mid === currentMediaId.value) await loadTrackingResult(mid, true)
    return true
  } catch (error) {
    objectDeletionError.value = error instanceof Error ? error.message : '删除保存失败，请重试'
    if (definiteRejection(error)) { pendingDeletion.value = null; rememberControls(intent.mediaId) }
    markVersionConflict(error)
    console.error('[annotation.object_deletion_failed]', { mediaId: intent.serverMediaId, objectId: intent.record.objectId, key: intent.key, error })
    return false
  } finally { objectDeletionBusy.value = false }
}
export const applyVideoDeletion = async (action: 'delete' | 'undo' | 'redo', record: VideoDeletion): Promise<boolean> => {
  const media = selectedMedia.value
  if (!media?.serverMediaId || editingBlocked.value || isInteracting()) return false
  objectDeletionBusy.value = true; objectDeletionError.value = ''
  try {
    pausePlayback(); cancelScheduledSave(media.id)
    await persistWorkspaceState(media.id)
    const state = buildWorkspaceState(media.id)
    const deleted = new Set(state.deletedObjectIds ?? [])
    if (action === 'undo') deleted.delete(record.objectId); else deleted.add(record.objectId)
    state.deletedObjectIds = [...deleted]
    const otherManual = (state.manualAnnotations as AnnotationObject[]).filter(o => o.objectId !== record.objectId)
    state.manualAnnotations = action === 'undo' ? dedupeAnnotationObjects([...otherManual, ...record.objects.filter(o => o.source === 'manual')]) : otherManual
    state.manualBaselines = latestManualBaselines(media, state.manualAnnotations as AnnotationObject[])
    if (action === 'undo' && record.pause) {
      state.pausedAnomalies = [...(state.pausedAnomalies as typeof pausedAnomalies.value).filter(item => item.objectId !== record.objectId), clone(record.pause.item)]
      state.lastPausedContext = clone(record.pause.context)
    }
    if (action !== 'undo') {
      state.pausedAnomalies = (state.pausedAnomalies as typeof pausedAnomalies.value).filter(item => item.objectId !== record.objectId)
      if (!state.pausedAnomalies.length) state.lastPausedContext = null
    }
    pendingDeletion.value = { mediaId: media.id, serverMediaId: media.serverMediaId, key: createRequestId(), action, record: clone(record), state }
    rememberControls(media.id)
  } catch (error) {
    objectDeletionError.value = error instanceof Error ? error.message : '删除准备失败，请重试'
    markVersionConflict(error)
    return false
  } finally { objectDeletionBusy.value = false }
  return retryObjectDeletion()
}
export const removeObjectAcrossVideo = async (objectId: number): Promise<boolean> => {
  const objects = (annotationsByMedia.value[currentMediaId.value] ?? []).filter(o => o.objectId === objectId)
  if (!objects.length) { objectDeletionError.value = '当前对象不存在，请重新选择'; return false }
  const item = pausedAnomalies.value.find(item => item.objectId === objectId)
  const pause = item && lastPausedContext.value?.mediaId === currentMediaId.value ? { item: clone(item), context: clone(lastPausedContext.value) } : undefined
  return applyVideoDeletion('delete', { pause, objectId, name: objects[0].name, objects: clone(objects), frameCount: new Set(objects.map(o => o.frameIndex ?? 0)).size, sequence: ++runtime.historySequence, undone: false })
}
export const undoVideoObjectDeletion = async (): Promise<boolean> => {
  const record = videoDeletionHistory.value[currentMediaId.value]
  if (!record || record.undone) return false
  return applyVideoDeletion('undo', record)
}
export const retryTrackingAnomalyFeedback = async (): Promise<boolean> => {
  const intent = pendingFeedback.value
  if (!intent || trackingFeedbackBusy.value) return false
  trackingFeedbackBusy.value = true; trackingFeedbackError.value = ''
  let resume = false
  try {
    const response = await workspaceWrites.write(intent.serverMediaId, 'feedback', intent.body, intent.key) as unknown as TrackingFeedbackResponse
    normalMotionSamples.value[intent.mediaId] = response.normalMotionSamples
    trackingFeedbackEvents.value[intent.mediaId] = response.trackingFeedbackEvents ?? []
    if (intent.mediaId === currentMediaId.value) {
      pausedAnomalies.value = (response.pausedAnomalies as typeof pausedAnomalies.value).map(normalizePauseNotice)
      lastPausedContext.value = response.lastPausedContext ? { mediaId: intent.mediaId, frameIndex: response.lastPausedContext.frameIndex } : null
      anomalyObjectIds.value = pausedAnomalies.value.map(item => item.objectId)
      anomalyPanelVisible.value = pausedAnomalies.value.length > 0
      resume = intent.body.decision !== 'reset' && !pausedAnomalies.value.some(item=>!item.resolved) && !response.lastPausedContext
    }
    pendingFeedback.value = null; rememberControls(intent.mediaId)
    showToast(intent.body.decision === 'reset' ? `已恢复对象 #${intent.body.objectId} 的默认判断` : intent.body.scope === 'frame' ? '本帧全部对象的人工检查点已保存，继续追踪' : resume ? '人工确认已保存，继续追踪' : '已确认此对象，请继续检查其他暂停对象','success')
    console.info('[annotation.feedback_saved]', { mediaId: intent.serverMediaId, objectId: intent.body.objectId, decision: intent.body.decision, scope:intent.body.scope ?? 'object', remaining:response.pausedAnomalies.filter(item=>!(item as {resolved?:boolean}).resolved).length, calibrate: intent.body.calibrate, key: intent.key })
  } catch (error) {
    trackingFeedbackError.value = error instanceof Error ? error.message : '确认保存失败，请重试'
    if (definiteRejection(error)) { pendingFeedback.value = null; rememberControls(intent.mediaId) }
    markVersionConflict(error)
    console.error('[annotation.feedback_failed]', { mediaId: intent.serverMediaId, key: intent.key, error })
    return false
  } finally { trackingFeedbackBusy.value = false }
  if (resume) {
    // Confirmation applies to the paused frame even if the view moved during
    // a slow request. Resume only from that exact saved frame.
    if (currentFrame.value !== intent.body.frameIndex) await seekVideo(frameToTime(intent.body.frameIndex))
    if (currentMediaId.value === intent.mediaId && currentFrame.value === intent.body.frameIndex && !frameError.value && !workspaceRestoring.value && !exactFrameLoading.value) await runAiTrack()
    else showToast('确认已保存；暂停帧读取未完成，请返回该帧后手动继续追踪','warning')
  }
  return true
}
const prepareTrackingFeedback = async (objectId: number, decision: 'normal' | 'corrected' | 'reset', learn: boolean,scope:'object'|'frame'='object'): Promise<boolean> => {
  const media = selectedMedia.value
  if (!media?.serverMediaId || editingBlocked.value || isInteracting()) return false
  const context = lastPausedContext.value
  if (decision !== 'reset' && (context?.mediaId !== media.id || context.frameIndex !== currentFrame.value || !pausedAnomalies.value.some(item => item.objectId === objectId))) {
    trackingFeedbackError.value = '请返回暂停帧并选择待确认对象'; return false
  }
  trackingFeedbackBusy.value = true; trackingFeedbackError.value = ''
  try {
    cancelScheduledSave(media.id)
    await persistWorkspaceState(media.id)
    pendingFeedback.value = { mediaId: media.id, serverMediaId: media.serverMediaId, key: createRequestId(), body: { expectedRevision: 0, objectId, frameIndex: context?.frameIndex ?? currentFrame.value, decision, calibrate: decision === 'normal' && learn, ...(decision !== 'reset' ? {scope} : {}) } }
    rememberControls(media.id)
  } catch (error) {
    trackingFeedbackError.value = error instanceof Error ? error.message : '请先重试工作区保存'
    markVersionConflict(error)
    return false
  } finally { trackingFeedbackBusy.value = false }
  return retryTrackingAnomalyFeedback()
}
export const confirmTrackingAnomaly = (objectId: number, decision: 'normal' | 'corrected', learn: boolean,scope:'object'|'frame'='object') => prepareTrackingFeedback(objectId, decision, learn,scope)
export const resetTrackingCalibration = (objectId: number) => prepareTrackingFeedback(objectId, 'reset', false)
