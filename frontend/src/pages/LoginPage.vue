
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from '../router'
import { useAuth } from '../stores/auth'
import { useAppearance } from '../stores/appearance'
import AppIcon from '../components/AppIcon.vue'
import LoginScene from '../components/LoginScene.vue'
const { theme, toggleTheme } = useAppearance()

const router = useRouter()
const { login, register } = useAuth()

const isRegisterMode = ref(false)
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')
const showPassword = ref(false)

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
  <div class="login-page">
    <LoginScene :theme="theme" />
    <header class="login-brand">
      <span class="login-mark"><AppIcon name="layers" :size="22" /></span>
      <div><h1>微流控标注工作台</h1><p>稀有精子识别与提取</p></div>
    </header>
    <button class="icon-button login-theme" :aria-label="theme==='light'?'切换到深色':'切换到浅色'" :title="theme==='light'?'深色':'浅色'" @click="toggleTheme"><AppIcon :name="theme==='light'?'moon':'sun'" :size="18" /></button>
    <main class="login-main">
      <div class="login-spacer" aria-hidden="true" />
      <section class="login-card" aria-labelledby="login-title">
        <h2 id="login-title">{{ isRegisterMode ? '注册' : '登录' }}</h2>
        <form class="login-form" @submit.prevent="submit">
          <label>
            <span>账号</span>
            <input v-model="username" class="input w-full" autocomplete="username" placeholder="请输入账号" />
          </label>
          <label>
            <span>密码</span>
            <span class="login-password"><input v-model="password" class="input w-full" :type="showPassword ? 'text' : 'password'" :autocomplete="isRegisterMode ? 'new-password' : 'current-password'" placeholder="请输入密码" /><button type="button" class="icon-button" :class="{active:showPassword}" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" @click="showPassword = !showPassword"><AppIcon name="eye" :size="16" /></button></span>
          </label>
          <label v-if="isRegisterMode">
            <span>确认密码</span>
            <input v-model="confirmPassword" class="input w-full" :type="showPassword ? 'text' : 'password'" autocomplete="new-password" placeholder="请再次输入密码" />
          </label>
          <p v-if="error" class="error-banner login-error" role="alert">{{ error }}</p>
          <button class="btn-primary w-full login-submit" :disabled="loading || !canSubmit">
            {{ loading ? '请稍候…' : isRegisterMode ? '注册并登录' : '登录' }}
          </button>
        </form>
        <div class="login-mode">
          <span>{{ isRegisterMode ? '已有账号？' : '还没有账号？' }}</span>
          <button type="button" @click="toggleMode">{{ isRegisterMode ? '去登录' : '注册' }}</button>
        </div>
        <p v-if="!isRegisterMode" class="login-support">忘记密码请联系管理员</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
/* The scene is a fixed canvas behind everything; the card sits to its right (below it on narrow screens). */
.login-page{position:relative;min-height:100vh;color:var(--text);background:var(--app-bg);overflow-x:hidden}
.login-brand{position:absolute;z-index:2;left:32px;top:28px;display:flex;align-items:center;gap:12px;animation:login-fade .8s var(--ease-out) .25s both}
.login-mark{display:grid;place-items:center;width:40px;height:40px;border-radius:11px;color:var(--on-accent);background:linear-gradient(140deg,var(--accent-hover),var(--accent));box-shadow:inset 0 1px 0 #ffffff30,0 8px 20px -8px var(--accent-ring)}
.login-brand h1{margin:0;font-size:17px;font-weight:700;letter-spacing:.04em;line-height:1.35}
.login-brand p{margin:2px 0 0;font-size:12px;color:var(--muted);letter-spacing:.12em}
.login-theme{position:absolute;z-index:2;right:24px;top:26px;animation:login-fade .8s var(--ease-out) .25s both}
.login-main{position:relative;z-index:1;min-height:100vh;display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,380px);align-items:center;padding:96px max(40px,7vw) 48px 24px}
.login-card{width:100%;padding:30px 28px 22px;border-radius:var(--radius-xl);border:1px solid var(--line);background:var(--surface);background:color-mix(in srgb,var(--surface) 86%,transparent);-webkit-backdrop-filter:blur(14px) saturate(1.2);backdrop-filter:blur(14px) saturate(1.2);box-shadow:var(--shadow-lg);animation:login-card .8s var(--ease-out) .55s both}
.login-card h2{margin:0 0 22px;font-size:22px;font-weight:700;letter-spacing:.06em}
.login-form{display:flex;flex-direction:column;gap:16px}
.login-form label{display:flex;flex-direction:column;gap:7px;min-width:0;font-size:13px;font-weight:550}
.login-form .input{min-height:42px;font-size:14px}
.login-submit{min-height:44px;font-size:15px;margin-top:6px;letter-spacing:.12em}
.login-password{position:relative;display:block}.login-password .input{padding-right:44px}
.login-password .icon-button{position:absolute;right:5px;top:50%;margin-top:-16px}
.login-error{margin:0;overflow-wrap:anywhere;font-size:13px;line-height:1.6}
.login-mode{display:flex;align-items:center;justify-content:center;gap:4px;margin-top:18px;font-size:13px;color:var(--muted)}
.login-mode button{color:var(--accent);font-weight:600;padding:4px;border-radius:4px}.login-mode button:hover{text-decoration:underline;text-underline-offset:3px}
.login-support{margin:6px 0 0;text-align:center;color:var(--muted);font-size:12px;opacity:.8}
@keyframes login-fade{from{opacity:0}to{opacity:1}}
@keyframes login-card{from{opacity:0;transform:translateY(14px) scale(.985)}to{opacity:1;transform:none}}
@media(max-width:859px){
  .login-main{display:flex;flex-direction:column;align-items:center;padding:0 20px 32px}
  .login-spacer{flex:none;height:calc(min(16vh,38vw) * 2 + 116px)}
  .login-card{max-width:380px}
  .login-brand{left:20px;top:18px}.login-brand p{display:none}
  .login-theme{right:14px;top:16px}
}
@media(prefers-reduced-motion:reduce){.login-brand,.login-theme,.login-card{animation:none}}
</style>
