const API_BASE = ''
const TOKEN_KEY = 'aipipe_token'

export const auth = {
  get token() {
    return localStorage.getItem(TOKEN_KEY) || ''
  },
  set token(v) {
    if (v) localStorage.setItem(TOKEN_KEY, v)
    else localStorage.removeItem(TOKEN_KEY)
  },
  headers() {
    const t = this.token
    return t ? { Authorization: `Bearer ${t}` } : {}
  },
}

function isAuthError(status) {
  return status === 401
}

async function request(path, options) {
  const res = await fetch(API_BASE + path, {
    ...options,
    headers: { ...(options?.headers || {}), ...auth.headers() },
  })
  if (isAuthError(res.status)) {
    auth.token = ''
    if (!location.hash.startsWith('#/login')) location.hash = '#/login'
    throw new Error('登录已失效，请重新登录')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* 非 JSON 响应，保留状态文本 */
    }
    throw new Error(detail)
  }
  return res.json()
}

function post(path, data) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export const api = {
  authStatus: () => request('/auth/status'),
  setup: (password) => post('/auth/setup', { password }),
  login: (password) => post('/auth/login', { password }),
  listPipelines: () => request('/pipelines'),
  getPipeline: (id) => request(`/pipelines/${id}`),
  refreshPipelines: () => request('/pipelines/refresh', { method: 'POST' }),
  createRun: (id, params) => post(`/pipelines/${id}/runs`, { params }),
  listRuns: (pipelineId) =>
    request('/runs' + (pipelineId ? `?pipeline=${pipelineId}` : '')),
  getRun: (id) => request(`/runs/${id}`),
  stopRun: (id) => request(`/runs/${id}/stop`, { method: 'POST' }),
  rerun: (id, fromStep) =>
    request(`/runs/${id}/rerun?from_step=${fromStep}`, { method: 'POST' }),
  logsUrl: (id) =>
    `${API_BASE}/runs/${id}/logs?token=${encodeURIComponent(auth.token)}`,
  streamUrl: (id) =>
    `${API_BASE}/runs/${id}/logs/stream?token=${encodeURIComponent(auth.token)}`,
  listArtifacts: (id, dir) =>
    request(`/runs/${id}/artifacts` + (dir ? `?dir=${encodeURIComponent(dir)}` : '')),
  artifactDownloadUrl: (id, path) =>
    `${API_BASE}/runs/${id}/artifacts/download?path=${encodeURIComponent(path)}&token=${encodeURIComponent(auth.token)}`,
  previewArtifact: (id, path) =>
    request(`/runs/${id}/artifacts/preview?path=${encodeURIComponent(path)}`),
  systemInfo: () => request('/system/info'),
  getSettings: () => request('/settings'),
  updateSettings: (body) => request('/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }),
}

export function fmtSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export const STATUS_META = {
  queued: { label: '排队中', cls: 'st-queued' },
  running: { label: '运行中', cls: 'st-running' },
  success: { label: '成功', cls: 'st-success' },
  failed: { label: '失败', cls: 'st-failed' },
  missing: { label: '未知', cls: 'st-queued' },
}

export function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.missing
}
