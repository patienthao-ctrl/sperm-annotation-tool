<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useWorkspace } from '../stores/workspace'
import { useAuth } from '../stores/auth'
import { listResults } from '../api/httpAnnotationApi'
import type { AnnotationObject, SavedAnnotationFile } from '../types/annotation'

const { savedResults, formatTime } = useWorkspace()
const { user: currentUser } = useAuth()

/** 结果行 = 本地保存记录 + 可选的标注人账号 */
interface Row extends SavedAnnotationFile {
  username?: string | null
}

interface RemoteRow {
  id: number
  user_id: number
  username: string | null
  batch_id: string | null
  object_id: string | null
  media_id: string
  media_name: string | null
  media_type: string | null
  frame_index: number
  timestamp_ms: number
  object_name: string | null
  shape_type: string | null
  pct_x: number | null
  pct_y: number | null
  pct_w: number | null
  pct_h: number | null
  created_at: string
}

/**
 * 挂载时从后端拉取历史标注，合并进结果列表。
 * 这样刷新页面、换浏览器都能看到以前标过的内容，而不是只显示本次会话的内存数据。
 */
onMounted(async () => {
  try {
    const res = await listResults('default')
    const rows = (res.items ?? []) as unknown as RemoteRow[]

    // 后端每条记录 = 一个标注对象；按批次号聚合回一次保存记录
    // （batch_id 为空的旧数据退化为「素材 + 落库时间」聚合，保证兼容）
    const groups = new Map<string, RemoteRow[]>()
    for (const row of rows) {
      const key = row.batch_id || `${row.media_id}@${row.created_at}`
      groups.set(key, [...(groups.get(key) ?? []), row])
    }

    const merged = Array.from(groups.values()).map((list) => {
      const head = list[0]
      const isVideo = head.media_type === 'video'

      const objects: AnnotationObject[] = list.map((r) => {
        const isBox = r.shape_type === 'bbox'
        return {
          id: r.object_id ?? String(r.id),
          name: r.object_name ?? '未命名目标',
          source: 'manual',
          bbox:
            isBox && r.pct_x !== null
              ? { x: r.pct_x, y: r.pct_y ?? 0, width: r.pct_w ?? 0, height: r.pct_h ?? 0 }
              : undefined,
          point:
            !isBox && r.pct_x !== null
              ? { x: r.pct_x, y: r.pct_y ?? 0 }
              : undefined,
          frameIndex: isVideo ? r.frame_index : undefined,
          timestampMs: isVideo ? r.timestamp_ms : undefined,
        } as AnnotationObject
      })

      return {
        mediaId: head.media_id,
        mediaName: head.media_name ?? head.media_id,
        // ↓ 标注人：后端 LEFT JOIN users 带出的账号
        username: head.username ?? (head.user_id ? `用户#${head.user_id}` : null),
        mediaType: (isVideo ? 'video' : 'image') as 'video' | 'image',
        frameIndex: isVideo ? head.frame_index : undefined,
        timestampMs: isVideo ? head.timestamp_ms : undefined,
        savedAt: new Date(head.created_at.replace(' ', 'T')).toISOString(),
        filename: '后端记录',
        objects,
      }
    })

    // 后端按 id 倒序返回，这里再反一次让最新的排最前
    savedResults.value = [...merged.reverse(), ...savedResults.value] as SavedAnnotationFile[]
  } catch (e) {
    console.warn('从后端加载标注结果失败：', e)
  }
})

/** 统一按带标注人的行类型渲染 */
const rows = computed<Row[]>(() => savedResults.value as Row[])
</script>

<template>
      <section  class="flex min-h-0 flex-1 flex-col gap-4">
        <div>
          <h2 class="text-lg font-semibold">标注结果</h2>
          <p class="text-xs text-slate-500">视频标注记录统一为第一帧（frameIndex = 0）；保存时会生成“第一帧带标注、后续帧保持原视频”的 WebM 文件。</p>
        </div>
        <div class="panel min-h-0 flex-1 overflow-auto">
          <table class="w-full border-collapse text-left text-xs">
            <thead class="sticky top-0 bg-slate-900">
              <tr class="border-b border-slate-800 text-slate-400">
                <th class="px-4 py-3">文件</th><th class="px-4 py-3">类型</th><th class="px-4 py-3">帧</th><th class="px-4 py-3">时间</th><th class="px-4 py-3">对象</th><th class="px-4 py-3">标注人</th><th class="px-4 py-3">保存文件</th><th class="px-4 py-3">保存时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in rows" :key="item.savedAt + item.mediaId" class="border-b border-slate-900">
                <td class="px-4 py-3">{{ item.mediaName }}</td>
                <td class="px-4 py-3">{{ item.mediaType === 'video' ? '视频第一帧' : '图片' }}</td>
                <td class="px-4 py-3">{{ item.frameIndex ?? '-' }}</td>
                <td class="px-4 py-3">{{ item.timestampMs !== undefined ? formatTime(item.timestampMs / 1000) : '-' }}</td>
                <td class="px-4 py-3">{{ item.objects.map((obj) => obj.name).join('、') }}</td>
                <td class="px-4 py-3">
                  <span v-if="item.username" class="rounded bg-indigo-500/15 px-2 py-0.5 text-indigo-300">{{ item.username }}</span>
                  <span v-else class="text-slate-600">{{ currentUser?.name ?? '-' }}</span>
                </td>
                <td class="px-4 py-3 text-indigo-300">{{ item.filename }}</td>
                <td class="px-4 py-3 text-slate-500">{{ new Date(item.savedAt).toLocaleString() }}</td>
              </tr>
              <tr v-if="!savedResults.length"><td colspan="8" class="px-4 py-20 text-center text-slate-600">暂无保存结果</td></tr>
            </tbody>
          </table>
        </div>
      </section>
</template>
