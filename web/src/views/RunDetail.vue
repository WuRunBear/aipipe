<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime, statusMeta } from '../api'

const route = useRoute()
const router = useRouter()
const runId = route.params.id

const run = ref(null)
const steps = ref([])
const loading = ref(true)
const error = ref('')
const logs = ref([])
const live = ref(false)
const logbox = ref(null)
const stickBottom = ref(true)
let es = null
let statusTimer = null

const terminal = computed(() =>
  run.value && ['success', 'failed'].includes(run.value.status)
)

async function loadRun() {
  const data = await api.getRun(runId)
  run.value = data
  steps.value = data.steps || []
}

function openStream() {
  if (es) es.close()
  es = new EventSource(api.streamUrl(runId))
  es.addEventListener('log', (e) => {
    const { file, content } = JSON.parse(e.data)
    logs.value.push({ file, content })
    scrollLog()
  })
  es.addEventListener('status', (e) => {
    const s = JSON.parse(e.data)
    if (run.value) {
      run.value.status = s.status
      run.value.current_step = s.current_step
      run.value.error = s.error
    }
  })
  es.addEventListener('done', () => {
    live.value = false
    es.close()
    es = null
    loadRun()
  })
  es.onopen = () => {
    live.value = true
  }
  es.onerror = () => {
    // 终态后服务端关闭连接会触发 error，直接回落到状态轮询
    if (terminal.value) {
      live.value = false
      if (es) {
        es.close()
        es = null
      }
    }
  }
}

async function scrollLog() {
  await nextTick()
  const el = logbox.value
  if (el && stickBottom.value) el.scrollTop = el.scrollHeight
}

function onLogScroll() {
  const el = logbox.value
  if (!el) return
  stickBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

function fullLogs() {
  window.open(api.logsUrl(runId), '_blank')
}

function rerunFrom(stepIndex) {
  if (!confirm(`从第 ${stepIndex} 步重新运行？（会复制本运行的工作目录，产物保留）`)) return
  api
    .rerun(runId, stepIndex)
    .then((r) => router.push(`/runs/${r.id}`))
    .catch((e) => (error.value = e.message))
}

onMounted(async () => {
  try {
    await loadRun()
  } catch (e) {
    error.value = e.message
    loading.value = false
    return
  }
  loading.value = false
  openStream()
  statusTimer = setInterval(loadRun, 5000)
})

onUnmounted(() => {
  if (es) es.close()
  if (statusTimer) clearInterval(statusTimer)
})
</script>

<template>
  <div>
    <div class="topbar">
      <button class="back" @click="router.push('/runs')">‹</button>
      <h1>运行 {{ runId.slice(0, 8) }}</h1>
    </div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <template v-else-if="run">
      <div class="card">
        <div class="card-title">
          <span class="badge" :class="statusMeta(run.status).cls">{{ statusMeta(run.status).label }}</span>
          <span v-if="live" class="pulse" style="font-size: 12px; color: var(--accent)">● LIVE</span>
        </div>
        <div class="kv" style="margin-top: 8px">
          <span class="k">开始</span><span class="v">{{ fmtTime(run.started_at) || '—' }}</span>
          <span class="k">结束</span><span class="v">{{ fmtTime(run.finished_at) || '—' }}</span>
          <span class="k">参数</span>
          <span class="v">
            {{ Object.entries(run.params || {}).map(([k, v]) => `${k}=${v}`).join(' · ') || '—' }}
          </span>
        </div>
        <div v-if="run.error" class="error-banner" style="margin: 10px 0 0">{{ run.error }}</div>
      </div>

      <h2 class="section-title">步骤（{{ steps.length }}）</h2>
      <div v-for="s in steps" :key="s.id" class="step">
        <span class="dot" :class="'dot-' + s.status"></span>
        <span class="name">{{ s.step_name }}</span>
        <button
          v-if="s.step_index > 1"
          class="rerun-btn"
          title="从该步骤重跑（复用已有产物）"
          @click="rerunFrom(s.step_index)"
        >
          从第 {{ s.step_index }} 步重跑
        </button>
        <span class="meta">
          <template v-if="s.exit_code !== null">{{ s.exit_code === -99 ? '超时' : `exit ${s.exit_code}` }}</template>
          <template v-else-if="s.status === 'running'">运行中</template>
          <template v-else>{{ statusMeta(s.status).label }}</template>
        </span>
      </div>

      <h2 class="section-title">实时日志 <span v-if="live" class="pulse" style="color: var(--accent)">●</span></h2>
      <div ref="logbox" class="logbox" @scroll="onLogScroll">
        <template v-if="logs.length === 0">（等待日志…）</template>
        <template v-for="(l, i) in logs" :key="i">
          <span v-if="i === 0 || logs[i - 1].file !== l.file" class="log-file">===== {{ l.file }} =====&#10;</span
          >{{ l.content }}
        </template>
      </div>

      <div style="display: flex; gap: 10px; margin-top: 14px">
        <button class="btn ghost" @click="fullLogs">完整日志</button>
        <button class="btn" @click="router.push(`/runs/${runId}/artifacts`)">查看产物</button>
      </div>
    </template>
  </div>
</template>
