import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import type { RunDetail as RD, Finding, RunState } from './types'
import {
  BandChip, DecisionChip, SeverityChip, StateChip, ScoreDial, HealthGauge, Card,
  RoleGate, useRun, useApprovals, resolveApproval, rerun, STAGES, stageRank,
} from './lib'
import { useRunEvents } from './sse'
import { AgentGraph } from './AgentGraph'

export default function RunDetailRoute() {
  const { id = '' } = useParams()
  return <RunDetailPane id={id} full />
}

export function RunDetailPane({ id, full }: { id: string; full?: boolean }) {
  const { data, loading, error, refetch } = useRun(id)
  const live = !!data && data.run.state !== 'done' && data.run.state !== 'failed'
  const { events, liveState } = useRunEvents(id, live, refetch)
  const nav = useNavigate()

  if (loading && !data) return <div className="p-6 text-[var(--ink-dim)]">Loading {id}…</div>
  if (error) return <div className="p-6 text-red-400">Error: {error}</div>
  if (!data) return null
  const { run, review_report, test_plan, test_results, risk_score, decision } = data
  const state = (liveState || run.state) as RunState
  const prodGate = run.to_env === 'production'
  const dec = decision?.decision ?? run.decision

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        {full && <Link to="/" className="text-xs text-[var(--ink-dim)] hover:text-[var(--signal)]">← runs</Link>}
        <h1 className="text-lg font-bold tracking-wide text-[var(--ink-hi)]">{run.repo}</h1>
        <span className="text-[var(--ink-dim)] text-sm">{run.from_env} <span className="text-[var(--signal)]">→</span> {run.to_env}</span>
        <StateChip s={state} />
        <BandChip band={risk_score?.band ?? run.band} />
        <DecisionChip d={dec} />
        <span className="ml-auto flex gap-2">
          <RoleGate need="approver">
            <button onClick={() => rerun(id).then(r => nav(`/runs/${r.run_id}`))}
              className="rounded-sm border border-[var(--line)] px-3 py-1 text-xs uppercase tracking-wide text-[var(--ink-dim)] hover:border-[var(--signal)] hover:text-[var(--signal)]">Rerun</button>
          </RoleGate>
        </span>
      </header>

      <NetworkCard state={state} events={events} decision={dec} />

      {decision && <DecisionCard d={decision} prodGate={prodGate} runId={id} onResolved={refetch} />}
      {risk_score && <RiskScoreCard r={risk_score} />}
      {review_report && <ReviewReportCard r={review_report} />}
      {test_results && <TestResultsCard r={test_results} />}
      {test_plan && <TestPlanCard r={test_plan} />}
      {!decision && <p className="text-sm text-[var(--ink-dim)]">No decision yet — pipeline running.</p>}
    </div>
  )
}

