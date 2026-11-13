// 多人协作同步：个人观看状态、应用共享工作区、冲突解决、stamp 轮询同步、放弃本机草稿。
import { askConfirm } from '../../utils/confirmDialog'
import type { AnnotationObject } from '../../types/annotation'
import { trackApi } from '../../api/trackApi'
import { WorkspaceMergeConflict, sharedWorkspace, workspaceEqual, type MergeChoice, type WorkspaceDraft } from '../../annotation/workspaceWrites'
import { useAuth } from '../auth'
import { activeTool, annotationsByMedia, brightness, clone, collaborationBusy, collaborationErrors, contrast, currentFrame, currentMediaId, dedupeAnnotationObjects, deletedFrameObjects, deletedObjectIds, deletedTrackingIds, editingBlocked, type FrameDeletion, isObjectDeleted, isTrackingMedia, lastEditors, mediaAssets, messageOf, objectNameInput, remoteTracking, runtime, saveError, saveQueues, saveState, selectedMedia, selectedObjectId, trackingJobs, trackingMedia, videoDeletionHistory, workspaceGenerations, workspaceReadError, workspaceRestoring, workspaceWrites, zoom } from './state'
import { histories, historyRevision } from './history'
import { draggingObjectId, headBoxBusy, tempBbox } from './editing'
import { buildWorkspaceState, cancelScheduledSave, draftSuppressed, rememberControls, workspaceSaveTimers } from './save'
import { resetAnnotationViewForMedia } from './restore'
import { loadTrackingResult } from './trackingResult'

