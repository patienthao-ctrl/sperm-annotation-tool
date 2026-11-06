// Real annotation controls, durable backend writes, and lost-response replay.
// Only subsequent GPU inference is mocked; source frames/feedback/deletions are real.
import assert from 'node:assert/strict'
import fs from 'node:fs'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const fixture = JSON.parse(fs.readFileSync(process.env.ANNOTATION_CONTROLS_FIXTURE || 'work/annotation-controls-browser-fixture.json', 'utf8'))
const origin = process.env.ANNOTATION_ORIGIN || process.env.CONFIRMATION_ORIGIN || 'http://127.0.0.1:5373'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({reducedMotion:'reduce',viewport: { width: 1440, height: 900 } })
page.setDefaultTimeout(15000)
const errors = [], consoleLog = [], checks = [], trackStarts = [], feedbackAttempts = [], deleteAttempts = []
let loseFeedbackResponse = false, loseDeletionResponse = false
fs.mkdirSync('output/playwright', { recursive: true })
page.on('pageerror', error => errors.push(String(error)))
page.on('console', message => consoleLog.push({ type: message.type(), text: message.text() }))
const check = (value, message) => { assert(value, message); checks.push(message); console.log('PASS', checks.length, message) }
function sameManualRecords(actual, expected) {
  // Percent-to-pixel round trips can turn 100 into 99.99999999999999.
  // Keep identity, source, frame, and every non-coordinate field exact.
  assert.deepEqual(actual.map(({ bbox, ...metadata }) => metadata), expected.map(({ bbox, ...metadata }) => metadata))
  actual.forEach((record, index) => {
    const before = expected[index].bbox, after = record.bbox
    assert.equal(Array.isArray(after), Array.isArray(before))
    assert.deepEqual(Object.keys(after).sort(), Object.keys(before).sort())
    for (const [coordinate, value] of Object.entries(after)) {
      assert(Number.isFinite(value) && Number.isFinite(before[coordinate]) && Math.abs(value - before[coordinate]) <= 1e-9,
        `manual record ${index} bbox.${coordinate} changed: ${before[coordinate]} -> ${value}`)
    }
  })
  return true
}
const button = name => page.getByRole('button', { name, exact: true })
const ids = () => page.evaluate(() => window.ws.currentObjects.value.map(object => object.objectId).sort((a, b) => a - b))
const idle = () => page.waitForFunction(() => window.ws && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && !window.ws.isAiBusy.value)
const saved = () => page.waitForFunction(() => window.ws.saveState.value === 'saved')
const attachStore = () => page.evaluate(async () => {
  const resource = performance.getEntriesByType('resource').find(item => item.name.includes('/src/stores/workspace.ts'))
  window.ws = (await import(resource.name)).useWorkspace()
})
async function api(path) {
  const response = await page.request.get(origin + '/api' + path, { headers: { Authorization: `Bearer ${fixture.token}` } })
  assert(response.ok(), `${path}: ${response.status()}`)
  return response.json()
}
const workspace = mid => api(`/track/workspace/${mid}`)
const resultRows = async mid => (await api(`/track/result/${mid}`)).frames
const countObject = (rows, oid) => rows.filter(row => (row.annotations || row.objects || []).some(object => (object.object_id ?? object.objectId) === oid)).length
async function waitApi(predicate) {
  const until = Date.now() + 15000
  while (Date.now() < until) {
    if (await predicate()) return
    await new Promise(resolve => setTimeout(resolve, 80))
  }
  assert.fail('Timed out waiting for durable backend state')
}
async function go(frame) {
  const input = page.getByRole('spinbutton', { name: '跳转帧号' })
  await input.fill(String(frame + 1)); await input.press('Enter')
  await page.waitForFunction(value => window.ws.currentFrame.value === value && !window.ws.exactFrameLoading.value, frame)
  await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur())
}
async function media(mid) {
  await openAsset(page,page.locator('.asset-card').filter({ hasText: mid + '.avi' }))
  await page.waitForFunction(value => window.ws.selectedMedia.value?.serverMediaId === value && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value, mid)
}
async function select(oid) { await page.getByRole('button', { name: new RegExp(`选择对象 ${oid} `) }).click() }
async function reload() { await saved(); await page.reload(); await attachStore(); await idle() }
async function openDeletion(expectedFrames) {
  await page.getByTestId('delete-object-video').click()
  await page.locator('[data-object-delete-dialog][open]').waitFor()
  await page.waitForFunction(count => document.querySelector('.object-delete-scope dd')?.textContent?.trim() === String(count), expectedFrames)
}

