import {legacyTrackingProbe} from './legacy-tracking-probe.mjs'
// Real 4K source/workspace APIs. Model responses and lost HTTP replies are injected.
import fs from 'node:fs'
import assert from 'node:assert/strict'
import { publishTrackingFixture, trackingRestartChecks } from './tracking-restart-checks.mjs'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const fixture=JSON.parse(fs.readFileSync('work/tracking-recovery-fixture.json'))
assert(fixture.directory.replaceAll('\\','/').includes('/work/e2e-confirm-ux-'))
const origin=process.env.CONFIRMATION_ORIGIN||'http://127.0.0.1:5387'
const browser=await chromium.launch({channel:process.env.CHROME_CHANNEL || 'chrome',headless:true})
const page=await browser.newPage({reducedMotion:'reduce',viewport:{width:1366,height:768}})
page.setDefaultTimeout(15000)
const errors=[],checks=[],starts=[],saveAttempts=[],receipts=new Map(),taskStates=new Map(),logs=[]
let rewindTimeout=false,pollTimeout=false,loseStart=false,loseSave=false,saveConflict=false,resultFailure=false,modelFailure=false,job=0
const check=(ok,message)=>{assert(ok,message);checks.push(message);console.log('PASS',checks.length,message)}
const button=name=>page.getByRole('button',{name,exact:true})
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms))
page.on('pageerror',e=>errors.push(String(e)))
page.on('console',m=>logs.push({type:m.type(),text:m.text()}))
page.on('dialog',d=>d.accept())
const api=async(path,method='GET',body)=>{
  const r=await page.request.fetch(origin+'/api'+path,{method,headers:{Authorization:'Bearer '+fixture.token,'Idempotency-Key':crypto.randomUUID()},data:body})
  assert(r.ok(),`${path}: ${r.status()} ${await r.text()}`);return r.json()
}
const attach=()=>page.evaluate(async()=>{const resource=performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts'));window.ws=(await import(resource.name)).useWorkspace()})
const idle=()=>page.waitForFunction(()=>window.ws&&!window.ws.workspaceRestoring.value&&!window.ws.exactFrameLoading.value)
const finish=()=>page.waitForFunction(()=>!window.ws.isAiBusy.value&&window.ws.saveState.value==='saved')
const timeout=active=>page.evaluate(active=>{window.shortTimeouts=active},active)
const inView=l=>l.evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})
fs.mkdirSync('output/playwright',{recursive:true})
try{
  await page.addInitScript(data=>{
    localStorage.setItem('rare-sperm-token',data.token);localStorage.setItem('rare-sperm-auth',JSON.stringify({id:'3',name:'Recovery tester'}))
    const original=window.setTimeout;window.setTimeout=function(callback,ms,...args){return original(callback,ms===30000&&window.shortTimeouts?300:ms,...args)}
    Object.defineProperty(AbortSignal,'timeout',{value:undefined,configurable:true});Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true})
  },fixture)
  await page.route('**/api/track/media',async route=>{const r=await route.fetch(),data=await r.json();data.items=data.items.filter(m=>m.mediaId===fixture.mid);await route.fulfill({response:r,json:data})})
  await page.route('**/api/track/rewind',async route=>{if(rewindTimeout){await delay(700);try{await route.fulfill({json:{removedRows:0,deletedFutureSeedFiles:0}})}catch{};return}await route.continue()})
  await page.route('**/api/track',async route=>{
    const key=route.request().headers()['idempotency-key'],body=route.request().postDataJSON();starts.push({key,body});assert.equal(body.edgeDiscovery,true,'edge discovery follows original tracking request and retries')
    if(!receipts.has(key)){
      const last=Math.min(7,body.startFrame+2),taskId='recovery-'+(++job)
      receipts.set(key,{taskId,status:'queued',maxFrames:last-body.startFrame+1});taskStates.set(taskId,{last,start:body.startFrame})
      // Simulated inference publishes complete source-numbered rows so real
      // result reloads and subsequent seeds exercise the full client pipeline.
      if(!modelFailure)publishTrackingFixture(fixture,{operation:'model',body,lastFrame:last})
    }
    if(loseStart){loseStart=false;await route.abort('failed');return}
    await route.fulfill({status:202,json:receipts.get(key)})
  })
  await page.route('**/api/track/status/recovery-*',async route=>{
    if(pollTimeout){await delay(700);try{await route.fulfill({json:{status:'running'}})}catch{};return}
    const status=taskStates.get(new URL(route.request().url()).pathname.split('/').at(-1))
    await route.fulfill({json:modelFailure?{status:'failed',message:'injected model failure'}:{status:'success',stage:'completed',processedFrames:status.last-status.start+1,lastProcessedFrame:status.last,reachedVideoEnd:status.last===7}})
  })
  await page.route('**/api/track/result/'+fixture.mid+'*',async route=>{if(resultFailure){await route.fulfill({status:503,json:{detail:'injected result read failure'}});return}await route.continue()})
  await page.route('**/api/track/workspace/'+fixture.mid,async route=>{
    if(route.request().method()!=='PUT')return route.continue()
    const attempt={key:route.request().headers()['idempotency-key'],body:route.request().postDataJSON()};saveAttempts.push(attempt)
    if(saveConflict){saveConflict=false;const state=await api('/track/workspace/'+fixture.mid);await api('/track/workspace/'+fixture.mid,'PUT',{...state,currentFrame:1,expectedRevision:state.revision});return route.continue()}
    if(loseSave){loseSave=false;const r=await route.fetch();assert(r.ok());await delay(700);try{await route.fulfill({response:r})}catch{};return}
    await route.continue()
  })
  await page.goto(origin+'/annotate');await attach();await idle()
  await openAsset(page,page.locator('.asset-card').filter({hasText:'4k-recovery.avi'}));await idle()
  check(await page.evaluate(()=>window.ws.selectedMedia.value.width===3840&&window.ws.selectedMedia.value.height===2160),'genuine 4K source retains original image dimensions')

  rewindTimeout=true;await timeout(true);await legacyTrackingProbe(page).click();await page.getByTestId('tracking-error').waitFor();await finish();await timeout(false)
  check((await page.getByTestId('tracking-error').innerText()).includes('整理旧追踪分支')&&(await page.getByTestId('tracking-error').innerText()).includes('请求超时'),'rewind timeout names its exact stage and failed source frame')
  await page.waitForTimeout(3500)
  check(await page.getByTestId('tracking-error').isVisible()&&await page.getByTestId('workspace-save-error').count()===0,'tracking error survives toast expiry and successful autosave')
  for(const size of[{width:1366,height:768},{width:1180,height:760}]){
    await page.setViewportSize(size)
    for(const dark of[false,true]){
      const toggle=button(dark?'切换深色主题':'切换浅色主题');if(await toggle.count())await toggle.click()
      check(await inView(page.getByTestId('tracking-error'))&&await inView(button('重试追踪')),`persistent error and matching retry visible ${size.width}, dark=${dark}`)
      await page.screenshot({path:`output/playwright/tracking-recovery-${size.width}-${dark?'dark':'light'}.png`})
    }
  }
  await button('专注').click();check(await inView(page.getByTestId('tracking-error')),'tracking failure remains visible in focus mode');await page.getByRole('button',{name:/^退出专注/}).click()
  rewindTimeout=false;await button('重试追踪').click();await finish()
  await page.getByTestId('tracking-error').waitFor({state:'detached'});check(await page.getByTestId('tracking-error').count()===0,'explicit successful tracking clears persistent error')
  check(starts[0].body.mediaWidth===3840&&starts[0].body.mediaHeight===2160&&starts[0].body.startFrame===0,'model request keeps 4K pixel grid and original seed frame')
  pollTimeout=true;await timeout(true);await legacyTrackingProbe(page).click();await page.getByTestId('tracking-error').waitFor();await timeout(false)
  check(await page.evaluate(()=>window.ws.isAiBusy.value)&&await button('首尾双向追踪').isDisabled(),'poll timeout keeps original job and prevents duplicate model start')
  const startCount=starts.length
  await page.reload();await attach();await idle();await page.getByTestId('tracking-error').waitFor()
  check((await page.getByTestId('tracking-error').innerText()).includes('上次追踪结果尚未确认'),'reload restores pending tracking job without starting inference')
  pollTimeout=false;await button('重新查询追踪任务').click();await finish()
  await page.getByTestId('tracking-error').waitFor({state:'detached',timeout:1000}).catch(()=>{});check(starts.length===startCount&&await page.getByTestId('tracking-error').count()===0,'status retry reconnects same task after refresh without new start')
  loseStart=true;await legacyTrackingProbe(page).click();await page.getByTestId('tracking-error').waitFor()
  const lost=starts.at(-1);pollTimeout=true;await timeout(true);await button('重试原追踪请求').click();await page.getByTestId('tracking-error').waitFor();await page.waitForFunction(()=>!window.ws.trackingRetryBusy.value);await timeout(false)
  check(await button('重新查询追踪任务').isVisible(),'verified task ID switches ambiguous-start recovery to status-only retry')
  pollTimeout=false;await button('重新查询追踪任务').click();await finish()
  assert.deepEqual(starts.at(-1),lost)
  check(true,'lost start response replays identical task key and input')
  resultFailure=true;await legacyTrackingProbe(page).click();await page.getByTestId('tracking-error').waitFor()
  check((await page.getByTestId('tracking-error').innerText()).includes('读取追踪结果'),'finished task with unreadable result remains a visible failure')
  const beforeResultRetry=starts.length;resultFailure=false;await button('重新查询追踪任务').click();await finish()
  check(starts.length===beforeResultRetry,'result retry rereads finished job instead of rerunning model')
  await timeout(true);loseSave=true;await page.evaluate(()=>window.ws.addObject({x:60,y:10},{x:60,y:10,width:5,height:5}));await page.getByTestId('workspace-save-error').waitFor();await timeout(false)
  check((await page.getByTestId('workspace-save-error').innerText()).includes('请求超时')&&await button('重试保存').isEnabled(),'save timeout has its own working retry action')
  const savedAttempt=saveAttempts.at(-1);await button('重试保存').click();await finish()
  check(saveAttempts.some((attempt,i)=>i<saveAttempts.length-1&&attempt.key===savedAttempt.key&&JSON.stringify(attempt.body)===JSON.stringify(savedAttempt.body))&&saveAttempts.filter(a=>a.key===savedAttempt.key).length===2,'save recovery replays original receipt key and exact body')
  await page.getByTestId('workspace-save-error').waitFor({state:'detached',timeout:1000}).catch(()=>{});check(await page.getByTestId('workspace-save-error').count()===0,'save error clears only after verified save success')
  const beforeConflict=await api('/track/workspace/'+fixture.mid),ownFrame=await page.evaluate(()=>window.ws.currentFrame.value)
  let recoveryDownloads=0;const downloaded=()=>recoveryDownloads++;page.on('download',downloaded)
  saveConflict=true;await page.evaluate(()=>window.ws.addObject({x:70,y:10},{x:70,y:10,width:5,height:5}))
  for(let i=0;i<150;i++){if((await api('/track/workspace/'+fixture.mid)).manualAnnotations.length===beforeConflict.manualAnnotations.length+1)break;await delay(100)}
  await finish()
  check((await api('/track/workspace/'+fixture.mid)).manualAnnotations.length===beforeConflict.manualAnnotations.length+1,'view-only remote version changes automatically rebase and retain the new box')
  await page.getByTestId('workspace-save-error').waitFor({state:'detached',timeout:1000}).catch(()=>{});check(await page.getByTestId('workspace-save-error').count()===0&&await page.getByTestId('workspace-merge').count()===0,'non-overlapping changes need neither reload nor manual conflict handling')
  check(recoveryDownloads===0,'automatic version recovery does not download a JSON backup')
  check(await page.evaluate(()=>window.ws.currentFrame.value)===ownFrame,'remote viewing state does not move this client to another frame')
  page.off('download',downloaded)
  modelFailure=true;await legacyTrackingProbe(page).click();await page.getByTestId('tracking-error').waitFor();await finish()
  check((await page.getByTestId('tracking-error').innerText()).includes('模型追踪')&&await button('重试追踪').isVisible(),'terminal model failure stays visible and permits a new explicit retry')
  // All branch mutations remain real; the helper installs its own model-only
  // simulation after the recovery suite's fault routes have been removed.
  await page.unroute('**/api/track'); await page.unroute('**/api/track/status/recovery-*')
  await page.unroute('**/api/track/rewind'); await page.unroute('**/api/track/result/'+fixture.mid+'*')
  await page.unroute('**/api/track/workspace/'+fixture.mid)
  await trackingRestartChecks(page,fixture,origin)
  check(errors.length===0,'no uncaught browser errors across recovery flows')
  fs.writeFileSync('output/playwright/tracking-recovery-results.json',JSON.stringify({checks,errors},null,2))
  console.log(`Completed ${checks.length} tracking recovery checks.`)
}catch(error){
  fs.writeFileSync('output/playwright/tracking-recovery-failure.json',JSON.stringify({checks,errors,logs},null,2))
  await page.screenshot({path:'output/playwright/tracking-recovery-failure.png'}).catch(()=>{})
  throw error
}finally{await browser.close()}
