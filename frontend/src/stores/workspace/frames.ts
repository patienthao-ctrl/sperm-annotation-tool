// 帧读取与播放：精确帧加载与缓存、视频定位/播放/逐帧降级播放、时间轴与下一未标注帧。
import { nextTick, ref, watch } from 'vue'
import { FrameCache } from '../../annotation/frameCache'
import { trackApi } from '../../api/trackApi'
import { annotationsByMedia, currentFrame, currentMediaId, currentTime, enhancedView, exactFrameLoading, exactFrameUrl, frameError, frameInput, imageRef, isAiBusy, isPlaying, isVideo, maxFrameIndex, mediaAssets, playbackRate, runtime, selectedMedia, statusMessage, videoDuration, videoFps, videoRef, workspaceRestoring } from './state'
import { isInteracting } from './editing'

let videoFrameCallbackId: number | null = null
export const videoPlaybackFallback = ref(false)
let fallbackPlaybackSerial = 0

export const formatTime = (seconds: number) => {
  if (!Number.isFinite(seconds)) return '00:00.000'
  const m = Math.floor(seconds / 60).toString().padStart(2, '0')
  const s = Math.floor(seconds % 60).toString().padStart(2, '0')
  const ms = Math.floor((seconds % 1) * 1000).toString().padStart(3, '0')
  return `${m}:${s}.${ms}`
}

export const timeToFrame = (time: number) => Math.max(0, Math.round(time * videoFps.value))
export const frameToTime = (frame: number) => frame / videoFps.value

// 浏览器实际呈现帧优先使用 requestVideoFrameCallback 的 mediaTime。
// 对严格 CFR 视频，mediaTime 与 frameIndex 的关系为 frame / fps；
// 不再用 video.currentTime 在 seek 尚未真正呈现时提前切换标注帧。
const commitPresentedFrame = (mediaTime: number) => {
  const time = Number.isFinite(mediaTime) ? Math.max(0, mediaTime) : 0
  const frame = timeToFrame(time)
  currentTime.value = time
  currentFrame.value = Math.max(0, Math.min(maxFrameIndex.value, frame))
  frameInput.value = currentFrame.value
}

export const revokeExactFrameUrl = () => {
  if (exactFrameUrl.value) {
    try { URL.revokeObjectURL(exactFrameUrl.value) } catch {}
    exactFrameUrl.value = null
  }
}

// Large enough to hold a whole short clip (35 frames of 4K JPEG ~ 70 MB) in both views.
export const frameCache = new FrameCache(160, 256 * 1024 * 1024)
const frameKey = (serverId: string, frameIndex: number, enhanced: boolean) => `${serverId}:${frameIndex}${enhanced ? ':enh' : ''}`
export const readFrame = (serverId: string, frameIndex: number, enhanced = enhancedView.value) =>
  frameCache.get(frameKey(serverId, frameIndex, enhanced), () => trackApi.getFrameBlob(serverId, frameIndex, enhanced))
