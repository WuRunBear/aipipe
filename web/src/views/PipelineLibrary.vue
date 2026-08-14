<script setup>
defineOptions({ name: 'PipelineLibrary' })

import { onActivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const pipelines = ref([])
const loading = ref(true)
const error = ref('')
const refreshing = ref(false)

async function load() {
  try {
    pipelines.value = await api.listPipelines()
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  try {
    await api.refreshPipelines()
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    refreshing.value = false
  }
}

onActivated(load)  // 首次挂载与从缓存激活时静默刷新（不重置 loading，避免闪烁）
</script>

<template>
  <div>
    <h1 class="page-title">流水线库</h1>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="pipelines.length === 0" class="empty">
      暂无流水线
      <div style="margin-top: 10px">
        <button class="btn ghost" style="width: auto; padding: 8px 18px" @click="refresh">
          重新扫描
        </button>
      </div>
    </div>
    <div
      v-for="p in pipelines"
      :key="p.id"
      class="card clickable"
      @click="router.push(`/pipelines/${p.id}`)"
    >
      <div class="card-title">
        <span>{{ p.name }}</span>
        <span class="badge" :class="p.status === 'active' ? 'st-success' : 'st-queued'">
          {{ p.status === 'active' ? '可用' : '停用' }}
        </span>
      </div>
      <p class="card-desc">{{ p.description || '（无描述）' }}</p>
      <div style="display: flex; gap: 8px; margin-top: 10px">
        <button class="mini-btn primary" @click.stop="router.push(`/pipelines/${p.id}`)">发起运行</button>
        <button class="mini-btn" @click.stop="router.push({ path: '/runs', query: { pipeline: p.id } })">
          运行记录
        </button>
      </div>
    </div>
    <div style="display: flex; gap: 10px; margin-top: 16px">
      <button class="btn ghost" :disabled="refreshing" @click="refresh">
        {{ refreshing ? '扫描中…' : '重新扫描流水线' }}
      </button>
    </div>
  </div>
</template>
