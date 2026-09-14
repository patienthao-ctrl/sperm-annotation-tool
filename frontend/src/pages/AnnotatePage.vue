<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useWorkspace } from '../stores/workspace'

const {
  mediaAssets, selectedMediaId, activeTool, objectNameInput, selectedObjectId,
  currentFrame, currentTime, videoDuration, videoFps, frameInput, isPlaying,
  isAiBusy, statusMessage, toastMessage,
  imageRef, videoRef, annotationHitRef, fileInputRef, videoInputRef,
  annotationsByMedia, selectedMedia, isVideo, maxFrameIndex, currentMediaId,
  currentObjects, selectedObject, anomalyObjectIds, anomalyFrames, formatTime, addObject, resetVideoViewToFirstFrame, trackingFrameCount,
  ensureVideoFirstFrame, selectTool, onStageClick, tempBbox, onBboxDown, onBboxMove,
  onBboxUp, onObjectDropdownChange, selectObject, removeObject, renameObject, undo, redo,
  copyPreviousFrame, brightness, contrast, mediaFilterStyle, resetMediaFilter, annotatedFrameCount,
  clearSelection, openFilePicker, handleFiles, onImageLoaded, onVideoLoaded,
  onVideoTimeUpdate, seekToInputFrame, togglePlayback, onVideoEnded,
  onTimelineClick, runAiSegment, runAiTrack, selectSaveFolder, generateAnnotationsJson,
  saveAnnotation, exportDataset, resetAnnotationViewForMedia, seekByFrame,
  zoom, zoomIn, zoomOut, zoomReset, deleteMedia,
} = useWorkspace()

// 画布滚动容器
const scrollContainerRef = ref<HTMLDivElement | null>(null)
const containerW = ref(0)
const containerH = ref(0)
// 关键：contain 基准只在素材切换或首次有尺寸时锁定一次，
// 后续容器尺寸变化（如底部播放条出现/消失）不再影响 zoom 对应的实际画面大小
const containBase = ref<{ w: number; h: number } | null>(null)
let resizeObserver: ResizeObserver | null = null

// ── 导出训练数据集弹窗 ──
const exportDialogOpen = ref(false)
const exportFormat = ref<'coco' | 'yolo' | 'both'>('coco')
const exportSplitRatio = ref(0.8)   // 0 = 不划分
const doingExport = ref(false)

const allFrameCount = computed(() => {
  const objs = annotationsByMedia.value[currentMediaId.value] ?? []
  return new Set(objs.map((o: any) => o.frameIndex ?? 0)).size
})
const allClassNameList = computed(() => {
  const objs = annotationsByMedia.value[currentMediaId.value] ?? []
  return Array.from(new Set(objs.map((o: any) => o.name).filter(Boolean))) as string[]
})

async function doExportDataset() {
  if (doingExport.value) return
  doingExport.value = true
  try {
    await exportDataset({ format: exportFormat.value, splitRatio: exportSplitRatio.value })
    exportDialogOpen.value = false
  } finally {
    doingExport.value = false
  }
}

const onKeyDown = (e: KeyboardEvent) => {
  // 输入框内不触发快捷键
  const tag = (e.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  if (isAiBusy.value) return

  switch (e.key) {
    case 'ArrowLeft':
      e.preventDefault()
      seekByFrame(-1)
      break
    case 'ArrowRight':
      e.preventDefault()
      seekByFrame(1)
      break
    case 'Delete':
    case 'Backspace':
      if (selectedObjectId.value) {
        e.preventDefault()
        removeObject(selectedObjectId.value)
      }
      break
    case 'z': case 'Z':
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); undo() }
      break
    case 'y': case 'Y':
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); redo() }
      break
    case 's': case 'S':
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); saveAnnotation() }
      break
    case 'c': case 'C':
      if (isVideo.value) copyPreviousFrame()
      break
    case 'p': case 'P':
      selectTool('point')
      break
    case 'b': case 'B':
      selectTool('bbox')
      break
    case 'Escape':
      clearSelection()
      break
  }
}