let prefetchGeneration = 0
/** Background prefetch, two requests at a time; a newer prefetch or video switch cancels the rest. */
export const prefetchFrames = async (serverId: string, frames: number[], enhanced = enhancedView.value) => {
  const generation = ++prefetchGeneration, queue = frames.filter(fi => !frameCache.has(frameKey(serverId, fi, enhanced)))
  const worker = async () => {
    while (queue.length && generation === prefetchGeneration) {
      const fi = queue.shift()!
      await readFrame(serverId, fi, enhanced).catch(error => console.debug('[annotation.prefetch_failed]', { frameIndex: fi, error }))
    }
  }
  await Promise.all([worker(), worker()])
}
/** Short clips are fetched whole so stepping, seeking and playback never wait on the network. */
export const prefetchClip = (mediaId = currentMediaId.value) => {
  const media = mediaAssets.value.find(item => item.id === mediaId)
  if (!media?.serverMediaId || media.type !== 'video' || !media.frameCount || media.frameCount > 60) return
  const order = Array.from({ length: media.frameCount }, (_, i) => (currentFrame.value + i) % media.frameCount!)
  void prefetchFrames(media.serverMediaId, order)
}
const failedFrame = ref<number | null>(null)
export const retryExactFrame = () => loadExactFrame(failedFrame.value ?? currentFrame.value)
export const loadExactFrame = async (frameIndex: number, mediaId = currentMediaId.value) => {
  const media = mediaAssets.value.find(item => item.id === mediaId)
  if (!media || media.type !== 'video' || !media.serverMediaId) return false
  const serial = ++runtime.exactFrameRequestSerial, started = performance.now()
  exactFrameLoading.value = true; frameError.value = ''; failedFrame.value = null
  let url: string | null = null
  const enhanced = enhancedView.value
  const read = (fi: number) => readFrame(media.serverMediaId!, fi, enhanced)
  try {
    const blob = await read(frameIndex)
    if (serial !== runtime.exactFrameRequestSerial || mediaId !== currentMediaId.value) return false
    url = URL.createObjectURL(blob)
    // Decode before swapping so the picture never flashes; a background tab may defer decoding
    // indefinitely, so never let that hold up restoring the workspace.
    const decoded = new Image(); decoded.src = url
    await Promise.race([decoded.decode(), new Promise(resolve => setTimeout(resolve, 2000))])
    if (serial !== runtime.exactFrameRequestSerial || mediaId !== currentMediaId.value) return false
    const old = exactFrameUrl.value
    exactFrameUrl.value = url; url = null
    currentFrame.value = frameIndex; frameInput.value = frameIndex; currentTime.value = frameToTime(frameIndex)
    await nextTick()
    if (old) URL.revokeObjectURL(old)
    const elapsedMs = Math.round(performance.now() - started)
    if (elapsedMs > 250) console.info('[annotation.frame_slow]', { mediaId, frameIndex, elapsedMs })
    // Stepping forward is the common direction; keep the next few frames ready.
    for (const next of [frameIndex + 1, frameIndex + 2, frameIndex - 1, frameIndex + 3, frameIndex + 4]) {
      if (next >= 0 && next < (media.frameCount || 0)) void read(next).catch(error => console.debug('[annotation.prefetch_failed]', { mediaId, frameIndex: next, error }))
    }
    return true
  } catch (error) {
    if (url) frameCache.invalidate(frameKey(media.serverMediaId, frameIndex, enhanced))
    if (serial === runtime.exactFrameRequestSerial && mediaId === currentMediaId.value) {
      failedFrame.value = frameIndex
      frameError.value = `第 ${frameIndex + 1} 帧读取失败，请重试`; statusMessage.value = frameError.value
      console.error('[annotation.frame_failed]', { mediaId, frameIndex, error })
    }
    return false
  } finally {
    if (url) URL.revokeObjectURL(url)
    if (serial === runtime.exactFrameRequestSerial) exactFrameLoading.value = false
  }
}

export const resetVideoViewToFirstFrame = (mediaId = currentMediaId.value) => {
  if (currentMediaId.value !== mediaId || !isVideo.value) return
  currentFrame.value = 0
  currentTime.value = 0
  frameInput.value = 0
  revokeExactFrameUrl()
  if (videoRef.value?.readyState) {
    try { videoRef.value.currentTime = 0 } catch { }
  }
  void loadExactFrame(0, mediaId)
}

export const ensureVideoFirstFrame = async (_mediaId = currentMediaId.value) => true

export const onImageLoaded = () => {
  if (!imageRef.value) return
  if (selectedMedia.value) {
    selectedMedia.value.width = imageRef.value.naturalWidth
    selectedMedia.value.height = imageRef.value.naturalHeight
  }
}

export const onVideoLoaded = async () => {
  const mediaId = currentMediaId.value
  const video = videoRef.value
  if (!video) return
  videoPlaybackFallback.value = false
  videoDuration.value = video.duration || videoDuration.value || 0
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media || mediaId !== currentMediaId.value || videoRef.value !== video) return
  media.duration = videoDuration.value
  // 以服务端/OpenCV 的源视频尺寸作为标注坐标系；精确逐帧模式直接显示服务端解码帧。
  if (!media.width) media.width = video.videoWidth
  if (!media.height) media.height = video.videoHeight
  videoFps.value = media.fps || 30
  media.frameCount = media.frameCount || (video.duration > 0 && videoFps.value > 0 ? Math.round(video.duration * videoFps.value) : undefined)
  await ensureVideoFirstFrame(mediaId)
  if (isPlaying.value) scheduleVideoFrameSync()
}

