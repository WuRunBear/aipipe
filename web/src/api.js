const API_BASE = ''

async function request(path, options) {
  const res = await fetch(API_BASE + path, options)
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

export const api = {
  listPipelines: () => request('/pipelines'),
  getPipeline: (id) => request(`/pipelines/${id}`),
  refreshPipelines: () => request('/pipelines/refresh', { method: 'POST' }),
  createRun: (id, params) =>
    request(`/pipelines/${id}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ params }),
    }),
  listRuns: (pipelineId) =>
    request('/runs' + (pipelineId ? `?pipeline=${pipelineId}` : '')),
  getRun: (id) => request(`/runs/${id}`),
  logsUrl: (id) => `${API_BASE}/runs/${id}/logs`,
  streamUrl: (id) => `${API_BASE}/runs/${id}/logs/stream`,
  listArtifacts: (id) => request(`/runs/${id}/artifacts`),
  artifactDownloadUrl: (id, path) =>
    `${API_BASE}/runs/${id}/artifacts/download?path=${encodeURIComponent(path)}`,
  previewArtifact: (id, path) =>
    request(`/runs/${id}/artifacts/preview?path=${encodeURIComponent(path)}`),
  systemInfo: () => request('/system/info'),
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
