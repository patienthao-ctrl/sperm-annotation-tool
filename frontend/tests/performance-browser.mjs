// Real user-video copies, bounded client state, compact C writes and recovery.
import assert from 'node:assert/strict'
import fs from 'node:fs'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
// The task list is a drawer that stays closed while a task is open.
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const fixture=JSON.parse(fs.readFileSync('work/real-4k-browser-fixture.json'))
const origin=process.env.CONFIRMATION_ORIGIN||'http://127.0.0.1:5387'
const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({reducedMotion:'reduce',viewport:{width:1366,height:768}})
const errors=[],checks=[],reads=[]
const check=(ok,msg)=>{assert(ok,msg);checks.push(msg);console.log('PASS',checks.length,msg)}
page.on('pageerror',e=>errors.push(String(e)))
page.on('dialog',d=>d.accept())
page.on('request',r=>{if(r.url().includes('/api/track/frame/'))reads.push(r.url())})
const button=name=>page.getByRole('button',{name,exact:true})
const idle=()=>page.waitForFunction(()=>window.ws&&!window.ws.workspaceRestoring.value&&!window.ws.exactFrameLoading.value)
const api=async(path,method='GET',body)=>{const r=await page.request.fetch(origin+'/api'+path,{method,headers:{Authorization:'Bearer '+fixture.token,'Idempotency-Key':crypto.randomUUID(),'X-Review-Contract':'2','X-Confirmation-Response':'delta'},data:body});assert(r.ok(),await r.text());return r.json()}
const attach=()=>page.evaluate(async()=>{window.ws=(await import(performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts')).name)).useWorkspace()})
fs.mkdirSync('output/playwright',{recursive:true})
try{
  await page.addInitScript(f=>{localStorage.setItem('rare-sperm-token',f.token);localStorage.setItem('rare-sperm-auth',JSON.stringify({id:'3',name:'Performance tester'}))},fixture)
  await page.route('**/api/track/media',async route=>{const r=await route.fetch(),d=await r.json();d.items=d.items.filter(m=>fixture.assets.some(x=>x.mid===m.mediaId));await route.fulfill({response:r,json:d})})
  await page.goto(origin+'/annotate');await attach();await idle()
  for(const asset of fixture.assets){
    await openAsset(page,page.locator('.asset-card').filter({hasText:asset.name}));await idle()
    const state=await page.evaluate(()=>({w:window.ws.selectedMedia.value.width,h:window.ws.selectedMedia.value.height,fps:window.ws.videoFps.value}))
    check(state.w===asset.width&&state.h===asset.height&&state.fps===asset.fps,asset.name+': native dimensions and source FPS load')
    for(const fi of [Math.floor(asset.frameCount/2),asset.frameCount-1,0]){
      await page.evaluate(async fi=>{await window.ws.loadExactFrame(fi)},fi);await idle()
      check(await page.evaluate(fi=>window.ws.currentFrame.value===fi&&!!window.ws.exactFrameUrl.value,fi),asset.name+': exact frame '+fi+' displays')
    }
  }
  const long=fixture.assets.reduce((a,b)=>a.frameCount>b.frameCount?a:b)
  await openAsset(page,page.locator('.asset-card').filter({hasText:long.name}));await idle()
  const telemetry=await page.evaluate(async()=>{
    const ws=window.ws,mid=ws.currentMediaId.value;window.originalAnnotations=ws.annotationsByMedia.value[mid]
    const rows=Array.from({length:50000},(_,i)=>({id:'stress-ai-'+i,objectId:100+i%80,frameIndex:Math.floor(i/80),name:'sperm '+(100+i%80),source:'ai',bbox:{x:10+i%20,y:20,width:1,height:1}}))
    const started=performance.now();ws.annotationsByMedia.value={...ws.annotationsByMedia.value,[mid]:[...window.originalAnnotations.filter(o=>o.source==='manual'),...rows]}
    const count=ws.currentObjects.value.length
    return {count,assignmentMs:performance.now()-started,deepProxy:!!ws.annotationsByMedia.value[mid][1].__v_isReactive}
  })
  check(telemetry.count===81&&!telemetry.deepProxy,'50,000 AI boxes use shallow immutable storage and a frame index')
  await page.waitForTimeout(1100)
  const cached=await page.evaluate(()=>{const mid=window.ws.currentMediaId.value,raw=localStorage.getItem('annotationsByMedia');return {bytes:raw.length,objects:JSON.parse(raw)[mid]}})
  check(cached.bytes<50000&&cached.objects.length===1&&cached.objects.every(o=>o.source==='manual'),'large server AI results are not repeatedly serialized into localStorage')
  await page.evaluate(()=>{window.ws.selectedObjectId.value='stress-ai-0';window.ws.nudgeSelected(1,0)})
  check(await page.evaluate(()=>window.ws.currentObjects.value.find(o=>o.id==='stress-ai-0').source==='manual'),'editing a dense frame still commits only the selected object')
  await page.evaluate(()=>window.ws.undo())
  check(await page.evaluate(()=>window.ws.currentObjects.value.find(o=>o.id==='stress-ai-0').source==='ai'),'dense-frame undo restores exact object source')
  await page.evaluate(async()=>{const ws=window.ws,mid=ws.currentMediaId.value;ws.annotationsByMedia.value={...ws.annotationsByMedia.value,[mid]:window.originalAnnotations};await ws.persistWorkspaceState(mid,true)})
  const badFrame=Math.floor(long.frameCount/2)+7,badUrl=`**/api/track/frame/${long.mid}/${badFrame}`
  await page.route(badUrl,route=>route.fulfill({status:200,contentType:'image/jpeg',body:'broken JPEG'}))
  const badResult=await page.evaluate(async fi=>({ok:await window.ws.loadExactFrame(fi),frame:window.ws.currentFrame.value,error:window.ws.frameError.value}),badFrame)
  check(!badResult.ok&&badResult.frame===0&&!!badResult.error,'undecodable HTTP 200 frame keeps the previous image/frame and locks editing')
  await page.unroute(badUrl);await page.evaluate(async()=>{await window.ws.retryExactFrame()});await idle()
  check(await page.evaluate(fi=>window.ws.currentFrame.value===fi&&!window.ws.frameError.value,badFrame),'bad JPEG cache invalidation allows a fresh successful retry')
  await page.evaluate(async()=>{await window.ws.loadExactFrame(0)});await idle()
  let failRead=true
  await page.route('**/api/track/result/**',async route=>{if(failRead)await route.fulfill({status:500,json:{message:'Injected initial result read failure'}});else await route.continue()})
  await page.reload();await attach();await idle()
  check(await page.evaluate(()=>window.ws.editingBlocked.value&&window.ws.saveError.value.includes('追踪结果读取失败')),'failed initial AI-result read locks editing instead of treating unknown frames as empty')
  failRead=false;await button('重新读取工作区').click();await idle()
  check(await page.evaluate(()=>!window.ws.editingBlocked.value&&window.ws.currentObjects.value.length===3),'explicit reload restores server AI results after compact local caching')
  check(errors.length===0,'no uncaught browser errors across real-video and dense-annotation flows')
  await page.screenshot({path:'output/playwright/real-4k-performance.png'})
  fs.writeFileSync('output/playwright/performance-results.json',JSON.stringify({checks,telemetry,errors},null,2))
  console.log('Completed',checks.length,'real-video performance/recovery checks.')
}finally{await browser.close()}
