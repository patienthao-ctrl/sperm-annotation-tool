<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import AppLayout from './layouts/AppLayout.vue'
import AnnotatePage from './pages/AnnotatePage.vue'
import ResultsPage from './pages/ResultsPage.vue'
import EffectsPage from './pages/EffectsPage.vue'
import LoginPage from './pages/LoginPage.vue'
import { useRouter } from './router'
import { useAuth } from './stores/auth'
import { useWorkspace } from './stores/workspace'

const router = useRouter()
const { isAuthenticated } = useAuth()
const { loadServerMedia } = useWorkspace()

const currentPath = computed(() => {
  if (!isAuthenticated.value) return '/login'
  return router.path.value === '/login' ? '/annotate' : router.path.value
})

const syncRoute = () => {
  if (!isAuthenticated.value && router.path.value !== '/login') router.replace('/login')
  if (isAuthenticated.value && router.path.value === '/login') router.replace('/annotate')
  if (isAuthenticated.value) loadServerMedia()
}

onMounted(syncRoute)
watch(isAuthenticated, syncRoute)
</script>

<template>
  <LoginPage v-if="currentPath === '/login'" />
  <AppLayout v-else>
    <AnnotatePage v-if="currentPath === '/annotate'" />
    <ResultsPage v-else-if="currentPath === '/results'" />
    <EffectsPage v-else-if="currentPath === '/effects'" />
  </AppLayout>
</template>
