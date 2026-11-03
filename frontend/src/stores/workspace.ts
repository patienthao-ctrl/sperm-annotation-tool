// 标注工作区 store 的组装入口：各职责模块在 ./workspace/，这里只注册监听并汇总 useWorkspace() 的对外接口。
import {
  trackingRestartBusy, trackingRestartError, trackingRestartPendingAction, trackingRestartStorageWarning,
  collaborationConflict, collaborationBusy, currentRemoteTracking, lastEditors, storageWarning, trackingError,
  trackingProgress, trackingProgressViewFor, currentTrackingProgress, currentTrackingFinished, trackingRetryBusy,
  isTrackingMedia, canUndoVideoDeletion, lastVideoObjectDeletion, objectDeletionBusy, objectDeletionError,
  objectDeletionPendingAction, trackingFeedbackBusy, trackingFeedbackError, trackingFeedbackPending,
  trackingFeedbackPendingAction, trackingCalibrationSummary, trackingWarningSummary, deletingMediaId,
  mediaDeleteError, saveState, saveError, playbackRate, displayObjects, editingBlocked, workspaceRestoring,
  frameError, api, mediaAssets, selectedMediaId, submissionLocks, submissionBusy, mediaImportBusy, importError,
  duplicateImport, activeTool, objectNameInput, selectedObjectId, currentFrame, currentTime, videoDuration,
  videoFps, frameInput, isPlaying, isAiBusy, trackingFrameCount, statusMessage, toastMessage, showToast, zoom,
  imageRef, videoRef, exactFrameImageRef, exactFrameUrl, exactFrameLoading, annotationHitRef, fileInputRef,
  videoInputRef, packageInputRef, packageImportReport, effectFolderInputRef, annotationsByMedia,
  trackingFramesByMedia, anomalyObjectIds, anomalyFrames, pausedAnomalies, anomalyPanelVisible,
  trackingPausedFrame, closeAnomalyPanel, selectedMedia, isVideo, maxFrameIndex, currentMediaId, currentObjects,
  selectedObject, brightness, contrast, mediaFilterStyle, resetMediaFilter, annotatedFrameCount,
} from './workspace/state'
import {
  canUndo, canRedo, undo, redo,
} from './workspace/history'
import {
  nudgeSelected, cancelAnnotationGesture, edgeDiscovery, headBoxBusy, headBoxError, cancelHeadBox, createHeadBox,
  zoomIn, zoomOut, zoomReset, getStagePoint, addObject, selectTool, onStageClick, tempBbox,
  onBboxDown, onBboxMove, onBboxUp, onObjectDropdownChange, selectObject, removeObject, renameObject,
  copyPreviousFrame, clearSelection, setupEditingWatchers,
} from './workspace/editing'
import {
  retryExactFrame, pausePlayback, loadExactFrame, videoPlaybackFallback, formatTime, timeToFrame, frameToTime,
  resetVideoViewToFirstFrame, ensureVideoFirstFrame, onImageLoaded, onVideoLoaded, onVideoTimeUpdate,
  onVideoError, seekVideo, seekToInputFrame, seekByFrame, togglePlayback, onVideoEnded, onTimelineClick,
  setupFramesWatchers,
} from './workspace/frames'
import {
  downloadWorkspaceBackup, workspaceRecoveryRequired, saveRecoveryLabel, retryWorkspaceSave,
  reloadWorkspaceFromServer, persistWorkspaceState, setupSaveWatchers,
} from './workspace/save'
import {
  resolveCollaboration, syncWorkspace, discardLocalDraft,
} from './workspace/collaboration'
import {
  restartTrackingFromEarlierFrame, retryTrackingRestart, cancelTrackingRestartRequest, getObjectDeletionSummary,
  removeObjectAcrossVideo, undoVideoObjectDeletion, retryObjectDeletion, confirmTrackingAnomaly,
  retryTrackingAnomalyFeedback, resetTrackingCalibration,
} from './workspace/trackingActions'
import {
  deleteMedia, reimportSaving, cancelDuplicateImport, confirmDuplicateImport, restoreImportIntent, importPackage,
  openFilePicker, handleFiles, loadServerMedia,
} from './workspace/media'
import {
  resetAnnotationViewForMedia, setupRestoreWatchers,
} from './workspace/restore'
import {
  loadTrackingResult, getMediaPixelSize,
} from './workspace/trackingResult'
import {
  runEndpointTrack, trackingRetryLabel, retryTracking, runAiSegment, runAiTrack,
} from './workspace/tracking'
import {
  savedResults, loadedRemoteResultKeys, effectResults, selectedEffectId, effectTime, effectPlaying,
  effectVideoRef, selectedEffect, buildSam3AnnotationsJson, generateAnnotationsJson, loadSavedResults,
  openEffectFolderPicker, handleEffectFolder, loadEffects, onEffectTimeUpdate, toggleEffectPlayback,
  selectEffect, effectOverlayObjects,
} from './workspace/results'

