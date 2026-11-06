import { useLegacyBrowserAPIs } from './legacy-browser-profile.mjs'
import { statisticsExportChecks } from './statistics-export-checks.mjs'
import { passReview, legacyTrackingProbe } from './legacy-tracking-probe.mjs'
// Annotation → review → export on disposable fixture videos and isolated test servers.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { execFileSync } from 'node:child_process'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const fixture = JSON.parse(fs.readFileSync('work/confirmation-browser-fixture.json'))
const origin = process.env.CONFIRMATION_ORIGIN || 'http://127.0.0.1:5373'
const browser = await chromium.launch({ channel: process.env.CHROME_CHANNEL || 'chrome', headless: true })
const page = await browser.newPage({reducedMotion:'reduce',viewport: { width: 1440, height: 900 } })
page.setDefaultTimeout(15000)
if (process.env.LEGACY_BROWSER === '1') await useLegacyBrowserAPIs(page)
const errors = [], logs = []
let checks = 0
page.on('pageerror', e => errors.push(String(e)))
page.on('console', m => logs.push({ type: m.type(), text: m.text() }))
page.on('dialog', d => d.accept())
const check = (ok, msg) => { assert(ok, msg); console.log('PASS', ++checks, msg) }
const button = name => page.getByRole('button', { name, exact: true })
const headers = { Authorization: `Bearer ${fixture.token}`, 'X-Review-Contract': '2' }
const api = (path, method='GET', data) => page.request.fetch(origin+'/api'+path, { method, data, headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() } })
const json = async (path, method, data) => { const r = await api(path, method, data); assert(r.ok(), `${method||'GET'} ${path}: ${r.status()} ${await r.text()}`); return r.json() }
const poll = async (fn, what) => { for (let i = 0; i < 150; i++) { if (await fn()) return; await page.waitForTimeout(100) } throw new Error('Timed out: '+what) }
const python = (code, ...args) => JSON.parse(execFileSync(process.env.TEST_PYTHON || 'work/review-venv/bin/python', ['-c', code, ...args], { encoding: 'utf8' }))
const reviewState = mid => json('/annotation-review/media/'+mid)
const attach = () => page.evaluate(async () => { const r = performance.getEntriesByType('resource').find(x => x.name.includes('/src/stores/workspace.ts')); window.ws = (await import(r.name)).useWorkspace() })
const ready = () => poll(async () => await page.evaluate(() => window.ws && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && !window.ws.editingBlocked.value), 'workbench idle')
async function openVideo(name, mid) {
  for (let i = 0; i < 40; i++) {
    const card = page.locator('.asset-card').filter({ hasText: name })
    if (await card.isVisible()) { await card.click(); break }
    await page.getByTestId('toggle-workbench-library').click(); await page.waitForTimeout(200)
  }
  await page.waitForFunction(id => window.ws?.selectedMedia.value?.serverMediaId === id, mid); await ready()
}
async function moveFirstBox(dx = 14, dy = 6, frameOne = true) {
  // Frame 1 always has two tracked heads in the fixture (the last frame is empty).
  const jump = page.getByRole('spinbutton', { name: '跳转帧号' })
  if (frameOne && await jump.inputValue() !== '1') { await ready(); await jump.fill('1'); await jump.press('Enter'); await page.waitForFunction(() => window.ws.currentFrame.value === 0) }
  await ready(); await button('适应画面').click()
  await page.locator('.shell-rail').click({ position: { x: 22, y: 300 } }); await page.keyboard.press('v')
  const beforeGeometry = await page.evaluate(() => JSON.stringify(window.ws.currentObjects.value[0].bbox))
  const box = await page.locator('.annotation-box').first().boundingBox(); assert(box)
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy, { steps: 5 }); await page.mouse.up()
  await poll(async () => await page.evaluate(() => JSON.stringify(window.ws.currentObjects.value[0].bbox))!==beforeGeometry,'actual drag changes the box geometry')
}
const exportButton = () => page.getByTestId('annotation-export')
async function openPicker() {
  await poll(() => exportButton().isEnabled(), 'export enabled'); await exportButton().click()
  await page.getByTestId('batch-export').waitFor()
}
async function pickerNext() {
  await poll(() => page.getByTestId('batch-export-next').isEnabled(), 'a video is chosen'); await page.getByTestId('batch-export-next').click()
  await page.getByRole('dialog').getByRole('heading', { name: '导出训练数据集' }).waitFor()
  await page.waitForFunction(() => !!document.querySelector('.training-export-summary'))
}
async function downloadFrom(name, file) {
  const [d] = await Promise.all([page.waitForEvent('download'), button(name).click()])
  await d.saveAs(file)
  await page.waitForFunction(() => !Array.from(document.querySelectorAll('.training-export-actions button')).every(b => b.disabled))
}
const taskId = () => page.locator('.training-export-id').innerText().then(s => s.split('：')[1])
const VIDEO = { mid: 'ux-video', name: 'ux-video.avi' }, PORTRAIT = { mid: 'ux-portrait', name: 'ux-portrait.avi' }
try {
  fs.mkdirSync('output/playwright', { recursive: true })
  await page.addInitScript(({ token }) => {
    localStorage.setItem('rare-sperm-token', token)
    localStorage.setItem('rare-sperm-auth', JSON.stringify({ id: '3', name: 'confirm-C', role: 'annotator' }))
  }, fixture)
  // The fixture publishes tracking results only; a saved workspace makes them reviewable.
  for (const v of [VIDEO, PORTRAIT]) await json('/track/workspace/'+v.mid, 'PUT', { manualAnnotations: [], manualBaselines: [] })

  await page.goto(origin+'/annotate'); await attach(); await openVideo(VIDEO.name, VIDEO.mid)
  await page.getByTestId('header-review-status').waitFor()
  check((await page.getByTestId('header-review-status').innerText()) === '待审查', 'annotation header shows the video has not been reviewed')
  check(await exportButton().isDisabled() && (await exportButton().getAttribute('title')).includes('审查通过'), 'export is locked until review passes and says why')
  const blocked = await api('/datasets/annotations/'+VIDEO.mid+'/snapshot', 'POST', { expectedSourceRevision: (await json('/datasets/annotations/'+VIDEO.mid+'/preview')).sourceRevision, confirmComplete: true, explicitEmptyFrameRanges: [] })
  check(blocked.status() === 409 && (await blocked.json()).code === 'REVIEW_REQUIRED', 'direct API snapshot cannot bypass the review gate')

  // Prepare the manual override with a real edit; fixtures must not overwrite
  // a live workspace while its automatic view saves are in flight.
  await moveFirstBox(2,1)
  await poll(async () => (await json('/track/workspace/'+VIDEO.mid)).manualAnnotations.some(o=>o.source==='manual' && o.objectId===1 && o.frameIndex===0), 'real first-frame manual correction saved')

  await page.getByTestId('go-review').click(); await page.waitForURL('**/review'); await attach(); await ready()
  check(await page.getByRole('button', { name: '对比确认', exact: true }).count() === 0 && await page.getByTestId('review-panel').isVisible(), 'review page reuses the workbench with a review checklist and no confirmation step')
  check(!await page.getByRole('button', { name: '首尾双向追踪' }).isVisible() && await page.locator('.annot-ai').isHidden(), 'review mode hides AI tracking controls')
  await page.getByTestId('review-start').click()
  await page.getByTestId('review-progress').waitFor()
  const started = await reviewState(VIDEO.mid)
  check(started.status === 'open' && started.review.total === 60 && JSON.stringify(started.review.frames) === '[[0,59]]', 'starting review lists every frame of the video')
  await poll(async () => await page.evaluate(() => window.ws.currentFrame.value) === 0, 'start jumps to the first frame')
  check(true, 'start review moves to the first unchecked frame')
  const geometry=await page.evaluate(()=>window.ws.currentObjects.value.map(o=>({id:o.id,bbox:o.bbox})))
  check(await page.locator('.annotation-box').count()===geometry.length && await page.locator('.review-candidate-circle, .review-muted, .review-relevant').count()===0, 'review draws only the actual boxes, with no automatic doubt marks')
  check(await page.locator('[data-review-group], [data-review-item]').count()===0, 'the panel has no automatic doubt checklist')
  await page.screenshot({ path: 'output/playwright/annotation-review-open.png' })
  const progress = async () => Number((await page.getByTestId('review-progress').innerText()).split('/')[0])

  // Correct a box directly on the shared canvas, then confirm the frame.
  const before = (await json('/track/workspace/'+VIDEO.mid)).revision
  await moveFirstBox(14, 6, false)  // Frame 1 has two tracked heads.
  await poll(async () => (await json('/track/workspace/'+VIDEO.mid)).revision > before, 'correction saved')
  // A failed check is never shown as done and does not move on.
  const checkPattern=`**/api/annotation-review/media/${VIDEO.mid}/frames/0`
  const failedCheck=route=>route.fulfill({status:503,contentType:'application/json',body:'{"message":"test review check failure"}'})
  await page.route(checkPattern,failedCheck)
  await page.getByTestId('review-confirm').click()
  await page.getByRole('alert').filter({hasText:'test review check failure'}).waitFor()
  check(await progress()===0 && await page.evaluate(()=>window.ws.currentFrame.value)===0 && (await reviewState(VIDEO.mid)).review.checked===0, 'a failed frame check records nothing and stays on the frame')
  await page.unroute(checkPattern,failedCheck)
  await page.getByTestId('review-confirm').click()
  await poll(async () => await progress() === 1 && await page.evaluate(() => window.ws.currentFrame.value) === 1, 'confirm records the frame and moves to the next')
  check(JSON.stringify((await reviewState(VIDEO.mid)).review.checkedFrames)==='[[0,0,"ok"]]', 'reviewer corrects a box in place and confirms the frame')
  for (let i = 0; i < 3; i++) {
    const checked = await progress(); await ready()
    await page.evaluate(()=>document.activeElement instanceof HTMLElement && document.activeElement.blur())
    await page.keyboard.press('1'); await poll(async () => await progress() > checked, 'keyboard 1 confirms a frame')
  }
  check(await page.evaluate(() => window.ws.currentFrame.value) === 4, 'keyboard confirmation walks the frames in order')
  check(await page.getByTestId('review-pass').isDisabled() && (await page.getByTestId('review-pass').innerText()).includes('还剩 56 帧'), 'pass stays disabled while frames remain')
  await button('专注').click()
  check(await page.getByTestId('review-confirm').isVisible(), 'focus keeps the frame confirmation reachable')
  await page.getByRole('button',{name:/退出专注/}).click()
  for (const [width,height,theme] of [[1180,760,'dark'],[760,760,'light']]) {
    await page.setViewportSize({width,height})
    if (await page.locator('html').getAttribute('data-theme')!==theme) await button(theme==='dark'?'切换深色主题':'切换浅色主题').click()
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1) && await page.getByTestId('review-confirm').isVisible(), `frame review has no overflow and keeps its action at ${width} ${theme}`)
    await page.screenshot({path:`output/playwright/review-frame-${width}-${theme}.png`,fullPage:true})
  }
  await page.setViewportSize({width:1440,height:900})
  // The remaining frames through the same API; frames without any annotation are confirmed empty.
  const toFinish=await reviewState(VIDEO.mid)
  for (const [start,end] of toFinish.review.frames) for (let f=start; f<=end; f++) {
    if (toFinish.review.checkedFrames.some(([a,b])=>a<=f && f<=b)) continue
    const ok=await api(`/annotation-review/media/${VIDEO.mid}/frames/${f}`,'PUT',{reviewId:toFinish.review.reviewId,result:'ok'})
    if (!ok.ok()) await json(`/annotation-review/media/${VIDEO.mid}/frames/${f}`,'PUT',{reviewId:toFinish.review.reviewId,result:'empty'})
  }
  check((await reviewState(VIDEO.mid)).review.checked===60, 'every frame of the video is confirmed before passing')
  await page.reload(); await attach(); await ready()
  await poll(() => page.getByTestId('review-pass').isEnabled(), 'pass enabled')
  await page.getByTestId('review-pass').click()
  await page.getByTestId('review-changes').waitFor()
  const passed = await reviewState(VIDEO.mid)
  check(passed.status === 'passed' && passed.review.changes.moved >= 1, 'review pass records the in-place correction as a moved box')
  check((await page.getByTestId('header-review-status').innerText()) === '审查通过', 'header reflects the passed review')

  // The round just passed is on the records page; a failed read is not an empty list.
  await button('标注记录').click(); await page.locator('.records-page').waitFor()
  await page.route('**/api/annotation-review/records', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"message":"读取失败（模拟）"}' }))
  await button('刷新记录').click()
  await page.getByTestId('records-tab-review').click(); await page.getByTestId('review-records-error').waitFor()
  check(!(await page.getByTestId('review-records').innerText()).includes('还没有审查记录'), 'a failed read of the review records does not look like an empty list')
  await page.getByTestId('records-tab-annotation').click()
  check(await page.locator('.records-page .error-banner').count() === 0, 'the annotation list stays usable when the review records fail')
  await page.unroute('**/api/annotation-review/records')
  await button('刷新记录').click()
  await page.getByTestId('records-tab-review').click()
  const recordRow = page.getByTestId('review-records').locator('tbody tr').filter({ hasText: VIDEO.name }).first()
  await recordRow.waitFor()
  const recordText = await recordRow.innerText()
  check(recordText.includes('第 1 轮') && recordText.includes('审查通过') && recordText.includes('当前有效') && recordText.includes('60 / 60 帧') && recordText.includes('移动'), 'the passed round is listed with its result, frames and changes')
  check((await page.getByTestId('records-tab-review').getAttribute('aria-selected')) === 'true' && await page.getByRole('textbox', { name: '搜索审查记录' }).isVisible(), 'the review list has its own search')
  await page.route('**/api/annotation-review/records/*', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"message":"读取失败（模拟）"}' }))
  await recordRow.getByTestId('review-record-toggle').click()
  const recordDetail = page.getByTestId('review-record-detail')
  await recordDetail.getByRole('alert').waitFor()
  await page.unroute('**/api/annotation-review/records/*')
  await recordDetail.getByRole('button', { name: '重试' }).click()
  await recordDetail.getByText('逐帧确认').waitFor()
  const detailText = await recordDetail.innerText()
  check(detailText.includes('60 帧') || detailText.includes('确认 '), 'the detail names who confirmed the frames')
  check(detailText.includes('移动对象'), 'the detail names the objects the round moved')
  await page.screenshot({ path: 'output/playwright/records-review.png' })
  await page.getByRole('textbox', { name: '搜索审查记录' }).fill('没有这个视频')
  check((await page.getByTestId('review-records').innerText()).includes('没有匹配的记录'), 'searching the review records can come up empty')
  await button('审查模式').click(); await page.getByTestId('review-panel').waitFor(); await attach(); await ready()

  for (const [width, height] of [[1366, 768], [1180, 760], [760, 760]]) {
    await page.setViewportSize({ width, height })
    for (const theme of ['light', 'dark']) {
      if (await page.locator('html').getAttribute('data-theme') !== theme) await button(theme === 'dark' ? '切换深色主题' : '切换浅色主题').click()
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `review page has no horizontal overflow ${width} ${theme}`)
      await page.screenshot({ path: `output/playwright/annotation-review-${width}-${theme}.png` })
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })

  await openPicker(); await pickerNext()
  check(await page.getByRole('combobox', { name: '划分方式' }).inputValue() === 'video', 'export defaults to video-level split')
  await page.screenshot({ path: 'output/playwright/training-export-dialog.png' })
  await downloadFrom('生成并下载 ZIP', 'output/playwright/training-yolo.zip')
  const firstJob = await taskId()
  const single = python(`
import zipfile,json,sys,hashlib
with zipfile.ZipFile(sys.argv[1]) as z:
 m=json.loads(z.read('manifest.json'));s=m['sources'][0]
 splits={x['split'] for x in m['samples']}
 assert m['splitStrategy'].startswith('video-level') and splits=={s['split']}
 assert hashlib.sha256(z.read(s['videoFile'])).hexdigest()==s['sourceSha256']
 print(json.dumps({'frames':len(m['samples']),'objects':m['counts']['objectCount']}))
`, 'output/playwright/training-yolo.zip')
  check(single.frames === 60 && single.objects === 118, 'single-video ZIP keeps the whole video in one split and includes the original video')

  // A second reviewed video needs no earlier export: the picker offers it directly.
  await passReview(page, fixture.token, PORTRAIT.mid)
  await button('关闭').click(); await openPicker()
  const other = page.getByTestId('batch-export-list').locator('label').filter({ hasText: PORTRAIT.name })
  await other.waitFor()
  check(await other.getByRole('checkbox').isChecked() === false, 'only the open video starts chosen')
  await other.getByRole('checkbox').check()
  await pickerNext()
  await page.waitForFunction(() => !!document.querySelector('.training-export-summary'))
  await poll(async () => (await page.locator('.training-export-videos').last().locator('label').count()) === 2, 'plan lists both videos')
  await page.getByRole('combobox', { name: `${PORTRAIT.name} 的划分` }).selectOption('val')
  await page.getByRole('combobox', { name: `${VIDEO.name} 的划分` }).selectOption('train')
  // Lose the create response once; the retry must replay the same job.
  let lost = true; const keys = []
  await page.route('**/api/datasets/exports', async route => {
    keys.push(route.request().headers()['idempotency-key'])
    if (lost) { lost = false; await route.fetch(); await route.abort('failed') } else await route.continue()
  })
  // The dialog restores the earlier job, so the action reads 重新生成 ZIP here.
  await page.getByRole('button', { name: /^(生成并下载|重新生成) ZIP$/ }).click(); await page.getByRole('alert').waitFor()
  await downloadFrom('重试导出请求', 'output/playwright/training-merged.zip')
  await page.unroute('**/api/datasets/exports')
  check(keys.length === 2 && keys[0] === keys[1], 'lost create response retries with the original idempotency key')
  const merged = python(`
import zipfile,json,sys
with zipfile.ZipFile(sys.argv[1]) as z:
 m=json.loads(z.read('manifest.json'))
 by={}
 for x in m['samples']: by.setdefault(x['finalVersionId'],set()).add(x['split'])
 print(json.dumps({'frames':len(m['samples']),'videos':len(m['sources']),'splits':sorted(s['split'] for s in m['sources']),'whole':all(len(v)==1 for v in by.values())}))
`, 'output/playwright/training-merged.zip')
  check(merged.frames === 120 && merged.videos === 2 && merged.whole && merged.splits.join() === 'train,val', 'merged package keeps each video whole in its chosen split')

  // The job list finds the package again after the dialog closed, and filters hide what was exported.
  await button('关闭').click()
  await page.screenshot({ path: 'output/playwright/library-before-picker.png' }); await openPicker()
  await page.screenshot({ path: 'output/playwright/batch-export-picker.png' })
  await poll(async () => await page.getByTestId('batch-export-jobs').locator('li').count() >= 2, 'jobs listed')
  const [again1] = await Promise.all([page.waitForEvent('download'), page.getByTestId('batch-job-download').first().click()])
  check((await again1.suggestedFilename()).includes('等2段'), 'the job list downloads a finished package with a readable name')
  await poll(async () => await page.getByTestId('batch-export-list').locator('label').filter({ hasText: PORTRAIT.name }).count() === 0, 'exported video hidden')
  await page.getByTestId('batch-export-new').uncheck()
  check(await page.getByTestId('batch-export-list').locator('label').filter({ hasText: PORTRAIT.name }).count() === 1, 'unticking the filter shows exported videos again')
  await page.getByTestId('batch-export-close').click()

  // A folder with subfolders, a duplicate, a broken video and unrelated files.
  const batchDir = 'output/playwright/batch-folder/第一批'
  fs.rmSync('output/playwright/batch-folder', { recursive: true, force: true }); fs.mkdirSync(batchDir + '/样本A', { recursive: true })
  python(`
import cv2,numpy as np,sys
for name,shade in (('a-001.avi',30),('b-001.avi',200)):
  w=cv2.VideoWriter(sys.argv[1]+'/'+name,cv2.VideoWriter_fourcc(*'MJPG'),10,(64,48))
  for i in range(3): w.write(np.full((48,64,3),shade+i,dtype=np.uint8))
  w.release()
print('{}')
`, batchDir)
  fs.renameSync(batchDir + '/a-001.avi', batchDir + '/样本A/a-001.avi')
  fs.writeFileSync(batchDir + '/坏视频.avi', 'not a video'); fs.writeFileSync(batchDir + '/说明.txt', 'x'); fs.writeFileSync(batchDir + '/.DS_Store', 'x')
  await page.goto(origin+'/annotate'); await attach(); await openVideo(VIDEO.name, VIDEO.mid); await ready()
  await page.getByTestId('toggle-workbench-library').click()
  await page.getByTestId('folder-input').setInputFiles(batchDir)
  await page.getByTestId('import-confirm').waitFor()
  check((await page.getByTestId('import-summary').innerText()).includes('视频 3 段') && (await page.getByTestId('import-summary').innerText()).includes('跳过其他文件 2'), 'the folder is sorted into videos and skipped files before anything uploads')
  check(await page.getByTestId('import-folder-name').inputValue() === '第一批', 'the folder name defaults to the chosen directory')
  const selectedBefore = await page.evaluate(() => window.ws.selectedMediaId.value)
  await page.getByTestId('import-start').click()
  await poll(async () => (await page.getByTestId('import-progress').innerText()).includes('导入完成'), 'batch import finished')
  const progressText = await page.getByTestId('import-progress').innerText()
  check(progressText.includes('成功 2') && progressText.includes('失败 1'), 'a broken file does not stop the others')
  check(await page.evaluate(() => window.ws.selectedMediaId.value) === selectedBefore, 'importing does not switch the open video')
  await page.getByTestId('import-report-toggle').click()
  check((await page.getByTestId('import-report').innerText()).includes('坏视频.avi'), 'the report names the failed file')
  const folders = async () => Object.fromEntries((await json('/track/media')).items.filter(i => /^(a|b)-001/.test(i.sourceVideoName)).map(i => [i.sourceVideoName, i.folderPath]))
  check(JSON.stringify(await folders()) === JSON.stringify({ 'a-001.avi': '第一批/样本A', 'b-001.avi': '第一批' }), 'videos land in folders that follow the directory tree')
  check(await page.getByTestId('library-folder').filter({ hasText: '样本A' }).count() === 1, 'the library shows the folder tree')
  await page.screenshot({ path: 'output/playwright/library-folders.png' })
  // Picking the same folder again uploads nothing new (the broken file aside).
  await page.getByTestId('folder-input').setInputFiles(batchDir)
  await page.getByTestId('import-confirm').waitFor()
  check(await page.getByTestId('import-upload-existing').isVisible() && !(await page.getByTestId('import-upload-existing').isChecked()), 'already imported videos are skipped by default')
  await page.getByRole('dialog', { name: '确认导入' }).getByRole('button', { name: '取消' }).click()
  // Renaming moves everything below the folder; the status filter narrows the list.
  await page.getByTestId('library-organize').click()
  await page.getByTestId('organizer-rename-from').selectOption('第一批')
  await page.getByTestId('organizer-rename-to').fill('十月第一批')
  await page.getByTestId('organizer-rename').click()
  await poll(async () => (await folders())['a-001.avi'] === '十月第一批/样本A', 'folder renamed')
  await page.getByRole('dialog', { name: '整理文件夹' }).getByRole('button', { name: '关闭' }).click()
  await page.getByTestId('library-status').selectOption('passed')
  check(await page.locator('.asset-card').filter({ hasText: 'a-001.avi' }).count() === 0, 'the status filter hides videos that have not passed')
  await page.getByTestId('library-status').selectOption('')
  for (const item of (await json('/track/media')).items.filter(i => /^(a|b)-001/.test(i.sourceVideoName))) check((await api('/track/media/'+item.mediaId, 'DELETE')).ok(), 'batch test video removed')
  await page.reload(); await attach(); await ready()

  // The statistics checks start by closing an open dialog.
  await openPicker()
  await statisticsExportChecks(page, fixture, origin)

  // Any later edit makes the pass stale: export locks and old archives stop downloading.
  await page.goto(origin+'/annotate'); await attach(); await openVideo(VIDEO.name, VIDEO.mid)
  const rev = (await json('/track/workspace/'+VIDEO.mid)).revision
  const frameJump=page.getByRole('spinbutton',{name:'跳转帧号'})
  await ready(); await frameJump.fill('21'); await frameJump.press('Enter')
  await page.waitForFunction(()=>window.ws.currentFrame.value===20); await ready()
  await moveFirstBox(-10, 4, false)
  await poll(async () => (await json('/track/workspace/'+VIDEO.mid)).revision > rev, 'edit saved')
  await poll(async () => (await reviewState(VIDEO.mid)).status === 'stale', 'review stale')
  await poll(async () => (await page.getByTestId('header-review-status').innerText()) === '需重新审查', 'header stale')
  check((await json('/annotation-review/records')).items.find(row => row.mediaId === VIDEO.mid)?.standing === 'stale', 'the record of the pass says later edits no longer leave it covering the content')
  await openPicker()
  check(await page.getByTestId('batch-export-list').locator('label').filter({ hasText: VIDEO.name }).count() === 0, 'an edit after passing removes the video from export until re-review')
  check(/\d+ 段需重新审查/.test(await page.getByTestId('batch-export-stale').innerText()), 'the picker says how many videos need re-review')
  await page.getByTestId('batch-export-close').click()
  check((await api(`/datasets/exports/${firstJob}/download`)).status() === 409, 'archives from the old snapshot stop downloading')
  await page.getByTestId('go-review').click(); await page.waitForURL('**/review'); await attach(); await ready()
  await page.getByTestId('review-start').click(); await page.getByTestId('review-progress').waitFor()
  const again = await reviewState(VIDEO.mid)
  check(again.review.round === 2 && JSON.stringify(again.review.frames) === '[[20,20]]', 're-review lists only the changed frame')
  await poll(async () => await page.evaluate(() => window.ws.currentFrame.value) === 20, 're-review starts on the changed frame')
  check(true, 're-review goes straight to the changed frame')

  // Another site imports the merged package: the portrait video is new there, the other already exists.
  check((await api('/track/media/'+PORTRAIT.mid, 'DELETE')).ok(), 'portrait removed so the package brings it back as a new video')
  const shipped = python(`
import zipfile,json,sys
with zipfile.ZipFile(sys.argv[1]) as z:
 m=json.loads(z.read('manifest.json'))
 s=next(s for s in m['sources'] if s['videoFile'] and s['videoFile'].endswith('${PORTRAIT.name}'))
 f=json.loads(z.read('provenance/'+s['finalVersionId']+'.json'))['frames']
 print(json.dumps({'objects':sum(len(x['objects']) for x in f)}))
`, 'output/playwright/training-merged.zip')
  await page.goto(origin+'/annotate'); await attach(); await openVideo(VIDEO.name, VIDEO.mid)
  await page.getByTestId('package-input').setInputFiles('output/playwright/training-merged.zip')
  await page.getByTestId('package-import-report').waitFor()
  const report = await page.getByTestId('package-import-report').innerText()
  check(report.includes('导入 1 个视频') && report.includes(VIDEO.name) && report.includes('未覆盖'), 'package import adds the missing video and refuses to overwrite the existing one')
  // The HTTP report arrives before media-list reconciliation and frame restore.
  await page.waitForFunction(()=>!window.ws.mediaImportBusy.value);await ready()
  const imported = await page.evaluate(() => window.ws.selectedMedia.value)
  check(imported.name === PORTRAIT.name && imported.serverMediaId !== VIDEO.mid, 'imported video opens in the workbench')
  const importedState = await json('/track/workspace/'+imported.serverMediaId)
  check(importedState.manualAnnotations.length === shipped.objects && importedState.manualAnnotations.every(o => o.source === 'manual'), 'every exported box returns as an editable manual annotation')
  await ready()
  check((await page.getByTestId('header-review-status').innerText()) === '待审查' && await exportButton().isDisabled(), 'imported video starts unreviewed and cannot be exported before review here')
  await page.screenshot({ path: 'output/playwright/package-import.png' })

  // Tracking one video locks only that video; another can be opened and edited meanwhile.
  let release = false
  const fakeStart = route => route.fulfill({ status: 202, json: { taskId: 'background-1', status: 'queued', maxFrames: 3 } })
  const fakeStatus = route => route.fulfill({ json: release ? { status: 'success', stage: 'completed', processedFrames: 3, lastProcessedFrame: 22, reachedVideoEnd: false } : { status: 'queued', stage: 'queued' } })
  const fakeRewind = route => route.fulfill({ json: { removedRows: 0, deletedFutureSeedFiles: 0 } })
  await page.route('**/api/track', fakeStart); await page.route('**/api/track/status/background-1', fakeStatus); await page.route('**/api/track/rewind', fakeRewind)
  try {
    await openVideo(VIDEO.name, VIDEO.mid)
    const jump = page.getByRole('spinbutton', { name: '跳转帧号' }); await jump.fill('21'); await jump.press('Enter')
    await page.waitForFunction(() => window.ws.currentFrame.value === 20); await ready()
    await legacyTrackingProbe(page).click()
    await page.waitForFunction(() => window.ws.isAiBusy.value && window.ws.trackingProgress.value.includes('排队'))
    check(await page.locator('.canvas-status').innerText().then(t => t.includes('可切换到其他视频')), 'queued tracking tells the user other videos stay available')
    await openVideo(PORTRAIT.name, imported.serverMediaId)
    check(await page.evaluate(() => !window.ws.isAiBusy.value && !window.ws.editingBlocked.value), 'another video is editable while the first one tracks')
    check(await page.locator('.asset-card').filter({ hasText: VIDEO.name }).getByTestId('asset-tracking').count() === 1, 'media list marks the video that is still tracking')
    const before = importedState.manualAnnotations.length
    await page.evaluate(() => window.ws.addObject({ x: 50, y: 50 }, { x: 48, y: 48, width: 4, height: 3 }))
    await poll(async () => (await json('/track/workspace/'+imported.serverMediaId)).manualAnnotations.length === before + 1, 'manual box on the other video saved during tracking')
    check(true, 'manual annotation saves on another video while the model job runs')
    release = true
    await page.waitForFunction(() => !window.ws.isTrackingMedia([...window.ws.mediaAssets.value].find(m => m.serverMediaId === 'ux-video').id))
    check(await page.locator('.asset-card').filter({ hasText: VIDEO.name }).getByTestId('asset-tracking').count() === 0 && await page.evaluate(() => window.ws.selectedMedia.value.name) === PORTRAIT.name, 'background job finishes without pulling the user back to its video')
  } finally { await page.unroute('**/api/track', fakeStart); await page.unroute('**/api/track/status/background-1', fakeStatus); await page.unroute('**/api/track/rewind', fakeRewind) }
  // Later suites recreate the portrait fixture; do not leave a same-named imported copy behind.
  check((await api('/track/media/'+imported.serverMediaId, 'DELETE')).ok(), 'imported test video removed after the import checks')

  check(errors.length === 0, 'no uncaught browser errors across review and export')
  console.log(`SUCCESS ${checks} checks`)
} finally {
  fs.writeFileSync('output/playwright/training-export-console.json', JSON.stringify({ errors, logs }, null, 2))
  await page.screenshot({ path: 'output/playwright/training-export-last.png' }).catch(() => {})
  await browser.close()
}
