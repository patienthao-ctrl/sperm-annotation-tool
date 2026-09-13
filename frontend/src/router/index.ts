import { ref } from 'vue'
import { useAuth } from '../stores/auth'

export type RoutePath = '/login' | '/annotate' | '/results' | '/effects'

const normalizePath = (path: string): RoutePath => {
  if (path === '/login' || path === '/results' || path === '/effects') return path
  return '/annotate'
}

const path = ref<RoutePath>(typeof window !== 'undefined' ? normalizePath(window.location.pathname) : '/login')

const navigate = (to: RoutePath, replace = false) => {
  const { isAuthenticated } = useAuth()
  const target = to === '/login' || isAuthenticated.value ? to : '/login'
  if (typeof window !== 'undefined') {
    const method = replace ? 'replaceState' : 'pushState'
    window.history[method]({}, '', target)
  }
  path.value = target
}

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', () => {
    const requested = normalizePath(window.location.pathname)
    const { isAuthenticated } = useAuth()
    path.value = requested !== '/login' && !isAuthenticated.value ? '/login' : requested
  })
}

export const useRouter = () => ({
  path,
  push: (to: RoutePath) => navigate(to),
  replace: (to: RoutePath) => navigate(to, true),
})
