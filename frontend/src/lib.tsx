import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Band, Decision, Severity, RunState, RunRow, RunDetail, Approval, AuditEvent,
  Me, Repo, LlmKey, AccountRow } from './types'

// ---------------------------------------------------------------- auth (token shim, 06 §9)
type Role = 'viewer' | 'approver' | 'admin'
const RANK: Record<Role, number> = { viewer: 0, approver: 1, admin: 2 }
const AUTH = {
  token: sessionStorage.getItem('token') || '', role: (sessionStorage.getItem('role') as Role) || 'approver',
  mode: 'tokens' as AuthMode,
  onLinkRequired: () => {},   // set by AuthProvider: an API 403 `link_required` flips the SPA to the link page
}
type AuthMode = 'tokens' | 'oidc'
// OIDC mode: the session is an HttpOnly cookie the Gateway set; the SPA never holds a token.
const signIn = () => { window.location.href = '/auth/login' }

// verify a token against the backend; returns its real role (throws on 401). Used by the login gate.
async function verifyToken(token: string): Promise<Role> {
  const r = await fetch('/api/v1/whoami', { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (!r.ok) throw new Error(`${r.status}`)
  return (await r.json()).role as Role
}

type AuthState = {
  role: Role; token: string
  openMode: boolean | null   // null = still discovering; true = no auth required
  verified: boolean          // cleared to false in token mode until a valid token is entered
  authMode: AuthMode
  me: Me | null              // OIDC only: account, linked code hosts, link gate
  authError: string          // OIDC only: whoami refused (e.g. account suspended)
  refreshMe: () => Promise<void>
  set: (r: Role, t: string) => void
  login: (t: string) => Promise<void>
  logout: () => void
}
const AuthCtx = createContext<AuthState>({
  role: AUTH.role, token: AUTH.token, openMode: null, verified: false,
  authMode: 'tokens', me: null, authError: '', refreshMe: async () => {},
  set: () => {}, login: async () => {}, logout: () => {},
})
export const useAuth = () => useContext(AuthCtx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<Role>(AUTH.role)
  const [token, setTok] = useState(AUTH.token)
  const [openMode, setOpenMode] = useState<boolean | null>(null)
  const [verified, setVerified] = useState(false)
  const [authMode, setAuthMode] = useState<AuthMode>('tokens')
  const [me, setMe] = useState<Me | null>(null)
  const [authError, setAuthError] = useState('')

  const refreshMe = useCallback(async () => {
    const r = await fetch('/api/v1/whoami').catch(() => null)
    if (r?.status === 401) return signIn()
    if (!r?.ok) { setAuthError(r ? await errText(r) : 'Gateway unreachable.'); return }
    const j: Me = await r.json()
    AUTH.role = j.role as Role
    setMe(j); setRole(j.role as Role); setAuthError(''); setVerified(true)
  }, [])

  useEffect(() => {
    AUTH.onLinkRequired = () => setMe(m => (m ? { ...m, link_required: true } : m))
    let alive = true
    fetch('/healthz').then(r => r.json()).then(async ({ open_mode, auth_mode }) => {
      if (!alive) return
      if (auth_mode === 'oidc') {
        AUTH.mode = 'oidc'; AUTH.token = ''; setTok('')
        setAuthMode('oidc'); setOpenMode(false)
        await refreshMe()
        return
      }
      if (open_mode) { AUTH.token = ''; setOpenMode(true); setVerified(true); return }
      setOpenMode(false)
      if (AUTH.token) {  // resume a stored session if the token still verifies
        try { const r = await verifyToken(AUTH.token); if (alive) { AUTH.role = r; setRole(r); setVerified(true) } }
        catch { /* fall through to the login gate */ }
      }
    }).catch(() => { if (alive) { setOpenMode(true); setVerified(true) } })  // healthz down → don't hard-lock the UI
    return () => { alive = false }
  }, [refreshMe])

  const set = (r: Role, t: string) => {  // OPEN_MODE demo controls (nav dropdown + token input)
    AUTH.role = r; AUTH.token = t
    sessionStorage.setItem('role', r); sessionStorage.setItem('token', t)
    setRole(r); setTok(t)
  }
  const login = async (t: string) => {
    const r = await verifyToken(t)  // throws on invalid token → caller shows the error
    AUTH.token = t; AUTH.role = r
    sessionStorage.setItem('token', t); sessionStorage.setItem('role', r)
    setTok(t); setRole(r); setVerified(true)
  }
  const logout = () => {
    if (AUTH.mode === 'oidc') { window.location.href = '/auth/logout'; return }
    fetch('/api/v1/logout', { method: 'POST' }).catch(() => {})  // clear the HttpOnly SSE cookie
    AUTH.token = ''; sessionStorage.removeItem('token')
    setTok(''); setVerified(false)
  }
  return <AuthCtx.Provider value={{ role, token, openMode, verified, authMode, me, authError, refreshMe, set, login, logout }}>
    {children}</AuthCtx.Provider>
}

export function RoleGate({ need, children }: { need: Role; children: ReactNode }) {
  const { role } = useAuth()
  return RANK[role] >= RANK[need] ? <>{children}</> : null
}

// ---------------------------------------------------------------- REST
// FastAPI errors are {"detail": "..."}: surface the sentence (quota, suspension, ownership), not the JSON.
async function errText(r: Response): Promise<string> {
  const body = await r.text().catch(() => '')
  try { const d = JSON.parse(body).detail; if (typeof d === 'string') return d } catch { /* not JSON */ }
  return body || r.statusText
}
export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(init?.headers as any) }
  if (AUTH.token) headers.Authorization = `Bearer ${AUTH.token}`
  const r = await fetch(path, { ...init, headers })
  if (!r.ok) {
    const detail = await errText(r)
    if (AUTH.mode === 'oidc') {
      if (r.status === 401) signIn()                                           // session expired
      else if (r.status === 403 && detail.includes('link_required')) AUTH.onLinkRequired()
    }
    throw new Error(`${r.status} ${detail}`)
  }
  return r.json()
}