// 对象颜色调色板（按 objectId 取色，选中时返回金色）
const PALETTE = ['#60a5fa', '#34d399', '#f472b6', '#fbbf24', '#a78bfa', '#fb923c', '#22d3ee', '#e879f9', '#84cc16', '#f87171']
const objColor = (objectId: number, selected: boolean) => {
  if (selected) return '#fbbf24'
  return PALETTE[objectId % PALETTE.length]
}

// 时间线上已标注帧的标记点（限制数量避免渲染过多）
const annotatedFrameMarkers = computed(() => {
  const mediaId = currentMediaId.value
  const all = (annotationsByMedia.value[mediaId] ?? []).map((o: any) => o.frameIndex ?? 0)
  const sorted = [...new Set(all)].sort((a, b) => a - b)
  if (sorted.length <= 200) return sorted
  const step = Math.ceil(sorted.length / 200)
  return sorted.filter((_, i) => i % step === 0)
})

// 鼠标坐标显示
const mousePixel = ref<{ x: number; y: number } | null>(null)
const onMouseMoveStage = (e: MouseEvent) => {
  const media = selectedMedia.value
  if (!media) { mousePixel.value = null; return }
  const stage = annotationHitRef.value
  if (!stage) return
  const rect = stage.getBoundingClientRect()
  const px = Math.round(((e.clientX - rect.left) / rect.width) * (media.width || 0))
  const py = Math.round(((e.clientY - rect.top) / rect.height) * (media.height || 0))
  mousePixel.value = { x: px, y: py }
}

onMounted(() => {
  resizeObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      containerW.value = entry.contentRect.width
      containerH.value = entry.contentRect.height
    }
  })
  if (scrollContainerRef.value) resizeObserver.observe(scrollContainerRef.value)
  window.addEventListener('keydown', onKeyDown)
})
onUnmounted(() => {
  resizeObserver?.disconnect()
  window.removeEventListener('keydown', onKeyDown)
})

// 根据锁定的 contain 基准和 zoom 计算画布尺寸
const stageSize = computed(() => {
  if (!containBase.value) return { w: 0, h: 0 }
  return { w: Math.round(containBase.value.w * zoom.value), h: Math.round(containBase.value.h * zoom.value) }
})

// 锁定 contain 基准：素材切换时重置，首次有有效容器尺寸时计算
const lockContainBase = () => {
  const ratio = (selectedMedia.value?.width || 16) / (selectedMedia.value?.height || 9)
  const cw = containerW.value
  const ch = containerH.value
  if (!cw || !ch) return
  const containW = Math.min(cw, ch * ratio)
  const containH = containW / ratio
  containBase.value = { w: containW, h: containH }
}

watch(selectedMediaId, async (newId) => {
  containBase.value = null  // 素材切换时解锁
  await nextTick()
  lockContainBase()        // 立即尝试锁定（此时容器可能已有尺寸）
  await resetAnnotationViewForMedia(newId)
  scrollContainerRef.value?.scrollTo({ top: 0, left: 0 })
})