try {
  await page.addInitScript(data => {
    localStorage.setItem('rare-sperm-token', data.token)
    localStorage.setItem('rare-sperm-auth', JSON.stringify({ id: String(data.userId), name: 'Annotation controls tester' }))
  }, fixture)
  await page.route('**/api/track/media', async route => {
    const response = await route.fetch(), data = await response.json()
    data.items = data.items.filter(item => fixture.mediaIds.includes(item.mediaId))
    await route.fulfill({ response, json: data })
  })
  await page.route('**/api/track/workspace/controls-delete', async route => {
    if (route.request().method() !== 'PUT') return route.continue()
    const body = route.request().postDataJSON()
    if (body.deletedObjectIds?.includes(7)) {
      deleteAttempts.push({ key: route.request().headers()['idempotency-key'], body })
      if (loseDeletionResponse) {
        loseDeletionResponse = false
        const response = await route.fetch(); assert(response.ok(), 'injected deletion response was persisted first')
        return route.abort('failed')
      }
    }
    return route.continue()
  })
  await page.route('**/api/track/feedback/controls-feedback-failure', async route => {
    feedbackAttempts.push({ key: route.request().headers()['idempotency-key'], body: route.request().postDataJSON() })
    if (loseFeedbackResponse) {
      loseFeedbackResponse = false
      const response = await route.fetch(); assert(response.ok(), 'injected feedback response was persisted first')
      return route.abort('failed')
    }
    return route.continue()
  })
  await page.route('**/api/track', async route => {
    const request = route.request().postDataJSON()
    trackStarts.push(request)
    await route.fulfill({ json: { taskId: 'controls-simulated-' + request.mediaId, status: 'queued', maxFrames: 2 } })
  })
  await page.route('**/api/track/status/controls-simulated-*', route => {
    const shape = route.request().url().endsWith('controls-shape')
    return route.fulfill({ json: {
      status: 'success', lastProcessedFrame: shape ? fixture.shapePausedFrame + 1 : 4, reachedVideoEnd: false,
      warningSummary: shape ? [] : [{ objectId: 7, reason: 'motion', count: 1, firstFrame: 4, lastFrame: 4, calibrated: true }],
    } })
  })

  await page.goto(origin + '/annotate'); await attachStore(); await idle()
  await media('controls-delete'); await go(0)
  check(JSON.stringify(await ids()) === '[7,12]', 'same-name objects retain separate stable IDs in the real frame')
  await select(7)
  check((await page.getByTestId('selected-object-actions').innerText()).includes('对象 7'), 'object-list selection drives the action area')
  await page.screenshot({ path: 'output/playwright/annotation-controls-objects.png', animations: 'disabled' })
  await page.getByTestId('delete-object-frame').click()
  check(JSON.stringify(await ids()) === '[12]', 'single-frame deletion removes only the selected object')
  check(await page.getByText('在本帧重新画此对象', { exact: true }).count() === 0, 'deletion has undo instead of the confusing redraw action')
  await saved()
  let rows = await resultRows('controls-delete')
  check(countObject(rows, 7) === 23 && countObject(rows, 12) === 24, 'server output applies the single-frame tombstone without affecting other IDs')
  await page.locator('.object-deletion-feedback').getByRole('button', { name: '撤销', exact: true }).click(); await saved()
  check(JSON.stringify(await ids()) === '[7,12]', 'undo restores the exact original identity in the current frame')
  check(countObject(await resultRows('controls-delete'), 7) === 24, 'single-frame undo restores durable effective annotations')
  await go(3); await select(7); await page.getByTestId('delete-object-frame').click(); await saved()
  await go(4)
  check((await ids()).includes(7), 'deleting frame four preserves the next frame')
  await go(3)
  let nativeErrorDuringRestore = false
  await page.route('**/api/track/frame/controls-delete/3', async route => {
    nativeErrorDuringRestore = await page.evaluate(() => {
      const video = document.querySelector('video')
      if (!video) return false
      video.dispatchEvent(new Event('error'))
      return true
    })
    await route.continue()
  })
  await reload()
  check(nativeErrorDuringRestore && await page.evaluate(() => window.ws.currentFrame.value) === 3, 'native AVI decode error during restored exact-frame loading cannot replace the saved target frame')
  await page.unroute('**/api/track/frame/controls-delete/3')
  await media('controls-delete'); await go(3)
  check(!(await ids()).includes(7), 'refresh does not resurrect a deleted single-frame AI box')
  check((await workspace('controls-delete')).deletedFrameObjects.some(item => item.objectId === 7 && item.frameIndex === 3), 'single-frame deletion is persisted by stable object and frame identity')

  await go(4); await select(7); await openDeletion(23)
  const scope = await page.locator('.object-delete-scope').innerText()
  check(scope.includes('#7') && scope.includes('人工标注') && scope.includes('AI 标注'), 'whole-video preview identifies the selected ID and both annotation sources')
  check(JSON.stringify(await page.locator('.object-delete-scope dd').allTextContents()) === '["23","1","22"]', 'whole-video preview counts actual effective annotations after a single-frame deletion')
  await page.screenshot({ path: 'output/playwright/annotation-controls-delete-dialog.png', animations: 'disabled' })
  await page.keyboard.press('Delete')
  check((await ids()).includes(7), 'deletion dialog isolates the underlying Delete shortcut')
  await button('取消').click()
  check(countObject(await resultRows('controls-delete'), 7) === 23, 'cancel leaves every annotation unchanged')

  await openDeletion(23)
  loseDeletionResponse = true
  await page.getByTestId('confirm-delete-object-video').click()
  await page.locator('.object-delete-dialog .error-banner').waitFor()
  check((await ids()).includes(7), 'lost deletion response keeps the local operation pending instead of claiming success')
  check(countObject(await resultRows('controls-delete'), 7) === 0, 'failure injection lost only the response after a real server commit')
  await page.getByTestId('confirm-delete-object-video').click()
  await page.locator('[data-object-delete-dialog][open]').waitFor({ state: 'hidden' }); await saved()
  check(deleteAttempts.length >= 2 && deleteAttempts[0].key && deleteAttempts[0].key === deleteAttempts[1].key, 'whole-video retry reuses its original idempotency key')
  check(JSON.stringify(deleteAttempts[0].body) === JSON.stringify(deleteAttempts[1].body), 'whole-video retry replays the exact original deletion intent')
  check(JSON.stringify(await ids()) === '[12]', 'confirmed whole-video deletion preserves the other same-name object')
  await go(0)
  check(JSON.stringify(await ids()) === '[12]', 'whole-video deletion applies to past manual annotations')
  await go(23)
  check(JSON.stringify(await ids()) === '[12]', 'whole-video deletion applies to the final AI frame')
  await page.locator('.object-deletion-feedback').getByRole('button', { name: '撤销', exact: true }).click(); await saved()
  rows = await resultRows('controls-delete')
  check(countObject(rows, 7) === 23 && countObject(rows, 12) === 24, 'cross-frame undo restores the whole operation and preserves the earlier single-frame deletion')
  await go(0)
  check(await page.getByRole('button', { name: /选择对象 7 / }).getAttribute('aria-pressed') !== null, 'restored object remains selectable with its original stable ID')
  await select(7); await openDeletion(23); await page.getByTestId('confirm-delete-object-video').click()
  await page.locator('[data-object-delete-dialog][open]').waitFor({ state: 'hidden' }); await saved(); await reload(); await media('controls-delete'); await go(10)
  check(JSON.stringify(await ids()) === '[12]', 'whole-video deletion survives browser refresh and tracking reload')
  await page.locator('.object-deletion-feedback').getByRole('button', { name: '撤销', exact: true }).click(); await saved()
  await waitApi(async()=> (await ids()).includes(7) && countObject(await resultRows('controls-delete'),7)===23)
  check((await ids()).includes(7) && countObject(await resultRows('controls-delete'), 7) === 23, 'whole-video undo survives same-tab refresh and still preserves preexisting single-frame deletion')
  await select(7); await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur())
  const restoredX = await page.evaluate(() => window.ws.selectedObject.value.bbox.x)
  await page.keyboard.press('Alt+ArrowRight'); await saved()
  const editedX = await page.evaluate(() => window.ws.selectedObject.value.bbox.x)
  check(editedX > restoredX && await button('重做本帧操作').isDisabled(), 'editing a restored object invalidates the old whole-video redo snapshot')
  await page.keyboard.press('Control+Shift+z')
  check((await ids()).includes(7) && await page.evaluate(() => window.ws.selectedObject.value.bbox.x) === editedX, 'redo shortcut cannot delete the newly edited object using stale deletion history')

  await media('controls-feedback'); await go(3)
  await page.getByTestId('tracking-feedback').waitFor()
  await select(7); await openDeletion(24); await page.getByTestId('confirm-delete-object-video').click()
  await page.locator('[data-object-delete-dialog][open]').waitFor({ state: 'hidden' }); await saved()
  check(await page.getByTestId('tracking-feedback').count() === 0 && !(await ids()).includes(7), 'whole-video deletion removes the deleted paused object from the unresolved queue')
  await page.locator('.object-deletion-feedback').getByRole('button', { name: '撤销', exact: true }).click(); await saved()
  await page.getByTestId('tracking-feedback').waitFor()
  check((await ids()).includes(7) && (await workspace('controls-feedback')).pausedAnomalies.some(item => item.objectId === 7), 'undoing deletion restores the original pending anomaly and its object')
  check(await button('首尾双向追踪').isDisabled(), 'normal AI-start action cannot silently accept an unresolved anomaly')
  await page.screenshot({ path: 'output/playwright/annotation-controls-feedback.png', animations: 'disabled' })
  for (const viewport of [{ width: 1366, height: 768 }, { width: 1180, height: 760 }]) {
    await page.setViewportSize(viewport); await page.waitForTimeout(180)
    const normal = await page.getByTestId('confirm-tracking-normal').boundingBox()
    const corrected = await page.getByTestId('confirm-tracking-corrected').boundingBox()
    check([normal, corrected].every(box => box && box.y >= 0 && box.y + box.height <= viewport.height), `${viewport.width}×${viewport.height} shows both core feedback actions without page scrolling`)
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${viewport.width}×${viewport.height} has no horizontal document overflow`)
    await page.screenshot({ path: `output/playwright/annotation-controls-feedback-${viewport.width}.png`, animations: 'disabled' })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await go(2)
  check(await page.getByTestId('confirm-tracking-normal').isDisabled(), 'normal confirmation is disabled while browsing another frame')
  await button('返回第 4 帧').click(); await idle()
  check(await page.evaluate(() => window.ws.currentFrame.value) === 3, 'return action selects the actual paused source frame')
  const beforeNormal = trackStarts.length
  await page.getByTestId('confirm-tracking-normal').click()
  await waitApi(async () => (await workspace('controls-feedback')).normalMotionSamples.length === 1)
  await page.waitForFunction(() => !window.ws.isAiBusy.value && !window.ws.trackingFeedbackBusy.value && window.ws.currentFrame.value === 4)
  let durable = await workspace('controls-feedback')
  check(durable.normalMotionSamples[0].objectId === 7 && durable.normalMotionSamples[0].reason === 'motion', 'normal feedback persists only the reviewed video/object/reason')
  check(durable.normalMotionSamples[0].features.motionNormalized > 1, 'server derives the normal motion sample from its saved tracking evidence')
  check(trackStarts.length === beforeNormal + 1, 'AI resumes only after explicit normal feedback is saved')
  await page.locator('.tracking-calibration summary').click()
  check((await page.locator('.tracking-calibration').innerText()).includes('对象 7 · 1 次正常运动确认'), 'calibration panel explains the affected object and sample count')
  check((await page.locator('.tracking-calibration').innerText()).includes('异常检测持续开启'), 'UI makes continued anomaly detection explicit')
  await saved(); await reload(); await media('controls-feedback')
  await page.locator('.tracking-calibration summary').click()
  check((await page.locator('.tracking-calibration').innerText()).includes('对象 7 · 1 次正常运动确认'), 'calibration survives reopening the actual saved workspace')
  await page.locator('[data-calibration-object-id="7"]').getByRole('button',{name:'恢复默认判断',exact:true}).click()
  await waitApi(async () => (await workspace('controls-feedback')).normalMotionSamples.length === 0)
  check((await workspace('controls-feedback')).trackingFeedbackEvents.some(event => event.decision === 'reset'), 'reset clears samples while preserving the audit event')

  await media('controls-feedback-once'); await go(3)
  await page.getByTestId('tracking-diagnostics').locator('summary').click()
  await page.locator('.tracking-learn input').uncheck()
  await page.getByTestId('confirm-tracking-normal').click()
  await waitApi(async () => (await workspace('controls-feedback-once')).trackingFeedbackEvents.length === 2)
  await idle()
  durable = await workspace('controls-feedback-once')
  check(durable.normalMotionSamples.length === 0 && durable.trackingFeedbackEvents[0].decision === 'normal', 'unchecking calibration accepts only this event without creating a normal sample')

  await media('controls-shape'); await go(fixture.shapePausedFrame)
  await page.getByTestId('tracking-feedback').waitFor()
  const beforeShape = await workspace('controls-shape')
  await page.getByTestId('tracking-diagnostics').locator('summary').click()
  const geometryNote = await page.getByTestId('tracking-geometry-acceptance').innerText()
  check(geometryNote.includes('尺寸和形状') && geometryNote.includes('无需重画'), 'restored shape pause explains that normal confirmation records geometry without redrawing')
  check(geometryNote.includes('丢失') && geometryNote.includes('重叠') && geometryNote.includes('仍会检查'), 'shape confirmation note retains future loss, overlap, and geometry checks')
  check(await page.locator('.tracking-learn input').count() === 0, 'pure shape feedback does not present unrelated motion-learning controls')
  await page.screenshot({ path: 'output/playwright/annotation-controls-shape.png', animations: 'disabled' })
  const beforeShapeStarts = trackStarts.length
  await page.getByTestId('confirm-tracking-normal').click()
  await waitApi(async () => (await workspace('controls-shape')).trackingFeedbackEvents.some(event => event.geometryReference))
  await page.waitForFunction(frame => !window.ws.isAiBusy.value && !window.ws.trackingFeedbackBusy.value && window.ws.currentFrame.value === frame + 1, fixture.shapePausedFrame)
  durable = await workspace('controls-shape')
  const geometryEvent = durable.trackingFeedbackEvents.find(event => event.geometryReference)
  check(geometryEvent.geometryReference.objectId===7 && geometryEvent.geometryReference.frameIndex===fixture.shapePausedFrame && geometryEvent.geometryReference.checkpoint && JSON.stringify(geometryEvent.geometryReference.bbox)===JSON.stringify(fixture.shapeBox), 'server records the exact confirmed shape, object, and source frame as a checkpoint')
  check(durable.normalMotionSamples.length === 0, 'shape acceptance does not create motion samples even after normal-motion checkbox was previously unchecked')
  check(sameManualRecords(durable.manualAnnotations, beforeShape.manualAnnotations) && sameManualRecords(durable.manualBaselines, beforeShape.manualBaselines), 'accepting a correct shape leaves frame-18 manual annotations and manual baselines unchanged')
  check(trackStarts.length === beforeShapeStarts + 1 && trackStarts.at(-1).startFrame === fixture.shapePausedFrame, 'shape confirmation saves the reference before resuming from the accepted pause frame')
  await saved(); await reload(); await media('controls-shape')
  durable = await workspace('controls-shape')
  check(durable.trackingFeedbackEvents.filter(event => event.geometryReference).length === 2 && JSON.stringify(durable.trackingFeedbackEvents.find(event => event.geometryReference).geometryReference) === JSON.stringify(geometryEvent.geometryReference), 'both confirmed geometry references survive reload exactly once')
  check(sameManualRecords(durable.manualAnnotations, beforeShape.manualAnnotations) && sameManualRecords(durable.manualBaselines, beforeShape.manualBaselines) && durable.normalMotionSamples.length === 0, 'reloading accepted shape preserves manual provenance and keeps motion calibration empty')
  await page.locator('.tracking-calibration summary').click()
  check((await page.locator('.tracking-calibration').innerText()).includes('第 23 帧尺寸已确认') && await page.locator('[data-calibration-object-id="7"]').getByRole('button',{name:'恢复默认判断',exact:true}).isVisible(), 'shape-only accepted reference remains visible with a reset action even without motion samples')
  await page.locator('[data-calibration-object-id="7"]').getByRole('button',{name:'恢复默认判断',exact:true}).click()
  await waitApi(async () => (await workspace('controls-shape')).trackingFeedbackEvents.some(event => event.decision === 'reset'))
  await saved(); await reload(); await media('controls-shape')
  durable = await workspace('controls-shape')
  check(await page.locator('[data-calibration-object-id="7"]').count() === 0 && await page.locator('[data-calibration-object-id="12"]').count() === 1 && durable.trackingFeedbackEvents.some(event => event.geometryReference) && durable.trackingFeedbackEvents.at(-1).decision === 'reset', 'reset survives reload, removes only the selected checkpoint reference, and preserves the other object and history')

  await media('controls-feedback-failure'); await go(3)
  const beforeFailure = trackStarts.length
  loseFeedbackResponse = true
  await page.getByTestId('confirm-tracking-normal').click()
  await page.locator('.tracking-feedback-error').waitFor()
  check(trackStarts.length === beforeFailure, 'lost feedback response does not start AI or dismiss the unresolved operation')
  check(await page.getByTestId('retry-tracking-feedback').isVisible(), 'failed feedback offers explicit retry of the pending decision')
  check((await workspace('controls-feedback-failure')).normalMotionSamples.length === 1, 'feedback failure injection occurs after its real durable write')
  await page.getByTestId('retry-tracking-feedback').click()
  await page.waitForFunction(() => !window.ws.isAiBusy.value && !window.ws.trackingFeedbackBusy.value && window.ws.currentFrame.value === 4)
  durable = await workspace('controls-feedback-failure')
  check(feedbackAttempts.length === 2 && feedbackAttempts[0].key && feedbackAttempts[0].key === feedbackAttempts[1].key, 'feedback retry keeps its original idempotency key')
  check(JSON.stringify(feedbackAttempts[0].body) === JSON.stringify(feedbackAttempts[1].body), 'feedback retry retains the exact original sample decision')
  check(durable.normalMotionSamples.length === 1 && durable.trackingFeedbackEvents.filter(event => event.decision === 'normal').length === 2, 'lost-response replay does not duplicate samples or whole-frame audit events')

  await media('controls-feedback-corrected'); await go(3); await select(7)
  await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur()); await page.keyboard.press('Alt+ArrowRight'); await saved()
  await page.getByTestId('confirm-tracking-corrected').click()
  await waitApi(async () => (await workspace('controls-feedback-corrected')).trackingFeedbackEvents.length === 1)
  await idle()
  durable = await workspace('controls-feedback-corrected')
  check(durable.trackingFeedbackEvents[0].decision === 'corrected' && durable.normalMotionSamples.length === 0, 'actual canvas correction records corrected feedback without learning the erroneous motion')

  await button('使用说明').click(); await page.getByRole('dialog', { name: '使用说明', exact: true }).waitFor()
  const guide = await page.locator('.user-guide-content').innerText()
  check(guide.includes('删除本帧') && guide.includes('删除全视频') && guide.includes('本对象无异常') && guide.includes('整帧全部无异常') && guide.includes('人工检查点'), 'existing top-right guide contains deletion scopes and separate object/frame confirmation')
  check(guide.includes('撤销') && guide.includes('恢复默认判断'), 'guide covers recovery and calibration reset')
  const downloadPromise = page.waitForEvent('download'); await button('下载完整说明（Markdown）').click()
  const download = await downloadPromise
  check(fs.readFileSync(await download.path(), 'utf8') === fs.readFileSync('frontend/src/help/user-guide.md', 'utf8'), 'downloaded help stays identical to its maintained Markdown source')
  await button('关闭使用说明').click()
  await page.screenshot({ path: 'output/playwright/annotation-controls-final.png', animations: 'disabled' })
  check(errors.length === 0, 'production annotation interactions have no uncaught browser errors')
  console.log('SUCCESS', checks.length, 'checks; real persistence, simulated GPU continuation')
} finally {
  fs.writeFileSync('output/playwright/annotation-controls-browser.json', JSON.stringify({ checks, errors, consoleLog, feedbackAttempts: feedbackAttempts.map(item => ({ key: item.key, body: item.body })), deleteAttempts: deleteAttempts.map(item => ({ key: item.key, body: item.body })) }, null, 2))
  await page.screenshot({ path: 'output/playwright/annotation-controls-last.png', animations: 'disabled' }).catch(() => {})
  await browser.close()
}
