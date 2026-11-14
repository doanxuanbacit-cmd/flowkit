import { useCallback, useEffect, useState, type DragEvent } from 'react'
import { Upload, Trash2 } from 'lucide-react'
import { api, assetUrl, type Asset } from './api'
import { Btn, Empty, ErrorBox, Panel, Pill } from './ui'
import { errMsg, fmtBytes, fmtSec, inputCls, inputStyle } from './format'

const KINDS = ['', 'video', 'audio', 'image', 'subtitle', 'lut']

export default function AssetsPage() {
  const [items, setItems] = useState<Asset[]>([])
  const [kind, setKind] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drag, setDrag] = useState(false)

  const load = useCallback(() => { api.assets(kind || undefined).then(setItems).catch(e => setError(errMsg(e))) }, [kind])
  useEffect(load, [load])

  const upload = async (files: FileList | File[]) => {
    setBusy(true)
    setError(null)
    try {
      for (const f of Array.from(files)) await api.upload(f)
      load()
    } catch (e) { setError(errMsg(e)) } finally { setBusy(false) }
  }
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files) }

  return (
    <div className="flex flex-col gap-4 max-w-7xl">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold mr-auto">Video Asset Library</h1>
        <select aria-label="Loại asset" value={kind} onChange={e => setKind(e.target.value)} className={inputCls + ' !w-40'} style={inputStyle}>
          {KINDS.map(k => <option key={k} value={k}>{k || 'Tất cả loại'}</option>)}
        </select>
        <label className="cursor-pointer">
          <input type="file" multiple className="sr-only" onChange={e => e.target.files && upload(e.target.files)} accept="video/*,audio/*,image/*,.srt,.ass,.cube" />
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium" style={{ background: 'var(--accent)', color: '#fff' }}><Upload size={13} />{busy ? 'Đang tải…' : 'Tải lên'}</span>
        </label>
      </div>
      <ErrorBox error={error} />
      <div onDragOver={e => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
        className="rounded-xl border-2 border-dashed p-5 text-center text-xs" style={{ borderColor: drag ? 'var(--accent)' : 'var(--border)', color: 'var(--muted)' }}>
        Kéo thả clip Google Flow, nhạc, logo, LUT (.cube) hoặc phụ đề vào đây. File trùng nội dung được nhận diện tự động (SHA-256).
      </div>
      <Panel title={`${items.length} asset`}>
        {!items.length ? <Empty>Chưa có asset. Tải lên hoặc import từ FlowKit trong Professional Editor.</Empty> : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
            {items.map(a => (
              <figure key={a.id} className="rounded-lg overflow-hidden border flex flex-col" style={{ borderColor: 'var(--border)', background: 'var(--card)' }}>
                <div className="aspect-[9/16] bg-black flex items-center justify-center">
                  {a.kind === 'video' ? <video src={assetUrl(a.id)} className="w-full h-full object-contain" preload="metadata" muted controls />
                    : a.kind === 'image' ? <img src={assetUrl(a.id)} alt={a.original_name} className="w-full h-full object-contain" />
                    : a.kind === 'audio' ? <audio src={assetUrl(a.id)} controls className="w-11/12" />
                    : <span className="text-xs" style={{ color: 'var(--muted)' }}>{a.kind.toUpperCase()}</span>}
                </div>
                <figcaption className="p-2 flex flex-col gap-1 text-[10px]">
                  <span className="truncate text-xs" title={a.original_name}>{a.original_name}</span>
                  <span style={{ color: 'var(--muted)' }}>{a.width ? `${a.width}×${a.height} · ` : ''}{fmtSec(a.duration)} · {fmtBytes(a.size_bytes)}</span>
                  <div className="flex items-center justify-between">
                    <Pill value={a.source === 'flowkit' ? 'ACTIVE' : 'info'} label={a.source} />
                    <Btn kind="ghost" title="Xoá" onClick={() => confirm(`Xoá ${a.original_name}?`) && api.deleteAsset(a.id).then(load)}><Trash2 size={12} /></Btn>
                  </div>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}
