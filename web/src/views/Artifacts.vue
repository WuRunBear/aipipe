<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtSize } from '../api'

const route = useRoute()
const router = useRouter()
const runId = route.params.id

function goBack() {
  if (window.history.state && window.history.state.back) router.back()
  else router.push(`/runs/${runId}`)
}

const artifacts = ref([])
const currentDir = ref('')
const loading = ref(true)
const error = ref('')
const preview = ref(null)
const previewing = ref(null)

const KIND_ICON = {
  video: { ico: '🎬', bg: '#2c1e3f', color: '#c39bff' },
  audio: { ico: '🎵', bg: '#1e3a2f', color: '#7fe3b0' },
  image: { ico: '🖼', bg: '#3a2c1e', color: '#ffd28a' },
  text: { ico: '📄', bg: '#1e2f3a', color: '#8ac8ff' },
  archive: { ico: '📦', bg: '#3a2c2c', color: '#ff9d9d' },
  dir: { ico: '📁', bg: '#262b36', color: '#b9c6e0' },
  other: { ico: '📁', bg: '#262b36', color: '#b9c6e0' },
}

function meta(kind) {
  return KIND_ICON[kind] || KIND_ICON.other
}

function crumbParts() {
  if (!currentDir.value) return []
  return currentDir.value.split('/').filter(Boolean)
}

function crumbPath(idx) {
  return crumbParts().slice(0, idx + 1).join('/')
}

async function load() {
  loading.value = true
  try {
    const data = await api.listArtifacts(runId, currentDir.value)
    artifacts.value = data.artifacts
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function openDir(dir) {
  currentDir.value = dir
  load()
}

function goRoot() {
  currentDir.value = ''
  load()
}

function isMedia(kind) {
  return kind === 'video' || kind === 'audio'
}

async function showPreview(a) {
  previewing.value = a
  preview.value = null
  if (a.kind === 'image') return
  try {
    preview.value = await api.previewArtifact(runId, a.path)
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="topbar">
      <button class="back" @click="goBack">‹</button>
      <h1>产物</h1>
    </div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="artifacts.length === 0" class="empty">暂无产物（运行中的流水线产物会实时出现）</div>
    <div v-else>
      <div class="crumb" v-if="currentDir">
        <button class="crumb-link" @click="goRoot">/work</button>
        <template v-for="(part, i) in crumbParts()" :key="i">
          <span class="crumb-sep">/</span>
          <button v-if="i < crumbParts().length - 1" class="crumb-link" @click="openDir(crumbPath(i))">{{ part }}</button>
          <span v-else class="crumb-here">{{ part }}</span>
        </template>
      </div>

      <div v-for="a in artifacts" :key="a.path" class="artifact">
        <span class="ico" :style="{ background: meta(a.kind).bg, color: meta(a.kind).color }">
          {{ meta(a.kind).ico }}
        </span>
        <button v-if="a.kind === 'dir'" class="body dir-row" @click="openDir(a.path)">
          <div class="name">{{ a.name }}/</div>
        </button>
        <div v-else class="body">
          <div class="name">{{ a.name }}</div>
          <div class="size">{{ fmtSize(a.size) }}</div>
        </div>
        <div class="act" v-if="a.kind !== 'dir'">
          <button v-if="a.kind === 'text'" @click="showPreview(a)">预览</button>
          <button v-if="a.kind === 'image'" @click="showPreview(a)">预览</button>
          <button v-if="isMedia(a.kind)" @click="showPreview(a)">播放</button>
          <a :href="api.artifactDownloadUrl(runId, a.path)" download>下载</a>
        </div>
      </div>

      <template v-if="previewing">
        <h2 class="section-title">预览：{{ previewing.name }}</h2>
        <video
          v-if="previewing.kind === 'video'"
          controls
          style="width: 100%; border-radius: 10px; background: #000"
          :src="api.artifactDownloadUrl(runId, previewing.path)"
        ></video>
        <audio
          v-else-if="previewing.kind === 'audio'"
          controls
          style="width: 100%"
          :src="api.artifactDownloadUrl(runId, previewing.path)"
        ></audio>
        <img
          v-else-if="previewing.kind === 'image'"
          :src="api.artifactDownloadUrl(runId, previewing.path)"
          style="width: 100%; border-radius: 10px; background: #000"
          alt=""
        />
        <pre v-else-if="preview && !preview.binary" class="preview">{{ preview.content }}<span v-if="preview.truncated" style="color: var(--muted)">&#10;…（内容过长，已截断）</span></pre>
        <div v-else class="empty">二进制文件，请下载查看</div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.crumb {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  margin-bottom: 8px;
  background: var(--card);
  border-radius: 10px;
  overflow-x: auto;
  white-space: nowrap;
}
.crumb-link {
  color: var(--accent);
  background: none;
  border: none;
  font-size: 14px;
}
.crumb-sep {
  color: var(--muted);
}
.crumb-here {
  font-size: 14px;
  color: var(--text);
}
.dir-row {
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
}
</style>
