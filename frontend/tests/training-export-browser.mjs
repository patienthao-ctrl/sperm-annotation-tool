import { reviewLocationChecks } from './review-location-checks.mjs'
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
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
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
async function openExport() {
  await poll(() => exportButton().isEnabled(), 'export enabled'); await exportButton().click()
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
async function reviewAddHeadChecks(state) {
  const item=state.items.find(i=>i.category==='missing' && i.source==='detector')
  assert(item,'the training fixture must have a detector path for current-frame head creation')
  const row=()=>page.locator(`[data-review-item="${item.id}"]`)
  const group=page.locator('[data-review-group="missing"]')
  if(await group.getAttribute('open')===null)await group.locator('summary').click()
  await row().click();await ready()
  const box=item.observations.find(o=>o.frameIndex===item.frame).candidates[0]
  const before=await json('/track/workspace/'+VIDEO.mid)
  let fail=false,requests=0
  const pattern=`**/api/track/head-box/${VIDEO.mid}`
  const model=async route=>{
    requests++
    const input=route.request().postDataJSON()
    assert.equal(input.frameIndex,item.frame)
    assert.equal(input.x,(box[0]+box[2])/2);assert.equal(input.y,(box[1]+box[3])/2)
    await route.fulfill({status:fail?503:200,contentType:'application/json',body:JSON.stringify(fail?{detail:'test model unavailable'}:{frameIndex:item.frame,width:800,height:450,bbox:box,confidence:.99})})
  }
  await page.route(pattern,model)
  try {
    await page.getByTestId('review-add-head').click()
    await poll(async()=> (await reviewState(VIDEO.mid)).review.items.find(i=>i.id===item.id)?.check?.result==='fixed','head creation is checked only after saving')
    const saved=await json('/track/workspace/'+VIDEO.mid)
    const added=saved.manualAnnotations.filter(o=>!before.manualAnnotations.some(old=>old.id===o.id))
    check(added.length===1 && added[0].frameIndex===item.frame && added[0].source==='manual','is-sperm action creates exactly one manual box on the candidate frame')
    assert.deepEqual(added[0].bbox,{x:100*box[0]/800,y:100*box[1]/450,width:100*(box[2]-box[0])/800,height:100*(box[3]-box[1])/450})
    await row().click();await ready()
    const checked=(await reviewState(VIDEO.mid)).review.items.find(i=>i.id===item.id).check
    await page.getByTestId('review-add-head').click()
    await poll(async()=> (await page.getByTestId('review-action-error').innerText()).includes('这里已经有框'),'existing head is reported')
    assert.deepEqual((await reviewState(VIDEO.mid)).review.items.find(i=>i.id===item.id).check,checked)
    check((await json('/track/workspace/'+VIDEO.mid)).manualAnnotations.length===saved.manualAnnotations.length,'existing-box result neither duplicates a box nor records another review decision')
    fail=true
    await page.getByTestId('review-add-head').click()
    await poll(async()=> (await page.getByTestId('review-action-error').innerText()).includes('请手动画框后点'),'model error remains actionable')
    assert.deepEqual((await reviewState(VIDEO.mid)).review.items.find(i=>i.id===item.id).check,checked)
    check((await json('/track/workspace/'+VIDEO.mid)).manualAnnotations.length===saved.manualAnnotations.length,'model failure does not change boxes or the saved check')
    await page.screenshot({path:'output/playwright/review-add-head-error.png'})
    // Restore fixture content through the normal undo and save path.
    await page.locator('.shell-rail').click({position:{x:22,y:300}})
    await page.keyboard.press('Control+z')
    await poll(async()=> (await json('/track/workspace/'+VIDEO.mid)).manualAnnotations.length===before.manualAnnotations.length,'normal undo removes the created head')
    await json(`/annotation-review/media/${VIDEO.mid}/items/${encodeURIComponent(item.id)}`,'PUT',{reviewId:state.reviewId,result:'not_target'})
    check(requests===3,'success, existing-box and model-error paths all used the real head-click request contract')
  } finally {await page.unroute(pattern,model)}
}
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
  const kinds = new Set(started.review.items.map(i => i.kind))
  check(started.status === 'open' && kinds.has('coverage') && kinds.has('sample'), 'starting review builds coverage and random-sample items')
  await poll(async () => await page.evaluate(() => window.ws.currentFrame.value) === started.review.items[0].frame, 'start jumps to first item')
  check(true, 'start review moves to the first checklist frame')
  await page.screenshot({ path: 'output/playwright/annotation-review-open.png' })

  // Upgrade an old object-by-object checklist through the real API and reload.
  execFileSync(process.env.TEST_PYTHON || 'work/review-venv/bin/python', ['-m','tests.endpoint_tolerance_browser_fixture',VIDEO.mid], {env:{...process.env,PYTHONPATH:'backend'}})
  await page.reload(); await attach(); await ready()
  const tolerant=await reviewState(VIDEO.mid)
  const hard=tolerant.review.items.find(i=>i.category==='position')
  check(hard?.frame===1 && !tolerant.review.items.some(i=>i.id==='legacy-endpoint-small'), 'upgraded checklist removes identity signals and manual-frame object doubts, retains AI position risk')
  check(!tolerant.review.items.some(i=>i.kind==='doubt' && i.startFrame===0), 'actual workspace manual override is exempt on its own frame')
  check(tolerant.review.items.filter(i=>i.kind==='doubt').every(i=>i.objectIds.length<=3 && i.message.length<=12), 'each actionable item has a short label and at most three objects')
  const categories=tolerant.review.items.filter(i=>i.kind==='doubt').map(i=>i.category)
  check(categories.join(',')==='missing,missing,missing,missing,missing,missing,missing,association,position', 'review orders reverse and detector missing locations before association and position risks')
  await page.locator(`[data-review-item="${hard.id}"]`).click(); await ready()
  check(await page.getByTestId('review-current').getAttribute('data-item')===hard.id && await page.evaluate(()=>window.ws.currentFrame.value)===1, 'AI position risk locates the correct frame')
  const geometry=await page.evaluate(()=>window.ws.currentObjects.value.map(o=>({id:o.id,bbox:o.bbox})))
  check(await page.locator('.annotation-box').count()===geometry.length && await page.locator('.review-highlight').count()===0, 'review draws exactly one actual box per annotation, no second review rectangle')
  check(await page.locator('.review-relevant').count()===1 && await page.locator('.review-muted').count()===1 && await page.locator('.review-candidate-circle').count()===0, 'position risk emphasizes existing box and fades others without candidate rectangles')
  check(!(await page.getByTestId('review-panel').innerText()).includes('独立追踪位置不一致'), 'raw long diagnostic prose is absent from the panel')
  await page.screenshot({path:'output/playwright/review-position-emphasis.png'})
  await reviewLocationChecks(page,{state:tolerant.review,ready,check,poll,reviewState,mediaId:VIDEO.mid})
  await reviewAddHeadChecks(tolerant.review)
  await page.locator(`[data-review-item="${hard.id}"]`).click(); await ready()
  const view=await page.evaluate(()=>({zoom:window.ws.zoom.value,selected:window.ws.selectedObjectId.value}))
  await button('专注').click()
  check(await page.evaluate(()=>window.ws.selectedObjectId.value)===view.selected, 'entering focus keeps the selected object')
  check(await page.getByTestId('review-relocate').isVisible(), 'focus keeps the review location action reachable')
  await page.getByTestId('review-relocate').click(); await ready()
  await page.screenshot({path:'output/playwright/review-emphasis-focus.png'})
  await page.getByRole('button',{name:/退出专注/}).click()
  check(await page.evaluate(()=>window.ws.zoom.value)===view.zoom, 'leaving focus restores the original zoom')
  for (const [width,height,theme] of [[1180,760,'dark'],[760,760,'light'],[390,844,'dark']]) {
    await page.setViewportSize({width,height})
    if (await page.locator('html').getAttribute('data-theme')!==theme) await button(theme==='dark'?'切换深色主题':'切换浅色主题').click()
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1), `lightweight review has no overflow at ${width} ${theme}`)
    await page.screenshot({path:`output/playwright/review-emphasis-${width}-${theme}.png`,fullPage:true})
  }
  assert.deepEqual(await page.evaluate(()=>window.ws.currentObjects.value.map(o=>({id:o.id,bbox:o.bbox}))),geometry)
  check(true, 'locating review items, themes and focus do not move or add annotations')
  await page.setViewportSize({width:1440,height:900})
  await page.reload(); await attach(); await ready()
  check((await reviewState(VIDEO.mid)).review.total===tolerant.review.total, 'reloading the upgraded review does not duplicate items or restore old noise')
  await page.waitForFunction(id=>window.ws?.selectedMedia.value?.serverMediaId===id,VIDEO.mid);await ready()
  const persistedZoom=await page.evaluate(()=>window.ws.zoom.value)
  const smallLocation=tolerant.review.items.find(i=>i.category==='missing' && i.frame===39)
  await page.locator(`[data-review-item="${smallLocation.id}"]`).click(); await ready()
  const afterLocateZoom=await page.evaluate(()=>window.ws.zoom.value)
  check(afterLocateZoom===persistedZoom, `manual zoom choice for the current review round survives page reload (${persistedZoom} -> ${afterLocateZoom})`)
  const reverse=tolerant.review.items.find(i=>i.source==='reverse')
  assert(reverse)
  await page.locator(`[data-review-item="${reverse.id}"]`).click(); await ready()
  check((await page.getByTestId('review-location-text').innerText()).includes('右下'), 'reverse-only evidence is labeled with its original-image region')
  await page.getByTestId('review-zoom-preview').waitFor()
  await page.evaluate(async()=>{
    const vueUrl=performance.getEntriesByType('resource').find(r=>r.name.includes('/node_modules/.vite/deps/vue.js?')).name
    const {createApp}=await import(vueUrl),{default:Preview}=await import('/src/components/ReviewZoomPreview.vue')
    const host=document.createElement('div');host.id='preview-loading-probe';host.style.cssText='position:fixed;left:-9999px';document.body.append(host)
    const image=new Image();window.previewProbe={image,app:createApp(Preview,{source:image,box:[5,5,15,15],mediaWidth:20,mediaHeight:20})}
    window.previewProbe.app.mount(host)
  })
  for(const color of ['red','blue']){
    await page.evaluate(color=>{window.previewProbe.image.src='data:image/svg+xml,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="${color}"/></svg>`)},color)
    await page.waitForFunction(color=>{const host=document.querySelector('#preview-loading-probe'),canvas=host.querySelector('canvas'),p=canvas.getContext('2d').getImageData(80,80,1,1).data;return !host.querySelector('span')&&p[3]>0&&(color==='red'?p[0]>200:p[2]>200)},color)
  }
  check(true,'preview redraws after asynchronous image load and same-element source changes')
  await page.evaluate(()=>{window.previewProbe.app.unmount();document.querySelector('#preview-loading-probe').remove()})
  await page.waitForFunction(() => { const c=document.querySelector('[data-testid=review-zoom-preview] canvas'); if(!c)return false; const p=c.getContext('2d').getImageData(c.width/2,c.height/2,1,1).data; return p[3]>0 })
  check(true, 'the current review card draws a non-transparent enlarged frame around the candidate')
  const adoptedBefore=(await json('/track/workspace/'+VIDEO.mid)).manualAnnotations.length
  let saveArrived,releaseSave
  const saveStarted=new Promise(r=>{saveArrived=r}),saveGate=new Promise(r=>{releaseSave=r})
  const savePattern=`**/api/track/workspace/${VIDEO.mid}`
  const delayedSave=async route=>{if(route.request().method()!=='PUT')return route.continue();saveArrived();await saveGate;await route.continue()}
  await page.route(savePattern,delayedSave)
  await page.getByTestId('review-adopt-track').click();await saveStarted
  check(await page.getByTestId('review-adopt-track').isDisabled(),'pending adoption disables duplicate clicks and keyboard actions')
  await page.keyboard.press('1')
  // Switch while the original media save is in flight; do not wait for its save.
  await page.getByTestId('toggle-workbench-library').click()
  await page.locator('.asset-card').filter({hasText:PORTRAIT.name}).click()
  await page.waitForFunction(id=>window.ws.selectedMedia.value?.serverMediaId===id,PORTRAIT.mid)
  releaseSave();await ready();await page.unroute(savePattern,delayedSave)
  await poll(async()=>(await json('/track/workspace/'+VIDEO.mid)).manualAnnotations.length===adoptedBefore+reverse.observations.length,'original video adoption saved after video switch')
  check(!(await reviewState(VIDEO.mid)).review.items.find(i=>i.id===reverse.id)?.check,'switching video leaves the old pending review item unchecked')
  check((await reviewState(PORTRAIT.mid)).status==='none','late adoption cannot start or check another video review')
  await openVideo(VIDEO.name,VIDEO.mid)
  await page.locator(`[data-review-item="${reverse.id}"]`).click();await ready()
  const checkPattern=`**/api/annotation-review/media/${VIDEO.mid}/items/${reverse.id}`
  const failedCheck=route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"test review check failure after saved adoption"}'})
  await page.route(checkPattern,failedCheck)
  await page.getByTestId('review-adopt-track').click()
  await poll(async()=>await page.getByTestId('review-action-error').isVisible(),'saved adoption reports pending review check')
  check(!(await reviewState(VIDEO.mid)).review.items.find(i=>i.id===reverse.id)?.check,'failed check never becomes false completion')
  await page.screenshot({path:'output/playwright/review-adoption-check-retry.png'})
  await page.unroute(checkPattern,failedCheck)
  await page.getByTestId('review-adopt-track').click()
  await poll(async () => (await json('/track/workspace/'+VIDEO.mid)).manualAnnotations.filter(o=>o.objectId===reverse.adoptObjectId).length===reverse.observations.length, 'replayed adoption saved every frame exactly once')
  await poll(async () => (await reviewState(VIDEO.mid)).review.items.find(i=>i.id===reverse.id)?.check?.result==='fixed', 'saved adoption can retry only its failed review check')
  const afterAdopt=await json('/track/workspace/'+VIDEO.mid)
  check(afterAdopt.manualAnnotations.length===adoptedBefore+reverse.observations.length
    && afterAdopt.manualAnnotations.some(o=>o.origin==='review_adopted'),
    'adopting a reverse track adds the stable object id and provenance to every observed frame')
  const coverage=tolerant.review.items.find(i=>i.kind==='coverage')
  await page.locator(`[data-review-item="${coverage.id}"]`).click(); await ready()

  // Correct a box directly on the shared canvas, then record the correction.
  const before = (await json('/track/workspace/'+VIDEO.mid)).revision
  await moveFirstBox(14, 6, false)  // On the first checklist frame (a coverage frame with two heads).
  await poll(async () => (await json('/track/workspace/'+VIDEO.mid)).revision > before, 'correction saved')
  await page.getByTestId('review-current').getByRole('button', { name: '已修正' }).click()
  // Wait for this item's own record: an earlier adoption already left a 'fixed' check.
  await poll(async () => (await reviewState(VIDEO.mid)).review.items.find(i => i.id === coverage.id)?.check?.result === 'fixed', 'fixed check recorded')
  check(true, 'reviewer corrects a box in place and marks the item fixed')
  const followup = (await reviewState(VIDEO.mid)).review.items.find(i => i.kind === 'followup' && i.followupOf === coverage.id)
  check(!!followup && !followup.check && followup.startFrame <= coverage.frame && coverage.frame <= followup.endFrame,
    'a correction adds one unchecked re-check of the frames around it')
  check(await page.getByTestId('review-pass').isDisabled(), 'pass stays disabled while items remain')
  const toFinish=await reviewState(VIDEO.mid)
  for (const item of toFinish.review.items.filter(i=>!i.check)) {
    const result=item.kind==='unknown'?'empty':['missing','static'].includes(item.category)?'not_target':'ok'
    await json(`/annotation-review/media/${VIDEO.mid}/items/${encodeURIComponent(item.id)}`,'PUT',{reviewId:toFinish.review.reviewId,result})
  }
  await page.reload(); await attach(); await ready()
  await poll(() => page.getByTestId('review-pass').isEnabled(), 'pass enabled')
  await page.getByTestId('review-pass').click()
  await page.getByTestId('review-changes').waitFor()
  const passed = await reviewState(VIDEO.mid)
  check(passed.status === 'passed' && passed.review.changes.moved >= 1, 'review pass records the in-place correction as a moved box')
  check((await page.getByTestId('header-review-status').innerText()) === '审查通过', 'header reflects the passed review')
  for (const [width, height] of [[1366, 768], [1180, 760], [760, 760]]) {
    await page.setViewportSize({ width, height })
    for (const theme of ['light', 'dark']) {
      if (await page.locator('html').getAttribute('data-theme') !== theme) await button(theme === 'dark' ? '切换深色主题' : '切换浅色主题').click()
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `review page has no horizontal overflow ${width} ${theme}`)
      await page.screenshot({ path: `output/playwright/annotation-review-${width}-${theme}.png` })
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })

  await openExport()
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
  check(single.frames === 60 && single.objects === 118 + reverse.observations.length, 'single-video ZIP keeps the whole video in one split, includes adopted review boxes, and includes the original video')

  // A second reviewed video exported earlier can be merged into one package.
  await passReview(page, fixture.token, PORTRAIT.mid)
  const preview = await json('/datasets/annotations/'+PORTRAIT.mid+'/preview')
  await json('/datasets/annotations/'+PORTRAIT.mid+'/snapshot', 'POST', { expectedSourceRevision: preview.sourceRevision, confirmComplete: true, explicitEmptyFrameRanges: [] })
  await button('关闭').click(); await openExport()
  const other = page.locator('.training-export-videos label').filter({ hasText: PORTRAIT.name })
  await other.getByRole('checkbox').check()
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
  check(await exportButton().isDisabled(), 'an edit after passing locks export until re-review')
  check((await api(`/datasets/exports/${firstJob}/download`)).status() === 409, 'archives from the old snapshot stop downloading')
  await page.getByTestId('go-review').click(); await page.waitForURL('**/review'); await attach(); await ready()
  await page.getByTestId('review-start').click(); await page.getByTestId('review-progress').waitFor()
  const again = await reviewState(VIDEO.mid)
  check(again.review.round === 2 && again.review.items.some(i => i.kind === 'changed') && !again.review.items.some(i => i.kind === 'coverage'), 're-review lists the changed frames instead of the whole checklist')
  check(!again.review.items.some(i=>i.source==='reverse' && i.startFrame===20), 'a reverse location already adopted does not reappear when a new checklist is generated')

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
