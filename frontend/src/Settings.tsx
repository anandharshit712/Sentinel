import { useState, type ReactNode } from 'react'
import {
  Card, RoleGate, RelativeTime, useAuth, errMsg, useRepos, useLlmKeys, useAdminAccounts,
  registerRepo, verifyRepo, createRepoKey, revokeRepoKey, unlinkHost, saveLlmKey, revokeLlmKey, setAccountStatus,
} from './lib'
import type { Repo } from './types'

/* Phase 4 screens: link gate, settings (code hosts, repos + CI keys, LLM keys), platform admin, privacy.
   OIDC-only parts are hidden in token mode, where one shared tenant has no accounts or BYOK. */

const BTN = 'rounded-sm border border-(--line) px-2.5 py-1 text-[10px] uppercase tracking-wider text-(--ink-dim) hover:border-(--signal)/40 hover:text-(--signal) disabled:opacity-40'
const BTN_DANGER = 'rounded-sm border border-(--line) px-2.5 py-1 text-[10px] uppercase tracking-wider text-(--ink-dim) hover:border-red-500/40 hover:text-red-300 disabled:opacity-40'
const BTN_PRIMARY = 'rounded-sm border border-(--signal)/40 bg-(--signal-dim) px-4 py-2 text-xs uppercase tracking-widest text-(--signal) hover:bg-(--signal)/10 disabled:opacity-40'
const INPUT = 'w-full rounded-sm border border-(--line) bg-(--bg-2) px-3 py-2 text-sm text-(--ink) focus:border-(--signal) focus:outline-none'
const LABEL = 'mb-1.5 block text-[10px] uppercase tracking-widest text-(--ink-dim)'
const HOSTS = [['github', 'GitHub'], ['gitlab', 'GitLab']] as const
const go = (path: string) => { window.location.href = path }   // full-page: the Gateway redirects to Keycloak

const Err = ({ msg }: { msg: string }) => msg ? <p role="alert" className="mt-2 text-[11px] text-red-300">{msg}</p> : null
const Note = ({ children }: { children: ReactNode }) => <p className="text-[11px] leading-relaxed text-(--ink-dim)">{children}</p>

