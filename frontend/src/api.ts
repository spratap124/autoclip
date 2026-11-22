/**
 * Typed client for the AutoClip API.
 *
 * Same-origin throughout: the server hands out the built bundle, so there is no
 * base URL to configure and no CORS to negotiate.
 */

export interface Source {
  id: string
  type: string
  title: string
  url: string | null
  filename: string | null
  channel: string | null
  duration_s: number
  width: number | null
  height: number | null
  fps: number | null
  has_audio: boolean
  has_video: boolean
  created_at: string
}

export type JobStatus = 'queued' | 'running' | 'failed' | 'done' | 'cancelled'

export interface Job {
  id: string
  source_id: string
  status: JobStatus
  current_stage: string
  progress: number
  error: string | null
  provider: string
  mode: 'transcript' | 'cs2'
  player_name: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  source: Source | null
}

export interface ExportRecord {
  id: string
  clip_id: string
  ratio: string
  style: string
  size_bytes: number
  created_at: string
  download_url: string
}

export type ClipStatus = 'candidate' | 'kept' | 'discarded' | 'exported'

export interface Clip {
  id: string
  job_id: string
  rank: number
  start_s: number
  end_s: number
  duration_s: number
  start_word: number
  end_word: number
  title: string
  hook: string
  score: number
  quality_score: number
  confidence: number
  category: string
  evidence_ids: string[]
  reason: string
  status: ClipStatus
  user_trimmed: boolean
  caption_style: string
  ratio: string
  exports: ExportRecord[]
}

export interface EvidenceRecord {
  event_id: string
  start_s: number
  end_s: number
  evidence_type: string
  detection_confidence: number
  supporting_metadata: Record<string, unknown>
  explanation: string
  detector_version: string
}

export interface Word {
  text: string
  start: number
  end: number
  speaker: string | null
}

export interface CropKeyframe {
  t: number
  x: number
  y: number
}

export interface CropSegment {
  start_s: number
  end_s: number
  width: number
  height: number
  keyframes: CropKeyframe[]
  strategy: 'track' | 'wide' | 'general'
  zoom: number
  fit: boolean
}

/** Mirrors autoclip.pipeline.reframe.croppath.CropPath. */
export interface CropPath {
  source_width: number
  source_height: number
  segments: CropSegment[]
}

export interface CaptionStyle {
  key: string
  label: string
  description: string
  preview: {
    font: string
    primary: string
    accent: string | null
    outline: string
    outlineWidth: number
    allCaps: boolean
    sizeRatio: number
    marginRatio: number
    boxed: boolean
    animation: string
    maxWords: number
  }
}

export interface ProviderStatus {
  name: string
  available: boolean
  detail: string
  models: string[]
  requires_key: boolean
  has_key: boolean
}

export interface Settings {
  active_provider: string
  providers: Record<string, { model: string; base_url: string | null }>
  whisper: { model: string; compute_type: string; language: string; diarization: boolean }
  clips: { min_duration_s: number; max_duration_s: number; max_clips: number }
  ingest: {
    ytdlp_format: string
    cookies_from_browser: string
    prefer_youtube_captions: boolean
  }
  cs2_demo: {
    max_upload_bytes: number
    parser_timeout_s: number
    parser_memory_limit_mb: number
    tick_rate_override: number | null
    pre_roll_s: number
    post_roll_s: number
    won_clutch_post_roll_s: number
    min_clip_duration_s: number
    max_clip_duration_s: number
    fast_multi_kill_s: number
    headshot_streak_kills: number
    strict_clutches: boolean
    default_visible_tags: string[]
  }
  export: {
    ratio: string
    caption_style: string
    loudness_lufs: number
    prefer_hardware_encoder: boolean
    crf: number
    write_srt: boolean
  }
  insecure_secret_storage: boolean
  keys_present: Record<string, boolean>
}

export interface SystemStatus {
  ready: boolean
  python_version: string
  platform: string
  ffmpeg_version: string | null
  has_libass: boolean
  nvenc_works: boolean
  accel: string
  gpu_name: string | null
  compute_type: string
  diarization_available: boolean
}

export interface DemoUpload {
  id: string
  original_filename: string
  size_bytes: number
  content_sha256: string
  parse_status: string
  warnings: string[]
  duplicate: boolean
}

