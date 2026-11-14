import { useState, useEffect } from 'react'
import { BrowserRouter, NavLink, Routes, Route, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { LayoutDashboard, FolderOpen, Film, ScrollText, BookOpen, SlidersHorizontal, Globe, Sparkles, Scissors, Library, ShieldCheck, Send } from 'lucide-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { WebSocketProvider } from './api/WebSocketContext'
import { useWebSocketContext } from './api/useWebSocketContext'
import { LanguageProvider } from './i18n/LanguageContext'
import { useTranslation } from './i18n/useTranslation'
import { LANGS, LANG_LABELS, type Lang } from './i18n/translations'
import type { TranslationKey } from './i18n/translations'
import { fetchAPI } from './api/client'
import type { Project } from './types'
import DashboardPage from './pages/DashboardPage'
import ProjectsPage from './pages/ProjectsPage'
import LogsPage from './pages/LogsPage'
import GalleryPage from './pages/GalleryPage'
import GuidePage from './pages/GuidePage'
import SettingsPage from './pages/SettingsPage'
import StudioOverview from './studio/StudioOverview'
import EditorPage from './studio/EditorPage'
import AssetsPage from './studio/AssetsPage'
import QAPage from './studio/QAPage'
import PublishingPage from './studio/PublishingPage'

const STUDIO_NAV: { to: string; icon: typeof LayoutDashboard; label: string }[] = [
  { to: '/studio', icon: Sparkles, label: 'Studio Overview' },
  { to: '/studio/editor', icon: Scissors, label: 'Professional Editor' },
  { to: '/studio/assets', icon: Library, label: 'Asset Library' },
  { to: '/studio/qa', icon: ShieldCheck, label: 'Render · QA · Duyệt' },
  { to: '/studio/publish', icon: Send, label: 'Auto Publisher' },
]
const STUDIO_CRUMB: Record<string, string> = {
  '/studio': 'Overview', '/studio/editor': 'Professional Editor', '/studio/assets': 'Asset Library',
  '/studio/qa': 'Render · QA', '/studio/publish': 'Auto Publisher',
}

const NAV: { to: string; icon: typeof LayoutDashboard; labelKey: TranslationKey; exact: boolean }[] = [
  { to: '/', icon: LayoutDashboard, labelKey: 'nav.dashboard', exact: true },
  { to: '/projects', icon: FolderOpen, labelKey: 'nav.projects', exact: false },
  { to: '/gallery', icon: Film, labelKey: 'nav.gallery', exact: false },
  { to: '/logs', icon: ScrollText, labelKey: 'nav.logs', exact: false },
  { to: '/guide', icon: BookOpen, labelKey: 'nav.guide', exact: false },
  { to: '/settings', icon: SlidersHorizontal, labelKey: 'nav.settings', exact: false },
]

const BREADCRUMB_TAB_KEY: Record<string, TranslationKey> = {
  overview: 'app.breadcrumbTab.overview',
  characters: 'app.breadcrumbTab.characters',
  videos: 'app.breadcrumbTab.videos',
  pipeline: 'app.breadcrumbTab.pipeline',
}

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

function useBreadcrumbs() {
  const { t } = useTranslation()
  const loc = useLocation()
  const { id } = useParams<{ id?: string }>()
  const [searchParams] = useSearchParams()
  const [projectName, setProjectName] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    fetchAPI<Project>(`/api/projects/${id}`).then(p => setProjectName(p.name)).catch(() => setProjectName(null))
  }, [id])

  const crumbs: string[] = []
  if (loc.pathname === '/') crumbs.push(t('app.breadcrumb.dashboard'))
  else if (loc.pathname.startsWith('/projects')) {
    crumbs.push(t('app.breadcrumb.projects'))
    if (id) {
      crumbs.push(projectName ?? '…')
      const tab = searchParams.get('tab')
      const tabKey = tab ? BREADCRUMB_TAB_KEY[tab] : undefined
      if (tabKey) crumbs.push(t(tabKey))
    }
  } else if (loc.pathname.startsWith('/gallery')) crumbs.push(t('app.breadcrumb.gallery'))
  else if (loc.pathname.startsWith('/logs')) crumbs.push(t('app.breadcrumb.logs'))
  else if (loc.pathname.startsWith('/guide')) crumbs.push(t('app.breadcrumb.guide'))
  else if (loc.pathname.startsWith('/settings')) crumbs.push(t('app.breadcrumb.settings'))
  else if (loc.pathname.startsWith('/studio')) crumbs.push('Studio V4', STUDIO_CRUMB[loc.pathname] ?? '')

  return crumbs
}

