<script setup lang="ts">
import { useRouter } from '../router'
import { useAuth } from '../stores/auth'

const router = useRouter()
const { user, logout } = useAuth()
const routePath = router.path
</script>

<template>
  <div class="min-h-screen w-full bg-slate-950 text-slate-100">
    <header class="sticky top-0 z-30 h-16 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
      <div class="flex h-full w-full items-center justify-between px-6 2xl:px-8">
        <div class="min-w-0">
          <h1 class="text-base font-bold tracking-wide">微流控稀有精子识别与提取</h1>
          <p class="mt-0.5 text-[11px] text-slate-400">PC 端人工标注、AI Mock 与效果查看 · 模块化页面架构</p>
        </div>
        <div class="flex items-center gap-4">
          <nav class="flex items-center rounded-xl border border-slate-700 bg-slate-900 p-1">
            <button class="nav-btn" :class="routePath === '/annotate' ? 'nav-btn-active' : ''" @click="router.push('/annotate')">人工标注</button>
            <button class="nav-btn" :class="routePath === '/results' ? 'nav-btn-active' : ''" @click="router.push('/results')">标注结果</button>
            <button class="nav-btn" :class="routePath === '/effects' ? 'nav-btn-active' : ''" @click="router.push('/effects')">效果查看</button>
          </nav>
          <div class="flex items-center gap-2 border-l border-slate-800 pl-4">
            <span class="text-xs text-slate-400">{{ user?.name }}</span>
            <button class="btn-secondary" @click="logout(); router.replace('/login')">退出</button>
          </div>
        </div>
      </div>
    </header>
    <main class="flex min-h-[calc(100vh-64px)] w-full flex-col px-5 py-5 2xl:px-8">
      <slot />
    </main>
  </div>
</template>