// ---------------------------------------------------------------- link gate (OIDC, no code host linked yet)
export function LinkPage() {
  const { me, logout } = useAuth()
  const errs = Object.entries(me?.link_errors || {})
  return (
    <div className="grid min-h-screen place-items-center px-5">
      <div className="w-full max-w-md rounded-md border border-(--line) bg-(--panel) p-6">
        <div className="mb-5 flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-(--signal)" style={{ boxShadow: '0 0 8px var(--signal)' }} />
          <span className="text-base font-bold uppercase tracking-[0.3em] text-(--ink-hi)">Sentinel</span>
        </div>
        <h1 className="mb-2 text-sm font-bold text-(--ink-hi)">Link a code host</h1>
        <Note>
          Sentinel needs a linked GitHub or GitLab account before anything else works. It checks that you
          administer a repository through that account before it will review it.
        </Note>
        {errs.map(([p, m]) => <Err key={p} msg={`${p}: ${m}`} />)}
        <div className="mt-5 flex flex-wrap gap-3">
          {HOSTS.map(([p, label]) => (
            <button key={p} onClick={() => go(`/auth/link/${p}`)} className={BTN_PRIMARY}>Link {label}</button>
          ))}
        </div>
        <p className="mt-5 text-[10px] text-(--ink-dim)">
          Signed in as {me?.account.email || me?.account.display_name}.{' '}
          <button onClick={logout} className="underline hover:text-(--ink)">Sign out</button>
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- settings
export function Settings() {
  const { authMode } = useAuth()
  const repos = useRepos()
  const oidc = authMode === 'oidc'
  return (
    <div className="space-y-4">
      <h1 className="text-sm font-bold uppercase tracking-widest text-(--ink-hi)">Settings</h1>
      {oidc && <LinkedAccounts onUnlinked={repos.refetch} />}
      <Repos {...repos} />
      {oidc && <LlmKeys repos={repos.data?.repos || []} />}
    </div>
  )
}

function LinkedAccounts({ onUnlinked }: { onUnlinked: () => void }) {
  const { me, refreshMe } = useAuth()
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const unlink = (p: string) => {
    if (!confirm(`Unlink ${p}? Every repo registered through ${p} will be suspended and its CI keys revoked.`)) return
    setBusy(p); setMsg(''); setErr('')
    unlinkHost(p)
      .then(r => { setMsg(`Unlinked ${r.unlinked}; ${r.repos_suspended} repo(s) suspended.`); onUnlinked(); return refreshMe() })
      .catch(e => setErr(errMsg(e))).finally(() => setBusy(''))
  }
  return (
    <Card title="Linked code hosts">
      <div className="space-y-2 text-sm">
        {HOSTS.map(([p, label]) => {
          const l = me?.links[p]
          return (
            <div key={p} className="flex flex-wrap items-center gap-3">
              <span className="w-16 text-[11px] uppercase tracking-wider text-(--signal)">{label}</span>
              {l ? <>
                <span className="text-(--ink-hi)">{l.username}</span>
                <span className="text-[11px] text-(--ink-dim)">linked</span> <RelativeTime t={l.linked_at} />
                <button onClick={() => unlink(p)} disabled={!!busy} className={`ml-auto ${BTN_DANGER}`}>
                  {busy === p ? 'Unlinking…' : 'Unlink'}</button>
              </> : <>
                <span className="text-[11px] text-(--ink-dim)">not linked</span>
                <button onClick={() => go(`/auth/link/${p}`)} className={`ml-auto ${BTN}`}>Link</button>
              </>}
              {me?.link_errors[p] && <Err msg={me.link_errors[p]} />}
            </div>
          )
        })}
      </div>
      {msg && <p role="status" className="mt-3 text-[11px] text-(--signal)">{msg}</p>}
      <Err msg={err} />
      <div className="mt-3"><Note>Unlinking a host suspends every repo registered through it and revokes their CI keys.</Note></div>
    </Card>
  )
}

const STATUS_CLS: Record<Repo['status'], string> = {
  active: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  pending: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  suspended: 'border-red-500/40 bg-red-500/10 text-red-300',
}

function Repos({ data, loading, error, refetch }: ReturnType<typeof useRepos>) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const repos = data?.repos || []
  const add = (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim() || busy) return
    setBusy(true); setErr('')
    registerRepo(url.trim()).then(() => { setUrl(''); refetch() })
      .catch(x => setErr(errMsg(x))).finally(() => setBusy(false))
  }
  return (
    <Card title="Repositories + CI keys">
      <RoleGate need="admin">
        <form onSubmit={add} className="mb-4 flex flex-wrap items-end gap-2">
          <div className="min-w-64 flex-1">
            <label htmlFor="repo-url" className={LABEL}>Register a repository (https clone URL)</label>
            <input id="repo-url" value={url} onChange={e => setUrl(e.target.value)} className={INPUT}
              placeholder="https://github.com/org/repo" />
          </div>
          <button type="submit" disabled={busy || !url.trim()} className={BTN_PRIMARY}>{busy ? 'Checking…' : 'Register'}</button>
        </form>
        <Err msg={err} />
      </RoleGate>
      {loading && !data && <Note>Loading…</Note>}
      <Err msg={error || ''} />
      {!loading && repos.length === 0 && !error && <Note>No repositories registered.</Note>}
      <div className="space-y-3">{repos.map(r => <RepoRow key={r.id} r={r} refetch={refetch} />)}</div>
    </Card>
  )
}

