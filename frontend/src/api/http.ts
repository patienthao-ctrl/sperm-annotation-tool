/**
 * 后端 HTTP 客户端（基于 fetch，不引入 axios 等新依赖）
 *
 * 所有请求走 /api 前缀，由 vite.config.ts 的 proxy 转发到 http://localhost:3000，
 * 所以前端不用写死后端地址，也没有跨域问题。
 */

const BASE = '/api'
export const TOKEN_KEY = 'rare-sperm-token'

/** token 存取 */
export const tokenStore = {
  get: (): string => localStorage.getItem(TOKEN_KEY) ?? '',
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export interface ApiError {
  message: string
  status: number
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = tokenStore.get()

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers ?? {}),
      },
    })
  } catch {
    // 后端没启动 / 网络不通
    throw { message: '无法连接后端，请确认 FastAPI 已启动（python -m uvicorn app.main:app --host 127.0.0.1 --port 3000）', status: 0 } as ApiError
  }

  const data = await res.json().catch(() => ({}))

  if (!res.ok) {
    if (res.status === 401) {
      // 登录失效：清掉本地 token，广播事件让 auth store 把用户踢下线
      tokenStore.clear()
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    throw { message: (data as any)?.message ?? `请求失败 (${res.status})`, status: res.status } as ApiError
  }

  return data as T
}

export const http = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),

  /** 下载二进制文件（zip / 图片等） */
  postBlob: async (path: string, body?: unknown): Promise<Blob> => {
    const token = tokenStore.get()
    let res: Response
    try {
      res = await fetch(`${BASE}${path}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw { message: '无法连接后端，请确认 FastAPI 已启动', status: 0 } as ApiError
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw { message: (data as any)?.message ?? `请求失败 (${res.status})`, status: res.status } as ApiError
    }
    return res.blob()
  },
}
