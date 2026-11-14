import { useCallback, useEffect, useState } from 'react'
import { useWebSocketContext } from '../api/useWebSocketContext'
import { api, fileUrl, type FullQA, type QAIssue, type RenderJob } from './api'
import { Btn, Empty, ErrorBox, Field, Panel, Pill } from './ui'
import { errMsg, fmtSec, fmtTime, inputCls, inputStyle } from './format'

function IssueTable({ issues }: { issues: QAIssue[] }) {
  if (!issues.length) return <p className="text-xs" style={{ color: 'var(--green)' }}>Không phát hiện lỗi.</p>
  return (
    <table className="w-full text-[11px]">
      <thead><tr style={{ color: 'var(--muted)' }}><th className="text-left py-1">Mức</th><th className="text-left">Mã</th><th className="text-left">Mô tả</th><th className="text-left">Clip</th></tr></thead>
      <tbody>
        {issues.map((i, k) => (
          <tr key={k} className="border-t" style={{ borderColor: 'var(--border)' }}>
            <td className="py-1.5 pr-2"><Pill value={i.severity} /></td>
            <td className="pr-2 font-mono">{i.code}</td>
            <td className="pr-2">{i.message}</td>
            <td style={{ color: 'var(--muted)' }}>{i.clip_id ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function QAPage() {
  const { lastEvent } = useWebSocketContext()
  const [jobs, setJobs] = useState<RenderJob[]>([])
  const [sel, setSel] = useState<RenderJob | null>(null)
  const [qaState, setQaState] = useState<{ id: string; data: FullQA } | null>(null)
  const [viewState, setViewState] = useState<{ id: string; v: string } | null>(null)
  const [reviewer, setReviewer] = useState(() => localStorage.getItem('studio.reviewer') ?? 'Anh Bắc')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => api.renders().then(js => {
    setJobs(js)
    setSel(s => (s ? js.find(j => j.id === s.id) ?? s : js.find(j => j.status === 'COMPLETED') ?? null))
  }).catch(e => setError(errMsg(e))), [])
  useEffect(() => { load() }, [load])
  useEffect(() => { if (lastEvent?.type === 'studio_render') load() }, [lastEvent, load])
  useEffect(() => {
    if (sel?.status !== 'COMPLETED') return
    const id = sel.id
    api.renderQA(id).then(data => setQaState({ id, data })).catch(() => setQaState(null))
  }, [sel?.id, sel?.status])
  const qa = qaState && qaState.id === sel?.id ? qaState.data : null
  const view = viewState && viewState.id === sel?.id ? viewState.v : 'master'
  const setView = (v: string) => sel && setViewState({ id: sel.id, v })

  const decide = async (approve: boolean) => {
    if (!sel) return
    try {
      try { localStorage.setItem('studio.reviewer', reviewer) } catch { /* storage unavailable */ }
      const j = await api.review(sel.id, approve, reviewer, note)
      setSel(j); setNote(''); load(); setError(null)
    } catch (e) { setError(errMsg(e)) }
  }

  const res = sel?.result
  const views = res ? ['master', ...res.renditions.map(r => r.platform)] : []

  return (
    <div className="flex flex-col lg:flex-row gap-4 max-w-[1600px]">
      <Panel title="Render Queue" className="lg:w-80 shrink-0">
        <ul className="flex flex-col gap-1 max-h-[75vh] overflow-auto">
          {jobs.map(j => (
            <li key={j.id}>
              <button onClick={() => setSel(j)} className="w-full text-left px-2 py-2 rounded text-xs flex flex-col gap-1"
                style={{ background: sel?.id === j.id ? 'var(--card)' : 'transparent', borderLeft: `2px solid ${sel?.id === j.id ? 'var(--accent)' : 'transparent'}` }}>
                <div className="flex items-center gap-2"><Pill value={j.status} />{j.status === 'COMPLETED' && <Pill value={j.approval} />}{j.qa_passed === 0 && <Pill value="error" label="QA lỗi" />}</div>
                <span className="truncate">{j.timeline_id} · v{j.timeline_version}</span>
                {j.status === 'RUNNING' && (
                  <div className="h-1 rounded bg-black/40 overflow-hidden" role="progressbar" aria-valuenow={Math.round(j.progress * 100)}>
                    <div className="h-full" style={{ width: `${j.progress * 100}%`, background: 'var(--accent)' }} />
                  </div>
                )}
                <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{j.stage ?? ''} {fmtTime(j.created_at)}</span>
              </button>
            </li>
          ))}
          {!jobs.length && <Empty>Chưa có render.</Empty>}
        </ul>
      </Panel>

      <div className="flex-1 min-w-0 flex flex-col gap-4">
        <ErrorBox error={error} />
        {!sel ? <Panel><Empty>Chọn một render.</Empty></Panel> : sel.status === 'FAILED' ? (
          <Panel title="Render lỗi"><pre className="text-[11px] whitespace-pre-wrap" style={{ color: 'var(--red)' }}>{sel.error}</pre></Panel>
        ) : sel.status !== 'COMPLETED' ? (
          <Panel title="Đang xử lý">
            <p className="text-xs">{sel.stage ?? 'Đang chờ'} — {Math.round(sel.progress * 100)}%</p>
            {sel.status === 'QUEUED' && <div className="mt-3"><Btn kind="danger" onClick={() => api.cancelRender(sel.id).then(load)}>Huỷ</Btn></div>}
          </Panel>
        ) : res && (
          <>
            <div className="grid xl:grid-cols-[minmax(0,360px)_1fr] gap-4">
              <Panel title="Xem lại" action={
                <select aria-label="Bản xuất" value={view} onChange={e => setView(e.target.value)} className={inputCls + ' !w-40'} style={inputStyle}>
                  {views.map(v => <option key={v} value={v}>{v}</option>)}
                </select>}>
                <video key={view} src={fileUrl(sel.id, view)} controls className="w-full rounded bg-black max-h-[560px]" />
                <div className="flex flex-wrap gap-2 mt-3 text-[11px]">
                  {['thumbnail', 'srt', 'metadata', 'manifest', 'qa_report'].map(f => (
                    <a key={f} href={fileUrl(sel.id, f)} target="_blank" rel="noreferrer" className="underline" style={{ color: 'var(--accent)' }}>{f}</a>
                  ))}
                </div>
              </Panel>
              <div className="flex flex-col gap-4">
                <Panel title="Kết quả" action={<Pill value={res.qa_passed ? 'passed' : 'error'} label={res.qa_passed ? 'QA đạt' : 'QA có lỗi'} />}>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                    <div><div style={{ color: 'var(--muted)' }}>Thời lượng</div>{fmtSec(res.duration)}</div>
                    <div><div style={{ color: 'var(--muted)' }}>Loudness</div>{String(qa?.master.metrics.lufs ?? '—')} LUFS</div>
                    <div><div style={{ color: 'var(--muted)' }}>True peak</div>{String(qa?.master.metrics.true_peak ?? '—')} dBTP</div>
                    <div><div style={{ color: 'var(--muted)' }}>Cache</div>{res.cache_hits} hit / {res.cache_misses} render</div>
                  </div>
                  {res.renditions.some(r => r.upscaled) && <p className="text-[11px] mt-2" style={{ color: 'var(--yellow)' }}>Nguồn nhỏ hơn khung xuất — bản xuất được phóng lớn, không phải độ phân giải gốc.</p>}
                  {res.notes.length > 0 && <ul className="mt-2 text-[11px] list-disc pl-4" style={{ color: 'var(--muted)' }}>{res.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
                </Panel>
                <Panel title="Phê duyệt" action={<Pill value={sel.approval} />}>
                  {sel.approval !== 'PENDING' ? (
                    <p className="text-xs">{sel.approval === 'APPROVED' ? 'Đã duyệt' : 'Đã từ chối'} bởi <b>{sel.approved_by}</b>{sel.approval_note ? ` — “${sel.approval_note}”` : ''}</p>
                  ) : (
                    <div className="grid md:grid-cols-[160px_1fr_auto_auto] gap-2 items-end">
                      <Field label="Người duyệt"><input value={reviewer} onChange={e => setReviewer(e.target.value)} className={inputCls} style={inputStyle} /></Field>
                      <Field label={res.qa_passed ? 'Ghi chú' : 'Ghi chú (bắt buộc khi QA lỗi)'}><input value={note} onChange={e => setNote(e.target.value)} className={inputCls} style={inputStyle} /></Field>
                      <Btn kind="primary" onClick={() => decide(true)} disabled={!reviewer || (!res.qa_passed && !note.trim())}>Duyệt</Btn>
                      <Btn kind="danger" onClick={() => decide(false)} disabled={!reviewer}>Từ chối</Btn>
                    </div>
                  )}
                </Panel>
              </div>
            </div>
            {qa && (
              <div className="grid xl:grid-cols-2 gap-4">
                <Panel title="QA kỹ thuật (master)"><IssueTable issues={qa.master.issues} /></Panel>
                <Panel title="Continuity giữa các shot">
                  {qa.contact_sheet && <img src={fileUrl(sel.id, 'continuity_sheet')} alt="Ảnh cặp khung hình tại các điểm nối" className="rounded mb-3 max-h-64" />}
                  <IssueTable issues={qa.continuity} />
                </Panel>
                {Object.entries(qa.renditions).map(([k, r]) => (
                  <Panel key={k} title={`QA ${k}`} action={<Pill value={r.passed ? 'passed' : 'error'} label={r.passed ? 'Đạt' : 'Lỗi'} />}><IssueTable issues={r.issues} /></Panel>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
