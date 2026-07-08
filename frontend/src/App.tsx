import { Link, Route, Routes, useSearchParams } from 'react-router-dom'
import {
  AuthProvider, useAuth, BandChip, DecisionChip, StateChip, RelativeTime,
  useRuns, useApprovals, useAudit,
} from './lib'
import RunDetailRoute, { RunDetailPane, ApprovalControls } from './RunDetail'

export default function App() {
  return (
    <AuthProvider>
      <div className="mx-auto min-h-screen max-w-6xl px-4">
        <Nav />
        <main className="py-4">
          <Routes>
            <Route path="/" element={<RunsList />} />
            <Route path="/runs/compare" element={<Compare />} />
            <Route path="/runs/:id" element={<RunDetailRoute />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/audit" element={<Audit />} />
          </Routes>
        </main>
      </div>
    </AuthProvider>
  )
}

function Nav() {
  const { role, token, set } = useAuth()
  const link = 'text-sm text-zinc-400 hover:text-zinc-100'
  return (
    <nav className="flex flex-wrap items-center gap-4 border-b border-zinc-800 py-3">
      <Link to="/" className="font-semibold text-zinc-100">Sentinel</Link>
      <Link to="/" className={link}>Runs</Link>
      <Link to="/approvals" className={link}>Approvals</Link>
      <Link to="/audit" className={link}>Audit</Link>
      <span className="ml-auto flex items-center gap-2">
        <select value={role} onChange={e => set(e.target.value as any, token)}
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs" title="demo role (UI gating)">
          <option value="viewer">viewer</option>
          <option value="approver">approver</option>
          <option value="admin">admin</option>
        </select>
        <input value={token} onChange={e => set(role, e.target.value)} placeholder="bearer token"
          className="w-28 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs" />
      </span>
    </nav>
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
      <div className="mb-3 flex flex-wrap gap-2">
        {[['state', ['', 'received', 'reviewing', 'scoring', 'done', 'failed']],
          ['band', ['', 'low', 'medium', 'high', 'critical']],
          ['decision', ['', 'promote', 'hold', 'escalate']]].map(([k, opts]) => (
          <select key={k as string} value={sp.get(k as string) || ''} onChange={e => setFilter(k as string, e.target.value)}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm">
            {(opts as string[]).map(o => <option key={o} value={o}>{o || `all ${k}`}</option>)}
          </select>
        ))}
      </div>
      {loading && <p className="text-zinc-500">Loading…</p>}
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-zinc-500">
          <tr><th className="py-2">Repo</th><th>Transition</th><th>State</th><th>Band</th><th>Decision</th><th>Created</th></tr>
        </thead>
        <tbody>
          {runs.map(r => (
            <tr key={r.run_id} className="border-t border-zinc-800 hover:bg-zinc-900/50">
              <td className="py-2"><Link to={`/runs/${r.run_id}`} className="font-mono text-zinc-200 hover:underline">{r.repo}</Link></td>
              <td className="text-zinc-400">{r.from_env} → {r.to_env}</td>
              <td><StateChip s={r.state} /></td>
              <td><BandChip band={r.band} /></td>
              <td><DecisionChip d={r.decision} /></td>
              <td className="text-xs"><RelativeTime t={r.created_at} /></td>
            </tr>
          ))}
          {!loading && runs.length === 0 && <tr><td colSpan={6} className="py-6 text-center text-zinc-500">No runs.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function Approvals() {
  const { data, loading, refetch } = useApprovals('pending')
  const rows = data?.approvals || []
  return (
    <div className="space-y-3">
      <h1 className="text-lg text-zinc-100">Pending approvals</h1>
      {loading && <p className="text-zinc-500">Loading…</p>}
      {!loading && rows.length === 0 && <p className="text-zinc-500">Nothing pending.</p>}
      {rows.map(a => (
        <div key={a.id} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
          <div className="mb-2 flex items-center gap-3 text-sm">
            <Link to={`/runs/${a.run_id}`} className="font-mono text-zinc-200 hover:underline">{a.run_id}</Link>
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
      <h1 className="mb-3 text-lg text-zinc-100">Audit {sp.get('run_id') ? `· ${sp.get('run_id')}` : ''}</h1>
      {loading && <p className="text-zinc-500">Loading…</p>}
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-zinc-500"><tr><th className="py-2">When</th><th>Actor</th><th>Action</th><th>Run</th></tr></thead>
        <tbody>
          {rows.map(e => (
            <tr key={e.id} className="border-t border-zinc-800">
              <td className="py-2 text-xs"><RelativeTime t={e.at} /></td>
              <td className="text-zinc-400">{e.actor}</td>
              <td className="text-zinc-200">{e.action}</td>
              <td>{e.run_id && <Link to={`/runs/${e.run_id}`} className="font-mono text-xs text-zinc-500 hover:underline">{e.run_id.slice(0, 8)}</Link>}</td>
            </tr>
          ))}
          {!loading && rows.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-zinc-500">No events.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

// F5 demo layout: two run-detail panes side by side (?a=&b=). Pure client composition, no endpoint.
function Compare() {
  const [sp] = useSearchParams()
  const a = sp.get('a') || '', b = sp.get('b') || ''
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {[a, b].map((id, i) => (
        <div key={i}>{id ? <RunDetailPane id={id} /> : <p className="text-zinc-500">Set ?a= and ?b= run ids.</p>}</div>
      ))}
    </div>
  )
}
