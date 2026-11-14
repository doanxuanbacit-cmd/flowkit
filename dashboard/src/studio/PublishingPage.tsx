import { useCallback, useEffect, useMemo, useState } from 'react'
import { useWebSocketContext } from '../api/useWebSocketContext'
import { api, type Account, type PublishJob, type RenderJob } from './api'
import { Btn, Empty, ErrorBox, Field, Panel, Pill } from './ui'
import { errMsg, fmtTime, inputCls, inputStyle } from './format'

const CRED_TEMPLATES: Record<string, Record<string, string>> = {
  facebook: { page_id: '', page_access_token: '' },
  youtube: { client_id: '', client_secret: '', refresh_token: '' },
  tiktok: { access_token: '', refresh_token: '', client_key: '', client_secret: '', scope: 'video.publish,video.upload' },
  zalo: { access_token: '', refresh_token: '', app_id: '', secret_key: '' },
}
const PLATFORM_LABEL: Record<string, string> = { facebook: 'Facebook Reels', youtube: 'YouTube Shorts', tiktok: 'TikTok', zalo: 'Zalo OA' }

function Calendar({ month, data }: { month: string; data: Record<string, PublishJob[]> }) {
  const [y, m] = month.split('-').map(Number)
  const first = new Date(y, m - 1, 1)
  const days = new Date(y, m, 0).getDate()
  const lead = (first.getDay() + 6) % 7
  const cells = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)]
  return (
    <div className="grid grid-cols-7 gap-1 text-[10px]">
      {['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'].map(d => <div key={d} className="text-center py-1" style={{ color: 'var(--muted)' }}>{d}</div>)}
      {cells.map((d, i) => {
        const key = d ? `${month}-${String(d).padStart(2, '0')}` : ''
        const items = d ? data[key] ?? [] : []
        return (
          <div key={i} className="min-h-16 rounded p-1 flex flex-col gap-0.5" style={{ background: d ? 'var(--card)' : 'transparent' }}>
            {d && <span style={{ color: 'var(--muted)' }}>{d}</span>}
            {items.slice(0, 3).map(j => <span key={j.id} className="truncate" title={`${j.platform} · ${j.status}`}><Pill value={j.status} label={j.platform} /></span>)}
            {items.length > 3 && <span style={{ color: 'var(--muted)' }}>+{items.length - 3}</span>}
          </div>
        )
      })}
    </div>
  )
}

