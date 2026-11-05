// 运行 SAM3 追踪：提交当前帧种子、启动/轮询/续追任务、失败重试与端点追踪。
import { createRequestId } from '../../utils/browserCompat'
import { computed } from 'vue'
import type { TrackingProgressInput } from '../../annotation/trackingProgress'
import type { AnnotationObject, MediaAsset, SavedAnnotationFile } from '../../types/annotation'
import { trackApi, type TrackStatusResponse } from '../../api/trackApi'
import { useAuth } from '../auth'
import { annotationsByMedia, anomalyFrames, anomalyObjectIds, anomalyPanelVisible, api, clone, currentFrame, currentMediaId, currentObjects, currentTime, exactFrameLoading, frameError, getNextObjectId, isAiBusy, isObjectDeleted, lastPausedContext, maxFrameIndex, mediaAssets, messageOf, objectNameInput, pausedAnomalies, pendingDeletion, pendingFeedback, pendingRestart, runtime, segmentBusy, selectedObjectId, showToast, statusMessage, submissionBusy, trackingErrors, trackingFinishedByMedia, trackingFrameCount, trackingFramesByMedia, trackingJobs, trackingMedia, trackingProgressByMedia, trackingRestartError, type TrackingRestartIntent, trackingStatusByMedia, trackingWarningSummary, videoDeletionHistory, workspaceGenerations, workspaceReadError } from './state'
import { histories, historyRevision } from './history'
import { edgeDiscovery, normalizeAnnotationObject } from './editing'
import { frameToTime, seekVideo } from './frames'
import { definiteRejection, persistWorkspaceState, rememberControls, scheduleWorkspaceStateSave } from './save'
import { rememberRestart } from './trackingActions'
import { canLearnNormalMotion, explainPausedObject, getMediaPixelSize, isGeometryReason, loadTrackingResult } from './trackingResult'
import { savedResults } from './results'

export const runAiSegment = async () => {
  const mediaId = currentMediaId.value
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media) return
  const mediaType = media.type
  segmentBusy.value = true
  statusMessage.value = mediaType === 'video' ? `AI 正在处理视频第 ${currentFrame.value + 1} 帧……` : 'AI 正在处理图片……'
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
    let nextId = getNextObjectId(mediaId)
    const normalizedObjects = result.objects.map((obj: AnnotationObject) => {
      const id = typeof obj.objectId === 'number' ? obj.objectId : nextId++
      return normalizeAnnotationObject({ ...obj, objectId: id }, id)
    })
    annotationsByMedia.value = {...annotationsByMedia.value,[mediaId]:[...existing, ...normalizedObjects]}
    selectedObjectId.value = normalizedObjects[0]?.id ?? null
    statusMessage.value = `AI 完成：${normalizedObjects.length} 个目标${mediaType === 'video' ? '（当前帧）' : ''}`
  } catch (error) {
    if (mediaId === currentMediaId.value) statusMessage.value = error instanceof Error ? error.message : 'AI 处理失败'
  } finally {
    segmentBusy.value = false
  }
}

