import type { CSSProperties } from 'react'

export const inputCls = 'px-2.5 py-1.5 rounded-lg text-xs outline-none w-full focus:ring-1'
export const inputStyle: CSSProperties = { background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }

export const fmtTime = (s: string | null | undefined) => (s ? new Date(s).toLocaleString('vi-VN') : '—')
export const fmtSec = (s: number | null | undefined) => (s == null ? '—' : `${s.toFixed(1)}s`)
export const fmtBytes = (b: number) => (b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} KB`)
export const errMsg = (e: unknown) => (e instanceof Error ? e.message.replace(/^API \d+: /, '') : String(e))
