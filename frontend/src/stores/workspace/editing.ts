// 画布交互与对象编辑：工具切换、缩放、点选头部、框的绘制/拖拽/微调、对象增删改。
import { ref, watch } from 'vue'
import { hitHandle, moveBox, resizeBox, type Box } from '../../annotation/geometry'
import type { AnnotationObject, AnnotationTool } from '../../types/annotation'
import { trackApi } from '../../api/trackApi'
import { useAuth } from '../auth'
import { activeTool, annotationHitRef, annotationsByMedia, currentFrame, currentMediaId, currentObjects, currentTime, deletedFrameObjects, deletedTrackingIds, dragPreview, editingBlocked, getNextObjectId, isAiBusy, isVideo, lastPausedContext, mediaAssets, objectNameInput, pausedAnomalies, runtime, selectedMedia, selectedObject, selectedObjectId, showToast, statusMessage, zoom } from './state'
import { snapshotUndo } from './history'
import { scheduleWorkspaceStateSave } from './save'
import { cancelExtend, extendAt } from './prelabel'

export const isInteracting = () => !!tempBbox.value || !!draggingObjectId || !!runtime.bboxStart
// 4K heads are 20–30 px in a ~0.27× fitted view, so allow up to 10× the fit.
export const zoomIn = (step = 0.1) => { if (isInteracting()) return; zoom.value = Math.min(10, +(zoom.value + step).toFixed(2)) }
export const zoomOut = (step = 0.1) => { if (isInteracting()) return; zoom.value = Math.max(0.25, +(zoom.value - step).toFixed(2)) }
export const zoomReset = () => { if (!isInteracting()) zoom.value = 1 }

export const getStagePoint = (event: MouseEvent | PointerEvent) => {
  const stage = annotationHitRef.value
  if (!stage) return { x: 50, y: 50 }
  const rect = stage.getBoundingClientRect()
  if (!rect.width || !rect.height) return { x: 50, y: 50 }
  return {
    x: Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
    y: Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100)),
  }
}

const baseObjectName = (name: string) => {
  const cleaned = name.trim() || 'rare sperm'
  return cleaned.replace(/\s+\d+$/, '').trim() || 'rare sperm'
}

export const numberedObjectName = (name: string, objectId: number) => `${baseObjectName(name)} ${objectId}`

export const normalizeAnnotationObject = (obj: AnnotationObject, fallbackObjectId: number): AnnotationObject => {
  const objectId = typeof obj.objectId === 'number' ? obj.objectId : fallbackObjectId
  return { ...obj, objectId, name: numberedObjectName(obj.name, objectId) }
}

