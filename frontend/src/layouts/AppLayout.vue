<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRouter } from '../router'
import { useAuth } from '../stores/auth'
import { useAppearance } from '../stores/appearance'
import { useWorkspace } from '../stores/workspace'
import AppIcon from '../components/AppIcon.vue'
import HelpDialog from '../components/HelpDialog.vue'
import { openHelp } from '../help/guide'
const router = useRouter(), routePath = router.path
const { user, logout } = useAuth()
const { theme, toggleTheme } = useAppearance()
const { showToast } = useWorkspace()
// Workbench pages fill the window edge to edge; their context goes into the header.
const workbenchRoute = computed(() => ['/annotate', '/review'].includes(routePath.value))
const stage = computed(() => ({ '/annotate': 'annotate', '/review': 'review', '/results': 'results' } as Record<string, string>)[routePath.value] ?? null)
// Both stages render the same canvas: say so at the moment of switching, not only in the colours.
const stageEntered: Record<string, string> = { '/annotate': '已进入标注模式', '/review': '已进入审查模式：逐个确认目标，有错直接改' }
watch(routePath, path => { if (stageEntered[path]) showToast(stageEntered[path]) }, { flush: 'post' })
async function signOut() { if (await router.canLeave()) { await logout(); await router.replace('/login') } }
</script>
<template>
  <div class="app-shell" :class="{'mode-review':routePath==='/review'}">
    <header class="app-header" :class="{'is-workbench':workbenchRoute}">
      <div class="app-brand" title="微流控标注工作台 · 稀有精子识别与提取"><span class="brand-mark"><AppIcon name="layers" :size="18" /></span><h1>标注工作台</h1></div>
      <nav class="workflow-nav" :data-stage="stage" aria-label="页面">
        <i class="workflow-thumb" aria-hidden="true" />
        <button aria-label="人工标注" :class="{active:stage==='annotate'}" :aria-current="stage==='annotate'?'page':undefined" title="第 1 步：标注" @click="router.push('/annotate')"><span>1</span>标注</button>
        <button aria-label="审查模式" :class="{active:stage==='review'}" :aria-current="stage==='review'?'page':undefined" title="第 2 步：审查" @click="router.push('/review')"><span>2</span>审查</button>
        <button class="records-nav" aria-label="标注记录" :class="{active:stage==='results'}" :aria-current="stage==='results'?'page':undefined" title="谁标了、谁审了，以及统计导出" @click="router.push('/results')"><AppIcon name="records" :size="15" />记录</button>
      </nav>
      <div id="app-header-context" class="app-header-context" />
      <div class="app-account">
        <span class="app-account-sep" aria-hidden="true" />
        <button class="header-help" title="使用说明和快捷键 (?)" @click="openHelp"><AppIcon name="help" :size="16" /><span>帮助</span></button>
        <button class="icon-button" :aria-label="theme === 'light' ? '切换深色主题' : '切换浅色主题'" :title="theme === 'light' ? '深色主题' : '浅色主题'" @click="toggleTheme"><AppIcon :name="theme==='light'?'moon':'sun'" /></button>
        <span class="app-account-sep" aria-hidden="true" />
        <span class="user-chip" :title="user?.name"><span class="user-avatar" aria-hidden="true">{{ user?.name?.slice(0, 1) }}</span><span class="user-name">{{ user?.name }}</span></span><button class="quiet-button" @click="signOut">退出</button>
      </div>
    </header>
    <main class="app-main" :class="{flush:workbenchRoute}"><slot /></main>
    <HelpDialog :page="routePath" />
  </div>
</template>