export const stopFallbackPlayback = () => {
  fallbackPlaybackSerial += 1
  isPlaying.value = false
}

export const onVideoError = async () => {
  const media = selectedMedia.value
  if (!media?.serverMediaId) return
  videoPlaybackFallback.value = true
  stopFallbackPlayback()
  if (media.frameCount && media.fps) {
    videoDuration.value = media.frameCount / media.fps
    videoFps.value = media.fps
  }
  // A native error can arrive while the saved frame is still decoding.
  // Do not replace that request with the temporary frame-zero view.
  if (!workspaceRestoring.value && !exactFrameLoading.value) await loadExactFrame(currentFrame.value, media.id)
  statusMessage.value = `当前浏览器不能直接播放 ${media.name}，已切换为原始视频逐帧预览（源文件未转换）`
}

// Frame-by-frame playback from decoded server frames (enhanced view, or a video the browser
// cannot play). Frames are scheduled on a fixed clock from the source FPS and the next ones are
// fetched ahead, so a slow frame delays only itself instead of the whole rhythm drifting.
const playFallbackFrames = async () => {
  const media = selectedMedia.value
  if (!media?.serverMediaId) return
  const serial = ++fallbackPlaybackSerial
  isPlaying.value = true
  const fps = Math.max(1, media.fps || videoFps.value || 30)
  const interval = 1000 / (fps * playbackRate.value)
  let due = performance.now() + interval
  while (serial === fallbackPlaybackSerial && isPlaying.value && currentFrame.value < maxFrameIndex.value) {
    const next = currentFrame.value + 1
    void prefetchFrames(media.serverMediaId, [next + 1, next + 2, next + 3, next + 4, next + 5, next + 6].filter(fi => fi <= maxFrameIndex.value))
    await new Promise((resolve) => setTimeout(resolve, Math.max(0, due - performance.now())))
    if (serial !== fallbackPlaybackSerial || !isPlaying.value) break
    const ok = await loadExactFrame(next, media.id)
    if (!ok) break
    due = Math.max(due + interval, performance.now())
  }
  if (serial === fallbackPlaybackSerial) isPlaying.value = false
}

const syncVideoFrameState = () => {
  const video = videoRef.value
  if (!video) return
  // currentTime 仅用于时间显示；currentFrame 只由实际呈现帧 metadata.mediaTime 更新。
  currentTime.value = Number.isFinite(video.currentTime) ? video.currentTime : 0
}

const scheduleVideoFrameSync = () => {
  const video = videoRef.value
  if (!video || typeof video.requestVideoFrameCallback !== 'function' || runtime.isSeekingVideo) return

  if (videoFrameCallbackId !== null) {
    try { video.cancelVideoFrameCallback(videoFrameCallbackId) } catch {}
    videoFrameCallbackId = null
  }

  const loop = (_now: number, metadata: VideoFrameCallbackMetadata) => {
    if (videoRef.value !== video || runtime.isSeekingVideo) return

    const mediaTime = Number.isFinite(metadata.mediaTime)
      ? metadata.mediaTime
      : video.currentTime

    // 唯一的真实当前帧来源：浏览器实际呈现帧的 mediaTime。
    commitPresentedFrame(mediaTime)

    if (!video.paused && !video.ended && !runtime.isSeekingVideo) {
      videoFrameCallbackId = video.requestVideoFrameCallback(loop)
    } else {
      videoFrameCallbackId = null
    }
  }

  videoFrameCallbackId = video.requestVideoFrameCallback(loop)
}

