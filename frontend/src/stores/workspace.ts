import { computed, nextTick, ref, watch } from 'vue'
import type { AnnotationObject, AnnotationTool, EffectResult, MediaAsset, SavedAnnotationFile } from '../types/annotation'
// 登录、人工标注、视频目录、SAM3 Tracking 均走真实后端
import { httpAnnotationApi, exportDataset as apiExportDataset } from '../api/httpAnnotationApi'
import { trackApi } from '../api/trackApi'
import type { TrackingFrameResult } from '../types/annotation'

const createWorkspace = () => {
  const api = httpAnnotationApi

  // ── mediaAssets 持久化：刷新后 id 不变，annotationsByMedia 才能匹配 ──
  const loadMediaAssets = (): MediaAsset[] => {
    try {
      const raw = localStorage.getItem('mediaAssets')
      if (raw) return JSON.parse(raw)
    } catch {}
    return [
      {
        id: 'img-demo-001',
        name: 'microfluidic-sample.svg',
        type: 'image',
        url: '/demo/microfluidic-sample.svg',
        width: 1600,
        height: 900,
      },
    ]
  }
  const mediaAssets = ref<MediaAsset[]>(loadMediaAssets())
  let mediaSaveTimer: ReturnType<typeof setTimeout> | null = null
  watch(mediaAssets, (val) => {
    if (mediaSaveTimer) clearTimeout(mediaSaveTimer)
    mediaSaveTimer = setTimeout(() => {
      try {
        // URL.createObjectURL 生成的 blob: URL 在刷新后失效，持久化前剔除
        const persistable = val.map((m) => ({ ...m }))
        localStorage.setItem('mediaAssets', JSON.stringify(persistable))
      } catch {}
    }, 300)
  }, { deep: true })

  const selectedMediaId = ref(mediaAssets.value[0]?.id ?? '')
  const activeTool = ref<AnnotationTool>('point')
  const objectNameInput = ref('rare sperm')
  const selectedObjectId = ref<string | null>(null)
  const currentFrame = ref(0)
  const currentTime = ref(0)
  const videoDuration = ref(0)
  const videoFps = ref(30)
  const frameInput = ref(0)
  const isPlaying = ref(false)
  const isAiBusy = ref(false)
  const trackingFrameCount = ref(5)
  const statusMessage = ref('就绪')
  const zoom = ref(1)
  const isInteracting = () => !!tempBbox.value || !!draggingObjectId || !!bboxStart
  const zoomIn = (step = 0.1) => { if (isInteracting()) return; zoom.value = Math.min(5, +(zoom.value + step).toFixed(2)) }
  const zoomOut = (step = 0.1) => { if (isInteracting()) return; zoom.value = Math.max(0.25, +(zoom.value - step).toFixed(2)) }
  const zoomReset = () => { zoom.value = 1 }
  const deleteMedia = async (mediaId: string) => {
    const media = mediaAssets.value.find((m) => m.id === mediaId)
    if (!media) return
    if (!media.serverMediaId) {
      // 本地素材直接移除
      mediaAssets.value = mediaAssets.value.filter((m) => m.id !== mediaId)
      if (selectedMediaId.value === mediaId) {
        selectedMediaId.value = mediaAssets.value.length ? mediaAssets.value[0].id : ''
      }
      return
    }
    try {
      await trackApi.deleteMedia(media.serverMediaId)
    } catch {
      // 后端可能已不存在该素材（404），忽略错误，前端仍然清理
    } finally {
      mediaAssets.value = mediaAssets.value.filter((m) => m.id !== mediaId)
      delete trackingFramesByMedia.value[mediaId]
      if (selectedMediaId.value === mediaId) {
        selectedMediaId.value = mediaAssets.value.length ? mediaAssets.value[0].id : ''
      }
      showToast(`已删除：${media.name}`)
    }
  }
  const toastMessage = ref('')
  let toastTimer: ReturnType<typeof setTimeout> | null = null
  const showToast = (message: string) => {
    toastMessage.value = message
    if (toastTimer) clearTimeout(toastTimer)
    toastTimer = setTimeout(() => { toastMessage.value = '' }, 2800)
  }
  const saveFolderHandle = ref<any>(null)
  const savedResults = ref<SavedAnnotationFile[]>([])
  const effectResults = ref<EffectResult[]>([])
  const selectedEffectId = ref<string | null>(null)
  const effectTime = ref(0)
  const effectPlaying = ref(false)
  const effectVideoRef = ref<HTMLVideoElement | null>(null)

  const imageRef = ref<HTMLImageElement | null>(null)
  const videoRef = ref<HTMLVideoElement | null>(null)
  const annotationHitRef = ref<HTMLDivElement | null>(null)
  const fileInputRef = ref<HTMLInputElement | null>(null)
  const videoInputRef = ref<HTMLInputElement | null>(null)
  const effectFolderInputRef = ref<HTMLInputElement | null>(null)

  // 从 localStorage 恢复标注数据
  const loadAnnotations = (): Record<string, AnnotationObject[]> => {
    try {
      const raw = localStorage.getItem('annotationsByMedia')
      if (raw) return JSON.parse(raw)
    } catch {}
    return {
      'img-demo-001': [],
      'video-demo-001': [],
    }
  }
  const annotationsByMedia = ref<Record<string, AnnotationObject[]>>(loadAnnotations())

  // 自动持久化到 localStorage（debounce 300ms）
  let saveTimer: ReturnType<typeof setTimeout> | null = null
  watch(annotationsByMedia, (val) => {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      try { localStorage.setItem('annotationsByMedia', JSON.stringify(val)) } catch {}
    }, 300)
  }, { deep: true })

  // ── 撤销/重做栈 ──
  const undoStack: string[] = []
  const redoStack: string[] = []
  const MAX_UNDO = 50
  let undoSnapshotInProgress = false

  const snapshotUndo = () => {
    if (undoSnapshotInProgress) return
    undoStack.push(JSON.stringify(annotationsByMedia.value))
    if (undoStack.length > MAX_UNDO) undoStack.shift()
    redoStack.length = 0
  }
  const undo = () => {
    if (!undoStack.length) { showToast('没有可撤销的操作'); return }
    redoStack.push(JSON.stringify(annotationsByMedia.value))
    const prev = undoStack.pop()!
    undoSnapshotInProgress = true
    annotationsByMedia.value = JSON.parse(prev)
    undoSnapshotInProgress = false
    statusMessage.value = '已撤销'
  }
  const redo = () => {
    if (!redoStack.length) { showToast('没有可重做的操作'); return }
    undoStack.push(JSON.stringify(annotationsByMedia.value))
    const next = redoStack.pop()!
    undoSnapshotInProgress = true
    annotationsByMedia.value = JSON.parse(next)
    undoSnapshotInProgress = false
    statusMessage.value = '已重做'
  }

  // SAM3 逐帧结果缓存。key=前端 media.id，value=服务器 tracking_result.json 的 frames。
  const trackingFramesByMedia = ref<Record<string, TrackingFrameResult[]>>({})
  /** 记录前端删除的 AI tracking 对象 id，避免重新加载后又出现 */
  const deletedTrackingIds = ref<Record<string, Set<string>>>({})
  // 异常物体 ID 列表（面积突变等），用于高亮
  const anomalyObjectIds = ref<number[]>([])
  // 异常帧列表（给时间轴标记用）
  const anomalyFrames = ref<Array<{ frame_index: number; level: string; reasons: string[] }>>([])
  let trackingLoadSerial = 0
  let lastTrackingPollAt = 0
  let videoFrameCallbackId: number | null = null

  const selectedMedia = computed(() => mediaAssets.value.find((item) => item.id === selectedMediaId.value) ?? mediaAssets.value[0] ?? null)
  const isVideo = computed(() => selectedMedia.value?.type === 'video')
  const maxFrameIndex = computed(() => Math.max(0, Math.ceil(videoDuration.value * videoFps.value) - 1))

  /** 视频按真实 currentFrame 标注；Tracking 从当前人工标注帧开始。 */
  const currentMediaId = computed(() => selectedMediaId.value)

  /** 全局 objectId 计数器：从现有最大 objectId 继续分配 */
  const getNextObjectId = (mediaId?: string): number => {
    let maxId = 0
    for (const [mid, objs] of Object.entries(annotationsByMedia.value)) {
      if (mediaId && mid !== mediaId) continue
      for (const o of objs) {
        if (typeof o.objectId === 'number' && o.objectId > maxId) maxId = o.objectId
      }
    }
    return maxId + 1
  }
  const currentObjects = computed(() => {
    const all = annotationsByMedia.value[currentMediaId.value] ?? []
    if (!isVideo.value) return all
    return all.filter((item) => (item.frameIndex ?? 0) === currentFrame.value)
  })
  const selectedObject = computed(() => currentObjects.value.find((item) => item.id === selectedObjectId.value) ?? null)
  const selectedEffect = computed(() => effectResults.value.find((item) => item.id === selectedEffectId.value) ?? effectResults.value[0] ?? null)

  const formatTime = (seconds: number) => {
    if (!Number.isFinite(seconds)) return '00:00.000'
    const m = Math.floor(seconds / 60).toString().padStart(2, '0')
    const s = Math.floor(seconds % 60).toString().padStart(2, '0')
    const ms = Math.floor((seconds % 1) * 1000).toString().padStart(3, '0')
    return `${m}:${s}.${ms}`
  }

  const timeToFrame = (time: number) => Math.max(0, Math.round(time * videoFps.value))
  const frameToTime = (frame: number) => frame / videoFps.value

  const getStagePoint = (event: MouseEvent | PointerEvent) => {
    const stage = annotationHitRef.value
    if (!stage) return { x: 50, y: 50 }
    const rect = stage.getBoundingClientRect()
    if (!rect.width || !rect.height) return { x: 50, y: 50 }
    return {
      x: Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
      y: Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100)),
    }
  }

  const addObject = (point: { x: number; y: number }, bbox?: { x: number; y: number; width: number; height: number }) => {
    if (isAiBusy.value) { showToast('SAM3 Tracking 运行中，暂时禁止人工标注'); return }
    const mediaId = currentMediaId.value
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media) return
    snapshotUndo()
    const name = objectNameInput.value.trim() || '未命名目标'
    const object: AnnotationObject = {
      id: `manual-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      objectId: getNextObjectId(mediaId),
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
    statusMessage.value = `已添加：${name}${media.type === 'video' ? `（第 ${currentFrame.value} 帧）` : ''}`
  }

  const resetVideoViewToFirstFrame = (mediaId = currentMediaId.value) => {
    if (currentMediaId.value !== mediaId || !isVideo.value) return
    currentFrame.value = 0
    currentTime.value = 0
    frameInput.value = 0
    if (videoRef.value?.readyState) {
      try { videoRef.value.currentTime = 0 } catch { }
    }
  }

  const ensureVideoFirstFrame = async (_mediaId = currentMediaId.value) => true

  const selectTool = (tool: AnnotationTool) => {
    if (isAiBusy.value) { showToast('SAM3 Tracking 运行中，暂时禁止人工标注'); return }
    activeTool.value = tool
    statusMessage.value = isVideo.value
      ? `已切换到${tool === 'point' ? '点标注' : '框标注'} · 当前第 ${currentFrame.value} 帧`
      : `已切换到${tool === 'point' ? '点标注' : '框标注'}`
  }

  const onStageClick = (event: MouseEvent) => {
    if (isAiBusy.value) return
    // 如果本次按下命中了已有框，跳过 click（防止添加新点标注覆盖选中）
    if (hitExisting) { hitExisting = false; return }
    if (activeTool.value !== 'point') return
    event.preventDefault()
    event.stopPropagation()
    addObject(getStagePoint(event))
  }

  let bboxStart: { x: number; y: number } | null = null
  let bboxPointerId: number | null = null
  const tempBbox = ref<{ x: number; y: number; width: number; height: number } | null>(null)

  // 拖拽已有框移动 / 调整大小
  let draggingObjectId: string | null = null
  let dragStartPoint: { x: number; y: number } | null = null
  let dragStartBbox: { x: number; y: number; width: number; height: number } | null = null
  let dragMoved = false
  let resizeHandle: 'nw' | 'ne' | 'sw' | 'se' | null = null
  let hitExisting = false // 标记本次按下命中了已有框，用于阻止后续 click 事件

  const HANDLE_HIT = 2.5 // 角点命中范围（百分比坐标）
  const pointInBbox = (px: number, py: number, b: { x: number; y: number; width: number; height: number }) =>
    px >= b.x && px <= b.x + b.width && py >= b.y && py <= b.y + b.height

  const getResizeHandle = (px: number, py: number, b: { x: number; y: number; width: number; height: number }) => {
    const { x, y, width: w, height: h } = b
    if (Math.abs(px - x) < HANDLE_HIT && Math.abs(py - y) < HANDLE_HIT) return 'nw'
    if (Math.abs(px - (x + w)) < HANDLE_HIT && Math.abs(py - y) < HANDLE_HIT) return 'ne'
    if (Math.abs(px - x) < HANDLE_HIT && Math.abs(py - (y + h)) < HANDLE_HIT) return 'sw'
    if (Math.abs(px - (x + w)) < HANDLE_HIT && Math.abs(py - (y + h)) < HANDLE_HIT) return 'se'
    return null
  }

  const onBboxDown = (event: PointerEvent) => {
    if (isAiBusy.value) return
    event.preventDefault()
    event.stopPropagation()
    bboxPointerId = event.pointerId
    const stage = annotationHitRef.value
    if (stage) {
      try { stage.setPointerCapture(event.pointerId) } catch { }
    }
    const point = getStagePoint(event)

    // 1. 优先检测：已选中框的角点（允许框外一定范围命中）
    if (selectedObjectId.value) {
      const sel = currentObjects.value.find((o) => o.id === selectedObjectId.value && o.bbox)
      if (sel?.bbox) {
        const handle = getResizeHandle(point.x, point.y, sel.bbox)
        if (handle) {
          draggingObjectId = sel.id
          dragStartPoint = point
          dragStartBbox = { ...sel.bbox }
          dragMoved = false
          resizeHandle = handle
          hitExisting = true
          statusMessage.value = `已选中 ${sel.name}，拖拽角点可调整框大小`
          return
        }
      }
    }

    // 2. 检测是否点中已有框（移动）
    const hit = [...currentObjects.value].reverse().find((obj) => obj.bbox && pointInBbox(point.x, point.y, obj.bbox))
    if (hit) {
      draggingObjectId = hit.id
      dragStartPoint = point
      dragStartBbox = { ...hit.bbox! }
      dragMoved = false
      resizeHandle = null
      hitExisting = true
      selectedObjectId.value = hit.id
      statusMessage.value = `已选中 ${hit.name}，拖拽可移动框位置`
      return
    }

    // 3. 未命中已有框：只有框标注模式才绘制新框
    if (activeTool.value !== 'bbox') return
    bboxStart = point
    tempBbox.value = { x: point.x, y: point.y, width: 0, height: 0 }
  }

  const onBboxMove = (event: PointerEvent) => {
    if (bboxPointerId !== event.pointerId) return
    const point = getStagePoint(event)

    // 拖拽已有框：移动或调整大小
    if (draggingObjectId && dragStartPoint && dragStartBbox) {
      const dx = point.x - dragStartPoint.x
      const dy = point.y - dragStartPoint.y
      if (Math.abs(dx) > 0.3 || Math.abs(dy) > 0.3) dragMoved = true
      const startBbox = dragStartBbox
      let newBbox = { ...startBbox }

      if (resizeHandle) {
        // 调整大小
        let { x, y, width: w, height: h } = startBbox
        if (resizeHandle === 'nw') { x = startBbox.x + dx; y = startBbox.y + dy; w = startBbox.width - dx; h = startBbox.height - dy }
        if (resizeHandle === 'ne') { y = startBbox.y + dy; w = startBbox.width + dx; h = startBbox.height - dy }
        if (resizeHandle === 'sw') { x = startBbox.x + dx; w = startBbox.width - dx; h = startBbox.height + dy }
        if (resizeHandle === 'se') { w = startBbox.width + dx; h = startBbox.height + dy }
        // 保证最小尺寸
        if (w < 1) { if (resizeHandle.includes('w')) x = startBbox.x + startBbox.width - 1; w = 1 }
        if (h < 1) { if (resizeHandle.includes('n')) y = startBbox.y + startBbox.height - 1; h = 1 }
        newBbox = {
          x: Math.max(0, Math.min(100 - w, x)),
          y: Math.max(0, Math.min(100 - h, y)),
          width: w,
          height: h,
        }
      } else {
        // 移动
        newBbox = {
          x: Math.max(0, Math.min(100 - startBbox.width, startBbox.x + dx)),
          y: Math.max(0, Math.min(100 - startBbox.height, startBbox.y + dy)),
          width: startBbox.width,
          height: startBbox.height,
        }
      }

      const mediaId = currentMediaId.value
      const allObjs = annotationsByMedia.value[mediaId] ?? []
      annotationsByMedia.value = {
        ...annotationsByMedia.value,
        [mediaId]: allObjs.map((obj) =>
          obj.id === draggingObjectId && obj.bbox ? { ...obj, bbox: newBbox } : obj
        ),
      }
      return
    }

    if (!bboxStart) return
    tempBbox.value = {
      x: Math.min(bboxStart.x, point.x),
      y: Math.min(bboxStart.y, point.y),
      width: Math.abs(point.x - bboxStart.x),
      height: Math.abs(point.y - bboxStart.y),
    }
  }

  const onBboxUp = (event: PointerEvent) => {
    if (bboxPointerId !== event.pointerId) return
    const stage = annotationHitRef.value
    if (stage) {
      try { stage.releasePointerCapture(event.pointerId) } catch { }
    }

    // 结束拖拽
    if (draggingObjectId) {
      const obj = currentObjects.value.find((o) => o.id === draggingObjectId)
      if (obj && dragMoved) snapshotUndo()
      if (obj) {
        const action = resizeHandle ? (dragMoved ? '已调整' : '已选中') : (dragMoved ? '已移动' : '已选中')
        statusMessage.value = `${action} ${obj.name}`
      }
      draggingObjectId = null
      dragStartPoint = null
      dragStartBbox = null
      dragMoved = false
      resizeHandle = null
      bboxPointerId = null
      return
    }

    // 只有真正拖拽出一定大小的框才创建
    if (tempBbox.value && tempBbox.value.width > 2 && tempBbox.value.height > 2) {
      const center = { x: tempBbox.value.x + tempBbox.value.width / 2, y: tempBbox.value.y + tempBbox.value.height / 2 }
      addObject(center, { ...tempBbox.value })
    } else if (selectedObjectId.value) {
      // 点击空白处且没画框：取消选中
      clearSelection()
    }
    bboxPointerId = null
    bboxStart = null
    tempBbox.value = null
  }

  const onObjectDropdownChange = () => {
    if (selectedObjectId.value) selectObject(selectedObjectId.value)
  }

  const selectObject = (objectId: string) => {
    selectedObjectId.value = objectId
    const object = currentObjects.value.find((item) => item.id === objectId)
    if (!object) return

    statusMessage.value = isVideo.value
      ? `已定位：${object.name} · 第 ${currentFrame.value} 帧`
      : `已定位：${object.name}`
  }

  const removeObject = (objectId: string) => {
    if (isAiBusy.value) { showToast('SAM3 Tracking 运行中，暂时禁止修改标注'); return }
    snapshotUndo()
    const mediaId = currentMediaId.value

    // 只删除当前帧的该标注，不影响其他帧
    const list = annotationsByMedia.value[mediaId] ?? []
    const filtered = list.filter((obj) => !(obj.id === objectId && (obj.frameIndex ?? 0) === currentFrame.value))
    if (filtered.length !== list.length) {
      annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: filtered }
      statusMessage.value = `已删除第 ${currentFrame.value} 帧的标注`
    } else {
      statusMessage.value = '未找到要删除的标注'
    }

    // 记录到已删除集合，防止 loadTrackingResult 重新加载后又出现
    if (!deletedTrackingIds.value[mediaId]) deletedTrackingIds.value[mediaId] = new Set()
    deletedTrackingIds.value[mediaId].add(objectId)

    if (selectedObjectId.value === objectId) selectedObjectId.value = null
  }

  const renameObject = () => {
    if (isAiBusy.value) { showToast('SAM3 Tracking 运行中，暂时禁止修改标注'); return }
    const name = objectNameInput.value.trim()
    if (!selectedObjectId.value || !name) return
    snapshotUndo()
    const mediaId = currentMediaId.value
    annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: currentObjects.value.map((obj) => obj.id === selectedObjectId.value ? { ...obj, name } : obj) }
    statusMessage.value = `对象已命名为：${name}`
  }

  // 复制上一帧标注到当前帧
  const copyPreviousFrame = () => {
    if (isAiBusy.value) { showToast('SAM3 Tracking 运行中'); return }
    if (!isVideo.value) { showToast('仅视频支持复制上一帧'); return }
    const mediaId = currentMediaId.value
    const prevFrame = currentFrame.value - 1
    if (prevFrame < 0) { showToast('已是第一帧'); return }
    const allObjs = annotationsByMedia.value[mediaId] ?? []
    const prevObjs = allObjs.filter((o) => (o.frameIndex ?? 0) === prevFrame)
    if (!prevObjs.length) { showToast(`第 ${prevFrame} 帧没有标注`); return }
    snapshotUndo()
    const newObjs = prevObjs.map((o) => ({
      ...o,
      id: `manual-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      frameIndex: currentFrame.value,
      timestampMs: Math.round(currentTime.value * 1000),
      source: 'manual' as const,
    }))
    annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: [...allObjs, ...newObjs] }
    statusMessage.value = `已从第 ${prevFrame} 帧复制 ${newObjs.length} 个标注`
  }

  // 亮度/对比度
  const brightness = ref(100)
  const contrast = ref(100)
  const mediaFilterStyle = computed(() => `brightness(${brightness.value}%) contrast(${contrast.value}%)`)
  const resetMediaFilter = () => { brightness.value = 100; contrast.value = 100 }

  // 标注进度统计
  const annotatedFrameCount = computed(() => {
    const mediaId = currentMediaId.value
    return new Set((annotationsByMedia.value[mediaId] ?? []).map((o) => o.frameIndex ?? 0)).size
  })

  const clearSelection = () => {
    if (isAiBusy.value) return
    selectedObjectId.value = null
    statusMessage.value = '已取消选择'
  }

  const openFilePicker = (type: 'image' | 'video') => {
    if (type === 'image') fileInputRef.value?.click()
    else videoInputRef.value?.click()
  }

  const handleFiles = async (files: FileList | null, type: 'image' | 'video') => {
    if (!files?.length) return
    let lastAddedId: string | null = null

    for (const file of Array.from(files)) {
      if (type === 'image' && !file.type.startsWith('image/')) continue
      if (type === 'video' && !file.type.startsWith('video/')) continue

      const media: MediaAsset = {
        id: `local-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`}`,
        name: file.name,
        type,
        url: URL.createObjectURL(file),
        fps: type === 'video' ? 30 : undefined,
        sizeBytes: file.size,
      }

      // 每个导入素材都创建独立的标注容器，绝不复用示例素材的数组。
      annotationsByMedia.value[media.id] = []
      mediaAssets.value = [...mediaAssets.value, media]
      lastAddedId = media.id

      // 视频必须先落到后端；后端以 MP4 文件名创建独立目录，后续 annotations / tracking JSON 都写入该目录。
      if (type === 'video') {
        try {
          const uploaded = await trackApi.uploadVideo(file)
          media.serverMediaId = uploaded.mediaId
          media.serverVideoName = uploaded.videoName
          media.url = uploaded.videoUrl
          if (uploaded.width) media.width = uploaded.width
          if (uploaded.height) media.height = uploaded.height
          if (uploaded.duration) media.duration = uploaded.duration
          if (uploaded.fps) media.fps = uploaded.fps
          trackingFramesByMedia.value[media.id] = []
        } catch (error) {
          console.error('视频上传到 Tracking 后端失败:', error)
          statusMessage.value = error instanceof Error ? error.message : '视频上传到后端失败'
        }
      } else {
        void api.uploadMedia({ file })
      }
    }

    if (lastAddedId) {
      // 先切换素材 ID，再等待 DOM 根据 key 创建全新的媒体元素，避免旧视频的异步事件覆盖新视频状态。
      selectedMediaId.value = lastAddedId
      await nextTick()
      await resetAnnotationViewForMedia(lastAddedId)
      const name = mediaAssets.value.find((m) => m.id === lastAddedId)?.name || ''
      statusMessage.value = `已导入并切换到新素材：${name}；已创建独立标注记录`
      showToast(`已导入「${name}」，标注记录已独立创建`)
    }
    if (type === 'image' && fileInputRef.value) fileInputRef.value.value = ''
    if (type === 'video' && videoInputRef.value) videoInputRef.value.value = ''
  }

  const onImageLoaded = () => {
    if (!imageRef.value) return
    if (selectedMedia.value) {
      selectedMedia.value.width = imageRef.value.naturalWidth
      selectedMedia.value.height = imageRef.value.naturalHeight
    }
  }

  const onVideoLoaded = async () => {
    const mediaId = currentMediaId.value
    const video = videoRef.value
    if (!video) return
    videoDuration.value = video.duration || 0
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media || mediaId !== currentMediaId.value || videoRef.value !== video) return
    media.duration = videoDuration.value
    // 优先用后端获取的真实分辨率（cv2 读取），避免浏览器 videoWidth 与编码分辨率不一致
    if (!media.width) media.width = video.videoWidth
    if (!media.height) media.height = video.videoHeight
    videoFps.value = media.fps || 30
    await ensureVideoFirstFrame(mediaId)
  }

  const loadTrackingResult = async (mediaId: string, force = false) => {
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media?.serverMediaId) return
    const serial = ++trackingLoadSerial
    try {
      const result = await trackApi.getResult(media.serverMediaId)
      if (serial !== trackingLoadSerial || mediaId !== currentMediaId.value) return
      const frames = 'frames' in result ? result.frames : [result]
      // 过滤掉前端已删除的 AI tracking 对象
      const deleted = deletedTrackingIds.value[mediaId]
      const filtered = deleted && deleted.size
        ? (frames || []).map((f: any) => ({ ...f, annotations: f.annotations.filter((a: any) => !deleted.has(a.id) && !deleted.has(String(a.objectId))) }))
        : (frames || [])
      trackingFramesByMedia.value[mediaId] = filtered

      // 将 AI tracking 结果合并到 annotationsByMedia（统一管理）
      // 策略：同帧同 objectId 的新结果覆盖旧结果，手动标注永不被覆盖
      const width = media.width || 1
      const height = media.height || 1
      const existing = annotationsByMedia.value[mediaId] ?? []
      // 手动标注的帧集合（AI 结果永远不要覆盖这些帧的手动标注）
      const manualFrames = new Set(existing.filter((o) => o.source === 'manual').map((o) => o.frameIndex ?? 0))
      // 按 (frameIndex, objectId) 建索引，方便覆盖
      const keyOf = (o: AnnotationObject) => `${o.frameIndex ?? 0}:${o.objectId ?? o.id}`
      const existingByKey = new Map(existing.map((o) => [keyOf(o), o]))
      // 先把手动标注全量放进去
      let merged: AnnotationObject[] = existing.filter((o) => o.source === 'manual').map((o) => ({ ...o }))

      for (const frame of filtered) {
        // 跳过有手动标注的帧（AI 和手动标注重叠）
        if (manualFrames.has(frame.frameIndex)) continue
        for (const ann of frame.annotations) {
          const [x1, y1, x2, y2] = ann.bbox ?? [0, 0, 0, 0]
          const key = `${frame.frameIndex}:${ann.objectId}`
          const newObj: AnnotationObject = {
            id: ann.id ?? `ai-${frame.frameIndex}-${ann.objectId}`,
            objectId: ann.objectId,
            name: ann.name ?? `object-${ann.objectId}`,
            source: 'ai',
            confidence: ann.confidence,
            bbox: {
              x: (x1 / width) * 100,
              y: (y1 / height) * 100,
              width: ((x2 - x1) / width) * 100,
              height: ((y2 - y1) / height) * 100,
            },
            frameIndex: frame.frameIndex,
            timestampMs: frame.timestampMs,
          } as AnnotationObject
          // 新结果覆盖旧结果（续接追踪时覆盖第一遍的结果）
          existingByKey.set(key, newObj)
        }
      }
      // 合并：手动标注 + 最终的 AI 标注
      const finalAi = [...existingByKey.values()].filter((o) => o.source === 'ai')
      merged.push(...finalAi)

      // ---------- 关键：用 AI tracking 的 objectId 补全手动标注 ----------
      // 策略：拿 AI 结果首帧的 bbox 和所有手动标注做 IoU 匹配
      const seedFrame = filtered.find((f: any) => f.annotations?.length)
      if (seedFrame) {
        const seedAiBoxes = seedFrame.annotations.map((a: any) => {
          const [x1, y1, x2, y2] = a.bbox ?? [0, 0, 0, 0]
          return {
            objectId: a.objectId,
            name: a.name,
            bbox: {
              x: (x1 / width) * 100,
              y: (y1 / height) * 100,
              width: ((x2 - x1) / width) * 100,
              height: ((y2 - y1) / height) * 100,
            },
          }
        })
        // IoU 计算
        const bboxIoU = (a: any, b: any) => {
          const ax2 = a.x + a.width, ay2 = a.y + a.height
          const bx2 = b.x + b.width, by2 = b.y + b.height
          const ix1 = Math.max(a.x, b.x), iy1 = Math.max(a.y, b.y)
          const ix2 = Math.min(ax2, bx2), iy2 = Math.min(ay2, by2)
          const iw = Math.max(0, ix2 - ix1), ih = Math.max(0, iy2 - iy1)
          const inter = iw * ih
          const areaA = a.width * a.height, areaB = b.width * b.height
          return areaA + areaB - inter > 0 ? inter / (areaA + areaB - inter) : 0
        }
        // 只处理没有 objectId 的手动标注
        merged = merged.map((obj) => {
          if (obj.source !== 'manual' || obj.objectId != null || !obj.bbox) return obj
          let bestIoU = 0, bestMatch: any = null
          for (const box of seedAiBoxes) {
            const iou = bboxIoU(obj.bbox, box.bbox)
            if (iou > bestIoU) { bestIoU = iou; bestMatch = box }
          }
          // IoU > 0.3 才认为是同一个物体
          if (bestMatch && bestIoU > 0.3) {
            return { ...obj, objectId: bestMatch.objectId }
          }
          return obj
        })
      }

      annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: merged }
    } catch {
      // 尚未生成 tracking_result.json 时静默处理：该帧不显示 AI 框。
    }
  }

  const syncVideoFrameState = () => {
    const video = videoRef.value
    if (!video) return
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0
    currentTime.value = time
    currentFrame.value = timeToFrame(time)
    frameInput.value = currentFrame.value
  }

  const scheduleVideoFrameSync = () => {
    const video = videoRef.value
    if (!video || typeof video.requestVideoFrameCallback !== 'function') return
    if (videoFrameCallbackId !== null) {
      try { video.cancelVideoFrameCallback(videoFrameCallbackId) } catch {}
    }
    const loop = (_now: number, metadata: VideoFrameCallbackMetadata) => {
      if (videoRef.value !== video) return
      currentTime.value = Number.isFinite(metadata.mediaTime) ? metadata.mediaTime : video.currentTime
      currentFrame.value = timeToFrame(currentTime.value)
      frameInput.value = currentFrame.value
      if (!video.paused && !video.ended) {
        videoFrameCallbackId = video.requestVideoFrameCallback(loop)
      }
    }
    videoFrameCallbackId = video.requestVideoFrameCallback(loop)
  }

  const onVideoTimeUpdate = () => {
    syncVideoFrameState()
    if (isVideo.value && Date.now() - lastTrackingPollAt > 1000) {
      lastTrackingPollAt = Date.now()
      void loadTrackingResult(currentMediaId.value, true)
    }
    scheduleVideoFrameSync()
  }

  const seekVideo = async (time: number) => {
    if (!videoRef.value) return
    const safeTime = Math.max(0, Math.min(videoDuration.value || 0, time))
    videoRef.value.currentTime = safeTime
    currentTime.value = safeTime
    currentFrame.value = timeToFrame(safeTime)
    if (isVideo.value && Date.now() - lastTrackingPollAt > 1000) { lastTrackingPollAt = Date.now(); void loadTrackingResult(currentMediaId.value, true) }
    await nextTick()
    syncVideoFrameState()
    scheduleVideoFrameSync()
  }

  const seekToInputFrame = async () => {
    if (!isVideo.value) return
    const frame = Math.max(0, Math.min(maxFrameIndex.value, Math.floor(Number(frameInput.value) || 0)))
    frameInput.value = frame
    await seekVideo(frameToTime(frame))
    await loadTrackingResult(currentMediaId.value, true)
    statusMessage.value = `已跳转到第 ${frame} 帧`
  }

  const seekByFrame = async (delta: number) => {
    if (!isVideo.value) return
    await seekVideo(currentTime.value + delta / videoFps.value)
  }

  const togglePlayback = async () => {
    if (!videoRef.value) return
    if (videoRef.value.paused) {
      await videoRef.value.play()
      isPlaying.value = true
      scheduleVideoFrameSync()
    } else {
      videoRef.value.pause()
      isPlaying.value = false
      syncVideoFrameState()
    }
  }

  const onVideoEnded = () => {
    isPlaying.value = false
    if (videoFrameCallbackId !== null && videoRef.value && typeof videoRef.value.cancelVideoFrameCallback === 'function') {
      try { videoRef.value.cancelVideoFrameCallback(videoFrameCallbackId) } catch {}
      videoFrameCallbackId = null
    }
    syncVideoFrameState()
  }

  const onTimelineClick = async (event: MouseEvent) => {
    if (!isVideo.value || !videoDuration.value) return
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
    await seekVideo(ratio * videoDuration.value)
  }

  const runAiSegment = async () => {
    const mediaId = currentMediaId.value
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media) return
    const mediaType = media.type
    isAiBusy.value = true
    statusMessage.value = mediaType === 'video' ? 'AI 正在处理视频第一帧……' : 'AI 正在处理图片……'
    try {
      const result = await api.segment({
        mediaId,
        mediaType,
        prompt: objectNameInput.value.trim() || 'rare sperm',
        frameIndex: mediaType === 'video' ? currentFrame.value : undefined,
        timestampMs: mediaType === 'video' ? Math.round(currentTime.value * 1000) : undefined,
      })
      // 关键隔离：AI 返回期间如果用户切换了素材，绝不能把旧素材的结果写到新素材。
      if (mediaId !== currentMediaId.value) return
      const existing = annotationsByMedia.value[mediaId] ?? []
      annotationsByMedia.value[mediaId] = [...existing, ...result.objects]
      selectedObjectId.value = result.objects[0]?.id ?? null
      statusMessage.value = `AI 完成：${result.objects.length} 个目标${mediaType === 'video' ? '（第一帧）' : ''}`
    } catch (error) {
      if (mediaId === currentMediaId.value) statusMessage.value = error instanceof Error ? error.message : 'AI 处理失败'
    } finally {
      isAiBusy.value = false
    }
  }

  const buildCurrentFrameSam3Objects = async (mediaId: string) => {
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media) return []
    const objects = (annotationsByMedia.value[mediaId] ?? []).filter((obj) => (obj.frameIndex ?? 0) === currentFrame.value)
    if (!objects.length) throw new Error(`第 ${currentFrame.value} 帧没有人工标注`)
    const { width, height } = await getMediaPixelSize(media)
    const clampX = (v: number) => Math.min(width, Math.max(0, v))
    const clampY = (v: number) => Math.min(height, Math.max(0, v))
    return objects.map((obj) => {
      const item: Record<string, unknown> = {
        id: obj.id, name: obj.name, source: obj.source,
        frameIndex: currentFrame.value,
        timestampMs: Math.round(currentTime.value * 1000),
      }
      if (obj.bbox) {
        item.bbox = [
          Math.round(clampX(obj.bbox.x / 100 * width)),
          Math.round(clampY(obj.bbox.y / 100 * height)),
          Math.round(clampX((obj.bbox.x + obj.bbox.width) / 100 * width)),
          Math.round(clampY((obj.bbox.y + obj.bbox.height) / 100 * height)),
        ]
      }
      if (obj.point) item.point = [
        Math.round(clampX(obj.point.x / 100 * width)),
        Math.round(clampY(obj.point.y / 100 * height)),
      ]
      return item
    }).filter((item: any) => Array.isArray(item.bbox) && item.bbox.length === 4)
  }

  const findNextUnannotatedFrame = (fromFrame = currentFrame.value) => {
    const all = annotationsByMedia.value[currentMediaId.value] ?? []
    const annotatedFrames = new Set(all.filter((o) => o.bbox).map((o) => o.frameIndex ?? 0))
    for (let frame = Math.max(0, fromFrame); frame <= maxFrameIndex.value; frame += 1) {
      if (!annotatedFrames.has(frame)) return frame
    }
    return Math.min(Math.max(0, fromFrame), maxFrameIndex.value)
  }

  const goToNextUnannotatedFrame = async () => {
    if (!isVideo.value) return
    const next = findNextUnannotatedFrame(currentFrame.value + 1)
    await seekVideo(frameToTime(next))
    statusMessage.value = next === currentFrame.value ? `当前已是最后可标注帧：${next}` : `下一个未标注帧：${next}`
  }

  const runAiTrack = async () => {
    const mediaId = currentMediaId.value
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media || media.type !== 'video' || !media.serverMediaId) {
      statusMessage.value = '请先上传视频'
      return
    }
    const startFrame = currentFrame.value
    const seed = await buildCurrentFrameSam3Objects(mediaId)
    if (!seed.length) {
      statusMessage.value = `第 ${startFrame} 帧没有人工 bbox 标注`
      return
    }

    // 重置异常状态：新一轮 tracking 开始时清空之前的异常高亮/标记
    anomalyFrames.value = []
    anomalyObjectIds.value = []

    isAiBusy.value = true
    statusMessage.value = `SAM3 已提交：从第 ${startFrame} 帧追踪 ${trackingFrameCount.value} 帧`
    try {
      const task = await trackApi.run({
        mediaId: media.serverMediaId,
        mediaName: media.name,
        mediaWidth: media.width || 0,
        mediaHeight: media.height || 0,
        startFrame,
        annotations: seed,
      })
      trackingFrameCount.value = task.maxFrames || task.trackFrames || trackingFrameCount.value
      statusMessage.value = `SAM3 正在运行：第 ${startFrame} 帧开始处理 ${trackingFrameCount.value} 帧`
      for (;;) {
        const status = await trackApi.getStatus(task.taskId)
        if (status.status === 'success') break
        if (status.status === 'failed') throw new Error(status.message || 'SAM3 Tracking 失败')
        if (status.status === 'paused' && status.paused) {
          // 异常暂停 → 跳到异常帧，高亮异常 objectId
          const pauseFrame = status.pausedFrame ?? startFrame
          // 先加载已完成的 tracking 结果
          await loadTrackingResult(mediaId, true)
          // 跳到异常帧并设置异常高亮/标记
          if (mediaId === currentMediaId.value) {
            await seekVideo(frameToTime(pauseFrame))
            // 设置异常 objectId 高亮
            const levels = status.anomalyLevels || {}
            anomalyObjectIds.value = Object.entries(levels)
              .filter(([, v]) => v !== 'normal')
              .map(([k]) => Number(k))
            // 推入异常帧列表（给时间轴标记用）
            const pausedObjs = status.pausedObjects || []
            const reasons = pausedObjs.map((o) => `${o.type}(oid:${o.object_id})`)
            const level = Object.values(levels).find((v) => v && v !== 'normal') || 'anomaly'
            anomalyFrames.value = [
              ...anomalyFrames.value,
              { frame_index: pauseFrame, level, reasons },
            ]
            statusMessage.value = `⚠️ Tracking 暂停 @ frame ${pauseFrame}: ${reasons.join('; ')}`
            showToast(`检测到异常，已暂停在第 ${pauseFrame} 帧`)
          }
          break // 跳出轮询，等用户修正后重新点 AI Tracking
        }
        await new Promise((resolve) => setTimeout(resolve, 700))
      }
      await loadTrackingResult(mediaId, true)
      if (mediaId === currentMediaId.value) {
        const next = findNextUnannotatedFrame(startFrame + 1)
        await seekVideo(frameToTime(next))
        statusMessage.value = `Tracking 完成：${startFrame}～${Math.min(startFrame + trackingFrameCount.value - 1, maxFrameIndex.value)}`
        showToast('SAM3 追踪完成')
      }
    } catch (error) {
      if (mediaId === currentMediaId.value) {
        statusMessage.value = error instanceof Error ? error.message : 'AI Tracking 失败'
        showToast(statusMessage.value)
      }
    } finally {
      isAiBusy.value = false
    }
  }


  const selectSaveFolder = async () => {
    const picker = (window as any).showDirectoryPicker
    if (!picker) {
      statusMessage.value = '当前浏览器不支持目录授权，将使用普通下载方式'
      return
    }
    try {
      saveFolderHandle.value = await picker({ mode: 'readwrite' })
      statusMessage.value = `保存目录：${saveFolderHandle.value.name}`
    } catch {
      statusMessage.value = '已取消目录选择'
    }
  }

  const pad = (n: number, width = 2) => String(n).padStart(width, '0')
  const fileTimestamp = () => {
    const d = new Date()
    return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}_${pad(d.getMilliseconds(), 3)}`
  }

  const drawAnnotationOverlayToCanvas = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
    const objects = currentObjects.value
    ctx.save()
    ctx.lineWidth = Math.max(2, width / 900)
    ctx.font = `${Math.max(14, width / 80)}px sans-serif`
    ctx.textBaseline = 'bottom'
    objects.forEach((obj) => {
      if (obj.bbox) {
        const x = obj.bbox.x / 100 * width
        const y = obj.bbox.y / 100 * height
        const w = obj.bbox.width / 100 * width
        const h = obj.bbox.height / 100 * height
        ctx.fillStyle = 'rgba(99,102,241,0.12)'
        ctx.strokeStyle = '#818cf8'
        ctx.fillRect(x, y, w, h)
        ctx.strokeRect(x, y, w, h)
        ctx.fillStyle = '#ffffff'
        ctx.fillText(obj.name, x, Math.max(16, y - 4))
      } else if (obj.point) {
        const x = obj.point.x / 100 * width
        const y = obj.point.y / 100 * height
        ctx.beginPath()
        ctx.arc(x, y, Math.max(4, width / 180), 0, Math.PI * 2)
        ctx.fillStyle = '#818cf8'
        ctx.fill()
        ctx.fillStyle = '#ffffff'
        ctx.fillText(obj.name, x + 8, y - 6)
      }
    })
    ctx.restore()
  }

  const svgToCanvas = async (): Promise<Blob> => {
    const canvas = document.createElement('canvas')
    const mediaWidth = selectedMedia.value?.width || (isVideo.value ? 1280 : 1600)
    const mediaHeight = selectedMedia.value?.height || (isVideo.value ? 720 : 900)
    canvas.width = mediaWidth
    canvas.height = mediaHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('无法创建 Canvas')

    if (isVideo.value) {
      await ensureVideoFirstFrame()
      if (!videoRef.value || videoRef.value.readyState < 2) throw new Error('视频第一帧尚未准备完成，请稍后再保存')
      ctx.drawImage(videoRef.value, 0, 0, mediaWidth, mediaHeight)
    } else if (imageRef.value) {
      ctx.drawImage(imageRef.value, 0, 0, mediaWidth, mediaHeight)
    }

    drawAnnotationOverlayToCanvas(ctx, mediaWidth, mediaHeight)
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('PNG 导出失败')), 'image/png')
    })
  }

  const renderAnnotatedVideo = async (): Promise<Blob> => {
    if (!videoRef.value) throw new Error('视频元素不存在')
    const sourceVideo = videoRef.value
    if (!Number.isFinite(sourceVideo.duration) || sourceVideo.duration <= 0) throw new Error('视频尚未加载完成，请稍后再试')
    if (!('captureStream' in HTMLVideoElement.prototype) || typeof MediaRecorder === 'undefined') {
      throw new Error('当前浏览器不支持前端视频导出，请使用最新版 Chrome 或 Edge')
    }

    const width = sourceVideo.videoWidth || selectedMedia.value?.width || 1280
    const height = sourceVideo.videoHeight || selectedMedia.value?.height || 720
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('无法创建视频渲染画布')

    const canvasStream = canvas.captureStream(videoFps.value || 30)
    const sourceStream = (sourceVideo as HTMLVideoElement & { captureStream: () => MediaStream }).captureStream()
    sourceStream.getAudioTracks().forEach((track) => canvasStream.addTrack(track))

    const mimeCandidates = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
    const mimeType = mimeCandidates.find((type) => MediaRecorder.isTypeSupported(type)) || ''
    const recorder = new MediaRecorder(canvasStream, mimeType ? { mimeType } : undefined)
    const chunks: Blob[] = []
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data) }

    const mediaId = currentMediaId.value
    const wasMuted = sourceVideo.muted
    sourceVideo.muted = false
    sourceVideo.pause()
    sourceVideo.currentTime = 0
    await new Promise<void>((resolve) => {
      if (sourceVideo.readyState >= 2) resolve()
      else sourceVideo.addEventListener('loadeddata', () => resolve(), { once: true })
    })
    if (mediaId !== currentMediaId.value) throw new Error('素材已切换，已取消保存')

    let firstFrameDrawn = false
    const drawLoop = () => {
      if (mediaId !== currentMediaId.value) return
      ctx.drawImage(sourceVideo, 0, 0, width, height)
      // 人工标注只存在于第一帧：导出视频时把标注写入第一帧，其余帧保持原视频。
      if (!firstFrameDrawn) {
        drawAnnotationOverlayToCanvas(ctx, width, height)
        firstFrameDrawn = true
      }
      if (!sourceVideo.ended) requestAnimationFrame(drawLoop)
    }

    recorder.start(100)
    const recordingDone = new Promise<Blob>((resolve) => {
      recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType || 'video/webm' }))
    })
    sourceVideo.addEventListener('ended', () => { if (recorder.state !== 'inactive') recorder.stop() }, { once: true })
    requestAnimationFrame(drawLoop)
    await sourceVideo.play()
    const blob = await recordingDone
    sourceVideo.muted = wasMuted
    sourceVideo.pause()
    sourceVideo.currentTime = 0
    return blob
  }

  const getMediaPixelSize = async (media: MediaAsset) => {
    if (media.type === 'video') {
      const video = videoRef.value
      if (!video || video.videoWidth <= 0 || video.videoHeight <= 0) {
        throw new Error('视频实际宽高尚未获取，请等待视频加载完成后再生成 JSON')
      }
      return { width: video.videoWidth, height: video.videoHeight }
    }

    const image = imageRef.value
    if (image && image.naturalWidth > 0 && image.naturalHeight > 0) {
      return { width: image.naturalWidth, height: image.naturalHeight }
    }
    if ((media.width || 0) > 0 && (media.height || 0) > 0) {
      return { width: media.width as number, height: media.height as number }
    }
    throw new Error('图片实际宽高尚未获取，请等待图片加载完成后再生成 JSON')
  }

  /**
   * 将前端 0~100% 百分比坐标转换为 SAM3 所需的像素坐标。
   * bbox: [x1, y1, x2, y2]
   * point: [x, y]
   */
  const buildSam3AnnotationsJson = async (mediaId: string) => {
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media) throw new Error('当前素材不存在，无法生成 JSON')
    const objects = annotationsByMedia.value[mediaId] ?? []
    if (!objects.length) throw new Error('当前素材没有可导出的标注')

    const { width, height } = await getMediaPixelSize(media)
    const frameIndex = media.type === 'video' ? 0 : 0
    const timestampMs = 0

    const clampX = (value: number) => Math.min(width, Math.max(0, value))
    const clampY = (value: number) => Math.min(height, Math.max(0, value))
    const round = (value: number) => Math.round(value)

    const annotations = objects.map((obj) => {
      const item: Record<string, unknown> = {
        id: obj.id,
        object_id: obj.objectId,
        name: obj.name,
        source: obj.source,
      }

      if (obj.bbox) {
        const x1 = clampX((obj.bbox.x / 100) * width)
        const y1 = clampY((obj.bbox.y / 100) * height)
        const x2 = clampX(((obj.bbox.x + obj.bbox.width) / 100) * width)
        const y2 = clampY(((obj.bbox.y + obj.bbox.height) / 100) * height)
        item.bbox = [round(x1), round(y1), round(x2), round(y2)]
      }

      if (obj.point) {
        const x = clampX((obj.point.x / 100) * width)
        const y = clampY((obj.point.y / 100) * height)
        item.point = [round(x), round(y)]
      }

      if (media.type === 'video') {
        item.frameIndex = frameIndex
        item.timestampMs = timestampMs
      }
      return item
    })

    const payload = {
      version: '1.0',
      format: 'sam3-annotations',
      media: {
        id: media.id,
        name: media.name,
        type: media.type,
        width,
        height,
      },
      frame: {
        frameIndex,
        timestampMs,
      },
      coordinateSystem: {
        source: 'frontend-percent',
        target: 'pixel',
        bbox: '[x1, y1, x2, y2]',
      },
      annotations,
    }

    return new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  }

  const generateAnnotationsJson = async () => {
    const mediaId = currentMediaId.value
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    if (!media) return showToast('当前素材不存在')
    try {
      const { width, height } = await getMediaPixelSize(media)
      if (media.type === 'video') {
        if (!media.serverMediaId) throw new Error('该视频尚未成功上传到后端，请重新上传视频')
        const annotations = await buildCurrentFrameSam3Objects(mediaId)
        const frameIndex = currentFrame.value
        const timestampMs = Math.round(currentTime.value * 1000)
        statusMessage.value = `正在保存第 ${frameIndex} 帧 annotations.json……`
        const result = await trackApi.saveFrameAnnotations({
          mediaId: media.serverMediaId,
          mediaName: media.name,
          mediaWidth: width,
          mediaHeight: height,
          frameIndex,
          timestampMs,
          annotations,
        })
        statusMessage.value = `第 ${frameIndex} 帧 JSON 已写入 ${result.filename}`
        showToast(`第 ${frameIndex} 帧 JSON 已保存到视频目录`)
      } else {
        const blob = await buildSam3AnnotationsJson(mediaId)
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url; link.download = 'annotations.json'; link.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
        statusMessage.value = 'JSON 生成成功：annotations.json'
        showToast('图片 annotations.json 已生成')
      }
    } catch (error) {
      statusMessage.value = error instanceof Error ? error.message : 'JSON 生成失败'
      showToast(statusMessage.value)
    }
  }


  const saveBlobToSelectedFolder = async (blob: Blob, filename: string) => {
    const handle = saveFolderHandle.value
    if (!handle) return false
    const fileHandle = await handle.getFileHandle(filename, { create: true })
    const writable = await fileHandle.createWritable()
    await writable.write(blob)
    await writable.close()
    return true
  }

  /**
   * 保存标注。
   *
   * 改动要点（接入后端后）：
   * 1. 结构化数据【最先】提交到后端 —— 视频导出耗时长且可能失败，
   *    不能让它挡住最核心的落库动作。
   * 2. 视频/图片导出单独 try-catch —— 导出失败不影响标注数据已入库。
   */
  const saveAnnotation = async () => {
    const mediaId = currentMediaId.value
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    const objects = annotationsByMedia.value[mediaId] ?? []
    if (!media) {
      statusMessage.value = '当前素材不存在，无法保存'
      return
    }
    if (!objects.length) {
      statusMessage.value = '当前素材没有可保存的标注'
      showToast('当前素材没有标注内容')
      return
    }

    const timestamp = fileTimestamp()

    try {
      // ---------- ① 先提交结构化数据到后端（核心，不受视频导出影响）----------
      // 取真实像素宽高：前端存的是百分比，后端需要据此换算成 SAM3 像素坐标
      const pixel = await getMediaPixelSize(media).catch(() => null)

      await api.saveManualAnnotation({
        mediaId,
        mediaType: media.type,
        mediaName: media.name,
        mediaWidth: pixel?.width ?? media.width,
        mediaHeight: pixel?.height ?? media.height,
        frameIndex: media.type === 'video' ? currentFrame.value : undefined,
        timestampMs: media.type === 'video' ? Math.round(currentTime.value * 1000) : undefined,
        objects: JSON.parse(JSON.stringify(objects)),
        annotationVersion: 'annotation-v5-video-current-frame',
      })

      // 视频保存时同步写入 seed JSON（AI Tracking 需要）
      if (media.type === 'video' && media.serverMediaId) {
        try {
          const seedAnnotations = await buildCurrentFrameSam3Objects(mediaId)
          if (seedAnnotations.length) {
            await trackApi.saveFrameAnnotations({
              mediaId: media.serverMediaId,
              mediaName: media.name,
              mediaWidth: pixel?.width ?? media.width ?? 0,
              mediaHeight: pixel?.height ?? media.height ?? 0,
              frameIndex: currentFrame.value,
              timestampMs: Math.round(currentTime.value * 1000),
              annotations: seedAnnotations,
            })
          }
        } catch {
          // seed JSON 写入失败不阻塞主保存流程
        }
      }

      // ---------- ② 再导出带标注的媒体文件（耗时，失败不影响上面）----------
      let blob: Blob | null = null
      let filename = media.type === 'video' ? `${timestamp}_annotated.webm` : `${timestamp}_annotated.png`
      let exportFailed = false

      try {
        if (media.type === 'video') {
          statusMessage.value = '标注已保存到后端，正在生成带第一帧标注的视频，请稍候……'
          blob = await renderAnnotatedVideo()
        } else {
          statusMessage.value = '标注已保存到后端，正在生成标注图片……'
          blob = await svgToCanvas()
        }
      } catch (exportError) {
        exportFailed = true
        console.warn('媒体导出失败（标注数据已入库）：', exportError)
      }

      if (blob) {
        let savedToFolder = false
        if (!saveFolderHandle.value) await selectSaveFolder()
        if (saveFolderHandle.value) {
          try {
            savedToFolder = await saveBlobToSelectedFolder(blob, filename)
          } catch {
            savedToFolder = false
          }
        }
        if (!savedToFolder) {
          const url = URL.createObjectURL(blob)
          const link = document.createElement('a')
          link.href = url
          link.download = filename
          link.click()
          setTimeout(() => URL.revokeObjectURL(url), 1000)
        }
      }

      // ---------- ③ 更新本地结果列表 ----------
      savedResults.value.unshift({
        mediaId,
        mediaName: media.name,
        mediaType: media.type,
        frameIndex: media.type === 'video' ? currentFrame.value : undefined,
        timestampMs: media.type === 'video' ? Math.round(currentTime.value * 1000) : undefined,
        savedAt: new Date().toISOString(),
        filename: exportFailed ? '' : filename,
        objects: JSON.parse(JSON.stringify(objects)),
      } as SavedAnnotationFile)

      if (exportFailed) {
        statusMessage.value = `标注已保存到后端；媒体文件导出失败：${filename}`
        showToast('标注数据已保存到后端，但视频/图片导出失败')
      } else {
        statusMessage.value = `保存成功：${filename}`
        showToast(media.type === 'video' ? '保存成功：已生成带第一帧标注的视频' : '保存成功：标注图片已保存')
      }
    } catch (error) {
      statusMessage.value = error instanceof Error ? error.message : '保存失败'
      showToast(statusMessage.value)
    }
  }

  /**
   * 导出训练数据集（COCO / YOLO / both）
   * 接收格式选择 + 划分设置，从 annotationsByMedia 中提取当前素材所有帧标注发给后端，
   * 后端抽视频帧、转格式、打包 zip 返回。
   */
  const exportDataset = async (opts: {
    format: 'coco' | 'yolo' | 'both'
    splitRatio: number   // 0 = 不划分，0.8 = 80/20
  }) => {
    const mediaId = currentMediaId.value
    const media = mediaAssets.value.find((item) => item.id === mediaId)
    const objects = annotationsByMedia.value[mediaId] ?? []

    if (!media) { showToast('请先选择素材'); return }
    if (!objects.length) { showToast('当前素材没有标注可导出'); return }

    const pixel = await getMediaPixelSize(media).catch(() => null)
    const allFrames = new Set(objects.map((o: any) => o.frameIndex ?? 0))
    const allNames = Array.from(new Set(objects.map((o: any) => o.name).filter(Boolean))) as string[]

    isAiBusy.value = true
    statusMessage.value = `正在导出数据集（${allFrames.size} 帧 × ${allNames.length} 类别）...`

    try {
      const blob = await apiExportDataset({
        mediaId,
        mediaType: media.type,
        mediaName: media.name,
        mediaWidth: pixel?.width ?? media.width,
        mediaHeight: pixel?.height ?? media.height,
        format: opts.format,
        splitRatio: opts.splitRatio,
        classNames: allNames,
        annotations: JSON.parse(JSON.stringify(objects)),
      })

      // 下载 zip
      const filename = `${media.name}_dataset.zip`
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      setTimeout(() => URL.revokeObjectURL(url), 2000)

      const size_mb = (blob.size / 1024 / 1024).toFixed(2)
      statusMessage.value = `✅ 数据集已导出：${filename} (${size_mb} MB, ${allFrames.size} 帧)`
      showToast(`导出成功：${filename}`)
    } catch (error: any) {
      statusMessage.value = `导出失败：${error?.message ?? '未知错误'}`
      showToast(statusMessage.value)
    } finally {
      isAiBusy.value = false
    }
  }

  const openEffectFolderPicker = () => {
    effectFolderInputRef.value?.click()
  }

  const handleEffectFolder = (files: FileList | null) => {
    if (!files?.length) return
    const videos = Array.from(files).filter((file) => file.type.startsWith('video/'))
    if (!videos.length) {
      statusMessage.value = '所选文件夹中没有可查看的视频文件'
      return
    }

    const now = Date.now()
    const localEffects: EffectResult[] = videos.map((file, index) => ({
      id: `local-effect-${now}-${index}`,
      sourceMediaId: `local-effect-media-${now}-${index}`,
      sourceMediaName: file.name,
      resultName: `${file.name} · 本地效果查看`,
      resultVideoUrl: URL.createObjectURL(file),
      createdAt: new Date().toISOString(),
      status: 'completed',
      summary: { detectedObjects: 0, extractedCandidates: 0, durationSeconds: 0 },
    }))

    effectResults.value = [...localEffects, ...effectResults.value]
    selectedEffectId.value = localEffects[0]?.id ?? selectedEffectId.value
    statusMessage.value = `已加载 ${videos.length} 个本地视频，可直接查看效果`
  }

  const loadEffects = async () => {
    try {
      const result = await api.listEffects()
      effectResults.value = result.items
      selectedEffectId.value = result.items[0]?.id ?? null
    } catch (error) {
      statusMessage.value = error instanceof Error ? error.message : '效果列表加载失败'
    }
  }

  const onEffectTimeUpdate = () => {
    if (effectVideoRef.value) effectTime.value = effectVideoRef.value.currentTime
  }

  const toggleEffectPlayback = async () => {
    if (!effectVideoRef.value) return
    if (effectVideoRef.value.paused) {
      await effectVideoRef.value.play()
      effectPlaying.value = true
    } else {
      effectVideoRef.value.pause()
      effectPlaying.value = false
    }
  }

  const selectEffect = (effectId: string) => {
    selectedEffectId.value = effectId
    effectTime.value = 0
    effectPlaying.value = false
    nextTick(() => {
      if (effectVideoRef.value) {
        effectVideoRef.value.pause()
        effectVideoRef.value.currentTime = 0
      }
    })
  }

  const effectOverlayObjects = computed(() => {
    if (!selectedEffect.value) return []
    const t = effectTime.value
    return Array.from({ length: Math.min(4, selectedEffect.value.summary.detectedObjects) }, (_, index) => ({
      id: `effect-${index}`,
      x: 22 + ((t * (4 + index) + index * 17) % 58),
      y: 25 + ((Math.sin(t * 1.5 + index) + 1) * 22),
      width: 6 + index * 0.5,
      height: 5 + index * 0.4,
    }))
  })

  const resetAnnotationViewForMedia = async (mediaId = currentMediaId.value) => {
    selectedObjectId.value = null
    currentFrame.value = 0
    currentTime.value = 0
    frameInput.value = 0
    tempBbox.value = null
    bboxStart = null
    activeTool.value = 'point'
    anomalyObjectIds.value = []
    zoom.value = 1
    if (isVideo.value && mediaId === currentMediaId.value) {
      await nextTick()
      const video = videoRef.value
      if (video) {
        try { video.currentTime = 0 } catch {}
      }
    }
  }

  /**
   * 启动时从后端加载已有视频素材，刷新页面后素材列表不丢失。
   * 关键修复：通过 serverMediaId 匹配已持久化（localStorage）的素材，
   * 复用原 id（如 local-xxx），这样 annotationsByMedia 的 key 才能对上。
   */
  const loadServerMedia = async () => {
    try {
      const res = await trackApi.listMedia()
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
          if (item.fps) existing.fps = item.fps
          if (item.width) existing.width = item.width
          if (item.height) existing.height = item.height
          if (item.frameCount && item.fps) existing.duration = item.frameCount / item.fps

          // 异步加载 tracking 结果并合并（不阻塞）
          if (item.hasTrackingResult) {
            void loadTrackingResult(existing.id)
          }
          continue
        }
        // 新素材：创建条目
        const newAsset: MediaAsset = {
          id: `server-${item.mediaId}`,
          serverMediaId: item.mediaId,
          name: item.videoName,
          type: 'video',
          url: item.videoUrl,
          fps: item.fps || undefined,
          width: item.width || undefined,
          height: item.height || undefined,
          duration: item.frameCount && item.fps ? item.frameCount / item.fps : undefined,
        }
        additions.push(newAsset)
        trackingFramesByMedia.value[newAsset.id] = []
        if (item.hasTrackingResult) {
          void trackApi.getResult(item.mediaId).then((result) => {
            const frames = 'frames' in result ? result.frames : [result]
            trackingFramesByMedia.value[newAsset.id] = frames || []
          }).catch(() => {})
        }
      }
      if (additions.length) {
        mediaAssets.value = [...mediaAssets.value, ...additions]
      }

      // 如果当前选中的 media 不存在了（被删），重置到第一个
      if (selectedMediaId.value && !mediaAssets.value.find((m) => m.id === selectedMediaId.value)) {
        selectedMediaId.value = mediaAssets.value[0]?.id ?? ''
      }
    } catch {
      // 后端未启动时静默处理
    }
  }

  return {
    api, mediaAssets, selectedMediaId, activeTool, objectNameInput, selectedObjectId, currentFrame, currentTime, videoDuration, videoFps, frameInput, isPlaying, isAiBusy, trackingFrameCount, statusMessage, toastMessage, showToast, zoom, zoomIn, zoomOut, zoomReset, deleteMedia, saveFolderHandle, savedResults, effectResults, selectedEffectId, effectTime, effectPlaying, effectVideoRef, imageRef, videoRef, annotationHitRef, fileInputRef, videoInputRef, effectFolderInputRef, annotationsByMedia, trackingFramesByMedia, anomalyObjectIds, anomalyFrames, selectedMedia, isVideo, maxFrameIndex, currentMediaId, currentObjects, selectedObject, selectedEffect, formatTime, timeToFrame, frameToTime, getStagePoint, addObject, resetVideoViewToFirstFrame, ensureVideoFirstFrame, selectTool, onStageClick, tempBbox, onBboxDown, onBboxMove, onBboxUp, onObjectDropdownChange, selectObject, removeObject, renameObject, undo, redo, copyPreviousFrame, brightness, contrast, mediaFilterStyle, resetMediaFilter, annotatedFrameCount, clearSelection, openFilePicker, handleFiles, onImageLoaded, onVideoLoaded, onVideoTimeUpdate, loadTrackingResult, seekVideo, seekToInputFrame, seekByFrame, togglePlayback, onVideoEnded, onTimelineClick, runAiSegment, runAiTrack, selectSaveFolder, pad, fileTimestamp, drawAnnotationOverlayToCanvas, svgToCanvas, renderAnnotatedVideo, getMediaPixelSize, buildSam3AnnotationsJson, generateAnnotationsJson, saveBlobToSelectedFolder, saveAnnotation, exportDataset, openEffectFolderPicker, handleEffectFolder, loadEffects, onEffectTimeUpdate, toggleEffectPlayback, selectEffect, effectOverlayObjects, resetAnnotationViewForMedia, loadServerMedia
  }
}

const workspace = createWorkspace()
export const useWorkspace = () => workspace