function RepoRow({ r, refetch }: { r: Repo; refetch: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [fresh, setFresh] = useState<{ key: string; note: string } | null>(null)   // shown once, never refetched
  const [copied, setCopied] = useState(false)
  const run = (p: Promise<unknown>) => {
    setBusy(true); setErr('')
    p.then(refetch).catch(e => setErr(errMsg(e))).finally(() => setBusy(false))
  }
  const newKey = () => run(createRepoKey(r.id).then(k => { setFresh({ key: k.key, note: k.note }); setCopied(false) }))
  const revoke = (id: number, prefix: string) => {
    if (confirm(`Revoke key ${prefix}…? CI jobs using it will stop working.`)) run(revokeRepoKey(r.id, id))
  }
  const copy = () => fresh && navigator.clipboard.writeText(fresh.key).then(() => setCopied(true), () => {})
  const live = r.keys.filter(k => !k.revoked_at)
  return (
    <div className="rounded-sm border border-(--line-soft) p-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-semibold text-(--ink-hi)">{r.full_name}</span>
        <span className="text-[11px] text-(--ink-dim)">{r.host}</span>
        <span className={`rounded-sm border px-2 py-0.5 text-[11px] uppercase tracking-wider ${STATUS_CLS[r.status]}`}>{r.status}</span>
        {r.verified_via && <span className="text-[11px] text-(--ink-dim)">verified via {r.verified_via}</span>}
        <span className="ml-auto"><RelativeTime t={r.created_at} /></span>
      </div>

      {r.status === 'pending' && (
        <div className="mt-2 space-y-2">
          <Note>
            Commit a file <code>.sentinel-verify</code> containing{' '}
            <code className="select-all text-(--signal)">{r.verify_token}</code> to the default branch, then Verify.
          </Note>
          <RoleGate need="admin">
            <button onClick={() => run(verifyRepo(r.id))} disabled={busy} className={BTN}>{busy ? 'Checking…' : 'Verify'}</button>
          </RoleGate>
        </div>
      )}

      {r.status === 'active' && (
        <div className="mt-3">
          <div className="mb-1 flex items-center gap-2">
            <span className={LABEL + ' mb-0'}>CI keys</span>
            <RoleGate need="admin">
              <button onClick={newKey} disabled={busy} className={`ml-auto ${BTN}`}>New CI key</button>
            </RoleGate>
          </div>
          {live.length === 0 && <Note>No active keys. GitHub Actions and GitLab CI authenticate with OIDC; keys are for Jenkins.</Note>}
          {live.map(k => (
            <div key={k.id} className="flex flex-wrap items-center gap-3 text-[11px]">
              <code className="text-(--ink)">{k.prefix}…</code>
              <RelativeTime t={k.created_at} />
              <RoleGate need="admin">
                <button onClick={() => revoke(k.id, k.prefix)} disabled={busy} className={`ml-auto ${BTN_DANGER}`}>Revoke</button>
              </RoleGate>
            </div>
          ))}
        </div>
      )}

      {fresh && (
        <div role="status" className="mt-3 rounded-sm border border-(--signal)/40 bg-(--signal-dim) p-3">
          <p className="mb-2 text-[11px] text-(--ink-hi)">{fresh.note}</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all select-all rounded-sm bg-(--bg-2) px-2 py-1.5 text-xs text-(--signal)">{fresh.key}</code>
            <button onClick={copy} className={BTN}>{copied ? 'Copied' : 'Copy'}</button>
            <button onClick={() => setFresh(null)} className={BTN}>Done</button>
          </div>
        </div>
      )}
      <Err msg={err} />
    </div>
  )
}

function LlmKeys({ repos }: { repos: Repo[] }) {
  const { data, loading, error, refetch } = useLlmKeys(true)
  const [key, setKey] = useState('')
  const [repoId, setRepoId] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')
  const name = (id?: string | null) => (id ? repos.find(r => r.id === id)?.full_name || id : 'account-wide')
  const save = (e: React.FormEvent) => {
    e.preventDefault()
    if (!key.trim() || busy) return
    const k = key.trim()
    setKey('')   // the key lives only in this request; never kept in state or echoed back
    setBusy(true); setErr(''); setOk('')
    saveLlmKey(k, repoId).then(r => { setOk(`Key …${r.hint} saved.`); refetch() })
      .catch(x => setErr(errMsg(x))).finally(() => setBusy(false))
  }
  const keys = data?.keys || []
  return (
    <Card title="LLM keys">
      <Note>
        Your NVIDIA NIM key. Account-wide unless you pick a repository. Encrypted at rest. Code snippets
        from your repositories are sent to NVIDIA with this key.
      </Note>
      <form onSubmit={save} className="mt-3 flex flex-wrap items-end gap-2">
        <div className="min-w-64 flex-1">
          <label htmlFor="llm-key" className={LABEL}>NVIDIA NIM API key</label>
          <input id="llm-key" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)}
            className={INPUT} placeholder="nvapi-…" disabled={busy} />
        </div>
        <div>
          <label htmlFor="llm-scope" className={LABEL}>Scope</label>
          <select id="llm-scope" value={repoId} onChange={e => setRepoId(e.target.value)} disabled={busy}
            className="rounded-sm border border-(--line) bg-(--bg-2) px-2 py-2 text-sm text-(--ink)">
            <option value="">account-wide</option>
            {repos.filter(r => r.status === 'active').map(r => <option key={r.id} value={r.id}>{r.full_name}</option>)}
          </select>
        </div>
        <button type="submit" disabled={busy || !key.trim()} className={BTN_PRIMARY}>{busy ? 'Testing key…' : 'Save'}</button>
      </form>
      {busy && <p role="status" className="mt-2 text-[11px] text-(--ink-dim)">Sending one real request to NVIDIA to check the key — this can take up to a minute.</p>}
      {ok && <p role="status" className="mt-2 text-[11px] text-(--signal)">{ok}</p>}
      <Err msg={err} />
      <div className="mt-4 space-y-1 text-[11px]">
        {loading && !data && <Note>Loading…</Note>}
        <Err msg={error || ''} />
        {!loading && keys.length === 0 && !error && <Note>No key saved. Runs fail until you add one.</Note>}
        {keys.map(k => (
          <div key={k.id} className="flex flex-wrap items-center gap-3">
            <code className="text-(--ink)">…{k.hint}</code>
            <span className="text-(--ink-dim)">{k.provider}</span>
            <span className="text-(--ink-hi)">{name(k.repo_id)}</span>
            <span className="ml-auto text-(--ink-dim)">validated <RelativeTime t={k.validated_at} /></span>
            <RoleGate need="admin">
              <button onClick={() => { if (window.confirm('Revoke this key? Runs that use it fail until another is saved.'))
                  revokeLlmKey(k.id).then(refetch).catch(e => setErr(errMsg(e))) }}
                className="rounded-sm border border-(--line) px-2 py-0.5 text-[10px] uppercase tracking-wide text-(--ink-dim) hover:border-red-500/40 hover:text-red-300">Revoke</button>
            </RoleGate>
          </div>
        ))}
      </div>
    </Card>
  )
}

