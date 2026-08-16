<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
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
const previewError = ref('')
const previewing = ref(null)
const zoomed = ref(false)
const packing = ref(false)

function onArchive() {
  packing.value = true
  setTimeout(() => (packing.value = false), 15000)
}

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
  previewError.value = ''
  zoomed.value = false
  if (a.kind === 'image' || isMedia(a.kind)) return
  try {
    preview.value = await api.previewArtifact(runId, a.path)
  } catch (e) {
    previewError.value = e.message
  }
}

function closePreview() {
  previewing.value = null
  preview.value = null
  previewError.value = ''
}

function onKey(e) {
  if (e.key === 'Escape') closePreview()
}

onMounted(() => {
  load()
  window.addEventListener('keydown', onKey)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
})
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

      <div class="toolbar">
        <a
          class="btn ghost"
          :class="{ disabled: packing }"
          :href="api.artifactArchiveUrl(runId, currentDir)"
          download
          @click="onArchive"
        >{{ packing ? '打包中…（大目录需等待）' : `打包下载${currentDir ? '（当前目录）' : '（全部）'}` }}</a>
        <span class="hint">zip 打包下载；图片较多时服务端需要一点时间，等待期间浏览器无响应属正常</span>
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
    </div>

    <!-- 全屏预览弹窗 -->
    <div v-if="previewing" class="overlay" @click.self="closePreview">
      <div class="ov-head">
        <span class="ov-title">{{ previewing.name }}</span>
        <button
          v-if="previewing.kind === 'image'"
          class="ov-btn"
          @click="zoomed = !zoomed"
        >{{ zoomed ? '适应' : '原尺寸' }}</button>
        <a class="ov-btn" :href="api.artifactDownloadUrl(runId, previewing.path)" download>下载</a>
        <button class="ov-close" @click="closePreview">✕</button>
      </div>
      <div class="ov-body" :class="{ center: previewing.kind === 'image' }">
        <video
          v-if="previewing.kind === 'video'"
          controls autoplay
          class="ov-media"
          :src="api.artifactDownloadUrl(runId, previewing.path)"
        ></video>
        <audio
          v-else-if="previewing.kind === 'audio'"
          controls autoplay
          class="ov-audio"
          :src="api.artifactDownloadUrl(runId, previewing.path)"
        ></audio>
        <img
          v-else-if="previewing.kind === 'image'"
          :class="zoomed ? 'img-full' : 'img-fit'"
          :src="api.artifactDownloadUrl(runId, previewing.path)"
          alt=""
        />
        <template v-else-if="previewing.kind === 'text'">
          <div v-if="previewError" class="empty">{{ previewError }}</div>
          <div v-else-if="!preview" class="empty">加载中…</div>
          <pre v-else-if="!preview.binary" class="preview">{{ preview.content }}<span v-if="preview.truncated" style="color: var(--muted)">&#10;…（内容过长，已截断）</span></pre>
          <div v-else class="empty">二进制文件，请下载查看</div>
        </template>
        <div v-else class="empty">此类型暂不支持预览，请下载查看</div>
      </div>
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
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.toolbar .hint {
  font-size: 11px;
  color: var(--muted);
  line-height: 1.4;
}
.toolbar a.disabled {
  pointer-events: none;
  opacity: 0.55;
}

.overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(6, 10, 18, 0.94);
  display: flex;
  flex-direction: column;
}
.ov-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  background: var(--card);
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}
.ov-title {
  flex: 1;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-btn {
  color: var(--accent);
  background: none;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 13px;
  text-decoration: none;
}
.ov-close {
  background: none;
  border: none;
  color: var(--text);
  font-size: 18px;
  padding: 4px 8px;
}
.ov-body {
  flex: 1;
  overflow: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.ov-body.center {
  align-items: center;
  justify-content: center;
}
.ov-media {
  width: 100%;
  max-height: 100%;
  border-radius: 10px;
  background: #000;
}
.ov-audio {
  width: 100%;
  margin: auto 0;
}
.img-fit {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 10px;
  background: #000;
}
.img-full {
  max-width: none;
  max-height: none;
  width: auto;
  border-radius: 4px;
  background: #000;
}
.preview {
  margin: 0;
  width: 100%;
  padding: 12px;
  background: var(--card);
  border-radius: 10px;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  overflow-x: auto;
}
</style>
