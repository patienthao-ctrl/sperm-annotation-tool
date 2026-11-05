// 标注 JSON 导出、已保存结果列表与效果预览。
import { computed, nextTick, ref } from 'vue'
import type { AnnotationObject, EffectResult, SavedAnnotationFile } from '../../types/annotation'
import { trackApi } from '../../api/trackApi'
import { annotationsByMedia, api, currentFrame, currentMediaId, currentTime, effectFolderInputRef, isVideo, mediaAssets, showToast, statusMessage, workspaceGenerations } from './state'
import { normalizeAnnotationObject } from './editing'
import { getMediaPixelSize } from './trackingResult'
import { buildCurrentFrameSam3Objects } from './tracking'

export const savedResults = ref<SavedAnnotationFile[]>([])
export const loadedRemoteResultKeys = new Set<string>()
let remoteResultsLoaded = false
export const effectResults = ref<EffectResult[]>([])
export const selectedEffectId = ref<string | null>(null)
export const effectTime = ref(0)
export const effectPlaying = ref(false)
export const effectVideoRef = ref<HTMLVideoElement | null>(null)
export const selectedEffect = computed(() => effectResults.value.find((item) => item.id === selectedEffectId.value) ?? effectResults.value[0] ?? null)

/**
 * Build a browser-downloadable SAM3 annotations JSON for the current media.
 * Kept for the existing JSON export action; it is independent of AI Tracking autosave.
 */
export const buildSam3AnnotationsJson = async (mediaId: string) => {
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media) throw new Error('当前素材不存在，无法生成 JSON')
  const objects = annotationsByMedia.value[mediaId] ?? []
  if (!objects.length) throw new Error('当前素材没有可导出的标注')

  const { width, height } = await getMediaPixelSize(media)
  const frameIndex = media.type === 'video' ? currentFrame.value : 0
  const timestampMs = media.type === 'video' ? Math.round(currentTime.value * 1000) : 0
  const clampX = (value: number) => Math.min(width, Math.max(0, value))
  const clampY = (value: number) => Math.min(height, Math.max(0, value))
  const toPixel = (value: number) => Math.round(value)

  const annotations = objects.map((obj) => {
    const item: Record<string, unknown> = {
      id: obj.id,
      object_id: obj.objectId,
      objectId: obj.objectId,
      name: obj.name,
      source: obj.source,
    }
    if (obj.bbox) {
      item.bbox = [
        toPixel(clampX((obj.bbox.x / 100) * width)),
        toPixel(clampY((obj.bbox.y / 100) * height)),
        toPixel(clampX(((obj.bbox.x + obj.bbox.width) / 100) * width)),
        toPixel(clampY(((obj.bbox.y + obj.bbox.height) / 100) * height)),
      ]
    }
    if (obj.point) {
      item.point = [
        toPixel(clampX((obj.point.x / 100) * width)),
        toPixel(clampY((obj.point.y / 100) * height)),
      ]
    }
    if (media.type === 'video') {
      item.frameIndex = obj.frameIndex ?? frameIndex
      item.timestampMs = obj.timestampMs ?? timestampMs
    }
    return item
  })

  const payload = {
    version: '1.0',
    format: 'sam3-annotations',
    media: { id: media.id, name: media.name, type: media.type, width, height },
    frame: { frameIndex, timestampMs },
    coordinateSystem: { source: 'frontend-percent', target: 'pixel', bbox: '[x1, y1, x2, y2]' },
    annotations,
  }
  return new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
}

export const generateAnnotationsJson = async () => {
  const mediaId = currentMediaId.value
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media) return showToast('当前素材不存在','error')
  try {
    const { width, height } = await getMediaPixelSize(media)
    if (media.type === 'video') {
      if (!media.serverMediaId) throw new Error('该视频尚未成功上传到后端，请重新上传视频')
      const annotations = await buildCurrentFrameSam3Objects(mediaId)
      const frameIndex = currentFrame.value
      const timestampMs = Math.round(currentTime.value * 1000)
      statusMessage.value = `正在保存第 ${frameIndex + 1} 帧 annotations.json……`
      const result = await trackApi.saveFrameAnnotations({
        mediaId: media.serverMediaId,
        generationId: workspaceGenerations.get(mediaId),
        mediaName: media.name,
        mediaWidth: width,
        mediaHeight: height,
        frameIndex,
        timestampMs,
        annotations,
      })
      statusMessage.value = `第 ${frameIndex + 1} 帧 JSON 已写入 ${result.filename}`
      showToast(`第 ${frameIndex + 1} 帧 JSON 已保存到视频目录`,'success')
    } else {
      const blob = await buildSam3AnnotationsJson(mediaId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url; link.download = 'annotations.json'; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      statusMessage.value = 'JSON 生成成功：annotations.json'
      showToast('图片 annotations.json 已生成','success')
    }
  } catch (error) {
    statusMessage.value = error instanceof Error ? error.message : 'JSON 生成失败'
    showToast(statusMessage.value,'error')
  }
}