// ---------------------------------------------------------------- platform admin (OIDC, accounts.is_admin)
export function Admin() {
  const { me } = useAuth()
  const { data, loading, error, refetch } = useAdminAccounts()
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  if (!me?.account.is_admin) return <Note>Platform admins only.</Note>
  const toggle = (id: string, email: string | undefined, to: 'active' | 'suspended') => {
    if (to === 'suspended' && !confirm(`Suspend ${email || id}? Their sessions and CI calls are refused at once.`)) return
    setBusy(id); setErr('')
    setAccountStatus(id, to).then(refetch).catch(e => setErr(errMsg(e))).finally(() => setBusy(''))
  }
  const rows = data?.accounts || []
  return (
    <div>
      <h1 className="mb-3 text-sm font-bold uppercase tracking-widest text-(--ink-hi)">Accounts</h1>
      <Err msg={err || error || ''} />
      <div className="overflow-x-auto rounded-md border border-(--line)">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-(--line) text-[10px] uppercase tracking-widest text-(--ink-dim)">
            <th className="px-3 py-2.5 font-medium">Account</th><th className="font-medium">Status</th>
            <th className="font-medium">Runs 24h</th><th className="font-medium">Retention</th>
            <th className="font-medium">Created</th><th className="px-3 font-medium text-right">Action</th></tr></thead>
          <tbody>
            {rows.map(a => (
              <tr key={a.id} className="border-b border-(--line-soft)">
                <td className="px-3 py-2">
                  <span className="text-(--ink-hi)">{a.email || a.display_name || a.id}</span>
                  {a.is_admin && <span className="ml-2 text-[10px] uppercase tracking-wider text-(--signal)">admin</span>}
                </td>
                <td className={a.status === 'active' ? 'text-emerald-300' : 'text-red-300'}>{a.status}</td>
                <td className="tabular-nums">{a.runs_24h}</td>
                <td className="tabular-nums text-(--ink-dim)">{a.retention_days ?? '—'} d</td>
                <td><RelativeTime t={a.created_at} /></td>
                <td className="px-3 text-right">
                  {a.id !== me.account.id && (a.status === 'active'
                    ? <button onClick={() => toggle(a.id, a.email, 'suspended')} disabled={!!busy} className={BTN_DANGER}>Suspend</button>
                    : <button onClick={() => toggle(a.id, a.email, 'active')} disabled={!!busy} className={BTN}>Reactivate</button>)}
                </td>
              </tr>
            ))}
            {loading && !data && <tr><td colSpan={6} className="py-10 text-center text-(--ink-dim)">Loading…</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- privacy
export function Privacy() {
  const { me, authMode } = useAuth()
  const days = me?.account.retention_days ?? 90
  const items = [
    'Code snippets and diffs from your repositories are sent to the LLM provider (NVIDIA NIM)'
      + (authMode === 'oidc' ? ' using your own key.' : ' using the key this Sentinel is configured with.'),
    "Findings, runs and decisions are stored in this Sentinel's PostgreSQL database.",
    ...(authMode === 'oidc' ? ['Each account sees only its own data. The database enforces this, not just the app.'] : []),
    `Runs and their findings are deleted after the retention period: ${days} days${me ? ' for your account' : ' by default'}.`,
    "CI credentials stay in your CI platform's secret store. Sentinel stores only a hash of each CI key.",
    'Unlinking a code host suspends every repository registered through it and revokes their CI keys.',
  ]
  return (
    <Card title="Privacy">
      <ul className="list-disc space-y-2 pl-5 text-sm text-(--ink)">{items.map(t => <li key={t}>{t}</li>)}</ul>
    </Card>
  )
}
