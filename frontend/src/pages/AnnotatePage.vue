<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { canvasZoomKey } from '../workbench/viewTransform'
import AnnotationDatasetExport from '../components/AnnotationDatasetExport.vue'
import MediaLibrary from '../components/MediaLibrary.vue'
import { importRunning, prepareImport } from '../stores/importQueue'
import { reviewFilterKey } from '../stores/annotationReview'
import AnnotationReviewPanel from '../components/AnnotationReviewPanel.vue'
import AppNotice from '../components/AppNotice.vue'
import { useNoticeCollapse } from '../annotation/useNoticeCollapse'
import { TOAST_DURATION } from '../annotation/notices'
import { motionAllowed } from '../utils/motion'
import ReviewZoomPreview from '../components/ReviewZoomPreview.vue'
import AnnotationMinimap from '../components/AnnotationMinimap.vue'
import WorkbenchShell from '../components/WorkbenchShell.vue'
import WorkbenchFrameStrip from '../components/WorkbenchFrameStrip.vue'
import TrackingProgress from '../components/TrackingProgress.vue'
import AppIcon from '../components/AppIcon.vue'
import AnnotationOverlay from '../components/AnnotationOverlay.vue'
import AnnotationTrackingFeedback from '../components/AnnotationTrackingFeedback.vue'
import UserGuide from '../components/UserGuide.vue'
import userGuide from '../help/user-guide.md?raw'
import { revealBoxScroll } from '../annotation/geometry'
import { useAuth } from '../stores/auth'
import { useWorkspace } from '../stores/workspace'
import { addLeaveGuard, useRouter } from '../router'
import { useAnnotationReview } from '../stores/annotationReview'
import type { MergeChoice, MergeEntry } from '../annotation/workspaceWrites'
import '../annotation/annotation.css'
import '../annotation/annotation-shell.css'
import { cancelExtend, checkFrames, currentCheckFrame, currentPrelabel, extendBusy, markFrameScanned, nextUnscannedFrame, prelabelErrors, reprelabelBusy, reprelabelCurrent, retryPrelabel, scanBusy, scanComplete, scannedCount } from '../stores/workspace/prelabel'
import { trackingResultState } from '../stores/workspace/state'
const {
  collaborationConflict, collaborationBusy, resolveCollaboration, syncWorkspace, downloadWorkspaceBackup, currentRemoteTracking, lastEditors, discardLocalDraft, storageWarning, headBoxBusy, headBoxError, cancelHeadBox, mediaAssets, selectedMediaId, submissionBusy, mediaImportBusy, importError, duplicateImport, reimportSaving, cancelDuplicateImport, confirmDuplicateImport, restoreImportIntent, activeTool, selectedObjectId, currentFrame, currentTime, frameInput, isPlaying,
  refreshLibrary, isAiBusy, statusMessage, toast, dismissToast, imageRef, videoRef, exactFrameImageRef, exactFrameUrl, exactFrameLoading, videoPlaybackFallback, annotationHitRef, fileInputRef, videoInputRef, packageInputRef, packageImportReport, importPackage,
  annotationsByMedia, selectedMedia, isVideo, maxFrameIndex, currentMediaId, currentObjects, selectedObject, displayObjects, editingBlocked, workspaceRestoring,
  anomalyFrames, pausedAnomalies, trackingPausedFrame, formatTime, selectTool, onStageClick, tempBbox, onBboxDown, onBboxMove, onBboxUp,
  selectObject, removeObject, undo, redo, canUndo, canRedo, copyPreviousFrame, brightness, contrast, mediaFilterStyle, resetMediaFilter, annotatedFrameCount,
  clearSelection, openFilePicker, handleFiles, onImageLoaded, onVideoLoaded, onVideoTimeUpdate, onVideoError, seekToInputFrame, togglePlayback, onVideoEnded,
  runEndpointTrack, seekVideo, frameToTime, resetAnnotationViewForMedia, seekByFrame, zoom, zoomIn, zoomOut, zoomReset,
  deleteMedia, deletingMediaId, mediaDeleteError, nudgeSelected, cancelAnnotationGesture, frameError, retryExactFrame, saveState, saveError, persistWorkspaceState, playbackRate, pausePlayback,
  getObjectDeletionSummary, removeObjectAcrossVideo, undoVideoObjectDeletion, canUndoVideoDeletion, lastVideoObjectDeletion,
  objectDeletionBusy, objectDeletionError, objectDeletionPendingAction, retryObjectDeletion,
  confirmTrackingAnomaly, trackingFeedbackBusy, trackingFeedbackError, trackingFeedbackPending, trackingFeedbackPendingAction, retryTrackingAnomalyFeedback,
  trackingCalibrationSummary, resetTrackingCalibration, trackingWarningSummary,
  trackingError,trackingProgress,trackingProgressViewFor,currentTrackingProgress,currentTrackingFinished,trackingRetryLabel,trackingRetryBusy,retryTracking,isTrackingMedia,workspaceRecoveryRequired,saveRecoveryLabel,retryWorkspaceSave,reloadWorkspaceFromServer,
  restartTrackingFromEarlierFrame, retryTrackingRestart, cancelTrackingRestartRequest, trackingRestartBusy, trackingRestartError, trackingRestartPendingAction, trackingRestartStorageWarning,
} = useWorkspace()
// The review step uses this same workbench: same boxes, same tools, same saves.
// Review mode swaps tracking controls for the review checklist.
const props = withDefaults(defineProps<{ mode?: 'annotate' | 'review' }>(), { mode: 'annotate' })
const reviewing = computed(() => props.mode === 'review')
const router = useRouter()
const review = useAnnotationReview()
const reviewPanel = ref<InstanceType<typeof AnnotationReviewPanel> | null>(null)
const reviewMediaId = computed(() => selectedMedia.value?.type === 'video' ? selectedMedia.value?.serverMediaId ?? null : null)
const mergeChoices=ref<Record<string,MergeChoice>>({})
watch(collaborationConflict,()=>{mergeChoices.value={} })
const mergeReady=computed(()=>!!collaborationConflict.value && collaborationConflict.value.entries.every(e=>!!mergeChoices.value[e.key]))
function chooseMerge(key:string,value:'local'|'remote',remote:unknown){mergeChoices.value={...mergeChoices.value,[key]:{value,remote}}}
// The other version is named after whoever saved the shared workspace last.
const otherEditor=computed(()=>lastEditors.value[currentMediaId.value]||'对方')
type PercentBox={x:number;y:number;width:number;height:number}
function entryObject(entry:MergeEntry,side:'local'|'remote'):{bbox?:PercentBox}|undefined{
  const v=entry[side] as {bbox?:PercentBox;manualAnnotations?:Array<{frameIndex?:number;bbox?:PercentBox}>}|undefined
  if(!v)return undefined
  if(Array.isArray(v.manualAnnotations))return v.manualAnnotations.find(o=>(o.frameIndex??0)===entry.frameIndex)??v.manualAnnotations[0]
  return v
}
function pixelBox(obj:{bbox?:PercentBox}|undefined){
  const b=obj?.bbox,w=selectedMedia.value?.width||0,h=selectedMedia.value?.height||0
  return b&&w&&h?[b.x/100*w,b.y/100*h,(b.x+b.width)/100*w,(b.y+b.height)/100*h]:null
}
function mergeSummary(entry:MergeEntry,side:'local'|'remote'){
  if(side==='remote'&&entry.deleted)return `${otherEditor.value}删除了它`
  if(side==='local'&&entry.localDeleted)return '你删除了它'
  return side==='local'?'你的框（实线）':`${otherEditor.value}的框（虚线）`
}
async function locateConflict(entry:MergeEntry){
  if(entry.frameIndex===undefined)return
  await seekFrame(entry.frameIndex)
}
let collaborationTimer: ReturnType<typeof setInterval>|null=null
// Refresh the review checklist only when the sync actually brought in someone else's change.
onMounted(()=>{collaborationTimer=setInterval(async()=>{if(pointerActive||pan||review.busy.value)return;const id=reviewMediaId.value;const changed=await syncWorkspace();if(changed===true&&reviewing.value&&id&&id===reviewMediaId.value&&!review.busy.value)void review.load(id)},5000)})
onUnmounted(()=>{if(collaborationTimer)clearInterval(collaborationTimer)})
const reviewFrames = computed(() => {
  const todo = new Set<number>(), done = new Set<number>()
  for (const frame of review.targetFrames.value) (review.checkedFrames.value.has(frame) ? done : todo).add(frame)
  return { todo, done }
})
const flushWorkspace = () => persistWorkspaceState(currentMediaId.value, true)
// A frame can be confirmed only once it is actually on screen and editable.
const reviewFrameReady = computed(() => !exactFrameLoading.value && !frameError.value && !workspaceRestoring.value && !isAiBusy.value)
function seekReviewFrame(frame: number) {
  if (!reviewing.value || isAiBusy.value || pointerActive || pan) return
  void seekFrame(frame)
}
watch(reviewMediaId, id => { void review.load(id) }, { immediate: true })
const pictureEnters = ref(true)
watch(selectedMediaId, () => { pictureEnters.value = true })
watch(isPlaying, playing => { if (playing) pictureEnters.value = false })
// App.vue keeps this page mounted across the two stages. The leave guard has already saved and cancelled gestures;
// what a remount used to do is limited to this. The frame, zoom, selection and caches deliberately stay.
watch(reviewing, () => {
  help.value = false; hiddenBoxes.value = false // A reviewer has to see the boxes being confirmed.
  cancelTrackingRestartRequest(); closeRestartDialog()
  if (deleteDialog.value?.open) deleteDialog.value.close()
  void syncWorkspace(); void review.load(reviewMediaId.value); void review.loadSummary()
})
// The minimap covers part of the image, so it is shown only on request.
const minimapOn = ref((() => { try { return localStorage.getItem('annotation-minimap') === '1' } catch { return false } })())
watch(minimapOn, value => { try { localStorage.setItem('annotation-minimap', value ? '1' : '0') } catch {} })
// Saved edits can invalidate a pass; refresh the status shortly after saves settle.
let reviewRefresh: ReturnType<typeof setTimeout> | null = null
watch(saveState, value => {
  if (value !== 'saved' || !reviewMediaId.value) return
  if (reviewRefresh) clearTimeout(reviewRefresh)
  reviewRefresh = setTimeout(() => { if(!review.busy.value)void review.load(reviewMediaId.value) }, 1500)
})
// One file keeps the single-file flow (it may offer to overwrite); several go to the batch queue, which never does.
function pickVideos(event:Event){const input=event.target as HTMLInputElement;if((input.files?.length||0)>1){prepareImport(input.files);input.value=''}else void handleFiles(input.files,'video')}
function pickPackages(event:Event){const input=event.target as HTMLInputElement;if((input.files?.length||0)>1){prepareImport(input.files);input.value=''}else void importPackage(input.files)}
// The list shows colleagues' work without a timer: on opening the drawer and on returning to the page.
const refreshIdle=()=>{if(!libraryBusy.value)void refreshLibrary()}
onMounted(()=>window.addEventListener('focus',refreshIdle))
onUnmounted(()=>window.removeEventListener('focus',refreshIdle))
const nextReviewVideo=computed(()=>{
  const current=selectedMedia.value
  if(!current)return null
  const waiting=(media:typeof current)=>media.id!==current.id&&!!media.serverMediaId&&['ready','stale'].includes(reviewFilterKey(media.serverMediaId)||'')
  return mediaAssets.value.find(media=>waiting(media)&&(media.folderPath||'')===(current.folderPath||''))??mediaAssets.value.find(waiting)??null
})
const importDialog=ref<HTMLElement|null>(null)
watch(duplicateImport,async value=>{if(value){await nextTick();importDialog.value?.querySelector<HTMLElement>('button:not(:disabled)')?.focus()}})
function importDialogKeys(e:KeyboardEvent){if(e.key==='Escape'){e.preventDefault();cancelDuplicateImport()}if(e.key==='Tab'){const nodes=Array.from(importDialog.value?.querySelectorAll<HTMLElement>('button:not(:disabled)')||[]);if(nodes.length){e.preventDefault();const i=nodes.indexOf(document.activeElement as HTMLElement);nodes[(i+(e.shiftKey?-1:1)+nodes.length)%nodes.length]?.focus()}}}
const scrollContainerRef = ref<HTMLDivElement | null>(null), stageRef = ref<HTMLDivElement | null>(null), loupe = ref<HTMLCanvasElement | null>(null)
const help = ref(false), helpDialog = ref<HTMLElement | null>(null), focused = ref(false), labels = ref(false), hiddenBoxes = ref(false), magnifier = ref(false), inverted = ref(false), enhancing = ref(false), spaceHeld = ref(false)
// Switching or importing videos stays possible while the current video tracks.
const libraryBusy = computed(() => submissionBusy.value || mediaImportBusy.value || !!duplicateImport.value || !!deletingMediaId.value || objectDeletionBusy.value || trackingFeedbackBusy.value || trackingRestartBusy.value || !!trackingRestartPendingAction.value || !!objectDeletionPendingAction.value || trackingFeedbackPending.value)
const mediaBusy = computed(() => submissionBusy.value || mediaImportBusy.value || !!duplicateImport.value || isAiBusy.value || !!deletingMediaId.value || objectDeletionBusy.value || trackingFeedbackBusy.value || trackingRestartBusy.value || !!trackingRestartPendingAction.value || !!objectDeletionPendingAction.value || trackingFeedbackPending.value)
const jumpFrame = ref(1)
// The shell keeps the media drawer closed while a video is open (it opens itself
// when nothing is selected) and the inspector forced open while tracking is paused.
const libraryOpen = ref(false)
watch(libraryOpen,value=>{if(value)refreshIdle()})
const frameStats = computed(() => {
  const stats = new Map<number, { manual: number; ai: number }>()
  for (const object of annotationsByMedia.value[currentMediaId.value] ?? []) {
    if (!object.bbox && !object.point) continue
    const frame = object.frameIndex ?? 0, entry = stats.get(frame) ?? { manual: 0, ai: 0 }
    if (object.source === 'manual') entry.manual++; else entry.ai++
    stats.set(frame, entry)
  }
  return stats
})
const anomalyFrameSet = computed(() => new Set(anomalyFrames.value.map(item => item.frame_index)))
const frameKind = (frame: number) => { const s = frameStats.value.get(frame); return s?.manual ? 'manual' : s?.ai ? 'ai' : 'empty' }
const frameFlags = (frame: number) => reviewing.value
  ? [reviewFrames.value.todo.has(frame) ? 'review-todo' : reviewFrames.value.done.has(frame) ? 'review-done' : '']
  : [checkFrameState.value.get(frame) ?? '', anomalyFrameSet.value.has(frame) ? 'anomaly' : '', trackingPausedFrame.value === frame ? 'paused' : ''].filter(Boolean)