function useFetch<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const refetch = useCallback(() => {
    if (!path) return
    setLoading(true)
    api<T>(path).then(d => { setData(d); setError(null) })
      .catch(e => setError(String(e))).finally(() => setLoading(false))
  }, [path])
  useEffect(refetch, [refetch, ...deps]) // eslint-disable-line
  return { data, loading, error, refetch }
}

export const useRuns = (qs: string) => useFetch<{ runs: RunRow[] }>(`/api/v1/runs${qs}`, [qs])
export const useRun = (id: string) => useFetch<RunDetail>(id ? `/api/v1/runs/${id}` : null, [id])
export const useApprovals = (status = 'pending') =>
  useFetch<{ approvals: Approval[] }>(`/api/v1/approvals?status=${status}`, [status])
export const useCalibration = () => useFetch<any>('/api/v1/calibration', [])
export const useAudit = (runId?: string) =>
  useFetch<{ events: AuditEvent[] }>(`/api/v1/audit${runId ? `?run_id=${runId}` : ''}`, [runId])

// accounts / tenancy (Phase 4)
export const useRepos = () => useFetch<{ repos: Repo[] }>('/api/v1/repos', [])
export const useLlmKeys = (on: boolean) => useFetch<{ keys: LlmKey[] }>(on ? '/api/v1/llm-keys' : null, [on])
export const useAdminAccounts = () => useFetch<{ accounts: AccountRow[] }>('/api/v1/admin/accounts', [])
const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const registerRepo = (url: string) => post<Repo>('/api/v1/repos', { url })
export const verifyRepo = (id: string) => post<{ id: string; status: string }>(`/api/v1/repos/${id}/verify`)
export const createRepoKey = (id: string) => post<{ id: number; key: string; note: string }>(`/api/v1/repos/${id}/keys`)
export const revokeRepoKey = (id: string, keyId: number) =>
  api<{ revoked: number }>(`/api/v1/repos/${id}/keys/${keyId}`, { method: 'DELETE' })
export const unlinkHost = (provider: string) =>
  post<{ unlinked: string; repos_suspended: number }>(`/api/v1/me/links/${provider}/unlink`)
