// 素材管理：服务器素材列表、导入（重复视频确认、标注包）、文件选择与删除素材。
import { createRequestId } from '../../utils/browserCompat'
import { computed, nextTick, ref } from 'vue'
import type { MediaAsset } from '../../types/annotation'
import { trackApi, type TrackUploadResponse } from '../../api/trackApi'
import { useAuth } from '../auth'
import { annotationReviewApi } from '../../api/annotationReviewApi'
import { annotationsByMedia, api, closedMediaFrontendIds, currentMediaId, deletedFrameObjects, deletedObjectIds, deletedTrackingIds, deletingMediaId, duplicateImport, fileInputRef, importError, isTrackingMedia, isVideoFile, mediaAssets, mediaDeleteError, mediaImportBusy, normalMotionSamples, objectDeletionBusy, packageImportReport, packageInputRef, pendingDeletion, pendingFeedback, pendingRestart, persistClosedMediaFrontendIds, saveQueues, saveTickets, segmentBusy, selectedMedia, selectedMediaId, selectedObjectId, showToast, statusMessage, submissionBusy, trackingErrors, trackingFeedbackBusy, trackingFeedbackEvents, trackingFramesByMedia, trackingJobs, trackingRestartBusy, videoDeletionHistory, videoInputRef, workspaceRestoring, workspaceViews, workspaceWrites } from './state'
import { histories } from './history'
import { cancelAnnotationGesture } from './editing'
import { frameCache, pausePlayback } from './frames'
import { cancelScheduledSave, controlsCacheKey, persistLocalAnnotations, persistWorkspaceState, scheduleWorkspaceStateSave, workspaceSaveTimers } from './save'
import { resetAnnotationViewForMedia } from './restore'
import { rememberTracking } from './tracking'
import { loadedRemoteResultKeys, savedResults } from './results'

let finishDuplicate: ((result:TrackUploadResponse|null)=>void)|null=null

export const deleteMedia = async (mediaId: string) => {
  const media = mediaAssets.value.find(item => item.id === mediaId)
  if (!media || segmentBusy.value || isTrackingMedia(mediaId) || deletingMediaId.value || workspaceRestoring.value || objectDeletionBusy.value || trackingFeedbackBusy.value || pendingDeletion.value || pendingFeedback.value) return
  mediaDeleteError.value = ''
  const message = media.serverMediaId
    ? `将从共享素材库删除「${media.name}」的原视频、工作区和追踪文件，其他使用者也将无法访问。此操作在页面上不能撤销，需要时可由维护者从服务器回收目录恢复；已有历史审查记录的视频不能删除。是否继续？`
    : `将删除「${media.name}」在本机工作区中的素材和标注，不能撤销。电脑原文件和已保存到服务器的标注记录会保留。是否继续？`
  if (!window.confirm(message)) return
  deletingMediaId.value = mediaId
  pausePlayback(); cancelAnnotationGesture()
  const pendingTimer = workspaceSaveTimers.get(mediaId)
  if (pendingTimer) clearTimeout(pendingTimer)
  workspaceSaveTimers.delete(mediaId)
  try {
    // Drain writes already sent before DELETE. New saves are blocked until
    // it finishes, so a delayed autosave cannot recreate the removed folder.
    await saveQueues.get(mediaId)?.catch(() => {})
    if (media.serverMediaId) {
      const result = await trackApi.deleteMedia(media.serverMediaId)
      if (!result.deleted || result.mediaId !== media.serverMediaId) throw new Error('服务器未确认删除，请刷新素材列表核对')
    }
    mediaAssets.value = mediaAssets.value.filter(item => item.id !== mediaId)
    const remainingAnnotations = {...annotationsByMedia.value}
    delete remainingAnnotations[mediaId]
    annotationsByMedia.value = remainingAnnotations
    delete trackingFramesByMedia.value[mediaId]
    delete deletedTrackingIds.value[mediaId]
    if (media.serverMediaId) {
      delete closedMediaFrontendIds[media.serverMediaId]
      persistClosedMediaFrontendIds()
    }
    workspaceViews.delete(mediaId); saveTickets.delete(mediaId)
    if (media.url.startsWith('blob:')) URL.revokeObjectURL(media.url)
    frameCache.clear()
    if (selectedMediaId.value === mediaId) selectedMediaId.value = mediaAssets.value[0]?.id || ''
    // Persist immediately as well as the normal debounced save, allowing an
    // immediate refresh after successful deletion.
    try {
      localStorage.setItem('mediaAssets', JSON.stringify(mediaAssets.value))
      await persistLocalAnnotations(new Set())
    } catch (error) { console.warn('[annotation.local_save_failed]', error) }
    console.info('[annotation.media_deleted]', { mediaId: media.serverMediaId || media.id, scope: media.serverMediaId ? 'server' : 'local' })
    statusMessage.value = `已删除素材：${media.name}`
    showToast(statusMessage.value)
  } catch (error) {
    console.error('[annotation.media_delete_failed]', { mediaId: media.serverMediaId || media.id, scope: media.serverMediaId ? 'server' : 'local', error })
    mediaDeleteError.value = error instanceof Error ? error.message : '删除失败，请重试'
    statusMessage.value = mediaDeleteError.value
  } finally {
    deletingMediaId.value = null
    // A refused/failed delete must not cancel unsaved annotation work.
    if (mediaDeleteError.value && pendingTimer) scheduleWorkspaceStateSave(mediaId)
  }
}