export const addObject = (point: { x: number; y: number }, bbox?: { x: number; y: number; width: number; height: number }) => {
  if (editingBlocked.value) return
  const mediaId = currentMediaId.value
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media) return
  snapshotUndo()
  const objectId = getNextObjectId(mediaId)
  const name = numberedObjectName(objectNameInput.value.trim() || 'rare sperm', objectId)
  const creationId=`manual-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  const object: AnnotationObject = {
    id: creationId, creationId,
    objectId,
    name,
    source: 'manual',
    point: bbox ? undefined : { ...point },
    bbox,
    frameIndex: media.type === 'video' ? currentFrame.value : undefined,
    timestampMs: media.type === 'video' ? Math.round(currentTime.value * 1000) : undefined,
  }

  const existing = annotationsByMedia.value[mediaId] ?? []
  annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: [...existing, object] }
  selectedObjectId.value = object.id
  statusMessage.value = `已添加：${name}${media.type === 'video' ? `（第 ${currentFrame.value + 1} 帧）` : ''}`
}

const toolLabel = (tool: AnnotationTool) =>
  tool === 'smart' ? '标注' : tool === 'head' ? '点选头部' : tool === 'select' ? '选择框' : tool === 'point' ? '点标注' : '框标注'

export const selectTool = (tool: AnnotationTool) => {
  if (editingBlocked.value) return
  activeTool.value = tool
  statusMessage.value = isVideo.value
    ? `已切换到${toolLabel(tool)} · 当前第 ${currentFrame.value + 1} 帧`
    : `已切换到${toolLabel(tool)}`
}

export const edgeDiscovery = ref(true) // New tracking always discovers edge entries; persisted jobs replay their original body.
export const headBoxBusy = ref(false)
export const headBoxError = ref('')
let headRequest: AbortController | null = null
export const cancelHeadBox = () => { headRequest?.abort(); headRequest=null; headBoxBusy.value=false }
export const createHeadBox = async (point: {x:number;y:number}, creationKey?:string) => {
  headBoxError.value=''
  if (editingBlocked.value || headBoxBusy.value) return {status:'blocked' as const}
  const media=selectedMedia.value
  if (!media?.serverMediaId || media.type!=='video') {
    headBoxError.value='点选头部目前用于已导入的视频；图片请使用手动画框。'; return {status:'error' as const}
  }
  if(creationKey && currentObjects.value.some(obj=>obj.reviewAdoptionKey===creationKey))return {status:'created' as const}
  const frame=currentFrame.value, controller=new AbortController()
  headRequest=controller; headBoxBusy.value=true; headBoxError.value=''
  try {
    const result=await trackApi.headBox(media.serverMediaId,frame,Math.min((media.width||1)-1,point.x*(media.width||1)/100),Math.min((media.height||1)-1,point.y*(media.height||1)/100),controller.signal)
    if(headRequest!==controller || controller.signal.aborted || editingBlocked.value) return {status:'cancelled' as const}
    const [x1,y1,x2,y2]=result.bbox || []
    if(result.frameIndex!==frame || result.width!==media.width || result.height!==media.height || result.bbox.length!==4 || ![x1,y1,x2,y2].every(Number.isFinite) || x1<0 || y1<0 || x2>result.width || y2>result.height || x2<=x1 || y2<=y1) throw new Error('检测结果无效，未创建标注框，请重试或手动画框。')
    const box={x:x1/result.width*100,y:y1/result.height*100,width:(x2-x1)/result.width*100,height:(y2-y1)/result.height*100}
    const existing=currentObjects.value.find(obj=>{if(!obj.bbox)return false;const b=obj.bbox;const area=Math.max(0,Math.min(b.x+b.width,box.x+box.width)-Math.max(b.x,box.x))*Math.max(0,Math.min(b.y+b.height,box.y+box.height)-Math.max(b.y,box.y));return area/(b.width*b.height+box.width*box.height-area)>.7})
    if(existing){selectedObjectId.value=existing.id;statusMessage.value='该头部已有标注，可继续点选其他头部；按 V 微调';return {status:creationKey && existing.reviewAdoptionKey===creationKey ? 'created' as const : 'existing' as const,objectId:existing.objectId}}
    addObject(point,box)
    if(creationKey){
      annotationsByMedia.value={...annotationsByMedia.value,[media.id]:(annotationsByMedia.value[media.id]??[]).map(obj=>obj.id===selectedObjectId.value?{...obj,reviewAdoptionKey:creationKey}:obj)}
    }
    statusMessage.value='已生成头部框，可继续点选；按 V 微调，或撤销'
    return {status:'created' as const}
  } catch(error) {
    if(headRequest!==controller || controller.signal.aborted)return {status:'cancelled' as const}
    headBoxError.value=error instanceof Error?error.message:'头部检测失败，请重试或手动画框'
    console.warn('[annotation.head_box_failed]',{mediaId:media.id,frame,error})
    return {status:'error' as const}
  } finally { if(headRequest===controller) cancelHeadBox() }
}

export const onStageClick = (event: MouseEvent) => {
  if (editingBlocked.value) return
  // The combined tool decides click versus drag on pointer up.
  if (activeTool.value === 'smart') { hitExisting = false; event.preventDefault(); return }
  if (activeTool.value === 'head') { event.preventDefault(); void createHeadBox(getStagePoint(event)); return }
  // 如果本次按下命中了已有框，跳过 click（防止添加新点标注覆盖选中）
  if (hitExisting) { hitExisting = false; return }
  if (activeTool.value === 'select') {
    const point = getStagePoint(event)
    const hit = [...currentObjects.value].reverse().find((obj) => obj.bbox && pointInBbox(point.x, point.y, obj.bbox))
    if (hit) {
      selectedObjectId.value = hit.id
      statusMessage.value = `已选中 ${hit.name}`
    } else {
      clearSelection()
    }
    event.preventDefault()
    event.stopPropagation()
    return
  }
  if (activeTool.value !== 'point') return
  event.preventDefault()
  event.stopPropagation()
  addObject(getStagePoint(event))
}
let bboxPointerId: number | null = null
export const tempBbox = ref<{ x: number; y: number; width: number; height: number } | null>(null)

// 拖拽已有框移动 / 调整大小（draggingObjectId 只在本模块写入，collaboration 读取其实时绑定）
export let draggingObjectId: string | null = null
let dragStartPoint: { x: number; y: number } | null = null
let dragStartBbox: { x: number; y: number; width: number; height: number } | null = null
let dragMoved = false
let resizeHandle: 'nw' | 'ne' | 'sw' | 'se' | null = null
let hitExisting = false // 标记本次按下命中了已有框，用于阻止后续 click 事件

const pointInBbox = (px: number, py: number, b: Box) => px >= b.x && px <= b.x+b.width && py >= b.y && py <= b.y+b.height
export const onBboxDown = (event: PointerEvent) => {
  if (editingBlocked.value || activeTool.value === 'head' || event.button !== 0) return
  event.preventDefault(); event.stopPropagation()
  cancelAnnotationGesture()
  bboxPointerId = event.pointerId
  const stage = annotationHitRef.value
  stage?.setPointerCapture(event.pointerId)
  const point = getStagePoint(event), rect = stage!.getBoundingClientRect()
  const selected = currentObjects.value.find(o => o.id === selectedObjectId.value)
  const handle = selected?.bbox ? hitHandle(point.x, point.y, selected.bbox, rect.width, rect.height) : null
  const hit = handle ? selected : [...currentObjects.value].reverse().find(o => o.bbox && pointInBbox(point.x, point.y, o.bbox))
  if (hit?.bbox) {
    selectedObjectId.value = hit.id; draggingObjectId = hit.id
    dragStartPoint = point; dragStartBbox = { ...hit.bbox }; dragMoved = false
    resizeHandle = handle; hitExisting = true
  } else if (activeTool.value === 'bbox' || activeTool.value === 'smart') {
    runtime.bboxStart = point; tempBbox.value = { ...point, width: 0, height: 0 }; hitExisting = false
  } else if (activeTool.value === 'select') { clearSelection() }
}
export const onBboxMove = (event: PointerEvent) => {
  if (bboxPointerId !== event.pointerId) return
  const point = getStagePoint(event)
  if (draggingObjectId && dragStartPoint && dragStartBbox) {
    const dx = point.x-dragStartPoint.x, dy = point.y-dragStartPoint.y
    const w = selectedMedia.value?.width || 1000, h = selectedMedia.value?.height || 1000
    const box = resizeHandle ? resizeBox(dragStartBbox, dx, dy, resizeHandle, 200/w, 200/h) : moveBox(dragStartBbox, dx, dy)
    dragMoved = JSON.stringify(box) !== JSON.stringify(dragStartBbox)
    dragPreview.value = { id: draggingObjectId, bbox: box }
  } else if (runtime.bboxStart) {
    tempBbox.value = { x: Math.min(runtime.bboxStart.x, point.x), y: Math.min(runtime.bboxStart.y, point.y), width: Math.abs(point.x-runtime.bboxStart.x), height: Math.abs(point.y-runtime.bboxStart.y) }
  }
}
export const cancelAnnotationGesture = () => {
  const id = bboxPointerId
  bboxPointerId = null; runtime.bboxStart = null; tempBbox.value = null
  draggingObjectId = null; dragStartPoint = null; dragStartBbox = null; dragMoved = false; resizeHandle = null; dragPreview.value = null
  if (id !== null && annotationHitRef.value?.hasPointerCapture(id)) annotationHitRef.value.releasePointerCapture(id)
}
export const onBboxUp = (event: PointerEvent) => {
  if (bboxPointerId !== event.pointerId) return
  onBboxMove(event)
  if (dragPreview.value && dragMoved) {
    snapshotUndo() // Snapshot the original, before the single committed mutation.
    const { id, bbox } = dragPreview.value, mid = currentMediaId.value
    annotationsByMedia.value = { ...annotationsByMedia.value, [mid]: annotationsByMedia.value[mid].map(o => o.id === id ? { ...o, bbox, source: 'manual' as const } : o) }
    statusMessage.value = resizeHandle ? '已调整标注框' : '已移动标注框'
  } else if (runtime.bboxStart && tempBbox.value) {
    const b = tempBbox.value, w = selectedMedia.value?.width || 1000, h = selectedMedia.value?.height || 1000
    const drawn = b.width*w/100 >= 2 && b.height*h/100 >= 2
    const clicked = activeTool.value === 'smart' && b.width*w/100 < 4 && b.height*h/100 < 4
    if (clicked) { const at = { ...runtime.bboxStart }; cancelAnnotationGesture(); selectedObjectId.value = null; void extendAt(at); return }
    if (drawn) addObject({ x: b.x+b.width/2, y: b.y+b.height/2 }, { ...b })
  }
  cancelAnnotationGesture()
}
export const nudgeSelected = (dx: number, dy: number) => {
  const obj = selectedObject.value, media = selectedMedia.value
  if (editingBlocked.value || isInteracting() || !obj?.bbox || !media?.width || !media.height) return
  const bbox = moveBox(obj.bbox, dx/media.width*100, dy/media.height*100)
  if (JSON.stringify(bbox) === JSON.stringify(obj.bbox)) return
  snapshotUndo()
  annotationsByMedia.value = { ...annotationsByMedia.value, [media.id]: annotationsByMedia.value[media.id].map(o => o.id === obj.id ? { ...o, bbox, source: 'manual' as const } : o) }
  statusMessage.value = `已微调 ${obj.name}`
}

export const onObjectDropdownChange = () => {
  if (selectedObjectId.value) selectObject(selectedObjectId.value)
}

export const selectObject = (objectId: string) => {
  selectedObjectId.value = objectId
  const object = currentObjects.value.find((item) => item.id === objectId)
  if (!object) return

  statusMessage.value = isVideo.value
    ? `已定位：${object.name} · 第 ${currentFrame.value + 1} 帧`
    : `已定位：${object.name}`
}

export const removeObject = (objectId: string) => {
  if (editingBlocked.value || isInteracting()) return
  const target = currentObjects.value.find(o => o.id === objectId)
  if (!target) return
  snapshotUndo()
  const mediaId = currentMediaId.value, frameIndex = currentFrame.value
  annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: (annotationsByMedia.value[mediaId] ?? []).filter(o => !((o.frameIndex ?? 0) === frameIndex && (target.objectId != null ? o.objectId === target.objectId : o.id === objectId))) }
  if (target.objectId != null) {
    deletedFrameObjects.value[mediaId] = [...(deletedFrameObjects.value[mediaId] ?? []).filter(d => !(d.objectId === target.objectId && d.frameIndex === frameIndex)), { objectId: target.objectId, frameIndex }]
  } else {
    if (!deletedTrackingIds.value[mediaId]) deletedTrackingIds.value[mediaId] = new Set()
    deletedTrackingIds.value[mediaId].add(objectId)
  }
  if (lastPausedContext.value?.mediaId === mediaId && lastPausedContext.value.frameIndex === frameIndex) {
    pausedAnomalies.value = pausedAnomalies.value.filter(item => item.objectId !== target.objectId)
    if (!pausedAnomalies.value.length) lastPausedContext.value = null
  }
  statusMessage.value = `已删除第 ${frameIndex + 1} 帧的 ${target.name}，可撤销`
  scheduleWorkspaceStateSave(mediaId)
  selectedObjectId.value = null
}

export const renameObject = () => {
  if (editingBlocked.value || isInteracting()) return
  const name = objectNameInput.value.trim()
  if (!selectedObjectId.value || !name) return
  snapshotUndo()
  const mediaId = currentMediaId.value
  const allObjects = annotationsByMedia.value[mediaId] ?? []
  annotationsByMedia.value = {
    ...annotationsByMedia.value,
    [mediaId]: allObjects.map((obj) => obj.id === selectedObjectId.value ? { ...obj, name, source: 'manual' as const } : obj),
  }
  statusMessage.value = `对象已命名为：${name}`
}

// 复制上一帧标注到当前帧
export const copyPreviousFrame = () => {
  if (editingBlocked.value || isInteracting()) return
  if (!isVideo.value) { showToast('仅视频支持复制上一帧'); return }
  const mediaId = currentMediaId.value
  const prevFrame = currentFrame.value - 1
  if (prevFrame < 0) { showToast('已是第一帧'); return }
  const allObjs = annotationsByMedia.value[mediaId] ?? []
  const present = new Set(currentObjects.value.map(o => o.objectId ?? o.id))
  const prevObjs = allObjs.filter(o => (o.frameIndex ?? 0) === prevFrame && !present.has(o.objectId ?? o.id))
  if (!prevObjs.length) { showToast(`上一帧没有可补充的对象`); return }
  snapshotUndo()
  const newObjs = prevObjs.map((o) => ({
    ...JSON.parse(JSON.stringify(o)),
    id: `manual-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    frameIndex: currentFrame.value,
    timestampMs: Math.round(currentTime.value * 1000),
    source: 'manual' as const,
  }))
  const copiedIds = new Set(newObjs.map(o => o.objectId))
  deletedFrameObjects.value[mediaId] = (deletedFrameObjects.value[mediaId] ?? []).filter(d => d.frameIndex !== currentFrame.value || !copiedIds.has(d.objectId))
  annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: [...allObjs, ...newObjs] }
  statusMessage.value = `已从上一帧补充 ${newObjs.length} 个标注`
}

export const clearSelection = () => {
  if (isAiBusy.value) return
  selectedObjectId.value = null
  statusMessage.value = '已取消选择'
}

export const setupEditingWatchers = () => {
  watch([currentMediaId, currentFrame, activeTool, editingBlocked, annotationsByMedia, () => useAuth().user.value?.id], cancelHeadBox, {flush:'sync'})
  watch([currentMediaId, currentFrame, editingBlocked, () => useAuth().user.value?.id], cancelExtend, {flush:'sync'})
}