export const saveLlmKey = (key: string, repo_id?: string) =>
  post<{ id: number; hint: string }>('/api/v1/llm-keys', { key, repo_id: repo_id || null })
export const revokeLlmKey = (id: number) => api<{ revoked: number }>(`/api/v1/llm-keys/${id}`, { method: 'DELETE' })
export const setAccountStatus = (id: string, status: 'active' | 'suspended') =>
  post<{ id: string; status: string }>(`/api/v1/admin/accounts/${id}/status`, { status })

export const resolveApproval = (id: number, action: 'approve' | 'reject', comment: string) =>
  api(`/api/v1/approvals/${id}`, { method: 'POST', body: JSON.stringify({ action, comment }) })
export const generateTests = (id: string) =>
  api<{ run_id: string; proposals: any[]; reason?: string; error?: string }>(
    `/api/v1/runs/${id}/generate-tests`, { method: 'POST' })
export const resolveProposal = (id: number, action: 'adopt' | 'discard') =>
  api<{ proposal_id: number; status: string }>(`/api/v1/proposals/${id}`,
    { method: 'POST', body: JSON.stringify({ action }) })
export const setFindingVerdict = (rowId: number, verdict: string, note?: string) =>
  api(`/api/v1/findings/${rowId}/verdict`, { method: 'POST', body: JSON.stringify({ verdict, note }) })
export const recordMissedBug = (runId: string, b: { file?: string; line?: number; category?: string; description: string }) =>
  api(`/api/v1/runs/${runId}/outcome`, { method: 'POST', body: JSON.stringify({ outcome: 'missed_bug', ...b }) })
export const rerun = (id: string) =>
  api<{ run_id: string }>(`/api/v1/runs/${id}/rerun`, { method: 'POST' })
export const stopRun = (id: string) =>
  api<{ run_id: string; stopping: boolean }>(`/api/v1/runs/${id}/stop`, { method: 'POST' })
export const simulate = (event: unknown, repo_workspace?: string) =>
  api<{ run_id: string }>(`/api/v1/simulate`, { method: 'POST', body: JSON.stringify({ event, repo_workspace }) })

// ---------------------------------------------------------------- semantic colors (06 §8)
const CHIP = 'inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-medium uppercase tracking-wider border'
const DOT = 'h-1.5 w-1.5 rounded-full'
const bandCls: Record<Band, string> = {
  low: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  medium: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  high: 'bg-orange-500/10 text-orange-300 border-orange-500/30',
  critical: 'bg-red-500/10 text-red-300 border-red-500/40',
}
const decisionCls: Record<Decision, string> = {
  promote: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  hold: 'bg-slate-500/10 text-slate-300 border-slate-500/30',
  escalate: 'bg-red-500/10 text-red-300 border-red-500/40',
}
const sevCls: Record<Severity, string> = {
  critical: 'bg-red-500/10 text-red-300 border-red-500/40',
  high: 'bg-orange-500/10 text-orange-300 border-orange-500/30',
  medium: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  low: 'bg-slate-500/10 text-slate-300 border-slate-500/30',
}
const dotColor: Record<string, string> = {
  low: 'bg-emerald-400', medium: 'bg-amber-400', high: 'bg-orange-400', critical: 'bg-red-400',
  promote: 'bg-emerald-400', hold: 'bg-slate-400', escalate: 'bg-red-400',
}
const stateCls: Record<string, string> = {
  done: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  failed: 'bg-red-500/10 text-red-300 border-red-500/40',
}
export const BAND_HEX: Record<Band, string> = {
  low: '#34d399', medium: '#fbbf24', high: '#fb923c', critical: '#f87171',
}

export const BandChip = ({ band }: { band?: Band | null }) =>
  band ? <span className={`${CHIP} ${bandCls[band]}`}><i className={`${DOT} ${dotColor[band]}`} />{band}</span>
       : <span className="text-(--ink-dim) text-xs">—</span>
