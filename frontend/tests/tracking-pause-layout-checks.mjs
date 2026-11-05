// Shared by the fixed tracking-focus checks. All pause/edit/feedback state is
// created by that suite through the real UI; only model predictions are simulated.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

export async function trackingPauseLayoutChecks(page, { poll, ready, out, beforePause, getWorkspace }) {
  const checks=[], evidence=[], measurements=[]
  let initially
  const button=name=>page.getByRole('button',{name,exact:true})
  const feedback=page.getByTestId('tracking-feedback')
  const details=page.getByTestId('tracking-diagnostics')
  const toggle=page.getByTestId('pause-details-toggle')
  const panel=page.getByTestId('pause-details-panel')
  const chip=page.getByRole('button',{name:/^选择暂停对象 1 /})
  const check=(ok,message)=>{assert(ok,message);checks.push(message);console.log('PASS pause-layout',checks.length,message)}
  const selected=()=>page.locator('.annotation-overlay g.selected text').textContent()
  const focused=async()=>await page.locator('.shell-annotate.is-focus').count()===1
  async function setFocus(value){if(value!==await focused())await (value?button('专注'):page.getByRole('button',{name:/^退出专注/})).click();await ready()}
  async function setDetails(value){if(((await details.getAttribute('open'))!==null)!==value){await toggle.click();await poll(async()=>((await details.getAttribute('open'))!==null)===value,'pause details toggled')}await page.waitForTimeout(80)}
  async function geometry(){return page.evaluate(()=>{
    const box=selector=>{const e=document.querySelector(selector);if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}}
    const canvas=document.querySelector('.canvas-scroll'),stage=document.querySelector('.annotation-stage')
    return {canvas:box('.annotation-canvas'),feedback:box('[data-testid=tracking-feedback]'),details:box('[data-testid=pause-details-panel]'),stage:box('.annotation-stage'),selection:box('.annotation-overlay g.selected .annotation-box'),
      scroll:{left:canvas.scrollLeft,top:canvas.scrollTop,width:canvas.clientWidth,height:canvas.clientHeight,scrollWidth:canvas.scrollWidth,scrollHeight:canvas.scrollHeight},selected:document.querySelector('.annotation-overlay g.selected text')?.textContent,
      sourceScale:stage.getBoundingClientRect().width,stageMargins:{inlineLeft:stage.style.marginLeft,inlineTop:stage.style.marginTop,computedLeft:getComputedStyle(stage).marginLeft,computedTop:getComputedStyle(stage).marginTop},compatMode:document.compatMode,
      overflow:Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)>innerWidth+1}
  })}
  const intersects=(a,b)=>a.x<b.right-1&&a.right>b.x+1&&a.y<b.bottom-1&&a.bottom>b.y+1
  async function reachable(locator){await locator.scrollIntoViewIfNeeded();return locator.evaluate(e=>{const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>0&&r.height>0&&e.contains(hit)})}
  const labelsFit=async()=>Promise.all(['confirm-tracking-object-normal','confirm-tracking-corrected'].map(id=>page.getByTestId(id).evaluate(e=>e.scrollWidth<=e.clientWidth+1&&e.scrollHeight<=e.clientHeight+1))).then(values=>values.every(Boolean))
  async function screenshot(label){await page.screenshot({path:path.join(out,label+'.png')});evidence.push(label)}
  try {
    initially=await geometry()
    check(initially.canvas.height>=beforePause.height-4,'ordinary pause preserves the image height instead of inserting a panel below it')
    check(await feedback.getAttribute('data-presentation')==='inspector','ordinary pause actions live in the inspector')
    check(await feedback.evaluate(e=>!!e.closest('[data-workbench-region=inspector]')),'ordinary pause actions use the actual right-hand operation region')
    for(const [width,height] of [[1366,768],[1180,760]])for(const theme of ['light','dark'])for(const focus of [false,true]){
      await setDetails(false);await setFocus(focus);await page.setViewportSize({width,height})
      await page.evaluate(t=>{document.documentElement.dataset.theme=t},theme);await page.waitForTimeout(120)
      const label=`pause-layout-${focus?'focus':'normal'}-${width}-${theme}`
      const closed=await geometry()
      const observation={label,closed};measurements.push(observation)
      check(closed.canvas.height>=300,label+': image retains at least 300 vertical pixels')
      check(!closed.overflow,label+': no page horizontal overflow')
      if(focus)check(closed.feedback.height<=96,label+': compact pause strip fits within 96 pixels')
      check(await reachable(chip)&&await reachable(page.getByTestId('confirm-tracking-corrected')),label+': object selection and correction remain reachable')
      check(await labelsFit(),label+': both confirmation labels fit their own buttons without clipping or overlap')
      await screenshot(label)
      await setDetails(true);const opened=await geometry()
      observation.opened=opened
      check(Math.abs(opened.canvas.height-closed.canvas.height)<=2,label+': opening evidence does not reduce image height')
      check(!intersects(opened.canvas,opened.details),label+': evidence occupies a separate region rather than overlaying the image')
      check(await panel.locator('.pause-details-content').evaluate(e=>e.clientHeight>70&&e.scrollHeight>e.clientHeight),label+': long evidence has its own usable scrolling area')
      check(await panel.getByTestId('pause-trigger-metric').first().locator('dd').first().evaluate(e=>{const r=e.getBoundingClientRect(),view=e.closest('.pause-details-content').getBoundingClientRect();return r.top>=view.top&&r.bottom<=view.bottom}),label+': the current trigger value is readable on opening without scrolling through an object heading')
      await panel.locator('.pause-details-content').evaluate(e=>e.scrollTop=e.scrollHeight)
      check(Math.abs((await geometry()).canvas.height-closed.canvas.height)<=2,label+': evidence scrolling does not move or resize the canvas')
      await panel.locator('.pause-details-content').evaluate(e=>e.scrollTop=0)
      await screenshot(label+'-details');await setDetails(false)
    }

    // Display enhancements may use a row, but a pause/details panel must not take
    // another block of image height, or conceal the selected rectangle.
    await page.setViewportSize({width:1366,height:768});await setFocus(true)
    await button('画面增强').click();await page.waitForTimeout(100)
    const enhanced=await geometry();check(enhanced.canvas.height>=300,'enhanced focus retains a large editable image while paused')
    await setDetails(true)
    check(Math.abs((await geometry()).canvas.height-enhanced.canvas.height)<=2,'enhanced focus evidence does not steal image height')
    await screenshot('pause-layout-focus-enhanced-details');await setDetails(false);await button('画面增强').click()

    // Use an actual user-controlled zoom and a target well inside the visible
    // image. Merely checking the zoom label misses a refit of the source stage.
    for(let i=0;i<8;i++)await button('放大画面').click()
    await chip.click();await page.waitForTimeout(100)
    const zoomed=await geometry(),identity=await selected()
    await setDetails(true);const detailsZoomed=await geometry()
    check(detailsZoomed.selected===identity&&Math.abs(detailsZoomed.sourceScale-zoomed.sourceScale)<=1,'opening evidence preserves selected identity and actual rendered zoom')
    await setDetails(false);const closedZoomed=await geometry()
    check(closedZoomed.selected===identity&&Math.abs(closedZoomed.sourceScale-zoomed.sourceScale)<=1,'closing evidence preserves selected identity and rendered zoom')
    check(Math.abs(closedZoomed.scroll.left-zoomed.scroll.left)<=1&&Math.abs(closedZoomed.scroll.top-zoomed.scroll.top)<=1,'evidence open/close restores the same viewport when the target remains visible')
    await setFocus(false);const normalZoomed=await geometry()
    check(normalZoomed.selected===identity&&Math.abs(normalZoomed.sourceScale-zoomed.sourceScale)<=1,'leaving focus during pause preserves selected identity and rendered zoom')
    await setFocus(true);const focusedAgain=await geometry()
    check(focusedAgain.selected===identity&&Math.abs(focusedAgain.sourceScale-zoomed.sourceScale)<=1,'returning to focus preserves selected identity and rendered zoom')
    // Execute a user's next action in the next browser task, before the dock's
    // two-animation-frame settling work. Late view restoration must not undo it.
    await toggle.evaluate(element=>{element.click();setTimeout(()=>document.querySelector('.shell-zoom button[title]')?.click(),0)})
    await poll(async()=>await page.locator('.shell-zoom button[title]').innerText()==='100%','explicit fit during evidence resize applied')
    await page.waitForTimeout(120)
    check(await page.locator('.shell-zoom button[title]').innerText()==='100%'&&(await geometry()).sourceScale<zoomed.sourceScale,'a rapid explicit fit is not replaced by stale pre-dock zoom')
    await setDetails(false)
    for(let i=0;i<8;i++)await button('放大画面').click()
    await chip.click()
    await toggle.evaluate(element=>{element.click();setTimeout(()=>document.querySelectorAll('.pause-object-tabs button')[1]?.click(),0)})
    await poll(async()=>(await selected())?.endsWith('two 2'),'new target selected during evidence opening')
    await page.waitForTimeout(120)
    check(await page.locator('.annotation-overlay g.selected .annotation-box').evaluate(e=>{const r=e.getBoundingClientRect(),view=document.querySelector('.canvas-scroll').getBoundingClientRect();return r.x>=view.x&&r.right<=view.right&&r.y>=view.y&&r.bottom<=view.bottom}),'a rapid target change keeps its own localization instead of restoring the previous target viewport')
    const sourceBeforeCorner=JSON.stringify((await getWorkspace()).manualAnnotations)
    let placedAtCorner=false
    for(let i=0;i<12&&!placedAtCorner;i++){
      placedAtCorner=await page.locator('.canvas-scroll').evaluate(scroller=>{
        const target=document.querySelector('.annotation-overlay g.selected .annotation-box').getBoundingClientRect(),view=scroller.getBoundingClientRect()
        const left=scroller.scrollLeft+target.right-view.right+16,top=scroller.scrollTop+target.bottom-view.bottom+16
        if(left<0||top<0||left>scroller.scrollWidth-scroller.clientWidth||top>scroller.scrollHeight-scroller.clientHeight)return false
        scroller.scrollTo(left,top);return true
      })
      if(!placedAtCorner)await button('放大画面').click()
    }
    check(placedAtCorner,'a real selected rectangle can be placed near the lower-right image edge')
    // Zoom used to float at the lower right; it now sits in the bottom bar.
    // A target near that corner must remain reachable by the same Space-pan
    // gesture; moving the view must not edit the box or require leaving focus mode.
    const cornerView=await geometry(),zoomBox=await page.locator('.shell-zoom').boundingBox()
    check(zoomBox&&zoomBox.x>=0&&zoomBox.y>=0&&zoomBox.x+zoomBox.width<=1366&&zoomBox.y+zoomBox.height<=768,'floating zoom controls remain visible beside the editable viewport')
    const panX=cornerView.canvas.x+cornerView.canvas.width*.5,panY=cornerView.canvas.y+cornerView.canvas.height*.5
    await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur())
    await page.keyboard.down('Space');await page.mouse.move(panX,panY);await page.mouse.down();await page.mouse.move(panX-150,panY-90,{steps:6});await page.mouse.up();await page.keyboard.up('Space')
    // Controls no longer float over the image. At this zoom the box can be taller
    // than the image area, so require the lower handles near the old zoom corner
    // to be visible, and every handle inside the image area to be editable.
    const handleHits=await page.locator('.annotation-overlay g.selected .box-handle').evaluateAll(handles=>{const view=document.querySelector('.canvas-scroll').getBoundingClientRect();return handles.map(handle=>{const r=handle.getBoundingClientRect(),cx=r.x+r.width/2,cy=r.y+r.height/2,hit=document.elementFromPoint(cx,cy);return {y:cy,inView:cx>=view.left&&cx<=view.right&&cy>=view.top&&cy<=view.bottom,ok:!!hit?.matches('[data-testid="annotation-hit"]')}})})
    const lowest=Math.max(...handleHits.map(h=>h.y)),lower=handleHits.filter(h=>h.y>=lowest-1)
    check(handleHits.length===4&&lower.length===2&&lower.every(h=>h.inView&&h.ok)&&handleHits.filter(h=>h.inView).every(h=>h.ok),'Space-pan reveals the lower-right target handles on the real editing surface rather than floating controls '+JSON.stringify(handleHits))
    check(JSON.stringify((await getWorkspace()).manualAnnotations)===sourceBeforeCorner,'moving the viewport around a paused target does not change saved annotation coordinates')
    await screenshot('pause-layout-right-bottom-handles')
    await setDetails(false);await chip.click()
    await button('适应画面').click()

    // The subtle centering boundary: the manually enlarged source fits in the
    // full viewport but becomes scrollable when the dock opens. Auto margins
    // must not shift a perfectly visible target even though the zoom is stable.
    const dockWidth=measurements.find(item=>item.label==='pause-layout-focus-1366-light').opened.details.width
    let threshold=await geometry()
    for(let i=0;i<12&&threshold.stage.width<threshold.canvas.width-dockWidth+16;i++){
      await button('放大画面').click();threshold=await geometry()
    }
    check(threshold.stage.width<threshold.canvas.width-8&&threshold.stage.width>threshold.canvas.width-dockWidth,'test reaches the actual centered-to-scrollable image boundary')
    await chip.click();await page.waitForTimeout(80);const centered=await geometry()
    await setDetails(true);const docked=await geometry()
    measurements.push({label:'centered-to-scrollable-boundary',centered,docked})
    const relative=(state,key)=>({x:state[key].x-state.canvas.x,y:state[key].y-state.canvas.y})
    const centeredStage=relative(centered,'stage'),dockedStage=relative(docked,'stage')
    check(Math.abs(centeredStage.x-dockedStage.x)<=1&&Math.abs(centeredStage.y-dockedStage.y)<=1,'opening evidence across the centering boundary keeps the image anchor instead of shifting by an auto margin')
    check(docked.selected===centered.selected&&Math.abs(docked.stage.width-centered.stage.width)<=1,'centering-boundary evidence preserves identity and actual source scale')
    const savedScroll=docked.scroll
    await page.locator('.canvas-scroll').evaluate(e=>e.scrollTo(0,0));const leading=await geometry()
    check(leading.stage.x>=leading.canvas.x-1&&leading.stage.y>=leading.canvas.y-1,'the image leading edges remain reachable after anchor compensation')
    await page.locator('.canvas-scroll').evaluate(e=>e.scrollTo(e.scrollWidth,e.scrollHeight));const trailing=await geometry()
    check(trailing.stage.right<=trailing.canvas.right+1&&trailing.stage.bottom<=trailing.canvas.bottom+1,'the image trailing edges remain reachable after anchor compensation')
    await page.locator('.canvas-scroll').evaluate((e,position)=>e.scrollTo(position.left,position.top),savedScroll)
    await setDetails(false);const undocked=await geometry(),undockedStage=relative(undocked,'stage')
    check(Math.abs(centeredStage.x-undockedStage.x)<=1&&Math.abs(centeredStage.y-undockedStage.y)<=1,'closing evidence restores the original image anchor at the centering boundary')
    await button('适应画面').click()
    await setDetails(true);await panel.getByRole('button',{name:'收起检查依据',exact:true}).focus();await page.keyboard.press('Escape')
    await poll(async()=>await details.getAttribute('open')===null,'evidence Escape closes only the evidence dock')
    check(await focused()&&await toggle.evaluate(e=>e===document.activeElement),'Escape from evidence returns focus to its toggle without leaving focused annotation')

    for(const [width,height] of [[760,900],[760,480]])for(const theme of ['light','dark'])for(const focus of [false,true]){
      await setDetails(false);await setFocus(focus);await page.setViewportSize({width,height});await page.waitForTimeout(120)
      await page.evaluate(t=>{document.documentElement.dataset.theme=t},theme)
      const label=`pause-layout-${focus?'focus':'normal'}-${width}x${height}-${theme}`
      const closed=await geometry()
      const observation={label,closed};measurements.push(observation)
      check(closed.canvas.height>=180,label+': a scrollable small window still keeps at least 180 pixels for the image')
      check(!closed.overflow,label+': responsive pause adds no page horizontal overflow')
      for(const locator of [chip,page.getByTestId('confirm-tracking-object-normal'),page.getByTestId('confirm-tracking-corrected'),page.getByRole('spinbutton',{name:'跳转帧号'})])
        check(await reachable(locator),label+': each essential operation can be scrolled into an unobstructed hit area')
      check(await labelsFit(),label+': confirmation text stays readable inside its own buttons')
      await setDetails(true);const opened=await geometry()
      observation.opened=opened
      check(Math.abs(opened.canvas.height-closed.canvas.height)<=2&&!intersects(opened.canvas,opened.details),label+': evidence neither squeezes nor covers the image')
      check(await reachable(panel.getByRole('button',{name:'收起检查依据',exact:true})),label+': evidence has a reachable close action')
      await screenshot(label+'-details');await setDetails(false)
      await button('画面增强').click();const enhancedSmall=await geometry()
      check(enhancedSmall.canvas.height>=180,label+': enhancement and pause preserve editable image height')
      check(Math.abs(enhancedSmall.canvas.height-closed.canvas.height)<=2,label+': enhancement overlay does not resize the editable image')
      check(await reachable(button('画面增强')),label+': enhancement does not cover its own close toggle when the toolbar wraps')
      check(await reachable(page.getByRole('button',{name:focus?/^退出专注/:/^专注$/})),label+': enhancement leaves focus and exit controls hit-testable')
      if(focus&&height<=480){
        await setDetails(true)
        const popover=page.locator('.annot-enhance')
        // The popover is a row above the image: it either shows everything or scrolls
        // inside itself, and never grows past its cap at the image's expense.
        const pop=await popover.evaluate(element=>({client:element.clientHeight,scroll:element.scrollHeight,height:element.getBoundingClientRect().height,overflow:getComputedStyle(element).overflowY,display:getComputedStyle(element).display}))
        check(pop.client>30&&pop.height<=161&&(pop.scroll<=pop.client+1||pop.overflow==='auto'),label+': the short narrow enhancement popover stays bounded and scrolls internally when needed '+JSON.stringify(pop))
        await popover.locator('input[type=checkbox]').scrollIntoViewIfNeeded()
        check(await popover.evaluate(element=>element.scrollHeight<=element.clientHeight+1||element.scrollTop>0)&&await reachable(popover.locator('input[type=checkbox]')),label+': the last enhancement control can be reached, scrolling only if the popover overflows')
        await setDetails(false)
      }
      await screenshot(label+'-enhanced');await button('画面增强').click()
      observation.enhanced=enhancedSmall
    }
    await page.setViewportSize({width:1366,height:768});await setDetails(false);await setFocus(false)
    await page.evaluate(()=>{document.documentElement.dataset.theme='light';scrollTo(0,0)})
    await button('适应画面').click();await chip.click();await ready()
    return {checks,evidence,measurements}
  } finally {
    fs.writeFileSync(path.join(out,'pause-layout-measurements.json'),JSON.stringify({checks,evidence,beforePause,initialPause:initially,measurements,model:'simulated predictions; real pause/edit API and original encoded frames'},null,2))
  }
}
