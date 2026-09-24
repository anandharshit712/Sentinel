import { useState } from 'react'
import { Link, NavLink, Route, Routes, useSearchParams } from 'react-router-dom'
import {
  AuthProvider, useAuth, BandChip, DecisionChip, StateChip, RelativeTime,
  useRuns, useApprovals, useAudit,
} from './lib'
import RunDetailRoute, { RunDetailPane, ApprovalControls } from './RunDetail'
import { Calibration } from './Calibration'

export default function App() {
  return (
    <AuthProvider>
      <Gate>
        <Shell>
          <Routes>
            <Route path="/" element={<RunsList />} />
            <Route path="/runs/compare" element={<Compare />} />
            <Route path="/runs/:id" element={<RunDetailRoute />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/calibration" element={<Calibration />} />
          </Routes>
        </Shell>
      </Gate>
    </AuthProvider>
  )
}

// Token mode: block the app behind a login until a valid token is entered. OPEN_MODE passes straight through.
function Gate({ children }: { children: React.ReactNode }) {
  const { openMode, verified } = useAuth()
  if (openMode === null)
    return <div className="grid min-h-screen place-items-center text-xs uppercase tracking-widest text-(--ink-dim)">connecting…</div>
  if (!openMode && !verified) return <LoginGate />
  return <>{children}</>
}

function LoginGate() {
  const { login } = useAuth()
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!token.trim() || busy) return
    setBusy(true); setErr('')
    login(token.trim())
      .catch(x => setErr(String(x).includes('401') ? 'Invalid token.' : 'Verification failed — is the Gateway up?'))
      .finally(() => setBusy(false))
  }
  return (
    <div className="grid min-h-screen place-items-center px-5">
      <form onSubmit={submit} className="w-full max-w-sm rounded-md border border-(--line) bg-(--panel) p-6">
        <div className="mb-5 flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-(--signal)" style={{ boxShadow: '0 0 8px var(--signal)' }} />
          <span className="text-base font-bold uppercase tracking-[0.3em] text-(--ink-hi)">Sentinel</span>
        </div>
        <label className="mb-1.5 block text-[10px] uppercase tracking-widest text-(--ink-dim)">Access token</label>
        <input autoFocus type="password" value={token} onChange={e => setToken(e.target.value)}
          placeholder="paste your token"
          className="w-full rounded-sm border border-(--line) bg-(--bg-2) px-3 py-2 text-sm text-(--ink) focus:border-(--signal) focus:outline-none" />
        {err && <p className="mt-2 text-[11px] text-red-300">{err}</p>}
        <button type="submit" disabled={busy || !token.trim()}
          className="mt-4 w-full rounded-sm border border-(--signal)/40 bg-(--signal-dim) px-4 py-2 text-xs uppercase tracking-widest text-(--signal) hover:bg-(--signal)/10 disabled:opacity-40">
          {busy ? 'Verifying…' : 'Enter'}
        </button>
        <p className="mt-3 text-[10px] leading-relaxed text-(--ink-dim)">Your role is read from the token. Ask an admin for one, or unset <code>API_TOKENS</code> in <code>.env</code> to run open.</p>
      </form>
    </div>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  const { role, token, openMode, set, logout } = useAuth()
  const nav = ({ isActive }: { isActive: boolean }) =>
    `text-xs uppercase tracking-widest transition-colors ${isActive ? 'text-(--signal)' : 'text-(--ink-dim) hover:text-(--ink)'}`
  return (
    <div className="mx-auto min-h-screen max-w-7xl px-5">
      <nav className="flex flex-wrap items-center gap-5 border-b border-(--line) py-3.5">
        <Link to="/" className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-(--signal)" style={{ boxShadow: '0 0 8px var(--signal)' }} />
          <span className="text-base font-bold uppercase tracking-[0.3em] text-(--ink-hi)">Sentinel</span>
        </Link>
        <NavLink to="/" end className={nav}>Runs</NavLink>
        <NavLink to="/approvals" className={nav}>Approvals</NavLink>
        <NavLink to="/audit" className={nav}>Audit</NavLink>
        <NavLink to="/calibration" className={nav}>Calibration</NavLink>
        <span className="ml-auto flex items-center gap-2 text-[10px] uppercase tracking-widest text-(--ink-dim)">
          <span className="hidden items-center gap-1.5 sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-(--signal)" style={{ animation: 'blink 1.4s steps(2) infinite' }} />system live
          </span>
          {openMode ? (
            <>
              <select value={role} onChange={e => set(e.target.value as any, token)}
                className="rounded-sm border border-(--line) bg-(--bg-2) px-2 py-1 text-[10px] uppercase tracking-wider text-(--ink)" title="demo role (UI gating)">
                <option value="viewer">viewer</option>
                <option value="approver">approver</option>
                <option value="admin">admin</option>
              </select>
              <input value={token} onChange={e => set(role, e.target.value)} placeholder="token"
                className="w-24 rounded-sm border border-(--line) bg-(--bg-2) px-2 py-1 text-[10px] text-(--ink)" />
            </>
          ) : (
            <>
              <span className="rounded-sm border border-(--line) bg-(--bg-2) px-2 py-1 text-[10px] uppercase tracking-wider text-(--signal)">{role}</span>
              <button onClick={logout} className="rounded-sm border border-(--line) px-2 py-1 text-[10px] uppercase tracking-wider text-(--ink-dim) hover:border-red-500/40 hover:text-red-300">logout</button>
            </>
          )}
        </span>
      </nav>
      <main className="py-5">{children}</main>
    </div>
  )
}

