<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtTime, statusMeta } from '../api'

const router = useRouter()
const runs = ref([])
const loading = ref(true)
const error = ref('')
const autoRefresh = ref(true)
let timer = null

async function load() {
  try {
    runs.value = await api.listRuns()
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function tick() {
  if (autoRefresh.value) load()
}

onMounted(() => {
  load()
  timer = setInterval(tick, 4000)
})

onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <h1 class="page-title">运行记录</h1>
    <label style="display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); margin-bottom: 10px">
      <input v-model="autoRefresh" type="checkbox" /> 自动刷新（4s）
    </label>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="runs.length === 0" class="empty">还没有运行记录</div>
    <div
      v-for="r in runs"
      :key="r.id"
      class="card clickable"
      @click="router.push(`/runs/${r.id}`)"
    >
      <div class="card-title">
        <span style="font-family: ui-monospace, monospace; font-size: 13px">{{ r.id.slice(0, 8) }}</span>
        <span class="badge" :class="statusMeta(r.status).cls">{{ statusMeta(r.status).label }}</span>
      </div>
      <p class="card-desc">
        {{ r.params ? Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(' · ') : '' }}
      </p>
      <div style="font-size: 12px; color: var(--muted); margin-top: 6px">
        步骤 {{ r.current_step || 0 }} · {{ fmtTime(r.created_at) }}
      </div>
    </div>
  </div>
</template>