export const viewKey=(mediaId:string)=>`annotation-view:${useAuth().user.value?.id??'unknown'}:${mediaAssets.value.find(m=>m.id===mediaId)?.serverMediaId??mediaId}:${workspaceGenerations.get(mediaId)??'legacy'}`
export const savePersonalView=(mediaId:string)=>{
  try{sessionStorage.setItem(viewKey(mediaId),JSON.stringify({currentFrame:currentFrame.value,display:{brightness:brightness.value,contrast:contrast.value,zoom:zoom.value},editor:{activeTool:activeTool.value,objectNameInput:objectNameInput.value,selectedObjectId:selectedObjectId.value}}))}
  catch(error){console.warn('[annotation.view_cache_failed]',{mediaId,error})}
}
export const applySharedWorkspace=(mediaId:string,state:WorkspaceDraft)=>{
  const manual=(annotationsByMedia.value[mediaId]??[]).filter(o=>o.source==='manual')
  if(workspaceEqual(manual,state.manualAnnotations??[]) && workspaceEqual(deletedObjectIds.value[mediaId]??[],state.deletedObjectIds??[]) && workspaceEqual(deletedFrameObjects.value[mediaId]??[],state.deletedFrameObjects??[]) && workspaceEqual([...(deletedTrackingIds.value[mediaId]??[])],state.deletedTrackingIds??[]))return
  // Undo restores whole-frame snapshots. A snapshot taken before this sync lacks the
  // colleague's boxes, so undoing it would delete their work on the next save.
  const frameOf=(o:{frameIndex?:number})=>o.frameIndex??0
  // JSON round trip: page objects carry undefined fields that saved copies drop.
  const before=new Map((clone(manual) as AnnotationObject[]).map(o=>[`${frameOf(o)}:${o.objectId}`,o]))
  const after=new Map((clone(state.manualAnnotations??[]) as AnnotationObject[]).map(o=>[`${frameOf(o)}:${o.objectId}`,o]))
  const touched=new Set<number>()
  for(const key of new Set([...before.keys(),...after.keys()]))if(!workspaceEqual(before.get(key),after.get(key)))touched.add(Number(key.split(':')[0]))
  const oldFrameDeletions=deletedFrameObjects.value[mediaId]??[],newFrameDeletions=state.deletedFrameObjects??[]
  const has=(list:FrameDeletion[],d:FrameDeletion)=>list.some(x=>x.frameIndex===d.frameIndex&&x.objectId===d.objectId)
  for(const d of [...oldFrameDeletions,...newFrameDeletions])if(!has(oldFrameDeletions,d)||!has(newFrameDeletions,d))touched.add(d.frameIndex)
  const wholeVideo=!workspaceEqual(deletedObjectIds.value[mediaId]??[],state.deletedObjectIds??[])||!workspaceEqual([...(deletedTrackingIds.value[mediaId]??[])],state.deletedTrackingIds??[])
  // Same content after normalization (e.g. our own save coming back): leave the page,
  // its object order and its undo history untouched.
  if(!touched.size&&!wholeVideo)return
  for(const key of [...histories.keys()])if(key.startsWith(`${mediaId}:`)&&(wholeVideo||touched.has(Number(key.slice(mediaId.length+1)))))histories.delete(key)
  if(wholeVideo){delete videoDeletionHistory.value[mediaId];rememberControls(mediaId)}
  historyRevision.value++
  runtime.workspaceRestoreInProgress=true
  try{
    deletedObjectIds.value[mediaId]=state.deletedObjectIds??[]
    deletedFrameObjects.value[mediaId]=state.deletedFrameObjects??[]
    deletedTrackingIds.value[mediaId]=new Set(state.deletedTrackingIds??[])
    // Update in place so unchanged boxes keep their position and identity on the page.
    const keyOf=(o:AnnotationObject)=>`${frameOf(o)}:${o.objectId}`
    const incoming=new Map(((state.manualAnnotations??[]) as AnnotationObject[]).map(o=>[keyOf(o),o]))
    const used=new Set<string>(),next:AnnotationObject[]=[]
    for(const o of annotationsByMedia.value[mediaId]??[]){
      if(o.source!=='manual'){next.push(o);continue}
      const key=keyOf(o)
      if(!incoming.has(key)||used.has(key))continue
      used.add(key);next.push(touched.has(frameOf(o))?incoming.get(key)!:o)
    }
    for(const [key,o] of incoming)if(!used.has(key))next.push(o)
    annotationsByMedia.value={...annotationsByMedia.value,[mediaId]:dedupeAnnotationObjects(next).filter(o=>!isObjectDeleted(mediaId,o.objectId,o.frameIndex??0))}
  }finally{runtime.workspaceRestoreInProgress=false}
}
export const resolveCollaboration=async(choices:Record<string,MergeChoice>)=>{
  const mediaId=currentMediaId.value,media=selectedMedia.value
  if(!media?.serverMediaId||collaborationBusy.value)return
  collaborationBusy.value=true
  try{
    const result=await workspaceWrites.resolve(media.serverMediaId,choices)
    if(currentMediaId.value!==mediaId)return
    if(result&&'workspace' in result)applySharedWorkspace(mediaId,result.workspace as WorkspaceDraft)
    const errors={...collaborationErrors.value};delete errors[mediaId];collaborationErrors.value=errors
    workspaceReadError.value='';saveError.value='';saveState.value='saved'
  }catch(error){
    if(error instanceof WorkspaceMergeConflict)collaborationErrors.value={...collaborationErrors.value,[mediaId]:error}
    if(currentMediaId.value===mediaId)saveError.value=messageOf(error,'合并未完成，请重试；草稿已保留')
    console.warn('[annotation.collaboration_resolve_failed]',{mediaId,error})
  }finally{collaborationBusy.value=false}
}
let synchronizing=false
const trackingStamps=new Map<string,string>()
// Returns true when the open video was updated from the server, false when it is
// current, undefined when the check was skipped or failed.
export const syncWorkspace=async()=>{
  const media=selectedMedia.value,mediaId=currentMediaId.value,actor=useAuth().user.value?.id
  if(typeof document!=='undefined'&&document.visibilityState==='hidden')return
  if(!media?.serverMediaId||synchronizing||workspaceRestoring.value)return
  synchronizing=true
  try{
    const stamp=await trackApi.getWorkspaceStamp(media.serverMediaId)
    if(actor!==useAuth().user.value?.id||mediaId!==currentMediaId.value)return
    const others={...remoteTracking.value}
    if(stamp.tracking&&!trackingMedia.has(mediaId)&&!trackingJobs.has(mediaId))others[mediaId]={userName:stamp.tracking.userName,status:stamp.tracking.status};else delete others[mediaId]
    remoteTracking.value=others
    lastEditors.value={...lastEditors.value,[mediaId]:stamp.updatedByName??null}
    const interacting=()=>editingBlocked.value||headBoxBusy.value||!!draggingObjectId||!!tempBbox.value||saveState.value==='saving'||saveState.value==='error'||workspaceSaveTimers.has(mediaId)||saveQueues.has(mediaId)
    let changed=false
    const trackingKey=JSON.stringify(stamp.trackingStamp)
    const previousTracking=trackingStamps.get(mediaId)
    if(previousTracking===undefined)trackingStamps.set(mediaId,trackingKey)
    else if(previousTracking!==trackingKey&&!stamp.tracking&&!isTrackingMedia(mediaId)&&!interacting()){
      // Someone else re-ran tracking: refresh the AI boxes; manual work is kept.
      await loadTrackingResult(mediaId,true)
      if(mediaId!==currentMediaId.value)return
      trackingStamps.set(mediaId,trackingKey);changed=true
    }
    if(stamp.revision===workspaceWrites.getRevision(media.serverMediaId)||interacting())return changed
    const base=workspaceWrites.getBaseline(media.serverMediaId)
    if(!base)return changed
    const observed=annotationsByMedia.value[mediaId]
    const remote=await trackApi.getWorkspaceState(media.serverMediaId)
    if(actor!==useAuth().user.value?.id||mediaId!==currentMediaId.value||interacting())return changed
    if(observed!==annotationsByMedia.value[mediaId] || base!==workspaceWrites.getBaseline(media.serverMediaId))return changed
    if(base.generationId!==remote.generationId)return changed // Reset requires the existing explicit generation recovery.
    if(remote.revision!==workspaceWrites.getRevision(media.serverMediaId)){
      // Unsaved local edits are merged by the save path instead.
      if(!workspaceEqual(sharedWorkspace(buildWorkspaceState(mediaId)),sharedWorkspace(base)))return changed
      applySharedWorkspace(mediaId,remote);workspaceWrites.setBaseline(media.serverMediaId,remote)
      return true
    }
    return changed
  }catch(error){console.warn('[annotation.collaboration_sync_failed]',{mediaId,error})}
  finally{synchronizing=false}
}
// Explicit escape when a local draft keeps failing or the user prefers the shared
// version. An unconfirmed request is kept: it may already be on the server.
export const discardLocalDraft = async () => {
  const mediaId=currentMediaId.value,serverId=selectedMedia.value?.serverMediaId
  if(!serverId||collaborationBusy.value)return
  if(!(await askConfirm({title:'放弃本机的修改？',body:'改用服务器上的最新标注，放弃后找不回来。',confirm:'放弃修改',danger:true})))return
  draftSuppressed.add(mediaId);collaborationBusy.value=true
  try{
    cancelScheduledSave(mediaId)
    await workspaceWrites.clearDraft(serverId)
    const errors={...collaborationErrors.value};delete errors[mediaId];collaborationErrors.value=errors
    saveError.value='';saveState.value='idle'
    collaborationBusy.value=false
    await resetAnnotationViewForMedia(mediaId)
    await workspaceWrites.clearDraft(serverId)
    console.info('[annotation.local_draft_discarded]',{mediaId:serverId})
  }finally{collaborationBusy.value=false;draftSuppressed.delete(mediaId)}
}
