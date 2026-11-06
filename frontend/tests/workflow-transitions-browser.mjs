import {legacyTrackingProbe} from './legacy-tracking-probe.mjs'
// Real APIs and videos. Inject a lost reset response after commit and bypass
// CPU-disabled rewind only to verify new manual/seed inputs; no GPU inference.
import assert from 'node:assert/strict'
import fs from 'node:fs'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
// The task list is a drawer that stays closed while a task is open.
const openTask=async(target,selector)=>{const card=target.locator(selector),toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await card.isVisible()){try{await card.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await card.click()}
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const fixture=JSON.parse(fs.readFileSync('work/workflow-transitions-browser-fixture.json'))
const origin=process.env.CONFIRMATION_ORIGIN||'http://127.0.0.1:5427'
const browser=await chromium.launch({channel:process.env.CHROME_CHANNEL || 'chrome',headless:true})
const page=await browser.newPage({reducedMotion:'reduce',viewport:{width:1366,height:768}})
page.setDefaultTimeout(15000)
const errors=[],checks=[]
page.on('pageerror',e=>errors.push(String(e)))
page.on('dialog',d=>d.accept())
const check=(value,message)=>{assert(value,message);checks.push(message);console.log('PASS',checks.length,message)}
const button=name=>page.getByRole('button',{name,exact:true})
const api=async(path,method='GET',body)=>{
  const response=await page.request.fetch(origin+'/api'+path,{method,headers:{Authorization:'Bearer '+fixture.token,'X-Review-Contract':'2','Idempotency-Key':crypto.randomUUID()},data:body})
  assert(response.ok(),`${path}: ${response.status()} ${await response.text()}`)
  return response.json()
}
const ws=mid=>api('/track/workspace/'+mid)
const attach=()=>page.evaluate(async()=>{const resource=performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts'));window.ws=(await import(resource.name)).useWorkspace()})
const idle=()=>page.waitForFunction(()=>window.ws&&!window.ws.workspaceRestoring.value&&!window.ws.exactFrameLoading.value&&!window.ws.mediaImportBusy.value)
async function asset(mode){const item=fixture.items[mode];await openAsset(page,page.locator('.asset-card').filter({hasText:item.name}));await page.waitForFunction(mid=>window.ws.selectedMedia.value?.serverMediaId===mid&&!window.ws.workspaceRestoring.value&&!window.ws.exactFrameLoading.value,item.mid)}
async function importVideo(mode){await page.locator('input[type=file][accept="video/*"]').setInputFiles(fixture.items[mode].video);await page.locator('[data-reimport-dialog]').waitFor()}
const inView=locator=>locator.evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth&&r.width>0})
const theme=async dark=>{const toggle=button(dark?'切换深色主题':'切换浅色主题');if(await toggle.count())await toggle.click()}
fs.mkdirSync('output/playwright',{recursive:true})
try{
  await page.addInitScript(data=>{localStorage.setItem('rare-sperm-token',data.token);localStorage.setItem('rare-sperm-auth',JSON.stringify({id:'1',name:'confirm-A'}));localStorage.setItem('workbench-library-collapsed','false')},fixture)
  await page.route('**/api/track/media',async route=>{const response=await route.fetch(),data=await response.json();data.items=data.items.filter(i=>Object.values(fixture.items).some(x=>x.mid===i.mediaId));await route.fulfill({response,json:data})})
  await page.goto(origin+'/annotate');await attach();await idle();await asset('free')
  check(await button('加载标注').count()===0,'annotation-folder entry removed')
  const original=(await ws(fixture.items.free.mid)).manualAnnotations
  const mediaCount=await page.locator('.asset-card').count()
  await importVideo('free')
  check(await button('覆盖并重新标注').isVisible(),'duplicate content opens explicit overwrite confirmation')
  for(const size of[{width:1366,height:768},{width:1180,height:760}]){
    await page.setViewportSize(size)
    for(const dark of[false,true]){
      await button('取消导入').click();await idle();await theme(dark);await importVideo('free')
      check(await inView(button('取消导入'))&&await inView(button('覆盖并重新标注')),`duplicate actions visible at ${size.width}, dark=${dark}`)
      await page.screenshot({path:`output/playwright/transitions-duplicate-${size.width}-${dark?'dark':'light'}.png`})
    }
  }
  await button('取消导入').click();await idle()
  check(await page.locator('.asset-card').count()===mediaCount,'cancel does not add a phantom media card')
  assert.deepEqual((await ws(fixture.items.free.mid)).manualAnnotations,original);check(true,'cancel preserves old annotation geometry')
  let lose=true
  await page.route('**/api/annotation-review/media/*/reset-annotations',async route=>{if(lose){lose=false;await route.fetch();await route.abort('failed')}else await route.continue()})
  await importVideo('free');await button('覆盖并重新标注').click()
  await button('重试覆盖').waitFor()
  check(await button('取消导入').isDisabled(),'ambiguous reset keeps intent and blocks cancelling a committed result')
  const revision=(await ws(fixture.items.free.mid)).revision
  await page.reload();await attach();await page.locator('[data-reimport-dialog]').waitFor()
  await button('重试覆盖').click();await page.locator('[data-reimport-dialog]').waitFor({state:'hidden'});await idle()
  check((await ws(fixture.items.free.mid)).revision===revision,'reload retries identical reset receipt without another reset')
  check(await page.evaluate(()=>window.ws.currentObjects.value.length===0&&window.ws.currentFrame.value===0&&!window.ws.canUndo.value),'overwrite clears objects, undo and frame position')
  await page.reload();await attach();await idle();await asset('free')
  check(await page.evaluate(()=>window.ws.currentObjects.value.length===0&&!window.ws.canUndo.value),'overwrite remains empty after refresh')
  await page.evaluate(({mid,original})=>{
    localStorage.setItem('annotation-generation:'+mid,'previous-generation')
    sessionStorage.setItem('annotation-write:1:'+mid,JSON.stringify({key:'stale-before-reset',kind:'workspace',body:{expectedRevision:0,manualAnnotations:original}}))
  },{mid:fixture.items.free.mid,original})
  await page.reload();await attach();await idle();await asset('free')
  check(await page.evaluate(mid=>window.ws.saveState.value!=='error'&&window.ws.currentObjects.value.length===0&&sessionStorage.getItem('annotation-write:1:'+mid)===null,fixture.items.free.mid),'another-window reset discards pending old draft before replay')
  await page.evaluate(()=>window.ws.addObject({x:15,y:25},{x:12.5,y:22,width:5,height:6}))
  await page.waitForFunction(()=>window.ws.saveState.value==='saved')
  const newState=await ws(fixture.items.free.mid)
  check(newState.manualAnnotations.length===1&&newState.manualAnnotations[0].objectId===1,'fresh annotation after overwrite saves normally starting with object 1')
  const requests={}
  await page.route('**/api/track/rewind',async route=>{requests.rewind=route.request().postDataJSON();await route.fulfill({json:{ok:true,removedRows:0,deletedFutureSeedFiles:0}})})
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/track')requests.run=request.postDataJSON()})
  const manual=page.waitForResponse(r=>r.url().includes('/api/annotation/annotations/manual')&&r.request().method()==='POST')
  const seed=page.waitForResponse(r=>r.url().endsWith('/api/track/annotations')&&r.request().method()==='POST')
  const run=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/track'&&r.request().method()==='POST')
  await legacyTrackingProbe(page).click()
  const [manualResponse,seedResponse,runResponse]=await Promise.all([manual,seed,run])
  check(manualResponse.status()===201&&seedResponse.status()===201&&manualResponse.request().postDataJSON().generationId===newState.generationId&&seedResponse.request().postDataJSON().generationId===newState.generationId,'current-generation manual records and seed succeed on real backend')
  const expectedStart = process.env.CRITICAL_AI_MODE === 'simulated' ? 202 : 503
  check(requests.rewind.generationId===newState.generationId&&requests.run.generationId===newState.generationId&&runResponse.status()===expectedStart,'rewind and tracking carry current generation to the configured model boundary')
  await page.waitForFunction(()=>!window.ws.isAiBusy.value&&window.ws.saveState.value==='saved')
  await page.unroute('**/api/track/rewind')
  // Retired A/B/C/F tasks stay as history only; they no longer lock re-annotation.
  for (const mode of ['pending','started']) {
    await asset(mode);await importVideo(mode)
    check(await button('覆盖并重新标注').count()===1,`${mode} legacy review history no longer blocks overwrite`)
    await button('取消导入').click();await idle()
  }
  check(await page.locator('.submission-pill').count()===0&&await page.getByRole('button',{name:'对比确认',exact:true}).count()===0,'no submission or confirmation controls remain')
  check(errors.length===0,'no browser runtime errors')
  fs.writeFileSync('output/playwright/workflow-transitions-results.json',JSON.stringify({checks,errors},null,2))
  console.log(`Completed ${checks.length} workflow transition checks.`)
}finally{await browser.close()}