export default function PublishingPage() {
  const { lastEvent } = useWebSocketContext()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [jobs, setJobs] = useState<PublishJob[]>([])
  const [renders, setRenders] = useState<RenderJob[]>([])
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7))
  const [cal, setCal] = useState<Record<string, PublishJob[]>>({})
  const [error, setError] = useState<string | null>(null)
  const [conn, setConn] = useState({ platform: 'tiktok', name: '', creds: JSON.stringify(CRED_TEMPLATES.tiktok, null, 2), verify: true })
  const [form, setForm] = useState({ render_job_id: '', accounts: [] as string[], title: '', caption: '', tags: '', privacy: 'private', when: '' })

  const load = useCallback(() => {
    Promise.all([api.accounts(), api.publishJobs(), api.renders(), api.calendar(month)])
      .then(([a, j, r, c]) => { setAccounts(a); setJobs(j); setRenders(r.filter(x => x.status === 'COMPLETED' && x.approval !== 'REJECTED')); setCal(c) })
      .catch(e => setError(errMsg(e)))
  }, [month])
  useEffect(() => { load() }, [load])
  useEffect(() => { if (lastEvent?.type?.startsWith('studio_')) load() }, [lastEvent, load])

  const acctName = useMemo(() => Object.fromEntries(accounts.map(a => [a.id, `${PLATFORM_LABEL[a.platform]} · ${a.name}`])), [accounts])

  const connect = async () => {
    try {
      await api.connect({ platform: conn.platform, name: conn.name, credentials: JSON.parse(conn.creds), verify: conn.verify })
      setConn(c => ({ ...c, name: '', creds: JSON.stringify(CRED_TEMPLATES[c.platform], null, 2) }))
      setError(null); load()
    } catch (e) { setError(errMsg(e)) }
  }
  const schedule = async () => {
    try {
      const when = form.when ? new Date(form.when).toISOString() : null
      for (const account_id of form.accounts) {
        await api.publish({ render_job_id: form.render_job_id, account_id, title: form.title, caption: form.caption,
          tags: form.tags.split(/[,\s]+/).map(t => t.replace(/^#/, '')).filter(Boolean), privacy: form.privacy, scheduled_at: when })
      }
      setForm(f => ({ ...f, accounts: [], title: '', caption: '', tags: '' })); setError(null); load()
    } catch (e) { setError(errMsg(e)) }
  }
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); load() } catch (e) { setError(errMsg(e)) } }
  const selRender = renders.find(r => r.id === form.render_job_id)

  return (
    <div className="flex flex-col gap-4 max-w-[1600px]">
      <h1 className="text-lg font-semibold">Auto Publisher</h1>
      <ErrorBox error={error} />
      <div className="grid xl:grid-cols-2 gap-4">
        <Panel title="Tạo lịch đăng">
          <div className="grid md:grid-cols-2 gap-3">
            <Field label="Bản render">
              <select value={form.render_job_id} onChange={e => setForm(f => ({ ...f, render_job_id: e.target.value }))} className={inputCls} style={inputStyle}>
                <option value="">— chọn —</option>
                {renders.map(r => <option key={r.id} value={r.id}>{r.timeline_id} v{r.timeline_version} · {r.approval === 'APPROVED' ? 'đã duyệt' : 'chờ duyệt'}</option>)}
              </select>
            </Field>
            <Field label="Thời điểm đăng (trống = ngay khi duyệt)"><input type="datetime-local" value={form.when} onChange={e => setForm(f => ({ ...f, when: e.target.value }))} className={inputCls} style={inputStyle} /></Field>
            <Field label="Tiêu đề"><input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} className={inputCls} style={inputStyle} /></Field>
            <Field label="Quyền riêng tư">
              <select value={form.privacy} onChange={e => setForm(f => ({ ...f, privacy: e.target.value }))} className={inputCls} style={inputStyle}>
                <option value="private">Riêng tư</option><option value="unlisted">Không công khai (YouTube)</option><option value="public">Công khai</option><option value="friends">Bạn bè (TikTok)</option>
              </select>
            </Field>
            <div className="md:col-span-2"><Field label="Caption"><textarea rows={3} value={form.caption} onChange={e => setForm(f => ({ ...f, caption: e.target.value }))} className={inputCls} style={inputStyle} /></Field></div>
            <Field label="Hashtag"><input value={form.tags} onChange={e => setForm(f => ({ ...f, tags: e.target.value }))} placeholder="#AI #XuanBacAI" className={inputCls} style={inputStyle} /></Field>
            <fieldset className="flex flex-col gap-1 text-xs"><legend className="text-[11px] mb-1" style={{ color: 'var(--muted)' }}>Kênh</legend>
              {accounts.filter(a => a.status === 'ACTIVE').map(a => (
                <label key={a.id} className="flex items-center gap-2">
                  <input type="checkbox" checked={form.accounts.includes(a.id)} onChange={e => setForm(f => ({ ...f, accounts: e.target.checked ? [...f.accounts, a.id] : f.accounts.filter(x => x !== a.id) }))} />{acctName[a.id]}
                </label>
              ))}
              {!accounts.length && <span style={{ color: 'var(--muted)' }}>Chưa kết nối kênh nào.</span>}
            </fieldset>
          </div>
          {selRender && selRender.approval !== 'APPROVED' && <p className="text-[11px] mt-2" style={{ color: 'var(--yellow)' }}>Bản này chưa duyệt — lịch sẽ ở trạng thái chờ duyệt, không đăng tự động.</p>}
          <div className="mt-3"><Btn kind="primary" disabled={!form.render_job_id || !form.accounts.length} onClick={schedule}>Lên lịch</Btn></div>
        </Panel>

        <Panel title={`Lịch đăng ${month}`} action={<input type="month" aria-label="Tháng" value={month} onChange={e => setMonth(e.target.value)} className={inputCls + ' !w-36'} style={inputStyle} />}>
          <Calendar month={month} data={cal} />
        </Panel>
      </div>

      <Panel title="Hàng đợi đăng">
        {!jobs.length ? <Empty>Chưa có lịch đăng.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] min-w-[760px]">
              <thead><tr style={{ color: 'var(--muted)' }} className="text-left"><th className="py-1">Kênh</th><th>Trạng thái</th><th>Lịch</th><th>Lần thử</th><th>Kết quả / lỗi</th><th /></tr></thead>
              <tbody>
                {jobs.slice().reverse().map(j => (
                  <tr key={j.id} className="border-t align-top" style={{ borderColor: 'var(--border)' }}>
                    <td className="py-2 pr-2">{acctName[j.account_id] ?? j.platform}<div style={{ color: 'var(--muted)' }}>{j.title || j.caption.slice(0, 40)}</div></td>
                    <td className="pr-2"><Pill value={j.status} /></td>
                    <td className="pr-2">{fmtTime(j.scheduled_at)}</td>
                    <td className="pr-2">{j.attempts}{j.next_attempt_at ? <div style={{ color: 'var(--muted)' }}>thử lại {fmtTime(j.next_attempt_at)}</div> : null}</td>
                    <td className="pr-2 max-w-sm">
                      {j.permalink && <a href={j.permalink} target="_blank" rel="noreferrer" className="underline" style={{ color: 'var(--accent)' }}>Xem bài</a>}
                      {j.error && <div style={{ color: j.status === 'MANUAL_REQUIRED' ? 'var(--yellow)' : 'var(--red)' }}>{j.error}</div>}
                      {j.manual_package?.dir && <div style={{ color: 'var(--muted)' }}>Gói đăng thủ công: {j.manual_package.dir}</div>}
                    </td>
                    <td className="whitespace-nowrap flex gap-1 py-2">
                      {['FAILED', 'MANUAL_REQUIRED', 'SCHEDULED'].includes(j.status) && <Btn onClick={() => act(() => api.publishAction(j.id, 'retry'))}>{j.status === 'SCHEDULED' ? 'Đăng ngay' : 'Thử lại'}</Btn>}
                      {j.status === 'MANUAL_REQUIRED' && <Btn onClick={() => { const l = prompt('Link bài đã đăng thủ công:'); if (l) act(() => api.markPublished(j.id, l)) }}>Đã đăng tay</Btn>}
                      {['WAITING_APPROVAL', 'SCHEDULED', 'QUEUED', 'FAILED', 'MANUAL_REQUIRED'].includes(j.status) && <Btn kind="danger" onClick={() => act(() => api.publishAction(j.id, 'cancel'))}>Huỷ</Btn>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="grid xl:grid-cols-2 gap-4">
        <Panel title="Tài khoản đã kết nối">
          <ul className="flex flex-col gap-2 text-xs">
            {accounts.map(a => (
              <li key={a.id} className="flex items-center gap-2">
                <Pill value={a.status} />
                <span className="flex-1">{acctName[a.id]}</span>
                <span className="text-[10px] truncate max-w-48" style={{ color: 'var(--muted)' }}>{Object.entries(a.capabilities ?? {}).filter(([, v]) => typeof v === 'boolean').map(([k, v]) => `${k}:${v ? '✓' : '✗'}`).join(' ')}</span>
                {a.status !== 'DISABLED' && <Btn kind="ghost" onClick={() => act(() => api.disableAccount(a.id))}>Tắt</Btn>}
              </li>
            ))}
            {!accounts.length && <Empty>Chưa có tài khoản.</Empty>}
          </ul>
        </Panel>
        <Panel title="Kết nối tài khoản (API chính thức)">
          <div className="grid md:grid-cols-2 gap-3">
            <Field label="Nền tảng">
              <select value={conn.platform} onChange={e => setConn(c => ({ ...c, platform: e.target.value, creds: JSON.stringify(CRED_TEMPLATES[e.target.value], null, 2) }))} className={inputCls} style={inputStyle}>
                {Object.entries(PLATFORM_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="Tên hiển thị"><input value={conn.name} onChange={e => setConn(c => ({ ...c, name: e.target.value }))} className={inputCls} style={inputStyle} /></Field>
            <div className="md:col-span-2"><Field label="Credentials (JSON — mã hoá Fernet khi lưu, không bao giờ trả về)" hint="Token lấy từ OAuth của app đã được nền tảng cấp quyền. Hệ thống kiểm tra quyền thực tế khi kết nối.">
              <textarea rows={6} value={conn.creds} onChange={e => setConn(c => ({ ...c, creds: e.target.value }))} className={inputCls + ' font-mono'} style={inputStyle} spellCheck={false} />
            </Field></div>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={conn.verify} onChange={e => setConn(c => ({ ...c, verify: e.target.checked }))} />Xác minh quyền với API ngay</label>
            <div className="text-right"><Btn kind="primary" disabled={!conn.name} onClick={connect}>Kết nối</Btn></div>
          </div>
        </Panel>
      </div>
    </div>
  )
}
