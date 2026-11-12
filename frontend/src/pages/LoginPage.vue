
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from '../router'
import { useAuth } from '../stores/auth'
import { useAppearance } from '../stores/appearance'
import AppIcon from '../components/AppIcon.vue'
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
    <button class="quiet-button login-theme" @click="toggleTheme"><AppIcon :name="theme==='light'?'moon':'sun'"/>{{ theme==='light'?'深色模式':'浅色模式' }}</button>
    <main class="login-shell">
      <header class="login-intro">
        <span class="login-mark"><AppIcon name="layers" :size="29" /></span>
        <div><h1>微流控标注工作台</h1><p class="login-subject">稀有精子识别与提取</p></div>
        <p class="login-purpose">AI 自动预标，人工查漏和确认，<br>审查通过后导出训练数据。</p>
        <ol class="login-flow" aria-label="数据制作流程">
          <li><AppIcon name="box" :size="17" /><strong>标注</strong><small>AI 预标，人工补上漏掉的</small></li>
          <li><AppIcon name="check" :size="17" /><strong>审查</strong><small>逐个确认目标，有错直接改</small></li>
          <li><AppIcon name="upload" :size="17" /><strong>导出</strong><small>审查通过的视频生成训练集</small></li>
        </ol>
      </header>
      <section class="panel login-card" aria-labelledby="login-title">
        <div class="login-card-heading">
          <h2 id="login-title">{{ isRegisterMode ? '注册账号' : '登录系统' }}</h2>
          <p>
            {{ isRegisterMode ? '注册后自动登录，进入稀有精子识别与标注工作台。' : '登录后进入稀有精子识别与标注工作台。' }}
          </p>
        </div>
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
          <button class="btn-primary w-full" :disabled="loading || !canSubmit">
            {{ loading ? '处理中…' : isRegisterMode ? '注册并登录' : '登录' }}
          </button>
        </form>

        <div class="login-mode">
          <span>{{ isRegisterMode ? '已有账号？' : '还没有账号？' }}</span>
          <button type="button" @click="toggleMode">
            {{ isRegisterMode ? '去登录' : '立即注册' }}
          </button>
        </div>

        <p class="login-support">标注记录会关联当前账号。忘记密码请联系管理员。</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
/* Faint microfluidic channels behind the page; pure CSS so it works offline. */
.login-page{position:relative;min-height:100vh;padding:80px 28px 40px;background:var(--app-bg);color:var(--text);overflow:hidden}
.login-page::before{content:'';position:absolute;inset:0;pointer-events:none;opacity:.55;background:
  radial-gradient(circle at 12% 18%,var(--accent-soft) 0,transparent 38%),
  radial-gradient(circle at 88% 82%,var(--accent-soft) 0,transparent 34%),
  repeating-linear-gradient(172deg,transparent 0 46px,var(--line) 46px 47px,transparent 47px 120px)}
.login-page::after{content:'';position:absolute;inset:0;pointer-events:none;background:linear-gradient(90deg,transparent 0,var(--app-bg) 62%)}
.login-shell{position:relative;z-index:1;min-height:calc(100vh - 120px);width:100%;max-width:960px;margin:auto;display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,400px);align-items:center;gap:72px}
.login-intro{display:grid;grid-template-columns:52px minmax(0,1fr);align-items:center;gap:14px;min-width:0}
.login-intro>*{animation:rise-in .5s var(--ease-out) both}
.login-intro>:nth-child(2){animation-delay:.05s}.login-intro>:nth-child(3){animation-delay:.1s}.login-intro>:nth-child(4){animation-delay:.16s}
.login-mark{display:grid;place-items:center;width:52px;height:52px;border-radius:14px;color:var(--on-accent);background:linear-gradient(140deg,var(--accent-hover),var(--accent));box-shadow:inset 0 1px 0 #ffffff30,0 8px 20px -6px var(--accent-ring)}
.login-intro h1{font-size:25px;font-weight:700;line-height:1.4;letter-spacing:.02em;margin:0}
.login-subject{margin:4px 0 0;font-size:13px;line-height:1.6;color:var(--muted);letter-spacing:.06em}
.login-purpose{grid-column:1/-1;font-size:21px;font-weight:500;line-height:1.8;margin:30px 0 14px;letter-spacing:.02em}
.login-flow{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0;padding:0;list-style:none;counter-reset:login-step}
.login-flow li{position:relative;display:flex;flex-direction:column;gap:4px;padding:14px 14px 13px;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--surface);box-shadow:var(--shadow-sm);counter-increment:login-step}
.login-flow li::before{content:counter(login-step,decimal-leading-zero);position:absolute;right:12px;top:12px;font-family:var(--font-mono);font-size:11px;color:var(--muted);opacity:.7}
.login-flow svg{color:var(--accent);margin-bottom:6px}
.login-flow strong{font-size:14px;font-weight:650}
.login-flow small{font-size:12px;line-height:1.55;color:var(--muted)}
.login-card{width:100%;padding:32px;border-radius:var(--radius-xl);box-shadow:var(--shadow-lg);animation:dialog-in .5s var(--ease-out) .08s both}
.login-card-heading{margin-bottom:26px}.login-card-heading h2{font-size:22px;line-height:1.4;font-weight:700;letter-spacing:.02em;margin:0}
.login-card-heading p{margin:8px 0 0;font-size:13px;line-height:1.7;color:var(--muted)}
.login-form{display:flex;flex-direction:column;gap:18px}.login-form label{display:flex;flex-direction:column;gap:8px;min-width:0;font-size:13px;font-weight:550;color:var(--text)}
.login-form .input{min-height:42px;font-size:14px}.login-form>.btn-primary{min-height:44px;font-size:14px;margin-top:4px;letter-spacing:.08em}
.login-password{position:relative;display:block}.login-password .input{padding-right:44px}
.login-password .icon-button{position:absolute;right:5px;top:50%;margin-top:-16px}
.login-error{margin:0;overflow-wrap:anywhere;font-size:13px;line-height:1.6}
.login-mode{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:5px;margin-top:22px;font-size:13px;color:var(--muted)}
.login-mode button{color:var(--accent);font-weight:600;padding:4px;border-radius:4px}.login-mode button:hover{text-decoration:underline;text-underline-offset:3px}
.login-support{border-top:1px solid var(--line);padding-top:18px;margin:18px 0 0;color:var(--muted);font-size:12px;line-height:1.7}
.login-theme{position:absolute;z-index:2;right:24px;top:20px}
@media(max-width:860px){.login-shell{gap:36px;max-width:760px}.login-intro h1{font-size:21px}.login-purpose{font-size:18px}.login-card{padding:26px}.login-flow{grid-template-columns:minmax(0,1fr)}.login-flow li{flex-direction:row;align-items:center;gap:10px;padding:11px 12px}.login-flow svg{margin:0}.login-flow small{margin-left:auto;text-align:right}.login-flow li::before{display:none}}
@media(max-width:680px){.login-page{padding:76px 20px 28px}.login-page::after{background:linear-gradient(180deg,transparent 0,var(--app-bg) 40%)}.login-shell{display:flex;flex-direction:column;gap:26px;min-height:0;max-width:400px;align-items:stretch}.login-intro{grid-template-columns:42px minmax(0,1fr);gap:12px}.login-mark{width:42px;height:42px;border-radius:11px}.login-intro h1{font-size:20px}.login-subject{font-size:12px;margin-top:2px}.login-purpose,.login-flow{display:none}.login-card{padding:24px}.login-theme{right:14px;top:16px}}
</style>
