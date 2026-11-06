// The two stages share one mounted workbench, and panels glide. The rest of the regression runs with reduced
// motion and reads final geometry, so the real motion and its end states are checked here. See docs/WORKFLOW.md.
import assert from 'node:assert/strict'

export async function stageMotionChecks(page, { origin, openStage }) {
  let count = 0
  const check = (ok, message) => { assert(ok, message); console.log('PASS motion', ++count, message) }
  const button = name => page.getByRole('button', { name, exact: true })
  const tag = page.getByTestId('workbench-mode'), nav = page.locator('.workflow-nav'), drawer = page.locator('.shell-drawer'), inspector = page.locator('[data-workbench-region=inspector]')
  const toggle = page.getByTestId('toggle-workbench-library')
  const canvasWidth = async () => (await page.locator('[data-workbench-region=canvas]').boundingBox()).width
  const settle = () => page.waitForTimeout(450) // Longer than --dur-slow (.32 s).
  const css = (selector, name, pseudo) => page.evaluate(([s, n, p]) => getComputedStyle(document.querySelector(s), p).getPropertyValue(n).trim(), [selector, name, pseudo])
  const listen = () => page.evaluate(() => {
    window.__motion = { transitions: [], animations: [] }
    document.addEventListener('transitionrun', e => window.__motion.transitions.push(`${e.target.classList?.[0]}:${e.propertyName}`), true)
    document.addEventListener('animationstart', e => window.__motion.animations.push(`${e.target.classList?.[0]}:${e.animationName}`), true)
  })
  const seen = async (kind, entry) => (await page.evaluate(k => window.__motion[k], kind)).includes(entry)
  const reset = () => page.evaluate(() => { window.__motion.transitions = []; window.__motion.animations = [] })
  const closeDrawer = async () => { if (await toggle.getAttribute('aria-expanded') === 'true') await toggle.click(); await drawer.waitFor({ state: 'hidden' }) }

  await page.setViewportSize({ width: 1366, height: 768 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await openStage('/annotate'); await listen(); await closeDrawer(); await settle()

  // Each stage says which it is, also where the header is out of sight.
  check(await tag.innerText() === '标注模式' && await nav.getAttribute('data-stage') === 'annotate', 'annotation labels itself above the image and in the step control')
  const annotateAccent = await css('.app-shell', '--accent'), annotateHeader = await css('.app-header', 'background-color')
  check(await css('.annotation-canvas', 'opacity', '::after') === '0', 'annotation has no stage rim around the canvas')

  // Switching keeps the page: same elements, frame and zoom, and nothing is downloaded again.
  const startFrame = await page.evaluate(() => window.ws.currentFrame.value)
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight')
  await page.waitForFunction(frame => window.ws.currentFrame.value === frame && !window.ws.exactFrameLoading.value, startFrame + 2)
  await page.evaluate(() => { window.ws.zoom.value = 1.5; window.__page = document.querySelector('.annotation-page'); window.__image = document.querySelector('.exact-media') })
  const requests = [], record = request => { if (request.url().includes('/api/')) requests.push(`${request.method()} ${decodeURI(new URL(request.url()).pathname)}`) }
  page.on('request', record); await reset()
  await button('审查模式').click(); await page.getByTestId('review-panel').waitFor()
  check((await page.getByTestId('status-toast').innerText()).includes('已进入审查模式'), 'the moment of switching is announced')
  await settle(); page.off('request', record)
  check(await page.evaluate(() => window.__page === document.querySelector('.annotation-page') && window.__image === document.querySelector('.exact-media')), 'switching to review keeps the mounted page and the same image element')
  check(await page.evaluate(frame => window.ws.currentFrame.value === frame && window.ws.zoom.value === 1.5, startFrame + 2), 'switching to review stays on the same frame and zoom')
  const reloads = requests.filter(r => /\/track\/(result|frame)|\/frames?\//.test(r) || (/^GET \/api\/track\/workspace\//.test(r) && !r.endsWith('/stamp')))
  check(reloads.length === 0 && requests.some(r => r.includes('/annotation-review/media/')), `switching re-reads only the review state, no workspace, tracking or frames (${requests.length} requests)`)
  await page.evaluate(() => { window.ws.zoom.value = 1 }); await page.waitForTimeout(100)

  // The stage colour turns everywhere and cross-fades; the pill slides.
  check(await tag.innerText() === '审查模式' && await nav.getAttribute('data-stage') === 'review', 'review labels itself above the image and in the step control')
  check(await css('.app-shell', '--accent') !== annotateAccent && await css('.app-header', 'background-color') !== annotateHeader, 'review turns the accent and the header to its own colour')
  check(await css('.annotation-canvas', 'opacity', '::after') === '1', 'review rims the canvas')
  check(await seen('transitions', 'app-shell:--accent') && await seen('transitions', 'workflow-thumb:transform'), 'the accent cross-fades and the step pill slides')
  check(await seen('animations', 'shell-card:slide-in-right'), 'the review panel enters softly')

  // Failing to save before leaving keeps the stage, and the same click works once saving recovers.
  await button('人工标注').click(); await page.getByTestId('review-panel').waitFor({ state: 'detached' }); await settle()
  check(await css('.app-shell', '--accent') === annotateAccent && await css('.annotation-canvas', 'opacity', '::after') === '0', 'back in annotation everything returns to the annotation colour')
  check(await page.locator('.annot-ai').isVisible(), 'back in annotation the tracking card is there again')
  await page.route('**/api/track/workspace/*', route => route.request().method() === 'PUT' ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"保存失败（模拟）"}' }) : route.fallback())
  const objectsBefore = await page.evaluate(() => window.ws.currentObjects.value.length)
  await page.keyboard.press('b')
  const hit = await page.getByTestId('annotation-hit').boundingBox()
  await page.mouse.move(hit.x + hit.width - 90, hit.y + hit.height - 90); await page.mouse.down(); await page.mouse.move(hit.x + hit.width - 40, hit.y + hit.height - 40, { steps: 4 }); await page.mouse.up()
  await page.waitForFunction(count => window.ws.currentObjects.value.length === count + 1, objectsBefore)
  await page.keyboard.press('h')
  await button('审查模式').click()
  await page.getByTestId('workspace-save-error').waitFor()
  check(new URL(page.url()).pathname === '/annotate' && await nav.getAttribute('data-stage') === 'annotate' && await tag.innerText() === '标注模式', 'a failed save keeps the current stage instead of switching')
  await page.unroute('**/api/track/workspace/*')
  await page.getByTestId('workspace-save-error').getByRole('button', { name: /重试/ }).first().click()
  await page.getByTestId('workspace-save-error').waitFor({ state: 'detached' })
  await button('审查模式').click(); await page.getByTestId('review-panel').waitFor()
  check(await nav.getAttribute('data-stage') === 'review', 'after saving recovers the switch goes through')
  check(await page.locator('.annotation-overlay').isVisible(), 'a reviewer always starts with the boxes visible')
  await button('人工标注').click(); await page.getByTestId('review-panel').waitFor({ state: 'detached' }); await settle()
  await page.keyboard.press('ControlOrMeta+z') // Same page, same undo history: the test box goes away again.
  await page.waitForFunction(count => window.ws.currentObjects.value.length === count && window.ws.saveState.value !== 'saving', objectsBefore)
  await page.waitForFunction(() => window.ws.saveState.value === 'saved')

  // The drawer glides open and shut; the image gets the space back.
  const width0 = await canvasWidth(), stage0 = await page.locator('.annotation-stage').boundingBox()
  await reset(); await toggle.click(); await settle()
  check(await seen('transitions', 'shell-drawer:width'), 'the drawer opens by gliding')
  check((await drawer.boundingBox()).width === 248 && await canvasWidth() === width0 - 248, 'the opened drawer ends at full width beside the canvas')
  check(await page.locator('.asset-card').first().isVisible(), 'the list is usable once the drawer is open')
  await reset(); await toggle.click(); await settle()
  check(await seen('transitions', 'shell-drawer:width') && await drawer.isHidden() && await canvasWidth() === width0, 'the drawer closes by gliding and returns all its space')
  const stage1 = await page.locator('.annotation-stage').boundingBox()
  check(stage1.width === stage0.width && stage1.height === stage0.height, 'the image is back at its fitted size')
  await toggle.click(); await toggle.click(); await settle()
  check(await toggle.getAttribute('aria-expanded') === 'false' && await drawer.isHidden() && await canvasWidth() === width0, 'reversing the drawer half-way ends in the state last asked for')

  // Same for the inspector, without reflowing its contents.
  await reset(); await button('收起检查面板').click(); await settle()
  check(await seen('transitions', 'shell-inspector:width') && await inspector.isHidden() && await canvasWidth() === width0 + 288, 'the inspector closes by gliding and gives its width to the canvas')
  await reset(); await button('展开检查面板').click(); await settle()
  check(await seen('transitions', 'shell-inspector:width') && await canvasWidth() === width0, 'the inspector opens by gliding back to its width')

  // Focus changes the layout in one step: the view logic needs the final size at once.
  await reset(); await button('专注').click()
  check(await inspector.isHidden(), 'entering focus hides the inspector at once')
  await settle()
  check(!await seen('transitions', 'shell-inspector:width') && await seen('animations', 'shell:focus-rise'), 'focus moves as a whole and its panels do not glide')
  check(await tag.isVisible(), 'the stage label stays visible in focus mode')
  await reset(); await page.getByRole('button', { name: /退出专注/ }).click(); await settle()
  check(await seen('animations', 'shell:shell-settle') && await canvasWidth() === width0, 'leaving focus settles back to the same layout')

  // Stepping frames stays immediate: the picture element persists and nothing fades.
  await reset(); await page.evaluate(() => { window.__image = document.querySelector('.exact-media') })
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight')
  await page.waitForFunction(() => !window.ws.exactFrameLoading.value); await page.waitForTimeout(150)
  check(await page.evaluate(() => window.__image === document.querySelector('.exact-media')) && !await seen('animations', 'stage-media:fade-in'), 'stepping frames keeps the same image without any fade')

  // Stopping playback swaps in the same picture: it must not flash.
  await reset(); await page.evaluate(() => { window.__image = document.querySelector('.exact-media') })
  await button('播放').click(); await page.waitForFunction(() => window.ws.isPlaying.value)
  await page.waitForTimeout(250); await button('暂停').click()
  await page.waitForFunction(() => !window.ws.isPlaying.value && !window.ws.exactFrameLoading.value && !!document.querySelector('.exact-media')); await page.waitForTimeout(150)
  check(!await seen('animations', 'stage-media:fade-in'), 'stopping playback does not fade the picture')

  // Theme: all colours cross-fade once, and the helper class does not linger.
  await button('切换深色主题').click()
  check(await page.evaluate(() => document.documentElement.classList.contains('theme-fading')), 'changing theme cross-fades the colours')
  await settle()
  check(await page.evaluate(() => !document.documentElement.classList.contains('theme-fading')), 'the theme fade ends by itself')
  await button('切换浅色主题').click(); await settle()

  // Opening another video: its picture fades in once.
  await toggle.click(); await reset()
  await page.locator('.asset-card:not(.selected)').filter({ hasText: 'VID' }).first().click()
  await page.waitForFunction(() => !window.ws.workspaceRestoring.value && !window.ws.exactFrameLoading.value && !!document.querySelector('.exact-media')); await page.waitForTimeout(100)
  check(await seen('animations', 'stage-media:fade-in'), 'opening another video fades its picture in')

  // Reduced motion: every panel and the stage switch are immediate.
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openStage('/annotate'); await listen(); await closeDrawer()
  await toggle.click()
  check(await drawer.isVisible() && (await drawer.boundingBox()).width === 248, 'reduced motion: the drawer opens at once')
  await toggle.click()
  check(await drawer.isHidden() && await canvasWidth() === width0, 'reduced motion: the drawer closes at once')
  await button('收起检查面板').click()
  check(await inspector.isHidden() && await canvasWidth() === width0 + 288, 'reduced motion: the inspector closes at once')
  await button('展开检查面板').click()
  check(await inspector.isVisible() && await canvasWidth() === width0, 'reduced motion: the inspector opens at once')
  check((await page.evaluate(() => window.__motion.transitions)).length === 0, 'reduced motion: no transition runs at all')
  await button('审查模式').click(); await page.getByTestId('review-panel').waitFor()
  check(await css('.app-shell', '--accent') !== annotateAccent, 'reduced motion: the stage colour applies at once')
  await button('人工标注').click(); await page.getByTestId('review-panel').waitFor({ state: 'detached' })
}
