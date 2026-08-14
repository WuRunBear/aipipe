<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, auth } from '../api'

const router = useRouter()

const info = ref(null)
const loading = ref(true)
const error = ref('')

const webhookUrl = ref('')
const savingHook = ref(false)
const hookMsg = ref('')

const curPw = ref('')
const newPw = ref('')
const confirmPw = ref('')
const changingPw = ref(false)
const pwMsg = ref('')

function logout() {
  auth.token = ''
  router.push('/login')
}

onMounted(async () => {
  try {
    ;[info.value, { webhook_url: webhookUrl.value }] = await Promise.all([
      api.systemInfo(),
      api.getSettings(),
    ])
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function saveHook() {
  savingHook.value = true
  hookMsg.value = ''
  try {
    const r = await api.updateSettings({ webhook_url: webhookUrl.value })
    webhookUrl.value = r.webhook_url
    hookMsg.value = '已保存'
  } catch (e) {
    hookMsg.value = e.message
  } finally {
    savingHook.value = false
  }
}

async function changePw() {
  pwMsg.value = ''
  if (newPw.value !== confirmPw.value) {
    pwMsg.value = '两次输入的新密码不一致'
    return
  }
  changingPw.value = true
  try {
    await api.updateSettings({
      current_password: curPw.value,
      new_password: newPw.value,
    })
    pwMsg.value = '密码已更新'
    curPw.value = ''
    newPw.value = ''
    confirmPw.value = ''
  } catch (e) {
    pwMsg.value = e.message
  } finally {
    changingPw.value = false
  }
}
</script>

<template>
  <div>
    <h1 class="page-title">设置</h1>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <template v-else-if="info">
      <h2 class="section-title">Webhook 通知</h2>
      <div class="card">
        <div class="field" style="margin-bottom: 10px">
          <label for="hook">完成/失败时通知的 URL（可桥接 Bark/TG/企业微信）</label>
          <input id="hook" v-model="webhookUrl" placeholder="https://example.com/hook" />
        </div>
        <button class="btn" :disabled="savingHook" @click="saveHook">
          {{ savingHook ? '保存中…' : '保存 Webhook' }}
        </button>
        <div v-if="hookMsg" class="hint" style="margin-top: 8px; font-size: 13px; color: var(--ok)">{{ hookMsg }}</div>
      </div>

      <h2 class="section-title">修改密码</h2>
      <div class="card">
        <div class="field">
          <label for="cpw">当前密码</label>
          <input id="cpw" v-model="curPw" type="password" autocomplete="current-password" />
        </div>
        <div class="field">
          <label for="npw">新密码（至少 6 位）</label>
          <input id="npw" v-model="newPw" type="password" autocomplete="new-password" />
        </div>
        <div class="field">
          <label for="npw2">确认新密码</label>
          <input id="npw2" v-model="confirmPw" type="password" autocomplete="new-password" />
        </div>
        <button class="btn ghost" :disabled="changingPw" @click="changePw">
          {{ changingPw ? '修改中…' : '修改密码' }}
        </button>
        <div v-if="pwMsg" class="hint" style="margin-top: 8px; font-size: 13px; color: var(--ok)">{{ pwMsg }}</div>
      </div>

      <h2 class="section-title">运行环境</h2>
      <div class="card">
        <div class="kv">
          <span class="k">Docker</span>
          <span class="v"><span class="badge" :class="info.docker_ok ? 'st-success' : 'st-failed'">{{ info.docker_ok ? '可用' : '不可用' }}</span></span>
          <span class="k">受限密钥</span>
          <span class="v"><span class="badge" :class="info.secrets_configured ? 'st-success' : 'st-failed'">{{ info.secrets_configured ? '已配置' : '未配置' }}</span></span>
          <span class="k">密钥文件</span>
          <span class="v" style="font-family: ui-monospace, monospace; font-size: 12px">{{ info.secrets_path }}</span>
        </div>
      </div>

      <h2 class="section-title">统计</h2>
      <div class="card">
        <div class="kv">
          <span class="k">流水线</span>
          <span class="v">{{ info.pipelines }}（可用 {{ info.active_pipelines }}）</span>
          <span class="k">运行次数</span>
          <span class="v">{{ info.runs }}</span>
        </div>
      </div>

      <button class="btn danger" style="margin-top: 16px" @click="logout">退出登录</button>
    </template>
  </div>
</template>