const frameTitle = (frame: number) => { const s = frameStats.value.get(frame); const parts = [s?.manual ? `人工 ${s.manual}` : '', s?.ai ? `AI ${s.ai}` : ''].filter(Boolean); const check = checkFrameState.value.get(frame); return `第 ${frame + 1} 帧 · ${parts.join(' · ') || '无框'}${check ? (check === 'check-done' ? ' · 已查漏' : ' · 要查漏') : ''}${trackingPausedFrame.value === frame ? ' · 追踪暂停' : anomalyFrameSet.value.has(frame) ? ' · 追踪提示' : ''}` }
// Frames the annotator scans for heads the detector missed, marked on the frame strip.
const checkFrameState = computed(() => new Map(checkFrames.value.map(item => [item.frame, item.scannedAt ? 'check-done' : 'check'])))
const prelabelError = computed(() => prelabelErrors.value[currentMediaId.value] || '')
const legacyResult = computed(() => isVideo.value && trackingResultState.value[currentMediaId.value] === 'available' && currentPrelabel.value?.state === 'none')
const reprelabelConfirm = ref(false)
watch(currentMediaId, () => { reprelabelConfirm.value = false })
const prelabelRunning = computed(() => currentTrackingProgress.value?.steps[0]?.key === 'detect')
async function finishScan() {
  if (!currentCheckFrame.value || currentCheckFrame.value.scannedAt || scanBusy.value || editingBlocked.value) return
  await flushWorkspace().catch(() => undefined)
  const next = await markFrameScanned()
  if (next != null) await seekFrame(next)
}
function goNextCheck() { if (nextUnscannedFrame.value != null) void seekFrame(nextUnscannedFrame.value) }
const frameManualCount = computed(() => currentObjects.value.filter(object => object.source === 'manual').length)
const endpointCount = (frame:number) => (annotationsByMedia.value[currentMediaId.value]||[]).filter(o=>o.source==='manual' && (o.frameIndex??0)===frame && o.bbox).length
async function requestEndpointTracking() {
  if(editingBlocked.value || isAiBusy.value || hasPausedTracking.value || !isVideo.value)return
  pausePlayback();cancelPointer()
  const head=endpointCount(0),tail=endpointCount(maxFrameIndex.value)
  if(!head){await seekVideo(0);return}
  if(window.confirm(`首帧 ${head} 个人工头框；尾帧 ${tail} 个人工头框。\n${tail?'请确认已完整检查首尾两帧。':'请确认尾帧确实没有精子头；缺少尾帧目标的片段仍需审查。'}\n将重新生成整段视频标注，保留人工框并统一匹配对象编号。旧 AI 分支会重建。`)) await runEndpointTrack()
}
const frameAiCount = computed(() => currentObjects.value.length - frameManualCount.value)
const minimapSource = computed(() => isVideo.value ? (exactFrameUrl.value && !isPlaying.value ? exactFrameImageRef.value : videoRef.value) : imageRef.value)
async function seekFrame(frame: number) { if (isAiBusy.value) return; frameInput.value = frame; await seekToInputFrame() }
const size = ref({ w: 0, h: 0 }), base = ref({ w: 800, h: 450 })
const mousePixel = ref<{ x: number; y: number } | null>(null)
const stageSize = computed(() => ({ w: Math.round(base.value.w * zoom.value), h: Math.round(base.value.h * zoom.value) }))
const stageStyle = computed(() => ({
  width: `${stageSize.value.w}px`, height: `${stageSize.value.h}px`,
  // Vue style bindings require explicit CSS lengths for nonzero margins.
  marginLeft: pauseStageMargins.value ? `${pauseStageMargins.value.marginLeft}px` : undefined,
  marginRight: pauseStageMargins.value ? `${pauseStageMargins.value.marginRight}px` : undefined,
  marginTop: pauseStageMargins.value ? `${pauseStageMargins.value.marginTop}px` : undefined,
  marginBottom: pauseStageMargins.value ? `${pauseStageMargins.value.marginBottom}px` : undefined,
}))
const pauseStageMargins = ref<{marginLeft:number;marginRight:number;marginTop:number;marginBottom:number} | null>(null)
// Another video tracking in the background: the closed media drawer shows a pulsing dot.
const backgroundTracking = computed(() => mediaAssets.value.some(media => media.id !== selectedMediaId.value && isTrackingMedia(media.id)))
const filter = computed(() => `${mediaFilterStyle.value} ${inverted.value ? 'invert(1)' : ''}`)
const saveLabel = computed(() => workspaceRecoveryRequired.value ? '需重新读取' : selectedMedia.value?.serverMediaId ? ({ idle:'自动保存', saving:'保存中…', saved:'已保存', error:'保存失败' }[saveState.value]) : '本机自动保存')
// Shortcut help and the full guide share the same maintained Markdown source.
const jumpFrameInput = ref<HTMLInputElement|null>(null)
const shortcutSection = userGuide.split(/^## 快捷键速查\s*$/m)[1] || ''
const shortcutHelpNote = shortcutSection.trim().split(/\n\s*\n/)[0] || ''
const annotationShortcuts = (shortcutSection.match(/人工标注\s*\n\s*(\| 快捷键[\s\S]*?)(?=\n\s*审查\s*\n)/)?.[1] || '')
  .trim().split('\n').slice(2).filter(row => row.startsWith('|')).map(row => row.split('|').slice(1,-1).map(cell => cell.trim()))
const selectedObjectCoverage = computed(() => {
  const id = selectedObject.value?.objectId
  if (id == null) return 0
  return new Set((annotationsByMedia.value[currentMediaId.value] || []).filter(object => object.objectId === id).map(object => object.frameIndex ?? 0)).size
})
const unresolvedAnomalies = computed(() => pausedAnomalies.value.filter(item => !item.resolved))
const frameCanLearn = computed(() => unresolvedAnomalies.value.some(item => item.canLearn))
const feedbackTargetId = ref<number | null>(null), learnNormalMotion = ref(true)
const feedbackTarget = computed(() => {
  const pendingId = trackingFeedbackPendingAction.value?.objectId
  if (pendingId != null) return pausedAnomalies.value.find(item => item.objectId === pendingId) || null
  return unresolvedAnomalies.value.find(item => item.objectId === feedbackTargetId.value)
    || unresolvedAnomalies.value.find(item => item.objectId === selectedObject.value?.objectId)
    || unresolvedAnomalies.value[0] || null
})
const hasPausedTracking = computed(() => trackingPausedFrame.value != null && pausedAnomalies.value.length > 0)
const showPauseFeedback = computed(() => !reviewing.value && (hasPausedTracking.value || !!(trackingFeedbackError.value && trackingFeedbackPendingAction.value?.decision !== 'reset')))
const pauseControlHost = ref<HTMLElement | null>(null), inspectorDetailsHost = ref<HTMLElement | null>(null), focusDetailsHost = ref<HTMLElement | null>(null)
const pauseDetailsOpen = ref(false)
const pauseDetailsTarget = computed(() => focused.value ? focusDetailsHost.value : inspectorDetailsHost.value)
watch([currentMediaId, showPauseFeedback], ([mediaId, shown], [previousMedia]) => { if (!shown || mediaId !== previousMedia) pauseDetailsOpen.value = false })

const canRestartEarlier = computed(() => hasPausedTracking.value && trackingPausedFrame.value != null && currentFrame.value < trackingPausedFrame.value && currentObjects.value.some(object => object.bbox))
const feedbackBlocked = computed(() => isAiBusy.value || !!deletingMediaId.value || exactFrameLoading.value || isPlaying.value || workspaceRestoring.value || !!frameError.value || objectDeletionBusy.value || trackingFeedbackBusy.value || (editingBlocked.value && !trackingFeedbackPending.value))
const missingFeedbackTarget = computed(() => currentFrame.value===trackingPausedFrame.value && !!feedbackTarget.value && !currentObjects.value.some(object=>object.objectId===feedbackTarget.value!.objectId && object.bbox))
const previousFeedbackTargetFrame = computed(() => {
  if (!missingFeedbackTarget.value || trackingPausedFrame.value==null) return null
  const frames=(annotationsByMedia.value[currentMediaId.value] || []).filter(object=>object.objectId===feedbackTarget.value?.objectId && object.bbox && (object.frameIndex ?? 0)<trackingPausedFrame.value!).map(object=>object.frameIndex ?? 0)
  return frames.length ? Math.max(...frames) : null
})
const canCopyMissingFeedbackTarget = computed(() => missingFeedbackTarget.value && previousFeedbackTargetFrame.value===currentFrame.value-1 && !currentObjects.value.some(object=>object.objectId===feedbackTarget.value?.objectId))
// An automatic selection is a one-shot response to a new pause, an accepted
// item, or returning to its frame. Ordinary inspection never reselects a box.
const feedbackSelectionRequest=ref<{mediaId:string;frameIndex:number;objectId:number;notices:typeof pausedAnomalies.value}|null>(null)
let applyingFeedbackSelection=false
function requestFeedbackSelection(objectId:number) {
  if (trackingPausedFrame.value==null) return
  feedbackSelectionRequest.value={mediaId:currentMediaId.value,frameIndex:trackingPausedFrame.value,objectId,notices:pausedAnomalies.value}
}
async function applyFeedbackSelection() {
  const request=feedbackSelectionRequest.value
  if (!request || request.mediaId!==currentMediaId.value || request.frameIndex!==trackingPausedFrame.value || request.notices!==pausedAnomalies.value) { feedbackSelectionRequest.value=null;return }
  if (currentFrame.value!==request.frameIndex || feedbackBlocked.value || trackingFeedbackPending.value || editingBlocked.value || trackingRestartBusy.value || trackingRestartPendingAction.value || pointerActive || pan || help.value || restartDialogOpen.value || deletionDialogOpen.value) return
  if (!unresolvedAnomalies.value.some(item=>item.objectId===request.objectId)) { feedbackSelectionRequest.value=null;return }
  applyingFeedbackSelection=true
  feedbackTargetId.value=request.objectId
  const object=currentObjects.value.find(item=>item.objectId===request.objectId)
  if (object) { selectTool('select');selectObject(object.id) }
  else clearSelection()
  applyingFeedbackSelection=false
  feedbackSelectionRequest.value=null
  await nextTick()
  // An explicit selection/media change during frame rendering cancels a late pan.
  if (!object?.bbox || request.mediaId!==currentMediaId.value || request.frameIndex!==currentFrame.value || request.frameIndex!==trackingPausedFrame.value || request.notices!==pausedAnomalies.value || selectedObjectId.value!==object.id || feedbackBlocked.value || trackingFeedbackPending.value || pointerActive || pan) return
  const stage=stageRef.value,scroller=scrollContainerRef.value
  if (!stage || !scroller) return
  const r=stage.getBoundingClientRect(),vp=scroller.getBoundingClientRect(),box=object.bbox
  const position=revealBoxScroll({x:r.left+box.x*r.width/100,y:r.top+box.y*r.height/100,width:box.width*r.width/100,height:box.height*r.height/100},{x:vp.left,y:vp.top,width:scroller.clientWidth,height:scroller.clientHeight},{left:scroller.scrollLeft,top:scroller.scrollTop,maxLeft:scroller.scrollWidth-scroller.clientWidth,maxTop:scroller.scrollHeight-scroller.clientHeight})
  scroller.scrollTo(position.left,position.top)
}
const deleteDialog = ref<HTMLDialogElement | null>(null), deletionDialogOpen = ref(false)
type DeletionSummary = Awaited<ReturnType<typeof getObjectDeletionSummary>>
const deletionSummary = ref<DeletionSummary | null>(null), deletionSummaryLoading = ref(false), deletionSummaryError = ref('')
const deletionTarget = ref<{objectId:number;name:string;mediaId:string;mediaName:string} | null>(null)
const frameDeletion = ref<{id:string;objectId?:number;name:string;mediaId:string;frameIndex:number;fingerprint:string} | null>(null)
let deletionSummarySerial = 0, deletionPriorFocus: HTMLElement | null = null, deletionPriorOverflow = ''
const frameFingerprint = () => JSON.stringify(currentObjects.value)
const frameDeletionVisible = computed(() => !!frameDeletion.value && frameDeletion.value.mediaId===currentMediaId.value && frameDeletion.value.frameIndex===currentFrame.value && frameDeletion.value.fingerprint===frameFingerprint())
const videoDeletionVisible = computed(() => !!lastVideoObjectDeletion.value && !(annotationsByMedia.value[currentMediaId.value] || []).some(object => object.objectId===lastVideoObjectDeletion.value?.objectId))
function deleteSelectedFrame() {
  const object = selectedObject.value
  if (!object || editingBlocked.value || objectDeletionBusy.value || trackingFeedbackPending.value || objectDeletionPendingAction.value) return
  removeObject(object.id)
  if (!currentObjects.value.some(item => item.id === object.id)) {
    clearSelection()
    frameDeletion.value = { id:object.id, objectId:object.objectId, name:object.name, mediaId:currentMediaId.value, frameIndex:currentFrame.value, fingerprint:frameFingerprint() }
  }
}
function undoFrameDeletion() { undo(); frameDeletion.value = null }
async function selectFeedbackObject(objectId:number) {
  if (trackingFeedbackPending.value || feedbackBlocked.value || editingBlocked.value || trackingPausedFrame.value==null || !unresolvedAnomalies.value.some(item=>item.objectId===objectId)) return
  feedbackTargetId.value = objectId
  hiddenBoxes.value=false
  requestFeedbackSelection(objectId)
  if(currentFrame.value!==trackingPausedFrame.value){frameInput.value=trackingPausedFrame.value;await seekToInputFrame()}
  await applyFeedbackSelection()
}
function restoreMissingFeedbackTarget() {
  const objectId=feedbackTarget.value?.objectId
  if (objectId==null || !canCopyMissingFeedbackTarget.value || feedbackBlocked.value || trackingFeedbackPending.value || editingBlocked.value) return
  copyPreviousFrame()
  selectFeedbackObject(objectId)
}
async function locatePreviousFeedbackTarget() {
  const frame=previousFeedbackTargetFrame.value,objectId=feedbackTarget.value?.objectId,mediaId=currentMediaId.value
  if (frame==null || objectId==null || feedbackBlocked.value || trackingFeedbackPending.value || editingBlocked.value) return
  frameInput.value=frame
  await seekToInputFrame()
  if(currentMediaId.value!==mediaId || currentFrame.value!==frame || feedbackBlocked.value || trackingFeedbackPending.value || editingBlocked.value) return
  const object=currentObjects.value.find(item=>item.objectId===objectId&&item.bbox)
  if(object){selectTool('select');selectObject(object.id)}
}
async function confirmFeedback(decision:'normal'|'corrected',scope:'object'|'frame'='object') {
  const target = feedbackTarget.value
  if (!target || feedbackBlocked.value || trackingFeedbackPending.value || currentFrame.value!==trackingPausedFrame.value) return
  await confirmTrackingAnomaly(target.objectId, decision, decision==='normal' && (scope==='frame'?frameCanLearn.value:target.canLearn) && learnNormalMotion.value,scope)
}
async function returnToPausedFrame() {
  if (trackingPausedFrame.value==null || isAiBusy.value || trackingFeedbackBusy.value) return
  frameInput.value = trackingPausedFrame.value
  await seekToInputFrame()
}
const restartDialog = ref<HTMLDialogElement | null>(null), restartDialogOpen = ref(false)
const restartTarget = ref<{mediaId:string;startFrame:number;pausedFrame:number} | null>(null)
let restartPriorFocus: HTMLElement | null = null, restartPriorOverflow = ''
async function openRestartDialog() {
  if (editingBlocked.value || !canRestartEarlier.value || trackingPausedFrame.value == null) return
  cancelPointer(); pausePlayback()
  restartTarget.value = {mediaId:currentMediaId.value,startFrame:currentFrame.value,pausedFrame:trackingPausedFrame.value}
  restartPriorFocus = document.activeElement as HTMLElement; restartPriorOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  await nextTick(); restartDialog.value?.showModal(); restartDialogOpen.value = true
}
function finishRestartDialog() {
  if (!restartDialogOpen.value) return
  restartDialogOpen.value = false; document.body.style.overflow = restartPriorOverflow
  if (restartPriorFocus?.isConnected && !(restartPriorFocus as HTMLButtonElement).disabled) restartPriorFocus.focus({preventScroll:true})
  else focusButton.value?.focus({preventScroll:true})
}
function closeRestartDialog() { restartDialog.value?.close(); finishRestartDialog() }
async function confirmRestart() {
  const target = restartTarget.value
  if (!target || target.mediaId !== currentMediaId.value || trackingRestartBusy.value) return
  const success = trackingRestartPendingAction.value ? await retryTrackingRestart() : await restartTrackingFromEarlierFrame(target.startFrame,target.pausedFrame)
  if (success) closeRestartDialog()
}
function restartDialogKeys(event: KeyboardEvent) {
  if (event.key !== 'Tab') return
  const nodes = Array.from(restartDialog.value?.querySelectorAll<HTMLElement>('button:not(:disabled)') || [])
  const first=nodes[0],last=nodes[nodes.length-1]
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
}
function finishDeleteDialog() {
  deletionSummarySerial++
  deletionDialogOpen.value = false
  deletionSummaryLoading.value = false
  document.body.style.overflow = deletionPriorOverflow
  if (deletionPriorFocus?.isConnected && !(deletionPriorFocus as HTMLButtonElement).disabled) deletionPriorFocus.focus({preventScroll:true})
  else focusButton.value?.focus({preventScroll:true})
}
function closeDeleteDialog() { if (!objectDeletionBusy.value) deleteDialog.value?.close() }
async function loadDeletionSummary() {
  const target = deletionTarget.value
  if (!target || target.mediaId!==currentMediaId.value) return
  const serial = ++deletionSummarySerial
  deletionSummaryLoading.value = true; deletionSummaryError.value = ''; deletionSummary.value = null
  try {
    const result = await getObjectDeletionSummary(target.objectId)
    if (serial===deletionSummarySerial && deleteDialog.value?.open && target.mediaId===currentMediaId.value) deletionSummary.value = result
  } catch (error) {
    if (serial===deletionSummarySerial) deletionSummaryError.value = error instanceof Error ? error.message : '读取删除范围失败，请重试'
  } finally { if (serial===deletionSummarySerial) deletionSummaryLoading.value = false }
}
async function openVideoDeletion() {
  const pending = objectDeletionPendingAction.value
  if (objectDeletionBusy.value || trackingFeedbackBusy.value || trackingFeedbackPending.value || isAiBusy.value || (editingBlocked.value && !pending)) return
  if (pending && pending.action!=='delete') return
  if (!pending) {
    const object = selectedObject.value
    if (!object || !isVideo.value || !Number.isInteger(object.objectId)) return
    deletionTarget.value = {objectId:object.objectId!,name:object.name,mediaId:currentMediaId.value,mediaName:selectedMedia.value?.name || ''}
  } else if (!deletionTarget.value || deletionTarget.value.objectId!==pending.objectId) {
    deletionTarget.value = {objectId:pending.objectId,name:`对象 ${pending.objectId}`,mediaId:currentMediaId.value,mediaName:selectedMedia.value?.name || ''}
  }
  cancelPointer(); pausePlayback()
  deletionPriorFocus = document.activeElement as HTMLElement
  deletionPriorOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  await nextTick(); deleteDialog.value?.showModal(); deletionDialogOpen.value = true
  if (!pending) await loadDeletionSummary()
}
async function confirmVideoDeletion() {
  const target = deletionTarget.value
  if (!target || objectDeletionBusy.value || target.mediaId!==currentMediaId.value) return
  const pending = objectDeletionPendingAction.value
  if (!pending && (!deletionSummary.value || deletionSummary.value.frameCount===0 || deletionSummaryLoading.value)) return
  const success = pending ? await retryObjectDeletion() : await removeObjectAcrossVideo(target.objectId)
  if (success) { clearSelection(); frameDeletion.value = null; deleteDialog.value?.close() }
}
async function undoVideoDeletion() { if (await undoVideoObjectDeletion()) frameDeletion.value = null }
function deletionDialogKeys(event:KeyboardEvent) {
  if (event.key!=='Tab') return
  const nodes = Array.from(deleteDialog.value?.querySelectorAll<HTMLElement>('button:not(:disabled),[tabindex="0"]') || [])
  const first = nodes[0], last = nodes[nodes.length-1]
  if (event.shiftKey && document.activeElement===first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement===last) { event.preventDefault(); first?.focus() }
}
let observer: ResizeObserver | null = null, removeGuard: (()=>void) | null = null, pointerRaf = 0
let pan: { x:number; y:number; left:number; top:number; id:number } | null = null, suppressClick = false, latestMove: PointerEvent | null = null
let helpPriorFocus: HTMLElement | null = null
const focusButton = ref<HTMLButtonElement | null>(null)
let pointerActive = false
let normalView: { mediaId: string; zoom: number; base: {w:number;h:number}; left: number; top: number; pageY: number } | null = null
let priorOverflow = '', priorHeaderInert = false
let pauseResizeLocked = false, pauseViewSerial = 0
function cancelPendingPauseView() {
  if (!pauseResizeLocked) return
  const serial = ++pauseViewSerial
  // Cancel stale restoration without re-enabling automatic fit in the middle
  // of the same dock resize. Explicit fit/zoom still applies immediately.
  requestAnimationFrame(() => requestAnimationFrame(() => { if (serial === pauseViewSerial) pauseResizeLocked = false }))
}
watch(selectedObjectId, cancelPendingPauseView)
function snapshotPauseView() {
  const scroller = scrollContainerRef.value
  return { mediaId: currentMediaId.value, frame: currentFrame.value, selected: selectedObjectId.value,
    zoom: zoom.value, box: JSON.stringify(selectedObject.value?.bbox), base: { ...base.value }, left: scroller?.scrollLeft || 0, top: scroller?.scrollTop || 0,
    offset: scroller && stageRef.value ? { x:stageRef.value.getBoundingClientRect().left-scroller.getBoundingClientRect().left-scroller.clientLeft, y:stageRef.value.getBoundingClientRect().top-scroller.getBoundingClientRect().top-scroller.clientTop } : null }
}
function anchorPauseStage(view: ReturnType<typeof snapshotPauseView>, keepScroll = false) {
  const scroller=scrollContainerRef.value, stage=stageRef.value
  if (!scroller || !stage || !view.offset) return null
  const css=getComputedStyle(scroller), pad={left:parseFloat(css.paddingLeft)||0,right:parseFloat(css.paddingRight)||0,top:parseFloat(css.paddingTop)||0,bottom:parseFloat(css.paddingBottom)||0}
  // Explicit positive margins replace flex auto-centering. Extra trailing space
  // makes the requested offset reachable through ordinary, nonnegative scroll.
  const marginLeft=Math.max(0,view.offset.x+(keepScroll?view.left:0)-pad.left)
  const marginTop=Math.max(0,view.offset.y+(keepScroll?view.top:0)-pad.top)
  const left=keepScroll?view.left:pad.left+marginLeft-view.offset.x, top=keepScroll?view.top:pad.top+marginTop-view.offset.y
  pauseStageMargins.value={marginLeft,marginTop,
    marginRight:Math.max(0,scroller.clientWidth+left-pad.left-marginLeft-stageSize.value.w-pad.right),
    marginBottom:Math.max(0,scroller.clientHeight+top-pad.top-marginTop-stageSize.value.h-pad.bottom)}
  return {left,top}
}
async function settlePauseView(view: ReturnType<typeof snapshotPauseView>, serial: number) {
  const isCurrent=()=>serial===pauseViewSerial && view.mediaId===currentMediaId.value && view.frame===currentFrame.value && !pointerActive && !pan && zoom.value===view.zoom && selectedObjectId.value===view.selected && JSON.stringify(selectedObject.value?.bbox)===view.box
  try {
    await nextTick()
    if (!isCurrent()) return
    base.value={...view.base}
    const position=anchorPauseStage(view)
    await nextTick()
    if (!isCurrent()) return
    const scroller=scrollContainerRef.value
    if (position) scroller?.scrollTo(position.left,position.top)
    // Only an object that falls outside the narrower dock needs a minimal pan.
    const object=selectedObject.value, stage=stageRef.value
    if (scroller && stage && object?.bbox) {
      const box=object.bbox,r=stage.getBoundingClientRect(),vp=scroller.getBoundingClientRect()
      const reveal=revealBoxScroll({x:r.left+box.x*r.width/100,y:r.top+box.y*r.height/100,width:box.width*r.width/100,height:box.height*r.height/100},
        {x:vp.left,y:vp.top,width:scroller.clientWidth,height:scroller.clientHeight},
        {left:scroller.scrollLeft,top:scroller.scrollTop,maxLeft:scroller.scrollWidth-scroller.clientWidth,maxTop:scroller.scrollHeight-scroller.clientHeight})
      scroller.scrollTo(reveal.left,reveal.top)
    }
  } finally {
    // ResizeObserver must see the completed dock layout before auto-fit resumes.
    await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())))
    if (serial===pauseViewSerial) pauseResizeLocked=false
  }
}
watch(showPauseFeedback,(shown,previous)=>{
  if (!shown) { pauseStageMargins.value=null; return }
  if (!previous) {
    // Restoring a workspace updates zoom before Vue patches the old stage.
    // Only anchor a pause against the matching rendered image; otherwise an
    // old 100% centering margin would be carried into the restored zoom.
    const rendered=stageRef.value?.getBoundingClientRect()
    if (!rendered || Math.abs(rendered.width-stageSize.value.w)>1 || Math.abs(rendered.height-stageSize.value.h)>1) return
    const view=snapshotPauseView(),serial=++pauseViewSerial
    pauseResizeLocked=true
    anchorPauseStage(view,true)
    void settlePauseView(view,serial)
  }
},{flush:'pre'})
watch(currentFrame,cancelPendingPauseView,{flush:'pre'})
watch(currentMediaId,()=>{cancelPendingPauseView();pauseStageMargins.value=null},{flush:'pre'})

