// The help window shows two parts of user-guide.md (the single source, also offered as a
// download): "三步上手" and the shortcut tables under "快捷键速查".
import { ref } from 'vue'
import guide from './user-guide.md?raw'

export { guide }
const section = (title: string) => guide.split(new RegExp(`^## ${title}\\s*$`, 'm'))[1]?.split(/^## /m)[0] ?? ''

/** `1. **标注**：…` lines of "三步上手". */
export const steps = section('三步上手').split('\n')
  .map(line => line.match(/^\d+\. \*\*(.+?)\*\*[：:]\s*(.+)$/))
  .filter((m): m is RegExpMatchArray => !!m)
  .map(m => ({ title: m[1], text: m[2] }))

function table(caption: string) {
  const shortcuts = section('快捷键速查')
  const start = shortcuts.indexOf(`\n${caption}\n`)
  if (start < 0) return []
  const rows: string[][] = []
  for (const line of shortcuts.slice(start + caption.length + 2).split('\n')) {
    if (!line.trim()) { if (rows.length) break; continue }
    if (!line.startsWith('|')) break
    rows.push(line.split('|').slice(1, -1).map(cell => cell.trim()))
  }
  return rows.slice(2).map(([keys, action]) => ({ keys, action }))  // Header and separator rows.
}
export const shortcuts = { annotate: table('人工标注'), review: table('审查') }

/** Open state shared by the header button, the "?" key and focus mode. */
export const helpOpen = ref(false)
export const openHelp = () => { helpOpen.value = true }