export interface DemoParseJob {
  id: string
  demo_id: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  current_stage: string
  progress: number
  error: string | null
  created_at: string
  updated_at: string
}

export interface DemoMatch {
  demo_id: string
  content_sha256: string
  parser_version: string
  schema_version: number
  header: Record<string, string>
  players: { session_id: string; steamid: number | null; name: string | null; name_history: string[]; team_number: number | null }[]
  event_counts: Record<string, number>
  warnings: string[]
  round_count: number
  match_score: Record<string, number>
  duration_s: number | null
  match_type: string
  cache_hit: boolean
}

export interface DemoRosterPlayer {
  player_id: string
  session_id: string
  steamid: number | null
  name: string | null
  name_history: string[]
  team_number: number | null
  kills: number
  deaths: number
  assists: number
  adr: number | null
  selectable: boolean
}

export interface DemoHighlight {
  highlight_id: string
  round_id: string
  round_number: number
  tags: string[]
  score: number
  score_breakdown: Record<string, number>
  start_tick: number
  end_tick: number
  start_seconds: number | null
  end_seconds: number | null
  kill_count: number
  kill_event_ids: string[]
  source_event_ids: string[]
  kills: Record<string, unknown>[]
  clutch: Record<string, unknown> | null
  data_quality_warnings: string[]
  timing_warning: string | null
}

export interface JobSettingsOverrides {
  mode?: 'transcript' | 'cs2'
  cs2?: {
    local_player_name?: string
    diagnostics_enabled?: boolean
    diagnostics_max_crops?: number
    frame_interval_s?: number
    audio_window_s?: number
    frame_width?: number
    audio_activity_threshold_dbfs?: number
    visual_change_threshold?: number
    context_before_s?: number
    context_after_s?: number
    merge_gap_s?: number
    min_candidate_s?: number
    max_candidate_s?: number
    max_candidates?: number
  }
  provider?: string
  whisper_model?: string
  language?: string
  diarization?: boolean
  min_duration_s?: number
  max_duration_s?: number
  max_clips?: number
  caption_style?: string
  ratio?: string
}

/** An API error carrying the server's message and its actionable hint. */
export class ApiError extends Error {
  hint: string
  status: number

  constructor(message: string, status: number, hint = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.hint = hint
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let hint = ''
    try {
      const body = await response.json()
      const detail = body.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (detail && typeof detail === 'object') {
        // The ingest endpoints return {message, hint} so the UI can show the
        // fix alongside the failure.
        message = detail.message ?? message
        hint = detail.hint ?? ''
      }
    } catch {
      /* Response had no JSON body; the status line is the best we have. */
    }
    throw new ApiError(message, response.status, hint)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string; version: string }>('/api/health'),
  system: () => request<SystemStatus>('/api/system'),
  fetchModels: () => request<void>('/api/system/models', { method: 'POST' }),

  listSources: () => request<Source[]>('/api/sources'),

  ingestYouTube: (url: string, cookiesFromBrowser?: string) =>
    request<Source>('/api/sources/youtube', {
      method: 'POST',
      body: JSON.stringify({ url, cookies_from_browser: cookiesFromBrowser || null }),
    }),