// ResizeObserver 只在 containBase 未锁定时更新，锁定后忽略后续尺寸变化
watch([containerW, containerH], () => {
  if (!containBase.value) lockContainBase()
})
</script>
<template>
      <section  class="flex min-h-0 flex-1 flex-col gap-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-4">
            <h2 class="text-lg font-semibold">人工标注图片 / 视频</h2>
            <div class="flex items-center gap-2 text-[11px]">
              <span class="flex items-center gap-1 rounded-full px-2.5 py-1 font-medium" :class="currentObjects.length ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-800 text-slate-400'">① 标注</span>
              <span class="text-slate-600">→</span>
              <span class="flex items-center gap-1 rounded-full px-2.5 py-1 font-medium" :class="statusMessage.includes('保存成功') ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-800 text-slate-400'">② 保存</span>
              <span class="text-slate-600">→</span>
              <span class="flex items-center gap-1 rounded-full px-2.5 py-1 font-medium" :class="isAiBusy ? 'bg-indigo-500/15 text-indigo-300' : 'bg-slate-800 text-slate-400'">③ AI Tracking</span>
            </div>
          </div>
          <div class="flex gap-2">
            <input ref="fileInputRef" type="file" accept="image/*" multiple class="hidden" @change="handleFiles(($event.target as HTMLInputElement).files, 'image')" />
            <input ref="videoInputRef" type="file" accept="video/*" multiple class="hidden" @change="handleFiles(($event.target as HTMLInputElement).files, 'video')" />
            <button class="btn-secondary" @click="openFilePicker('image')">上传图片</button>
            <button class="btn-secondary" @click="openFilePicker('video')">上传视频</button>
            <button class="btn-secondary" @click="selectSaveFolder">选择保存文件夹</button>
          </div>
        </div>

        <div class="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)_360px] gap-4">
          <!-- 左侧素材 -->
          <aside class="panel min-h-0 overflow-hidden">
            <div class="panel-title"><span>素材</span><span class="badge">{{ mediaAssets.length }}</span></div>
            <div class="h-[calc(100%-49px)] space-y-2 overflow-y-auto p-3">
              <button
                v-for="media in mediaAssets"
                :key="media.id"
                class="group flex w-full items-center gap-3 rounded-xl border p-3 text-left transition"
                :class="selectedMediaId === media.id ? 'border-indigo-400 bg-indigo-500/10' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'"
                @click="selectedMediaId = media.id"
              >
                <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-800 text-[10px] font-bold text-indigo-300">{{ media.type === 'video' ? 'VID' : 'IMG' }}</div>
                <div class="min-w-0 flex-1">
                  <div class="truncate text-xs font-medium">{{ media.name }}</div>
                  <div class="mt-1 text-[10px] text-slate-500">
                    {{ media.type === 'video' ? `${formatTime(media.duration || 0)} · ${media.fps || 30} FPS` : `${media.width || '-'} × ${media.height || '-'}` }}
                  </div>
                </div>
                <button
                  class="shrink-0 rounded p-1 text-slate-500 transition hover:bg-red-500/20 hover:text-red-400"
                  title="删除素材"
                  @click.stop="deleteMedia(media.id)"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </button>
            </div>
          </aside>

          <!-- 中央工作区 -->
          <section class="panel flex min-h-0 flex-col overflow-hidden">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-800 px-4 py-3">
              <div class="min-w-0">
                <h3 class="truncate text-sm font-semibold">{{ selectedMedia?.name || '未选择素材' }}</h3>
                <p class="mt-1 text-[10px] text-slate-500">
                  {{ isVideo ? `视频第 ${currentFrame} 帧标注模式` : '图片标注模式' }}
                  <span v-if="isVideo" class="ml-2 text-indigo-300">Frame {{ currentFrame }} · {{ formatTime(currentTime) }}</span>
                </p>
              </div>
              <div class="flex gap-2">
                <button class="tool-btn" :class="activeTool === 'point' ? 'active' : ''" :disabled="isAiBusy" @click="selectTool('point')">点标注 <kbd class="ml-1 text-[9px] opacity-50">P</kbd></button>
                <button class="tool-btn" :class="activeTool === 'bbox' ? 'active' : ''" :disabled="isAiBusy" @click="selectTool('bbox')">框标注 <kbd class="ml-1 text-[9px] opacity-50">B</kbd></button>
              </div>
            </div>

            <div class="relative min-h-0 flex-1">
              <div
                ref="scrollContainerRef"
                class="absolute inset-0 flex overflow-auto bg-black p-5"
                @wheel="(e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); if (e.deltaY < 0) zoomIn(0.1); else zoomOut(0.1); } }"
              >
                <div
                  class="relative m-auto shrink-0"
                  :style="{ width: `${stageSize.w}px`, height: `${stageSize.h}px` }"
                >
                    <template v-if="selectedMedia">
                    <video
                      v-if="isVideo"
                      :key="selectedMedia.id"
                      ref="videoRef"
                      :src="selectedMedia.url"
                      class="block h-full w-full select-none object-contain"
                      :style="{ filter: mediaFilterStyle }"
                      preload="metadata"
                      playsinline
                      @loadedmetadata="onVideoLoaded"
                      @timeupdate="onVideoTimeUpdate"
                      @ended="onVideoEnded"
                    />
                    <img
                      v-else
                      :key="selectedMedia.id"
                      ref="imageRef"
                      :src="selectedMedia.url"
                      :alt="selectedMedia.name"
                      class="block h-full w-full select-none object-contain"
                      :style="{ filter: mediaFilterStyle }"
                      @load="onImageLoaded"
                    />
                    </template>
                    <div v-else class="flex h-full w-full items-center justify-center text-slate-500 text-sm">请从左侧选择或上传素材</div>

                    <div
                      ref="annotationHitRef"
                      class="absolute inset-0 z-20 cursor-crosshair select-none"
                      style="touch-action: none;"
                      @click="onStageClick"
                      @pointerdown="onBboxDown"
                      @pointermove="onBboxMove"
                      @pointerup="onBboxUp"
                      @pointercancel="onBboxUp"
                      @mousemove="onMouseMoveStage"
                      @mouseleave="mousePixel = null"
                    ></div>

                    <svg viewBox="0 0 100 100" preserveAspectRatio="none" class="pointer-events-none absolute inset-0 z-30 h-full w-full">
                      <g v-for="obj in currentObjects" :key="obj.id">
                        <rect
                          v-if="obj.bbox"
                          :x="obj.bbox.x" :y="obj.bbox.y" :width="obj.bbox.width" :height="obj.bbox.height"
                          :fill="selectedObjectId === obj.id ? 'rgba(251,191,36,.20)' : 'rgba(99,102,241,.08)'"
                          :stroke="objColor(obj.objectId ?? 0, selectedObjectId === obj.id)"
                          :stroke-width="selectedObjectId === obj.id ? 1.0 : 0.4"
                          :stroke-dasharray="selectedObjectId === obj.id ? '2 1' : ''"
                          vector-effect="non-scaling-stroke"
                        />
                        <circle v-if="obj.point && !obj.bbox" :cx="obj.point.x" :cy="obj.point.y" r="1" :fill="objColor(obj.objectId ?? 0, selectedObjectId === obj.id)" />
                        <text v-if="obj.point && !obj.bbox" :x="obj.point.x + 1.5" :y="obj.point.y - 1.5" :fill="objColor(obj.objectId ?? 0, false)" font-size="2.3" font-weight="600">{{ obj.name }}</text>
                        <text v-if="obj.bbox" :x="obj.bbox.x" :y="Math.max(2, obj.bbox.y - 1)" :fill="objColor(obj.objectId ?? 0, false)" font-size="2.2" font-weight="600">{{ obj.name }}</text>
                        <!-- 选中时显示四角拖拽手柄 -->
                        <template v-if="selectedObjectId === obj.id && obj.bbox">
                          <rect :x="obj.bbox.x - 0.8" :y="obj.bbox.y - 0.8" width="1.6" height="1.6" fill="#fbbf24" stroke="#000" :stroke-width="0.2" vector-effect="non-scaling-stroke" />
                          <rect :x="obj.bbox.x + obj.bbox.width - 0.8" :y="obj.bbox.y - 0.8" width="1.6" height="1.6" fill="#fbbf24" stroke="#000" :stroke-width="0.2" vector-effect="non-scaling-stroke" />
                          <rect :x="obj.bbox.x - 0.8" :y="obj.bbox.y + obj.bbox.height - 0.8" width="1.6" height="1.6" fill="#fbbf24" stroke="#000" :stroke-width="0.2" vector-effect="non-scaling-stroke" />
                          <rect :x="obj.bbox.x + obj.bbox.width - 0.8" :y="obj.bbox.y + obj.bbox.height - 0.8" width="1.6" height="1.6" fill="#fbbf24" stroke="#000" :stroke-width="0.2" vector-effect="non-scaling-stroke" />
                        </template>
                      </g>
                      <rect v-if="tempBbox" :x="tempBbox.x" :y="tempBbox.y" :width="tempBbox.width" :height="tempBbox.height" fill="rgba(99,102,241,.08)" stroke="#a5b4fc" stroke-width="0.4" stroke-dasharray="1 0.7" />
                    </svg>
                </div>
              </div>

              <div v-if="isAiBusy" class="pointer-events-none absolute right-8 top-8 z-40 rounded-lg border border-indigo-400/30 bg-slate-950/85 px-3 py-2 text-[11px] text-indigo-200 backdrop-blur">
                SAM3 正在运行；人工标注已暂时锁定
              </div>

              <!-- 鼠标坐标 -->
              <div v-if="mousePixel" class="pointer-events-none absolute left-8 top-8 z-40 rounded-lg border border-slate-700 bg-slate-950/85 px-3 py-1.5 text-[11px] text-slate-300 backdrop-blur">
                x: {{ mousePixel.x }}, y: {{ mousePixel.y }}
              </div>

              <!-- 亮度/对比度 -->
              <div class="absolute left-8 bottom-8 z-40 flex items-center gap-3 rounded-lg border border-slate-700 bg-slate-950/85 px-3 py-2 text-[11px] text-slate-300 backdrop-blur">
                <span class="text-slate-400">亮度</span>
                <input type="range" min="50" max="200" v-model.number="brightness" class="w-20 accent-indigo-400" />
                <span class="text-slate-400">对比度</span>
                <input type="range" min="50" max="200" v-model.number="contrast" class="w-20 accent-indigo-400" />
                <button class="rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-700 hover:text-white" @click="resetMediaFilter">重置</button>
              </div>

              <div class="absolute bottom-8 right-8 z-40 flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-950/85 px-2 py-1 text-[11px] text-slate-200 backdrop-blur">
                <button class="rounded px-2 py-1 hover:bg-slate-700" @click="zoomOut()" title="缩小">−</button>
                <button class="min-w-[48px] px-2 py-1 text-center hover:bg-slate-700" @click="zoomReset" title="重置">{{ Math.round(zoom * 100) }}%</button>
                <button class="rounded px-2 py-1 hover:bg-slate-700" @click="zoomIn()" title="放大">+</button>
              </div>
            </div>

            <div v-if="anomalyObjectIds.length > 0" class="shrink-0 border-t border-amber-700 bg-amber-950/80 px-4 py-2 text-[11px] text-amber-300">
              ⚠️ 检测到异常，Tracking 已暂停。请修正第 {{ currentFrame }} 帧的标注框（异常对象：
              {{ anomalyObjectIds.join(', ') }}），然后重新点击 AI Tracking 继续。
            </div>

            <div v-if="isVideo" class="shrink-0 border-t border-slate-800 bg-slate-950/80 px-4 py-3">
              <div class="flex flex-wrap items-center gap-3">
                <span class="text-[11px] font-medium text-slate-300">视频进度</span>
                <button class="video-btn primary" @click="togglePlayback">{{ isPlaying ? '暂停' : '播放' }}</button>
                <span class="text-[10px] text-slate-400">当前第 {{ currentFrame }} 帧 / {{ maxFrameIndex }} 帧</span>
                <span class="text-[10px] text-emerald-300">已标注 {{ annotatedFrameCount }} / {{ maxFrameIndex + 1 }} 帧</span>
                <div class="ml-auto flex items-center gap-2">
                  <button class="btn-secondary" :disabled="isAiBusy || currentFrame === 0" @click="copyPreviousFrame" title="复制上一帧标注">复制上一帧</button>
                  <span class="text-[10px] text-slate-400">跳转帧</span>
                  <input v-model.number="frameInput" type="number" min="0" :max="maxFrameIndex" class="input w-24 text-center" :disabled="isAiBusy" @keyup.enter="seekToInputFrame" />
                  <button class="btn-secondary" :disabled="isAiBusy" @click="seekToInputFrame">跳转</button>
                </div>
              </div>
              <div class="mt-2 flex items-center gap-3 text-[11px] text-slate-400">
                <span class="w-20">{{ formatTime(currentTime) }}</span>
                <div class="relative h-5 flex-1 cursor-pointer" @click="onTimelineClick">
                  <div class="absolute inset-y-1.5 left-0 right-0 rounded-full bg-slate-800"></div>
                  <div class="absolute inset-y-1.5 left-0 rounded-full bg-indigo-500" :style="{ width: `${videoDuration ? (currentTime / videoDuration) * 100 : 0}%` }"></div>
                  <!-- 已标注帧的标记点 -->
                  <div
                    v-for="f in annotatedFrameMarkers"
                    :key="f"
                    class="absolute top-1/2 h-2 w-0.5 -translate-y-1/2 rounded-full bg-emerald-400"
                    :style="{ left: `${maxFrameIndex ? (f / maxFrameIndex) * 100 : 0}%` }"
                  ></div>
                  <!-- 异常帧标记 -->
                  <div
                    v-for="(af, i) in anomalyFrames"
                    :key="'anom-' + i"
                    class="absolute top-0 h-5 w-1 -translate-x-1/2 rounded-full"
                    :class="af.level === 'anomaly' ? 'bg-red-500' : af.level === 'disappeared' ? 'bg-purple-500' : 'bg-amber-500'"
                    :style="{ left: `${maxFrameIndex ? (af.frame_index / maxFrameIndex) * 100 : 0}%` }"
                    :title="`frame ${af.frame_index}: ${af.reasons.join(', ')}`"
                  ></div>
                  <div class="absolute top-0 h-5 w-1 -translate-x-1/2 rounded-full bg-white shadow" :style="{ left: `${videoDuration ? (currentTime / videoDuration) * 100 : 0}%` }"></div>
                </div>
                <span class="w-20 text-right">{{ formatTime(videoDuration) }}</span>
              </div>
            </div>

            <div class="flex shrink-0 items-center justify-between border-t border-slate-800 px-4 py-3">
              <div class="text-[11px] text-slate-500">
                当前工具：<span class="text-indigo-300">{{ activeTool === 'point' ? '点标注' : '框标注' }}</span>
                <span class="mx-2">·</span>{{ statusMessage }}
                <span class="ml-3 text-slate-600">快捷键：<kbd>←/→</kbd>切帧 <kbd>Del</kbd>删除 <kbd>P/B</kbd>切工具 <kbd>Ctrl+Z</kbd>撤销 <kbd>Ctrl+Y</kbd>重做 <kbd>Ctrl+S</kbd>保存 <kbd>C</kbd>复制上一帧 <kbd>Esc</kbd>取消</span>
              </div>
              <div class="flex gap-2">
                <button class="btn-secondary" @click="clearSelection">取消选择</button>
                <button class="btn-secondary" :disabled="isAiBusy || !annotatedFrameCount" @click="exportDialogOpen = true">导出训练数据集</button>
                <button class="btn-primary" :disabled="isAiBusy" @click="saveAnnotation">保存标注</button>
              </div>
            </div>
          </section>

          <!-- 右侧 -->
          <aside class="flex min-h-0 flex-col gap-4">
            <section class="panel shrink-0 p-4">
              <div class="mb-3">
                <h3 class="text-sm font-semibold">对象名称</h3>
                <p class="mt-1 text-[10px] text-slate-500">新建对象时使用；对象列表名称来自这里。</p>
              </div>
              <div class="flex gap-2">
                <input v-model="objectNameInput" class="input flex-1" placeholder="例如：rare sperm A" />
                <button v-if="selectedObjectId" class="btn-secondary shrink-0" :disabled="isAiBusy" @click="renameObject">重命名</button>
              </div>
            </section>

            <section class="panel shrink-0 p-4">
              <div class="mb-3">
                <h3 class="text-sm font-semibold">AI 辅助</h3>
                <p class="mt-1 text-[10px] leading-4 text-slate-500">当前接入后端 SAM3；每次从当前帧人工框开始，生成当前帧起连续 {{ trackingFrameCount }} 帧 tracking JSON。</p>
              </div>
              <div class="mb-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">Prompt：{{ objectNameInput || 'rare sperm' }}</div>
              <div class="grid grid-cols-2 gap-2">
                <button class="btn-primary" :disabled="isAiBusy" @click="runAiSegment">AI 检测 / 分割</button>
                <button class="btn-secondary" :disabled="isAiBusy || !isVideo || !currentObjects.length" @click="runAiTrack">AI Tracking</button>
              </div>
              <p v-if="isVideo" class="mt-2 text-[10px] text-amber-300/80">Tracking 的种子目标来自当前帧人工 bbox；Tracking 运行期间人工标注锁定；完成后自动寻找下一个没有标注的帧。每次帧数由后端统一配置，SAM3 模型只加载一次并复用。</p>
            </section>

            <section class="panel flex min-h-0 flex-1 flex-col overflow-hidden">
              <div class="panel-title"><span>对象列表</span><span class="badge">{{ currentObjects.length }}</span></div>
              <div v-if="currentObjects.length" class="min-h-0 flex-1 overflow-y-auto p-3">
                <template v-if="currentObjects.length > 6">
                  <div class="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                    <label class="mb-2 block text-[10px] font-medium text-slate-500">对象快速选择（共 {{ currentObjects.length }} 个）</label>
                    <select v-model="selectedObjectId" class="input w-full" @change="onObjectDropdownChange">
                      <option v-for="obj in currentObjects" :key="obj.id" :value="obj.id">{{ obj.name }} · {{ obj.source === 'ai' ? 'AI' : '人工' }}</option>
                    </select>
                  </div>
                  <div v-if="selectedObject" class="mt-3 rounded-xl border border-amber-400/40 bg-amber-400/5 p-3">
                    <div class="flex items-start justify-between gap-2">
                      <div class="min-w-0">
                        <div class="truncate text-xs font-semibold">{{ selectedObject.name }}</div>
                        <div class="mt-1 text-[10px] text-slate-500">
                          {{ selectedObject.source === 'ai' ? 'AI 标注' : '人工标注' }}
                          <span v-if="selectedObject.confidence"> · {{ Math.round(selectedObject.confidence * 100) }}%</span>
                          <span v-if="isVideo"> · 第 {{ currentFrame }} 帧</span>
                        </div>
                      </div>
                      <button class="rounded px-1.5 py-0.5 text-[10px] text-slate-500 transition hover:bg-red-500/10 hover:text-red-300" @click="removeObject(selectedObject.id)">删除</button>
                    </div>
                  </div>
                </template>
                <div v-else class="space-y-2">
                  <div
                    v-for="obj in currentObjects"
                    :key="obj.id"
                    class="group cursor-pointer rounded-xl border p-3 transition"
                    :class="selectedObjectId === obj.id ? 'border-amber-400 bg-amber-400/10' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'"
                    @click="selectObject(obj.id)"
                  >
                    <div class="flex items-start justify-between gap-2">
                      <div class="min-w-0">
                        <div class="truncate text-xs font-semibold">{{ obj.name }}</div>
                        <div class="mt-1 text-[10px] text-slate-500">
                          {{ obj.source === 'ai' ? 'AI 标注' : '人工标注' }}
                          <span v-if="obj.confidence"> · {{ Math.round(obj.confidence * 100) }}%</span>
                          <span v-if="isVideo"> · 第 {{ currentFrame }} 帧</span>
                        </div>
                      </div>
                      <button class="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-slate-500 transition hover:bg-red-500/10 hover:text-red-300" :disabled="isAiBusy" @click.stop="removeObject(obj.id)">删除</button>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="flex flex-1 items-center justify-center p-8 text-center text-xs text-slate-600">当前{{ isVideo ? `第 ${currentFrame} 帧` : '图片' }}暂无标注</div>
            </section>
          </aside>
        </div>
      </section>
