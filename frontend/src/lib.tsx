import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Band, Decision, Severity, RunState, RunRow, RunDetail, Approval, AuditEvent } from './types'

// ---------------------------------------------------------------- auth (token shim, 06 §9)
// Demo: a role selector (gates UI only; Gateway enforces server-side) + optional bearer token.
type Role = 'viewer' | 'approver' | 'admin'
const RANK: Record<Role, number> = { viewer: 0, approver: 1, admin: 2 }
const AUTH = { token: sessionStorage.getItem('token') || '', role: (sessionStorage.getItem('role') as Role) || 'approver' }

const AuthCtx = createContext<{ role: Role; token: string; set: (r: Role, t: string) => void }>({
  role: AUTH.role, token: AUTH.token, set: () => {},
})
export const useAuth = () => useContext(AuthCtx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<Role>(AUTH.role)
  const [token, setTok] = useState(AUTH.token)
  const set = (r: Role, t: string) => {
    AUTH.role = r; AUTH.token = t
    sessionStorage.setItem('role', r); sessionStorage.setItem('token', t)
    setRole(r); setTok(t)
  }
  return <AuthCtx.Provider value={{ role, token, set }}>{children}</AuthCtx.Provider>
}

export function RoleGate({ need, children }: { need: Role; children: ReactNode }) {
  const { role } = useAuth()
  return RANK[role] >= RANK[need] ? <>{children}</> : null
}

// ---------------------------------------------------------------- REST
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(init?.headers as any) }
  if (AUTH.token) headers.Authorization = `Bearer ${AUTH.token}`
  const r = await fetch(path, { ...init, headers })
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => '')}`)
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
export const useAudit = (runId?: string) =>
  useFetch<{ events: AuditEvent[] }>(`/api/v1/audit${runId ? `?run_id=${runId}` : ''}`, [runId])

export const resolveApproval = (id: number, action: 'approve' | 'reject', comment: string) =>
  api(`/api/v1/approvals/${id}`, { method: 'POST', body: JSON.stringify({ action, comment }) })
export const rerun = (id: string) =>
  api<{ run_id: string }>(`/api/v1/runs/${id}/rerun`, { method: 'POST' })
export const simulate = (event: unknown, repo_workspace?: string) =>
  api<{ run_id: string }>(`/api/v1/simulate`, { method: 'POST', body: JSON.stringify({ event, repo_workspace }) })

// ---------------------------------------------------------------- semantic colors (06 §8)
const CHIP = 'inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium border'
const bandCls: Record<Band, string> = {
  low: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  medium: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  high: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
  critical: 'bg-red-500/15 text-red-300 border-red-500/30',
}
const decisionCls: Record<Decision, string> = {
  promote: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  hold: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
  escalate: 'bg-red-500/15 text-red-300 border-red-500/30',
}
const sevCls: Record<Severity, string> = {
  critical: 'bg-red-500/15 text-red-300 border-red-500/30',
  high: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
  medium: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  low: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
}
const stateCls: Record<string, string> = {
  done: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  failed: 'bg-red-500/15 text-red-300 border-red-500/30',
}
export const BAND_HEX: Record<Band, string> = {
  low: '#34d399', medium: '#fbbf24', high: '#fb923c', critical: '#f87171',
}

export const BandChip = ({ band }: { band?: Band | null }) =>
  band ? <span className={`${CHIP} ${bandCls[band]}`}>{band}</span> : <span className="text-zinc-500 text-xs">—</span>
export const DecisionChip = ({ d }: { d?: Decision | null }) =>
  d ? <span className={`${CHIP} ${decisionCls[d]}`}>{d}</span> : <span className="text-zinc-500 text-xs">—</span>
export const SeverityChip = ({ s }: { s: Severity }) => <span className={`${CHIP} ${sevCls[s]}`}>{s}</span>
export const StateChip = ({ s }: { s: RunState }) =>
  <span className={`${CHIP} ${stateCls[s] || 'bg-blue-500/15 text-blue-300 border-blue-500/30'}`}>
    {s !== 'done' && s !== 'failed' && <Spinner />}{s}
  </span>

const Spinner = () => <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-current" />

export const RelativeTime = ({ t }: { t?: string | null }) =>
  <span title={t || ''} className="text-zinc-500">{t ? new Date(t).toLocaleString() : '—'}</span>

// ---------------------------------------------------------------- score dial (SVG, no chart lib)
export function ScoreDial({ score, band }: { score: number; band: Band }) {
  const R = 52, C = 2 * Math.PI * R, frac = Math.max(0, Math.min(100, score)) / 100
  const hex = BAND_HEX[band]
  return (
    <svg width="140" height="140" viewBox="0 0 140 140" className="shrink-0" role="img"
         aria-label={`risk score ${score}, band ${band}`}>
      <circle cx="70" cy="70" r={R} fill="none" stroke="#27272a" strokeWidth="12" />
      <circle cx="70" cy="70" r={R} fill="none" stroke={hex} strokeWidth="12" strokeLinecap="round"
              strokeDasharray={C} strokeDashoffset={C * (1 - frac)} transform="rotate(-90 70 70)" />
      {/* 75 = critical threshold tick */}
      <line x1="70" y1="12" x2="70" y2="24" stroke="#a1a1aa" strokeWidth="2"
            transform={`rotate(${360 * 0.75} 70 70)`} />
      <text x="70" y="66" textAnchor="middle" className="fill-zinc-100" fontSize="30" fontWeight="700">{score}</text>
      <text x="70" y="88" textAnchor="middle" fill={hex} fontSize="13" fontWeight="600">{band}</text>
    </svg>
  )
}

export function HealthGauge({ value }: { value?: number }) {
  if (value == null) return <span className="text-zinc-500">—</span>
  const c = value >= 75 ? '#34d399' : value >= 50 ? '#fbbf24' : '#f87171'
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-32 rounded bg-zinc-800">
        <div className="h-2 rounded" style={{ width: `${value}%`, background: c }} />
      </div>
      <span className="text-sm font-semibold" style={{ color: c }}>{value}</span>
    </div>
  )
}

export const Card = ({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) => (
  <section className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
    <header className="mb-3 flex items-center justify-between">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">{title}</h2>
      {right}
    </header>
    {children}
  </section>
)

export const STAGES: RunState[] = ['received', 'analyzing', 'reviewing', 'testing', 'scoring', 'gated', 'done']
export const stageRank = (s: RunState) => STAGES.indexOf(s)