export const DecisionChip = ({ d }: { d?: Decision | null }) =>
  d ? <span className={`${CHIP} ${decisionCls[d]}`}><i className={`${DOT} ${dotColor[d]}`} />{d}</span>
    : <span className="text-(--ink-dim) text-xs">—</span>
export const SeverityChip = ({ s }: { s: Severity }) => <span className={`${CHIP} ${sevCls[s]}`}>{s}</span>
export const StateChip = ({ s }: { s: RunState }) => {
  const live = s !== 'done' && s !== 'failed'
  return (
    <span className={`${CHIP} ${stateCls[s] || 'bg-cyan-500/10 text-cyan-300 border-cyan-500/30'}`}>
      {live && <i className={`${DOT} bg-cyan-300`} style={{ animation: 'blink 1s steps(2) infinite' }} />}{s}
    </span>
  )
}

export const RelativeTime = ({ t }: { t?: string | null }) =>
  <span title={t || ''} className="text-(--ink-dim) text-xs">{t ? new Date(t).toLocaleString() : '—'}</span>

// ---------------------------------------------------------------- panel with corner ticks
export function Card({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  const tick = 'pointer-events-none absolute h-2 w-2 border-(--signal)/40'
  return (
    <section className="relative rounded-md border border-(--line) bg-(--panel) p-4">
      <span className={`${tick} left-0 top-0 border-l border-t`} />
      <span className={`${tick} right-0 top-0 border-r border-t`} />
      <span className={`${tick} left-0 bottom-0 border-l border-b`} />
      <span className={`${tick} right-0 bottom-0 border-r border-b`} />
      <header className="mb-3 flex items-center justify-between border-b border-(--line-soft) pb-2">
        <h2 className="tt text-[11px] font-semibold text-(--ink-dim)">{title}</h2>
        {right}
      </header>
      {children}
    </section>
  )
}

// ---------------------------------------------------------------- score dial (SVG, glow)
export function ScoreDial({ score, band }: { score: number; band: Band }) {
  const R = 54, C = 2 * Math.PI * R, frac = Math.max(0, Math.min(100, score)) / 100
  const hex = BAND_HEX[band]
  return (
    <svg width="150" height="150" viewBox="0 0 150 150" className="shrink-0" role="img"
         aria-label={`risk score ${score}, band ${band}`}>
      <circle cx="75" cy="75" r={R} fill="none" stroke="rgba(148,163,184,0.12)" strokeWidth="10" />
      <circle cx="75" cy="75" r={R} fill="none" stroke={hex} strokeWidth="10" strokeLinecap="round"
              strokeDasharray={C} strokeDashoffset={C * (1 - frac)} transform="rotate(-90 75 75)"
              style={{ filter: `drop-shadow(0 0 6px ${hex})`, transition: 'stroke-dashoffset .8s ease' }} />
      <line x1="75" y1="15" x2="75" y2="27" stroke="#e5e7eb" strokeWidth="2" strokeOpacity="0.5"
            transform="rotate(270 75 75)" />
      <text x="75" y="72" textAnchor="middle" className="fill-(--ink-hi)" fontSize="34" fontWeight="700"
            fontFamily="ui-monospace, monospace">{score}</text>
      <text x="75" y="94" textAnchor="middle" fill={hex} fontSize="12" fontWeight="700"
            letterSpacing="2" style={{ textTransform: 'uppercase' }}>{band}</text>
    </svg>
  )
}

export function HealthGauge({ value }: { value?: number }) {
  if (value == null) return <span className="text-(--ink-dim)">—</span>
  const c = value >= 75 ? '#34d399' : value >= 50 ? '#fbbf24' : '#f87171'
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-32 rounded-full bg-[rgba(148,163,184,0.12)]">
        <div className="h-1.5 rounded-full" style={{ width: `${value}%`, background: c, boxShadow: `0 0 6px ${c}` }} />
      </div>
      <span className="text-sm font-bold tabular-nums" style={{ color: c }}>{value}</span>
    </div>
  )
}

export const STAGES: RunState[] = ['received', 'analyzing', 'reviewing', 'testing', 'scoring', 'gated', 'done']
export const stageRank = (s: RunState) => STAGES.indexOf(s)
