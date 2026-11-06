// Acceptance of the compiled frontend served by the isolated Docker deployment.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { useLegacyBrowserAPIs } from './legacy-browser-profile.mjs'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const port = process.env.COMPAT_PRODUCTION_PORT || '18087'
assert(/^\d+$/.test(port))
const origin = `http://hospital-test.local:${port}`, apiOrigin = `http://127.0.0.1:${port}`
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--host-resolver-rules=MAP hospital-test.local 127.0.0.1', '--no-proxy-server'] })
const page = await browser.newPage({reducedMotion:'reduce',viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', e => errors.push(String(e)))
try {
  const auth = await page.request.post(apiOrigin + '/api/auth/register', { data: { username: `offline-compat-${Date.now()}`, password: `test-${Date.now()}` } })
  assert.equal(auth.status(), 201)
  const account = await auth.json()
  await useLegacyBrowserAPIs(page)
  await page.addInitScript(a => {
    localStorage.setItem('rare-sperm-token', a.token)
    localStorage.setItem('rare-sperm-auth', JSON.stringify(a.user))
  }, account)
  await page.goto(origin + '/annotate')
  assert(await page.evaluate(() => !isSecureContext && !crypto.randomUUID && !AbortSignal.timeout))
  const video = fs.readFileSync('work/e2e-confirm-ux-compat-storage/media/review-fixture/review-fixture.avi')
  const name = `offline-ui-${Date.now()}.avi`
  const upload = async () => {
    await page.locator('input[accept="video/*"]').setInputFiles({ name, mimeType: 'video/x-msvideo', buffer: video })
    await page.locator(`img[alt="${name} 第 1 帧"]`).waitFor()
    await page.waitForFunction(() => !document.querySelector('.annot-context .submission-controls .btn-primary')?.disabled)
    assert(await page.locator('img.exact-media').evaluate(i => i.complete && i.naturalWidth === 800))
  }
  await upload()
  console.log('PASS compiled offline frontend uploads and displays a real frame over HTTP without modern APIs')
  page.once('dialog', d => d.accept())
  const row = () => page.locator('.asset-card').filter({ hasText: name })
  await row().getByRole('button', { name: `删除素材 ${name}`, exact: true }).click()
  await row().waitFor({ state: 'detached' })
  await page.reload()
  await page.locator('.asset-list').waitFor()
  assert.equal(await row().count(), 0)
  console.log('PASS compiled delete removes server video and stays deleted after reload')
  await upload()
  const bounds = await page.getByTestId('annotation-hit').boundingBox()
  await page.mouse.move(bounds.x + bounds.width * .2, bounds.y + bounds.height * .2)
  await page.mouse.down()
  await page.mouse.move(bounds.x + bounds.width * .35, bounds.y + bounds.height * .35, { steps: 5 })
  await page.mouse.up()
  await page.getByRole('button', { name: '完成标注并送审', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '完成标注并送审' })
  await dialog.getByPlaceholder('例如 3, 8-10；其余未知帧请先完成标注').fill('2-3')
  await dialog.getByRole('checkbox').check()
  await dialog.getByRole('button', { name: '确认送审', exact: true }).click()
  await dialog.getByRole('status').waitFor()
  console.log('PASS compiled send-to-review saves the manual box and explicit empty frames without crypto.randomUUID')
  assert.deepEqual(errors, [])
  await page.screenshot({ path: 'output/playwright/compat-offline-production.png', fullPage: true })
  console.log('PASS compiled production page has no uncaught JavaScript errors')
} finally { await browser.close() }
