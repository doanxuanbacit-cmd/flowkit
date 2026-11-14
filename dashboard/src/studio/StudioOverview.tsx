import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useWebSocketContext } from '../api/useWebSocketContext'
import { api, type AuditRow, type RenderJob } from './api'
import { Panel, Pill, ErrorBox } from './ui'
import { fmtTime, errMsg } from './format'

type Counts = Record<string, number>

function Stat({ label, value, to }: { label: string; value: number | string; to: string }) {
  return (
    <Link to={to} className="rounded-xl border p-4 flex flex-col gap-1 hover:opacity-90" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--muted)' }}>{label}</span>
      <span className="text-2xl font-semibold">{value}</span>
    </Link>
  )
}

export default function StudioOverview() {
  const { lastEvent } = useWebSocketContext()
  const [ov, setOv] = useState<Record<string, Counts | number> | null>(null)
  const [renders, setRenders] = useState<RenderJob[]>([])
  const [audit, setAudit] = useState<AuditRow[]>([])
  const [health, setHealth] = useState<Awaited<ReturnType<typeof api.health>> | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    Promise.all([api.overview(), api.renders(), api.audit(), api.health()])
      .then(([o, r, a, h]) => { setOv(o); setRenders(r.slice(0, 8)); setAudit(a.slice(0, 15)); setHealth(h); setError(null) })
      .catch(e => setError(errMsg(e)))
  }, [])
  useEffect(load, [load])
  useEffect(() => { if (lastEvent?.type?.startsWith('studio_')) load() }, [lastEvent, load])

  const sum = (k: string) => Object.values((ov?.[k] as Counts) ?? {}).reduce((a, b) => a + b, 0)
  const c = (k: string, s: string) => ((ov?.[k] as Counts) ?? {})[s] ?? 0

  return (
    <div className="flex flex-col gap-4 max-w-7xl">
      <div>
        <h1 className="text-lg font-semibold">Xuân Bắc AI Video Studio V4</h1>
        <p className="text-xs" style={{ color: 'var(--muted)' }}>Ý tưởng → FlowKit → Dựng chuyên nghiệp → QA → Phê duyệt → Đăng đa nền tảng</p>
      </div>
      <ErrorBox error={error} />
      {health && !health.secret_key_set && (
        <div className="text-xs rounded-lg px-3 py-2" style={{ color: 'var(--yellow)', background: 'color-mix(in srgb, var(--yellow) 10%, transparent)' }}>
          Chưa đặt STUDIO_SECRET_KEY — chưa thể kết nối tài khoản mạng xã hội (credential bắt buộc mã hoá).
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Stat label="Timeline" value={(ov?.timelines as number) ?? 0} to="/studio/editor" />
        <Stat label="Asset" value={sum('assets')} to="/studio/assets" />
        <Stat label="Đang render" value={c('renders', 'RUNNING') + c('renders', 'QUEUED')} to="/studio/qa" />
        <Stat label="Chờ duyệt" value={c('approvals', 'PENDING')} to="/studio/qa" />
        <Stat label="Lịch đăng" value={c('publish', 'SCHEDULED') + c('publish', 'QUEUED') + c('publish', 'WAITING_APPROVAL')} to="/studio/publish" />
        <Stat label="Đã đăng" value={c('publish', 'PUBLISHED')} to="/studio/publish" />
      </div>
      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Render gần đây">
          <ul className="flex flex-col divide-y" style={{ borderColor: 'var(--border)' }}>
            {renders.map(r => (
              <li key={r.id} className="py-2 flex items-center gap-3 text-xs">
                <Pill value={r.status} />
                <span className="truncate flex-1">{r.timeline_id} · v{r.timeline_version}</span>
                {r.status === 'RUNNING' && <span style={{ color: 'var(--muted)' }}>{r.stage} {Math.round(r.progress * 100)}%</span>}
                {r.status === 'COMPLETED' && <Pill value={r.approval} />}
              </li>
            ))}
            {!renders.length && <li className="py-6 text-xs text-center" style={{ color: 'var(--muted)' }}>Chưa có render.</li>}
          </ul>
        </Panel>
        <Panel title="Nhật ký hệ thống (audit)">
          <ul className="flex flex-col gap-1.5 text-[11px] max-h-80 overflow-auto">
            {audit.map(a => (
              <li key={a.id} className="flex gap-2">
                <span className="shrink-0 tabular-nums" style={{ color: 'var(--muted)' }}>{fmtTime(a.ts)}</span>
                <span className="font-medium">{a.action}</span>
                <span className="truncate" style={{ color: 'var(--muted)' }}>{a.actor} · {a.entity_id?.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      {health && <p className="text-[10px]" style={{ color: 'var(--muted)' }}>{health.ffmpeg} · DB v{health.db_version} · {health.skills} editing skills · Phê duyệt bắt buộc: {health.approval_required ? 'Có' : 'Không'}</p>}
    </div>
  )
}
