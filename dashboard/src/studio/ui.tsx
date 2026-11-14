import type { ReactNode } from 'react'

export function Panel({ title, action, children, className = '' }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border p-4 ${className}`} style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {(title || action) && (
        <div className="flex items-center justify-between mb-3 gap-2">
          <h2 className="text-xs font-semibold tracking-wider uppercase" style={{ color: 'var(--muted)' }}>{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

const TONE: Record<string, string> = {
  COMPLETED: 'var(--green)', PUBLISHED: 'var(--green)', APPROVED: 'var(--green)', ACTIVE: 'var(--green)', passed: 'var(--green)',
  RUNNING: 'var(--accent)', UPLOADING: 'var(--accent)', PROCESSING: 'var(--accent)', QUEUED: 'var(--accent)', SCHEDULED: 'var(--accent)',
  PENDING: 'var(--yellow)', WAITING_APPROVAL: 'var(--yellow)', MANUAL_REQUIRED: 'var(--yellow)', NEEDS_REAUTH: 'var(--yellow)', warning: 'var(--yellow)',
  FAILED: 'var(--red)', REJECTED: 'var(--red)', CANCELLED: 'var(--muted)', DISABLED: 'var(--muted)', error: 'var(--red)', info: 'var(--muted)',
}

export function Pill({ value, label }: { value: string; label?: string }) {
  const c = TONE[value] ?? 'var(--muted)'
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap"
      style={{ color: c, background: `color-mix(in srgb, ${c} 14%, transparent)`, border: `1px solid color-mix(in srgb, ${c} 35%, transparent)` }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: c }} />{label ?? value}
    </span>
  )
}

export function Btn({ children, onClick, kind = 'default', disabled, type = 'button', title }: { children: ReactNode; onClick?: () => void; kind?: 'default' | 'primary' | 'danger' | 'ghost'; disabled?: boolean; type?: 'button' | 'submit'; title?: string }) {
  const styles: Record<string, React.CSSProperties> = {
    default: { background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' },
    primary: { background: 'var(--accent)', color: '#fff', border: '1px solid var(--accent)' },
    danger: { background: 'transparent', color: 'var(--red)', border: '1px solid color-mix(in srgb, var(--red) 50%, transparent)' },
    ghost: { background: 'transparent', color: 'var(--muted)', border: '1px solid transparent' },
  }
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-opacity hover:opacity-85 disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2"
      style={styles[kind]}>
      {children}
    </button>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-[11px]" style={{ color: 'var(--muted)' }}>
      <span>{label}</span>
      {children}
      {hint && <span className="text-[10px] opacity-75">{hint}</span>}
    </label>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-xs py-8 text-center" style={{ color: 'var(--muted)' }}>{children}</div>
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null
  return <div role="alert" className="text-xs rounded-lg px-3 py-2 mb-3" style={{ color: 'var(--red)', background: 'color-mix(in srgb, var(--red) 10%, transparent)' }}>{error}</div>
}