async function setPauseDetails(open: boolean) {
  if (pauseDetailsOpen.value === open) return
  const view = snapshotPauseView(), serial = ++pauseViewSerial
  pauseResizeLocked = true
  anchorPauseStage(view,true)
  pauseDetailsOpen.value = open
  await settlePauseView(view, serial)
}

function restorePage() {
  document.body.style.overflow = priorOverflow
  const header = document.querySelector<HTMLElement>('.app-header')
  if (header) header.inert = priorHeaderInert
}
async function toggleFocus() {
  cancelPointer(); spaceHeld.value = false
  const scroller = scrollContainerRef.value
  const pausedView = showPauseFeedback.value ? snapshotPauseView() : null
  const viewSerial = pausedView ? ++pauseViewSerial : 0
  if (pausedView) { pauseResizeLocked = true; anchorPauseStage(pausedView,true) }
  if (!focused.value) {
    normalView = { mediaId: currentMediaId.value, zoom: zoom.value, base: {...base.value}, left: scroller?.scrollLeft || 0, top: scroller?.scrollTop || 0, pageY: window.scrollY }
    priorOverflow = document.body.style.overflow
    const header = document.querySelector<HTMLElement>('.app-header')
    priorHeaderInert = header?.inert || false
    if (header) header.inert = true
    document.body.style.overflow = 'hidden'
    focused.value = true
    if (!pausedView) zoomReset()
    await nextTick()
    size.value = { w: scroller?.clientWidth || 0, h: scroller?.clientHeight || 0 }
    if (!pausedView) { fit(); scroller?.scrollTo(0, 0) }
  } else {
    focused.value = false; restorePage()
    if (pausedView) { /* Keep the paused edit at its current rendered scale. */ }
    else if (normalView?.mediaId === currentMediaId.value) {
      zoom.value = normalView.zoom; base.value = {...normalView.base}
    } else zoomReset()
    await nextTick()
    size.value = { w: scroller?.clientWidth || 0, h: scroller?.clientHeight || 0 }
    if (zoom.value === 1 && !pausedView) fit()
    await nextTick()
    if (!pausedView) scroller?.scrollTo(normalView?.left || 0, normalView?.top || 0)
    window.scrollTo(0, normalView?.pageY || 0)
  }
  if (pausedView) await settlePauseView(pausedView, viewSerial)
  focusButton.value?.focus({preventScroll:true})
}
function fit() {
  pauseStageMargins.value=null
  const ratio = (selectedMedia.value?.width || 1600)/(selectedMedia.value?.height || 900)
  const w = Math.max(1, Math.min(size.value.w-40, (size.value.h-40)*ratio))
  base.value = {w,h:w/ratio}
}
function fitView() { if(pointerActive||pan)return;cancelPendingPauseView(); cancelAnnotationGesture(); zoomReset(); fit(); scrollContainerRef.value?.scrollTo(0,0) }
async function zoomAt(steps:number, point?:{x:number;y:number}) {
  if(pointerActive||pan)return
  cancelPendingPauseView()
  const el = stageRef.value, scroller = scrollContainerRef.value
  if (!el || !scroller) return
  const old = el.getBoundingClientRect(), vp = scroller.getBoundingClientRect()
  const p = point || {x:vp.left+vp.width/2,y:vp.top+vp.height/2}, nx=(p.x-old.left)/old.width, ny=(p.y-old.top)/old.height
  pauseStageMargins.value=null
  // Multiplicative steps: each press scales the view by 1.25×, wheel ticks by ~1.14×.
  const target=Math.min(10,Math.max(.25,+(zoom.value*Math.pow(1.25,steps)).toFixed(2)))
  if(target>zoom.value)zoomIn(target-zoom.value);else if(target<zoom.value)zoomOut(zoom.value-target)
  await nextTick()
  const now=el.getBoundingClientRect();scroller.scrollBy(now.left+nx*now.width-p.x,now.top+ny*now.height-p.y)
}
function wheel(e:WheelEvent) { if(e.ctrlKey||e.metaKey) { e.preventDefault();void zoomAt(e.deltaY<0?.6:-.6,{x:e.clientX,y:e.clientY}) } }
function drawLoupe(e:PointerEvent) {
  const stage=stageRef.value, media=selectedMedia.value
  if(!stage||!media)return
  const rect=stage.getBoundingClientRect(),nx=(e.clientX-rect.left)/rect.width,ny=(e.clientY-rect.top)/rect.height
  if(nx<0||ny<0||nx>1||ny>1){mousePixel.value=null;return}
  const w=media.width||1,h=media.height||1
  mousePixel.value={x:Math.min(w-1,Math.floor(nx*w)),y:Math.min(h-1,Math.floor(ny*h))}
  if(!magnifier.value||!loupe.value)return
  const source=isVideo.value ? (exactFrameUrl.value && !isPlaying.value ? exactFrameImageRef.value : videoRef.value) : imageRef.value
  const ctx=loupe.value.getContext('2d');if(!source||!ctx)return
  ctx.fillStyle='#0d1726';ctx.fillRect(0,0,200,150)
  try {ctx.drawImage(source,nx*w-25,ny*h-18.75,50,37.5,0,0,200,150)} catch { return }
  ctx.strokeStyle='#ffd36d';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(92,75);ctx.lineTo(108,75);ctx.moveTo(100,67);ctx.lineTo(100,83);ctx.stroke()
}
function pointerDown(e:PointerEvent) {
  if((spaceHeld.value||e.button===1)&&!editingBlocked.value){
    e.preventDefault();const sc=scrollContainerRef.value!;pan={x:e.clientX,y:e.clientY,left:sc.scrollLeft,top:sc.scrollTop,id:e.pointerId};annotationHitRef.value?.setPointerCapture(e.pointerId);suppressClick=true;return
  }
  suppressClick=false
  if(hiddenBoxes.value){hiddenBoxes.value=false;suppressClick=true;return}
  if(activeTool.value==='head')return
  pointerActive=true
  onBboxDown(e)
}
function processMove() {
  pointerRaf=0;const e=latestMove;if(!e)return
  if(pan){scrollContainerRef.value?.scrollTo(pan.left-(e.clientX-pan.x),pan.top-(e.clientY-pan.y))}
  else onBboxMove(e)
  drawLoupe(e)
}
function pointerMove(e:PointerEvent) {latestMove=e;if(!pointerRaf)pointerRaf=requestAnimationFrame(processMove)}
function pointerUp(e:PointerEvent) {
  pointerActive=false
  if(pointerRaf)cancelAnimationFrame(pointerRaf);pointerRaf=0
  if(pan){pan=null;annotationHitRef.value?.releasePointerCapture(e.pointerId);return}
  onBboxUp(e)
}
function cancelPointer(){if(pointerRaf)cancelAnimationFrame(pointerRaf);pointerRaf=0;pan=null;pointerActive=false;cancelAnnotationGesture()}
function stageClick(e:MouseEvent){if(suppressClick){suppressClick=false;return}if(!hiddenBoxes.value)onStageClick(e)}
async function jump(){frameInput.value=Math.max(0,Math.min(maxFrameIndex.value,Math.floor(Number(jumpFrame.value)||1)-1));await seekToInputFrame();jumpFrame.value=currentFrame.value+1}
async function retrySave(){
  if(workspaceRecoveryRequired.value){try{await reloadWorkspaceFromServer()}catch(error){saveError.value=(error as Error)?.message||'本机备份未成功，请保持页面打开并导出备份'}return}
  try{await retryWorkspaceSave()}catch{/* The store logs and exposes the failure. */}
}
function enhancement(preset:string){if(preset==='原图'){resetMediaFilter();inverted.value=false}else if(preset==='暗场'){brightness.value=150;contrast.value=125}else{brightness.value=100;contrast.value=160}}
function keys(e:KeyboardEvent){
  if(document.querySelector('[data-user-guide][open], [data-object-delete-dialog][open], [data-reimport-dialog]'))return

  if(e.isComposing||e.keyCode===229||document.querySelector('[data-completion-dialog]'))return
  if(help.value){if(e.key==='Escape'){help.value=false;e.preventDefault()}if(e.key==='Tab'){const nodes=Array.from(helpDialog.value?.querySelectorAll<HTMLElement>('button,input,[tabindex="0"]')||[]);if(nodes.length){e.preventDefault();const i=nodes.indexOf(document.activeElement as HTMLElement);nodes[(i+(e.shiftKey?-1:1)+nodes.length)%nodes.length]?.focus()}}return}
  if((e.target as HTMLElement)?.closest('input,textarea,select,[contenteditable="true"]'))return
  if(reviewing.value&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.repeat){
    const reviewKey=e.key.toLowerCase()
    if(reviewKey==='1'){e.preventDefault();reviewPanel.value?.confirm();return}
    if(reviewKey==='n'){e.preventDefault();reviewPanel.value?.next();return}
  }
  if(!reviewing.value&&e.key==='Enter'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.repeat&&currentCheckFrame.value&&!currentCheckFrame.value.scannedAt&&!(e.target as HTMLElement)?.closest('button,a,summary')){e.preventDefault();void finishScan();return}
  if(e.key==='Escape'){e.preventDefault();if(pointerActive||pan){cancelPointer();return}if(focused.value){void toggleFocus();return}cancelPointer();hiddenBoxes.value=false;clearSelection();return}
  if(e.key.toLowerCase()==='f'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.repeat){e.preventDefault();void toggleFocus();return}
  if(mediaBusy.value||workspaceRestoring.value)return
  if(e.key===' '){if((e.target as HTMLElement)?.closest('button,summary'))return;e.preventDefault();spaceHeld.value=true;return}
  if((e.ctrlKey||e.metaKey)&&!e.altKey){if(e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo()}else if(e.key.toLowerCase()==='y'){e.preventDefault();redo()}return}
  if(e.altKey){if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const step=e.shiftKey?10:1;nudgeSelected(e.key==='ArrowLeft'?-step:e.key==='ArrowRight'?step:0,e.key==='ArrowUp'?-step:e.key==='ArrowDown'?step:0)}return}
  if(e.repeat)return
  const key=e.key.toLowerCase()
  if(key==='arrowleft'||key==='arrowright'){e.preventDefault();void seekByFrame((key==='arrowleft'?-1:1)*(e.shiftKey?10:1))}
  else if(key==='delete'||key==='backspace'){if(selectedObject.value){e.preventDefault();deleteSelectedFrame()}}
  else if(key==='v'||key==='b')selectTool(key==='v'?'select':'smart')
  else if(key==='m')libraryOpen.value=!libraryOpen.value
  else if(key==='h')hiddenBoxes.value=!hiddenBoxes.value
  else if(key==='l')magnifier.value=!magnifier.value
  else if(key==='k'){e.preventDefault();void togglePlayback()}
  else if(key==='g'){e.preventDefault();jumpFrameInput.value?.focus();jumpFrameInput.value?.select()}
  else if(canvasZoomKey(e)==='in'){e.preventDefault();void zoomAt(1)}
  else if(canvasZoomKey(e)==='out'){e.preventDefault();void zoomAt(-1)}
  else if(canvasZoomKey(e)==='fit'){e.preventDefault();fitView()}
  else if(key==='?'){e.preventDefault();help.value=true}
}
function keyup(e:KeyboardEvent){if(e.key===' ')spaceHeld.value=false}
function blur(){spaceHeld.value=false;cancelPointer()}
function beforeUnload(e:BeforeUnloadEvent){if(importRunning.value||mediaImportBusy.value||duplicateImport.value||saveState.value==='saving'||saveState.value==='error'||objectDeletionBusy.value||trackingFeedbackBusy.value||trackingRestartBusy.value||trackingRestartPendingAction.value||objectDeletionPendingAction.value||trackingFeedbackPending.value){e.preventDefault();e.returnValue=''}}
watch(trackingRestartPendingAction,(value,previous)=>{if(!value&&previous&&restartDialogOpen.value)closeRestartDialog()})
watch(help,async value=>{if(value){helpPriorFocus=document.activeElement as HTMLElement;await nextTick();helpDialog.value?.querySelector<HTMLElement>('button')?.focus()}else helpPriorFocus?.focus()})
// Single-class head boxes: the point tool and per-object names are no longer offered.
watch(activeTool,tool=>{if(tool==='point'||tool==='bbox'||tool==='head')selectTool('smart')},{immediate:true})
watch(selectedObjectId,()=>{if(!trackingFeedbackPending.value&&selectedObject.value?.objectId!=null){feedbackTargetId.value=selectedObject.value.objectId;if(!applyingFeedbackSelection)feedbackSelectionRequest.value=null}},{flush:'sync'})
watch(()=>feedbackTarget.value?.objectId,()=>{if(!trackingFeedbackPending.value)learnNormalMotion.value=true})
watch(trackingPausedFrame,()=>{feedbackTargetId.value=null;if(!trackingFeedbackPending.value)learnNormalMotion.value=true})
watch(()=>({mediaId:currentMediaId.value,frame:trackingPausedFrame.value,ids:unresolvedAnomalies.value.map(item=>item.objectId),target:feedbackTarget.value?.objectId}), (value,previous)=>{
  if (value.frame==null || !value.ids.length) { feedbackSelectionRequest.value=null;return }
  const newPause=!previous || value.mediaId!==previous.mediaId || value.frame!==previous.frame || !previous.ids.length
  const acceptedTarget=previous?.target!=null && previous.ids.includes(previous.target) && !value.ids.includes(previous.target)
  if (!newPause && !acceptedTarget) return
  // A user may be checking an unflagged object before confirming an edited
  // target. Keep that deliberate selection instead of advancing over it.
  const selectedId=selectedObject.value?.objectId
  if (!newPause && selectedId!=null && !previous.ids.includes(selectedId)) return
  requestFeedbackSelection(value.target ?? value.ids[0]!)
},{flush:'post',immediate:true})
watch(()=>[feedbackSelectionRequest.value,currentFrame.value,feedbackBlocked.value,trackingFeedbackPending.value,editingBlocked.value,trackingRestartBusy.value,trackingRestartPendingAction.value,help.value,restartDialogOpen.value,deletionDialogOpen.value,currentObjects.value],applyFeedbackSelection,{flush:'post'})
watch(currentFrame,(frame,previous)=>{if(frame!==previous&&frame===trackingPausedFrame.value&&!trackingFeedbackPending.value&&feedbackTarget.value)requestFeedbackSelection(feedbackTarget.value.objectId)})
watch(currentFrame,value=>{jumpFrame.value=value+1;mousePixel.value=null})
watch(selectedMediaId,async id=>{closeRestartDialog();cancelPointer();mousePixel.value=null;fit();await nextTick();await resetAnnotationViewForMedia(id);scrollContainerRef.value?.scrollTo(0,0)})
watch(()=>[selectedMedia.value?.width,selectedMedia.value?.height],fit)
onMounted(()=>{
  restoreImportIntent()
  observer=new ResizeObserver(()=>{const scroller=scrollContainerRef.value;if(scroller)size.value={w:scroller.clientWidth,h:scroller.clientHeight};if(zoom.value===1 && !pauseResizeLocked)fit()})
  if(scrollContainerRef.value)observer.observe(scrollContainerRef.value)
  window.addEventListener('keydown',keys);window.addEventListener('keyup',keyup);window.addEventListener('blur',blur);window.addEventListener('beforeunload',beforeUnload)
  removeGuard=addLeaveGuard(async()=>{if(mediaBusy.value)return false;cancelHeadBox();cancelExtend();cancelPointer();pausePlayback();try{await persistWorkspaceState(currentMediaId.value,true);return true}catch{return false}})
  if(selectedMedia.value)void resetAnnotationViewForMedia(selectedMediaId.value)
  void review.loadSummary()
})
// Unresolved failures can be collapsed to the toolbar chip, never dropped; see docs/WORKFLOW.md.
const {noticeOpen,collapse,expandNotices,collapsedCount}=useNoticeCollapse(computed(()=>({
  save:saveError.value&&!collaborationConflict.value?`save|${saveError.value}`:'',
  tracking:trackingError.value?`tracking|${selectedMediaId.value}|${trackingError.value.frameIndex}|${trackingError.value.phase}|${trackingError.value.message}`:'',
  restart:trackingRestartError.value&&!restartDialogOpen.value?`restart|${trackingRestartError.value}`:'',
  deletion:objectDeletionError.value&&!deletionDialogOpen.value?`deletion|${objectDeletionError.value}`:'',
})))
// A failed head click loses nothing and can simply be clicked again, so its message expires.
let headErrorTimer:ReturnType<typeof setTimeout>|null=null
watch(headBoxError,message=>{if(headErrorTimer)clearTimeout(headErrorTimer);headErrorTimer=message?setTimeout(()=>{headBoxError.value=''},TOAST_DURATION.error):null})
onUnmounted(()=>{if(headErrorTimer)clearTimeout(headErrorTimer)})
onUnmounted(()=>{if(reviewRefresh)clearTimeout(reviewRefresh);cancelHeadBox();cancelTrackingRestartRequest();closeRestartDialog();if(deleteDialog.value?.open){deleteDialog.value.close();document.body.style.overflow=deletionPriorOverflow}if(focused.value)restorePage();observer?.disconnect();cancelPointer();pausePlayback();removeGuard?.();window.removeEventListener('keydown',keys);window.removeEventListener('keyup',keyup);window.removeEventListener('blur',blur);window.removeEventListener('beforeunload',beforeUnload)})
</script>
<template>
  <section class="annotation-page" :class="{'tracking-paused':hasPausedTracking,'review-mode':reviewing}" data-testid="annotation-page">
    <input ref="fileInputRef" type="file" accept="image/*" multiple hidden @change="handleFiles(($event.target as HTMLInputElement).files,'image')" />
    <input ref="videoInputRef" type="file" accept="video/*" multiple hidden @change="pickVideos($event)" />
    <input ref="packageInputRef" type="file" accept=".zip,application/zip" multiple hidden data-testid="package-input" @change="pickPackages($event)" />
    <Teleport to="body"><div v-if="duplicateImport" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" data-reimport-dialog @keydown.esc.prevent="cancelDuplicateImport">
      <section ref="importDialog" role="dialog" aria-modal="true" aria-label="视频已存在" @keydown="importDialogKeys" class="app-dialog w-[520px]">
        <h2 class="mb-4 text-lg font-semibold">该视频已存在，是否覆盖重新标注？</h2>
        <p>「{{ duplicateImport.videoName }}」与本次导入的视频内容相同。</p>
        <p v-if="duplicateImport.canOverwrite" class="my-3">覆盖将舍弃该视频的旧人工标注、AI 追踪、删除规则、校准与撤销记录，从第 1 帧重新标注。此操作在页面上不能撤销，需要时可由维护者从服务器回收目录恢复。已撤回的送审快照留作历史记录。</p>
        <p v-else class="my-3" role="alert">{{ duplicateImport.reason||'当前工作区或送审状态已变化，请关闭后重新导入。' }}</p>
        <p v-if="duplicateImport.error" class="my-3 text-[var(--danger)]" role="alert">{{ duplicateImport.error }}</p>
        <div class="mt-5 flex justify-end gap-3"><button class="btn-secondary" :disabled="reimportSaving||duplicateImport.pending" @click="cancelDuplicateImport">取消导入</button><button v-if="duplicateImport.canOverwrite" class="btn-primary" :disabled="reimportSaving" @click="confirmDuplicateImport">{{ reimportSaving?'正在覆盖…':duplicateImport.pending?'重试覆盖':'覆盖并重新标注' }}</button></div>
      </section>
    </div></Teleport>
    <WorkbenchShell name="annotate" :focused="focused" :focus-keeps-inspector="reviewing || !!collaborationConflict" drawer-label="素材库" :drawer-count="mediaAssets.length" :drawer-forced="!selectedMedia" :drawer-busy="backgroundTracking||importRunning" v-model:drawer-open="libraryOpen" :inspector-forced="showPauseFeedback || !!collaborationConflict" :inspector-badge="currentObjects.length">
      <template #context="{ inspectorVisible }"><span class="annot-context" :class="{'is-tracking':(!inspectorVisible||reviewing)&&(currentTrackingProgress||currentTrackingFinished)}" data-testid="annotation-stage-status" aria-label="标注工作区状态">
        <strong :title="selectedMedia?.name">{{ selectedMedia?.name || (reviewing?'选择视频开始审查':'选择素材开始标注') }}</strong>
        <span v-if="isVideo" class="shell-meta">第 {{ currentFrame+1 }} / {{ maxFrameIndex+1 }} 帧 · {{ annotatedFrameCount }} 帧有框</span>
        <span class="save-indicator" :class="saveState" role="status"><i />{{ saveLabel }}</span>
        <!-- The AI card already shows the full bar; the header carries it only when that card is off screen. -->
        <TrackingProgress v-if="!inspectorVisible || reviewing" variant="compact" :view="currentTrackingProgress" :finished="currentTrackingFinished" />
        <button class="icon-button" title="快捷键 (?)" aria-label="快捷键" @click="help=true"><AppIcon name="keyboard" :size="17" /></button>
        <span v-if="reviewMediaId && review.status.value" class="review-chip" :class="review.status.value" title="这段视频的审查进度，不是当前页面的模式"><small>本视频</small><span data-testid="header-review-status">{{ review.label(reviewMediaId,review.status.value) }}</span></span>
        <button v-if="!reviewing && reviewMediaId && review.status.value!=='passed'" class="btn-secondary" data-testid="go-review" @click="router.push('/review')">去审查</button>
        <button v-if="reviewing" class="quiet-button" @click="router.push('/annotate')">回到标注</button>
        <AnnotationDatasetExport />
        <template v-if="focused && isVideo && !reviewing && checkFrames.length && !scanComplete"><span class="shell-meta">查漏 {{ scannedCount }} / {{ checkFrames.length }}</span><button class="quiet-button" :disabled="nextUnscannedFrame==null" @click="goNextCheck">下一张要看的</button><button class="btn-primary" :disabled="!currentCheckFrame||!!currentCheckFrame.scannedAt||scanBusy||editingBlocked" @click="finishScan">这张看完了</button></template>
        <template v-if="focused"><UserGuide :page="reviewing?'/review':'/annotate'" /></template>
      </span></template>
      <template #drawer><MediaLibrary :busy="libraryBusy" :reviewing="reviewing" @select="id=>{selectedMediaId=id;libraryOpen=false}" /></template>
      <template #canvas>
      <div class="annotation-view-body" :class="{'with-pause-details':focused && showPauseFeedback && pauseDetailsOpen}">
        <div class="annotation-canvas">
          <TransitionGroup name="notice" tag="div" class="workbench-notices" aria-label="未处理提示">
            <AppNotice v-if="importError" key="import" @close="importError=''">{{ importError }}</AppNotice>
            <AppNotice v-if="packageImportReport" key="package" tone="info" data-testid="package-import-report" :closable="false"><span>标注包：导入 {{ packageImportReport.imported.length }} 个视频<template v-if="packageImportReport.imported.length">（未审查，请到审查页检查）</template><template v-for="item in packageImportReport.skipped" :key="item.videoName">；{{ item.videoName }} 未导入：{{ item.reason }}</template></span><template #actions><button class="quiet-button" @click="packageImportReport=null">知道了</button></template></AppNotice>
            <AppNotice v-if="noticeOpen('save')" key="save" data-testid="workspace-save-error" close-label="收起提示（问题未解决）" @close="collapse('save')">{{ saveError }}<template #actions><button class="quiet-button" :disabled="saveState==='saving'||workspaceRestoring||trackingRetryBusy||!!deletingMediaId||objectDeletionBusy||trackingFeedbackBusy" @click="retrySave">{{ saveState==='saving'?'正在重试保存…':saveRecoveryLabel }}</button><button class="quiet-button" @click="downloadWorkspaceBackup">导出本地备份</button><button class="quiet-button" @click="discardLocalDraft">放弃本机修改</button></template></AppNotice>
            <AppNotice v-if="storageWarning" key="storage" tone="warning" data-testid="storage-warning" :closable="false"><span>{{ storageWarning }}</span><template #actions><button class="quiet-button" @click="storageWarning=''">知道了</button></template></AppNotice>
            <AppNotice v-if="noticeOpen('tracking')" key="tracking" data-testid="tracking-error" close-label="收起提示（问题未解决）" @close="collapse('tracking')"><span>第 {{ trackingError.frameIndex+1 }} 帧 · {{ trackingError.phase }}失败：{{ trackingError.message }}</span><template #actions><button class="quiet-button" :disabled="trackingRetryBusy||workspaceRestoring||saveState==='saving'||!!objectDeletionPendingAction||trackingFeedbackPending" @click="retryTracking">{{ trackingRetryBusy?'正在重试…':trackingRetryLabel }}</button></template></AppNotice>
            <AppNotice v-if="noticeOpen('restart')" key="restart" data-testid="tracking-restart-error" close-label="收起提示（问题未解决）" @close="collapse('restart')"><span>{{ trackingRestartError }}</span><template v-if="trackingRestartPendingAction" #actions><button v-if="trackingRestartPendingAction&&trackingRestartPendingAction.phase!=='conflict'" class="quiet-button" data-testid="tracking-restart-retry" :disabled="trackingRestartBusy||workspaceRestoring" @click="retryTrackingRestart">{{ trackingRestartBusy?'正在恢复…':trackingRestartPendingAction.phase==='start'?'继续从原起点追踪':'重试原回退请求' }}</button><button v-else-if="trackingRestartPendingAction?.phase==='conflict'" class="quiet-button" :disabled="trackingRestartBusy||workspaceRestoring" @click="reloadWorkspaceFromServer">备份并重新读取</button></template></AppNotice>
            <AppNotice v-if="trackingRestartStorageWarning" key="restart-storage" tone="warning" @close="trackingRestartStorageWarning=''">{{ trackingRestartStorageWarning }}</AppNotice>
            <AppNotice v-if="mediaDeleteError" key="media-delete" @close="mediaDeleteError=''">{{ mediaDeleteError }}</AppNotice>
            <AppNotice v-if="noticeOpen('deletion')" key="deletion" close-label="收起提示（问题未解决）" @close="collapse('deletion')">{{ objectDeletionError }}<template #actions><button class="quiet-button" :disabled="objectDeletionBusy" @click="objectDeletionPendingAction?.action==='delete'?openVideoDeletion():retryObjectDeletion()">重试本次{{ objectDeletionPendingAction?.action==='undo'?'撤销':objectDeletionPendingAction?.action==='redo'?'重做':'删除' }}</button></template></AppNotice>
          </TransitionGroup>
          <div ref="scrollContainerRef" class="canvas-scroll" @wheel.capture="cancelPendingPauseView" @pointerdown.capture="cancelPendingPauseView" @wheel="wheel">
            <div ref="stageRef" class="annotation-stage" :style="stageStyle">
              <template v-if="selectedMedia"><template v-if="isVideo">
                <video :key="selectedMedia.id" ref="videoRef" :src="selectedMedia.url" class="stage-media" :class="{'hidden-video':videoPlaybackFallback||(exactFrameUrl&&!isPlaying)}" :style="{filter}" preload="metadata" playsinline @loadedmetadata="onVideoLoaded" @timeupdate="onVideoTimeUpdate" @ended="onVideoEnded" @error="onVideoError" />
                <img v-if="exactFrameUrl&&(!isPlaying||videoPlaybackFallback)" ref="exactFrameImageRef" :src="exactFrameUrl" :alt="`${selectedMedia.name} 第 ${currentFrame+1} 帧`" class="stage-media exact-media" :class="{'picture-enter':pictureEnters}" :style="{filter}" />
              </template><img v-else :key="selectedMedia.id" ref="imageRef" :src="selectedMedia.url" :alt="selectedMedia.name" class="stage-media picture-enter" :style="{filter}" @load="onImageLoaded" /></template>
              <div v-else-if="reviewing" class="canvas-empty"><AppIcon name="folder" :size="36" /><h3>选择一段视频开始审查</h3><p>在左侧素材库里选择要审查的视频。</p></div><div v-else class="canvas-empty"><AppIcon name="upload" :size="36" /><h3>把注意力留给画面</h3><p>导入一段视频，开始精细标注。</p><button class="btn-primary" @click="openFilePicker('video')">导入视频</button></div>
              <div ref="annotationHitRef" data-testid="annotation-hit" class="annotation-hit" :class="{panning:spaceHeld||pan,selecting:activeTool==='select',locked:editingBlocked}" @click="stageClick" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="cancelPointer" @lostpointercapture="cancelPointer" @pointerleave="mousePixel=null" />
              <AnnotationOverlay :objects="displayObjects" :selected="selectedObjectId" :width="stageSize.w" :height="stageSize.h" :draft="tempBbox" :labels="labels" :hidden="hiddenBoxes || (!!frameError && !exactFrameUrl)" />
            </div>
          </div>
          <div class="shell-tools" aria-label="标注工具">
            <span :key="mode" class="mode-tag" data-testid="workbench-mode"><AppIcon :name="reviewing?'check':'box'" :size="14" />{{ reviewing?'审查模式':'标注模式' }}</span>
            <i class="shell-tools-sep" />
            <button class="shell-tool" :class="{active:activeTool==='smart'}" :aria-pressed="activeTool==='smart'" :disabled="editingBlocked" title="标注 (B)：点一下精子头自动补框，其他帧一起补；按住拖动手动画框" aria-label="标注" @click="selectTool('smart')"><AppIcon name="box" :size="17" /><span>标注</span></button>
            <button class="shell-tool" :class="{active:activeTool==='select'}" :aria-pressed="activeTool==='select'" :disabled="editingBlocked" title="只选择和移动框 (V)" aria-label="选择" @click="selectTool('select')"><AppIcon name="cursor" :size="17" /></button>
            <i class="shell-tools-sep" />
            <button class="shell-tool" title="撤销 (Ctrl/⌘ Z)" aria-label="撤销本帧操作" :disabled="editingBlocked||!!objectDeletionPendingAction||trackingFeedbackPending||!canUndo" @click="undo"><AppIcon name="undo" :size="17" /></button>
            <button class="shell-tool" title="重做 (Ctrl/⌘ Shift Z)" aria-label="重做本帧操作" :disabled="editingBlocked||!!objectDeletionPendingAction||trackingFeedbackPending||!canRedo" @click="redo"><AppIcon name="redo" :size="17" /></button>
            <button class="shell-tool delete-frame-tool" title="删除本帧选中框 (Delete)" aria-label="删除本帧选中对象" :disabled="editingBlocked||!selectedObject||!!objectDeletionPendingAction||trackingFeedbackPending" @click="deleteSelectedFrame"><AppIcon name="trash" :size="16" /></button>
            <i class="shell-tools-sep" />
            <button class="shell-tool" :class="{active:magnifier}" :aria-pressed="magnifier" title="放大镜 (L)" aria-label="局部放大镜" @click="magnifier=!magnifier"><AppIcon name="zoom" :size="17" /></button>
            <button class="shell-tool" :class="{active:hiddenBoxes}" :aria-pressed="hiddenBoxes" title="隐藏框 (H)" aria-label="显示或隐藏标注" @click="hiddenBoxes=!hiddenBoxes"><AppIcon name="eye" :size="17" /></button>
            <button class="shell-tool" :class="{active:labels}" :aria-pressed="labels" title="显示对象编号" aria-label="显示对象编号" @click="labels=!labels"><AppIcon name="hash" :size="16" /></button>
            <button class="shell-tool" :class="{active:enhancing}" :aria-expanded="enhancing" title="画面增强" aria-label="画面增强" @click="enhancing=!enhancing"><AppIcon name="sun" :size="17" /></button>
            <button class="shell-tool" :class="{active:minimapOn}" :aria-pressed="minimapOn" title="放大后显示小地图（会遮住画面右上角）" aria-label="小地图" @click="minimapOn=!minimapOn"><span>小地图</span></button>
            <button ref="focusButton" class="shell-tool" :class="{active:focused}" :aria-pressed="focused" :title="focused?'退出专注 (F / Esc)':'专注模式 (F)'" :aria-label="focused?'退出专注':'专注'" @click="toggleFocus"><AppIcon name="fit" :size="16" /></button>
            <Transition name="notice-chip"><button v-if="collapsedCount" class="notice-chip" data-testid="notice-chip" title="展开未处理的提示" @click="expandNotices"><AppIcon name="alert" :size="14" /><span>{{ collapsedCount }} 条未处理</span></button></Transition>
          </div>
          <Transition name="toast-corner"><div v-if="headBoxBusy || headBoxError || extendBusy" class="head-proposal-status" :class="{failed:!headBoxBusy&&!extendBusy&&!!headBoxError}" :role="headBoxError&&!extendBusy?'alert':'status'">
            <AppIcon v-if="!headBoxBusy&&!extendBusy" name="alert" :size="15" /><i v-else class="notice-spinner" aria-hidden="true" />
            <span>{{ extendBusy||headBoxBusy?'正在识别点击处的头部…':headBoxError }}</span>
            <button class="quiet-button" @click="extendBusy?cancelExtend():headBoxBusy?cancelHeadBox():headBoxError=''">{{ extendBusy||headBoxBusy?'取消':'关闭' }}</button>
          </div></Transition>
          <Transition name="enhance-glide" :css="motionAllowed"><div v-if="enhancing" class="annot-enhance enhancement-bar"><button v-for="p in ['原图','暗场','低对比']" :key="p" class="quiet-button" @click="enhancement(p)">{{ p }}</button><label>亮度 <input v-model.number="brightness" aria-label="亮度" type="range" min="50" max="200" /></label><label>对比 <input v-model.number="contrast" aria-label="对比度" type="range" min="50" max="220" /></label><label><input v-model="inverted" type="checkbox" />反相</label><span>只影响显示</span></div></Transition>
          <AnnotationMinimap v-if="selectedMedia && minimapOn" :source="minimapSource" :scroller="scrollContainerRef" :stage="stageRef" :media-width="selectedMedia.width||4" :media-height="selectedMedia.height||3" :redraw-key="`${currentMediaId}:${currentFrame}:${exactFrameUrl}:${stageSize.w}`" />
          <div v-if="exactFrameLoading||workspaceRestoring||isAiBusy||currentRemoteTracking" class="canvas-status" data-testid="canvas-status">{{ isAiBusy?(prelabelRunning?'AI 正在预标本视频 · 可以先看其他视频':'AI 正在追踪本视频 · 可切换到其他视频继续标注'):currentRemoteTracking?`${currentRemoteTracking.userName||'其他人'}正在追踪本视频 · 完成后自动刷新，可先标注其他视频`:workspaceRestoring?'正在恢复工作区…':'正在读取帧…' }}</div>
          <div v-if="frameError" class="canvas-error" role="alert">{{ frameError }}<button class="btn-secondary" @click="retryExactFrame">重试读取</button></div>
          <Transition name="toast"><div v-if="toast" :key="toast.id" class="status-toast" :class="`toast-${toast.tone}`" role="status" data-testid="status-toast"><span class="notice-icon"><AppIcon :name="toast.tone==='success'?'check':toast.tone==='info'?'info':'alert'" :size="14" /></span><span>{{ toast.message }}</span><button v-if="toast.tone==='error'" type="button" class="notice-close" aria-label="关闭提示" @click="dismissToast"><AppIcon name="close" :size="13" /></button></div></Transition>
          <div v-if="magnifier" class="loupe"><canvas ref="loupe" width="200" height="150" :style="{filter}" /><span>4× 原图局部 · {{ mousePixel?`${mousePixel.x}, ${mousePixel.y}`:'移动指针查看' }}</span></div>
          <div v-if="!isVideo" class="shell-zoom" role="group" aria-label="画面缩放"><span v-if="mousePixel" class="annot-coords">{{ mousePixel.x }}, {{ mousePixel.y }} px</span><button aria-label="缩小画面" @click="zoomAt(-1)">−</button><button title="适应画面 (0)" aria-label="适应画面" @click="fitView">{{ Math.round(zoom*100) }}%</button><button aria-label="放大画面" @click="zoomAt(1)">＋</button></div>
        </div>
        <aside v-show="focused && showPauseFeedback && pauseDetailsOpen" ref="focusDetailsHost" class="pause-details-dock" aria-label="暂停检查依据" />
      </div>
      <Teleport :to="pauseControlHost || 'body'" :disabled="focused || !pauseControlHost">
      <AnnotationTrackingFeedback v-if="showPauseFeedback" :compact="focused" :details-open="pauseDetailsOpen" :details-target="pauseDetailsTarget" @update:details-open="setPauseDetails" :paused-frame="trackingPausedFrame" :current-frame="currentFrame" :notices="pausedAnomalies" :target="feedbackTarget" :remaining="unresolvedAnomalies.length" v-model:learn="learnNormalMotion" :disabled="feedbackBlocked" :busy="trackingFeedbackBusy" :pending="trackingFeedbackPending" :error="trackingFeedbackError" :can-restart-earlier="canRestartEarlier" :missing-target="missingFeedbackTarget" :can-copy-missing-target="canCopyMissingFeedbackTarget" :previous-target-frame="previousFeedbackTargetFrame" @restart="openRestartDialog" @select="selectFeedbackObject" @return="returnToPausedFrame" @confirm="confirmFeedback" @retry="retryTrackingAnomalyFeedback" @restore-missing="restoreMissingFeedbackTarget" @locate-previous="locatePreviousFeedbackTarget" />
      </Teleport>
      <details v-if="focused && trackingCalibrationSummary.length" class="focus-calibration"><summary>已保存正常确认（{{ trackingCalibrationSummary.length }} 个对象） · 异常检测持续开启</summary><div class="calibration-items"><button v-for="item in trackingCalibrationSummary" :key="item.objectId" class="quiet-button" :disabled="feedbackBlocked||trackingFeedbackPending" @click="resetTrackingCalibration(item.objectId)">对象 {{ item.objectId }}<template v-if="item.overlapCount"> · {{ item.overlapCount }} 个重叠组合已确认</template> 恢复默认判断</button></div></details>
      <div v-if="focused && trackingFeedbackError && trackingFeedbackPendingAction?.decision==='reset'" class="focus-calibration tracking-feedback-error" role="alert"><span>{{ trackingFeedbackError }}</span><button class="quiet-button" :disabled="trackingFeedbackBusy" @click="retryTrackingAnomalyFeedback">重试恢复默认判断</button></div>
      <div v-if="focused && (frameDeletionVisible||videoDeletionVisible)" class="focus-calibration object-deletion-feedback" role="status"><span v-if="frameDeletionVisible">对象 {{ frameDeletion?.objectId }} 已删除本帧标注</span><span v-else-if="lastVideoObjectDeletion">对象 {{ lastVideoObjectDeletion.objectId }} 已删除 {{ lastVideoObjectDeletion.frameCount }} 帧标注</span><button class="quiet-button" :disabled="editingBlocked||!!objectDeletionPendingAction||trackingFeedbackPending||(frameDeletionVisible?!canUndo:!canUndoVideoDeletion)" @click="frameDeletionVisible?undoFrameDeletion():undoVideoDeletion()"><AppIcon name="undo" :size="13" />撤销</button></div>
      </template>
      <template v-if="isVideo" #bottom>
        <button class="icon-button" aria-label="上一帧" title="上一帧 (←)" :disabled="isAiBusy||currentFrame===0" @click="seekByFrame(-1)"><AppIcon name="prev" :size="16" /></button>
        <button class="icon-button shell-play" :aria-label="isPlaying?'暂停':'播放'" :title="isPlaying?'暂停 (K)':'播放 (K)'" :disabled="isAiBusy||exactFrameLoading" @click="togglePlayback"><AppIcon :name="isPlaying?'pause':'play'" :size="15" /></button>
        <button class="icon-button" aria-label="下一帧" title="下一帧 (→)" :disabled="isAiBusy||currentFrame===maxFrameIndex" @click="seekByFrame(1)"><AppIcon name="next" :size="16" /></button>
        <label class="shell-jump"><input ref="jumpFrameInput" v-model.number="jumpFrame" aria-label="跳转帧号" title="跳转帧号 (G)" type="number" min="1" :max="maxFrameIndex+1" :disabled="isAiBusy" @change="jump" @keydown.enter="jump" /> / {{ maxFrameIndex+1 }}</label>
        <WorkbenchFrameStrip :frame-count="maxFrameIndex+1" :current="currentFrame" :disabled="isAiBusy" :priority="['manual','ai','empty']" :kind-of="frameKind" :flags-of="frameFlags" :title-of="frameTitle" :scan="currentTrackingProgress" @seek="seekFrame" />
        <span class="annot-time">{{ formatTime(currentTime) }}</span>
        <div class="shell-zoom in-bottom" role="group" aria-label="画面缩放"><span v-if="mousePixel" class="annot-coords">{{ mousePixel.x }}, {{ mousePixel.y }} px</span><button aria-label="缩小画面" @click="zoomAt(-1)">−</button><button title="适应画面 (0)" aria-label="适应画面" @click="fitView">{{ Math.round(zoom*100) }}%</button><button aria-label="放大画面" @click="zoomAt(1)">＋</button></div>
      </template>
      <template #inspector><div class="annot-inspector" :class="{'pause-active':showPauseFeedback}" aria-label="检查面板">
      <section v-if="collaborationConflict" class="shell-card collaboration-card" role="region" aria-label="处理同时修改的标注" data-testid="workspace-merge">
        <div class="shell-card-head"><strong>{{ collaborationConflict.entries.length }} 个框你和{{ otherEditor }}同时改了</strong></div>
        <p class="shell-card-note">其他修改已自动合并。逐个选择保留哪一份。</p>
        <div class="collaboration-entries">
          <div v-for="entry in collaborationConflict.entries" :key="entry.key" class="collaboration-entry">
            <div class="collaboration-entry-head"><strong>{{ entry.label }}</strong><button v-if="entry.frameIndex!==undefined" class="quiet-button" :disabled="currentFrame===entry.frameIndex" @click="locateConflict(entry)">{{ currentFrame===entry.frameIndex?'当前帧':'定位' }}</button></div>
            <ReviewZoomPreview v-if="currentFrame===entry.frameIndex" comparing :source="minimapSource" :box="pixelBox(entryObject(entry,'local'))" :compare="pixelBox(entryObject(entry,'remote'))" :size="140" :media-width="selectedMedia?.width||0" :media-height="selectedMedia?.height||0" :filter="filter" :frame-key="`${entry.key}:${currentFrame}`" />
            <div class="collaboration-choice">
              <button class="btn-secondary" :disabled="collaborationBusy" :aria-pressed="mergeChoices[entry.key]?.value==='local'" @click="chooseMerge(entry.key,'local',entry.remote)">保留我的<small>{{ mergeSummary(entry,'local') }}</small></button>
              <button class="btn-secondary" :disabled="collaborationBusy" :aria-pressed="mergeChoices[entry.key]?.value==='remote'" @click="chooseMerge(entry.key,'remote',entry.remote)">用{{ otherEditor }}的<small>{{ mergeSummary(entry,'remote') }}</small></button>
            </div>
          </div>
        </div>
        <p v-if="saveError" class="review-warning" role="alert">{{ saveError }}</p>
        <button class="btn-primary" :disabled="!mergeReady||collaborationBusy" @click="resolveCollaboration(mergeChoices)">{{ collaborationBusy?'正在保存…':'应用选择并继续' }}</button>
        <div class="collaboration-more"><button class="quiet-button" @click="downloadWorkspaceBackup">导出本地备份</button><button class="quiet-button" @click="discardLocalDraft">全部用服务器版本</button></div>
      </section>
      <AnnotationReviewPanel v-if="reviewing" ref="reviewPanel" :media-id="reviewMediaId" :last-editor="lastEditors[currentMediaId]||null" :next-video="nextReviewVideo?.name||null" @next-video="nextReviewVideo&&(selectedMediaId=nextReviewVideo.id)" :current-frame="currentFrame" :frame-count="maxFrameIndex+1" :object-count="currentObjects.length" :frame-ready="reviewFrameReady" :blocked="editingBlocked" :tracking-pending="hasPausedTracking" :flush="flushWorkspace" @seek="seekReviewFrame" />
      <section v-show="showPauseFeedback" class="pause-inspector-panel" aria-label="暂停核对">
        <div ref="pauseControlHost" class="pause-control-host" />
        <div v-show="pauseDetailsOpen" ref="inspectorDetailsHost" class="pause-inspector-details" />
      </section>
      <section v-show="!reviewing && (!showPauseFeedback || trackingCalibrationSummary.length || (trackingFeedbackError && trackingFeedbackPendingAction?.decision==='reset'))" class="shell-card annot-task" data-testid="prelabel-card">
        <template v-if="!showPauseFeedback">
          <Transition name="task-swap" mode="out-in" :css="motionAllowed">
          <div v-if="currentTrackingProgress || currentTrackingFinished" key="running" class="task-body">
            <div class="shell-card-head"><AppIcon name="spark" :size="16"/><strong>{{ prelabelRunning || currentTrackingFinished ? '自动预标' : 'AI 追踪' }}</strong></div>
            <TrackingProgress class="annot-tracking-progress" :view="currentTrackingProgress" :finished="currentTrackingFinished" />
            <p v-if="currentTrackingProgress && currentTrackingProgress.mode !== 'finishing'" class="shell-card-note">在服务器上识别，可以先看其他视频。</p>
          </div>
          <div v-else-if="prelabelError" key="failed" class="task-body" role="alert">
            <div class="shell-card-head"><AppIcon name="alert" :size="16"/><strong>自动预标没完成</strong></div>
            <p class="shell-card-note">{{ prelabelError }}</p>
            <button class="btn-primary shell-wide" :disabled="isAiBusy" @click="retryPrelabel">重试</button>
          </div>
          <div v-else-if="checkFrames.length" key="scan" class="task-body" data-testid="scan-task">
            <div class="task-eyebrow"><span>第 1 步 · 标注</span><span class="num">{{ scannedCount }} / {{ checkFrames.length }}</span></div>
            <h3 class="task-title">{{ scanComplete ? '都看完了' : '找漏掉的精子' }}</h3>
            <p class="shell-card-note">{{ scanComplete ? '接下来去审查，逐个确认找到的目标。' : '看这一张，没框到的头点一下就补上。' }}</p>
            <div class="check-steps" role="group" aria-label="要查漏的帧">
              <button v-for="(item,i) in checkFrames" :key="item.frame" class="check-step" :class="{done:!!item.scannedAt,current:item.frame===currentFrame}" :aria-current="item.frame===currentFrame?'step':undefined" :title="`第 ${item.frame+1} 帧${item.scannedAt?` · ${item.scannedByName||''}已看`:''}`" :aria-label="`第 ${i+1} 张，第 ${item.frame+1} 帧${item.scannedAt?'，已看':''}`" :disabled="isAiBusy" @click="seekFrame(item.frame)"><AppIcon v-if="item.scannedAt" name="check" :size="12" /><template v-else>{{ i+1 }}</template></button>
            </div>
            <button v-if="!scanComplete && currentCheckFrame && !currentCheckFrame.scannedAt" class="btn-primary shell-wide task-primary" data-testid="scan-done" :disabled="scanBusy||editingBlocked" @click="finishScan">这张看完了<kbd>Enter</kbd></button>
            <button v-else-if="!scanComplete" class="btn-primary shell-wide task-primary" :disabled="isAiBusy" @click="goNextCheck">回到要看的那张</button>
            <button v-else class="btn-primary shell-wide task-primary" @click="router.push('/review')">去审查<AppIcon name="next" :size="14" /></button>
            <div v-if="!scanComplete" class="task-how" aria-hidden="true"><span><AppIcon name="cursor" :size="15" />点一下 补框</span><span><AppIcon name="box" :size="15" />拖动 画框</span><span><kbd>Del</kbd>删除</span></div>
          </div>
          <div v-else-if="legacyResult" key="legacy" class="task-body">
            <div class="shell-card-head"><AppIcon name="spark" :size="16"/><strong>旧流程标注的视频</strong></div>
            <p class="shell-card-note">可以继续手动修改；也可以清空现有的框，用新模型重新预标。</p>
            <button v-if="!reprelabelConfirm" class="btn-secondary shell-wide" :disabled="isAiBusy||editingBlocked||reprelabelBusy" @click="reprelabelConfirm=true">用新模型重新预标…</button>
            <div v-else class="task-confirm" role="alert"><p>会清空这段视频现有的全部框，审查也要重新做。确定吗？</p><div><button class="btn-secondary" @click="reprelabelConfirm=false">取消</button><button class="btn-primary" :disabled="reprelabelBusy" @click="reprelabelConfirm=false;reprelabelCurrent()">{{ reprelabelBusy?'正在清空…':'清空并重新预标' }}</button></div></div>
          </div>
          <div v-else-if="isVideo" key="idle" class="task-body">
            <div class="shell-card-head"><AppIcon name="spark" :size="16"/><strong>自动预标</strong></div>
            <p class="shell-card-note">{{ workspaceRestoring ? '正在读取这段视频…' : '打开视频后自动识别精子头。' }}</p>
          </div>
          </Transition>
        </template>
        <details v-if="trackingCalibrationSummary.length" class="tracking-calibration"><summary>已保存的正常确认（{{ trackingCalibrationSummary.length }} 个对象）</summary><div v-for="item in trackingCalibrationSummary" :key="item.objectId" :data-calibration-object-id="item.objectId"><span>对象 {{ item.objectId }}<template v-if="item.sampleCount"> · {{ item.sampleCount }} 次正常运动确认</template><template v-if="item.geometryFrame!=null"> · 第 {{ item.geometryFrame+1 }} 帧尺寸已确认</template><template v-if="item.overlapCount"> · {{ item.overlapCount }} 个重叠组合已确认（{{ item.overlapPartnerIds.map(id=>'对象 '+id).join('、') }}）</template></span><button class="quiet-button" :disabled="feedbackBlocked||trackingFeedbackPending" @click="resetTrackingCalibration(item.objectId)">恢复默认判断</button></div></details>
        <div v-if="trackingFeedbackError && trackingFeedbackPendingAction?.decision==='reset'" class="tracking-feedback-error" role="alert">{{ trackingFeedbackError }}<button class="quiet-button" :disabled="trackingFeedbackBusy" @click="retryTrackingAnomalyFeedback">重试恢复默认判断</button></div>
      </section>
      <section class="shell-card annot-objects" aria-label="当前帧对象">
        <div class="shell-card-head"><strong>本帧</strong><span class="annot-count" data-testid="frame-object-count">{{ currentObjects.length }} 个框</span><span class="annot-split"><i class="manual" />人工 {{ frameManualCount }}<i class="ai" />AI {{ frameAiCount }}</span></div>
        <div class="object-selection" data-testid="selected-object-actions">
          <template v-if="selectedObject">
            <div class="selection-context"><strong>对象 {{ selectedObject.objectId }}</strong><span>{{ selectedObject.source==='manual'?'人工':'AI 追踪' }}{{ selectedObject.confidence?` · ${Math.round(selectedObject.confidence*100)}%`:'' }}<template v-if="isVideo"> · {{ selectedObjectCoverage }}/{{ maxFrameIndex+1 }} 帧</template><template v-if="selectedObject.bbox"> · {{ (selectedObject.bbox.width*(selectedMedia?.width||0)/100).toFixed(0) }}×{{ (selectedObject.bbox.height*(selectedMedia?.height||0)/100).toFixed(0) }} px</template></span></div>
            <div class="object-delete-actions"><button class="btn-secondary" data-testid="delete-object-frame" title="只删当前帧 (Delete)" :disabled="editingBlocked||trackingFeedbackPending||!!objectDeletionPendingAction" @click="deleteSelectedFrame"><AppIcon name="trash" :size="13" />删除本帧的框</button><button class="btn-secondary delete-video-button" data-testid="delete-object-video" :disabled="editingBlocked||!isVideo||!selectedMedia?.serverMediaId||trackingFeedbackPending||!!objectDeletionPendingAction" title="删除这个对象在整段视频所有帧中的框，可撤销" @click="openVideoDeletion"><AppIcon name="trash" :size="13" />删除全视频中此对象</button></div>
          </template>
          <p v-else class="object-no-selection">{{ activeTool==='select'?'点击画面中的框，或下方编号选中对象。':'点一下精子头补框（其他帧一起补），按住拖动手动画框；点框可选中、拖动。' }}</p>
          <div v-if="frameDeletionVisible" class="object-deletion-feedback" role="status"><AppIcon name="check" :size="14"/><span>对象 {{ frameDeletion?.objectId }} 已删除本帧标注</span><button class="quiet-button" :disabled="editingBlocked||!canUndo||!!objectDeletionPendingAction||trackingFeedbackPending" @click="undoFrameDeletion"><AppIcon name="undo" :size="13" />撤销</button></div>
          <div v-else-if="videoDeletionVisible && lastVideoObjectDeletion" class="object-deletion-feedback" role="status"><AppIcon name="check" :size="14"/><span>对象 {{ lastVideoObjectDeletion.objectId }} 已删除 {{ lastVideoObjectDeletion.frameCount }} 帧标注</span><button class="quiet-button" :disabled="editingBlocked||!canUndoVideoDeletion||!!objectDeletionPendingAction||trackingFeedbackPending" @click="undoVideoDeletion"><AppIcon name="undo" :size="13" />撤销</button></div>
        </div>
        <div class="annot-chips" role="group" aria-label="本帧对象编号">
          <button v-for="obj in currentObjects" :key="obj.id" class="annot-chip object-select" :class="[obj.source==='manual'?'manual':'ai',{selected:selectedObjectId===obj.id}]" :aria-pressed="selectedObjectId===obj.id" :aria-label="`选择对象 ${obj.objectId} ${obj.source==='manual'?'人工':'AI'}`" :title="`对象 ${obj.objectId} · ${obj.source==='manual'?'人工':'AI'}`" :disabled="editingBlocked||trackingFeedbackPending||!!objectDeletionPendingAction" @click="selectObject(obj.id)">{{ obj.objectId }}</button>
          <p v-if="!currentObjects.length" class="sidebar-empty">本帧还没有框</p>
        </div>
      </section>
      </div></template>
    </WorkbenchShell>
    <Teleport to="body">
      <dialog ref="restartDialog" class="app-dialog tracking-restart-dialog" aria-labelledby="tracking-restart-title" @cancel.prevent="closeRestartDialog" @close="finishRestartDialog" @keydown.stop="restartDialogKeys">
        <header><h2 id="tracking-restart-title">从第 {{ (restartTarget?.startFrame??0)+1 }} 帧重新追踪？</h2><button class="icon-button" aria-label="关闭重新追踪确认" @click="closeRestartDialog"><AppIcon name="close" /></button></header>
        <p>使用第 {{ (restartTarget?.startFrame??0)+1 }} 帧当前已修正的框作为起点，重新生成后续 AI 追踪。</p>
        <ul><li>替换此帧之后的旧 AI 结果和第 {{ (restartTarget?.pausedFrame??0)+1 }} 帧的待确认暂停。</li><li>保留所有人工标注、人工基准及历史反馈。</li><li>这次操作不视为“无异常”，不添加正常样本；新分支仍执行异常检测。</li></ul>
        <div v-if="trackingRestartError&&restartDialogOpen" class="error-banner" role="alert" data-testid="tracking-restart-error"><span>{{ trackingRestartError }}</span><button v-if="trackingRestartPendingAction&&trackingRestartPendingAction.phase!=='conflict'" class="quiet-button" data-testid="tracking-restart-retry" :disabled="trackingRestartBusy" @click="confirmRestart">{{ trackingRestartPendingAction.phase==='start'?'继续从原起点追踪':'重试原回退请求' }}</button><button v-else-if="trackingRestartPendingAction?.phase==='conflict'" class="quiet-button" @click="closeRestartDialog">关闭后重新读取工作区</button></div>
        <p v-if="trackingRestartBusy" role="status">正在保存修正并确认分支，请稍候…</p>
        <div class="app-dialog-actions"><button class="btn-secondary" data-testid="tracking-restart-cancel" @click="closeRestartDialog">{{ trackingRestartPendingAction||trackingRestartBusy?'关闭':'取消' }}</button><button v-if="!trackingRestartPendingAction" class="btn-primary" data-testid="tracking-restart-confirm" :disabled="trackingRestartBusy" @click="confirmRestart">确认修正并重新追踪</button></div>
      </dialog>
      <dialog ref="deleteDialog" class="app-dialog object-delete-dialog" data-object-delete-dialog aria-labelledby="object-delete-title" aria-describedby="object-delete-description" @cancel.prevent="closeDeleteDialog" @close="finishDeleteDialog" @keydown.stop="deletionDialogKeys">
        <header class="object-delete-heading"><div><h2 id="object-delete-title">删除整段视频中的此对象？</h2><p id="object-delete-description">只删除「{{ deletionTarget?.mediaName }}」中对象 {{ deletionTarget?.objectId }} 的标注。</p></div><button class="icon-button" aria-label="关闭删除确认" :disabled="objectDeletionBusy" @click="closeDeleteDialog"><AppIcon name="close" /></button></header>
        <div v-if="deletionSummaryLoading" class="object-delete-loading" role="status">正在核对全视频的实际删除范围…</div>
        <div v-else-if="deletionSummaryError" class="error-banner" role="alert">{{ deletionSummaryError }} <button class="quiet-button" @click="loadDeletionSummary">重试读取范围</button></div>
        <div v-else-if="deletionSummary" class="object-delete-scope"><div><strong>{{ deletionSummary.name || deletionTarget?.name }} · 对象 {{ deletionSummary.objectId }}</strong><span v-if="deletionSummary.firstFrame!=null&&deletionSummary.lastFrame!=null">第 {{ deletionSummary.firstFrame+1 }}–{{ deletionSummary.lastFrame+1 }} 帧范围</span></div><dl><div><dt>涉及帧数</dt><dd>{{ deletionSummary.frameCount }}</dd></div><div><dt>人工标注</dt><dd>{{ deletionSummary.manualCount }}</dd></div><div><dt>AI 标注</dt><dd>{{ deletionSummary.aiCount }}</dd></div></dl></div>
        <p v-else-if="objectDeletionPendingAction" class="object-delete-loading">原删除请求仍待确认，请用原请求重试。</p>
        <ul class="object-delete-boundaries"><li>移除过去、当前、未来帧中此对象已有的人工框与 AI 标注。</li><li>此对象的续追种子一并排除；刷新、重新读取和续追不会自动恢复。</li><li>其他对象、原视频及已固定的 A/B/F 快照保留。</li></ul>
        <p class="object-delete-undo-note">误删可撤销整次操作，恢复原框与原对象 ID。最近一次全视频删除可在当前标签页刷新后继续撤销；关闭标签页或启动新追踪后不保证恢复。</p>
        <div v-if="objectDeletionError" class="error-banner" role="alert">{{ objectDeletionError }}<p v-if="objectDeletionPendingAction">删除结果尚未确认；重试沿用本次请求。</p></div>
        <div class="app-dialog-actions"><button class="btn-secondary" :disabled="objectDeletionBusy" @click="closeDeleteDialog">取消</button><button class="btn-primary confirm-delete-video" data-testid="confirm-delete-object-video" :disabled="objectDeletionBusy||(!objectDeletionPendingAction&&(deletionSummaryLoading||!deletionSummary||deletionSummary.frameCount===0))" @click="confirmVideoDeletion">{{ objectDeletionBusy?'正在确认删除…':objectDeletionPendingAction?`重试删除对象 ${deletionTarget?.objectId}`:`删除全部 ${deletionSummary?.frameCount || 0} 帧标注` }}</button></div>
      </dialog>
      <div v-if="help" class="dialog-mask" @click.self="help=false"><section ref="helpDialog" class="app-dialog annotation-help" role="dialog" aria-modal="true" aria-labelledby="annotation-help-title"><h2 id="annotation-help-title">标注快捷键</h2><dl><div v-for="[key,desc] in annotationShortcuts" :key="key"><dt><kbd>{{ key }}</kbd></dt><dd>{{ desc }}</dd></div></dl><p class="muted">{{ shortcutHelpNote }}</p><div class="app-dialog-actions"><button class="btn-primary" @click="help=false">知道了</button></div></section></div>
    </Teleport>
  </section>
