<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const info = ref(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    info.value = await api.systemInfo()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <h1 class="page-title">设置</h1>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <template v-else-if="info">
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

      <h2 class="section-title">说明</h2>
      <div class="card" style="font-size: 13px; color: var(--muted)">
        密钥管理与 Webhook 通知属 M3 里程碑，当前仅展示运行环境只读状态。
      </div>
    </template>
  </div>
</template>