export const loadSavedResults = async () => {
  if (remoteResultsLoaded) return
  remoteResultsLoaded = true
  try {
    const { listResults } = await import('../../api/httpAnnotationApi')
    const res = await listResults('default')
    const rows = (res.items ?? []) as any[]
    const groups = new Map<string, any[]>()
    for (const row of rows) {
      const fileIdentity = String(row.media_name || row.media_id || '').trim().toLocaleLowerCase()
      const key = `${row.media_type || 'video'}:${fileIdentity}:${row.frame_index ?? 0}:${row.user_id ?? 0}`
      groups.set(key, [...(groups.get(key) ?? []), row])
    }
    const remote = Array.from(groups.entries()).map(([key, list]) => {
      const head = list[0]
      const isVideo = head.media_type === 'video'
      const objects = list.map((r) => {
        const objectIdRaw = r.object_id && /^\d+$/.test(String(r.object_id)) ? Number(r.object_id) : undefined
        const isBox = r.shape_type === 'bbox'
        return normalizeAnnotationObject({
          id: r.object_id ?? String(r.id), objectId: objectIdRaw, name: r.object_name ?? 'rare sperm', source: (r.source === 'ai' ? 'ai' : 'manual'),
          bbox: isBox && r.pct_x !== null ? { x: r.pct_x, y: r.pct_y ?? 0, width: r.pct_w ?? 0, height: r.pct_h ?? 0 } : undefined,
          point: !isBox && r.pct_x !== null ? { x: r.pct_x, y: r.pct_y ?? 0 } : undefined,
          frameIndex: isVideo ? r.frame_index : undefined, timestampMs: isVideo ? r.timestamp_ms : undefined,
        } as AnnotationObject, objectIdRaw ?? Number(r.id))
      })
      return { key, batchId: head.batch_id || undefined, mediaId: head.media_id, mediaName: head.media_name ?? head.media_id,
        mediaType: (isVideo ? 'video' : 'image') as 'video' | 'image',
        frameIndex: isVideo ? head.frame_index : undefined, timestampMs: isVideo ? head.timestamp_ms : undefined,
        savedAt: new Date(String(head.created_at).replace(' ', 'T')).toISOString(), filename: '后端记录', username: head.username || null, objects } as SavedAnnotationFile & { key: string }
    })
    const existingKeys = new Set(savedResults.value.map((r: any) => r.batchId ? `batch:${r.batchId}` : `local:${r.mediaId}:${r.savedAt}:${r.objects.map((o: any) => o.id).join(',')}`))
    const fresh = remote.filter((r) => {
      const dedupeKey = r.batchId ? `batch:${r.batchId}` : `remote:${r.key}`
      if (loadedRemoteResultKeys.has(dedupeKey) || existingKeys.has(dedupeKey)) return false
      loadedRemoteResultKeys.add(dedupeKey)
      return true
    }).map(({ key: _key, ...r }) => r)
    if (fresh.length) savedResults.value = [...fresh, ...savedResults.value]
  } catch (error) {
    remoteResultsLoaded = false
    console.warn('从后端加载标注结果失败：', error)
  }
}

export const openEffectFolderPicker = () => {
  effectFolderInputRef.value?.click()
}

export const handleEffectFolder = (files: FileList | null) => {
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

export const loadEffects = async () => {
  try {
    const result = await api.listEffects()
    effectResults.value = result.items
    selectedEffectId.value = result.items[0]?.id ?? null
  } catch (error) {
    statusMessage.value = error instanceof Error ? error.message : '效果列表加载失败'
  }
}

export const onEffectTimeUpdate = () => {
  if (effectVideoRef.value) effectTime.value = effectVideoRef.value.currentTime
}

export const toggleEffectPlayback = async () => {
  if (!effectVideoRef.value) return
  if (effectVideoRef.value.paused) {
    await effectVideoRef.value.play()
    effectPlaying.value = true
  } else {
    effectVideoRef.value.pause()
    effectPlaying.value = false
  }
}

export const selectEffect = (effectId: string) => {
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

export const effectOverlayObjects = computed(() => {
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