function Select({ k, sp, set }: { k: string; sp: URLSearchParams; set: (k: string, v: string) => void }) {
  const opts: Record<string, string[]> = {
    state: ['', 'received', 'reviewing', 'scoring', 'done', 'failed'],
    band: ['', 'low', 'medium', 'high', 'critical'],
    decision: ['', 'promote', 'hold', 'escalate'],
  }
  return (
    <select value={sp.get(k) || ''} onChange={e => set(k, e.target.value)}
      className="rounded-sm border border-(--line) bg-(--bg-2) px-2 py-1 text-xs uppercase tracking-wide text-(--ink)">
      {opts[k].map(o => <option key={o} value={o}>{o || `all ${k}`}</option>)}
    </select>
  )
}

function RunsList() {
  const [sp, setSp] = useSearchParams()
  const qs = sp.toString() ? `?${sp.toString()}` : ''
  const { data, loading } = useRuns(qs)
  const setFilter = (k: string, v: string) => {
    const n = new URLSearchParams(sp)
    if (v) n.set(k, v); else n.delete(k)
    setSp(n)
  }
  const runs = data?.runs || []
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select k="state" sp={sp} set={setFilter} />
        <Select k="band" sp={sp} set={setFilter} />
        <Select k="decision" sp={sp} set={setFilter} />
        <span className="ml-auto text-[10px] uppercase tracking-widest text-(--ink-dim)">{runs.length} runs</span>
      </div>
      <div className="overflow-x-auto rounded-md border border-(--line)">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-(--line) text-[10px] uppercase tracking-widest text-(--ink-dim)">
              <th className="px-3 py-2.5 font-medium">Repo</th><th className="font-medium">Transition</th>
              <th className="font-medium">State</th><th className="font-medium">Band</th>
              <th className="font-medium">Decision</th><th className="px-3 font-medium text-right">Created</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(r => (
              <tr key={r.run_id} className="border-b border-(--line-soft) transition-colors hover:bg-(--panel-hi)">
                <td className="px-3 py-2.5"><Link to={`/runs/${r.run_id}`} className="font-semibold text-(--ink-hi) hover:text-(--signal)">{r.repo}</Link></td>
                <td className="text-(--ink-dim)">{r.from_env} <span className="text-(--signal)">→</span> {r.to_env}</td>
                <td><StateChip s={r.state} /></td>
                <td><BandChip band={r.band} /></td>
                <td><DecisionChip d={r.decision} /></td>
                <td className="px-3 text-right"><RelativeTime t={r.created_at} /></td>
              </tr>
            ))}
            {!loading && runs.length === 0 && <tr><td colSpan={6} className="py-10 text-center text-(--ink-dim)">No runs. Run <code className="text-(--signal)">scripts/verify_c.py</code>.</td></tr>}
            {loading && <tr><td colSpan={6} className="py-10 text-center text-(--ink-dim)">Loading…</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Approvals() {
  const { data, loading, refetch } = useApprovals('pending')
  const rows = data?.approvals || []
  return (
    <div className="space-y-3">
      <h1 className="text-sm font-bold uppercase tracking-widest text-(--ink-hi)">Pending approvals</h1>
      {loading && <p className="text-(--ink-dim)">Loading…</p>}
      {!loading && rows.length === 0 && <p className="text-(--ink-dim)">Nothing pending.</p>}
      {rows.map(a => (
        <div key={a.id} className="rounded-md border border-(--line) bg-(--panel) p-4">
          <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
            <Link to={`/runs/${a.run_id}`} className="font-semibold text-(--ink-hi) hover:text-(--signal)">{a.run_id}</Link>
            <RelativeTime t={a.created_at} />
          </div>
          <ApprovalControls approvalId={a.id} onResolved={refetch} />
        </div>
      ))}
    </div>
  )
}

function Audit() {
  const [sp] = useSearchParams()
  const { data, loading } = useAudit(sp.get('run_id') || undefined)
  const rows = data?.events || []
  return (
    <div>
      <h1 className="mb-3 text-sm font-bold uppercase tracking-widest text-(--ink-hi)">Audit {sp.get('run_id') ? `· ${sp.get('run_id')}` : ''}</h1>
      <div className="overflow-x-auto rounded-md border border-(--line)">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-(--line) text-[10px] uppercase tracking-widest text-(--ink-dim)">
            <th className="px-3 py-2.5 font-medium">When</th><th className="font-medium">Actor</th><th className="font-medium">Action</th><th className="px-3 font-medium">Run</th></tr></thead>
          <tbody>
            {rows.map(e => (
              <tr key={e.id} className="border-b border-(--line-soft)">
                <td className="px-3 py-2"><RelativeTime t={e.at} /></td>
                <td className="text-(--ink-dim)">{e.actor}</td>
                <td className="text-(--ink-hi)">{e.action}</td>
                <td className="px-3">{e.run_id && <Link to={`/runs/${e.run_id}`} className="text-[11px] text-(--ink-dim) hover:text-(--signal)">{e.run_id.slice(0, 8)}</Link>}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && <tr><td colSpan={4} className="py-10 text-center text-(--ink-dim)">No events.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// F5 demo layout: two run-detail panes side by side (?a=&b=). Pure client composition.
function Compare() {
  const [sp] = useSearchParams()
  const a = sp.get('a') || '', b = sp.get('b') || ''
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {[a, b].map((id, i) => (
        <div key={i} className="rounded-md border border-(--line-soft) p-3">
          {id ? <RunDetailPane id={id} /> : <p className="text-(--ink-dim)">Set ?a= and ?b= run ids.</p>}
        </div>
      ))}
    </div>
  )
}
