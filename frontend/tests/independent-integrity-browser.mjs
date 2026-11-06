import {legacyTrackingProbe} from './legacy-tracking-probe.mjs'
// Independent audit: real login/upload/UI; only model inference is simulated.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { trackingPolicyChecks } from './tracking-policy-checks.mjs'
import { trackingFocusChecks } from './tracking-focus-checks.mjs'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
// Object names are no longer editable in the UI; tests still steer the simulated model through the store's default name.
const setObjectName=(target,name)=>target.evaluate(async n=>{const url=performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts')).name;(await import(url)).useWorkspace().objectNameInput.value=n},name)
const root = path.resolve(process.env.INDEPENDENT_AUDIT_ROOT || '')
assert(root.startsWith(path.resolve('work') + path.sep) && /e2e-confirm-ux-(independent|regression)-/.test(path.basename(root)))
const origin = process.env.INDEPENDENT_AUDIT_ORIGIN
assert(['localhost','127.0.0.1'].includes(new URL(origin).hostname))
const out = path.resolve('output/playwright', path.basename(root))
fs.mkdirSync(out, { recursive:true })
const browser = await chromium.launch({ channel:process.env.CHROME_CHANNEL || 'chrome', headless:true })
const results = []
let page, context, token, observations, username, password
const button = name => page.getByRole('button', { name, exact:true })
async function poll(fn, name) {
  for (let i=0;i<150;i++) { if(await fn()) return; await page.waitForTimeout(100) }
  throw new Error('Timed out: '+name)
}
const locked = async () => ((await page.getByTestId('annotation-hit').getAttribute('class'))||'').includes('locked')
async function ready() { await poll(async()=>await page.getByTestId('annotation-hit').isVisible() && !await locked(), 'editable annotation canvas') }
async function api(url, method='GET', body) {
  const response = await page.request.fetch(origin+'/api'+url, { method, data:body,
    headers:{ Authorization:'Bearer '+token, 'X-Review-Contract':'2', 'Idempotency-Key':crypto.randomUUID() } })
  assert(response.ok(), method+' '+url+' returned '+response.status()+': '+await response.text())
  return response.json()
}
async function login() {
  username='independent-'+crypto.randomUUID();password=crypto.randomUUID()
  const r=await page.request.post(origin+'/api/auth/register',{data:{username,password}})
  assert.equal(r.status(),201); token=(await r.json()).token
  await page.goto(origin+'/login')
  await page.getByPlaceholder('请输入账号').fill(username)
  await page.getByPlaceholder('请输入密码').fill(password)
  await button('登录').click(); await ready()
  token=await page.evaluate(()=>localStorage.getItem('rare-sperm-token'))
}
async function upload(label, fourK=false) {
  const buffer=Buffer.concat([fs.readFileSync(path.join(root,fourK?'4k.avi':'small.avi')),Buffer.from(crypto.randomUUID())])
  const pending=page.waitForResponse(r=>r.url().endsWith('/api/track/upload') && r.request().method()==='POST')
  await page.locator('input[type=file][accept="video/*"]').setInputFiles({name:label+'.avi',mimeType:'video/x-msvideo',buffer})
  const r=await pending; assert.equal(r.status(),201,await r.text())
  const data=await r.json(); await ready(); return data.mediaId
}
async function draw(mid, count=1, dense=false) {
  await ready(); await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur()); await page.keyboard.press('b')
  const box=await page.getByTestId('annotation-hit').boundingBox(); assert(box)
  const [x,y,w,h]=dense?[.92,.94,.04,.04]:count===1?[.3,.3,.08,.08]:count===2?[.75,.7,.08,.08]:[.5,.7,.08,.08]
  await page.mouse.move(box.x+box.width*x,box.y+box.height*y);await page.mouse.down()
  await page.mouse.move(box.x+box.width*(x+w),box.y+box.height*(y+h),{steps:5});await page.mouse.up()
  await poll(async()=>(await api('/track/workspace/'+mid)).manualAnnotations?.length===count,'durable manual count '+count)
}
async function caseRun(name, run) {
  context=await browser.newContext({reducedMotion:'reduce',viewport:{width:1366,height:768},acceptDownloads:true})
  page=await context.newPage();page.setDefaultTimeout(15000);page.on('dialog',d=>d.accept())
  observations={};const errors=[];page.on('pageerror',e=>errors.push(String(e)))
  const started=Date.now()
  try { await login();await run();assert.equal(errors.length,0,errors.join('\n'));results.push({name,status:'passed',seconds:(Date.now()-started)/1000,observations,errors});console.log('PASS',name) }
  catch(e) { results.push({name,status:'failed',seconds:(Date.now()-started)/1000,error:String(e),observations,errors});console.log('FAIL',name,String(e));await page.screenshot({path:path.join(out,name+'-failure.png')}).catch(()=>{}) }
  finally { fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({model:'simulated; no GPU validation',results},null,2));await context.close() }
}
try {
  await caseRun('authenticated-native-video',async()=>{
    const content=fs.readFileSync(path.join(root,'playable.mp4'))
    const response=await page.request.post(origin+'/api/track/upload',{headers:{Authorization:'Bearer '+token},
      multipart:{file:{name:'native-playback-'+crypto.randomUUID()+'.mp4',mimeType:'video/mp4',buffer:content}}})
    assert.equal(response.status(),201,await response.text())
    const asset=await response.json(),url=origin+'/api/track/video/'+encodeURIComponent(asset.mediaId)
    const anonymous=await browser.newContext()
    try{
      for(const headers of [{},{Range:'bytes=0-99'},{Authorization:'Bearer invalid'}])
        assert.equal((await anonymous.request.get(url,{headers})).status(),401)
    }finally{await anonymous.close()}
    const range=await page.request.get(url,{headers:{Range:'bytes=0-99'}})
    assert.equal(range.status(),206);assert.deepEqual(await range.body(),content.subarray(0,100))
    await page.reload();await ready()
    await openAsset(page,page.locator('.asset-card').filter({hasText:asset.videoName}));await ready()
    await poll(async()=>await page.locator('video').first().evaluate(v=>v.readyState>=2 && v.videoWidth===640),'authenticated native video decoded')
    await button('播放').click()
    await poll(async()=>await page.locator('video').first().evaluate(v=>v.currentTime>0.3 && !v.paused),'native playback advances')
    await button('暂停').click()
    const videoTime=await page.locator('video').first().evaluate(v=>v.currentTime)
    await button('退出').click();await page.getByPlaceholder('请输入账号').waitFor()
    assert.equal((await page.request.get(url)).status(),401,'logout removes playback credentials')
    observations={anonymousStatus:401,rangeStatus:206,nativeTime:videoTime,loggedOutStatus:401}
  })

  for(const fault of ['500','timeout','401','bad-200'])await caseRun('session-read-'+fault,async()=>{
    const mid=await upload('session-read-'+fault);await draw(mid)
    const workspaceMatcher='**/api/track/workspace/'+mid
    let lost=false
    await page.route(workspaceMatcher,async route=>{
      if(route.request().method()==='PUT'&&!lost){lost=true;await route.fetch();await route.abort('failed')}
      else await route.continue()
    })
    await draw(mid,2)
    await poll(async()=>await page.getByTestId('workspace-save-error').count()>0,'lost save response has pending retry')
    const pending=await page.evaluate(()=>Object.fromEntries(Object.entries(sessionStorage).filter(([k])=>k.startsWith('annotation-write:'))))
    assert(Object.keys(pending).length>0,'a real save intent is retained')
    await page.unroute(workspaceMatcher)
    const matcher='**/api/auth/me'
    await page.route(matcher,route=>fault==='timeout'?route.abort('timedout'):route.fulfill(fault==='bad-200'?{status:200,json:{id:null,username:null}}:{status:Number(fault),json:{detail:'injected identity read failure'}}))
    await page.reload()
    if(fault==='401'){
      await page.getByPlaceholder('请输入账号').waitFor()
      assert.equal(await page.evaluate(()=>localStorage.getItem('rare-sperm-token')),null)
    }else{
      await button('重新验证登录').waitFor()
      assert((await page.evaluate(()=>localStorage.getItem('rare-sperm-token')))===token,'temporary identity read failure preserves the exact saved token')
      assert.equal(await page.getByTestId('annotation-hit').count(),0,'uncertain identity cannot mount an editable workspace')
    }
    assert.deepEqual(await page.evaluate(()=>Object.fromEntries(Object.entries(sessionStorage).filter(([k])=>k.startsWith('annotation-write:')))),pending)
    await page.unroute(matcher)
    if(fault==='401'){
      await page.getByPlaceholder('请输入账号').fill(username);await page.getByPlaceholder('请输入密码').fill(password)
      await button('登录').click()
    }else await button('重新验证登录').click()
    await ready()
    assert.equal((await api('/track/workspace/'+mid)).manualAnnotations.length,2,'the committed save is replayed without duplicate boxes')
    await draw(mid,3);await page.reload();await ready()
    assert.equal((await api('/track/workspace/'+mid)).manualAnnotations.length,3)
    observations={fault,pendingPreserved:true,committedSaveReplayed:true,recoveredManualCount:3}
  })

  await caseRun('legacy-tracking-pause-and-confirm',async()=>{
    const legacyRequest=route=>route.continue({postData:JSON.stringify({...route.request().postDataJSON(),reviewMode:false})})
    await page.route('**/api/track',legacyRequest)
    const mid=await upload('first-pause')
    await setObjectName(page,'audit-pause')
    await draw(mid)
    // The second object is also named explicitly through the actual editor.
    await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur());await page.keyboard.press('Escape')
    await setObjectName(page,'audit-pause')
    await draw(mid,2)
    await legacyTrackingProbe(page).click()
    await page.getByTestId('confirm-tracking-normal').waitFor()
    await poll(async()=>(await api('/track/workspace/'+mid)).pausedAnomalies?.length===2,'real paused context persisted')
    const paused=await api('/track/workspace/'+mid)
    assert.equal(paused.lastPausedContext.frameIndex,1)
    assert.equal(paused.pausedAnomalies.length,2)
    await page.getByTestId('confirm-tracking-normal').click()
    await poll(async()=>(await api('/track/workspace/'+mid)).trackingFeedbackEvents?.length===2,'one whole-frame checkpoint saved for both objects')
    await poll(async()=>(await api('/track/result/'+mid)).frames.length===3,'real tracking resumes and publishes remaining frame')
    await ready()
    const resumed=await api('/track/workspace/'+mid)
    assert.equal(resumed.trackingFeedbackEvents.length,2)
    assert.equal(new Set(resumed.trackingFeedbackEvents.map(event=>event.checkpointId)).size,1)
    assert.ok(resumed.trackingFeedbackEvents.every(event=>event.scope==='frame'&&event.geometryReference.checkpoint))
    assert.deepEqual(resumed.pausedAnomalies,[])
    await page.reload();await ready()
    assert.equal(await page.locator('.object-select').count(),2)
    await page.unroute('**/api/track',legacyRequest)
    observations={firstPauseOriginalFrame:1,confirmedObjects:2,resultFrames:3,reloadedObjects:2,model:'simulated size anomaly; real detector and publication'}
  })

  await caseRun('authoritative-tracking-reload',async()=>{
    const mid=await upload('authoritative');await draw(mid)
    await legacyTrackingProbe(page).click()
    await poll(async()=>(await api('/track/result/'+mid)).frames.length===3,'three tracking frames published')
    await ready();assert.equal(await page.locator('.object-select').count(),1)
    // Another tab cuts the old future branch. The SPA must use the new source.
    const rewind=await api('/track/rewind','POST',{mediaId:mid,startFrame:0})
    assert.equal(rewind.removedRows,2)
    await button('审查模式').click();await page.getByTestId('review-panel').waitFor()
    await button('人工标注').click();await ready()
    const server=await api('/track/result/'+mid)
    const uiBeforeReload=await page.locator('.object-select').count()
    const selectedFrame=await page.getByRole('spinbutton',{name:'跳转帧号'}).inputValue()
    await page.screenshot({path:path.join(out,'stale-tracking-before-refresh.png')})
    await page.reload();await ready()
    const uiAfterReload=await page.locator('.object-select').count()
    observations={serverFrames:server.frames.map(f=>f.frameIndex),selectedFrame,uiBeforeReload,uiAfterReload,rewindRemovedRows:rewind.removedRows}
    assert.equal(selectedFrame,'3','remain on the previously viewed original frame')
    assert.equal(uiAfterReload,0,'a full reload recovers the true empty displayed frame')
    assert.equal(uiBeforeReload,0,'SPA reload must remove objects absent from the authoritative result')
  })

  await caseRun('malformed-success-response',async()=>{
    const mid=await upload('malformed-success');await draw(mid)
    await legacyTrackingProbe(page).click()
    await poll(async()=>(await api('/track/result/'+mid)).frames.length===3,'tracking result before malformed response')
    await ready();assert.equal(await page.locator('.object-select').count(),1)
    await poll(async()=>await page.getByRole('spinbutton',{name:'跳转帧号'}).inputValue()==='3','tracking end position restored in this client before refresh')
    const matcher='**/api/track/result/'+mid+'*'
    await page.route(matcher,route=>route.fulfill({status:200,json:{
      format:'sam3-tracking-results-jsonl',state:'available',frames:null,count:3}}))
    await page.reload();await page.getByTestId('annotation-hit').waitFor()
    await page.waitForTimeout(400)
    const blocked=await locked(),hasError=await page.getByTestId('workspace-save-error').count()>0
    const visibleObjects=await page.locator('.object-select').count()
    await page.screenshot({path:path.join(out,'malformed-success-response.png')})
    await page.unroute(matcher);await page.reload();await ready()
    const recoveredObjects=await page.locator('.object-select').count()
    await draw(mid,2);await page.reload();await ready()
    const recoveredManualCount=(await api('/track/workspace/'+mid)).manualAnnotations.length
    observations={brokenPayload:{state:'available',frames:null,count:3},blocked,hasError,visibleObjects,
      recoveredObjects,recoveredManualCount}
    assert.equal(recoveredObjects,1,'restoring a real result must recover the AI box')
    assert.equal(recoveredManualCount,2,'after the fault clears, a new real edit must save and reload')
    assert.equal(blocked,true,'HTTP 200 with a malformed result must not silently unlock an empty editor')
    assert.equal(hasError,true,'a malformed result must show an actionable read failure')
  })

  for (const fault of [false,true]) await caseRun(fault?'late-workspace-error':'late-workspace-success',async()=>{
    const labelA='race-'+(fault?'failure':'healthy')+'-a'
    const labelB='race-'+(fault?'failure':'healthy')+'-b'
    const a=await upload(labelA);await draw(a)
    const b=await upload(labelB);await draw(b)
    const before=(await api('/track/workspace/'+b)).manualAnnotations
    let release,entered
    const gate=new Promise(r=>release=r),seen=new Promise(r=>entered=r)
    const matcher='**/api/track/workspace/'+a
    await page.route(matcher,async route=>{
      if(route.request().method()!=='GET')return route.continue()
      entered();await gate
      if(fault)await route.fulfill({status:503,json:{detail:'injected late read failure for previous asset'}})
      else await route.continue()
    })
    await openAsset(page,page.locator('.asset-card').filter({hasText:labelA+'.avi'}));await seen
    await openAsset(page,page.locator('.asset-card').filter({hasText:labelB+'.avi'}));await ready()
    release();await page.waitForTimeout(700)
    const blocked=await locked(),hasError=await page.getByTestId('workspace-save-error').count()>0
    const preserved=JSON.stringify((await api('/track/workspace/'+b)).manualAnnotations)===JSON.stringify(before)
    observations={fault,activeAsset:labelB+'.avi',blocked,hasError,savedBoxesPreserved:preserved}
    if(blocked) {
      await page.screenshot({path:path.join(out,'late-error-locks-healthy-video.png')})
      await page.unroute(matcher);await button('重新读取工作区').click();await ready()
    }
    await draw(b,2);await page.reload();await ready()
    observations.recoveredManualCount=(await api('/track/workspace/'+b)).manualAnnotations.length
    assert.equal(preserved,true,'a late read must preserve the active saved boxes')
    assert.equal(observations.recoveredManualCount,2,'after recovery, real drawing saves and reloads')
    assert.equal(blocked,false,'a previous asset reply must not lock a healthy active asset')
    assert.equal(hasError,false,'a previous asset reply must not publish an error on the active asset')
  })

  await caseRun('tracking-pause-policy',async()=>{
    observations=await trackingPolicyChecks(page,{root,origin,api,poll,ready,out})
  })

  await caseRun('tracking-focus-selection',async()=>{
    observations=await trackingFocusChecks(page,{root,origin,api,poll,ready,out})
  })

  await caseRun('4k-50000-objects',async()=>{
    const mid=await upload('dense-4k',true);await draw(mid)
    assert(/^[^./\\]+$/.test(mid))
    const reference=JSON.parse(fs.readFileSync(path.join(root,'reference-frames.json')))
    const verifiedFrames=[]
    for(const fi of [0,29,59]) {
      const image=await page.request.get(origin+`/api/track/frame/${mid}/${fi}`,{headers:{Authorization:'Bearer '+token}})
      assert(image.ok())
      assert.equal(createHash('sha256').update(await image.body()).digest('hex'),reference[String(fi)],'exact native 4K frame '+fi+' agrees with independent decoding')
      verifiedFrames.push(fi)
    }
    // Performance fixture only: labels are synthetic, video decoding stays real.
    const rows=Array.from({length:60},(_,fi)=>({frame_index:fi,source_frame_index:fi,objects:[]}))
    for(let i=0;i<50000;i++) {
      const fi=i%60,oid=100+Math.floor(i/60),x=(oid%40)*90,y=(Math.floor(oid/40)%20)*90
      rows[fi].objects.push({object_id:oid,bbox:[x,y,x+20,y+20],score:.99})
    }
    fs.writeFileSync(path.join(root,'storage/media',mid,'tracker_results.json'),rows.map(JSON.stringify).join('\n')+'\n')
    const httpStart=performance.now(),result=await api('/track/result/'+mid),readMs=performance.now()-httpStart
    assert.equal(result.frames.reduce((n,f)=>n+f.annotations.length,0),50000)
    await page.addInitScript(()=>{window.auditLongTasks=[];try{new PerformanceObserver(l=>window.auditLongTasks.push(...l.getEntries().map(e=>e.duration))).observe({entryTypes:['longtask']})}catch{}})
    const started=Date.now();await page.reload();await ready()
    const hydratedMs=Date.now()-started
    const currentCount=await page.locator('.object-select').count()
    assert.equal(currentCount,rows[0].objects.length+1)
    const imageSize=await page.locator('img').evaluateAll(images=>images.filter(i=>i.naturalWidth>=3840).map(i=>[i.naturalWidth,i.naturalHeight]))
    assert(imageSize.some(([w,h])=>w===3840&&h===2160),'a decoded native 4K frame must actually display')
    await draw(mid,2,true)
    await page.reload();await ready()
    const state=await api('/track/workspace/'+mid)
    const local=await page.evaluate(()=>({annotations:localStorage.getItem('annotationsByMedia'),longTasks:window.auditLongTasks||[]}))
    const cached=JSON.parse(local.annotations)
    assert(Object.values(cached).flat().every(o=>o.source==='manual'),'server AI arrays must not fill localStorage')
    assert.equal(state.manualAnnotations.length,2)
    await page.setViewportSize({width:1180,height:760});await button('切换深色主题').click()
    await page.screenshot({path:path.join(out,'4k-50000-objects.png')})
    observations={width:3840,height:2160,frames:60,syntheticObjects:50000,currentFrameObjects:currentCount,
      resultReadMs:readMs,firstHydrationMs:hydratedMs,responseBytes:Buffer.byteLength(JSON.stringify(result)),
      localCacheBytes:Buffer.byteLength(local.annotations),maxLongTaskMs:Math.max(0,...local.longTasks),
      independentlyDecodedFrames:verifiedFrames,recoveredManualCount:state.manualAnnotations.length,labels:'synthetic; accuracy not evaluated'}
    // Broad CI hang guards, not a latency promise for hospital GPU/network hardware.
    assert(readMs<8000,'50k result read exceeds the isolated regression budget')
    assert(hydratedMs<15000,'4K hydration exceeds the isolated regression budget')
    assert(observations.localCacheBytes<65536,'AI results must not inflate synchronous local storage')
    assert(observations.maxLongTaskMs<1500,'a sustained main-thread freeze exceeds the regression budget')
  })
} finally { await browser.close() }
console.log(JSON.stringify(results.map(({name,status,error})=>({name,status,error})),null,2))
process.exitCode=results.some(r=>r.status==='failed')?1:0