  uploadSource: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Source>('/api/sources/upload', { method: 'POST', body: form })
  },

  uploadDemo: (
    file: File,
    onProgress: (loaded: number, total: number) => void,
    signal?: AbortSignal,
  ) =>
    new Promise<DemoUpload>((resolve, reject) => {
      const form = new FormData()
      form.append('file', file)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/demos/upload')
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded, event.total)
      }
      signal?.addEventListener('abort', () => xhr.abort(), { once: true })
      xhr.onabort = () => reject(new ApiError('Demo upload was cancelled.', 0))
      xhr.onerror = () => reject(new ApiError('Demo upload failed.', 0))
      xhr.onload = () => {
        let body: Record<string, unknown> = {}
        try {
          body = JSON.parse(xhr.responseText) as Record<string, unknown>
        } catch {
          /* The status is enough when the server returned a non-JSON body. */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body as unknown as DemoUpload)
        else reject(new ApiError(String(body.detail ?? `Upload failed (${xhr.status}).`), xhr.status))
      }
      xhr.send(form)
    }),
  startDemoParse: (demoId: string) =>
    request<DemoParseJob>(`/api/demos/${demoId}/parse`, { method: 'POST' }),
  getDemo: (demoId: string) => request<DemoUpload>(`/api/demos/${demoId}`),
  getDemoParseJob: (jobId: string) => request<DemoParseJob>(`/api/demos/jobs/${jobId}`),
  cancelDemoParse: (jobId: string) =>
    request<DemoParseJob>(`/api/demos/jobs/${jobId}/cancel`, { method: 'POST' }),
  getDemoMatch: (demoId: string) => request<DemoMatch>(`/api/demos/${demoId}/match`),
  getDemoPlayers: (demoId: string) =>
    request<{ demo_id: string; players: DemoRosterPlayer[]; warnings: string[] }>(
      `/api/demos/${demoId}/players`,
    ),
  getDemoRounds: (demoId: string, playerId?: string) =>
    request<{ rounds: Record<string, unknown>[]; warnings: string[] }>(
      `/api/demos/${demoId}/rounds${playerId ? `?player_id=${encodeURIComponent(playerId)}` : ''}`,
    ),
  getDemoAnalysis: (
    demoId: string,
    playerId: string,
    params: {
      tag?: string
      round_number?: number
      minimum_score?: number
      sort?: string
      offset?: number
      limit?: number
    } = {},
  ) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) query.set(key, String(value))
    }
    return request<{
      total: number
      highlights: DemoHighlight[]
      warnings: string[]
      offset: number
      limit: number
    }>(`/api/demos/${demoId}/analysis/${encodeURIComponent(playerId)}?${query}`)
  },
  demoExportUrl: (demoId: string, playerId: string, format: 'json' | 'csv' | 'timestamps') =>
    `/api/demos/${demoId}/analysis/${encodeURIComponent(playerId)}/export/${format}`,

  listJobs: (limit = 25) => request<Job[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),

  createJob: (sourceId: string, settings: JobSettingsOverrides = {}) =>
    request<Job>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ source_id: sourceId, settings }),
    }),

  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),

  listClips: (jobId: string) => request<Clip[]>(`/api/jobs/${jobId}/clips`),
  getJobEvidence: (jobId: string) =>
    request<EvidenceRecord[]>(`/api/jobs/${jobId}/evidence`),
  getClip: (clipId: string) => request<Clip>(`/api/clips/${clipId}`),

  getCropPath: (clipId: string) => request<CropPath>(`/api/clips/${clipId}/crop-path`),
  getClipWords: (clipId: string) => request<Word[]>(`/api/clips/${clipId}/words`),

  patchClip: (
    clipId: string,
    patch: { start_s?: number; end_s?: number; title?: string; status?: ClipStatus },
  ) => request<Clip>(`/api/clips/${clipId}`, { method: 'PATCH', body: JSON.stringify(patch) }),

  patchCaptions: (
    clipId: string,
    patch: { words?: Word[]; caption_style?: string; ratio?: string },
  ) =>
    request<Clip>(`/api/clips/${clipId}/captions`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  exportClip: (clipId: string, ratio: string, style: string, writeSrt = false) =>
    request<ExportRecord>(`/api/clips/${clipId}/export`, {
      method: 'POST',
      body: JSON.stringify({ ratio, style, write_srt: writeSrt }),
    }),

  captionStyles: () => request<CaptionStyle[]>('/api/caption-styles'),
  providerStatus: () => request<ProviderStatus[]>('/api/providers/status'),

  getSettings: () => request<Settings>('/api/settings'),
  putSettings: (patch: Partial<Settings>) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(patch) }),

  putSecret: (key: string, value: string) =>
    request<void>('/api/settings/secrets', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),

  deleteSecret: (key: string) =>
    request<void>(`/api/settings/secrets/${key}`, { method: 'DELETE' }),

  mediaUrl: (jobId: string) => `/api/jobs/${jobId}/media`,
}

/** Format seconds as m:ss, or h:mm:ss past an hour. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

/** Format seconds as timecode with centiseconds, for trim handles. */
export function formatTimecode(seconds: number): string {
  const total = Math.max(0, seconds)
  const cs = Math.floor((total % 1) * 100)
  const s = Math.floor(total) % 60
  const m = Math.floor(total / 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(m)}:${pad(s)}.${pad(cs)}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}
