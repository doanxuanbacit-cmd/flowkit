import { fetchAPI } from '../api/client'

export const S = '/api/studio'

export type TransitionType = 'cut' | 'match_cut' | 'crossfade' | 'fade_black' | 'whip_pan' | 'j_cut' | 'l_cut'
export type Genre = 'short_film' | 'tvc' | 'ugc' | 'podcast' | 'comedy' | 'drama' | 'action'
export type Platform = 'master' | 'tiktok' | 'youtube_shorts' | 'facebook_reels' | 'zalo' | 'youtube'

export interface Transition { type: TransitionType; duration: number; audio_offset: number }
export interface ColorAdjust { brightness: number; contrast: number; saturation: number; gamma: number; r: number; g: number; b: number; lut_path?: string | null; lut_strength: number }
export interface TimelineClip { id: string; source: string; in_s: number; out_s: number | null; transition_in: Transition; color: ColorAdjust; volume_db: number; mute: boolean; label: string; characters: string[] }
export interface SubtitleCue { start: number; end: number; text: string; speaker?: string | null }
export interface Overlay { kind: 'title' | 'lower_third' | 'cta' | 'logo' | 'kinetic'; start: number; end: number; text: string; subtext: string; image?: string | null; position: string; color: string; accent: string }
export interface AudioCue { source: string; start_s: number; in_s: number; duration: number | null; gain_db: number; fade_in: number; fade_out: number }
export interface Timeline {
  id: string; title: string; genre: Genre; width: number; height: number; fps: number
  clips: TimelineClip[]; music: AudioCue[]; voice: AudioCue[]; sfx: AudioCue[]
  subtitles: SubtitleCue[]; overlays: Overlay[]; renditions: Platform[]; keep_clip_audio: boolean
  audio: { target_lufs: number; true_peak: number; denoise: boolean; duck_music_db: number }
  subtitle_style: { font_size: number; max_chars_per_line: number; max_lines: number; margin_v_pct: number; burn_in: boolean }
}
export interface TimelineRow { id: string; title: string; flowkit_video_id: string | null; version: number; updated_at: string; data?: Timeline }
export interface Asset { id: string; kind: string; path: string; original_name: string; duration: number | null; width: number | null; height: number | null; fps: number | null; has_audio: number; size_bytes: number; source: string; created_at: string; flowkit_scene_id: string | null }
export interface QAIssue { code: string; severity: 'info' | 'warning' | 'error'; message: string; start: number | null; end: number | null; clip_id: string | null; skill: string }
export interface QAReport { target: string; passed: boolean; issues: QAIssue[]; metrics: Record<string, number | string | boolean | null> }
export interface RenderResult { master: string; duration: number; qa_passed: boolean; notes: string[]; renditions: { platform: Platform; width: number; height: number; upscaled: boolean; warnings: string[] }[]; cache_hits: number; cache_misses: number }
export interface RenderJob { id: string; timeline_id: string; timeline_version: number; status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'; stage: string | null; progress: number; result: RenderResult | null; error: string | null; qa_passed: number | null; approval: 'PENDING' | 'APPROVED' | 'REJECTED'; approved_by: string | null; approval_note: string | null; created_at: string; finished_at: string | null }
export interface FullQA { passed: boolean; master: QAReport; renditions: Record<string, QAReport>; continuity: QAIssue[]; contact_sheet: string | null }
export interface Account { id: string; platform: 'facebook' | 'youtube' | 'tiktok' | 'zalo'; name: string; status: string; capabilities: Record<string, unknown>; created_at: string }
export interface PublishJob { id: string; render_job_id: string; account_id: string; platform: string; rendition: string; status: string; scheduled_at: string | null; title: string; caption: string; tags: string[]; privacy: string; attempts: number; next_attempt_at: string | null; external_id: string | null; permalink: string | null; error: string | null; manual_package: Record<string, string> | null; created_at: string }
export interface FlowVideo { id: string; title: string; status: string; project_name: string; scenes: number }
export interface AuditRow { id: number; ts: string; actor: string; action: string; entity: string; entity_id: string; detail: Record<string, unknown> }

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  health: () => fetchAPI<{ ok: boolean; ffmpeg: string; db_version: number; skills: number; approval_required: boolean; secret_key_set: boolean }>(`${S}/health`),
  overview: () => fetchAPI<Record<string, Record<string, number> | number>>(`${S}/overview`),
  skills: () => fetchAPI<{ name: string; slot: string; summary: string; runnable: boolean }[]>(`${S}/skills`),
  assets: (kind?: string) => fetchAPI<Asset[]>(`${S}/assets${kind ? `?kind=${kind}` : ''}`),
  deleteAsset: (id: string) => fetchAPI(`${S}/assets/${id}`, json('DELETE')),
  upload: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${S}/assets/upload`, { method: 'POST', body: fd })
    if (!res.ok) throw new Error(await res.text())
    return res.json() as Promise<Asset>
  },
  flowVideos: () => fetchAPI<FlowVideo[]>(`${S}/flowkit/videos`),
  flowImport: (video_id: string, orientation: string) => fetchAPI<{ timeline: Timeline; missing_scenes: string[] }>(`${S}/flowkit/import`, json('POST', { video_id, orientation, save_timeline: true })),
  timelines: () => fetchAPI<TimelineRow[]>(`${S}/timelines`),
  timeline: (id: string) => fetchAPI<TimelineRow>(`${S}/timelines/${id}`),
  saveTimeline: (tl: Timeline) => fetchAPI<TimelineRow>(`${S}/timelines/${tl.id}`, json('PUT', tl)),
  deleteTimeline: (id: string) => fetchAPI(`${S}/timelines/${id}`, json('DELETE')),
  render: (id: string, opts: Record<string, unknown> = {}) => fetchAPI<RenderJob>(`${S}/timelines/${id}/render`, json('POST', opts)),
  renders: () => fetchAPI<RenderJob[]>(`${S}/renders`),
  renderQA: (id: string) => fetchAPI<FullQA>(`${S}/renders/${id}/qa`),
  review: (id: string, approve: boolean, reviewer: string, note: string) => fetchAPI<RenderJob>(`${S}/renders/${id}/review`, json('POST', { approve, reviewer, note })),
  cancelRender: (id: string) => fetchAPI(`${S}/renders/${id}/cancel`, json('POST')),
  accounts: () => fetchAPI<Account[]>(`${S}/accounts`),
  connect: (body: { platform: string; name: string; credentials: Record<string, unknown>; verify: boolean }) => fetchAPI<Account>(`${S}/accounts`, json('POST', body)),
  disableAccount: (id: string) => fetchAPI(`${S}/accounts/${id}/disable`, json('POST')),
  publishJobs: () => fetchAPI<PublishJob[]>(`${S}/publish`),
  publish: (body: Record<string, unknown>) => fetchAPI<PublishJob>(`${S}/publish`, json('POST', body)),
  calendar: (month: string) => fetchAPI<Record<string, PublishJob[]>>(`${S}/publish/calendar?month=${month}`),
  publishAction: (id: string, action: 'cancel' | 'retry') => fetchAPI<PublishJob>(`${S}/publish/${id}/${action}`, json('POST')),
  markPublished: (id: string, permalink: string) => fetchAPI<PublishJob>(`${S}/publish/${id}/mark-published`, json('POST', { permalink, actor: 'dashboard' })),
  audit: () => fetchAPI<AuditRow[]>(`${S}/audit?limit=100`),
}

export const fileUrl = (renderId: string, name: string) => `${S}/renders/${renderId}/files/${name}`
export const assetUrl = (id: string) => `${S}/assets/${id}/file`

export function clipDuration(c: TimelineClip, assetDur?: number | null): number {
  const out = c.out_s ?? assetDur ?? c.in_s + 5
  return Math.max(0.1, out - c.in_s)
}

export const defaultClip = (id: string, source: string): TimelineClip => ({
  id, source, in_s: 0, out_s: null, label: '', characters: [], volume_db: 0, mute: false,
  transition_in: { type: 'cut', duration: 0, audio_offset: 0 },
  color: { brightness: 0, contrast: 1, saturation: 1, gamma: 1, r: 0, g: 0, b: 0, lut_path: null, lut_strength: 1 },
})

export const newTimeline = (id: string, clips: TimelineClip[]): Timeline => ({
  id, title: 'Phim mới', genre: 'short_film', width: 1080, height: 1920, fps: 30, clips,
  music: [], voice: [], sfx: [], subtitles: [], overlays: [], renditions: ['tiktok', 'facebook_reels', 'youtube_shorts'],
  keep_clip_audio: true, audio: { target_lufs: -14, true_peak: -1.5, denoise: true, duck_music_db: -12 },
  subtitle_style: { font_size: 64, max_chars_per_line: 32, max_lines: 2, margin_v_pct: 0.18, burn_in: true },
})
