// Real UI gestures and isolated server data. No business database/media are modified.
import assert from 'node:assert/strict'
import fs from 'node:fs'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const fixture=JSON.parse(fs.readFileSync('work/confirmation-browser-fixture.json'))
const origin=process.env.CONFIRMATION_ORIGIN||'http://127.0.0.1:5373'
const browser=await chromium.launch({channel:process.env.CHROME_CHANNEL || 'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:900}})
const errors=[],logs=[],requests=[];let checks=0
page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>logs.push({type:m.type(),text:m.text()}));page.on('request',r=>requests.push(r.url()))
const check=(ok,msg)=>{assert(ok,msg);console.log('PASS',++checks,msg)}
const state=()=>page.evaluate(()=>({objects:JSON.parse(JSON.stringify(window.ws.currentObjects.value)),frame:window.ws.currentFrame.value,tool:window.ws.activeTool.value,zoom:window.ws.zoom.value,busy:window.ws.editingBlocked.value,save:window.ws.saveState.value}))
const idle=()=>page.waitForFunction(()=>window.ws&&!window.ws.workspaceRestoring.value&&!window.ws.exactFrameLoading.value)
const uiButton=(name)=>page.getByRole('button',{name,exact:true})
const blur=()=>page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur())
async function go(frame){await page.getByRole('spinbutton',{name:'跳转帧号'}).fill(String(frame+1));await page.getByRole('spinbutton',{name:'跳转帧号'}).press('Enter');await idle();await page.waitForFunction(fi=>window.ws.currentFrame.value===fi,frame);await blur()}
async function selectMedia(name){await openAsset(page,page.locator('.asset-card').filter({hasText:name}));await idle();await page.waitForFunction(()=>window.ws.currentObjects.value.length===2)}
async function rect(){return page.getByTestId('annotation-hit').boundingBox()}
async function gesture(x,y,dx,dy,finish=true){const r=await rect();await page.mouse.move(r.x+x*r.width/100,r.y+y*r.height/100);await page.mouse.down();await page.mouse.move(r.x+x*r.width/100+dx,r.y+y*r.height/100+dy,{steps:12});await page.waitForTimeout(40);if(finish)await page.mouse.up()}
try{
  await page.addInitScript(f=>{localStorage.setItem('rare-sperm-token',f.token);localStorage.setItem('rare-sperm-auth',JSON.stringify({id:'3',name:'UX tester'}))},fixture)
  // Keep unrelated legacy storage out of the fixture's media list.
  await page.route('**/api/track/media',async route=>{const r=await route.fetch(),d=await r.json();d.items=d.items.filter(m=>m.mediaId.startsWith('ux-'));await route.fulfill({response:r,json:d})})
  await page.goto(origin+'/annotate');await page.evaluate(async()=>{window.ws=(await import(performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts')).name)).useWorkspace()});await idle()
  check(await page.locator('html').getAttribute('data-theme')==='light','new default is a light workbench')
  await selectMedia('ux-video.avi');await go(0)
  check((await state()).objects.length===2,'real tracking objects load into the annotation workspace')
  check(await page.getByRole('checkbox',{name:'识别边缘入画',exact:true}).count()===0,'edge discovery is automatic without an option')
  check(await page.evaluate(()=>window.ws.edgeDiscovery.value),'new tracking defaults to edge discovery')

  check(await page.locator('.annotation-box').first().evaluate(el=>parseFloat(getComputedStyle(el).strokeWidth))>=2,'annotation outlines use readable screen-pixel widths')
  const initial=(await state()).objects,box=initial[0].bbox
  // Model is stubbed here; real GPU predictions are verified separately on the server.
  let headMode='ok',headProposal=[500,240,520,258]
  await page.route('**/api/track/head-box/*',async route=>{
    if(headMode==='slow')await new Promise(resolve=>setTimeout(resolve,600))
    if(headMode==='error')return route.fulfill({status:503,json:{detail:'测试：模型暂不可用'}})
    const input=route.request().postDataJSON()
    await route.fulfill({json:{bbox:headMode==='corrupt'?[NaN,20,30,40]:headProposal,confidence:.8,frameIndex:input.frameIndex,width:800,height:450}}).catch(()=>{})
  })
  await uiButton('点选头部').click()
  const headRect=await rect()
  await page.mouse.move(headRect.x+headRect.width*.5,headRect.y+headRect.height*.5)
  await page.mouse.down();await page.mouse.move(5,5);await page.mouse.up();await blur()
  const zoomBeforeHeadDrag=(await state()).zoom
  await page.keyboard.press('+')
  check((await state()).zoom>zoomBeforeHeadDrag,'dragging out of the head tool never leaves the viewport locked')
  await page.keyboard.press('-')
  const clickHead=async()=>{await uiButton('点选头部').click();const r=await rect();await page.mouse.click(r.x+r.width*.64,r.y+r.height*.55)}
  await clickHead();await page.waitForFunction(()=>window.ws.currentObjects.value.length===3)
  check((await state()).objects.at(-1).source==='manual','head click creates a new editable manual object')
  check((await state()).tool==='head','head proposal retains head tool for consecutive clicks')
  headProposal=[550,240,570,258]
  const nextHeadRect=await rect();await page.mouse.click(nextHeadRect.x+nextHeadRect.width*.70,nextHeadRect.y+nextHeadRect.height*.55)
  await page.waitForFunction(()=>window.ws.currentObjects.value.length===4)
  check((await state()).tool==='head','a second head is created without reselecting the tool')
  await uiButton('撤销本帧操作').click();headProposal=[500,240,520,258]
  await page.locator('.object-select').last().click()
  await uiButton('选择').click()
  await page.screenshot({path:'output/playwright/head-click-created.png'})
  await blur();await page.keyboard.press('Alt+ArrowRight')
  const adjusted=(await state()).objects.at(-1)
  check(adjusted.bbox.x>500/800*100,'generated head box can be adjusted immediately')
  await page.keyboard.press('Control+z')
  check(Math.abs((await state()).objects.at(-1).bbox.x-500/800*100)<1e-8,'undo adjustment preserves generated box')
  await uiButton('专注').click();await page.screenshot({path:'output/playwright/head-click-focus.png'});await uiButton('退出专注').click()

  await uiButton('撤销本帧操作').click();check(JSON.stringify((await state()).objects)===JSON.stringify(initial),'undo removes generated head box exactly')
  await uiButton('重做本帧操作').click();check((await state()).objects.length===3,'redo restores head box identity')
  await clickHead();await page.waitForFunction(()=>!window.ws.headBoxBusy.value)
  check((await state()).objects.length===3,'clicking the same proposed head does not create duplicates')
  check((await state()).tool==='head','duplicate head click retains head tool')
  await uiButton('撤销本帧操作').click()
  headMode='error';await clickHead();await page.getByRole('alert').filter({hasText:'测试：模型暂不可用'}).waitFor()
  check((await state()).objects.length===2,'model failure never creates a fallback rectangle')
  headMode='corrupt';await clickHead();await page.getByRole('alert').filter({hasText:'检测结果无效'}).waitFor()
  check((await state()).objects.length===2,'malformed model box is rejected')
  headMode='slow';await clickHead();await uiButton('选择').click();await page.waitForTimeout(750)
  check((await state()).objects.length===2,'switching tools discards late proposal responses')
  await clickHead();await go(1);await page.waitForTimeout(750);await go(0)
  check((await state()).objects.length===2,'changing frames discards late proposals without polluting either frame')
  await clickHead();await uiButton('审查模式').click();await page.waitForTimeout(750)
  check((await state()).objects.length===2,'leaving annotation cancels pending head proposals without background edits')
  await uiButton('人工标注').click();await idle();await go(0)
  await page.unroute('**/api/track/head-box/*');await uiButton('选择').click()
  const before=JSON.stringify(initial)
  await gesture(box.x+box.width/2,box.y+box.height/2,30,14,false)
  check(JSON.stringify((await state()).objects)===before,'drag preview does not rewrite video annotations during pointer motion')
  await page.mouse.up();const moved=(await state()).objects
  check(moved[0].bbox.x>box.x&&moved[0].source==='manual','pointer release commits the edited box once as manual')
  await uiButton('撤销本帧操作').click();check(JSON.stringify((await state()).objects)===before,'undo restores the exact pre-drag AI box and source')
  await uiButton('重做本帧操作').click();check(JSON.stringify((await state()).objects)===JSON.stringify(moved),'redo restores the committed geometry')
  await uiButton('撤销本帧操作').click()
  const handleRect=await rect()
  await gesture(box.x-3/handleRect.width*100,box.y-3/handleRect.height*100,-8,-6)
  const resized=(await state()).objects[0].bbox
  check(resized.width>box.width&&resized.height>box.height,'corner handles resize from just outside the selected outline')
  await uiButton('撤销本帧操作').click();check(JSON.stringify((await state()).objects)===before,'corner resize undo preserves original subpixel coordinates')
  await gesture(box.x+box.width/2,box.y+box.height/2,22,20,false);await page.keyboard.press('Escape');await page.mouse.up()
  check(JSON.stringify((await state()).objects)===before,'Escape cancels a gesture without a partial commit')
  await gesture(box.x+box.width/2,box.y+box.height/2,0,0)
  check(JSON.stringify((await state()).objects)===before,'selection alone does not convert AI objects to manual')
  await blur();await page.keyboard.press('Alt+ArrowRight');let nudged=(await state()).objects[0]
  check(Math.abs(nudged.bbox.x-box.x-100/800)<1e-8,'Alt arrow moves exactly one source-image pixel')
  await page.keyboard.press('Control+z');check(JSON.stringify((await state()).objects)===before,'keyboard undo restores pixel nudge')
  await page.keyboard.press('v');check((await state()).tool==='select','V shortcut matches the visible select tool')
  await page.keyboard.press('Delete');check((await state()).objects.length===1,'Delete removes only the selected current-frame object')
  await page.keyboard.press('Control+z');await page.evaluate(()=>window.ws.loadTrackingResult(window.ws.currentMediaId.value,true))
  check((await state()).objects.length===2,'undo deletion restores AI object even after tracking data reload')
  await page.locator('.object-select').first().click();await blur();await page.keyboard.press('Delete')
  await go(1);await page.locator('.object-select').first().click();await blur();await page.keyboard.press('Delete')
  await go(0);await page.keyboard.press('Control+z')
  await page.evaluate(()=>window.ws.loadTrackingResult(window.ws.currentMediaId.value,true));await go(1)
  check((await state()).objects.length===1,'undo in another frame never resurrects this frame deleted AI object')
  await page.keyboard.press('Control+z');await page.evaluate(()=>window.ws.loadTrackingResult(window.ws.currentMediaId.value,true))
  check((await state()).objects.length===2,'each frame retains its own independent undo history')
  await go(0)
  await page.keyboard.press('Shift+ArrowRight');await idle();check((await state()).frame===10,'Shift arrow advances exactly ten frames');await go(0)
  await page.keyboard.press('h');check(await page.locator('.annotation-overlay').isHidden(),'H temporarily hides overlays while retaining annotation data');await page.keyboard.press('h')
  await uiButton('显示对象编号').click();check(await page.locator('.annotation-label').count()>0,'object id labels can be shown on demand');await uiButton('显示对象编号').click();check(await page.locator('.annotation-label').count()===0,'id labels hide independently of boxes')
  const baselineRequests=requests.filter(u=>u.includes('/track/result/')).length
  check(await page.getByRole('button',{name:'复制上一帧',exact:true}).count()===0,'previous frame copy tool is removed')
  await go(59);await blur();await page.keyboard.press('c')
  check((await state()).objects.length===0,'C no longer copies previous frame objects')
  await go(0);await go(1);await go(0)
  check(requests.filter(u=>u.includes('/track/result/')).length===baselineRequests,'frame navigation makes no repeated full tracking-result requests')
  const frame0Requests=requests.filter(u=>u.endsWith('/track/frame/ux-video/0')).length
  await go(1);await go(0)
  check(requests.filter(u=>u.endsWith('/track/frame/ux-video/0')).length===frame0Requests,'revisiting a cached frame requires no frame download')
  await blur();await page.keyboard.press('b')
  const r=await rect();await gesture(60,35,r.width*1/100,r.height*1.2/100)
  check((await state()).objects.length===3,'small targets below the old 2-percent threshold can be boxed')
  await uiButton('撤销本帧操作').click()
  await uiButton('放大画面').click();await uiButton('放大画面').click()
  const zoom=(await state()).zoom;check(zoom>1,'zoom increases canvas size without changing annotations')
  await blur();await page.keyboard.down('Space');const panRect=await rect();await page.mouse.move(panRect.x+panRect.width/2,panRect.y+panRect.height/2);await page.mouse.down();await page.mouse.move(panRect.x+panRect.width/2-80,panRect.y+panRect.height/2-20,{steps:8});await page.mouse.up();await page.keyboard.up('Space')
  check((await state()).objects.length===2,'space-drag pans without creating or moving labels')
  await uiButton('适应画面').click();await uiButton('局部放大镜').click();const rr=await rect();await page.mouse.move(rr.x+rr.width*.5,rr.y+rr.height*.5);await page.waitForTimeout(100)
  check(await page.locator('.loupe canvas').evaluate(c=>c.getContext('2d').getImageData(100,60,1,1).data[3])===255,'magnifier draws real source pixels')
  await uiButton('画面增强').click();await uiButton('暗场').click();await page.getByRole('checkbox',{name:'反相'}).check()
  check((await page.locator('.exact-media').getAttribute('style')).includes('invert(1)'),'display enhancements support dark backgrounds and inversion')
  check((await state()).objects.length===2,'display adjustments do not mutate annotation data')
  await uiButton('原图').click();await uiButton('画面增强').click();await uiButton('局部放大镜').click()
  await uiButton('快捷键').click();await page.keyboard.press('b');await page.keyboard.press('c');check((await state()).objects.length===2,'help dialog isolates annotation keyboard commands');await page.keyboard.press('Escape')
  await page.getByTestId('toggle-workbench-library').click();await page.getByRole('textbox',{name:'查找素材'}).fill('typing v b c');await page.getByRole('textbox',{name:'查找素材'}).press('Control+a');check((await state()).objects.length===2,'typing and text shortcuts cannot mutate annotations');await page.getByRole('textbox',{name:'查找素材'}).fill('');await page.getByTestId('toggle-workbench-library').click();await blur()
  await page.screenshot({path:'output/playwright/ux-annotation-video.png'})
  await uiButton('切换深色主题').click();check(await page.locator('html').getAttribute('data-theme')==='dark','theme switches without leaving the task');await page.screenshot({path:'output/playwright/ux-annotation-dark.png'})
  await uiButton('切换浅色主题').click()
  // Force an uncached frame to fail, then verify precise retry rather than returning the previous frame.
  await page.route('**/api/track/frame/ux-video/45',r=>r.fulfill({status:503,json:{message:'Injected frame read failure'}}))
  await page.getByRole('spinbutton',{name:'跳转帧号'}).fill('46');await page.getByRole('spinbutton',{name:'跳转帧号'}).press('Enter');await page.locator('.canvas-error').waitFor()
  check((await state()).frame===0&&(await state()).busy,'failed frame read preserves the old frame and blocks editing')
  await page.unroute('**/api/track/frame/ux-video/45');await uiButton('重试读取').click();await idle();check((await state()).frame===45,'retry loads the actual failed target frame')
  // Concurrent requests complete out of order.
  await page.route('**/api/track/frame/ux-video/30',async route=>{await new Promise(r=>setTimeout(r,300));await route.continue()})
  await page.evaluate(()=>Promise.all([window.ws.seekVideo(30/15),window.ws.seekVideo(35/15)]));await idle()
  check((await state()).frame===35,'late frame responses cannot replace a newer requested frame');await page.unroute('**/api/track/frame/ux-video/30')
  await page.route('**/api/track/workspace/ux-video',route=>route.request().method()==='PUT'?route.fulfill({status:503,json:{message:'Injected save failure'}}):route.continue())
  await page.locator('.object-select').first().click();await blur();await page.keyboard.press('Alt+ArrowRight');await page.waitForSelector('.error-banner')
  check((await state()).save==='error','autosave failure is visible instead of claiming data was saved')
  await page.unroute('**/api/track/workspace/ux-video');await uiButton('重试保存').click();await page.waitForFunction(()=>window.ws.saveState.value==='saved')
  await page.locator('.error-banner').first().waitFor({state:'detached'});check(!(await page.locator('.error-banner').count()),'retry persists data and clears the saving error')
  const persisted=(await state()).objects
  await page.reload();await page.evaluate(async()=>{window.ws=(await import(performance.getEntriesByType('resource').find(r=>r.name.includes('/src/stores/workspace.ts')).name)).useWorkspace()})
  // Login validation precedes workspace mounting. Idle alone can be true
  // before restoration starts; require the original frame to actually return.
  await page.waitForFunction(()=>window.ws.currentFrame.value===35&&!window.ws.workspaceRestoring.value&&!window.ws.exactFrameLoading.value)
  check((await state()).frame===35,'refresh resumes the last selected video and exact frame');await selectMedia('ux-video.avi');await go(35)
  // Object identity is independent of insertion order. Compare every original
  // field by stable ID; a manual override can change Map insertion order.
  const byId=objects=>[...objects].sort((a,b)=>(a.objectId-b.objectId)||a.id.localeCompare(b.id))
  assert.deepEqual(byId((await state()).objects),byId(persisted))
  check(true,'manual geometry and identity survive reload from the real workspace endpoint')
  await selectMedia('ux-portrait.avi');check(Math.abs((await rect()).width/(await rect()).height-450/800)<.01,'portrait media uses the correct source aspect ratio')
  await page.setViewportSize({width:1180,height:760});await page.waitForTimeout(150)
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'compact desktop layout has no horizontal document overflow')
  await page.screenshot({path:'output/playwright/ux-portrait-compact.png'})
  await page.setViewportSize({width:1440,height:900});await uiButton('标注记录').click();await page.locator('.records-page').waitFor()
  check(await page.locator('.workflow-nav').getByRole('button',{name:'人工标注',exact:true}).count()===1&&!(await page.locator('.workflow-nav').innerText()).includes('标注记录'),'records are separate from the three workflow stages')
  check((await page.locator('.records-page').innerText()).includes('不影响标注与审查流程'),'record page explains its lookup purpose')
  await page.screenshot({path:'output/playwright/ux-records.png'})
  check(errors.length===0,'annotation, recovery, theme and navigation have no uncaught errors')
  console.log('SUCCESS',checks,'checks')
}finally{fs.writeFileSync('output/playwright/ux-browser-console.json',JSON.stringify({errors,logs},null,2));await page.screenshot({path:'output/playwright/ux-last.png'});await browser.close()}
