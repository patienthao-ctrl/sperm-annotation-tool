import { computed, ref } from 'vue'
import { http, tokenStore } from '../api/http'

export interface AuthUser {
  id: string
  name: string
  role: string
}

const STORAGE_KEY = 'rare-sperm-auth'

const readUser = (): AuthUser | null => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

const user = ref<AuthUser | null>(typeof window !== 'undefined' ? readUser() : null)

/**
 * 登录：原为前端 Mock，现改为调用真实后端 POST /api/auth/login
 *
 * 页面（LoginPage.vue）不用改 —— 它只关心 login() 成功跳转、失败抛错
 */
const login = async (username: string, password: string): Promise<AuthUser> => {
  if (!username.trim() || !password) throw new Error('请输入账号和密码')

  const res = await http.post<{ token: string; user: { id: number; username: string } }>('/auth/login', {
    username: username.trim(),
    password,
  })

  const nextUser: AuthUser = {
    id: String(res.user.id),
    name: res.user.username,
    role: 'annotator',
  }

  tokenStore.set(res.token)
  user.value = nextUser
  localStorage.setItem(STORAGE_KEY, JSON.stringify(nextUser))
  return nextUser
}

/**
 * 注册：后端 POST /api/auth/register（登录页暂未用到，先留着）
 */
const register = async (username: string, password: string): Promise<AuthUser> => {
  const res = await http.post<{ token: string; user: { id: number; username: string } }>('/auth/register', {
    username: username.trim(),
    password,
  })

  const nextUser: AuthUser = {
    id: String(res.user.id),
    name: res.user.username,
    role: 'annotator',
  }

  tokenStore.set(res.token)
  user.value = nextUser
  localStorage.setItem(STORAGE_KEY, JSON.stringify(nextUser))
  return nextUser
}

const logout = () => {
  user.value = null
  localStorage.removeItem(STORAGE_KEY)
  tokenStore.clear()
}

/**
 * 启动时校验本地 token 是否还有效（防止 token 过期后页面还显示已登录）
 * 在 App.vue 的 onMounted 里调用即可
 */
const restoreSession = async (): Promise<boolean> => {
  if (!tokenStore.get()) return false
  try {
    const me = await http.get<{ id: number; username: string }>('/auth/me')
    user.value = { id: String(me.id), name: me.username, role: 'annotator' }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(user.value))
    return true
  } catch {
    logout()
    return false
  }
}

// 请求返回 401 时，http.ts 会广播这个事件，这里负责把用户踢下线
if (typeof window !== 'undefined') {
  window.addEventListener('auth:expired', () => {
    user.value = null
    localStorage.removeItem(STORAGE_KEY)
  })
}

export const useAuth = () => ({
  user,
  isAuthenticated: computed(() => Boolean(user.value)),
  login,
  register,
  logout,
  restoreSession,
})