const createWorkspace = () => {
  setupSaveWatchers()
  setupEditingWatchers()
  setupRestoreWatchers()
  setupFramesWatchers()

  return {
    runEndpointTrack,
    restartTrackingFromEarlierFrame, retryTrackingRestart, cancelTrackingRestartRequest, trackingRestartBusy, trackingRestartError, trackingRestartPendingAction, trackingRestartStorageWarning,
    collaborationConflict,collaborationBusy,resolveCollaboration,syncWorkspace,downloadWorkspaceBackup,currentRemoteTracking,lastEditors,discardLocalDraft,storageWarning,trackingError,trackingProgress,trackingProgressViewFor,currentTrackingProgress,currentTrackingFinished,trackingRetryLabel,trackingRetryBusy,retryTracking,isTrackingMedia,workspaceRecoveryRequired,saveRecoveryLabel,retryWorkspaceSave,reloadWorkspaceFromServer,
    getObjectDeletionSummary, removeObjectAcrossVideo, undoVideoObjectDeletion, retryObjectDeletion, canUndoVideoDeletion, lastVideoObjectDeletion, objectDeletionBusy, objectDeletionError, objectDeletionPendingAction, confirmTrackingAnomaly, retryTrackingAnomalyFeedback, trackingFeedbackBusy, trackingFeedbackError, trackingFeedbackPending, trackingFeedbackPendingAction, trackingCalibrationSummary, resetTrackingCalibration, trackingWarningSummary,
    deleteMedia, deletingMediaId, mediaDeleteError, persistWorkspaceState, retryExactFrame, saveState, saveError, playbackRate, pausePlayback, displayObjects, editingBlocked, workspaceRestoring, frameError, loadExactFrame, nudgeSelected, cancelAnnotationGesture, canUndo, canRedo,
    edgeDiscovery, headBoxBusy, headBoxError, cancelHeadBox, createHeadBox, api, mediaAssets, selectedMediaId, submissionLocks, submissionBusy, mediaImportBusy, importError, duplicateImport, reimportSaving, cancelDuplicateImport, confirmDuplicateImport, restoreImportIntent, activeTool, objectNameInput, selectedObjectId, currentFrame, currentTime, videoDuration, videoFps, frameInput, isPlaying, isAiBusy, trackingFrameCount, statusMessage, toastMessage, showToast, zoom, zoomIn, zoomOut, zoomReset, savedResults, loadedRemoteResultKeys, effectResults, selectedEffectId, effectTime, effectPlaying, effectVideoRef, imageRef, videoRef, exactFrameImageRef, exactFrameUrl, exactFrameLoading, videoPlaybackFallback, annotationHitRef, fileInputRef, videoInputRef, packageInputRef, packageImportReport, importPackage, effectFolderInputRef, annotationsByMedia, trackingFramesByMedia, anomalyObjectIds, anomalyFrames, pausedAnomalies, anomalyPanelVisible, trackingPausedFrame, closeAnomalyPanel, selectedMedia, isVideo, maxFrameIndex, currentMediaId, currentObjects, selectedObject, selectedEffect, formatTime, timeToFrame, frameToTime, getStagePoint, addObject, resetVideoViewToFirstFrame, ensureVideoFirstFrame, selectTool, onStageClick, tempBbox, onBboxDown, onBboxMove, onBboxUp, onObjectDropdownChange, selectObject, removeObject, renameObject, undo, redo, copyPreviousFrame, brightness, contrast, mediaFilterStyle, resetMediaFilter, annotatedFrameCount, clearSelection, openFilePicker, handleFiles, onImageLoaded, onVideoLoaded, onVideoTimeUpdate, onVideoError, loadTrackingResult, seekVideo, seekToInputFrame, seekByFrame, togglePlayback, onVideoEnded, onTimelineClick, runAiSegment, runAiTrack, getMediaPixelSize, buildSam3AnnotationsJson, generateAnnotationsJson, loadSavedResults, openEffectFolderPicker, handleEffectFolder, loadEffects, onEffectTimeUpdate, toggleEffectPlayback, selectEffect, effectOverlayObjects, resetAnnotationViewForMedia, loadServerMedia
  }
}

const workspace = createWorkspace()
export const useWorkspace = () => workspace
