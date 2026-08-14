<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime, statusMeta } from '../api'

const route = useRoute()
const router = useRouter()

const runs = ref([])
const pipelines = ref([])
const selected = ref('')
const loading = ref(true)
const error = ref('')
const autoRefresh = ref(true)
let timer = null

async function load() {
  try {
    runs.value = await api.listRuns(selected.value || undefined)
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function changeFilter() {
  const query = selected.value ? { pipeline: selected.value } : {}
  router.replace({ path: '/runs', query })
  load()
}

watch(
  () => route.query.pipeline,
  (v) => {
    if (v !== selected.value) selected.value = String(v ?? '')
  }
)

function tick() {
  if (autoRefresh.value) load()
}

onMounted(async () => {
  selected.value = String(route.query.pipeline ?? '')
  try {
    pipelines.value = await api.listPipelines()
  } catch {
    /* 下拉可选，失败不阻塞列表 */
  }
  load()
  timer = setInterval(tick, 4000)
})

onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <h1 class="page-title">运行记录</h1>
    <div class="filter-row">
      <select v-model="selected" class="filter-select" @change="changeFilter">
        <option value="">全部流水线</option>
        <option v-for="p in pipelines" :key="p.id" :value="p.id">
          {{ p.name }}
        </option>
      </select>
      <label class="auto-label">
        <input v-model="autoRefresh" type="checkbox" /> 自动刷新
      </label>
    </div>
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
        <span>{{ r.pipeline_name || `流水线 #${r.pipeline_id}` }}</span>
        <span class="badge" :class="statusMeta(r.status).cls">{{ statusMeta(r.status).label }}</span>
      </div>
      <p class="card-desc">
        {{ r.params ? Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(' · ') : '' }}
      </p>
      <div style="font-size: 12px; color: var(--muted); margin-top: 6px; display: flex; justify-content: space-between; gap: 8px">
        <span style="font-family: ui-monospace, monospace">{{ r.id.slice(0, 8) }}</span>
        <span>步骤 {{ r.current_step || 0 }} · {{ fmtTime(r.created_at) }}</span>
      </div>
    </div>
  </div>
</template>