export const openFilePicker = (type: 'image' | 'video') => {
  if (type === 'image') fileInputRef.value?.click()
  else videoInputRef.value?.click()
}

export const clearResetCache = (serverId:string, revision:number) => {
  const media=mediaAssets.value.find(m=>m.serverMediaId===serverId)
  if(!media)return
  const mid=media.id
  trackingJobs.delete(mid);rememberTracking(mid);delete trackingErrors.value[mid]
  cancelScheduledSave(mid)
  annotationsByMedia.value={...annotationsByMedia.value,[mid]:[]};trackingFramesByMedia.value[mid]=[]
  deletedTrackingIds.value[mid]=new Set();deletedObjectIds.value[mid]=[];deletedFrameObjects.value[mid]=[]
  normalMotionSamples.value[mid]=[];trackingFeedbackEvents.value[mid]=[]
  savedResults.value=savedResults.value.filter(item=>item.mediaId!==mid&&item.mediaId!==serverId)
  loadedRemoteResultKeys.clear()
  delete videoDeletionHistory.value[mid]
  workspaceViews.delete(mid);frameCache.clear()
  for(const key of histories.keys())if(key.startsWith(`${mid}:`))histories.delete(key)
  workspaceWrites.setRevision(serverId,revision)
  try{sessionStorage.removeItem(controlsCacheKey(serverId))}catch{}
  selectedObjectId.value=null
}
const importJournalKey=()=>`annotation-reimport:${useAuth().user.value?.id}`
export const cancelDuplicateImport=()=>{
  if(duplicateImport.value?.pending)return
  duplicateImport.value=null;finishDuplicate?.(null);finishDuplicate=null
}
export const reimportSaving=ref(false)
export const confirmDuplicateImport=async()=>{
  const intent=duplicateImport.value
  if(!intent||!intent.canOverwrite||reimportSaving.value)return
  reimportSaving.value=true
  intent.error='';intent.pending=true
  let dispatched=false
  try{
    sessionStorage.setItem(importJournalKey(),JSON.stringify(intent))
    dispatched=true
    const result=await annotationReviewApi.resetAnnotations(intent.mediaId,intent.workspaceRevision,intent.key)
    if(!result.ok||result.mediaId!==intent.mediaId)throw new Error('服务器未确认覆盖')
    clearResetCache(intent.mediaId,result.revision)
    sessionStorage.removeItem(importJournalKey())
    duplicateImport.value=null
    const resolve=finishDuplicate;finishDuplicate=null
    if(resolve)resolve(intent.media)
    else{await loadServerMedia();const media=mediaAssets.value.find(m=>m.serverMediaId===intent.mediaId);if(media){selectedMediaId.value=media.id;await nextTick();await resetAnnotationViewForMedia(media.id)}}
    showToast('旧工作区标注已清空，可以从第 1 帧重新标注')
  }catch(e:any){
    intent.error=e.message||'覆盖结果尚未确认，请重试原请求';console.error('[annotation.reimport_failed]',e)
    if(e.status&&e.status<500){intent.pending=false;intent.canOverwrite=false;sessionStorage.removeItem(importJournalKey())}
    if(!dispatched)intent.pending=false
  }finally{reimportSaving.value=false}
}
export const restoreImportIntent=()=>{
  try{const intent=JSON.parse(sessionStorage.getItem(importJournalKey())||'null');if(intent?.pending&&intent.mediaId)duplicateImport.value={...intent,error:'上次覆盖结果尚未确认，请重试原请求。'}}catch(e){console.warn('[annotation.reimport_restore_failed]',e)}
}
// Importing only needs the library idle: the open video may still be tracking.
const libraryBlocked = computed(() => submissionBusy.value || mediaImportBusy.value || !!duplicateImport.value || !!deletingMediaId.value || objectDeletionBusy.value || trackingFeedbackBusy.value || trackingRestartBusy.value || !!pendingRestart.value || !!pendingDeletion.value || !!pendingFeedback.value || workspaceRestoring.value)
export const importPackage = async (files: FileList | null) => {
  const file = files?.[0]
  if (!file || libraryBlocked.value) return
  mediaImportBusy.value=true;importError.value='';packageImportReport.value=null;pausePlayback();cancelAnnotationGesture()
  try{
    await persistWorkspaceState(currentMediaId.value,true)
    cancelScheduledSave(currentMediaId.value)
    statusMessage.value=`正在导入标注包「${file.name}」……`
    const report=await trackApi.importPackage(file)
    packageImportReport.value=report
    await loadServerMedia()
    const first=report.imported[0]&&mediaAssets.value.find(m=>m.serverMediaId===report.imported[0].mediaId)
    if(first){selectedMediaId.value=first.id;await nextTick();await resetAnnotationViewForMedia(first.id)}
    statusMessage.value=report.imported.length?`已导入 ${report.imported.length} 个视频，请到审查页检查`:'没有导入任何视频'
    showToast(statusMessage.value)
  }catch(e:any){importError.value=e.message||'标注包导入失败，请重试';statusMessage.value=importError.value;console.error('[annotation.package_import_failed]',e)}
  finally{mediaImportBusy.value=false;if(packageInputRef.value)packageInputRef.value.value=''}
}
export const handleFiles = async (files: FileList | null, type: 'image' | 'video') => {
  if (!files?.length||libraryBlocked.value) return
  mediaImportBusy.value=true;importError.value='';pausePlayback();cancelAnnotationGesture()
  let lastAddedId:string|null=null
  try{
    await persistWorkspaceState(currentMediaId.value,true)
    cancelScheduledSave(currentMediaId.value)
    for(const file of Array.from(files)){
      if(type==='image'&&!file.type.startsWith('image/'))continue
      if(type==='video'&&!isVideoFile(file))continue
      let uploaded:TrackUploadResponse|undefined
      if(type==='video'){
        try{uploaded=await trackApi.uploadVideo(file)}
        catch(e:any){
          if(e.code!=='DUPLICATE_VIDEO'||!e.duplicate)throw e
          duplicateImport.value={...e.duplicate,key:createRequestId(),pending:false,error:''}
          const existing=mediaAssets.value.find(m=>m.serverMediaId===e.duplicate.mediaId)
          if(existing){cancelScheduledSave(existing.id);await saveQueues.get(existing.id)?.catch(()=>{});const state=await trackApi.getWorkspaceState(e.duplicate.mediaId);duplicateImport.value!.workspaceRevision=state.revision||0}
          const replacement=await new Promise<TrackUploadResponse|null>(resolve=>{finishDuplicate=resolve})
          if(!replacement)continue
          uploaded=replacement
        }
      }
      let media=uploaded?mediaAssets.value.find(m=>m.serverMediaId===uploaded!.mediaId):undefined
      if(!media){
        media={id:`local-${createRequestId()}`,name:uploaded?.videoName||file.name,type,url:uploaded?.videoUrl||URL.createObjectURL(file),sizeBytes:file.size,
          ...(uploaded?{serverMediaId:uploaded.mediaId,serverVideoName:uploaded.videoName,width:uploaded.width,height:uploaded.height,fps:uploaded.fps,duration:uploaded.duration,frameCount:uploaded.frameCount}:{})}
        annotationsByMedia.value={...annotationsByMedia.value,[media.id]:[]};trackingFramesByMedia.value[media.id]=[];mediaAssets.value=[...mediaAssets.value,media]
      }
      lastAddedId=media.id
      if(type==='image')void api.uploadMedia({file})
    }
    if(lastAddedId){selectedMediaId.value=lastAddedId;await nextTick();await resetAnnotationViewForMedia(lastAddedId);statusMessage.value=`已导入：${selectedMedia.value?.name}`;showToast(statusMessage.value)}
  }catch(e:any){importError.value=e.message||'导入失败，请重试';statusMessage.value=importError.value;console.error('[annotation.import_failed]',e)}
  finally{mediaImportBusy.value=false;if(type==='image'&&fileInputRef.value)fileInputRef.value.value='';if(type==='video'&&videoInputRef.value)videoInputRef.value.value=''}
}

