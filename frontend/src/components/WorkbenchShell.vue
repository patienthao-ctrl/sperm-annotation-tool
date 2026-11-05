<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import '../workbench/shell.css'

// Canvas-first shell shared by annotation, review and confirmation. Pages fill
// the slots; the shell owns the rail, drawer, inspector collapse and where the
// page context goes (site header normally, a slim bar in focus mode).
const props = withDefaults(defineProps<{
  name: string
  focused?: boolean
  focusKeepsInspector?: boolean
  drawerLabel: string
  drawerCount?: number
  drawerForced?: boolean
  drawerBusy?: boolean
  inspectorForced?: boolean
  inspectorBadge?: string | number
}>(), { focused: false, focusKeepsInspector: false, drawerCount: 0, drawerForced: false, drawerBusy: false, inspectorForced: false, inspectorBadge: '' })

const drawerOpen = defineModel<boolean>('drawerOpen', { default: false })
const key = `workbench-${props.name}-inspector-collapsed`
const read = () => { try { return localStorage.getItem(key) === 'true' } catch { return false } }
const inspectorCollapsed = ref(read())
watch(inspectorCollapsed, value => { try { localStorage.setItem(key, String(value)) } catch (error) { console.warn('[ui.preference_save_failed]', { preference: key, error }) } })

const headerReady = ref(false)
onMounted(() => { headerReady.value = !!document.getElementById('app-header-context') })
const drawerVisible = computed(() => !props.focused && (drawerOpen.value || props.drawerForced))
const inspectorVisible = computed(() => (!props.focused || props.focusKeepsInspector) && (!inspectorCollapsed.value || props.inspectorForced))
defineExpose({ collapseInspector: () => { inspectorCollapsed.value = true } })
</script>

<template>
  <div class="shell" :class="[`shell-${name}`, { 'is-focus': focused }]" data-testid="workbench-layout">
    <Teleport v-if="headerReady && !focused" to="#app-header-context"><div class="shell-context"><slot name="context" :inspector-visible="inspectorVisible" /></div></Teleport>
    <nav v-show="!focused" class="shell-rail" :aria-label="drawerLabel">
      <button class="shell-rail-btn" :class="{ active: drawerVisible, busy: drawerBusy && !drawerVisible }" data-testid="toggle-workbench-library" :aria-expanded="drawerVisible" :title="drawerVisible ? `收起${drawerLabel} (M)` : `展开${drawerLabel} (M)`" :aria-label="drawerLabel" @click="drawerOpen = !drawerVisible"><AppIcon name="folder" :size="18" /><small v-if="drawerCount">{{ drawerCount }}</small></button>
      <slot name="rail" />
    </nav>
    <aside v-show="drawerVisible" class="shell-drawer" :aria-label="drawerLabel" data-workbench-region="library">
      <div class="shell-drawer-head"><strong>{{ drawerLabel }}</strong><span v-if="drawerCount" class="badge">{{ drawerCount }}</span><button class="icon-button" :aria-label="`收起${drawerLabel}`" @click="drawerOpen = false"><AppIcon name="close" :size="15" /></button></div>
      <div class="shell-drawer-body"><slot name="drawer" /></div>
    </aside>
    <section class="shell-center" data-workbench-region="canvas">
      <div v-if="focused" class="shell-focusbar"><div class="shell-context"><slot name="context" :inspector-visible="inspectorVisible" /></div></div>
      <div class="shell-canvas"><slot name="canvas" /></div>
      <div v-if="$slots.bottom" class="shell-bottom"><slot name="bottom" /></div>
    </section>
    <aside v-show="inspectorVisible" class="shell-inspector" data-workbench-region="inspector">
      <button v-if="!inspectorForced" class="icon-button shell-collapse" title="收起面板" aria-label="收起检查面板" @click="inspectorCollapsed = true"><AppIcon name="panel" :size="15" /></button>
      <slot name="inspector" />
    </aside>
    <button v-if="!inspectorVisible && (!focused || focusKeepsInspector)" class="shell-inspector-open" title="展开检查面板" aria-label="展开检查面板" @click="inspectorCollapsed = false"><AppIcon name="panel" :size="16" /><span v-if="inspectorBadge !== ''">{{ inspectorBadge }}</span></button>
  </div>
</template>
