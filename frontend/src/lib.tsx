import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Band, Decision, Severity, RunState, RunRow, RunDetail, Approval, AuditEvent } from './types'

// ---------------------------------------------------------------- auth (token shim, 06 §9)
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
       : <span className="text-[var(--ink-dim)] text-xs">—</span>
export const DecisionChip = ({ d }: { d?: Decision | null }) =>
  d ? <span className={`${CHIP} ${decisionCls[d]}`}><i className={`${DOT} ${dotColor[d]}`} />{d}</span>
    : <span className="text-[var(--ink-dim)] text-xs">—</span>
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
  <span title={t || ''} className="text-[var(--ink-dim)] text-xs">{t ? new Date(t).toLocaleString() : '—'}</span>

// ---------------------------------------------------------------- panel with corner ticks
export function Card({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  const tick = 'pointer-events-none absolute h-2 w-2 border-[var(--signal)]/40'
  return (
    <section className="relative rounded-md border border-[var(--line)] bg-[var(--panel)] p-4">
      <span className={`${tick} left-0 top-0 border-l border-t`} />
      <span className={`${tick} right-0 top-0 border-r border-t`} />
      <span className={`${tick} left-0 bottom-0 border-l border-b`} />
      <span className={`${tick} right-0 bottom-0 border-r border-b`} />
      <header className="mb-3 flex items-center justify-between border-b border-[var(--line-soft)] pb-2">
        <h2 className="tt text-[11px] font-semibold text-[var(--ink-dim)]">{title}</h2>
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
      <text x="75" y="72" textAnchor="middle" className="fill-[var(--ink-hi)]" fontSize="34" fontWeight="700"
            fontFamily="ui-monospace, monospace">{score}</text>
      <text x="75" y="94" textAnchor="middle" fill={hex} fontSize="12" fontWeight="700"
            letterSpacing="2" style={{ textTransform: 'uppercase' }}>{band}</text>
    </svg>
  )
}

export function HealthGauge({ value }: { value?: number }) {
  if (value == null) return <span className="text-[var(--ink-dim)]">—</span>
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