/**
 * 启动时从后端加载已有视频素材，刷新页面后素材列表不丢失。
 * 关键修复：通过 serverMediaId 匹配已持久化（localStorage）的素材，
 * 复用原 id（如 local-xxx），这样 annotationsByMedia 的 key 才能对上。
 */
export const loadServerMedia = async () => {
  try {
    const cachedIds = new Set(mediaAssets.value.filter(item => item.serverMediaId).map(item => item.id))
    const res = await trackApi.listMedia()
    const existingIds = new Set(res.items.map(item => item.mediaId))
    // Uploads may finish while the list request is in flight. Only reconcile
    // entries that were already cached when this request began.
    mediaAssets.value = mediaAssets.value.filter(item => !cachedIds.has(item.id) || !item.serverMediaId || existingIds.has(item.serverMediaId))
    // 建立 serverMediaId -> MediaAsset 的索引
    const byServerId = new Map<string, MediaAsset>()
    for (const m of mediaAssets.value) {
      if (m.serverMediaId) byServerId.set(m.serverMediaId, m)
    }

    const additions: MediaAsset[] = []
    for (const item of res.items) {
      const existing = byServerId.get(item.mediaId)
      if (existing) {
        // 已有记录：刷新后 blob: URL 必然失效，用后端新地址覆盖 url；保留原 id
        if (item.videoUrl) existing.url = item.videoUrl
        existing.serverVideoName = item.sourceVideoName || existing.serverVideoName
        if (item.fps) existing.fps = item.fps
        if (item.width) existing.width = item.width
        if (item.height) existing.height = item.height
        if (item.frameCount) existing.frameCount = item.frameCount
        if (item.frameCount && item.fps) existing.duration = item.duration || item.frameCount / item.fps

        continue
      }
      // 新素材：创建条目
      const newAsset: MediaAsset = {
        id: closedMediaFrontendIds[item.mediaId] || `server-${item.mediaId}`,
        serverMediaId: item.mediaId,
        serverVideoName: item.sourceVideoName,
        name: item.videoName,
        type: 'video',
        url: item.videoUrl,
        fps: item.fps || undefined,
        width: item.width || undefined,
        height: item.height || undefined,
        duration: item.duration || (item.frameCount && item.fps ? item.frameCount / item.fps : undefined),
        frameCount: item.frameCount || undefined,
      }
      additions.push(newAsset)
      if (closedMediaFrontendIds[item.mediaId]) delete closedMediaFrontendIds[item.mediaId]
      trackingFramesByMedia.value[newAsset.id] = []
    }
    if (additions.length) {
      mediaAssets.value = [...mediaAssets.value, ...additions]
      persistClosedMediaFrontendIds()
    }

    // 如果当前选中的 media 不存在了（被删），重置到第一个
    if (selectedMediaId.value && !mediaAssets.value.find((m) => m.id === selectedMediaId.value)) {
      selectedMediaId.value = mediaAssets.value[0]?.id ?? ''
    }
    const active = mediaAssets.value.find((item) => item.id === selectedMediaId.value)
    if (active?.serverMediaId) await resetAnnotationViewForMedia(active.id)
  } catch (error) {
    console.error('[annotation.media_list_failed]', error)
    statusMessage.value = '素材列表同步失败，请刷新重试；本机列表已保留'
  }
}
