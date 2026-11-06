// Message behaviour on the workbench and records page: placement, animation, expiry and
// collapse. Failures are injected into the store (simulated); the real failure paths are
// covered by tracking-recovery and annotation-ux. Screenshots go to output/playwright.
import assert from 'node:assert/strict'

const MESSAGE = '模型追踪失败：头部检测器忙'
const SIZES = [{ width: 1366, height: 768 }, { width: 1180, height: 760 }, { width: 760, height: 760 }]

export async function noticeChecks(page, { origin, openStage, selectTheme, out = '../output/playwright' }) {
  let count = 0
  const check = (ok, message) => { assert(ok, message); console.log('PASS notices', ++count, message) }
  const notice = page.getByTestId('tracking-error'), chip = page.getByTestId('notice-chip'), toast = page.getByTestId('status-toast')
  const failTracking = message => page.evaluate(async text => {
    const url = performance.getEntriesByType('resource').find(r => r.name.includes('/stores/workspace/state.ts')).name
    const { trackingErrors, currentMediaId } = await import(url)
    trackingErrors.value[currentMediaId.value] = { phase: '模型追踪', frameIndex: 0, message: text }
  }, message)
  const clearTracking = () => page.evaluate(async () => {
    const url = performance.getEntriesByType('resource').find(r => r.name.includes('/stores/workspace/state.ts')).name
    const { trackingErrors, currentMediaId } = await import(url)
    delete trackingErrors.value[currentMediaId.value]
  })
  const showToast = (message, tone) => page.evaluate(([m, t]) => window.ws.showToast(m, t), [message, tone])
  // Nothing may cover the image or the toolbar: the rects must not overlap.
  const placement = () => page.evaluate(() => {
    const rect = selector => document.querySelector(selector).getBoundingClientRect()
    const box = rect('.workbench-notices'), scroller = rect('.canvas-scroll'), tools = rect('.annotation-canvas>.shell-tools')
    const close = document.querySelector('[data-testid=tracking-error] .notice-close').getBoundingClientRect()
    const hit = document.elementFromPoint(close.x + close.width / 2, close.y + close.height / 2)
    return { clear: box.top >= tools.bottom - 1 && box.bottom <= scroller.top + 1, closeHit: !!hit?.closest('.notice-close'), height: scroller.height }
  })
  const stageBox = () => page.evaluate(() => { const r = document.querySelector('.annotation-stage').getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join() })

  for (const [path, stage] of [['/annotate', '标注'], ['/review', '审查']]) {
    await page.setViewportSize(SIZES[0]); await openStage(path)
    const before = await stageBox()

    // Persistent failure: no silent expiry, can be collapsed, cannot be lost.
    await page.emulateMedia({ reducedMotion: 'no-preference' }) // Only the entrance looks at the motion itself.
    await failTracking(MESSAGE); await notice.waitFor()
    check((await page.getByRole('alert').filter({ hasText: MESSAGE }).count()) === 1, `${stage}: failure is announced as an alert`)
    const opacity = await notice.evaluate(el => Number(getComputedStyle(el).opacity))
    check(opacity < 1, `${stage}: failure fades in rather than popping`)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.waitForTimeout(6500)
    check(await notice.isVisible(), `${stage}: unresolved failure does not expire by itself`)
    await page.screenshot({ path: `${out}/notices-${stage}-banner.png` })
    await notice.getByRole('button', { name: /收起提示/ }).click()
    await notice.waitFor({ state: 'detached' })
    check(await chip.isVisible() && (await chip.innerText()).includes('1'), `${stage}: collapsing leaves an unresolved marker in the toolbar`)
    await page.waitForFunction(b => { const r = document.querySelector('.annotation-stage').getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join() === b }, before)
    check(true, `${stage}: collapsing returns the image to its original size`)
    await page.screenshot({ path: `${out}/notices-${stage}-chip.png` })
    await chip.click(); await notice.waitFor()
    await chip.waitFor({ state: 'detached' }); check(await notice.isVisible(), `${stage}: the marker reopens the same failure`)
    await notice.getByRole('button', { name: /收起提示/ }).click(); await notice.waitFor({ state: 'detached' })
    await failTracking('另一次失败：连接中断'); await notice.filter({ hasText: '另一次失败' }).waitFor()
    check(true, `${stage}: a different failure reopens instead of staying collapsed`)
    await clearTracking(); await notice.waitFor({ state: 'detached' })
    await page.waitForFunction(() => !document.querySelector('[data-testid=notice-chip]'))
    check(true, `${stage}: a resolved failure clears both the notice and the marker`)

    // Placement at every width and theme.
    for (const size of SIZES) for (const dark of [false, true]) {
      await page.setViewportSize(size); await selectTheme(dark)
      await failTracking(MESSAGE); await notice.waitFor(); await notice.evaluate(el => el.scrollIntoView({ block: 'center' }))
      await page.waitForTimeout(350)
      const at = await placement(), label = `${stage} ${size.width}${dark ? ' dark' : ''}`
      check(at.clear, `${label}: failure sits between toolbar and image, covering neither`)
      check(at.closeHit, `${label}: failure actions are reachable`)
      check(at.height >= 120, `${label}: image area stays usable (${Math.round(at.height)} px)`)
      await page.screenshot({ path: `${out}/notices-${stage}-${size.width}-${dark ? 'dark' : 'light'}.png` })
      await clearTracking(); await notice.waitFor({ state: 'detached' })
    }
    await page.setViewportSize(SIZES[0]); await selectTheme(false)

    // A failed head click can simply be repeated, so its message expires; it stays off the toolbar.
    const head = page.locator('.head-proposal-status')
    await page.evaluate(() => { window.ws.headBoxError.value = '头部检测器忙，请稍后重试' }); await head.waitFor()
    const headClear = await head.evaluate(el => { const r = el.getBoundingClientRect(), tools = document.querySelector('.annotation-canvas>.shell-tools').getBoundingClientRect(), canvas = document.querySelector('.annotation-canvas').getBoundingClientRect(); return r.top >= tools.bottom && r.bottom <= canvas.bottom })
    check(headClear, `${stage}: head-click failure sits inside the canvas corner`)
    await page.waitForTimeout(400); await page.screenshot({ path: `${out}/notices-${stage}-head.png` })
    await head.waitFor({ state: 'detached', timeout: 7000 })
    check(await page.evaluate(() => window.ws.headBoxError.value === ''), `${stage}: head-click failure expires by itself`)

    // Toasts: above the frame strip, pass clicks through, leave on time.
    const started = Date.now(); await showToast('已追踪到视频末尾', 'success'); await toast.waitFor()
    for (let i = 0; i < 5; i++) { await page.waitForTimeout(400); await showToast('已追踪到视频末尾', 'success') }
    const geometry = await toast.evaluate(el => { const r = el.getBoundingClientRect(), bar = document.querySelector('.shell-bottom').getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return { above: r.bottom <= bar.top, through: !el.contains(hit), events: getComputedStyle(el).pointerEvents } })
    check(geometry.above, `${stage}: toast no longer covers the frame strip`)
    check(geometry.through && geometry.events === 'none', `${stage}: toast never swallows a click meant for the canvas`)
    await page.screenshot({ path: `${out}/notices-${stage}-toast.png` })
    await toast.waitFor({ state: 'detached', timeout: 4000 })
    const elapsed = Date.now() - started
    check(elapsed >= 2900 && elapsed < 3700, `${stage}: repeated toast still leaves on time (${elapsed} ms)`)
    await showToast('JSON 生成失败', 'error'); await toast.waitFor()
    await toast.getByRole('button', { name: '关闭提示' }).click(); await toast.waitFor({ state: 'detached' })
    check(true, `${stage}: failure toast can be closed by hand`)
    const held = await page.evaluate(() => new Promise(resolve => { window.ws.showToast('第一条'); requestAnimationFrame(() => { window.ws.showToast('第二条'); requestAnimationFrame(() => resolve(document.querySelector('[data-testid=status-toast]:last-child').innerText)) }) }))
    check(held.includes('第二条'), `${stage}: a new toast replaces the visible one`)
    await page.waitForFunction(() => !document.querySelector('[data-testid=status-toast]'), null, { timeout: 4500 })
  }

  // Reduced motion: no entrance animation and no delayed removal.
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await failTracking(MESSAGE); await notice.waitFor()
  // Vue drops its enter class after two frames; with motion off the end state is then immediate.
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(done)))))
  check(await notice.evaluate(el => getComputedStyle(el).transitionDuration === '0s' && getComputedStyle(el).opacity === '1'), 'reduced motion: failure appears without animation')
  await clearTracking(); await page.waitForFunction(() => !document.querySelector('[data-testid=tracking-error]'), null, { timeout: 300 })
  check(true, 'reduced motion: failure leaves without delay')

  // Records page shares the error style.
  await page.route('**/api/annotation/projects/*/results*', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"读取失败（模拟）"}' }))
  for (const dark of [false, true]) {
    await page.goto(origin + '/results'); await selectTheme(dark)
    await page.locator('.error-banner').first().waitFor()
    const style = await page.locator('.error-banner').first().evaluate(el => { const s = getComputedStyle(el); return { radius: s.borderRadius, width: s.borderLeftWidth } })
    check(style.radius === '8px' && style.width === '1px', `记录${dark ? ' dark' : ''}: failure uses the shared style`)
    await page.screenshot({ path: `${out}/notices-results-${dark ? 'dark' : 'light'}.png` })
  }
  await page.unroute('**/api/annotation/projects/*/results*'); await selectTheme(false)
}