</template>

<style scoped>
.annot-task { display:grid; gap:10px; }
.task-body { display:grid; gap:10px; }
.task-eyebrow { display:flex; justify-content:space-between; padding-right:30px; font-size:var(--fs-xs); font-weight:600; color:var(--accent); } /* Clear of the inspector collapse button. */
.task-eyebrow .num { font-variant-numeric:tabular-nums; color:var(--muted); font-weight:500; }
.task-title { margin:0; font-size:var(--fs-xl); line-height:1.25; letter-spacing:-.01em; }
.check-steps { display:flex; flex-wrap:wrap; gap:5px; }
.check-step { min-width:28px; height:28px; padding:0 6px; border-radius:var(--radius-sm); border:1.5px solid var(--accent); background:var(--surface); color:var(--accent); font-size:var(--fs-xs); font-weight:650; font-variant-numeric:tabular-nums; display:inline-grid; place-items:center; cursor:pointer; transition:background-color var(--dur-fast,.12s) var(--ease-out,ease), color var(--dur-fast,.12s) var(--ease-out,ease), box-shadow var(--dur-fast,.12s) var(--ease-out,ease); }
.check-step:hover:not(:disabled) { background:var(--accent-soft); }
.check-step.done { background:var(--accent); color:var(--on-accent); }
.check-step.current { box-shadow:0 0 0 2px var(--surface), 0 0 0 4px var(--accent); }
.task-primary { display:flex; align-items:center; justify-content:center; gap:8px; min-height:40px; font-size:var(--fs-md); }
.task-primary kbd { font-size:var(--fs-2xs); opacity:.75; border:1px solid currentColor; border-radius:4px; padding:0 4px; }
.task-how { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }
.task-how span { display:flex; flex-direction:column; align-items:center; gap:3px; padding:7px 4px; border-radius:var(--radius-sm); background:var(--surface-subtle); color:var(--muted); font-size:var(--fs-2xs); }
.task-how kbd { font-size:var(--fs-2xs); }
.task-confirm { display:grid; gap:8px; padding:10px; border-radius:var(--radius-sm); background:var(--danger-soft); font-size:var(--fs-xs); }
.task-confirm p { margin:0; }
.task-confirm div { display:flex; gap:6px; justify-content:flex-end; }
.task-swap-enter-active, .task-swap-leave-active { transition:opacity var(--dur-base,.2s) var(--ease-out,ease), transform var(--dur-base,.2s) var(--ease-out,ease); }
.task-swap-enter-from { opacity:0; transform:translateY(6px); }
.task-swap-leave-to { opacity:0; transform:translateY(-4px); }
.collaboration-card { border:1px solid var(--warning,#b87b21); padding:12px; display:grid; gap:10px; }
.collaboration-entries { max-height:260px; overflow:auto; display:grid; gap:12px; }
.collaboration-entry { display:grid; gap:6px; }
.collaboration-entry-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.collaboration-choice { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
.collaboration-choice button { display:flex; flex-direction:column; align-items:flex-start; gap:2px; white-space:normal; text-align:left; font-size:12px; }
.collaboration-choice small { color:var(--muted); font-size:11px; }
.collaboration-choice button[aria-pressed="true"] { outline:2px solid var(--accent,#18777b); outline-offset:-2px; }
.collaboration-more { display:flex; gap:8px; flex-wrap:wrap; }
</style>
