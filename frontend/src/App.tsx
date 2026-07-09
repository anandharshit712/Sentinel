import { Link, NavLink, Route, Routes, useSearchParams } from 'react-router-dom'
import {
  AuthProvider, useAuth, BandChip, DecisionChip, StateChip, RelativeTime,
  useRuns, useApprovals, useAudit,
} from './lib'
import RunDetailRoute, { RunDetailPane, ApprovalControls } from './RunDetail'

export default function App() {
  return (
    <AuthProvider>
      <Shell>
        <Routes>
          <Route path="/" element={<RunsList />} />
          <Route path="/runs/compare" element={<Compare />} />
          <Route path="/runs/:id" element={<RunDetailRoute />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/audit" element={<Audit />} />
        </Routes>
      </Shell>
    </AuthProvider>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  const { role, token, set } = useAuth()
  const nav = ({ isActive }: { isActive: boolean }) =>
    `text-xs uppercase tracking-widest transition-colors ${isActive ? 'text-[var(--signal)]' : 'text-[var(--ink-dim)] hover:text-[var(--ink)]'}`
  return (
    <div className="mx-auto min-h-screen max-w-7xl px-5">
      <nav className="flex flex-wrap items-center gap-5 border-b border-[var(--line)] py-3.5">
        <Link to="/" className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-[var(--signal)]" style={{ boxShadow: '0 0 8px var(--signal)' }} />
          <span className="text-base font-bold uppercase tracking-[0.3em] text-[var(--ink-hi)]">Sentinel</span>
        </Link>
        <NavLink to="/" end className={nav}>Runs</NavLink>
        <NavLink to="/approvals" className={nav}>Approvals</NavLink>
        <NavLink to="/audit" className={nav}>Audit</NavLink>
        <span className="ml-auto flex items-center gap-2 text-[10px] uppercase tracking-widest text-[var(--ink-dim)]">
          <span className="hidden items-center gap-1.5 sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--signal)]" style={{ animation: 'blink 1.4s steps(2) infinite' }} />system live
          </span>
          <select value={role} onChange={e => set(e.target.value as any, token)}
            className="rounded-sm border border-[var(--line)] bg-[var(--bg-2)] px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--ink)]" title="demo role (UI gating)">
            <option value="viewer">viewer</option>
            <option value="approver">approver</option>
            <option value="admin">admin</option>
          </select>
          <input value={token} onChange={e => set(role, e.target.value)} placeholder="token"
            className="w-24 rounded-sm border border-[var(--line)] bg-[var(--bg-2)] px-2 py-1 text-[10px] text-[var(--ink)]" />
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
      className="rounded-sm border border-[var(--line)] bg-[var(--bg-2)] px-2 py-1 text-xs uppercase tracking-wide text-[var(--ink)]">
      {opts[k].map(o => <option key={o} value={o}>{o || `all ${k}`}</option>)}
    </select>
  )
}

function RunsList() {
  const [sp, setSp] = useSearchParams()
  const qs = sp.toString() ? `?${sp.toString()}` : ''
  const { data, loading } = useRuns(qs)
  const setFilter = (k: string, v: string) => {
    const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n)
  }
  const runs = data?.runs || []
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select k="state" sp={sp} set={setFilter} />
        <Select k="band" sp={sp} set={setFilter} />
        <Select k="decision" sp={sp} set={setFilter} />
        <span className="ml-auto text-[10px] uppercase tracking-widest text-[var(--ink-dim)]">{runs.length} runs</span>
      </div>
      <div className="overflow-x-auto rounded-md border border-[var(--line)]">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--line)] text-[10px] uppercase tracking-widest text-[var(--ink-dim)]">
              <th className="px-3 py-2.5 font-medium">Repo</th><th className="font-medium">Transition</th>
              <th className="font-medium">State</th><th className="font-medium">Band</th>
              <th className="font-medium">Decision</th><th className="px-3 font-medium text-right">Created</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(r => (
              <tr key={r.run_id} className="border-b border-[var(--line-soft)] transition-colors hover:bg-[var(--panel-hi)]">
                <td className="px-3 py-2.5"><Link to={`/runs/${r.run_id}`} className="font-semibold text-[var(--ink-hi)] hover:text-[var(--signal)]">{r.repo}</Link></td>
                <td className="text-[var(--ink-dim)]">{r.from_env} <span className="text-[var(--signal)]">→</span> {r.to_env}</td>
                <td><StateChip s={r.state} /></td>
                <td><BandChip band={r.band} /></td>
                <td><DecisionChip d={r.decision} /></td>
                <td className="px-3 text-right"><RelativeTime t={r.created_at} /></td>
              </tr>
            ))}
            {!loading && runs.length === 0 && <tr><td colSpan={6} className="py-10 text-center text-[var(--ink-dim)]">No runs. Run <code className="text-[var(--signal)]">scripts/verify_c.py</code>.</td></tr>}
            {loading && <tr><td colSpan={6} className="py-10 text-center text-[var(--ink-dim)]">Loading…</td></tr>}
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
      <h1 className="text-sm font-bold uppercase tracking-widest text-[var(--ink-hi)]">Pending approvals</h1>
      {loading && <p className="text-[var(--ink-dim)]">Loading…</p>}
      {!loading && rows.length === 0 && <p className="text-[var(--ink-dim)]">Nothing pending.</p>}
      {rows.map(a => (
        <div key={a.id} className="rounded-md border border-[var(--line)] bg-[var(--panel)] p-4">
          <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
            <Link to={`/runs/${a.run_id}`} className="font-semibold text-[var(--ink-hi)] hover:text-[var(--signal)]">{a.run_id}</Link>
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
      <h1 className="mb-3 text-sm font-bold uppercase tracking-widest text-[var(--ink-hi)]">Audit {sp.get('run_id') ? `· ${sp.get('run_id')}` : ''}</h1>
      <div className="overflow-x-auto rounded-md border border-[var(--line)]">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-[var(--line)] text-[10px] uppercase tracking-widest text-[var(--ink-dim)]">
            <th className="px-3 py-2.5 font-medium">When</th><th className="font-medium">Actor</th><th className="font-medium">Action</th><th className="px-3 font-medium">Run</th></tr></thead>
          <tbody>
            {rows.map(e => (
              <tr key={e.id} className="border-b border-[var(--line-soft)]">
                <td className="px-3 py-2"><RelativeTime t={e.at} /></td>
                <td className="text-[var(--ink-dim)]">{e.actor}</td>
                <td className="text-[var(--ink-hi)]">{e.action}</td>
                <td className="px-3">{e.run_id && <Link to={`/runs/${e.run_id}`} className="text-[11px] text-[var(--ink-dim)] hover:text-[var(--signal)]">{e.run_id.slice(0, 8)}</Link>}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && <tr><td colSpan={4} className="py-10 text-center text-[var(--ink-dim)]">No events.</td></tr>}
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
        <div key={i} className="rounded-md border border-[var(--line-soft)] p-3">
          {id ? <RunDetailPane id={id} /> : <p className="text-[var(--ink-dim)]">Set ?a= and ?b= run ids.</p>}
        </div>
      ))}
    </div>
  )
}
