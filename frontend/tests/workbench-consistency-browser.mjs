// Shared workbench acceptance for the two stages: annotation and review use the same
// canvas and layout; review mode swaps tracking controls for the review checklist.
// Uses real fixture video frames. See docs/TESTING.md.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { useLegacyBrowserAPIs } from './legacy-browser-profile.mjs'
import { noticeChecks } from './notice-checks.mjs'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const confirmation = JSON.parse(fs.readFileSync('work/confirmation-browser-fixture.json'))
const origin = process.env.CONFIRMATION_ORIGIN || 'http://127.0.0.1:5373'
const browser = await chromium.launch({ channel: process.env.CHROME_CHANNEL || 'chrome', headless: true, ignoreDefaultArgs: ['--hide-scrollbars'] })
const page = await browser.newPage({ viewport: { width: 1366, height: 768 } })
if (process.env.LEGACY_BROWSER === '1') await useLegacyBrowserAPIs(page)
const logs = [], errors = [], libraryHits = []
let checks = 0
page.on('console', message => logs.push({ type: message.type(), text: message.text() }))
page.on('pageerror', error => errors.push(String(error)))
const check = (value, message) => { assert(value, message); console.log('PASS', ++checks, message) }
const button = name => page.getByRole('button', { name, exact: true })
const inView = locator => locator.evaluate(element => {
  const rect = element.getBoundingClientRect()
  // The 48px header now carries page context; occlusion is checked by elementFromPoint.
  return rect.top >= 0 && rect.bottom <= innerHeight + 1 && rect.width > 0 && element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2))
})
const ready = () => page.waitForFunction(() => window.ws && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && !window.ws.editingBlocked.value)
const poll = async (fn, message) => { for (let i=0;i<150;i++) { if (await fn()) return; await page.waitForTimeout(100) } throw new Error('Timed out: '+message) }
const api = async (path, method = 'GET', data) => {
  const response = await page.request.fetch(origin + '/api' + path, { method, data, headers: { Authorization: 'Bearer ' + confirmation.token } })
  assert(response.ok(), await response.text())
  return response.json()
}
const attach = () => page.evaluate(async () => {
  const url = performance.getEntriesByType('resource').find(resource => resource.name.includes('/src/stores/workspace.ts')).name
  window.ws = (await import(url)).useWorkspace()
})
const settled = () => page.waitForFunction(() => window.ws && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && window.ws.currentObjects.value.length === 2)
async function openStage(path) {
  await page.goto(origin + path)
  await attach()
  if (!await page.evaluate(() => window.ws.selectedMedia.value?.serverMediaId === 'ux-video')) {
    await expandLibrary(path === '/review' ? 'review' : 'annotation')
    await openAsset(page, page.locator('.asset-card').filter({ hasText: 'ux-video.avi' }))
  }
  await settled()
}
async function expandLibrary(stage, expectCollapsed = false) {
  const toggle = page.getByTestId('toggle-workbench-library')
  await toggle.waitFor()
  // Entering a stage with no task open shows its list so a task can be picked;
  // the list closes again once a task opens (checked by the open helpers).
  if (expectCollapsed) check(await toggle.getAttribute('aria-expanded') === 'true' && await page.locator('.shell-drawer').isVisible(), `${stage}: entering a stage without an open task shows its video list`)
  if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click()
}
async function selectTheme(dark) {
  const next = button(dark ? '切换深色主题' : '切换浅色主题')
  if (await next.count()) await next.click()
}
async function layout(stage, progress, core) {
  for (const size of [{ width: 1366, height: 768 }, { width: 1180, height: 760 }]) {
    await page.setViewportSize(size)
    for (const dark of [false, true]) {
      await selectTheme(dark)
      const rail = await page.locator('.shell-rail').boundingBox()
      const canvas = await page.locator('[data-workbench-region=canvas]').boundingBox()
      const inspector = await page.locator('[data-workbench-region=inspector]').boundingBox()
      check(rail && canvas && inspector && rail.x + rail.width <= canvas.x + 2 && canvas.x + canvas.width <= inspector.x + 2,
        `${stage} ${size.width} ${dark ? 'dark' : 'light'}: list rail, image and operations keep the same left-to-right structure`)
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${stage}: no horizontal page overflow`)
      check(await inView(core), `${stage}: core operation is visible without scrolling`)
      if (progress) {
        const summary = page.getByTestId('video-progress')
        const box = await summary.boundingBox()
        check(await inView(summary) && box.y + box.height <= canvas.y + 2 && (await summary.getAttribute('title') !== null || (await summary.innerText()).length > 0), `${stage}: ${progress} stays in the header above the workspace`)
      }
      await page.screenshot({ path: `output/playwright/workbench-${stage}-${size.width}-${dark ? 'dark' : 'light'}.png`, animations: 'disabled' })
    }
  }
  await selectTheme(false)
  await page.setViewportSize({ width: 1366, height: 768 })
}
async function imageUncovered(stage) {
  // Toolbar above, zoom in the bottom bar, minimap off unless requested: nothing over the picture.
  const scroll = await page.locator('.canvas-scroll').boundingBox(), tools = await page.locator('.shell-tools').boundingBox(), zoom = await page.locator('.shell-zoom').boundingBox()
  check(tools.y + tools.height <= scroll.y + 1 && zoom.y >= scroll.y + scroll.height - 1 && await page.getByTestId('annotation-minimap').count() === 0,
    `${stage}: toolbar, zoom and minimap do not cover the image ${JSON.stringify({ scroll, tools, zoom })}`)
  await page.getByRole('button', { name: '小地图', exact: true }).click()
  await button('放大画面').click(); await button('放大画面').click()
  check(await page.getByTestId('annotation-minimap').isVisible(), `${stage}: the minimap appears on request when zoomed in`)
  await page.getByRole('button', { name: '小地图', exact: true }).click(); await button('适应画面').click()
}
async function assertGuideIsolation(stage, before, after, keys) {
  await button('使用说明').click()
  await page.getByRole('dialog', { name: '使用说明', exact: true }).waitFor()
  await page.locator('.user-guide-content').focus()
  for (const key of keys) await page.keyboard.press(key)
  check(JSON.stringify(await after()) === JSON.stringify(before), `${stage}: help keyboard input does not change the underlying work`)
  await page.keyboard.press('Escape')
  check(await button('使用说明').evaluate(element => document.activeElement === element), `${stage}: closing help restores focus to its launcher`)
}
const inViewport = locator => locator.evaluate(element => {
  const rect = element.getBoundingClientRect()
  return rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth && rect.width > 0 && rect.height > 0 && element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2))
})
async function narrowLibrary(stage, list, size, target) {
  await page.setViewportSize(size)
  const toggle = page.getByTestId('toggle-workbench-library')
  if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click()
  const listBox = await list.boundingBox()
  check(listBox && listBox.height >= 64, `${stage} ${size.width}: expanded video library retains a usable scrollable list below its filters`)
  await toggle.click()
  check(!await page.locator('.shell-drawer').isVisible() && await page.locator('[data-workbench-region=canvas]').isVisible(),
    `${stage} ${size.width}: collapsing the list returns the whole area to the image`)
  await toggle.click()
  check(await list.isVisible() && await toggle.getAttribute('aria-expanded') === 'true', `${stage} ${size.width}: expanding restores video navigation`)
  const reorderedProgress=false
  await target.scrollIntoViewIfNeeded()
  const hit=await target.evaluate(element => {
    const rect = element.getBoundingClientRect(), list = element.closest('.asset-list,.review-videos,.c-video-list').getBoundingClientRect()
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2
    const target=document.elementFromPoint(x,y),progress=document.querySelector('[data-testid=video-progress]'),pr=progress?.getBoundingClientRect(),box=r=>({x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right})
    return {passed:rect.width > 40 && rect.height >= 32 && x > Math.max(0, list.left) && x < Math.min(innerWidth, list.right) && y > Math.max(0, list.top) && y < Math.min(innerHeight, list.bottom) && element.contains(target),
      scrollY,target:box(rect),list:box(list),progress:pr?box(pr):null,progressPosition:progress?getComputedStyle(progress).position:null,
      hit:target?{tag:target.tagName,text:target.textContent?.slice(0,100),inProgress:!!target.closest('[data-testid=video-progress]')}:null}
  })
  libraryHits.push({stage,width:size.width,reorderedProgress,...hit})
  check(hit.passed, `${stage} ${size.width}: an actual video entry is visible and hit-testable inside the list`)
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${stage} ${size.width}: header and workbench do not cause horizontal page overflow`)
}
async function narrowGuide(stage, readState) {
  for (const dark of [false, true]) {
    await selectTheme(dark)
    const before = JSON.stringify(await readState())
    await button('使用说明').click()
    const dialog = page.getByRole('dialog', { name: '使用说明', exact: true })
    await dialog.waitFor()
    const geometry = await dialog.evaluate(element => {
      const box = element.getBoundingClientRect(), nav = element.querySelector('.user-guide-nav').getBoundingClientRect(), content = element.querySelector('.user-guide-content'), text = content.getBoundingClientRect()
      return { contained: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight, stacked: nav.bottom <= text.top + 2, textWidth: text.width, textHeight: text.height, font: parseFloat(getComputedStyle(content).fontSize), noTextOverflow: content.scrollWidth <= content.clientWidth + 1, focusWithin: element.contains(document.activeElement), focusCount: element.querySelectorAll('button:not(:disabled),input,[tabindex="0"]').length }
    })
    check(geometry.contained && geometry.textWidth >= 280 && geometry.textHeight >= 200 && geometry.font >= 12 && geometry.noTextOverflow, `${stage} 760 ${dark ? 'dark' : 'light'}: help is fully contained with a readable body`)
    check(geometry.focusWithin, `${stage} 760: opening help places keyboard focus inside its modal`)
    for (let count = 0; count < geometry.focusCount + 2; count++) {
      await page.keyboard.press('Tab')
      assert(await dialog.evaluate(element => element.contains(document.activeElement)), `${stage}: Tab escaped the help dialog`)
    }
    await page.locator('.user-guide-content').focus()
    for (const key of ['a', 'b', 'Control+Enter', 'ArrowRight', 'Delete', 'f']) await page.keyboard.press(key)
    check(JSON.stringify(await readState()) === before, `${stage} 760: help keyboard input cannot change frame, object, submission or decisions`)
    await page.screenshot({ path: `output/playwright/workbench-help-${stage}-760-${dark ? 'dark' : 'light'}.png`, animations: 'disabled' })
    await page.keyboard.press('Escape')
    check(!await dialog.isVisible() && await button('使用说明').evaluate(element => element === document.activeElement), `${stage} 760: Escape closes help and restores its launcher focus`)
  }
  await selectTheme(false)
}
async function narrowWorkbenchChecks() {
  const desktop = { width: 1366, height: 768 }
  const sizes = [{ width: 760, height: 900 }]
  await page.setViewportSize(desktop)
  await page.goto(origin + '/annotate')
  await expandLibrary('annotation')
  await openAsset(page,page.locator('.asset-card').filter({ hasText: 'ux-video.avi' }))
  await page.locator('.object-select').first().waitFor()
  await page.waitForFunction(() => !document.querySelector('.canvas-status') && document.querySelector('.exact-media')?.complete)
  const annotationFrame = await page.getByRole('spinbutton', { name: '跳转帧号' }).inputValue()
  for (const size of sizes) {
    const video = page.locator('.asset-card').filter({ hasText: 'ux-video.avi' })
    await narrowLibrary('annotation', page.locator('.asset-list'), size, video)
    await video.click()
    await page.locator('.object-select').first().focus(); await page.keyboard.press('Enter')
    check(await page.locator('.object-select').first().getAttribute('aria-pressed') === 'true', `annotation ${size.width}: keyboard selection remains available below the image`)
    check(await page.locator('.object-select').first().evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 12), `annotation ${size.width}: object IDs keep a readable text size`)
    if (size.width === 760) check((await page.locator('.annotation-stage').boundingBox()).width > 350, 'annotation 760: source image is wider than the former 327px canvas column')
    await button('专注').click()
    check((await page.locator('[data-workbench-region=canvas]').boundingBox()).width >= size.width - 24 && await inViewport(page.getByRole('button', { name: /退出专注/ })), `annotation ${size.width}: focus uses the window width and keeps its exit visible`)
    await page.getByRole('button', { name: /退出专注/ }).click()
    check(await page.getByRole('spinbutton', { name: '跳转帧号' }).inputValue() === annotationFrame && await page.locator('.object-select').first().getAttribute('aria-pressed') === 'true', `annotation ${size.width}: leaving focus restores the same frame and object`)
    await narrowGuide('annotation', async () => ({ frame: await page.getByRole('spinbutton', { name: '跳转帧号' }).inputValue(), selected: await page.locator('.object-select[aria-pressed=true]').getAttribute('aria-label'), focused: await page.locator('.shell-annotate').evaluate(element => element.classList.contains('is-focus')) }))
    await page.screenshot({ path: `output/playwright/workbench-annotation-narrow-${size.width}.png`, animations: 'disabled', fullPage: true })
  }
  await page.setViewportSize(desktop); await page.evaluate(() => scrollTo(0, 0))
  check(await inView(button('首尾双向追踪')), 'annotation: desktop core action recovers after narrow layouts and focus')

  // The same narrow-layout guarantees hold for review mode.
  await openStage('/review')
  for (const size of sizes) {
    const video = page.locator('.asset-card').filter({ hasText: 'ux-video.avi' })
    await narrowLibrary('review', page.locator('.asset-list'), size, video)
    await video.click()
    const panel = page.getByTestId('review-panel')
    await panel.scrollIntoViewIfNeeded()
    check(await panel.isVisible() && (await page.getByTestId('review-start').isVisible() || await page.getByTestId('review-progress').isVisible()), `review ${size.width}: the review checklist stays reachable below the image`)
    if (size.width === 760) check((await page.locator('.annotation-stage').boundingBox()).width > 350, 'review 760: the source image keeps a useful width')
    await narrowGuide('review', async () => ({ frame: await page.getByRole('spinbutton', { name: '跳转帧号' }).inputValue(), status: await page.getByTestId('review-status').innerText() }))
    await page.screenshot({ path: `output/playwright/workbench-review-narrow-${size.width}.png`, animations: 'disabled', fullPage: true })
  }
  await page.setViewportSize(desktop); await page.evaluate(() => scrollTo(0, 0))
}
async function collaborationChecks(){
  const contexts=[],peers=[];let downloads=0
  const mid='ux-portrait'
  const read=async()=>{const response=await page.request.get(origin+'/api/track/workspace/'+mid,{headers:{Authorization:`Bearer ${confirmation.token}`}});assert(response.ok());return response.json()}
  const idle=target=>target.waitForFunction(()=>window.ws && window.ws.selectedMedia.value?.serverMediaId==='ux-portrait' && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && !window.ws.editingBlocked.value)
  const attach=target=>target.evaluate(async()=>{const resource=performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts'));window.ws=(await import(resource.name)).useWorkspace()})
  async function move(target,frame,dx){
    const jump=target.getByRole('spinbutton',{name:'跳转帧号'});await jump.fill(String(frame+1));await jump.press('Enter');await idle(target)
    await target.getByRole('button',{name:'适应画面',exact:true}).click();await target.keyboard.press('v')
    const rect=await target.locator('.annotation-box').first().boundingBox();assert(rect)
    await target.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2);await target.mouse.down();await target.mouse.move(rect.x+rect.width/2+dx,rect.y+rect.height/2+3,{steps:3});await target.mouse.up()
  }
  try{
    for(const [token,id] of [[confirmation.token,'3'],[confirmation.authorToken,'1']]){
      const context=await browser.newContext({viewport:{width:1366,height:900}});contexts.push(context)
      await context.addInitScript(({token,id})=>{localStorage.setItem('rare-sperm-token',token);localStorage.setItem('rare-sperm-auth',JSON.stringify({id,name:'协作测试',role:'annotator'}))},{token,id})
      // Earlier suites may leave other media with the same file name; show only the shared video.
      await context.route('**/api/track/media',async route=>{const response=await route.fetch(),data=await response.json();data.items=data.items.filter(m=>m.mediaId===mid);await route.fulfill({response,json:data})})
      const peer=await context.newPage();peer.on('download',()=>downloads++);peers.push(peer)
      await peer.goto(origin+'/annotate');await attach(peer)
      await openAsset(peer,peer.locator('.asset-card').filter({hasText:'ux-portrait.avi'}));await idle(peer)
    }
    const [a,b]=peers
    const before=await read()
    await a.getByRole('button',{name:'放大画面',exact:true}).click()
    const jump=a.getByRole('spinbutton',{name:'跳转帧号'});await jump.fill('2');await jump.press('Enter');await idle(a)
    await a.waitForTimeout(1300)
    check((await read()).revision===before.revision,'two accounts can browse and zoom without advancing a shared workspace revision')
    // Hold B's ordinary synchronization at its independently read baseline.
    // Once its write receives 409, the real fresh server read must be used.
    let stale=await read(),allowFresh=false
    const pattern='**/api/track/workspace/'+mid
    const staleRead=async route=>{
      if(route.request().method()==='GET'&&!allowFresh)return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(stale)})
      const response=await route.fetch();if(route.request().method()==='PUT'&&response.status()===409)allowFresh=true
      await route.fulfill({response})
    }
    await b.route(pattern,staleRead)
    await move(a,0,9)
    await poll(async()=> (await read()).manualAnnotations.some(o=>o.frameIndex===0),'first account edit saved')
    await move(b,1,12)
    await poll(async()=>{const state=await read();return state.manualAnnotations.some(o=>o.frameIndex===0)&&state.manualAnnotations.some(o=>o.frameIndex===1)},'disjoint edits from two accounts both saved')
    await b.unroute(pattern,staleRead)
    check(await b.getByTestId('workspace-merge').count()===0 && downloads===0,'different-frame edits merge without a conflict banner or JSON download')
    // Re-establish identical baselines before a genuine same-head collision.
    for(const peer of peers){await peer.reload();await attach(peer);await idle(peer)}
    stale=await read();allowFresh=false;await b.route(pattern,staleRead)
    const original=stale.manualAnnotations.find(o=>o.frameIndex===0)
    await move(a,0,10)
    await poll(async()=>JSON.stringify((await read()).manualAnnotations.find(o=>o.frameIndex===0).bbox)!==JSON.stringify(original.bbox),'remote same-head edit saved')
    await move(b,0,-10)
    await b.getByTestId('workspace-merge').waitFor()
    check(downloads===0,'same-head conflict retains both versions without forcing a download')
    await b.screenshot({path:'output/playwright/collaboration-same-head.png',fullPage:true})
    for(const [width,theme] of [[760,'light'],[760,'dark']]){
      await b.setViewportSize({width,height:844});await b.evaluate(theme=>document.documentElement.dataset.theme=theme,theme)
      await b.screenshot({path:`output/playwright/collaboration-${width}-${theme}.png`,fullPage:true})
      console.log('COLLAB_LAYOUT',await b.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+1||r.left< -1)}).slice(0,14).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width,left:e.getBoundingClientRect().left}))})))
      check(await b.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`collaboration choice fits ${width} ${theme}`)
      await b.screenshot({path:`output/playwright/collaboration-${width}-${theme}.png`,fullPage:true})
    }
    await b.setViewportSize({width:1366,height:900})
    // A refresh must restore the local draft and present the unresolved choice.
    await b.unroute(pattern,staleRead);await b.reload();await attach(b)
    await b.getByTestId('workspace-merge').waitFor()
    check(downloads===0,'refresh preserves the unresolved draft without downloading JSON')
    await b.getByTestId('workspace-merge').getByRole('button',{name:/^用.+的/}).first().click()
    await b.getByRole('button',{name:'应用选择并继续',exact:true}).click()
    await b.getByTestId('workspace-merge').waitFor({state:'hidden'});await idle(b)
    await b.reload();await attach(b);await idle(b)
    check(await b.getByTestId('workspace-merge').count()===0,'accepted remote choice stays resolved after refresh')
    check(downloads===0,'the complete collaborative recovery does not generate backup downloads')
    await b.evaluate(async()=>{
      const url=performance.getEntriesByType('resource').find(r=>r.name.includes('/src/annotation/workspaceWrites.ts')).name
      const {WorkspaceWrites}=await import(url),writer=new WorkspaceWrites()
      const manualAnnotations=Array.from({length:22000},(_,i)=>({id:'large-'+i,objectId:i+1,frameIndex:0,name:'x'.repeat(200),bbox:{x:1,y:1,width:1,height:1}}))
      await writer.saveDraft('large-cache-probe',{manualAnnotations:[]},{manualAnnotations})
    })
    await b.reload();await attach(b);await idle(b)
    const count=await b.evaluate(async()=>{
      const url=performance.getEntriesByType('resource').find(r=>r.name.includes('/src/annotation/workspaceWrites.ts')).name
      const {WorkspaceWrites}=await import(url),writer=new WorkspaceWrites(),draft=await writer.getDraft('large-cache-probe')
      await writer.clearDraft('large-cache-probe');return draft.local.manualAnnotations.length
    })
    check(count===22000,'large drafts exceeding Web Storage quota survive refresh in IndexedDB (cache component test)')

    // Undo after a sync must not erase a colleague's box: B edits frame 4, A then adds a box there.
    for(const peer of peers){await peer.reload();await attach(peer);await idle(peer)}
    await move(b,3,8)
    await poll(async()=>(await read()).manualAnnotations.some(o=>o.frameIndex===3),'B frame-4 edit saved')
    await a.getByRole('spinbutton',{name:'跳转帧号'}).fill('4');await a.getByRole('spinbutton',{name:'跳转帧号'}).press('Enter');await idle(a)
    await a.evaluate(()=>window.ws.addObject({x:80,y:80},{x:78,y:78,width:3,height:3}))
    let added
    await poll(async()=>{added=(await read()).manualAnnotations.find(o=>o.frameIndex===3&&o.bbox.x===78);return !!added},'A box on frame 4 saved')
    await b.waitForFunction(id=>window.ws.currentObjects.value.some(o=>o.objectId===id),added.objectId,{timeout:20000})
    check(true,'a colleague box appears through the background sync without reloading')
    await idle(b);await b.locator('.shell-rail').click({position:{x:22,y:300}});await b.keyboard.press('Control+z');await b.waitForTimeout(1500)
    await b.waitForFunction(()=>window.ws.saveState.value!=='saving')
    check((await read()).manualAnnotations.some(o=>o.objectId===added.objectId&&o.frameIndex===3),'undo after a sync never deletes the colleague box')

    // Another account tracking this video: shown by name and editing waits, then resumes.
    const stampPattern='**/api/track/workspace/'+mid+'/stamp'
    const busyStamp=async route=>{const response=await route.fetch(),data=await response.json();await route.fulfill({response,json:{...data,tracking:{userId:99,userName:'张三',status:'running'}}})}
    await b.route(stampPattern,busyStamp)
    await b.getByTestId('canvas-status').filter({hasText:'张三正在追踪本视频'}).waitFor({timeout:15000})
    check(await b.evaluate(()=>window.ws.editingBlocked.value),'editing waits while another account tracks this video (stamp simulated)')
    await b.unroute(stampPattern,busyStamp)
    await b.waitForFunction(()=>!window.ws.currentRemoteTracking.value,{timeout:15000})
    check(true,'the notice clears once the other job ends')
  }finally{for(const context of contexts)await context.close()}
}

