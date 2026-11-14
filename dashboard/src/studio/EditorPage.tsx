import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Save, Clapperboard, Download, Trash2, Import, ArrowLeft, ArrowRight } from 'lucide-react'
import {
  api, assetUrl, clipDuration, defaultClip, newTimeline, S,
  type Asset, type FlowVideo, type Genre, type Overlay, type Platform, type Timeline, type TimelineClip, type TimelineRow, type TransitionType,
} from './api'
import { Btn, Empty, ErrorBox, Field, Panel } from './ui'
import { errMsg, fmtSec, inputCls, inputStyle } from './format'

const GENRES: { v: Genre; l: string }[] = [
  { v: 'short_film', l: 'Phim ngắn' }, { v: 'tvc', l: 'TVC' }, { v: 'ugc', l: 'UGC' }, { v: 'podcast', l: 'Podcast' },
  { v: 'comedy', l: 'Hài' }, { v: 'drama', l: 'Drama' }, { v: 'action', l: 'Hành động' },
]
const TRANSITIONS: { v: TransitionType; l: string }[] = [
  { v: 'cut', l: 'Cut' }, { v: 'match_cut', l: 'Match cut' }, { v: 'crossfade', l: 'Crossfade' }, { v: 'fade_black', l: 'Fade đen' },
  { v: 'whip_pan', l: 'Whip pan' }, { v: 'j_cut', l: 'J-cut (tiếng vào trước)' }, { v: 'l_cut', l: 'L-cut (tiếng kéo dài)' },
]
const PLATFORMS: { v: Platform; l: string }[] = [
  { v: 'tiktok', l: 'TikTok' }, { v: 'facebook_reels', l: 'Facebook Reels' }, { v: 'youtube_shorts', l: 'YouTube Shorts' },
  { v: 'zalo', l: 'Zalo OA' }, { v: 'youtube', l: 'YouTube 16:9' },
]
const PX_PER_S = 48

function NumberInput({ value, onChange, step = 0.1, min, max, label }: { value: number | null; onChange: (v: number | null) => void; step?: number; min?: number; max?: number; label: string }) {
  return (
    <Field label={label}>
      <input type="number" step={step} min={min} max={max} value={value ?? ''} placeholder="auto"
        onChange={e => onChange(e.target.value === '' ? null : Number(e.target.value))} className={inputCls} style={inputStyle} />
    </Field>
  )
}

function Slider({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void }) {
  return (
    <Field label={`${label}: ${value.toFixed(2)}`}>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} aria-label={label} />
    </Field>
  )
}