export const onVideoTimeUpdate = () => {
  if (runtime.isSeekingVideo || !isPlaying.value) return
  syncVideoFrameState(); scheduleVideoFrameSync()
}
export const requestedFrame = ref<number | null>(null)
export const seekVideo = async (time: number) => {
  if (!isVideo.value || isInteracting()) return
  videoRef.value?.pause()
  stopFallbackPlayback()
  const media = selectedMedia.value
  if (!media) return

  const targetFrame = Math.max(
    0,
    Math.min(maxFrameIndex.value, Math.round(time * Math.max(videoFps.value, 1)))
  )

  // 精确逐帧模式：暂停后不再让浏览器通过 currentTime 猜测目标帧，
  // 直接向后端请求 source video 的指定 frame_index。
  runtime.isSeekingVideo = true
  const serial = ++runtime.seekSerial
  requestedFrame.value = targetFrame
  try {
    if (media.serverMediaId) {
      const ok = await loadExactFrame(targetFrame, media.id)
      if (serial !== runtime.seekSerial) return
      if (!ok) return
      // 后端精确帧图像已经与 JSON 使用同一个 frame_index；同步视频元素位置仅供恢复播放使用。
      if (videoRef.value) {
        try { videoRef.value.currentTime = frameToTime(targetFrame) } catch {}
      }
      statusMessage.value = `已定位到第 ${targetFrame + 1} 帧`
    } else if (videoRef.value) {
      // 没有 serverMediaId 的旧本地视频只能回退到浏览器 video。
      videoRef.value.currentTime = frameToTime(targetFrame)
      currentFrame.value = targetFrame
      currentTime.value = frameToTime(targetFrame)
      frameInput.value = targetFrame
    }
  } finally {
    if (serial === runtime.seekSerial) { runtime.isSeekingVideo = false; requestedFrame.value = null }
  }
}

export const seekToInputFrame = async () => {
  if (!isVideo.value || isAiBusy.value) return
  const frame = Math.max(0, Math.min(maxFrameIndex.value, Math.floor(Number(frameInput.value) || 0)))
  frameInput.value = frame
  await seekVideo(frameToTime(frame))

}

export const seekByFrame = async (delta: number) => {
  if (!isVideo.value || isAiBusy.value) return
  // 用 currentFrame (整数帧号) 做基准, 不用 currentTime (可能有小数误差)
  const target = Math.max(0, Math.min(maxFrameIndex.value, (requestedFrame.value ?? currentFrame.value) + delta))
  if (target === currentFrame.value && requestedFrame.value === null) return
  await seekVideo(frameToTime(target))
}

export const togglePlayback = async () => {
  if (runtime.isSeekingVideo || exactFrameLoading.value || isAiBusy.value || isInteracting()) return
  if (videoPlaybackFallback.value || enhancedView.value) {
    if (isPlaying.value) {
      stopFallbackPlayback()
      await loadExactFrame(currentFrame.value)
    } else {
      void playFallbackFrames()
    }
    return
  }
  if (!videoRef.value) return
  if (videoRef.value.paused) {
    try {
      videoRef.value.currentTime = frameToTime(currentFrame.value)
      revokeExactFrameUrl()
      videoRef.value.playbackRate = playbackRate.value
      await videoRef.value.play()
      isPlaying.value = true
      scheduleVideoFrameSync()
    } catch {
      await onVideoError()
      void playFallbackFrames()
    }
  } else {
    videoRef.value.pause()
    isPlaying.value = false
    const target = currentFrame.value
    syncVideoFrameState()
    if (selectedMedia.value?.serverMediaId) await loadExactFrame(target)
  }
}


export const pausePlayback = () => {
  videoRef.value?.pause(); stopFallbackPlayback()
  if (videoFrameCallbackId !== null && videoRef.value) videoRef.value.cancelVideoFrameCallback?.(videoFrameCallbackId)
  videoFrameCallbackId = null
}
export const onVideoEnded = async () => {
  stopFallbackPlayback()
  if (videoFrameCallbackId !== null && videoRef.value && typeof videoRef.value.cancelVideoFrameCallback === 'function') {
    try { videoRef.value.cancelVideoFrameCallback(videoFrameCallbackId) } catch {}
    videoFrameCallbackId = null
  }
  syncVideoFrameState()
  await loadExactFrame(currentFrame.value)
}

export const onTimelineClick = async (event: MouseEvent) => {
  if (!isVideo.value || !videoDuration.value || isAiBusy.value) return
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  await seekVideo(ratio * videoDuration.value)
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
  statusMessage.value = next === currentFrame.value ? `当前已是最后可标注帧：${next + 1}` : `下一个未标注帧：${next + 1}`
}

export const setupFramesWatchers = () => {
  watch(playbackRate, value => { if (videoRef.value) videoRef.value.playbackRate = value })
  watch(enhancedView, value => {
    try { sessionStorage.setItem('annotation-enhanced', value ? '1' : '0') } catch {}
    if (!isVideo.value) return
    pausePlayback()
    void loadExactFrame(currentFrame.value).then(() => prefetchClip())
  })
}
