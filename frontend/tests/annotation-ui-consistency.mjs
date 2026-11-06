import assert from 'node:assert/strict'
import fs from 'node:fs'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
const f=JSON.parse(fs.readFileSync('work/confirmation-browser-fixture.json'))
const b=await chromium.launch({channel:'chrome',headless:true}),p=await b.newPage({reducedMotion:'reduce',viewport:{width:1440,height:900}})
const errors=[];p.on('pageerror',e=>errors.push(String(e)));let checks=0
const check=(ok,msg)=>{assert(ok,msg);console.log('PASS',++checks,msg)}
const origin=process.env.CONFIRMATION_ORIGIN||'http://127.0.0.1:5373'
try{
 await p.addInitScript(f=>{localStorage.setItem('rare-sperm-token',f.token);localStorage.setItem('rare-sperm-auth',JSON.stringify({id:'3',name:'UX tester'}));localStorage.setItem('annotation-theme','dark')},f)
 await p.route('**/api/track/media',async route=>{const res=await route.fetch(),d=await res.json();d.items=d.items.filter(m=>m.mediaId.startsWith('ux-'));await route.fulfill({response:res,json:d})})
 await p.goto(origin+'/annotate');await p.locator('.annotation-page').waitFor()
 check(await p.locator('html').getAttribute('data-theme')==='dark','saved dark preference applies before the workbench appears')
 const colors=await p.locator('.asset-actions .btn-secondary').evaluate(e=>({bg:getComputedStyle(e).backgroundColor,fg:getComputedStyle(e).color}))
 check(colors.bg==='rgb(25, 37, 50)'&&colors.fg==='rgb(224, 233, 243)','dark-theme secondary controls have matching readable surface and text')
 await p.screenshot({path:'output/playwright/ux-annotation-dark-stable.png',animations:'disabled'})
 await p.getByRole('button',{name:'切换浅色主题'}).click();await p.waitForTimeout(180)
 await p.getByRole('button',{name:'专注',exact:true}).click()
 check(await p.locator('.annotation-inspector').isHidden()&&await p.locator('.asset-panel').isHidden(),'focus mode gives both sidebar columns to the canvas')
 await p.keyboard.press('f');check(await p.locator('.asset-panel').isVisible(),'F restores the full editing layout')
 await p.getByRole('button',{name:'快捷键',exact:true}).click();await p.keyboard.press('Tab')
 check(await p.getByRole('dialog').evaluate(e=>e.contains(document.activeElement)),'annotation help traps keyboard focus')
 await p.keyboard.press('Escape');check(await p.getByRole('button',{name:'快捷键',exact:true}).evaluate(e=>document.activeElement===e),'help close restores focus to its launcher')
 await p.route('**/api/annotation/projects/default/results',r=>r.fulfill({json:{items:Array.from({length:102},(_,i)=>({id:i,user_id:3,media_id:'unique-'+i,media_name:i<2?'same-name.avi':'sample-'+i+'.avi',media_type:'video',frame_index:0,object_name:'sperm '+i,source:'manual',username:'UX tester',created_at:'2026-09-27T09:00:00Z'})),total:102}}))
 await p.getByRole('button',{name:'标注记录',exact:true}).click();await p.waitForFunction(()=>document.querySelectorAll('.records-table tbody tr').length===50)
 check(await p.locator('.records-table tbody tr').filter({hasText:'same-name.avi'}).count()===2,'records keep two separate media with the same filename distinct')
 await p.getByRole('button',{name:'下一页',exact:true}).click();check((await p.locator('.records-pagination').innerText()).includes('第 2 / 3 页'),'large record lists are paginated')
 await p.getByRole('textbox',{name:'搜索标注记录'}).fill('sperm 101');check(await p.locator('.records-table tbody tr').count()===1,'record search filters objects and resets pagination')
 check((await p.locator('.records-table tbody').innerText()).includes('sample-101.avi'),'search returns the intended annotation record')
 await p.screenshot({path:'output/playwright/ux-records-search.png',animations:'disabled'})
 await p.getByRole('button',{name:'审查模式',exact:true}).click();await p.locator('.review-page').waitFor();await p.screenshot({path:'output/playwright/ux-review-light.png',animations:'disabled'})
 check(await p.locator('.review-page').evaluate(e=>getComputedStyle(e).backgroundColor)==='rgb(237, 242, 246)','review uses the shared light surface tokens')
 await p.getByRole('button',{name:'对比确认',exact:true}).click();await p.locator('.confirmation-page[data-busy="false"]').waitFor();await p.screenshot({path:'output/playwright/ux-confirm-light.png',animations:'disabled'})
 check(await p.locator('.confirmation-page').evaluate(e=>getComputedStyle(e).backgroundColor)==='rgb(237, 242, 246)','confirmation uses the same light surface tokens')
 await p.getByRole('button',{name:'退出',exact:true}).click();await p.getByRole('heading',{name:'登录系统'}).waitFor();await p.screenshot({path:'output/playwright/ux-login-light.png',animations:'disabled'})
 check(await p.locator('.login-card').evaluate(e=>getComputedStyle(e).backgroundColor)==='rgb(255, 255, 255)','login follows the shared appearance')
 check(errors.length===0,'cross-page appearance and records checks have no uncaught errors')
 console.log('SUCCESS',checks,'checks')
}finally{await b.close()}