export default function EditorPage() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<TimelineRow[]>([])
  const [assets, setAssets] = useState<Asset[]>([])
  const [tl, setTl] = useState<Timeline | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [flow, setFlow] = useState<FlowVideo[] | null>(null)
  const [picking, setPicking] = useState(false)
  const preview = useRef<HTMLVideoElement>(null)

  const loadRows = useCallback(() => api.timelines().then(setRows).catch(e => setError(errMsg(e))), [])
  useEffect(() => { loadRows(); api.assets().then(setAssets).catch(() => {}) }, [loadRows])

  const byPath = useMemo(() => Object.fromEntries(assets.map(a => [a.path, a])), [assets])
  const open = async (id: string) => {
    try { const r = await api.timeline(id); setTl(r.data!); setSel(r.data!.clips[0]?.id ?? null); setDirty(false); setError(null) }
    catch (e) { setError(errMsg(e)) }
  }
  const mutate = (fn: (t: Timeline) => void) => setTl(prev => {
    if (!prev) return prev
    const next = structuredClone(prev)
    fn(next)
    setDirty(true)
    return next
  })
  const clip = tl?.clips.find(c => c.id === sel) ?? null
  const setClip = (fn: (c: TimelineClip) => void) => mutate(t => { const c = t.clips.find(x => x.id === sel); if (c) fn(c) })

  const total = useMemo(() => {
    if (!tl) return 0
    return tl.clips.reduce((acc, c, i) => {
      const d = clipDuration(c, byPath[c.source]?.duration)
      const ov = i > 0 && ['crossfade', 'fade_black', 'whip_pan'].includes(c.transition_in.type) ? c.transition_in.duration : 0
      return acc + d - ov
    }, 0)
  }, [tl, byPath])

  const save = async () => {
    if (!tl) return
    try { await api.saveTimeline(tl); setDirty(false); setInfo('Đã lưu timeline'); loadRows() } catch (e) { setError(errMsg(e)) }
  }
  const render = async () => {
    if (!tl) return
    try {
      if (dirty) await api.saveTimeline(tl)
      const j = await api.render(tl.id)
      setInfo(`Đã đưa vào hàng đợi render ${j.id.slice(0, 8)}`)
      navigate('/studio/qa')
    } catch (e) { setError(errMsg(e)) }
  }
  const createFromAssets = (picked: Asset[]) => {
    const id = `tl_${Date.now().toString(36)}`
    const t = newTimeline(id, picked.map((a, i) => ({ ...defaultClip(`c${i + 1}`, a.path), label: a.original_name })))
    if (picked[0]?.width && picked[0]?.height && picked[0].width > picked[0].height) { t.width = 1920; t.height = 1080 }
    setTl(t); setSel(t.clips[0]?.id ?? null); setDirty(true); setPicking(false)
  }
  const importFlow = async (v: FlowVideo) => {
    try {
      const r = await api.flowImport(v.id, 'VERTICAL')
      setFlow(null); await loadRows(); setAssets(await api.assets())
      setTl(r.timeline); setSel(r.timeline.clips[0]?.id ?? null); setDirty(false)
      setInfo(r.missing_scenes.length ? `Đã import; ${r.missing_scenes.length} scene chưa có video` : 'Đã import toàn bộ scene từ FlowKit')
    } catch (e) { setError(errMsg(e)) }
  }
  const move = (dir: -1 | 1) => mutate(t => {
    const i = t.clips.findIndex(c => c.id === sel)
    const j = i + dir
    if (i < 0 || j < 0 || j >= t.clips.length) return
    ;[t.clips[i], t.clips[j]] = [t.clips[j], t.clips[i]]
    t.clips[0].transition_in = { type: 'cut', duration: 0, audio_offset: 0 }
  })
  useEffect(() => {
    const v = preview.current
    if (v && clip) { v.currentTime = clip.in_s }
  }, [clip?.id, clip?.in_s]) // eslint-disable-line react-hooks/exhaustive-deps

  const videoAssets = assets.filter(a => a.kind === 'video')
  const audioAssets = assets.filter(a => a.kind === 'audio')
  const selAsset = clip ? byPath[clip.source] : undefined

  return (
    <div className="flex flex-col lg:flex-row gap-4 max-w-[1600px]">
      <aside className="lg:w-64 flex flex-col gap-3 shrink-0">
        <div className="flex gap-2">
          <Btn kind="primary" onClick={() => setPicking(true)}><Plus size={13} />Mới</Btn>
          <Btn onClick={() => api.flowVideos().then(setFlow).catch(e => setError(errMsg(e)))}><Import size={13} />Từ FlowKit</Btn>
        </div>
        <Panel title="Timeline">
          <ul className="flex flex-col gap-1">
            {rows.map(r => (
              <li key={r.id}>
                <button onClick={() => open(r.id)} className="w-full text-left px-2 py-1.5 rounded text-xs hover:opacity-90"
                  style={{ background: tl?.id === r.id ? 'var(--card)' : 'transparent', borderLeft: `2px solid ${tl?.id === r.id ? 'var(--accent)' : 'transparent'}` }}>
                  <div className="truncate">{r.title || r.id}</div>
                  <div className="text-[10px]" style={{ color: 'var(--muted)' }}>v{r.version}{r.flowkit_video_id ? ' · FlowKit' : ''}</div>
                </button>
              </li>
            ))}
            {!rows.length && <Empty>Chưa có timeline</Empty>}
          </ul>
        </Panel>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col gap-4">
        <ErrorBox error={error} />
        {info && <div className="text-xs rounded-lg px-3 py-2" style={{ color: 'var(--green)', background: 'color-mix(in srgb, var(--green) 10%, transparent)' }} onClick={() => setInfo(null)}>{info}</div>}

        {picking && (
          <Panel title="Chọn clip cho timeline mới" action={<Btn kind="ghost" onClick={() => setPicking(false)}>Đóng</Btn>}>
            <AssetPicker assets={videoAssets} onDone={createFromAssets} />
          </Panel>
        )}
        {flow && (
          <Panel title="Import video từ FlowKit" action={<Btn kind="ghost" onClick={() => setFlow(null)}>Đóng</Btn>}>
            {!flow.length ? <Empty>FlowKit chưa có video.</Empty> : (
              <ul className="flex flex-col divide-y" style={{ borderColor: 'var(--border)' }}>
                {flow.map(v => (
                  <li key={v.id} className="flex items-center gap-3 py-2 text-xs">
                    <span className="flex-1 truncate">{v.project_name} / <b>{v.title}</b></span>
                    <span style={{ color: 'var(--muted)' }}>{v.scenes} scene · {v.status}</span>
                    <Btn onClick={() => importFlow(v)}>Import</Btn>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}

        {!tl ? <Panel><Empty>Chọn hoặc tạo timeline để bắt đầu dựng.</Empty></Panel> : (
          <>
            <Panel>
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex-1 min-w-48"><Field label="Tên phim"><input value={tl.title} onChange={e => mutate(t => { t.title = e.target.value })} className={inputCls} style={inputStyle} /></Field></div>
                <Field label="Thể loại">
                  <select value={tl.genre} onChange={e => mutate(t => { t.genre = e.target.value as Genre })} className={inputCls} style={inputStyle}>
                    {GENRES.map(g => <option key={g.v} value={g.v}>{g.l}</option>)}
                  </select>
                </Field>
                <Field label="Khung hình">
                  <select value={`${tl.width}x${tl.height}`} onChange={e => mutate(t => { const [w, h] = e.target.value.split('x').map(Number); t.width = w; t.height = h })} className={inputCls} style={inputStyle}>
                    <option value="1080x1920">9:16 1080×1920</option><option value="1920x1080">16:9 1920×1080</option><option value="1080x1080">1:1 1080×1080</option><option value="720x1280">9:16 720×1280</option>
                  </select>
                </Field>
                <div className="text-xs pb-2" style={{ color: 'var(--muted)' }}>Tổng ≈ <b style={{ color: 'var(--text)' }}>{fmtSec(total)}</b></div>
                <Btn onClick={save} disabled={!dirty}><Save size={13} />Lưu</Btn>
                <a href={`${S}/timelines/${tl.id}/edl`} target="_blank" rel="noreferrer"><Btn kind="ghost"><Download size={13} />EDL</Btn></a>
                <Btn kind="primary" onClick={render}><Clapperboard size={13} />Render + QA</Btn>
              </div>
              <div className="flex flex-wrap gap-3 mt-3 text-xs">
                {PLATFORMS.map(p => (
                  <label key={p.v} className="flex items-center gap-1.5">
                    <input type="checkbox" checked={tl.renditions.includes(p.v)} onChange={e => mutate(t => { t.renditions = e.target.checked ? [...t.renditions, p.v] : t.renditions.filter(x => x !== p.v) })} />{p.l}
                  </label>
                ))}
              </div>
            </Panel>

            <Panel title="Timeline">
              <div className="overflow-x-auto pb-2">
                <div className="flex items-stretch gap-0 min-h-20" role="listbox" aria-label="Các clip trong timeline">
                  {tl.clips.map((c, i) => {
                    const d = clipDuration(c, byPath[c.source]?.duration)
                    return (
                      <div key={c.id} className="flex items-stretch">
                        {i > 0 && (
                          <div className="w-6 flex items-center justify-center text-[9px] font-bold" title={c.transition_in.type} style={{ color: c.transition_in.type === 'cut' ? 'var(--muted)' : 'var(--yellow)' }}>
                            {c.transition_in.type === 'cut' ? '|' : c.transition_in.type.slice(0, 2).toUpperCase()}
                          </div>
                        )}
                        <button role="option" aria-selected={sel === c.id} onClick={() => setSel(c.id)}
                          className="rounded-lg px-2 py-1.5 text-left text-[10px] flex flex-col justify-between overflow-hidden"
                          style={{ width: Math.max(70, d * PX_PER_S), background: sel === c.id ? 'color-mix(in srgb, var(--accent) 30%, var(--card))' : 'var(--card)', border: `1px solid ${sel === c.id ? 'var(--accent)' : 'var(--border)'}` }}>
                          <span className="truncate font-medium">{c.label || c.id}</span>
                          <span style={{ color: 'var(--muted)' }}>{fmtSec(d)}{c.mute ? ' · 🔇' : ''}</span>
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
              <div className="flex gap-2 mt-2">
                <Btn kind="ghost" onClick={() => move(-1)} disabled={!clip}><ArrowLeft size={12} />Trái</Btn>
                <Btn kind="ghost" onClick={() => move(1)} disabled={!clip}>Phải<ArrowRight size={12} /></Btn>
                <Btn kind="danger" disabled={!clip || tl.clips.length < 2} onClick={() => mutate(t => { t.clips = t.clips.filter(c => c.id !== sel); t.clips[0].transition_in = { type: 'cut', duration: 0, audio_offset: 0 } })}><Trash2 size={12} />Xoá clip</Btn>
              </div>
            </Panel>

            {clip && (
              <div className="grid xl:grid-cols-[minmax(0,320px)_1fr] gap-4">
                <Panel title="Xem trước">
                  {selAsset ? <video ref={preview} src={assetUrl(selAsset.id)} controls className="w-full rounded bg-black max-h-[480px]" /> : <Empty>Clip không thuộc thư viện asset.</Empty>}
                </Panel>
                <Panel title={`Clip ${clip.label || clip.id}`}>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <NumberInput label="In (s)" value={clip.in_s} min={0} onChange={v => setClip(c => { c.in_s = v ?? 0 })} />
                    <NumberInput label="Out (s)" value={clip.out_s} min={0} onChange={v => setClip(c => { c.out_s = v })} />
                    <NumberInput label="Âm lượng (dB)" value={clip.volume_db} step={1} onChange={v => setClip(c => { c.volume_db = v ?? 0 })} />
                    <Field label="Tắt tiếng"><input type="checkbox" checked={clip.mute} onChange={e => setClip(c => { c.mute = e.target.checked })} /></Field>
                    <Field label="Chuyển cảnh vào">
                      <select disabled={tl.clips[0].id === clip.id} value={clip.transition_in.type} onChange={e => setClip(c => {
                        const t = e.target.value as TransitionType
                        c.transition_in = { type: t, duration: ['crossfade', 'fade_black', 'whip_pan'].includes(t) ? (t === 'whip_pan' ? 0.25 : 0.5) : 0, audio_offset: ['j_cut', 'l_cut'].includes(t) ? 0.4 : 0 }
                      })} className={inputCls} style={inputStyle}>
                        {TRANSITIONS.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
                      </select>
                    </Field>
                    <NumberInput label="Thời lượng chuyển (s)" value={clip.transition_in.duration} min={0} max={3} onChange={v => setClip(c => { c.transition_in.duration = v ?? 0 })} />
                    <NumberInput label="Lệch tiếng J/L (s)" value={clip.transition_in.audio_offset} min={0} max={3} onChange={v => setClip(c => { c.transition_in.audio_offset = v ?? 0 })} />
                    <Field label="Nhân vật (phẩy)"><input value={clip.characters.join(', ')} onChange={e => setClip(c => { c.characters = e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} className={inputCls} style={inputStyle} /></Field>
                  </div>
                  <h3 className="text-[11px] mt-4 mb-2 font-semibold" style={{ color: 'var(--muted)' }}>Màu (engine tự match giữa các shot khi render)</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <Slider label="Sáng" value={clip.color.brightness} min={-0.3} max={0.3} step={0.01} onChange={v => setClip(c => { c.color.brightness = v })} />
                    <Slider label="Tương phản" value={clip.color.contrast} min={0.5} max={1.5} step={0.01} onChange={v => setClip(c => { c.color.contrast = v })} />
                    <Slider label="Bão hoà" value={clip.color.saturation} min={0} max={2} step={0.01} onChange={v => setClip(c => { c.color.saturation = v })} />
                    <Slider label="Gamma" value={clip.color.gamma} min={0.5} max={2} step={0.01} onChange={v => setClip(c => { c.color.gamma = v })} />
                  </div>
                </Panel>
              </div>
            )}

            <div className="grid xl:grid-cols-2 gap-4">
              <Panel title="Phụ đề tiếng Việt" action={<Btn onClick={() => mutate(t => { const last = t.subtitles.at(-1); const s = last ? last.end + 0.1 : 0; t.subtitles.push({ start: s, end: s + 2, text: '' }) })}><Plus size={12} />Dòng</Btn>}>
                <div className="flex flex-col gap-2 max-h-72 overflow-auto">
                  {tl.subtitles.map((s, i) => (
                    <div key={i} className="grid grid-cols-[70px_70px_1fr_auto] gap-2 items-center">
                      <input aria-label="Bắt đầu" type="number" step={0.1} value={s.start} onChange={e => mutate(t => { t.subtitles[i].start = Number(e.target.value) })} className={inputCls} style={inputStyle} />
                      <input aria-label="Kết thúc" type="number" step={0.1} value={s.end} onChange={e => mutate(t => { t.subtitles[i].end = Number(e.target.value) })} className={inputCls} style={inputStyle} />
                      <input aria-label="Nội dung" value={s.text} onChange={e => mutate(t => { t.subtitles[i].text = e.target.value })} className={inputCls} style={inputStyle} />
                      <Btn kind="ghost" onClick={() => mutate(t => { t.subtitles.splice(i, 1) })}><Trash2 size={12} /></Btn>
                    </div>
                  ))}
                  {!tl.subtitles.length && <Empty>Chưa có phụ đề. Vùng an toàn 9:16 được áp dụng tự động.</Empty>}
                </div>
              </Panel>
              <Panel title="Đồ hoạ & nhạc" action={<Btn onClick={() => mutate(t => { t.overlays.push({ kind: 'cta', start: Math.max(0, total - 3), end: total, text: 'Theo dõi Xuân Bắc AI', subtext: '', position: 'bottom', color: 'white', accent: '0xF5B700' } as Overlay) })}><Plus size={12} />Đồ hoạ</Btn>}>
                <div className="flex flex-col gap-2">
                  {tl.overlays.map((o, i) => (
                    <div key={i} className="grid grid-cols-[110px_60px_60px_1fr_auto] gap-2 items-center">
                      <select aria-label="Loại" value={o.kind} onChange={e => mutate(t => { t.overlays[i].kind = e.target.value as Overlay['kind'] })} className={inputCls} style={inputStyle}>
                        <option value="title">Title</option><option value="lower_third">Lower third</option><option value="cta">CTA</option><option value="kinetic">Kinetic text</option><option value="logo">Logo</option>
                      </select>
                      <input aria-label="Bắt đầu" type="number" step={0.1} value={o.start} onChange={e => mutate(t => { t.overlays[i].start = Number(e.target.value) })} className={inputCls} style={inputStyle} />
                      <input aria-label="Kết thúc" type="number" step={0.1} value={o.end} onChange={e => mutate(t => { t.overlays[i].end = Number(e.target.value) })} className={inputCls} style={inputStyle} />
                      {o.kind === 'logo'
                        ? <select aria-label="Ảnh logo" value={o.image ?? ''} onChange={e => mutate(t => { t.overlays[i].image = e.target.value })} className={inputCls} style={inputStyle}>
                            <option value="">— chọn ảnh —</option>{assets.filter(a => a.kind === 'image').map(a => <option key={a.id} value={a.path}>{a.original_name}</option>)}
                          </select>
                        : <input aria-label="Chữ" value={o.text} onChange={e => mutate(t => { t.overlays[i].text = e.target.value })} className={inputCls} style={inputStyle} />}
                      <Btn kind="ghost" onClick={() => mutate(t => { t.overlays.splice(i, 1) })}><Trash2 size={12} /></Btn>
                    </div>
                  ))}
                  <div className="grid grid-cols-[1fr_90px_auto] gap-2 items-end mt-2">
                    <Field label="Nhạc nền (tự ducking dưới thoại)">
                      <select value={tl.music[0]?.source ?? ''} onChange={e => mutate(t => { t.music = e.target.value ? [{ source: e.target.value, start_s: 0, in_s: 0, duration: null, gain_db: -6, fade_in: 0.5, fade_out: 1.5 }] : [] })} className={inputCls} style={inputStyle}>
                        <option value="">— không nhạc —</option>{audioAssets.map(a => <option key={a.id} value={a.path}>{a.original_name}</option>)}
                      </select>
                    </Field>
                    <NumberInput label="LUFS đích" value={tl.audio.target_lufs} step={1} min={-31} max={-5} onChange={v => mutate(t => { t.audio.target_lufs = v ?? -14 })} />
                    <Field label="Lọc ồn"><input type="checkbox" checked={tl.audio.denoise} onChange={e => mutate(t => { t.audio.denoise = e.target.checked })} /></Field>
                  </div>
                </div>
              </Panel>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function AssetPicker({ assets, onDone }: { assets: Asset[]; onDone: (a: Asset[]) => void }) {
  const [picked, setPicked] = useState<string[]>([])
  if (!assets.length) return <Empty>Chưa có video. Tải lên ở Asset Library trước.</Empty>
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
        {assets.map(a => {
          const n = picked.indexOf(a.id)
          return (
            <button key={a.id} onClick={() => setPicked(p => n >= 0 ? p.filter(x => x !== a.id) : [...p, a.id])}
              className="rounded-lg p-2 text-left text-[10px] border" style={{ borderColor: n >= 0 ? 'var(--accent)' : 'var(--border)', background: 'var(--card)' }}>
              <div className="truncate text-xs">{n >= 0 ? `${n + 1}. ` : ''}{a.original_name}</div>
              <div style={{ color: 'var(--muted)' }}>{a.width}×{a.height} · {fmtSec(a.duration)}</div>
            </button>
          )
        })}
      </div>
      <div><Btn kind="primary" disabled={!picked.length} onClick={() => onDone(picked.map(id => assets.find(a => a.id === id)!))}>Tạo timeline ({picked.length} clip)</Btn></div>
    </div>
  )
}