function NetworkCard({ state, events, decision }: { state: RunState; events: any[]; decision: any }) {
  const cur = stageRank(state)
  return (
    <Card title="Agent Network · Live"
          right={<span className="text-[10px] uppercase tracking-widest text-[var(--ink-dim)]">{events.length} signals</span>}>
      {/* linear stage strip */}
      <ol className="mb-4 flex flex-wrap items-center gap-1.5">
        {STAGES.map((s, i) => {
          const done = i < cur || state === 'done'
          const active = i === cur && !done && state !== 'failed'
          return (
            <li key={s} className={`rounded-sm px-2 py-0.5 text-[10px] uppercase tracking-wide border ${
              done ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300'
              : active ? 'border-[var(--signal)] bg-[var(--signal-dim)] text-[var(--ink-hi)]'
              : 'border-[var(--line)] text-[var(--ink-dim)]'}`}
              style={active ? { animation: 'blink 1.2s steps(2) infinite' } : undefined}>{s}</li>
          )
        })}
        {state === 'failed' && <li className="rounded-sm border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-[10px] uppercase text-red-300">failed</li>}
      </ol>

      <AgentGraph events={events} state={state} decision={decision} />

      {events.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[10px] uppercase tracking-widest text-[var(--ink-dim)] hover:text-[var(--signal)]">signal log</summary>
          <ul aria-live="polite" className="mt-2 max-h-40 space-y-0.5 overflow-auto text-[11px] text-[var(--ink-dim)]">
            {events.filter(e => e.text).map((e, i) => (
              <li key={i}><span className="text-[var(--signal)]">›</span> {e.text}</li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  )
}

function DecisionCard({ d, prodGate, runId, onResolved }:
  { d: NonNullable<RD['decision']>; prodGate: boolean; runId: string; onResolved: () => void }) {
  const trail = (d.reasoning_trail || {}) as Record<string, string>
  const sections = ['review', 'testing', 'results', 'context', 'policy'] as const
  return (
    <Card title="Decision" right={<DecisionChip d={d.decision} />}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        {d.rule_fired && <code className="rounded-sm border border-[var(--line)] bg-[var(--panel-hi)] px-2 py-0.5 text-[11px] text-[var(--ink)]">{d.rule_fired}</code>}
        {prodGate && <span className="rounded-sm border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-[11px] text-red-300">🔒 Human approval required — always (staging→production)</span>}
      </div>
      <dl className="space-y-1.5 text-sm">
        {sections.filter(s => trail[s]).map(s => (
          <div key={s} className="flex gap-3">
            <dt className="w-16 shrink-0 text-[10px] uppercase tracking-wide text-[var(--signal)] pt-0.5">{s}</dt>
            <dd className="text-[var(--ink)]">{trail[s]}</dd>
          </div>
        ))}
      </dl>
      {d.actions_taken?.length ? (
        <div className="mt-3 text-[11px] text-[var(--ink-dim)]">
          actions: {d.actions_taken.map(a => `${a.action}${a.detail ? ` (${a.detail})` : ''}`).join(', ')}
        </div>
      ) : null}
      {d.approval_required && (
        <RoleGate need="approver">
          <div className="mt-4 border-t border-[var(--line-soft)] pt-3">
            <ApprovalControls runId={runId} onResolved={onResolved} />
          </div>
        </RoleGate>
      )}
    </Card>
  )
}

export function ApprovalControls({ runId, approvalId, onResolved }:
  { runId?: string; approvalId?: number; onResolved: () => void }) {
  const { data } = useApprovals('pending')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const id = approvalId ?? data?.approvals.find(a => a.run_id === runId)?.id
  if (id == null) return <span className="text-xs text-[var(--ink-dim)]">No pending approval.</span>
  const act = (action: 'approve' | 'reject') => {
    if (action === 'reject' && !comment.trim()) return
    setBusy(true)
    resolveApproval(id, action, comment).then(onResolved).finally(() => setBusy(false))
  }
  return (
    <div className="space-y-2">
      <textarea value={comment} onChange={e => setComment(e.target.value)}
        placeholder="Comment (required to reject)"
        className="w-full rounded-sm border border-[var(--line)] bg-[var(--bg-2)] p-2 text-sm focus:border-[var(--signal)]" rows={2} />
      <div className="flex gap-2">
        <button disabled={busy} onClick={() => act('approve')}
          className="rounded-sm border border-emerald-500/40 bg-emerald-500/10 px-4 py-1 text-xs uppercase tracking-wide text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50">Approve</button>
        <button disabled={busy || !comment.trim()} onClick={() => act('reject')}
          className="rounded-sm border border-red-500/40 bg-red-500/10 px-4 py-1 text-xs uppercase tracking-wide text-red-300 hover:bg-red-500/20 disabled:opacity-40">Reject</button>
      </div>
    </div>
  )
}

function RiskScoreCard({ r }: { r: NonNullable<RD['risk_score']> }) {
  const contribs = r.contributions || []
  const max = Math.max(1, ...contribs.map(c => Math.abs(c.points)))
  const esc = r.llm_escalation
  return (
    <Card title="Risk Score" right={<code className="text-[10px] text-[var(--ink-dim)]">{r.formula_version}</code>}>
      <div className="flex flex-wrap items-center gap-6">
        <ScoreDial score={r.score} band={r.band} />
        <div className="min-w-56 flex-1 space-y-1.5">
          {contribs.length === 0 && <p className="text-sm text-[var(--ink-dim)]">No contributing factors.</p>}
          {contribs.map((c, i) => (
            <div key={i} className="text-[11px]">
              <div className="flex justify-between">
                <span className="text-[var(--ink-dim)]">{c.factor}{c.evidence_ref ? ` · ${c.evidence_ref}` : ''}</span>
                <span className="font-bold tabular-nums text-[var(--ink-hi)]">+{c.points}</span>
              </div>
              <div className="mt-0.5 h-1.5 rounded-full bg-[rgba(148,163,184,0.1)]">
                <div className="h-1.5 rounded-full bg-[var(--ink-dim)]" style={{ width: `${(Math.abs(c.points) / max) * 100}%` }} />
              </div>
            </div>
          ))}
          {esc && esc.points_added > 0 && (
            <div className="mt-2 rounded-sm border border-violet-500/40 bg-violet-500/10 px-2 py-1 text-[11px] text-violet-300">
              ⬡ +{esc.points_added} pts — LLM escalation{esc.justification ? `: ${esc.justification}` : ''}
            </div>
          )}
        </div>
      </div>
      {r.explanation && <p className="mt-3 border-t border-[var(--line-soft)] pt-2 text-[11px] text-[var(--ink-dim)]">{r.explanation}</p>}
    </Card>
  )
}

function ReviewReportCard({ r }: { r: NonNullable<RD['review_report']> }) {
  const findings = r.findings || []
  return (
    <Card title="Review Report" right={<HealthGauge value={r.pr_health_score} />}>
      {r.executive_summary && <p className="mb-3 text-sm text-[var(--ink)]">{r.executive_summary}</p>}
      {findings.length === 0 && <p className="text-sm text-[var(--ink-dim)]">No findings.</p>}
      <ul className="space-y-2">
        {findings.map((f: Finding) => (
          <li key={f.id} className="rounded-sm border border-[var(--line)] bg-[var(--panel)] p-2">
            <div className="flex flex-wrap items-center gap-2">
              <SeverityChip s={f.severity} />
              <span className="text-sm text-[var(--ink-hi)]">{f.title}</span>
              {f.source === 'llm' && <span className="rounded-sm border border-violet-500/30 bg-violet-500/10 px-1.5 text-[10px] uppercase text-violet-300">AI</span>}
              {f.file && <code className="ml-auto text-[10px] text-[var(--ink-dim)]">{f.file}{f.line_start ? `:${f.line_start}` : ''}</code>}
            </div>
            {f.explanation && <p className="mt-1 text-[11px] text-[var(--ink-dim)]">{f.explanation}</p>}
            {f.fix_suggestion && <p className="mt-1 text-[11px] text-emerald-400/80">fix: {f.fix_suggestion}</p>}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function TestResultsCard({ r }: { r: NonNullable<RD['test_results']> }) {
  const t = r.totals || { passed: 0, failed: 0, skipped: 0 }
  const total = r.suite_total ?? 0
  const executed = r.executed ?? (t.passed + t.failed + t.skipped + (t.errors || 0))
  const excluded = r.excluded ?? Math.max(0, total - executed)
  const savedPct = total > 0 ? Math.round((excluded / total) * 100) : 0
  const subset = r.selection_mode === 'subset'
  const modeChip = r.selection_mode && (
    <span className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[10px] uppercase tracking-wider border ${
      subset ? 'border-[var(--signal)]/40 bg-[var(--signal-dim)] text-[var(--signal)]'
             : 'border-amber-500/40 bg-amber-500/10 text-amber-300'}`}>
      {subset ? '◇ smart subset' : '◆ full suite · no mapping'}
    </span>
  )
  const statusColor: Record<string, string> = { passed: 'bg-emerald-400', failed: 'bg-red-400', error: 'bg-red-400', skipped: 'bg-slate-500' }
  return (
    <Card title="Test Results · Selection" right={modeChip}>
      {total > 0 && (
        <div className="mb-3">
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-2xl font-bold tabular-nums text-[var(--ink-hi)]">
              {executed}<span className="text-[var(--ink-dim)] text-base"> / {total}</span>
              <span className="ml-2 text-xs font-normal uppercase tracking-wide text-[var(--ink-dim)]">tests run</span>
            </span>
            <span className="text-sm font-semibold text-[var(--signal)]">{excluded} excluded · {savedPct}% skipped</span>
          </div>
          {/* selected vs excluded bar */}
          <div className="flex h-2 overflow-hidden rounded-full bg-[rgba(148,163,184,0.1)]">
            <div style={{ width: `${total ? (executed / total) * 100 : 0}%`, background: 'var(--signal)', boxShadow: '0 0 6px var(--signal)' }} />
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-4 text-sm">
        <span className="text-emerald-300">▪ {t.passed} passed</span>
        <span className={t.failed ? 'text-red-300' : 'text-[var(--ink-dim)]'}>▪ {t.failed} failed</span>
        <span className="text-[var(--ink-dim)]">▪ {t.skipped} skipped</span>
        {r.timed_out && <span className="text-red-300">timed out</span>}
        {r.duration_seconds != null && <span className="ml-auto text-[var(--ink-dim)]">{r.duration_seconds}s · {r.runner}</span>}
      </div>
      {r.cases && r.cases.length > 0 && (
        <ul className="mt-3 max-h-44 space-y-1 overflow-auto border-t border-[var(--line-soft)] pt-2">
          {r.cases.map((c, i) => (
            <li key={i} className="flex items-center gap-2 text-[11px]">
              <i className={`h-1.5 w-1.5 rounded-full ${statusColor[c.status] || 'bg-slate-500'}`} />
              <code className="text-[var(--ink)]">{c.test_id}</code>
              <span className="ml-auto text-[var(--ink-dim)]">{c.duration_ms}ms</span>
            </li>
          ))}
        </ul>
      )}
      {r.stage_failure && <p className="mt-2 text-[11px] text-amber-300">⚠ {r.stage_failure}</p>}
      {r.command && <code className="mt-2 block truncate text-[10px] text-[var(--ink-dim)]" title={r.command}>{r.command}</code>}
    </Card>
  )
}

function TestPlanCard({ r }: { r: NonNullable<RD['test_plan']> }) {
  return (
    <Card title="Test Plan" right={r.selection_confidence
      ? <span className="text-[10px] uppercase tracking-wide text-[var(--ink-dim)]">confidence: {r.selection_confidence}</span> : undefined}>
      <ul className="space-y-1 text-sm">
        {(r.selected || []).map((s, i) => (
          <li key={i} className="flex flex-wrap gap-2">
            <code className="text-[11px] text-[var(--ink)]">{s.test_id}</code>
            <span className="text-[10px] text-[var(--ink-dim)]">{s.mapping_source}{s.reason ? ` · ${s.reason}` : ''}</span>
          </li>
        ))}
      </ul>
      {r.excluded_summary && <p className="mt-2 text-[11px] text-[var(--ink-dim)]">{r.excluded_summary}</p>}
    </Card>
  )
}
