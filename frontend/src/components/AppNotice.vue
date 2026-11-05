<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
const props = withDefaults(defineProps<{ tone?: 'danger' | 'warning' | 'info'; closeLabel?: string; closable?: boolean }>(), { tone: 'danger', closeLabel: '关闭提示', closable: true })
defineEmits<{ close: [] }>()
const icon = computed(() => props.tone === 'info' ? 'info' : 'alert')
</script>
<template>
  <div class="error-banner app-notice" :class="`notice-${tone}`" :role="tone === 'danger' ? 'alert' : 'status'">
    <span class="notice-icon"><AppIcon :name="icon" :size="15" /></span>
    <div class="notice-body"><slot /></div>
    <div v-if="$slots.actions" class="notice-actions"><slot name="actions" /></div>
    <button v-if="closable" type="button" class="notice-close" :aria-label="closeLabel" :title="closeLabel" @click="$emit('close')"><AppIcon name="close" :size="14" /></button>
  </div>
</template>