type TrackingSeedSnapshot = {frameIndex:number;timestampMs:number;objects:AnnotationObject[]}
export const buildCurrentFrameSam3Objects = async (mediaId: string, snapshot?: TrackingSeedSnapshot) => {
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media) return []
  const frameIndex = snapshot?.frameIndex ?? currentFrame.value, timestampMs = snapshot?.timestampMs ?? Math.round(currentTime.value * 1000)
  const objects = snapshot?.objects ?? (annotationsByMedia.value[mediaId] ?? []).filter((obj) => (obj.frameIndex ?? 0) === currentFrame.value)
  if (!objects.length) throw new Error(`第 ${currentFrame.value + 1} 帧没有人工标注`)

  // ID 完整性是断点续追的硬约束：不能在生成 seed JSON 时丢失 objectId，
  // 更不能让后端按 annotations 数组顺序重新编号。否则下一轮会出现 15→8 之类的 identity switch。
  const seenIds = new Set<number>()
  for (const obj of objects) {
    if (!Number.isInteger(obj.objectId) || (obj.objectId as number) <= 0) {
      throw new Error(`第 ${currentFrame.value + 1} 帧存在缺少有效 objectId 的标注（${obj.name || obj.id}），请删除后重新框选`)
    }
    if (seenIds.has(obj.objectId as number)) {
      throw new Error(`第 ${currentFrame.value + 1} 帧存在重复 objectId=${obj.objectId}，请修正后再进行 AI Tracking`)
    }
    seenIds.add(obj.objectId as number)
  }

  const { width, height } = await getMediaPixelSize(media)
  const clampX = (v: number) => Math.min(width, Math.max(0, v))
  const clampY = (v: number) => Math.min(height, Math.max(0, v))
  return objects.map((obj) => {
    const objectId = obj.objectId as number
    const item: Record<string, unknown> = {
      id: obj.id,
      object_id: objectId,
      objectId,
      name: obj.name,
      source: obj.source,
      frameIndex,
      timestampMs,
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

/**
 * AI Tracking 开始前自动把当前帧的人工标注写入数据库。
 * 每次 AI Tracking 点击时自动提交当前帧人工标注。
 */
const persistCurrentFrameManualAnnotations = async (media: MediaAsset, mediaId: string, snapshot?: TrackingSeedSnapshot) => {
  const frameIndex = snapshot?.frameIndex ?? currentFrame.value, timestampMs = snapshot?.timestampMs ?? Math.round(currentTime.value * 1000)
  const current = (snapshot?.objects ?? annotationsByMedia.value[mediaId] ?? []).filter(
    (obj) => (obj.frameIndex ?? 0) === frameIndex && obj.source === 'manual'
  )
  if (!current.length) return { count: 0, batchId: undefined as string | undefined }

  const pixel = await getMediaPixelSize(media).catch(() => null)
  const canonicalMediaId = media.serverMediaId || mediaId
  const result = await api.saveManualAnnotation({
    mediaId: canonicalMediaId,
    generationId: workspaceGenerations.get(mediaId),
    mediaType: media.type,
    mediaName: media.name,
    mediaWidth: pixel?.width ?? media.width,
    mediaHeight: pixel?.height ?? media.height,
    frameIndex: media.type === 'video' ? frameIndex : undefined,
    timestampMs: media.type === 'video' ? timestampMs : undefined,
    objects: JSON.parse(JSON.stringify(current)),
    annotationVersion: 'annotation-v8-ai-tracking-autosave',
  })

  // 让标注结果页/工作区本地结果同步显示本次自动入库批次。
  const savedResult = {
    mediaId: canonicalMediaId,
    mediaName: media.name,
    mediaType: media.type,
    frameIndex: media.type === 'video' ? frameIndex : undefined,
    timestampMs: media.type === 'video' ? timestampMs : undefined,
    savedAt: new Date().toISOString(),
    filename: '',
    batchId: result.batchId,
    objects: JSON.parse(JSON.stringify(current)),
  } as SavedAnnotationFile
  savedResults.value = [
    savedResult,
    ...savedResults.value.filter((item) => !(
      item.mediaId === canonicalMediaId
      && (item.frameIndex ?? 0) === frameIndex
      && item.username === savedResult.username
    )),
  ]

  return { count: current.length, batchId: result.batchId }
}

export const trackingJournalKey = (mediaId:string)=>`annotation-tracking:${useAuth().user.value?.id}:${mediaId}`
export const rememberTracking = (mediaId:string) => {
  try{const job=trackingJobs.get(mediaId);if(job)sessionStorage.setItem(trackingJournalKey(mediaId),JSON.stringify(job));else sessionStorage.removeItem(trackingJournalKey(mediaId))}
  catch(error){console.warn('[annotation.tracking_journal_failed]',{mediaId,error})}
}
export const performTracking = async (resume=false, restartIntent?: TrackingRestartIntent, endpointMode=false) => {
  const mediaId = currentMediaId.value
  if(trackingMedia.has(mediaId))return
  const media = mediaAssets.value.find((item) => item.id === mediaId)
  if (!media || media.type !== 'video' || !media.serverMediaId) {
    statusMessage.value = '请先上传视频'
    return
  }

  let job=resume?trackingJobs.get(mediaId):undefined
  const startFrame = job?.startFrame ?? restartIntent?.body.startFrame ?? (endpointMode ? 0 : currentFrame.value)
  const restartSerial = runtime.restartEpoch
  let preparingRestartSeed = !!restartIntent
  const restartSeedSnapshot: TrackingSeedSnapshot | undefined = restartIntent || endpointMode ? {frameIndex:startFrame,timestampMs:Math.round(frameToTime(startFrame)*1000),objects:clone((annotationsByMedia.value[mediaId]??[]).filter(object=>(object.frameIndex??0)===startFrame && (!endpointMode || object.source==='manual')))} : undefined
  const terminalSnapshot: TrackingSeedSnapshot | undefined = endpointMode ? {frameIndex:maxFrameIndex.value,timestampMs:Math.round(frameToTime(maxFrameIndex.value)*1000),objects:clone((annotationsByMedia.value[mediaId]??[]).filter(object=>(object.frameIndex??0)===maxFrameIndex.value && object.source==='manual'))} : undefined
  // Until the job is launched a restart depends on this page's view; afterwards the
  // durable job keeps polling in the background even if the user opens another video.
  let launched = false
  const restartAbandoned = () => !!restartIntent && (restartIntent.actorId !== useAuth().user.value?.id || (!launched && (restartSerial !== runtime.restartEpoch || mediaId !== currentMediaId.value)))
  const ensureRestartCurrent = () => {
    if (restartAbandoned()) throw new DOMException('账号或素材已切换，已停止本页续追', 'AbortError')
    if (preparingRestartSeed && (currentFrame.value !== startFrame || exactFrameLoading.value || !!frameError.value)) throw new Error('重追起始帧已变化或尚未读取完成，未继续写入起始框；请重试从原起点追踪')
    if (preparingRestartSeed && submissionBusy.value) throw new Error('当前有导出操作尚未结束，已停止准备起始框；请完成导出后再继续原起点追踪')
  }
  let phase='保存工作区'
  let seed: Record<string, unknown>[]
  if (pendingDeletion.value || pendingFeedback.value || (pendingRestart.value && pendingRestart.value !== restartIntent) || workspaceReadError.value || (isAiBusy.value&&!resume)) return
  if (!job&&lastPausedContext.value?.mediaId === mediaId && pausedAnomalies.value.some(item => !isObjectDeleted(mediaId, item.objectId, lastPausedContext.value!.frameIndex))) {
    anomalyPanelVisible.value = true
    showToast('请先逐项确认暂停对象，或修正框后确认，再继续追踪','warning')
    return
  }
  if (!job&&!(restartSeedSnapshot?.objects ?? currentObjects.value).some(object => object.bbox)) { showToast('起始帧没有人工头框，请先完成首帧标注','warning'); return }
  anomalyObjectIds.value = []
  pausedAnomalies.value = []
  anomalyPanelVisible.value = false
  trackingMedia.add(mediaId)
  const progressEnd = maxFrameIndex.value
  const progressInput = (patch: Partial<TrackingProgressInput> = {}): TrackingProgressInput => {
    const endpoint = (job?.input.terminalAnnotations ?? terminalSnapshot) !== undefined
    return {...trackingStatusByMedia.value[mediaId], startFrame, endFrame: progressEnd, endpoint, combine: endpoint && (job ? !!job.input.combineResults : true), ...patch}
  }
  delete trackingFinishedByMedia.value[mediaId]
  trackingStatusByMedia.value[mediaId] = progressInput()
  let trackingSucceeded = false

  try {
    let task: {taskId:string;maxFrames?:number;trackFrames?:number}
    if(job){
      phase=job.taskId?'查询追踪任务':'确认启动结果'
      task=job.taskId?{taskId:job.taskId,maxFrames:job.maxFrames}:await trackApi.run(job.input,job.key)
      job.taskId=task.taskId;job.maxFrames=task.maxFrames;rememberTracking(mediaId);launched=true
    }else{
    const terminalAnnotations = terminalSnapshot ? (terminalSnapshot.objects.length ? await buildCurrentFrameSam3Objects(mediaId, terminalSnapshot) : []) : undefined
    // 固化完整工作区（人工框、稳定 ID/名称、人工基准和异常状态），
    // 确保后端本轮 Tracking 与以后重新加载使用同一份人工基准。
    if (!restartIntent) await persistWorkspaceState(mediaId)
    ensureRestartCurrent()
    // ① AI Tracking 点击即自动落库：当前帧人工标注先进入数据库，
    // 然后才写 seed JSON / 启动 SAM3。任何一步失败都不会“假保存”。
    statusMessage.value = `① 正在将第 ${startFrame + 1} 帧人工标注写入数据库……`
    phase='保存人工标注记录'
    const persisted = await persistCurrentFrameManualAnnotations(media, mediaId, restartSeedSnapshot)
    ensureRestartCurrent()
    if (persisted.count) {
      showToast(`第 ${startFrame + 1} 帧 ${persisted.count} 个人工标注已写入数据库`,'success')
    }

    // 关键：如果这是对旧 Tracking 结果的人工回退/修正，从当前帧重新开分支。
    // 服务端只删除 tracker_results.json 中 frame > startFrame 的旧结果。
    // 当前帧保留，作为新的分支锚点；其上的 AI 框和人工修改/新增框会一起成为新 seed。
    statusMessage.value = `①b 正在从第 ${startFrame + 1} 帧切断旧 Tracking 未来分支……`
    phase='整理旧追踪分支'
    const rewind = restartIntent ? null : await trackApi.rewind({
      mediaId: media.serverMediaId,
      generationId: workspaceGenerations.get(mediaId),
      startFrame,
    })
    ensureRestartCurrent()
    // The future branch is now replaced. Old AI snapshots can no longer be
    // durably restored by an editor-only undo, so invalidate that history.
    delete videoDeletionHistory.value[mediaId]
    for (const key of histories.keys()) if (key.startsWith(`${mediaId}:`)) histories.delete(key)
    historyRevision.value++; rememberControls(mediaId)
    const existingAfterRewind = annotationsByMedia.value[mediaId] ?? []
    // 当前帧 N 是新的分支锚点：保留此前所有结果以及 N 帧现有 AI/人工框。
    // 只有 N 之后的旧未来轨迹被清掉；随后 buildCurrentFrameSam3Objects() 会
    // 把 N 帧当前页面上最终存在的全部 bbox（旧 AI + 人工新增/修改）作为新 seed。
    annotationsByMedia.value = {
      ...annotationsByMedia.value,
      [mediaId]: existingAfterRewind.filter((obj) => obj.source === 'manual' || (obj.frameIndex ?? 0) <= startFrame),
    }
    trackingFramesByMedia.value[mediaId] = (trackingFramesByMedia.value[mediaId] ?? [])
      .filter((frame) => frame.frameIndex <= startFrame)
    console.info(
      `[ai-track] ${restartIntent ? 'confirmed restart' : 'rewind'} branch at frame=${startFrame}: removedRows=${rewind?.removedRows ?? 0}, ` +
      `deletedFutureSeedFiles=${rewind?.deletedFutureSeedFiles ?? 0}`
    )

    seed = await buildCurrentFrameSam3Objects(mediaId, restartSeedSnapshot)
    ensureRestartCurrent()
    if (!seed.length) {
      throw new Error(`第 ${startFrame + 1} 帧没有人工 bbox 标注`)
    }
    // 不能直接使用 media.width || 0：在 AVI 浏览器播放降级为逐帧预览时，
    // 尺寸来自后端上传元数据。统一从该函数获取，保证 seed 与 Tracking
    // 请求使用同一套像素坐标系。
    const pixelSize = await getMediaPixelSize(media)
    ensureRestartCurrent()

    // Phase 2: 把“当前页面正在看的这一帧”固化成后端 JSON seed。
    statusMessage.value = `② 正在生成第 ${startFrame + 1} 帧标注 JSON……`
    phase='保存追踪起始框'
    await trackApi.saveFrameAnnotations({
      mediaId: media.serverMediaId,
      generationId: workspaceGenerations.get(mediaId),
      mediaName: media.name,
      mediaWidth: pixelSize.width,
      mediaHeight: pixelSize.height,
      frameIndex: startFrame,
      timestampMs: restartSeedSnapshot?.timestampMs ?? Math.round(currentTime.value * 1000),
      annotations: seed,
    })
    ensureRestartCurrent()

    // 后端以 SAM3_TRACK_FRAMES（包含 seed 帧）和剩余视频帧数为上限。
    statusMessage.value = `③ SAM3 将从第 ${startFrame + 1} 帧持续向后追踪，疑点留待审查，分段自动续跑至视频末尾……`
    const input = {
      mediaId: media.serverMediaId,
      generationId: workspaceGenerations.get(mediaId),
      mediaName: media.name,
      mediaWidth: pixelSize.width,
      mediaHeight: pixelSize.height,
      startFrame,
      edgeDiscovery: edgeDiscovery.value,
      reviewMode: true,
      ...(terminalAnnotations !== undefined ? {terminalAnnotations,combineResults:true} : {}),
      annotations: seed,
    }
    phase='启动追踪任务'
    preparingRestartSeed = false
    trackingJobs.set(mediaId,{mediaId,startFrame,input,key:createRequestId()})
    job=trackingJobs.get(mediaId)!;rememberTracking(mediaId);launched=true
    if (restartIntent && pendingRestart.value === restartIntent) {
      // The durable tracking journal now owns recovery. Replaying a restart
      // after this point must never truncate newly generated model results.
      rememberRestart(restartIntent, true); pendingRestart.value = null; trackingRestartError.value = ''
    }
    task=await trackApi.run(input,job.key)
    ensureRestartCurrent()
    job.taskId=task.taskId;job.maxFrames=task.maxFrames;rememberTracking(mediaId)
    }

    if (mediaId === currentMediaId.value) {
      trackingFrameCount.value = task.maxFrames || task.trackFrames || 1
      statusMessage.value =
        `SAM3 正在运行：处理第 ${startFrame + 1}～${Math.min(maxFrameIndex.value, startFrame + trackingFrameCount.value - 1) + 1} 帧`
    }

    let completedStatus: TrackStatusResponse|undefined
    for (;;) {
      phase='查询追踪任务'
      const status = await trackApi.getStatus(task.taskId)
      ensureRestartCurrent()
      trackingStatusByMedia.value[mediaId] = progressInput({status:status.status,stage:status.stage,phase:status.phase,phaseFrom:status.phaseFrom,phaseTo:status.phaseTo,phaseCurrent:status.phaseCurrent,lastProcessedFrame:status.lastProcessedFrame,elapsedSeconds:status.elapsedSeconds})
      trackingProgressByMedia.value[mediaId]=status.stage==='decoding'?'正在读取本轮原始帧':status.stage==='preparing_model'?'正在准备模型和输入':status.stage==='checking'?(status.message||'正在核验疑点片段'):status.stage==='saving_results'?'正在保存追踪结果':status.lastProcessedFrame!=null?`正在追踪第 ${status.lastProcessedFrame+1} 帧`:status.status==='queued'?'排队中：其他视频的追踪完成后自动开始，可先标注其他视频':'正在追踪'

      if (mediaId === currentMediaId.value) trackingWarningSummary.value = status.warningSummary ?? []
      if (status.status === 'success') {completedStatus=status;break}
      if (status.status === 'failed') {
        trackingJobs.delete(mediaId);rememberTracking(mediaId)
        phase='模型追踪'
        throw new Error(status.message || 'SAM3 Tracking 失败')
      }
      if (status.status === 'paused' && status.paused) {
        const pauseFrame = status.pausedFrame ?? startFrame
        phase='读取追踪结果'
        await loadTrackingResult(mediaId, true, true, true)
        ensureRestartCurrent()

        if (mediaId === currentMediaId.value) {
          await seekVideo(frameToTime(pauseFrame))
          const levels = status.anomalyLevels || {}
          anomalyObjectIds.value = Object.entries(levels)
            .filter(([, v]) => v !== 'normal')
            .map(([k]) => Number(k))
          const pausedObjs = status.pausedObjects || []
          pausedAnomalies.value = pausedObjs.map(item => ({
            ...explainPausedObject(item),
            rawReasons: item.reasons ?? [],
            canLearn: canLearnNormalMotion(item.reasons ?? []),
            acceptsGeometry: ['size_shrink', 'size_growth', 'shape_change'].includes(item.type ?? '') || (item.reasons ?? []).some(isGeometryReason),
          }))
          lastPausedContext.value = { mediaId, frameIndex: pauseFrame }
          anomalyPanelVisible.value = true
          const reasons = pausedAnomalies.value.map((item) => `${item.displayName}：${item.title}`)
          const level = Object.values(levels).find((v) => v && v !== 'normal') || 'anomaly'
          anomalyFrames.value = [
            ...anomalyFrames.value,
            { frame_index: pauseFrame, level, reasons },
          ]
          statusMessage.value = `⚠️ Tracking 暂停在第 ${pauseFrame + 1} 帧: ${reasons.join('; ')}`
          showToast(`检测到异常，已暂停在第 ${pauseFrame + 1} 帧`,'warning')
        }
        trackingJobs.delete(mediaId);rememberTracking(mediaId);delete trackingErrors.value[mediaId]
        return
      }
      await new Promise((resolve) => setTimeout(resolve, 700))
    }

    if(job?.input.terminalAnnotations!==undefined && !completedStatus?.endpointVerification){
      phase='校验首尾核验结果'
      trackingJobs.delete(mediaId);rememberTracking(mediaId)
      throw new Error('后端没有返回首尾反向核验证据，请更新并重启后端后重试；不能视为双向核验完成')
    }
    if(job?.input.combineResults && completedStatus?.jointTracking?.state!=='checked'){
      phase='读取自动标注状态'
      trackingJobs.delete(mediaId);rememberTracking(mediaId)
      throw new Error('后端未完成综合标注，请更新并重启后端后重试；不能视为自动标注完成')
    }
    phase='读取追踪结果'
    trackingStatusByMedia.value[mediaId] = progressInput({loadingResult:true})
    await loadTrackingResult(mediaId, true, true, true)
    ensureRestartCurrent()

    if (mediaId === currentMediaId.value) {
      const finalStatus = completedStatus!
      trackingWarningSummary.value = finalStatus.warningSummary ?? []
      const lastProcessedFrame = finalStatus.lastProcessedFrame ?? startFrame
      await seekVideo(frameToTime(lastProcessedFrame))
      if (finalStatus.reachedVideoEnd || lastProcessedFrame >= maxFrameIndex.value) {
        statusMessage.value = `完成：已追踪到视频末尾第 ${lastProcessedFrame + 1} 帧。`
        if(finalStatus.jointTracking?.state==='checked') statusMessage.value='自动标注已完成，可微调后去审查。'
        else if(finalStatus.endpointVerification) statusMessage.value += finalStatus.endpointVerification.state==='checked'?'首尾核验完成，疑点请到审查页处理。':'反向核验失败或证据不足，请到审查页检查。'
        showToast('已追踪到视频末尾','success')
      } else {
        statusMessage.value = `完成：SAM3 已追踪第 ${startFrame + 1}～${lastProcessedFrame + 1} 帧。请检查/修正最终帧后再次点击 AI Tracking 继续。`
        showToast(`已到达单轮上限第 ${lastProcessedFrame + 1} 帧，可检查后继续`)
      }
    }
    trackingJobs.delete(mediaId);rememberTracking(mediaId);delete trackingErrors.value[mediaId]
    trackingSucceeded = true
  } catch (error) {
    if (restartAbandoned()) return
    if (job&&(definiteRejection(error)||messageOf(error,'').includes('此服务器未启用 AI Tracking'))) {trackingJobs.delete(mediaId);rememberTracking(mediaId)}
    const message=phase==='查询追踪任务'&&Number((error as {status?:number})?.status)===404?'后台任务已不存在，服务器可能已重启。请检查已保存的结果，再重新追踪。':messageOf(error,'AI Tracking 失败')
    trackingErrors.value[mediaId]={phase,frameIndex:startFrame,message,endpointMode:endpointMode || job?.input.terminalAnnotations!==undefined}
    console.error('[annotation.tracking_failed]',{mediaId:media.serverMediaId,frameIndex:startFrame,phase,taskId:job?.taskId,requestKey:job?.key,error})
    if (mediaId === currentMediaId.value) {
      statusMessage.value = `${phase}失败：${message}`
    }
  } finally {
    // This call added mediaId to trackingMedia; no other loop can own the video meanwhile.
    trackingMedia.delete(mediaId)
    delete trackingProgressByMedia.value[mediaId]
    delete trackingStatusByMedia.value[mediaId]
    if (trackingSucceeded) {
      trackingFinishedByMedia.value[mediaId] = true
      setTimeout(() => { delete trackingFinishedByMedia.value[mediaId] }, 2600)
    }
    if (!restartAbandoned() && !trackingJobs.has(mediaId) && mediaId === currentMediaId.value) scheduleWorkspaceStateSave(mediaId)
  }
}
export const runAiTrack = () => performTracking()
export const runEndpointTrack = () => performTracking(false, undefined, true)
export const trackingRetryLabel = computed(()=>trackingJobs.get(currentMediaId.value)?.taskId?'重新查询追踪任务':trackingJobs.has(currentMediaId.value)?'重试原追踪请求':'重试追踪')
export const retryTracking = () => performTracking(true, undefined, !!trackingErrors.value[currentMediaId.value]?.endpointMode)
