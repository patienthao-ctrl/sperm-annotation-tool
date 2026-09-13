
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from '../router'
import { useAuth } from '../stores/auth'

const router = useRouter()
const { login, register } = useAuth()

const isRegisterMode = ref(false)
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

const canSubmit = computed(() => {
  if (!username.value.trim() || !password.value) return false
  if (isRegisterMode.value && password.value !== confirmPassword.value) return false
  return true
})

const toggleMode = () => {
  isRegisterMode.value = !isRegisterMode.value
  error.value = ''
  confirmPassword.value = ''
}

const submit = async () => {
  error.value = ''

  if (!username.value.trim() || !password.value) {
    error.value = '请输入账号和密码'
    return
  }
  if (isRegisterMode.value) {
    if (password.value.length < 6) {
      error.value = '密码至少 6 位'
      return
    }
    if (password.value !== confirmPassword.value) {
      error.value = '两次输入的密码不一致'
      return
    }
  }

  loading.value = true
  try {
    if (isRegisterMode.value) {
      await register(username.value, password.value)
    } else {
      await login(username.value, password.value)
    }
    router.replace('/annotate')
  } catch (err) {
    error.value = (err as any)?.message || '操作失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-slate-950 px-5 text-slate-100">
    <div class="mx-auto flex min-h-screen max-w-md items-center justify-center">
      <section class="panel w-full p-8">
        <div class="mb-8">
          <div class="mb-4 inline-flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-300">RS</div>
          <h1 class="text-xl font-bold">{{ isRegisterMode ? '注册账号' : '登录系统' }}</h1>
          <p class="mt-2 text-xs leading-5 text-slate-500">
            {{ isRegisterMode ? '注册后自动登录，进入稀有精子识别与标注工作台。' : '登录后进入稀有精子识别与标注工作台。' }}
          </p>
        </div>
        <form class="space-y-4" @submit.prevent="submit">
          <label class="block">
            <span class="mb-2 block text-xs text-slate-400">账号</span>
            <input v-model="username" class="input w-full" autocomplete="username" placeholder="请输入账号" />
          </label>
          <label class="block">
            <span class="mb-2 block text-xs text-slate-400">密码</span>
            <input v-model="password" class="input w-full" type="password" :autocomplete="isRegisterMode ? 'new-password' : 'current-password'" placeholder="请输入密码" />
          </label>
          <label v-if="isRegisterMode" class="block">
            <span class="mb-2 block text-xs text-slate-400">确认密码</span>
            <input v-model="confirmPassword" class="input w-full" type="password" autocomplete="new-password" placeholder="请再次输入密码" />
          </label>
          <p v-if="error" class="rounded-lg border border-red-400/20 bg-red-400/5 px-3 py-2 text-xs text-red-300">{{ error }}</p>
          <button class="btn-primary w-full" :disabled="loading || !canSubmit">
            {{ loading ? '处理中…' : isRegisterMode ? '注册并登录' : '登录' }}
          </button>
        </form>

        <div class="mt-6 flex items-center justify-center text-xs">
          <span class="text-slate-500">{{ isRegisterMode ? '已有账号？' : '还没有账号？' }}</span>
          <button type="button" class="ml-1 text-indigo-300 hover:underline" @click="toggleMode">
            {{ isRegisterMode ? '去登录' : '立即注册' }}
          </button>
        </div>

        <div class="mt-4 rounded-lg border border-slate-800 bg-slate-950/70 p-3 text-[10px] leading-5 text-slate-500">
          账号与密码由后端校验并加密存储，标注结果会自动记录标注人。如忘记密码，请联系管理员。
        </div>
      </section>
    </div>
  </div>
</template>
