// 追踪结果加载与暂停诊断说明。
import { reconcileTerminalIdentities } from '../../annotation/trackingResult'
import { pauseDiagnostics, pauseTypeFromEvidence } from '../../annotation/pauseDiagnostics'
import type { AnnotationObject, MediaAsset } from '../../types/annotation'
import { trackApi } from '../../api/trackApi'
import { useAuth } from '../auth'
import { annotationsByMedia, currentMediaId, dedupeAnnotationObjects, deletedTrackingIds, imageRef, trackingResultState, isObjectDeleted, mediaAssets, pausedAnomalies, runtime, trackingErrors, trackingFramesByMedia, videoRef } from './state'
import { numberedObjectName } from './editing'

export const loadTrackingResult = async (mediaId: string, force = false, strict = false, required = false) => {
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media?.serverMediaId) return
  const serial = ++runtime.trackingLoadSerial
  const actor = useAuth().user.value?.id
  try {
    const result = await trackApi.getResult(media.serverMediaId, undefined, required)
    if (serial !== runtime.trackingLoadSerial || mediaId !== currentMediaId.value || actor !== useAuth().user.value?.id) return
    if ('state' in result && result.state === 'not_generated' && required) throw new Error('追踪任务应已生成结果，请管理员检查任务日志与存储')
    if ('frames' in result) trackingResultState.value = { ...trackingResultState.value, [mediaId]: 'state' in result && result.state === 'not_generated' ? 'not_generated' : 'available' }
    if('jointTracking' in result && result.jointTracking?.state==='failed'){
      trackingErrors.value[mediaId]={phase:'生成自动标注',frameIndex:0,endpointMode:true,message:'上次自动标注未完成，已保留原结果；请重试生成或检查服务日志。'}
    }
    const frames = 'frames' in result ? result.frames : [result]
    if (media.frameCount && frames.some(f => f.frameIndex >= media.frameCount!)) throw new Error('追踪结果帧号超过原视频范围')
    // 过滤掉前端已删除的 AI tracking 对象
    const deleted = deletedTrackingIds.value[mediaId]
    const filtered = frames.map((f: any) => ({ ...f, annotations: f.annotations.filter((a: any) => !isObjectDeleted(mediaId, a.objectId, f.frameIndex) && !deleted?.has(a.id) && !deleted?.has(String(a.objectId))) }))
    trackingFramesByMedia.value[mediaId] = filtered

    // 将 AI tracking 结果合并到 annotationsByMedia（统一管理）
    // 策略：同帧同 objectId 的新结果覆盖旧结果，手动标注永不被覆盖
    const width = media.width || 1
    const height = media.height || 1
    const existing = reconcileTerminalIdentities(annotationsByMedia.value[mediaId] ?? [], 'terminalIdentity' in result ? result.terminalIdentity : undefined).filter(o => !isObjectDeleted(mediaId, o.objectId, o.frameIndex ?? 0))
    // 被手动修改过的 (frameIndex, objectId) 集合 — 精准保护, 只跳过用户改过的那一个
    const manualKeys = new Set(existing
      .filter((o) => o.source === 'manual' && o.objectId != null)
      .map((o) => `${o.frameIndex ?? 0}:${o.objectId}`))
    // 手动新建 (objectId=null) 也保留; 有 objectId 的才走 key 粒度保护
    // Full results are authoritative: absent AI rows belong to an obsolete branch.
    const existingByKey = new Map<string, AnnotationObject>()
    // 先把手动标注全量放进去，并清理旧版本重复保存的同帧同 ID 项。
    let merged: AnnotationObject[] = dedupeAnnotationObjects(
      existing.filter((o) => o.source === 'manual').map((o) => ({ ...o })),
    )

    for (const frame of filtered) {
      for (const ann of frame.annotations) {
        const key = `${frame.frameIndex}:${ann.objectId}`
        // 跳过被用户手动修改过的那一个 (其他同帧同 objectId 的正常更新)
        if (manualKeys.has(key)) continue
        const [x1, y1, x2, y2] = ann.bbox ?? [0, 0, 0, 0]
        const newObj: AnnotationObject = {
          id: `ai-${frame.frameIndex}-${ann.objectId}`,  // 强制唯一 id，不管后端返回什么
          objectId: ann.objectId,
          name: numberedObjectName(ann.name ?? 'rare sperm', Number(ann.objectId)),
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
          anomaly_level: ann.anomaly_level ?? undefined,
          anomaly_reasons: ann.anomaly_reasons ?? undefined,
          anomaly_details: ann.anomaly_details ?? undefined,
        }
        // 新结果覆盖旧结果（续接追踪时覆盖第一遍的结果）
        existingByKey.set(key, newObj)
      }
    }

    // ---------- 关键：用 AI tracking 的 objectId 补全手动标注 ----------
    // 策略：拿 AI 结果首帧的 bbox 和所有手动标注做 IoU 匹配
    // 必须在 finalAi push 之前做，否则同帧同 oid 的手动/AI 会共存
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

    // 合并：手动标注 + 最终的 AI 标注
    // 手动已有的 (frame, objectId) 不再加 AI 的 — 避免同帧同 oid 两条共存
    const manualKeysAfterMatch = new Set(
      merged.filter((o) => o.objectId != null).map((o) => `${o.frameIndex ?? 0}:${o.objectId}`)
    )
    const finalAi = [...existingByKey.values()]
      .filter((o) => o.source === 'ai')
      .filter((o) => !manualKeysAfterMatch.has(`${o.frameIndex ?? 0}:${o.objectId}`))
    merged = dedupeAnnotationObjects([...merged, ...finalAi])

    annotationsByMedia.value = { ...annotationsByMedia.value, [mediaId]: merged }
  } catch (error) {
    if (serial !== runtime.trackingLoadSerial || mediaId !== currentMediaId.value || actor !== useAuth().user.value?.id) return
    console.warn('[annotation.tracking_load_failed]', { mediaId, error })
    if(strict)throw error
  }
}

