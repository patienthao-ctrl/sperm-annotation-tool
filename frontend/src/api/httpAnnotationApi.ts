/**
 * 真实后端实现 —— README 里说的「后续新增 httpAnnotationApi.ts」就是这个文件
 *
 * 采用【混合模式】：
 *   - 登录 + 保存人工标注  → 走真后端（本次要做的）
 *   - segment / track / effects 等 AI 接口 → 仍走 mockAnnotationApi
 *
 * 为什么要混合：效果查看页用的是本地 Mock 视频，后端还没有 AI 模块。
 * 如果整个换成 http 版，不启动后端时效果查看页会直接报错。
 * 以后后端每接入一个模块，把下面对应的那行换成真实实现即可。
 */
import { mockAnnotationApi } from './annotationApi'
import type { AnnotationApi, SaveManualAnnotationRequest } from './annotationApi'
import { http } from './http'

/* ------------------------------ 标注保存 ------------------------------ */

/**
 * 保存人工标注
 *
 * 注意：标注人由后端从 token 解析，这里不需要、也不能传 userId
 * —— 这就是「标注结果表加上标注人字段」的落法
 */
const saveManualAnnotation = (input: SaveManualAnnotationRequest): Promise<{ id: string }> =>
  http.post<{ id: string }>('/annotation/annotations/manual', input)

/** 查询当前登录用户的标注结果（标注结果页可用） */
export const listResults = (projectId = 'default', mediaId?: string) =>
  http.get<{ items: unknown[]; total: number }>(
    `/annotation/projects/${projectId}/results${mediaId ? `?mediaId=${encodeURIComponent(mediaId)}` : ''}`,
  )

/** 按素材查询当前用户的标注 */
export const listAnnotationsByMedia = (mediaId: string) =>
  http.get<{ items: unknown[]; total: number }>(`/annotation/media/${encodeURIComponent(mediaId)}`)

/** 删除一条自己的标注 */
export const deleteAnnotation = (id: number) => http.del<{ ok: boolean }>(`/annotation/${id}`)

/* -------------------------- 组装 AnnotationApi -------------------------- */

export const httpAnnotationApi: AnnotationApi = {
  // ↓ 已接后端
  saveManualAnnotation,

  // ↓ 后端还没有这些模块，继续用 Mock；后端实现后逐个替换即可
  uploadMedia: mockAnnotationApi.uploadMedia,
  segment: mockAnnotationApi.segment,
  track: mockAnnotationApi.track,
  getTask: mockAnnotationApi.getTask,
  listEffects: mockAnnotationApi.listEffects,
  getEffect: mockAnnotationApi.getEffect,
}
