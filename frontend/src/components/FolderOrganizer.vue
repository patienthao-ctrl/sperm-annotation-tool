<script setup lang="ts">
import { computed, ref } from 'vue'
import { trackApi } from '../api/trackApi'
import { useWorkspace } from '../stores/workspace'

// Moving and renaming only change where a video is listed; its files and records stay as they are.
const emit = defineEmits<{ close: [] }>()
const { mediaAssets, refreshLibrary } = useWorkspace()
const selected = ref<string[]>([]), target = ref(''), oldPath = ref(''), newPath = ref(''), filter = ref('')
const busy = ref(false), error = ref('')
const videos = computed(() => mediaAssets.value.filter(media => media.serverMediaId && (!filter.value || (filter.value === '\u0000' ? !media.folderPath : media.folderPath === filter.value || media.folderPath?.startsWith(`${filter.value}/`)))))
const folders = computed(() => {
  const out = new Set<string>()
  for (const media of mediaAssets.value) {
    const parts = (media.folderPath ?? '').split('/').filter(Boolean)
    parts.forEach((_, index) => out.add(parts.slice(0, index + 1).join('/')))
  }
  return [...out].sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }))
})
const allSelected = computed(() => videos.value.length > 0 && videos.value.every(media => selected.value.includes(media.serverMediaId!)))
const toggleAll = () => { selected.value = allSelected.value ? [] : videos.value.map(media => media.serverMediaId!) }
async function act(work: () => Promise<unknown>) {
  busy.value = true; error.value = ''
  try { await work(); await refreshLibrary() }
  catch (e: any) { error.value = e?.message || '操作失败，请重试'; console.error('[library.organize_failed]', e) }
  finally { busy.value = false }
}
const move = () => act(async () => { await trackApi.moveToFolder(selected.value, target.value); selected.value = [] })
const rename = () => act(async () => { await trackApi.renameFolder(oldPath.value, newPath.value); oldPath.value = ''; newPath.value = '' })
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" @keydown.esc.prevent="!busy && emit('close')">
      <section class="app-dialog organizer" role="dialog" aria-modal="true" aria-label="整理文件夹" data-testid="folder-organizer">
        <h2>整理文件夹</h2>
        <p v-if="error" role="alert" class="text-[var(--danger)]">{{ error }}</p>
        <datalist id="library-folders"><option v-for="folder in folders" :key="folder" :value="folder" /></datalist>
        <label class="organizer-field">只看<select v-model="filter" aria-label="只看文件夹"><option value="">全部视频</option><option value="&#0;">未分类</option><option v-for="folder in folders" :key="folder" :value="folder">{{ folder }}</option></select></label>
        <label class="organizer-row organizer-all"><input type="checkbox" :checked="allSelected" @change="toggleAll" />全选（{{ videos.length }} 段）</label>
        <div class="organizer-list">
          <label v-for="media in videos" :key="media.id" class="organizer-row"><input v-model="selected" type="checkbox" :value="media.serverMediaId" /><span>{{ media.name }}</span><em>{{ media.folderPath || '未分类' }}</em></label>
        </div>
        <div class="organizer-form">
          <input v-model="target" class="input" list="library-folders" placeholder="移动到，如 2026-10 第一批/样本A；留空为未分类" aria-label="移动到文件夹" data-testid="organizer-target" />
          <button class="btn-primary" data-testid="organizer-move" :disabled="busy || !selected.length" @click="move">移动 {{ selected.length }} 段</button>
        </div>
        <div class="organizer-form">
          <select v-model="oldPath" aria-label="要重命名的文件夹" data-testid="organizer-rename-from"><option value="">选择要重命名的文件夹</option><option v-for="folder in folders" :key="folder" :value="folder">{{ folder }}</option></select>
          <input v-model="newPath" class="input" placeholder="新名称（完整路径）" aria-label="文件夹新名称" data-testid="organizer-rename-to" />
          <button class="btn-secondary" data-testid="organizer-rename" :disabled="busy || !oldPath || !newPath.trim()" @click="rename">重命名</button>
        </div>
        <p class="review-note">只改变列表里的位置，不影响视频文件、标注和审查状态。</p>
        <div class="mt-4 flex justify-end"><button class="btn-secondary" :disabled="busy" @click="emit('close')">关闭</button></div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.organizer { width: 620px; max-width: 100%; max-height: 90vh; display: flex; flex-direction: column; gap: 8px; }
.organizer-field { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
.organizer select { flex: 1; min-width: 0; background: var(--surface-subtle); border: 1px solid var(--line); border-radius: 5px; padding: 6px 8px; color: var(--text); }
.organizer-list { flex: 1; min-height: 80px; overflow-y: auto; border: 1px solid var(--line); border-radius: 5px; padding: 4px 8px; }
.organizer-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 13px; }
.organizer-row span { flex: 1; overflow-wrap: anywhere; }
.organizer-row em { color: var(--muted); font-style: normal; font-size: 12px; overflow-wrap: anywhere; }
.organizer-form { display: flex; flex-wrap: wrap; gap: 8px; }
.organizer-form .input { flex: 1 1 200px; min-width: 0; }
</style>
