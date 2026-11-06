import { nextTick, ref } from 'vue'

// style.css zeroes every CSS transition under prefers-reduced-motion, but a Vue <Transition> still waits two
// animation frames for it. With this off, panels switch in the same tick; the scripted animations below stay still.
const query = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null
export const motionAllowed = ref(!query?.matches)
query?.addEventListener('change', event => { motionAllowed.value = !event.matches })

const GLIDE = { duration: 240, easing: 'cubic-bezier(.4,0,.2,1)' } // --ease-glide

/**
 * Run a change that reorders a list, then glide the rows from where they were: moved rows slide, new rows fade in.
 * Only rows on screen are measured, so the cost does not grow with the length of the list.
 * Rows carry `data-row-key`; the layout is final at once, this only animates transforms.
 */
export async function glideRows(list: HTMLElement | null | undefined, change: () => void) {
  if (!list || !motionAllowed.value) { change(); return }
  const onScreen = () => {
    const view = list.getBoundingClientRect()
    return Array.from(list.querySelectorAll<HTMLElement>('[data-row-key]')).map(el => ({ el, top: el.getBoundingClientRect().top }))
      .filter(({ top, el }) => top < view.bottom && top + el.offsetHeight > view.top)
  }
  const before = new Map(onScreen().map(({ el, top }) => [el.dataset.rowKey, top]))
  change()
  await nextTick()
  for (const { el, top } of onScreen()) {
    const was = before.get(el.dataset.rowKey)
    if (was === undefined) el.animate([{ opacity: 0, transform: 'translateY(-4px)' }, { opacity: 1, transform: 'none' }], GLIDE)
    else if (was !== top) el.animate([{ transform: `translateY(${was - top}px)` }, { transform: 'none' }], GLIDE)
  }
}

/** Cross-fade every colour for one theme change; the rule lives in style.css and is off under reduced motion. */
let themeFade: ReturnType<typeof setTimeout> | null = null
export function fadeThemeColors() {
  const root = document.documentElement
  root.classList.add('theme-fading')
  if (themeFade) clearTimeout(themeFade)
  themeFade = setTimeout(() => root.classList.remove('theme-fading'), 360)
}
