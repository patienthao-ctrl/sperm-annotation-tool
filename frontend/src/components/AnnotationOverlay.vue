<script setup lang="ts">
import type { AnnotationObject } from '../types/annotation'
import type { Box } from '../annotation/geometry'
const props = defineProps<{ objects: AnnotationObject[]; selected: string | null; width: number; height: number; draft: Box | null; labels: boolean; hidden: boolean }>()
const x = (v: number) => v * props.width / 100, y = (v: number) => v * props.height / 100
const color = (obj: AnnotationObject) => obj.id === props.selected ? '#ffd36d' : obj.source === 'ai' ? '#5fe1b0' : '#c2a5ff'
</script>
<template>
  <svg v-show="!hidden" :viewBox="`0 0 ${width} ${height}`" class="annotation-overlay" aria-hidden="true">
    <g v-for="obj in objects" :key="obj.id" :data-object-id="obj.id" :class="{selected:obj.id===selected}">
      <template v-if="obj.bbox">
        <rect class="box-halo" :x="x(obj.bbox.x)" :y="y(obj.bbox.y)" :width="x(obj.bbox.width)" :height="y(obj.bbox.height)" />
        <rect class="annotation-box" :x="x(obj.bbox.x)" :y="y(obj.bbox.y)" :width="x(obj.bbox.width)" :height="y(obj.bbox.height)" :stroke="color(obj)" :stroke-dasharray="obj.source==='manual' && obj.id!==selected ? '6 3' : undefined" />
        <template v-if="obj.id===selected"><rect v-for="[dx,dy] in [[0,0],[1,0],[0,1],[1,1]]" :key="`${dx}${dy}`" class="box-handle" :x="x(obj.bbox.x+dx*obj.bbox.width)-3.5" :y="y(obj.bbox.y+dy*obj.bbox.height)-3.5" width="7" height="7" /></template>
      </template>
      <circle v-else-if="obj.point" :cx="x(obj.point.x)" :cy="y(obj.point.y)" r="4" :fill="color(obj)" stroke="#0c1728" stroke-width="2" />
      <text v-if="labels && (obj.bbox || obj.point)" :x="Math.min(width-65, Math.max(3,x((obj.bbox || obj.point)!.x)))" :y="Math.max(14,y((obj.bbox || obj.point)!.y)-7)" :fill="color(obj)" class="annotation-label">{{ obj.name }}</text>
    </g>
    <rect v-if="draft" class="annotation-draft" :x="x(draft.x)" :y="y(draft.y)" :width="x(draft.width)" :height="y(draft.height)" />
  </svg>
</template>
<style scoped>
.annotation-overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:30;overflow:visible}.box-halo{fill:none;stroke:#09131fbb;stroke-width:4.5px}.annotation-box{fill:none;stroke-width:2px}.selected .annotation-box{stroke-width:2.5px;fill:#ffd36d09}.box-handle{fill:#ffd36d;stroke:#142336;stroke-width:1px}.annotation-label{font:600 11px Inter,system-ui,sans-serif;paint-order:stroke;stroke:#071422;stroke-width:3px;stroke-linejoin:round}.annotation-draft{fill:#c2a5ff10;stroke:#e1cfff;stroke-width:2px;stroke-dasharray:5 3}
</style>
