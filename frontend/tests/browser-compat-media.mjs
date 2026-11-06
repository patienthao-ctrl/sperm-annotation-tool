import assert from 'node:assert/strict'
import fs from 'node:fs'
import { useLegacyBrowserAPIs } from './legacy-browser-profile.mjs'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
// The media list is a drawer that stays closed while a video is open.
const openAsset=async(target,locator)=>{const toggle=target.getByTestId('toggle-workbench-library');for(let i=0;i<40;i++){if(await locator.isVisible()){try{await locator.click({timeout:1500});return}catch{}}else if(await toggle.isVisible())await toggle.click();await target.waitForTimeout(250)}await locator.click()}
const fixture = JSON.parse(fs.readFileSync('work/browser-fixture.json'))
const videoFile = process.env.COMPAT_VIDEO || 'work/e2e-confirm-ux-compat-storage/media/review-fixture/review-fixture.avi'
assert(videoFile.includes('work/'), 'Only generated fixture videos may be uploaded by this test')
const origin = process.env.COMPAT_ORIGIN || 'http://hospital-test.local:5377'
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--host-resolver-rules=MAP hospital-test.local 127.0.0.1', '--no-proxy-server'] })
const page = await browser.newPage({reducedMotion:'reduce',viewport: { width: 1440, height: 900 } })
const errors = [], logs = [], deletes = []
page.on('pageerror', e => errors.push(String(e)))
page.on('console', m => logs.push({ type: m.type(), text: m.text() }))
page.on('request', r => { if (r.method() === 'DELETE') deletes.push(r.url()) })
const check = (ok, message) => { assert(ok, message); console.log('PASS', message) }
const row = () => page.locator('.asset-card').filter({ hasText: 'compat-upload.avi' })
async function loaded() {
  await page.locator('img.exact-media').waitFor()
  await page.getByRole('button', { name: '完成标注并送审', exact: true }).waitFor()
  await page.waitForFunction(() => !document.querySelector('.annotation-stage-status .btn-primary')?.disabled)
}
async function deleteClick(accept = true) {
  page.once('dialog', async dialog => { check(dialog.message().includes('不能撤销'), 'deletion explains its effect'); await (accept ? dialog.accept() : dialog.dismiss()) })
  await row().getByRole('button', { name: '删除素材 compat-upload.avi', exact: true }).click()
}
try {
  await useLegacyBrowserAPIs(page)
  await page.addInitScript(({ authorToken }) => {
    localStorage.setItem('rare-sperm-token', authorToken)
    localStorage.setItem('rare-sperm-auth', JSON.stringify({ id: '1', name: 'review-A' }))
  }, fixture)
  await page.goto(origin + '/annotate')
  check(await page.evaluate(() => !isSecureContext && typeof crypto.randomUUID === 'undefined' && typeof AbortSignal.timeout === 'undefined'), 'actual insecure HTTP context lacks both modern APIs')
  const localRow = page.locator('.asset-card').filter({ hasText: 'microfluidic-sample.svg' })
  await localRow.waitFor()
  check(await page.getByRole('button', { name: /关闭素材/ }).count() === 0, 'media list exposes deletion only')
  const localDelete = localRow.getByRole('button', { name: '删除素材 microfluidic-sample.svg', exact: true })
  page.once('dialog', async d => { check(d.message().includes('电脑原文件和已保存到服务器的标注记录会保留'), 'local deletion explains its distinct scope'); await d.dismiss() })
  await localDelete.click()
  check(await localRow.count() === 1 && deletes.length === 0, 'cancel preserves local image without server deletion')
  page.once('dialog', d => d.accept())
  await localDelete.click()
  await localRow.waitFor({ state: 'detached' })
  check(deletes.length === 0, 'local image deletion sends no server DELETE')
  check(await page.evaluate(() => !JSON.parse(localStorage.getItem('annotationsByMedia') || '{}')['img-demo-001']), 'local image annotations are removed')
  await page.reload()
  await page.locator('.asset-card').filter({ hasText: 'review-fixture.avi' }).waitFor()
  check(await localRow.count() === 0, 'deleted local image stays removed after refresh')
  const upload = page.waitForResponse(r => r.url().endsWith('/api/track/upload') && r.request().method() === 'POST')
  await page.locator('input[accept="video/*"]').setInputFiles({ name: 'compat-upload.avi', mimeType: 'video/x-msvideo', buffer: fs.readFileSync(videoFile) })
  const response = await upload
  check(response.ok(), 'real video upload succeeds')
  const media = await response.json()
  await loaded()
  check(await page.locator('img.exact-media').evaluate(img => img.complete && img.naturalWidth === 800), 'server-decoded frame is displayed without AbortSignal.timeout')
  await page.getByRole('spinbutton', { name: '跳转帧号' }).fill('2')
  await page.getByRole('spinbutton', { name: '跳转帧号' }).press('Enter')
  await page.locator('img[alt="compat-upload.avi 第 2 帧"]').waitFor()
  check(true, 'frame navigation works with old-browser callback fallback')
  await deleteClick(false)
  check(deletes.length === 0 && await row().count() === 1, 'cancel does not send DELETE or hide the video')
  // Preserve a stale browser cache to reproduce another tab/client after deletion.
  const stale = await page.evaluate(() => JSON.parse(localStorage.getItem('mediaAssets') || '[]'))
  await page.route('**/api/track/media/' + media.mediaId, route => route.fulfill({ status: 500, json: { detail: '测试注入：文件删除失败' } }))
  await deleteClick()
  await page.getByRole('alert').filter({ hasText: '文件删除失败' }).waitFor()
  check(await row().count() === 1, 'failed deletion remains visible and retryable')
  await page.unroute('**/api/track/media/' + media.mediaId)
  const deleted = page.waitForResponse(r => r.request().method() === 'DELETE' && r.url().endsWith('/' + media.mediaId))
  await deleteClick()
  check((await deleted).ok(), 'retry deletes the real server video')
  await row().waitFor({ state: 'detached' })
  await page.reload()
  await page.locator('.asset-card').filter({ hasText: 'review-fixture.avi' }).waitFor()
  check(await row().count() === 0, 'immediate reload does not resurrect deleted video')
  await page.evaluate(items => localStorage.setItem('mediaAssets', JSON.stringify(items)), stale)
  const synced = page.waitForResponse(r => r.url().endsWith('/api/track/media') && r.request().method() === 'GET')
  await page.reload(); await synced
  await row().waitFor({ state: 'detached' })
  check(true, 'authoritative server list removes stale cached media from another tab')
  await openAsset(page,page.locator('.asset-card').filter({ hasText: 'review-fixture.avi' }))
  await loaded()
  await page.getByRole('button', { name: '完成标注并送审', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '完成标注并送审' })
  await dialog.getByRole('checkbox').check()
  let first = true
  const keys = []
  await page.route('**/api/review/media/review-fixture/complete', async route => {
    keys.push(route.request().headers()['idempotency-key'])
    if (first) { first = false; const result = await route.fetch({ url: route.request().url().replace("hospital-test.local", "127.0.0.1") }); check(result.ok(), 'first send-to-review is committed before injected response loss'); await route.abort('failed') }
    else await route.continue()
  })
  await dialog.getByRole('button', { name: '确认送审', exact: true }).click()
  await dialog.getByRole('alert').waitFor()
  await dialog.getByRole('button', { name: '重试送审', exact: true }).click()
  await dialog.getByRole('status').waitFor()
  check(keys.length === 2 && keys[0] === keys[1] && /^[a-f0-9-]{36}$/.test(keys[0]), 'HTTP send-to-review retries the original UUID without duplicate submission')
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  page.once('dialog', d => d.accept())
  await page.getByRole('button', { name: '删除素材 review-fixture.avi', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: '审查基准' }).waitFor()
  check(await page.locator('.asset-card').filter({ hasText: 'review-fixture.avi' }).count() === 1, 'submitted original video remains protected')
  // A slow initial list must not prune an upload which finishes after its snapshot.
  let releaseList, capturedList
  const gate = new Promise(resolve => { releaseList = resolve })
  const captured = new Promise(resolve => { capturedList = resolve })
  await page.route('**/api/track/media', async route => {
    const result = await route.fetch({ url: route.request().url().replace('hospital-test.local', '127.0.0.1') })
    capturedList(); await gate; await route.fulfill({ response: result })
  })
  await page.reload(); await captured
  const newUpload = page.waitForResponse(r => r.url().endsWith('/api/track/upload'))
  await page.locator('input[accept="video/*"]').setInputFiles({ name: 'compat-upload.avi', mimeType: 'video/x-msvideo', buffer: fs.readFileSync(videoFile) })
  check((await newUpload).ok(), 'upload can finish while an older media list is pending')
  await loaded()
  const listResponse = page.waitForResponse(r => r.url().endsWith('/api/track/media'))
  releaseList(); await listResponse
  await page.waitForTimeout(300)
  check(await row().count() === 1, 'older list response does not remove a newly uploaded video')
  await page.unroute('**/api/track/media')
  await page.route('**/api/track/media', r => r.fulfill({ status: 503, json: { detail: '列表临时不可用' } }))
  const listFailed = page.waitForResponse(r => r.url().endsWith('/api/track/media') && r.status() === 503)
  await page.reload(); await listFailed
  await page.waitForTimeout(200)
  check(logs.some(entry => entry.text.includes('[annotation.media_list_failed]')), 'failed list fetch is logged')
  check(await row().count() === 1, 'failed list fetch preserves local media instead of treating it as deleted')
  await page.unroute('**/api/track/media')
  await loaded(); await deleteClick()
  await row().waitFor({ state: 'detached' })
  await page.screenshot({ path: 'output/playwright/compat-media.png', fullPage: true })
  check(errors.length === 0, 'no uncaught JavaScript runtime errors')
} finally {
  fs.writeFileSync('output/playwright/compat-media-console.json', JSON.stringify({ errors, logs }, null, 2))
  await browser.close()
}
