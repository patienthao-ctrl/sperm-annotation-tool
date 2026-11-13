<script setup lang="ts">
// One toolbar button: icon above a short label, and a hover/focus tip that says what it does
// and its key. In a compact toolbar the label folds away and the tip carries it.
import AppIcon from './AppIcon.vue'

defineProps<{
  icon: string
  label: string
  /** One short line: what happens when it is used. */
  hint?: string
  keys?: string
  /** Toggle that is on. */
  on?: boolean
  /** Inside a segmented group: the selected one (the group draws the highlight). */
  selected?: boolean
  pressed?: boolean
  disabled?: boolean
  ariaLabel?: string
  tipAlign?: 'start' | 'center' | 'end'
}>()
defineEmits<{ click: [event: MouseEvent] }>()
</script>

<template>
  <button type="button" class="wb-tool" :class="{ on, selected }" :disabled="disabled" :aria-pressed="pressed" :aria-label="ariaLabel || label" @click="$emit('click', $event)">
    <AppIcon class="tool-ic" :name="icon" :size="18" />
    <span class="tool-label">{{ label }}</span>
    <span class="tool-tip" :class="tipAlign || 'center'" role="tooltip" aria-hidden="true">
      <span class="tool-tip-head"><b>{{ label }}</b><kbd v-if="keys">{{ keys }}</kbd></span>
      <small v-if="hint">{{ hint }}</small>
    </span>
  </button>
</template>
