<script setup lang="ts">
import { onMounted } from 'vue'
import { useWorkspace } from '../stores/workspace'

const {
  effectResults, selectedEffectId, selectedEffect, effectVideoRef, effectTime,
  effectPlaying, effectOverlayObjects, openEffectFolderPicker, handleEffectFolder,
  loadEffects, onEffectTimeUpdate, toggleEffectPlayback, selectEffect, formatTime,
} = useWorkspace()

onMounted(loadEffects)
</script>

<template>
      <section  class="flex min-h-0 flex-1 flex-col gap-4">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-lg font-semibold">效果查看</h2>
            <p class="text-xs text-slate-500">查看处理结果的前端展示效果。当前使用本地 Mock 数据，完全不依赖后端。</p>
          </div>
          <div class="flex items-center gap-2">
            <input ref="effectFolderInputRef" type="file" accept="video/*" webkitdirectory directory multiple class="hidden" @change="handleEffectFolder(($event.target as HTMLInputElement).files)" />
            <button class="btn-secondary" @click="openEffectFolderPicker">选择效果视频文件夹</button>
            <button class="btn-secondary" @click="loadEffects">刷新效果列表</button>
          </div>
        </div>

        <div class="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)_320px] gap-4">
          <aside class="panel min-h-0 overflow-hidden">
            <div class="panel-title"><span>处理结果</span><span class="badge">{{ effectResults.length }}</span></div>
            <div class="h-[calc(100%-49px)] space-y-2 overflow-y-auto p-3">
              <button
                v-for="effect in effectResults"
                :key="effect.id"
                class="w-full rounded-xl border p-3 text-left transition"
                :class="selectedEffectId === effect.id ? 'border-indigo-400 bg-indigo-500/10' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'"
                @click="selectEffect(effect.id)"
              >
                <div class="truncate text-xs font-semibold">{{ effect.resultName }}</div>
                <div class="mt-2 text-[10px] text-slate-500">源文件：{{ effect.sourceMediaName }}</div>
                <div class="mt-1 text-[10px] text-emerald-300">{{ effect.status === 'completed' ? '处理完成' : effect.status }}</div>
              </button>
              <div v-if="!effectResults.length" class="p-8 text-center text-xs text-slate-600">暂无处理结果</div>
            </div>
          </aside>

          <section class="panel flex min-h-0 flex-col overflow-hidden">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-800 px-4 py-3">
              <div>
                <h3 class="text-sm font-semibold">{{ selectedEffect?.resultName || '暂无效果' }}</h3>
                <p class="mt-1 text-[10px] text-slate-500">处理视频回放 + 目标识别效果叠加（Demo）</p>
              </div>
              <span v-if="selectedEffect" class="rounded-full bg-emerald-500/10 px-2 py-1 text-[10px] text-emerald-300">{{ selectedEffect.status }}</span>
            </div>
            <div class="relative flex min-h-0 flex-1 items-center justify-center bg-black p-5">
              <video v-if="selectedEffect" ref="effectVideoRef" :src="selectedEffect.resultVideoUrl" class="max-h-full max-w-full rounded-lg object-contain" controls @timeupdate="onEffectTimeUpdate" @ended="effectPlaying = false" />
              <svg v-if="selectedEffect" viewBox="0 0 100 100" preserveAspectRatio="none" class="pointer-events-none absolute inset-5 h-[calc(100%-2.5rem)] w-[calc(100%-2.5rem)]">
                <g v-for="obj in effectOverlayObjects" :key="obj.id">
                  <rect :x="obj.x" :y="obj.y" :width="obj.width" :height="obj.height" fill="rgba(34,197,94,.08)" stroke="#4ade80" stroke-width="0.35" />
                  <text :x="obj.x" :y="Math.max(2, obj.y - 1)" fill="#86efac" font-size="2.2">rare sperm</text>
                </g>
              </svg>
              <div v-if="!selectedEffect" class="text-sm text-slate-600">选择左侧处理结果</div>
            </div>
            <div v-if="selectedEffect" class="flex shrink-0 items-center justify-between border-t border-slate-800 px-4 py-3">
              <div class="text-[11px] text-slate-400">当前时间 {{ formatTime(effectTime) }}</div>
              <button class="video-btn primary" @click="toggleEffectPlayback">{{ effectPlaying ? '暂停效果' : '播放效果' }}</button>
            </div>
          </section>

          <aside class="panel p-4">
            <h3 class="text-sm font-semibold">处理统计</h3>
            <div v-if="selectedEffect" class="mt-4 grid grid-cols-1 gap-3">
              <div class="stat-card"><span>识别目标</span><strong>{{ selectedEffect.summary.detectedObjects }}</strong></div>
              <div class="stat-card"><span>提取候选</span><strong>{{ selectedEffect.summary.extractedCandidates }}</strong></div>
              <div class="stat-card"><span>视频时长</span><strong>{{ selectedEffect.summary.durationSeconds }}s</strong></div>
              <div class="stat-card"><span>处理时间</span><strong class="text-xs">{{ new Date(selectedEffect.createdAt).toLocaleString() }}</strong></div>
            </div>
            <div  class="mt-8 text-center text-xs text-slate-600">暂无统计</div>
          </aside>
        </div>
      </section>
</template>
