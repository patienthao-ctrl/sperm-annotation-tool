// Works against production bundles too. No store imports or prepared annotation state.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { useLegacyBrowserAPIs } from './legacy-browser-profile.mjs'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const origin = process.env.CRITICAL_ORIGIN || 'http://127.0.0.1:5373'
assert(['localhost', '127.0.0.1'].includes(new URL(origin).hostname), 'Local isolated server required')
const video = path.resolve(process.env.CRITICAL_VIDEO || 'work/critical-upload.avi')
assert(video.startsWith(path.resolve('work')+path.sep), 'Disposable input required')
const modelMode = process.env.CRITICAL_AI_MODE || 'disabled'
const browser = await chromium.launch({ channel: process.env.CHROME_CHANNEL || 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1366, height: 768 }, acceptDownloads: true })
page.setDefaultTimeout(15000)
if (process.env.LEGACY_BROWSER === '1') await useLegacyBrowserAPIs(page)
const checks = [], errors = [], requests = []
const check = (value, message) => { assert(value, message); checks.push(message); console.log('PASS', checks.length, message) }
page.on('pageerror', e => errors.push(String(e)))
page.on('response', r => { if (r.status() >= 400) requests.push({ method:r.request().method(), path:new URL(r.url()).pathname, status:r.status() }) })
page.on('dialog', d => d.accept())
const button = name => page.getByRole('button', { name, exact:true })
let token, mid, sid
const api = async (url, method='GET', body) => {
  const response = await page.request.fetch(origin+'/api'+url, { method, data:body, headers:{ Authorization:'Bearer '+token, 'X-Review-Contract':'2', 'Idempotency-Key':crypto.randomUUID() } })
  assert(response.ok(), `${method} ${url}: ${response.status()} ${await response.text()}`)
  return response.json()
}
const poll = async (fn, description) => {
  for (let attempt=0; attempt<100; attempt++) { if (await fn()) return; await page.waitForTimeout(100) }
  throw new Error('Timed out: '+description)
}
const workspace = () => api('/track/workspace/'+mid)
const ready = async () => {
  await poll(async () => await page.getByTestId('annotation-hit').isVisible() && !(await page.getByTestId('annotation-hit').getAttribute('class')).includes('locked'), 'annotation canvas is editable')
  // A cleared notice fades out briefly; a failure that really remains still fails after this wait.
  await page.getByTestId('workspace-save-error').waitFor({state:'detached',timeout:1000}).catch(()=>{});
  assert.equal(await page.getByTestId('workspace-save-error').count(), 0, 'Unexpected workspace error')
}
const draw = async () => {
  await ready(); await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur()); await page.keyboard.press('b')
  const box = await page.getByTestId('annotation-hit').boundingBox(); assert(box)
  await page.mouse.move(box.x+box.width*.35, box.y+box.height*.35); await page.mouse.down()
  await page.mouse.move(box.x+box.width*.45, box.y+box.height*.46, {steps:6}); await page.mouse.up()
  await poll(async () => (await workspace()).manualAnnotations?.length > 0, 'manual box durably saved')
}
const upload = async () => {
  // Wait for the user-visible import control after deletion/restore settles.
  await ready();await poll(async()=>await button('导入视频').isEnabled(),'import control is ready')
  const response = page.waitForResponse(r => r.url().endsWith('/api/track/upload') && r.request().method()==='POST')
  await page.locator('input[type=file][accept="video/*"]').setInputFiles(video)
  const r = await response; assert.equal(r.status(), 201, await r.text()); mid = (await r.json()).mediaId
  await ready()
}
const reload = async () => { await page.reload(); await ready() }
const jump = async n => {
  const input = page.getByRole('spinbutton', {name:'跳转帧号'})
  await input.fill(String(n)); await input.press('Enter'); await ready()
  assert.equal(await input.inputValue(), String(n))
}
try {
  const username = 'critical-'+crypto.randomUUID(), password = crypto.randomUUID()
  const registered = await page.request.post(origin+'/api/auth/register', {data:{username,password}})
  assert.equal(registered.status(),201); token=(await registered.json()).token
  await page.goto(origin+'/login')
  await page.getByPlaceholder('请输入账号').fill(username); await page.getByPlaceholder('请输入密码').fill(password)
  await button('登录').click(); await page.getByTestId('annotation-page').waitFor(); await ready()
  check(await button('导入视频').isEnabled(), 'real login with a local sample permits the first upload')
  await upload()
  check((await api('/track/result/'+mid)).state==='not_generated', 'fresh upload has explicit untracked state and editable canvas')
  await draw(); const manual=(await workspace()).manualAnnotations
  await reload()
  check(JSON.stringify((await workspace()).manualAnnotations)===JSON.stringify(manual), 'manual-only work survives refresh before first AI run')

  // Pair every blocking fault with a successful retry and another real edit.
  for (const fault of ['server', 'offline', 'invalid-json', 'missing-media']) {
    const matcher='**/api/track/result/'+mid+'*'
    await page.route(matcher, route => fault==='offline' ? route.abort('failed') : route.fulfill(fault==='invalid-json' ?
      {status:200,contentType:'application/json',body:'{broken'} :
      {status:fault==='missing-media'?404:500,json:{detail:'Injected '+fault}}))
    await page.reload(); await page.getByTestId('workspace-save-error').waitFor()
    check((await page.getByTestId('annotation-hit').getAttribute('class')).includes('locked'), fault+': read failure blocks editing')
    check(JSON.stringify((await workspace()).manualAnnotations)===JSON.stringify(manual), fault+': read failure preserves saved annotations')
    await page.unroute(matcher); await button('重新读取工作区').click(); await ready()
    check(await page.locator('.object-select').count()===1, fault+': explicit retry restores existing manual box')
  }

  const asset=()=>page.locator('.asset-card').filter({hasText:path.basename(video)})
  await asset().getByRole('button',{name:/删除/}).click()
  await poll(async () => !(await api('/track/media')).items.some(x=>x.mediaId===mid), 'server deletion')
  await upload(); await draw(); await reload()
  check((await workspace()).manualAnnotations.length===1, 'delete and reupload can draw, save and reload')
  await page.locator('input[type=file][accept="video/*"]').setInputFiles(video)
  await button('取消导入').click(); await ready()
  check((await workspace()).manualAnnotations.length===1, 'cancel duplicate import preserves annotation')
  await page.locator('input[type=file][accept="video/*"]').setInputFiles(video)
  await button('覆盖并重新标注').click(); await page.locator('[data-reimport-dialog]').waitFor({state:'hidden'}); await ready(); await reload()
  check((await workspace()).manualAnnotations.length===0 && (await api('/track/result/'+mid)).state==='not_generated', 'overwrite and refresh return to editable untracked state')
  await draw()

  check((await api('/track/media')).items.find(x => x.mediaId === mid).width === 4096, 'real 4096px video retains original resolution')
  check(errors.length === 0, 'real-video upload lifecycle has no uncaught browser errors')
} finally {
  const name=path.basename(video, path.extname(video))
  fs.writeFileSync('output/playwright/real-upload-'+name+'-results.json', JSON.stringify({scope:'real-video fresh/delete/overwrite/save/reload only; no model inference', checks,errors,requests},null,2))
  await page.screenshot({path:'output/playwright/real-upload-'+name+'.png'}).catch(()=>{})
  await browser.close()
}
