<script setup lang="ts">
defineProps<{ name: string; size?: number }>()
const paths: Record<string, string> = {
  cursor: 'm4 3 7 17 2-7 7-2L4 3Z', box: 'M5 3H3v2m16-2h2v2M3 19v2h2m16-2v2h-2M7 3h10M7 21h10M3 7v10M21 7v10',
  point: 'M12 3v4m0 10v4M3 12h4m10 0h4M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  upload: 'M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5', folder: 'M3 7V4h6l2 3h10v13H3V7Z',
  undo: 'M9 5 4 10l5 5M4 10h10a6 6 0 0 1 0 12', redo: 'm15 5 5 5-5 5m5-5H10a6 6 0 0 0 0 12',
  zoom: 'M15 15l6 6M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Zm-7-3v6m-3-3h6',
  fit: 'M3 9V3h6m6 0h6v6M3 15v6h6m12-6v6h-6', chevron: 'm9 5 7 7-7 7', close: 'm6 6 12 12M6 18 18 6',
  sun: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  moon: 'M20 15a9 9 0 0 1-11-11 9 9 0 1 0 11 11Z',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  spark: 'm12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Z', list: 'M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1',
  help: 'M9 8a3 3 0 0 1 6 0c0 3-3 2-3 5m0 3v1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
  check: 'm5 12 4 4L19 6', trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  play: 'm7 4 14 8-14 8V4Z', pause: 'M8 4v16M16 4v16', layers: 'm12 3 10 6-10 6L2 9l10-6Zm-10 11 10 6 10-6',
  copy: 'M9 9h11v11H9V9ZM5 15H4V4h11v1', panel: 'M3 4h18v16H3V4Zm12 0v16', hash: 'M5 9h14M5 15h14M10 3 8 21M16 3l-2 18',
  prev: 'M15 5 8 12l7 7', next: 'm9 5 7 7-7 7',
  alert: 'M12 3.5 2.5 20h19L12 3.5ZM12 10v4.5m0 2.7v.01', info: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-.5V16m0-7.8v.01',
  keyboard: 'M3 6h18v12H3V6Zm4 4h.01M11 10h.01M15 10h.01M7 14h10',
}
</script>
<template><svg :width="size || 18" :height="size || 18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.box" /></svg></template>
