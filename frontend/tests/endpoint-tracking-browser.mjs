import {passReview} from './legacy-tracking-probe.mjs'
// Real UI / storage / workflow; model inference is explicitly simulated by host.
import assert from 'node:assert/strict'
import fs from 'node:fs'
if(process.env.CRITICAL_AI_MODE==='simulated'){
  const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
  const origin=process.env.CRITICAL_ORIGIN
  assert(['localhost','127.0.0.1'].includes(new URL(origin).hostname))
  const browser=await chromium.launch({channel:process.env.CHROME_CHANNEL||'chrome',headless:true})
  const page=await browser.newPage({reducedMotion:'reduce',viewport:{width:1366,height:768}})
  page.setDefaultTimeout(15000)
  page.on('dialog',d=>d.accept())
  const errors=[];page.on('pageerror',e=>errors.push(String(e)))
  const button=name=>page.getByRole('button',{name,exact:true})
  const checks=[];const check=(value,message)=>{assert(value,message);checks.push(message);console.log('ENDPOINT PASS',message)}
  const username='endpoint-'+crypto.randomUUID(),password=crypto.randomUUID()
  const registered=await page.request.post(origin+'/api/auth/register',{data:{username,password}})
  assert.equal(registered.status(),201);const token=(await registered.json()).token
  const api=async(url)=>{const r=await page.request.get(origin+'/api'+url,{headers:{Authorization:'Bearer '+token}});assert(r.ok(),await r.text());return r.json()}
  let mid
  const poll=async fn=>{for(let i=0;i<150;i++){if(await fn())return;await page.waitForTimeout(100)}throw new Error('endpoint condition timed out')}
  const ready=async()=>{await page.getByTestId('annotation-hit').waitFor();await poll(async()=>!(await page.getByTestId('annotation-hit').getAttribute('class')).includes('locked'))}
  const draw=async count=>{await ready();await page.locator('.shell-rail').click({position:{x:22,y:300}});await page.keyboard.press('b');const r=await page.getByTestId('annotation-hit').boundingBox();await page.mouse.move(r.x+r.width*.35,r.y+r.height*.35);await page.mouse.down();await page.mouse.move(r.x+r.width*.45,r.y+r.height*.46,{steps:5});await page.mouse.up();await poll(async()=>(await api('/track/workspace/'+mid)).manualAnnotations?.length===count)}
  try{
    await page.goto(origin+'/login');await page.getByPlaceholder('请输入账号').fill(username);await page.getByPlaceholder('请输入密码').fill(password);await button('登录').click();await ready()
    if(!await page.locator('.shell-drawer').isVisible())await page.getByTestId('toggle-workbench-library').click()
    const upload=page.waitForResponse(r=>r.url().endsWith('/api/track/upload')&&r.request().method()==='POST')
    const input=process.env.CRITICAL_VIDEO+'.endpoint.avi'
    fs.writeFileSync(input,Buffer.concat([fs.readFileSync(process.env.CRITICAL_VIDEO),Buffer.from('endpoint-'+crypto.randomUUID())]))
    await page.locator('input[type=file][accept="video/*"]').setInputFiles(input)
    const response=await upload;assert.equal(response.status(),201,await response.text())
    mid=(await response.json()).mediaId;await page.getByRole('button',{name:'尾帧 · 0 人工框',exact:true}).waitFor();await ready();await draw(1)
    await page.getByRole('button',{name:'尾帧 · 0 人工框',exact:true}).click();await ready();await draw(2)
    let state=await api('/track/workspace/'+mid)
    check(state.manualAnnotations[0].objectId!==state.manualAnnotations[1].objectId,'tail annotation has independent identity without guessing first-frame ID')
    await page.reload();await ready();check((await api('/track/workspace/'+mid)).manualAnnotations.length===2,'both human endpoint annotations survive refresh')
    const bodies=[],keys=[];let lose=true
    await page.route('**/api/track',async route=>{bodies.push(route.request().postDataJSON());keys.push(route.request().headers()['idempotency-key']);const response=await route.fetch();if(lose){lose=false;await route.abort('failed')}else await route.fulfill({response})})
    const started=page.waitForRequest(r=>r.url().endsWith('/api/track')&&r.method()==='POST')
    await button('首尾双向追踪').click();await started
    await page.getByTestId('tracking-error').waitFor();await button('重试原追踪请求').click()
    await poll(async()=>await page.getByTestId('tracking-error').count()===0)
    await poll(async()=>(await api('/track/result/'+mid)).frames.length===3)
    check(keys.length===2&&keys[0]===keys[1]&&JSON.stringify(bodies[0])===JSON.stringify(bodies[1]),'lost start response replays identical key, first-frame and tail-frame snapshots')
    check(bodies[0].startFrame===0&&bodies[0].terminalAnnotations.length===1&&bodies[0].terminalAnnotations[0].source==='manual'&&bodies[0].combineResults===true,'run from tail view requests joint output with independent endpoints')
    // Final A is also checked by backend tests; UI must not display paired AI + manual duplicates.
    await poll(async()=>await page.getByTestId('frame-object-count').innerText()==='1 个框')
    state=await api('/track/workspace/'+mid)
    check(state.manualAnnotations.length===2,'reverse verification preserves both original manual annotations')
    const rawResponse=await page.request.get(origin+'/api/track/result-file/'+mid,{headers:{Authorization:'Bearer '+token}})
    assert(rawResponse.ok());const raw=(await rawResponse.text()).trim().split('\n').map(line=>JSON.parse(line))
    const endpoint=raw.at(-1).reviewEvidence.endpointCheck
    check(raw.every(r=>r.reviewEvidence.jointCheck.state==='checked'),'joint synthesis completed for every frame')
    check(JSON.stringify(raw[1].objects[0].bbox)===JSON.stringify([238,188,262,212]),'middle output is actually refined by the detector instead of copying forward geometry')
    await page.route('**/api/track/result/'+mid+'*',async route=>{const response=await route.fetch();const data=await response.json();data.jointTracking={state:'failed',checkedFrames:0};await route.fulfill({response,json:data})})
    await page.reload();await ready();await page.getByTestId('tracking-error').filter({hasText:'上次自动标注未完成'}).waitFor()
    check(await page.getByTestId('frame-object-count').innerText()==='1 个框','failed synthesis marker survives reload without hiding retained geometry')
    await page.unroute('**/api/track/result/'+mid+'*');await page.reload();await ready()
    check(endpoint.state==='checked'&&Object.values(endpoint.matches).length===1,'real worker publishes completed reverse evidence with independent trajectory mapping')
    await poll(async()=>{const s=await api('/track/workspace/'+mid);return s.manualAnnotations.every(o=>o.objectId===1)})
    check(endpoint.terminalAnnotations[0].object_id!==1,'original tail-local number is retained in the anchor snapshot after canonical numbering')
    await page.route('**/api/track/status/*',async route=>{const response=await route.fetch();const data=await response.json();delete data.endpointVerification;await route.fulfill({response,json:data})})
    await button('首尾双向追踪').click();await page.getByTestId('tracking-error').filter({hasText:'不能视为双向核验完成'}).waitFor()
    check(true,'old or incomplete backend response never becomes successful endpoint verification')
    await page.unroute('**/api/track/status/*');await button('重试追踪').click();await poll(async()=>await page.getByTestId('tracking-error').count()===0)
    await page.route('**/api/track/status/*',async route=>{const response=await route.fetch();const data=await response.json();delete data.jointTracking;await route.fulfill({response,json:data})})
    await button('首尾双向追踪').click();await page.getByTestId('tracking-error').filter({hasText:'不能视为自动标注完成'}).waitFor()
    check(true,'verification-only response cannot masquerade as completed synthesis')
    await page.unroute('**/api/track/status/*');await button('重试追踪').click();await poll(async()=>await page.getByTestId('tracking-error').count()===0)
    await page.getByTestId('delete-object-video').click();await page.getByTestId('confirm-delete-object-video').click()
    await poll(async()=>await page.getByTestId('frame-object-count').innerText()==='0 个框')
    await page.reload();await ready();check(await page.getByTestId('frame-object-count').innerText()==='0 个框','whole-video deletion from reconciled tail cannot resurrect a second local identity')
    // Undo is durable and restores the same stable identity, not a new object.
    await page.getByRole('button',{name:'撤销',exact:true}).click();await poll(async()=>await page.getByTestId('frame-object-count').innerText()==='1 个框')
    const jump=async n=>{const input=page.getByRole('spinbutton',{name:'跳转帧号'});await input.fill(String(n));await input.press('Enter');await ready()}
    await jump(2);await page.locator('.object-select').first().click();await button('选择').click()
    check(await page.locator('.annotation-box').count()===1,'annotation canvas renders exactly one synthesized box, without alternative-source overlays')
    const box=await page.locator('.annotation-box').boundingBox();assert(box)
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+7,box.y+box.height/2+4,{steps:4});await page.mouse.up()
    await poll(async()=>(await api('/track/workspace/'+mid)).manualAnnotations.some(o=>o.frameIndex===1&&o.objectId===1))
    await button('撤销本帧操作').click();await poll(async()=>!(await api('/track/workspace/'+mid)).manualAnnotations.some(o=>o.frameIndex===1))
    check(true,'synthesized box supports normal manual adjustment and undo without a second overlay')
    await page.screenshot({path:'output/playwright/joint-tracking-middle.png'});await jump(3);await page.locator('.object-select').first().click()
    await page.screenshot({path:'output/playwright/endpoint-tracking.png'})
    await button('专注').click();check(await button('首尾双向追踪').isVisible(),'focus mode retains endpoint run entry');await page.screenshot({path:'output/playwright/endpoint-tracking-focus.png'});await button('退出专注').click()
    for(const width of [1180,760]){await page.setViewportSize({width,height:844});for(const theme of ['light','dark']){await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.screenshot({path:`output/playwright/endpoint-tracking-${width}-${theme}.png`});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal overflow for endpoint controls '+width+' '+theme)}}
    await page.setViewportSize({width:1366,height:768})
    // Saved legacy joint output: human head is tight, AI is padded, IDs unpaired.
    // Only this fixture installation is synthetic; all subsequent reads/merge/UI/export are real.
    const {execFileSync}=await import('node:child_process')
    execFileSync(process.env.TEST_PYTHON||'python3',['-m','tests.endpoint_duplicate_browser_fixture',mid],{env:{...process.env,PYTHONPATH:'backend'}})
    await page.reload();await page.getByTestId('annotation-hit').waitFor()
    await poll(async()=>await page.getByTestId('workspace-save-error').count()>0||!(await page.getByTestId('annotation-hit').getAttribute('class')).includes('locked'))
    if(await page.getByTestId('workspace-save-error').count()){await button('重新读取工作区').click()}
    await ready();await jump(3)
    check(await page.locator('.annotation-box').count()===1,'old padded AI plus tight unmatched human tail renders exactly one box after reload')
    const repaired=await api('/track/result/'+mid)
    check(repaired.terminalIdentity.matches['91']===1,'real API recovers the identity from saved reverse observations')
    const repairedRaw=await page.request.get(origin+'/api/track/result-file/'+mid,{headers:{Authorization:'Bearer '+token}})
    const repairedRows=(await repairedRaw.text()).trim().split('\n').map(JSON.parse)
    check(repairedRows.at(-1).objects.length===1&&repairedRows.at(-1).objects[0].object_id===1,'JSONL download uses the same one-object identity')
    await page.screenshot({path:'output/playwright/endpoint-duplicate-repaired.png'})
    await passReview(page,token,mid)
    const p=await api('/datasets/annotations/'+mid+'/preview')
    const frozen=await page.request.post(origin+'/api/datasets/annotations/'+mid+'/snapshot',{headers:{Authorization:'Bearer '+token,'X-Review-Contract':'2','Idempotency-Key':crypto.randomUUID()},data:{expectedSourceRevision:p.sourceRevision,confirmComplete:true,explicitEmptyFrameRanges:[]}})
    assert(frozen.ok(),await frozen.text())
    const frozenId=(await frozen.json()).finalVersionId
    const exportPreview=await page.request.post(origin+'/api/datasets/exports/preview',{headers:{Authorization:'Bearer '+token},data:{finalVersionIds:[frozenId]}})
    check(exportPreview.ok()&&(await exportPreview.json()).objectCount===3,'dataset source has exactly one head per frame rather than duplicate labels')
    execFileSync(process.env.TEST_PYTHON||'python3',['-m','tests.endpoint_duplicate_browser_fixture',mid,'reassigned'],{env:{...process.env,PYTHONPATH:'backend'}})
    await page.reload();await page.getByTestId('annotation-hit').waitFor()
    await poll(async()=>await page.getByTestId('workspace-save-error').count()>0||!(await page.getByTestId('annotation-hit').getAttribute('class')).includes('locked'))
    if(await page.getByTestId('workspace-save-error').count()){await button('重新读取工作区').click()}
    await ready();await jump(3)
    await poll(async()=>{const s=await api('/track/workspace/'+mid);return s.manualAnnotations.some(o=>o.frameIndex===2&&o.objectId===2&&o.endpointOriginalObjectId===91)})
    check(await page.locator('.annotation-box').count()===1,'corrected same-run identity refresh keeps one human tail box, not the old canonical ID plus a second AI box')
    await page.screenshot({path:'output/playwright/endpoint-identity-corrected.png'})
    await page.reload();await ready();await jump(3)
    check(await page.locator('.annotation-box').count()===1,'corrected endpoint identity survives a second reload')
    await page.goto(origin+'/review');await page.getByTestId('review-panel').waitFor()
    await poll(async()=>(await page.getByTestId('review-status').count())>0)
    const reviewStatus=(await api('/annotation-review/media/'+mid)).status
    check((await page.getByTestId('review-status').innerText())==={passed:'审查通过',stale:'需重新审查'}[reviewStatus],'review page shows the same pass state as the server after identity repair')
    await page.screenshot({path:'output/playwright/endpoint-tracking-review.png'})
    check(errors.length===0,'no browser runtime errors')
  }finally{fs.writeFileSync('output/playwright/endpoint-tracking-results.json',JSON.stringify({checks,errors},null,2));await page.screenshot({path:'output/playwright/endpoint-tracking-last.png'}).catch(()=>{});await browser.close()}
}
