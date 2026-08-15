<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const id = route.params.id

const pipeline = ref(null)
const loading = ref(true)
const error = ref('')
const submitting = ref(false)
const form = reactive({})

function initForm(params) {
  for (const [key, spec] of Object.entries(params || {})) {
    const s = typeof spec === 'object' ? spec : {}
    form[key] = s.type === 'boolean' ? !!s.default : (s.default ?? '')
  }
}

async function load() {
  try {
    pipeline.value = await api.getPipeline(id)
    initForm(pipeline.value.params)
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function submit() {
  const params = {}
  const schema = pipeline.value.params || {}
  for (const [key, spec] of Object.entries(schema)) {
    const s = typeof spec === 'object' ? spec : {}
    const val = form[key]
    if (val === '' || val == null) {
      if (s.required) {
        error.value = `缺少必填参数：${key}`
        return
      }
      continue
    }
    params[key] = val
  }
  submitting.value = true
  error.value = ''
  api
    .createRun(id, params)
    .then((run) => router.push(`/runs/${run.id}`))
    .catch((e) => {
      error.value = e.message
      submitting.value = false
    })
}

function goBack() {
  if (window.history.state && window.history.state.back) router.back()
  else router.push('/')
}

function specOf(p) {
  return typeof p === 'object' && p ? p : {}
}

onMounted(load)
</script>

<template>
  <div>
    <div class="topbar">
      <button class="back" @click="goBack">‹</button>
      <h1>发起运行</h1>
    </div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <template v-else-if="pipeline">
      <div class="card">
        <div class="card-title">{{ pipeline.name }}</div>
        <p class="card-desc">{{ pipeline.description || '（无描述）' }}</p>
      </div>
      <h2 class="section-title">参数</h2>
      <form @submit.prevent="submit">
        <div v-for="(specRaw, key) in pipeline.params" :key="key" class="field">
          <label :for="`p-${key}`">
            {{ key }}
            <span v-if="specOf(specRaw).required" class="req">*</span>
          </label>
          <label
            v-if="specOf(specRaw).type === 'boolean'"
            class="check-row"
            :for="`p-${key}`"
          >
            <input
              :id="`p-${key}`"
              v-model="form[key]"
              type="checkbox"
            />
            {{ specOf(specRaw).hint || '启用' }}
          </label>
          <textarea
            v-else-if="specOf(specRaw).type === 'text'"
            :id="`p-${key}`"
            v-model="form[key]"
            :placeholder="specOf(specRaw).default ?? ''"
          ></textarea>
          <input
            v-else-if="specOf(specRaw).type === 'number'"
            :id="`p-${key}`"
            v-model.number="form[key]"
            type="number"
            :placeholder="specOf(specRaw).default ?? ''"
          />
          <input
            v-else
            :id="`p-${key}`"
            v-model="form[key]"
            :placeholder="specOf(specRaw).default ?? ''"
          />
          <div
            v-if="specOf(specRaw).hint && specOf(specRaw).type !== 'boolean'"
            class="hint"
          >{{ specOf(specRaw).hint }}</div>
        </div>
        <button class="btn" type="submit" :disabled="submitting">
          {{ submitting ? '提交中…' : '开始运行' }}
        </button>
      </form>
    </template>
  </div>
</template>