try {
  fs.mkdirSync('output/playwright', { recursive: true })
  await page.addInitScript(({ token }) => {
    localStorage.setItem('rare-sperm-token', token)
    localStorage.setItem('rare-sperm-auth', JSON.stringify({ id: '3', name: 'confirm-C', role: 'annotator' }))
  }, confirmation)
  // The fixture publishes tracking results only; a saved workspace makes them reviewable.
  for (const mid of ['ux-video', 'ux-portrait']) await api('/track/workspace/' + mid, 'PUT', { manualAnnotations: [], manualBaselines: [] })
  await page.route('**/api/track/media', async route => {
    const response = await route.fetch(), data = await response.json()
    data.items = data.items.filter(media => media.mediaId.startsWith('ux-'))
    await route.fulfill({ response, json: data })
  })

  await page.goto(origin + '/annotate')
  await expandLibrary('annotation')
  await openAsset(page,page.locator('.asset-card').filter({ hasText: 'ux-video.avi' }))
  await page.evaluate(async () => {
    const url = performance.getEntriesByType('resource').find(resource => resource.name.includes('/src/stores/workspace.ts')).name
    window.ws = (await import(url)).useWorkspace()
  })
  await page.waitForFunction(() => window.ws && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && window.ws.currentObjects.value.length === 2)
  await layout('annotation', null, button('首尾双向追踪'))
  await imageUncovered('annotation')
  await page.locator('.object-select').first().click()
  await page.locator('.object-select').nth(1).focus(); await page.keyboard.press(' ')
  check(await page.locator('.object-select').nth(1).getAttribute('aria-pressed') === 'true', 'Space activates an object chip like a native button')
  await page.setViewportSize({ width: 1180, height: 760 })
  if (await page.getByTestId('toggle-workbench-library').getAttribute('aria-expanded') === 'true') await page.getByTestId('toggle-workbench-library').click()
  await button('专注').click()
  check((await page.locator('[data-workbench-region=canvas]').boundingBox()).width > 1100 && !await page.locator('[data-workbench-region=library]').isVisible() && !await page.locator('[data-workbench-region=inspector]').isVisible(), 'compact focus uses the whole width even when the library was collapsed')
  await page.getByRole('button', { name: /退出专注/ }).click()
  check(await page.getByTestId('toggle-workbench-library').getAttribute('aria-expanded') === 'false', 'leaving focus retains the collapsed library preference')
  await page.getByTestId('toggle-workbench-library').click()
  await page.setViewportSize({ width: 1366, height: 768 })
  const annotationBefore = await page.evaluate(() => ({ frame: window.ws.currentFrame.value, count: window.ws.currentObjects.value.length, focused: !!document.querySelector('.shell-annotate.is-focus') }))
  await assertGuideIsolation('annotation', annotationBefore, () => page.evaluate(() => ({ frame: window.ws.currentFrame.value, count: window.ws.currentObjects.value.length, focused: !!document.querySelector('.shell-annotate.is-focus') })), ['f', 'b', 'Delete'])

  const beforeCollapse = await page.locator('[data-workbench-region=canvas]').boundingBox()
  await page.getByTestId('toggle-workbench-library').click()
  check((await page.locator('[data-workbench-region=canvas]').boundingBox()).width > beforeCollapse.width, 'collapsing the library gives its space to the image workspace')

  // Review is the same workbench: same regions, canvas, objects and editing tools.
  await openStage('/review')
  await layout('review', null, page.getByTestId('review-start'))
  await imageUncovered('review')
  check(await page.locator('.annotation-stage').count() === 1 && await page.locator('.object-select').count() === 2 && await button('画框').isVisible() && await button('点选头部').isVisible(),
    'review uses the annotation canvas, object chips and editing tools')
  check(!await page.getByRole('button', { name: '首尾双向追踪' }).isVisible() && await page.locator('.annot-ai').isHidden(), 'review mode hides AI tracking controls')
  await expandLibrary('review')
  check(await page.locator('.asset-actions').count() === 0 && await page.locator('.asset-card').filter({ hasText: 'ux-video.avi' }).isVisible(), 'review lists the same videos without import actions')
  await page.getByTestId('toggle-workbench-library').click()
  const reviewBefore = await page.evaluate(() => ({ frame: window.ws.currentFrame.value, count: window.ws.currentObjects.value.length }))
  await assertGuideIsolation('review', reviewBefore, () => page.evaluate(() => ({ frame: window.ws.currentFrame.value, count: window.ws.currentObjects.value.length })), ['b', 'Delete', 'ArrowRight'])
  await page.getByTestId('review-start').click(); await page.getByTestId('review-progress').waitFor()
  const startedReview=await api('/annotation-review/media/ux-video')
  const progress=async()=>Number((await page.getByTestId('review-progress').innerText()).split('/')[0])
  check(startedReview.review.total===startedReview.review.frames.reduce((n,[a,b])=>n+b-a+1,0) && startedReview.review.frames[0][0]===0, 'the first round lists every frame')
  await page.waitForFunction(() => window.ws.currentFrame.value === 0 && !window.ws.exactFrameLoading.value)
  check(await page.getByTestId('review-current').getAttribute('data-frame')==='0', 'starting a review goes to the first unchecked frame')
  check(await page.locator('.frame-strip i.review-todo').count() > 0, 'the frame strip marks frames still to review')

  // A different account checking one frame must be visible, non-blocking, and
  // disappear when the activity is older than ten minutes (server evidence).
  const foreignFrame=startedReview.review.frames.at(-1)[1]
  const foreignCheck=result=>page.request.fetch(origin+'/api/annotation-review/media/ux-video/frames/'+foreignFrame,{
    method:'PUT',data:{reviewId:startedReview.review.reviewId,result},
    headers:{Authorization:`Bearer ${confirmation.authorToken}`,'Content-Type':'application/json'}})
  let foreign=await foreignCheck('ok'); if(!foreign.ok())foreign=await foreignCheck('empty')
  check(foreign.ok(),'a second reviewer can record a check without taking over the workspace')
  await page.reload(); await page.evaluate(async () => {
    const url=performance.getEntriesByType('resource').find(resource=>resource.name.includes('/src/stores/workspace.ts')).name
    window.ws=(await import(url)).useWorkspace()
  })
  await page.waitForFunction(()=>window.ws && !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && !window.ws.editingBlocked.value)
  await page.getByTestId('review-concurrent').waitFor()
  check((await page.getByTestId('review-concurrent').innerText()).includes(' 也在审查') && !(await page.getByTestId('review-concurrent').innerText()).includes('用户 #'), 'recent activity by another reviewer shows a non-blocking yellow warning')

  // Keyboard confirm and next frame: inputs must isolate both.
  await page.keyboard.press('n'); await ready()
  const frameBefore=await page.evaluate(()=>window.ws.currentFrame.value)
  check(await page.getByTestId('review-frame-state').innerText()==='未检查', 'N moves to an unchecked frame')
  const checkedBefore=await progress()
  await page.evaluate(()=>document.activeElement instanceof HTMLElement && document.activeElement.blur())
  await page.keyboard.press('1')
  await poll(async()=>await progress()>checkedBefore,'keyboard 1 confirms the current frame')
  await page.waitForFunction(f=>window.ws.currentFrame.value!==f,frameBefore); await ready()
  check(await page.getByTestId('review-frame-state').innerText()==='未检查', 'confirming a frame advances to the next unchecked one')
  check(await page.locator('.frame-strip i.review-done').count() > 0, 'the frame strip marks confirmed frames')
  const checkedAfter=await progress()
  const jump=page.getByRole('spinbutton',{name:'跳转帧号'})
  await jump.focus(); await page.keyboard.press('1'); await page.waitForTimeout(200)
  check(await progress()===checkedAfter, 'review shortcuts do not fire while an input is focused')
  await page.evaluate(()=>document.activeElement instanceof HTMLElement && document.activeElement.blur())
  await page.evaluate(f=>window.ws.frameInput.value=f,frameBefore); await page.evaluate(async()=>window.ws.seekToInputFrame()); await ready()
  check(await page.getByTestId('review-frame-state').innerText()==='已确认' && await page.getByTestId('review-checked').isVisible(), 'a confirmed frame shows its result and can be confirmed again after edits')
  // A colleague's edit after a confirmation withdraws it, and the page says which frame.
  const colleague={Authorization:`Bearer ${confirmation.authorToken}`,'Content-Type':'application/json'}
  const shared=await (await page.request.get(origin+'/api/track/workspace/ux-video',{headers:colleague})).json()
  const edited=await page.request.put(origin+'/api/track/workspace/ux-video',{headers:{...colleague,'Idempotency-Key':'colleague-edit-after-confirm'},data:{...shared,expectedRevision:shared.revision,
    manualAnnotations:[...shared.manualAnnotations,{id:'colleague-box',objectId:900,name:'rare sperm 900',source:'manual',frameIndex:frameBefore,bbox:{x:20,y:20,width:8,height:8}}]}})
  check(edited.ok(),'a colleague saves a change to a confirmed frame')
  await page.getByTestId('review-changed').waitFor({timeout:15000})
  check((await page.getByTestId('review-changed').innerText()).includes(`第 ${frameBefore+1} 帧`),'the review panel names the frame changed after its confirmation')
  check(await progress()===checkedAfter-1,'a frame changed after confirmation no longer counts as checked')
  await page.getByTestId('review-changed').click(); await ready()
  check(await page.getByTestId('review-frame-state').innerText()==='未检查','the warning goes to the changed frame, now unchecked')
  await page.evaluate(()=>document.activeElement instanceof HTMLElement && document.activeElement.blur())
  await page.keyboard.press('1')
  await poll(async()=>await progress()===checkedAfter,'the changed frame can be confirmed again')
  check(await page.getByTestId('review-changed').count()===0,'the warning clears once someone has looked at the new content')
  await page.setViewportSize({ width: 1180, height: 760 })
  await button('专注').click()
  check(await page.getByTestId('review-current').isVisible() && (await page.locator('[data-workbench-region=canvas]').boundingBox()).width > 800, 'review focus retains core review actions beside a useful image')
  await button('收起检查面板').click()
  check((await page.locator('[data-workbench-region=canvas]').boundingBox()).width > 1100, 'collapsing the review inspector recovers the full image width in focus')
  await button('展开检查面板').click()
  check(await page.getByTestId('review-current').isVisible(), 'review inspector can be reopened in focus without leaving the workbench')
  await page.getByRole('button', { name: /退出专注/ }).click()
  await page.setViewportSize({ width: 1366, height: 768 })
  await noticeChecks(page, { origin, openStage, selectTheme })
  await narrowWorkbenchChecks()
  await collaborationChecks()
  check(errors.length === 0, 'shared workbench paths have no uncaught browser exceptions')
  console.log('SUCCESS', checks, 'checks')
} catch (error) {
  console.log('WORKBENCH_FAILURE_STATE',await page.evaluate(()=>({frame:window.ws?.currentFrame.value,blocked:window.ws?.editingBlocked.value,restoring:window.ws?.workspaceRestoring.value,loading:window.ws?.exactFrameLoading.value,playing:window.ws?.isPlaying.value,focus:document.activeElement?.outerHTML.slice(0,180),conflict:!!window.ws?.collaborationConflict.value})))
  await page.screenshot({ path: 'output/playwright/workbench-consistency-failure.png', fullPage: true })
  throw error
} finally {
  fs.writeFileSync('output/playwright/workbench-consistency-console.json', JSON.stringify({ checks, logs, errors, libraryHits }, null, 2))
  await browser.close()
}