function LanguageSwitcher() {
  const { lang, setLang, t } = useTranslation()
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5 text-[10px]" style={{ color: 'var(--muted)' }}>
        <Globe size={11} />
        <span>{t('app.language')}</span>
      </div>
      <select
        value={lang}
        onChange={e => setLang(e.target.value as Lang)}
        className="text-[10px] px-2 py-1.5 rounded outline-none w-full cursor-pointer"
        style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
        aria-label={t('app.language')}
      >
        {LANGS.map(l => (
          <option key={l} value={l}>{LANG_LABELS[l]}</option>
        ))}
      </select>
    </div>
  )
}

function Sidebar() {
  const { t } = useTranslation()
  const { worker } = useWebSocketContext()
  const [health, setHealth] = useState<{ extension_connected: boolean } | null>(null)

  useEffect(() => {
    fetchAPI<{ extension_connected: boolean }>('/health').then(setHealth).catch(() => setHealth(null))
  }, [])

  return (
    <aside className="w-52 flex-shrink-0 hidden md:flex flex-col border-r" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-4 py-4 flex items-center gap-2.5 border-b" style={{ borderColor: 'var(--border)' }}>
        <span className="w-[22px] h-[22px] rounded flex items-center justify-center text-xs font-bold" style={{ background: 'var(--accent)', color: 'var(--bg)' }}>F</span>
        <div className="flex flex-col">
          <span className="text-xs font-bold tracking-widest">{t('app.brandName')}</span>
          <span className="text-[9px] tracking-wide" style={{ color: 'var(--muted)' }}>{t('app.brandTag')}</span>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5 px-2.5 py-3">
        {NAV.map(({ to, icon: Icon, labelKey, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
            className="flex items-center gap-2.5 px-2.5 py-2 rounded text-xs transition-colors hover:opacity-90"
            style={({ isActive }) => ({
              background: isActive ? 'var(--card)' : 'transparent',
              color: isActive ? 'var(--text)' : 'var(--muted)',
              borderLeft: `2px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
            })}
          >
            <Icon size={13} />
            {t(labelKey)}
          </NavLink>
        ))}
        <div className="mt-3 mb-1 px-2.5 text-[9px] tracking-widest uppercase" style={{ color: 'var(--muted)' }}>Studio V4</div>
        {STUDIO_NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/studio'}
            className="flex items-center gap-2.5 px-2.5 py-2 rounded text-xs transition-colors hover:opacity-90"
            style={({ isActive }) => ({
              background: isActive ? 'var(--card)' : 'transparent',
              color: isActive ? 'var(--text)' : 'var(--muted)',
              borderLeft: `2px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
            })}
          >
            <Icon size={13} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto px-4 py-3.5 border-t flex flex-col gap-2.5" style={{ borderColor: 'var(--border)' }}>
        <LanguageSwitcher />
        <div className="flex items-center justify-between text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
          <span>{t('app.workers')}</span>
          <span style={{ color: 'var(--text)' }}>{worker ? `${worker.active}/${worker.active + worker.slots}` : '—'}</span>
        </div>
        <div className="flex items-center gap-1.5 text-[10px]" style={{ color: 'var(--muted)' }}>
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: health?.extension_connected ? 'var(--green)' : 'var(--red)' }}
          />
          {health?.extension_connected ? t('app.extensionConnected') : health ? t('app.extensionDisconnected') : t('app.extensionChecking')}
        </div>
      </div>
    </aside>
  )
}

function Header() {
  const { lang, setLang, t } = useTranslation()
  const { isConnected } = useWebSocketContext()
  const crumbs = useBreadcrumbs()
  const clock = useClock()

  return (
    <header className="flex items-center gap-4 px-5 h-13 flex-shrink-0 border-b" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="flex items-center gap-2 text-[11px] tracking-wide" style={{ color: 'var(--muted)' }}>
        <span style={{ color: 'var(--accent)' }}>{t('app.breadcrumbRoot')}</span>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-2">
            <span>/</span>
            <span style={{ color: i === crumbs.length - 1 ? 'var(--text)' : 'var(--muted)' }}>{c}</span>
          </span>
        ))}
      </div>
      <span className="ml-auto" />
      <div className="flex items-center gap-3 text-[10px]" style={{ color: 'var(--muted)' }}>
        <div className="flex items-center gap-1.5 px-2 py-1 rounded border" style={{ borderColor: 'var(--border)' }}>
          <Globe size={11} style={{ color: 'var(--muted)' }} />
          <select
            value={lang}
            onChange={e => setLang(e.target.value as Lang)}
            className="text-[10px] bg-transparent outline-none cursor-pointer"
            style={{ color: 'var(--text)' }}
            aria-label={t('app.language')}
          >
            {LANGS.map(l => (
              <option key={l} value={l} style={{ background: 'var(--surface)', color: 'var(--text)' }}>
                {LANG_LABELS[l]}
              </option>
            ))}
          </select>
        </div>
        <span className="tracking-wide">{clock.toLocaleTimeString()}</span>
        <span className="flex items-center gap-1.5 px-2.5 py-1 rounded border" style={{ borderColor: 'var(--border)', color: isConnected ? 'var(--green)' : 'var(--red)' }}>
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: isConnected ? 'var(--green)' : 'var(--red)', animation: isConnected ? 'pulse 2s ease-in-out infinite' : 'none' }}
          />
          {isConnected ? t('app.wsLive') : t('app.wsDisconnected')}
        </span>
      </div>
    </header>
  )
}

