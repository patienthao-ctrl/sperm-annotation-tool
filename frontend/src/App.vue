<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import AppLayout from './layouts/AppLayout.vue'
import AnnotatePage from './pages/AnnotatePage.vue'
import ResultsPage from './pages/ResultsPage.vue'
import LoginPage from './pages/LoginPage.vue'
import { useRouter } from './router'
import { useAuth } from './stores/auth'
import { useWorkspace } from './stores/workspace'

const router = useRouter()
const { isAuthenticated, restoreSession } = useAuth()
const { loadServerMedia } = useWorkspace()
const sessionReady = ref(false)
const sessionError = ref('')
const restoreLogin = async () => {
  sessionError.value = ''
  try {
    await restoreSession()
    sessionReady.value = true
    syncRoute()
  } catch (error) {
    sessionError.value = (error as {message?: string})?.message || '登录状态读取失败，请重试'
  }
}

const currentPath = computed(() => {
  if (!isAuthenticated.value) return '/login'
  return router.path.value === '/login' ? '/annotate' : router.path.value
})

const syncRoute = () => {
  if (!sessionReady.value) return
  if (!isAuthenticated.value && router.path.value !== '/login') router.replace('/login')
  if (isAuthenticated.value && router.path.value === '/login') router.replace('/annotate')
  if (isAuthenticated.value) loadServerMedia()
}

onMounted(restoreLogin)
watch(isAuthenticated, syncRoute)
</script>

<template>
  <div v-if="!sessionReady" class="session-loading" role="status">
    <p>{{ sessionError || '正在验证登录状态…' }}</p>
    <button v-if="sessionError" class="btn-primary" @click="restoreLogin">重新验证登录</button>
  </div>
  <LoginPage v-else-if="currentPath === '/login'" />
  <AppLayout v-else>
    <!-- One instance for both stages: same canvas and frame, so switching does not reload the video. -->
    <AnnotatePage v-if="currentPath === '/annotate' || currentPath === '/review'" :mode="currentPath === '/review' ? 'review' : 'annotate'" />
    <ResultsPage v-else-if="currentPath === '/results'" />
  </AppLayout>
</template>

<style scoped>
.session-loading { min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; color: var(--text); background: var(--app-bg); }
</style>