/**
 * 获取当前素材的实际像素尺寸。
 * 视频优先读取 HTMLVideoElement.videoWidth/videoHeight；图片读取 naturalWidth/naturalHeight；
 * 最后才回退到 MediaAsset 中已保存的 width/height。
 */
export const getMediaPixelSize = async (media: MediaAsset) => {
  if (media.type === 'video') {
    const video = videoRef.value
    if (video && video.videoWidth > 0 && video.videoHeight > 0) {
      return { width: video.videoWidth, height: video.videoHeight }
    }
    // AVI/MKV 等容器可能被后端 OpenCV 正常解码，却不被浏览器的
    // <video> 原生支持。此时界面会切换到 /api/track/frame 的逐帧预览，
    // videoWidth 会一直是 0；上传接口已保存的真实尺寸才是可信来源。
    if ((media.width || 0) > 0 && (media.height || 0) > 0) {
      return { width: media.width as number, height: media.height as number }
    }
    throw new Error('视频宽高不可用：请确认视频已成功上传且后端可以读取其帧数据')
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

const formatMetric = (label: string, value: unknown) => {
  const number = Number(value)
  return Number.isFinite(number) ? `${label} ${number.toFixed(2)}` : ''
}

const objectDisplayName = (objectId: number, preferred?: string | null) => {
  if (preferred?.trim()) return preferred.trim()
  const local = (annotationsByMedia.value[currentMediaId.value] ?? [])
    .find((obj) => obj.objectId === objectId && obj.name?.trim())
  return local?.name?.trim() || `object-${objectId}`
}

export const isGeometryReason = (reason: string) => /^(?:manual|geometry)_(?:area_ratio|width_ratio|height_ratio|aspect_change|diagonal_ratio)=|^confirmed_mask_area_ratio=/.test(reason)
export const canLearnNormalMotion = (reasons: string[]) => reasons.some(reason => reason.startsWith('adjacent_center_shift='))
  && reasons.every(reason => reason.startsWith('adjacent_center_shift=') || reason.startsWith('motion_')
    // Recognizing the new display metric must not expand motion learning.
    || (isGeometryReason(reason) && !/^(?:manual|geometry)_diagonal_ratio=/.test(reason)))

export const normalizePauseNotice = (item: typeof pausedAnomalies.value[number]) => {
  // Older saved pauses have only the displayed title and canLearn flag.
  // This restores their explanation; the server validates the real evidence.
  const rawDiagnostic = item.diagnostic ?? (item.rawReasons?.some(reason => reason.endsWith(' HARD')) ? {
    object_id: item.objectId, display_name: item.displayName, type: '', reasons: item.rawReasons,
    manualBaselineFrame: item.baselineFrame,
    details: { geometry_reference_frame: item.geometryReferenceFrame, geometry_reference_source: item.geometryReferenceSource },
  } : undefined)
  const diagnostic: Partial<typeof pausedAnomalies.value[number]> = rawDiagnostic ? explainPausedObject(rawDiagnostic) : {}
  const acceptsGeometry = !!item.acceptsGeometry || (item.rawReasons ?? []).some(isGeometryReason)
    || ['框相对最近人工标注明显缩小', '框相对最近人工标注明显扩大或变形'].includes(item.title)
  return {
    ...item,
    ...diagnostic,
    displayName: item.displayName || diagnostic.displayName || `object-${item.objectId}`,
    canLearn: item.rawReasons?.length ? canLearnNormalMotion(item.rawReasons) : !!item.canLearn,
    acceptsGeometry,
    acceptsOverlap: item.acceptsOverlap || (item.rawReasons ?? []).some(reason => reason.startsWith('bbox_overlap_with=')),
    geometryReferenceFrame: diagnostic.geometryReferenceFrame ?? item.geometryReferenceFrame ?? (acceptsGeometry ? item.baselineFrame : undefined),
    geometryReferenceSource: diagnostic.geometryReferenceSource ?? item.geometryReferenceSource ?? (acceptsGeometry ? 'manual' as const : undefined),
    rawReasons: item.rawReasons ?? [],
  }
}

export const explainPausedObject = (item: NonNullable<Awaited<ReturnType<typeof trackApi.getStatus>>['pausedObjects']>[number]) => {
  const details = item.details ?? {}
  const reasons = item.reasons ?? []
  const type = pauseTypeFromEvidence(item.type, reasons)
  const displayName = objectDisplayName(item.object_id, item.display_name || item.name)
  const evidence = pauseDiagnostics(item.object_id, reasons, details, id => objectDisplayName(id))
  const baselineFrame = Number(item.manualBaselineFrame ?? details.manual_baseline_frame ?? NaN)
  const geometryReferenceFrame = Number(details.geometry_reference_frame ?? item.manualBaselineFrame ?? details.manual_baseline_frame ?? NaN)
  const geometryReferenceSource = details.geometry_reference_source === 'confirmed-normal' ? 'confirmed-normal' as const : 'manual' as const
  const referenceName = geometryReferenceSource === 'confirmed-normal' ? '已确认正常的框' : '最近人工标注'
  const geometryReference = {
    geometryReferenceFrame: Number.isFinite(geometryReferenceFrame) ? geometryReferenceFrame : undefined,
    geometryReferenceSource,
  }
  const reviewStart = Number(item.reviewStartFrame ?? details.review_start_frame)
  const reviewEnd = Number(item.reviewEndFrame ?? details.review_end_frame)
  const lookback = Number(item.reviewLookbackFrames ?? details.review_lookback_frames ?? 5)
  const reviewRange = Number.isFinite(reviewStart) && Number.isFinite(reviewEnd)
    ? `第 ${reviewStart + 1}～${reviewEnd + 1} 帧`
    : undefined
  const reviewNotice = `该错误可能在触发暂停前已经逐渐出现，请同时检查前 ${Number.isFinite(lookback) ? lookback : 5} 帧的框是否已经开始变形、缩小或扩大。`
  const metrics = [
    formatMetric('面积倍率', item.metrics?.areaRatio ?? details.area_ratio ?? item.ratio),
    formatMetric('宽度倍率', item.metrics?.widthRatio ?? details.width_ratio),
    formatMetric('高度倍率', item.metrics?.heightRatio ?? details.height_ratio),
    formatMetric('长宽比倍率', item.metrics?.aspectRatio ?? details.aspect_ratio_ratio),
    formatMetric(details.motion_reference_kind === 'head' ? '主体位移(px)' : '中心偏移(px)', details.center_shift),
    formatMetric('重叠覆盖率', item.metrics?.overlapCoverage ?? details.overlap_coverage),
  ].filter(Boolean)

  if (type === 'coverage_loss' || type === 'identity_loss') {
    return {
      objectId: item.object_id,
      displayName,
      title: type === 'coverage_loss' ? '目标覆盖可能不完整' : '目标主体或身份存在风险',
      ...geometryReference,
      ...evidence,
      summary: `${displayName} 在原图与模型掩膜的核验中出现持续风险，请检查主体是否跟对、是否漏掉尾部或混入其他对象。`,
      metrics,
      baselineFrame: Number.isFinite(baselineFrame) ? baselineFrame : undefined,
      reviewRange,
      suggestion: '这些是追踪检查线索；请结合原图核验。全部对象正确可确认无异常，有误先修框，再确认。',
    }
  }
  if (type === 'overlap') {
    const otherId = Number(item.other_object_id ?? details.other_object_id)
    const otherName = Number.isFinite(otherId) ? objectDisplayName(otherId, item.other_display_name) : '另一个对象'
    return {
      objectId: item.object_id,
      displayName,
      title: '对象重叠异常',
      summary: evidence.overlapPairs.length
        ? `${displayName} 与 ${evidence.overlapPairs.map(pair => `#${pair.otherObjectId} ${objectDisplayName(pair.otherObjectId)}`).join('、')} 出现持续或恶化的重叠，请检查对象边界和身份。`
        : `${displayName} 与 ${otherName} 的框高度重叠，请检查对象边界和身份。`,
      ...evidence,
      acceptsOverlap: true,
      metrics,
      baselineFrame: Number.isFinite(baselineFrame) ? baselineFrame : undefined,
      reviewRange,
      suggestion: '核对这里列出的所有对象组合；框准确可确认无异常，有误先修正。所有待处理对象确认后才续追。',
    }
  }
  const diagonalHard = reasons.some(reason => /^(?:manual|geometry)_diagonal_ratio=/.test(reason) && reason.endsWith(' HARD'))
  if (type === 'size_shrink') {
    return {
      objectId: item.object_id,
      displayName,
      title: diagonalHard ? '标注范围明显缩小' : `框相对${referenceName}明显缩小`,
      ...geometryReference,
      ...evidence,
      summary: diagonalHard ? `${displayName} 的框整体跨度相对${referenceName}明显缩小，请检查是否遗漏目标的主体或尾部。`
        : `${displayName} 的框相对${referenceName}明显缩小，可能已经丢失精子尾部。`,
      metrics,
      baselineFrame: Number.isFinite(baselineFrame) ? baselineFrame : undefined,
      reviewRange,
      reviewNotice,
      suggestion: '检查当前帧与前几帧；框准确时可直接确认无异常，有误时先重新框住完整目标再确认。',
    }
  }
  if (type === 'size_growth' || type === 'shape_change') {
    return {
      objectId: item.object_id,
      displayName,
      title: diagonalHard ? '标注范围明显扩大' : `框相对${referenceName}明显扩大或变形`,
      ...geometryReference,
      ...evidence,
      summary: diagonalHard ? `${displayName} 的框整体跨度相对${referenceName}明显扩大，可能混入邻近对象，请检查框的覆盖范围。`
        : `${displayName} 的框相对${referenceName}明显扩大或变形，可能框入了杂质或其他精子。`,
      metrics,
      baselineFrame: Number.isFinite(baselineFrame) ? baselineFrame : undefined,
      reviewRange,
      reviewNotice,
      suggestion: '框准确时可直接确认无异常；若框入其他对象，先修正为只覆盖当前目标，再确认。',
    }
  }
  if (type === 'disappearance') {
    return {
      objectId: item.object_id,
      displayName,
      title: '对象在画面内消失',
      ...evidence,
      summary: `${displayName} 未在边界附近被检测到，无法确认其是否正常离场。`,
      metrics,
      baselineFrame: Number.isFinite(baselineFrame) ? baselineFrame : undefined,
      reviewRange,
      suggestion: '确认对象是否被遮挡、合并到其他对象或确实离开画面；必要时补框或删除该对象。',
    }
  }
  return {
    objectId: item.object_id,
    displayName,
    title: '追踪运动异常',
    ...evidence,
    summary: `${displayName} 相对上一帧发生异常位置跳变。`,
    metrics,
    baselineFrame: Number.isFinite(baselineFrame) ? baselineFrame : undefined,
    reviewRange,
    suggestion: '检查当前帧对象身份和位置，修正框后从当前帧继续追踪。',
  }
}