<transition name="toast">
  <div v-if="toastMessage" class="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-xl border border-emerald-400/30 bg-slate-900/95 px-5 py-3 text-sm text-emerald-200 shadow-2xl backdrop-blur">
    ✓ {{ toastMessage }}
  </div>
</transition>

<!-- 导出训练数据集弹窗 -->
<transition name="fade">
  <div v-if="exportDialogOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" @click.self="exportDialogOpen = false">
    <div class="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
      <div class="flex items-center justify-between border-b border-slate-800 px-6 py-4">
        <h2 class="text-base font-semibold text-slate-100">📦 导出训练数据集</h2>
        <button class="text-slate-500 hover:text-slate-300" @click="exportDialogOpen = false">✕</button>
      </div>

      <div class="space-y-5 px-6 py-5">
        <!-- 数据概览 -->
        <div class="rounded-lg border border-slate-800 bg-slate-950/60 p-4 text-xs">
          <div class="mb-2 text-[11px] uppercase tracking-wider text-slate-500">数据概览</div>
          <div class="grid grid-cols-3 gap-3 text-slate-300">
            <div>
              <div class="text-lg font-semibold text-indigo-300">{{ allFrameCount }}</div>
              <div class="text-[10px] text-slate-500">标注帧数</div>
            </div>
            <div>
              <div class="text-lg font-semibold text-emerald-300">{{ allClassNameList.length }}</div>
              <div class="text-[10px] text-slate-500">类别数</div>
            </div>
            <div>
              <div class="text-lg font-semibold text-amber-300">{{ isVideo ? '视频' : '图片' }}</div>
              <div class="text-[10px] text-slate-500">素材类型</div>
            </div>
          </div>
          <div v-if="allClassNameList.length" class="mt-3 text-[11px] text-slate-500">
            类别：<span class="text-slate-300">{{ allClassNameList.join(' · ') }}</span>
          </div>
        </div>

        <!-- 格式选择 -->
        <div>
          <label class="mb-2 block text-xs font-medium text-slate-400">输出格式</label>
          <div class="grid grid-cols-3 gap-2">
            <button
              v-for="fmt in ([
                { v: 'coco', label: 'COCO', desc: 'YOLO/DETR 标准' },
                { v: 'yolo', label: 'YOLO', desc: 'YOLOv5/v8 训练' },
                { v: 'both', label: '都要', desc: '两种格式都生成' },
              ] as const)"
              :key="fmt.v"
              class="rounded-lg border px-3 py-2 text-left text-xs transition"
              :class="exportFormat === fmt.v
                ? 'border-indigo-400 bg-indigo-500/10 text-indigo-200'
                : 'border-slate-700 bg-slate-950 text-slate-400 hover:border-slate-500'"
              @click="exportFormat = fmt.v"
            >
              <div class="font-semibold">{{ fmt.label }}</div>
              <div class="mt-0.5 text-[10px] opacity-70">{{ fmt.desc }}</div>
            </button>
          </div>
        </div>

        <!-- 划分比例 -->
        <div>
          <label class="mb-2 flex items-center justify-between text-xs font-medium text-slate-400">
            <span>训练 / 验证集划分</span>
            <span class="text-slate-500">
              {{ exportSplitRatio === 0 ? '不划分' : `${Math.round(exportSplitRatio * 100)} / ${Math.round((1 - exportSplitRatio) * 100)}` }}
            </span>
          </label>
          <input type="range" min="0" max="0.9" step="0.1" v-model.number="exportSplitRatio"
            class="w-full accent-indigo-400" />
          <div class="mt-1 flex justify-between text-[10px] text-slate-600">
            <span>全部训练集</span><span>80/20</span><span>90/10</span>
          </div>
        </div>

        <p class="rounded-md bg-slate-950/60 p-3 text-[11px] leading-relaxed text-slate-500">
          💡 导出后会自动抽视频帧、转像素坐标、打包 zip 下载。COCO 格式包含标准 annotations.json；YOLO 格式包含每帧 txt + data.yaml 训练配置。
        </p>
      </div>

      <div class="flex gap-2 border-t border-slate-800 px-6 py-4">
        <button class="btn-secondary flex-1" @click="exportDialogOpen = false">取消</button>
        <button class="btn-primary flex-1" :disabled="doingExport || allFrameCount === 0" @click="doExportDataset">
          {{ doingExport ? '正在导出...' : '生成并下载 ZIP' }}
        </button>
      </div>
    </div>
  </div>
</transition>
</template>