function MobileNav() {
  const { t } = useTranslation()
  const items = [...NAV.map(n => ({ to: n.to, label: t(n.labelKey), exact: n.exact })), ...STUDIO_NAV.map(n => ({ to: n.to, label: n.label, exact: n.to === '/studio' }))]
  return (
    <nav aria-label="Mobile" className="md:hidden flex gap-1 overflow-x-auto px-3 py-2 border-b" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {items.map(i => (
        <NavLink key={i.to} to={i.to} end={i.exact} className="px-2.5 py-1 rounded text-[11px] whitespace-nowrap"
          style={({ isActive }) => ({ background: isActive ? 'var(--card)' : 'transparent', color: isActive ? 'var(--text)' : 'var(--muted)' })}>
          {i.label}
        </NavLink>
      ))}
    </nav>
  )
}

function Layout() {
  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar />
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header />
        <MobileNav />
        <main className="flex-1 overflow-auto p-5">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:id" element={<ProjectsPage />} />
            <Route path="/gallery" element={<GalleryPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/guide" element={<GuidePage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/studio" element={<StudioOverview />} />
            <Route path="/studio/editor" element={<EditorPage />} />
            <Route path="/studio/assets" element={<AssetsPage />} />
            <Route path="/studio/qa" element={<QAPage />} />
            <Route path="/studio/publish" element={<PublishingPage />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <LanguageProvider>
        <WebSocketProvider>
          <TooltipProvider>
            <Layout />
          </TooltipProvider>
        </WebSocketProvider>
      </LanguageProvider>
    </BrowserRouter>
  )
}
