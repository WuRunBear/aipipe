<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, auth } from '../api'

const router = useRouter()
const initialized = ref(null)
const password = ref('')
const confirm = ref('')
const error = ref('')
const busy = ref(false)

onMounted(async () => {
  try {
    initialized.value = (await api.authStatus()).initialized
  } catch (e) {
    error.value = e.message
  }
})

function goHome() {
  router.push('/')
}

async function submit() {
  error.value = ''
  if (!initialized.value && password.value !== confirm.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  try {
    if (initialized.value) {
      const r = await api.login(password.value)
      auth.token = r.token
    } else {
      const r = await api.setup(password.value)
      auth.token = r.token
    }
    goHome()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <h1 class="login-title">aipipe</h1>
      <p class="login-sub">
        {{ initialized === null ? '检查状态…' : initialized ? '请输入密码登录' : '首次使用，请设置登录密码' }}
      </p>
      <div v-if="error" class="error-banner">{{ error }}</div>
      <form v-if="initialized !== null" @submit.prevent="submit">
        <div class="field">
          <label for="pw">密码</label>
          <input id="pw" v-model="password" type="password" autocomplete="current-password" />
        </div>
        <div v-if="!initialized" class="field">
          <label for="pw2">确认密码</label>
          <input id="pw2" v-model="confirm" type="password" autocomplete="new-password" />
        </div>
        <button class="btn" type="submit" :disabled="busy || !password">
          {{ busy ? '请稍候…' : initialized ? '登录' : '设置并进入' }}
        </button>
      </form>
    </div>
  </div>
</template>
