// Simulated GPU job responses; original UI pipeline and real source frame reads.
import fs from 'node:fs'
import assert from 'node:assert/strict'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const f=JSON.parse(fs.readFileSync('work/confirmation-browser-fixture.json'))
const b=await chromium.launch({channel:'chrome',headless:true}),p=await b.newPage({reducedMotion:'reduce',viewport:{width:1440,height:900}})
const origin=process.env.CONFIRMATION_ORIGIN||'http://127.0.0.1:5373'
const errors=[],logs=[];p.on('pageerror',e=>errors.push(String(e)));p.on('console',m=>logs.push({type:m.type(),text:m.text()}));let n=0,mode='success',release,started,seeds=[]
const check=(ok,msg)=>{assert(ok,msg);console.log('PASS',++n,msg)}
try{
 await p.addInitScript(f=>{localStorage.setItem('rare-sperm-token',f.token);localStorage.setItem('rare-sperm-auth',JSON.stringify({id:'3',name:'UX tester'}))},f)
 await p.route('**/api/track/media',async r=>{const res=await r.fetch(),d=await res.json();d.items=d.items.filter(m=>m.mediaId==='ux-video');await r.fulfill({response:res,json:d})})
 await p.route('**/api/track/rewind',r=>r.fulfill({json:{removedRows:0,deletedFutureSeedFiles:0}}))
 await p.route('**/api/track/annotations',r=>{seeds.push(r.request().postDataJSON());return r.fulfill({json:{filename:'fixture-seed.json'}})})
 await p.route('**/api/annotation/annotations/manual',r=>r.fulfill({status:201,json:{id:'fixture-save',batchId:'fixture'}}))
 await p.route('**/api/track',r=>r.fulfill({json:{taskId:'ux-simulated-job',status:'queued',maxFrames:5}}))
 await p.route('**/api/track/status/ux-simulated-job',async r=>{
   if(release===undefined){await new Promise(resolve=>{release=resolve;started?.()})}
   await r.fulfill({json:mode==='success'?{status:'success',lastProcessedFrame:4,reachedVideoEnd:false}:{status:'paused',paused:true,pausedFrame:6,anomalyLevels:{1:'anomaly'},pausedObjects:[{object_id:1,name:'sperm',display_name:'sperm 1',type:'size_growth',reasons:['manual_area_ratio=1.800 HARD'],manualBaselineFrame:4,reviewStartFrame:3,reviewEndFrame:6,metrics:{areaRatio:1.8}}]}})
 })
 await p.goto(origin+'/annotate');await p.evaluate(async()=>{window.ws=(await import(performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts')).name)).useWorkspace()})
 await openAsset(p,p.locator('.asset-card').filter({hasText:'ux-video.avi'}));await p.waitForFunction(()=>!window.ws.editingBlocked.value&&window.ws.currentObjects.value.length===2)
 await p.evaluate(()=>window.ws.seekVideo(0));await p.getByRole('button',{name:'AI Tracking',exact:true}).click()
 await p.waitForFunction(()=>window.ws.isAiBusy.value);await p.waitForTimeout(150)
 check(await p.getByRole('button',{name:'下一帧',exact:true}).isDisabled(),'AI running locks user frame navigation')
 await p.evaluate(()=>window.ws.seekByFrame(1));check(await p.evaluate(()=>window.ws.currentFrame.value)===0,'user seek helper cannot move the seed frame during tracking')
 for(let i=0;i<60&&!release;i++)await new Promise(r=>setTimeout(r,50));assert(release);release()
 await p.waitForFunction(()=>!window.ws.isAiBusy.value)
 check(await p.evaluate(()=>window.ws.currentFrame.value)===4,'successful tracking still navigates internally to the last processed frame')
 check(seeds[0].frameIndex===0&&seeds[0].mediaWidth===800&&seeds[0].annotations.length===2,'tracking seed retains original frame, dimensions and object count')
 mode='paused';await p.getByRole('button',{name:'AI Tracking',exact:true}).click();await p.waitForFunction(()=>!window.ws.isAiBusy.value&&window.ws.currentFrame.value===6)
 check(await p.locator('.tracking-anomaly').isVisible(),'anomaly pause navigates to its exact frame and displays the existing warning')
 const pauseTitle=await p.locator('.tracking-feedback-title strong').innerText()
 check(pauseTitle.includes('第 7 帧')&&(await p.getByTestId('tracking-diagnostics').getAttribute('open'))===null,'anomaly banner shows the paused frame with diagnostic reminders collapsed')
 check(await p.getByRole('button',{name:'AI Tracking',exact:true}).isDisabled(),'plain AI start cannot implicitly accept the paused anomaly')
 await p.getByRole('button',{name:'上一帧',exact:true}).click();await p.waitForFunction(()=>window.ws.currentFrame.value===5)
 check((await p.locator('.tracking-feedback-title strong').innerText()).includes('第 7 帧'),'browsing another frame does not rewrite the paused-frame banner')
 check(await p.getByTestId('confirm-tracking-normal').isDisabled()&&await p.getByTestId('confirm-tracking-corrected').isDisabled(),'explicit feedback cannot be submitted while browsing a different frame')
 await p.locator('.tracking-anomaly summary').click();const text=await p.locator('.tracking-anomaly').innerText()
 check(text.includes('最近人工标注：第 5 帧')&&text.includes('第 4～7 帧')&&text.includes('确认保存成功')&&text.includes('查看前几帧是否已经偏移'),'expanded anomaly details preserve manual provenance, review range and explicit save-before-resume meaning')
 await p.locator('.tracking-anomaly summary').click();check(await p.locator('.timeline-anomaly').count()===1,'collapsing diagnostic details retains the timeline marker')
 await p.getByRole('button',{name:'返回第 7 帧',exact:true}).click();await p.waitForFunction(()=>window.ws.currentFrame.value===6&&!window.ws.exactFrameLoading.value)
 check(await p.getByTestId('confirm-tracking-normal').isEnabled()&&await p.getByTestId('confirm-tracking-corrected').isEnabled(),'returning to the real paused frame enables both explicit confirmation choices')
 await p.getByTestId('tracking-diagnostics').locator('summary').click();check(await p.locator('.tracking-learn').count()===0&&(await p.getByTestId('tracking-geometry-acceptance').innerText()).includes('无需重画'),'size anomalies accept reviewed geometry without requiring redrawing or broadening normal-motion calibration')
 await p.screenshot({path:'output/playwright/ux-tracking-explicit-feedback.png',animations:'disabled'})
 check(errors.length===0,'simulated tracking UI has no uncaught errors')
 console.log('SUCCESS',n,'checks')
}finally{fs.writeFileSync('output/playwright/ux-tracking-browser-console.json',JSON.stringify({errors,logs},null,2));await b.close()}
