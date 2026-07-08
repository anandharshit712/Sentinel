import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import type { RunDetail as RD, Finding, RunState } from './types'
import {
  BandChip, DecisionChip, SeverityChip, StateChip, ScoreDial, HealthGauge, Card,
  RoleGate, useRun, useApprovals, resolveApproval, rerun, STAGES, stageRank,
} from './lib'
import { useRunEvents } from './sse'

export default function RunDetailRoute() {
  const { id = '' } = useParams()
  return <RunDetailPane id={id} full />
}

export function RunDetailPane({ id, full }: { id: string; full?: boolean }) {
  const { data, loading, error, refetch } = useRun(id)
  const live = !!data && data.run.state !== 'done' && data.run.state !== 'failed'
  const { events, liveState } = useRunEvents(id, live, refetch)
  const nav = useNavigate()

  if (loading && !data) return <div className="p-6 text-zinc-500">Loading {id}…</div>
  if (error) return <div className="p-6 text-red-400">Error: {error}</div>
  if (!data) return null
  const { run, review_report, test_plan, test_results, risk_score, decision } = data
  const state = (liveState || run.state) as RunState
  const prodGate = run.to_env === 'production'

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        {full && <Link to="/" className="text-sm text-zinc-400 hover:text-zinc-200">← runs</Link>}
        <h1 className="font-mono text-lg text-zinc-100">{run.repo}</h1>
        <span className="text-zinc-500">{run.from_env} → {run.to_env}</span>
        <StateChip s={state} />
        <BandChip band={risk_score?.band ?? run.band} />
        <DecisionChip d={decision?.decision ?? run.decision} />
        <span className="ml-auto flex gap-2">
          <RoleGate need="approver">
            <button onClick={() => rerun(id).then(r => nav(`/runs/${r.run_id}`))}
              className="rounded border border-zinc-700 px-3 py-1 text-sm hover:bg-zinc-800">Rerun</button>
          </RoleGate>
        </span>
      </header>

      <StageTimeline state={state} events={events} />

      {decision && <DecisionCard d={decision} prodGate={prodGate} runId={id} onResolved={refetch} />}
      {risk_score && <RiskScoreCard r={risk_score} />}
      {review_report && <ReviewReportCard r={review_report} />}
      {test_results && <TestResultsCard r={test_results} />}
      {test_plan && <TestPlanCard r={test_plan} />}
      {!decision && <p className="text-sm text-zinc-500">No decision yet — pipeline running.</p>}
    </div>
  )
}

function StageTimeline({ state, events }: { state: RunState; events: any[] }) {
  const cur = stageRank(state)
  const failed = state === 'failed'
  return (
    <Card title="Stages" right={<span className="text-xs text-zinc-500">{events.length} events</span>}>
      <ol className="flex flex-wrap items-center gap-2">
        {STAGES.map((s, i) => {
          const done = i < cur || state === 'done'
          const active = i === cur && !done
          return (
            <li key={s} className={`rounded px-2 py-1 text-xs border ${
              done ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
              : active ? 'border-blue-500/40 bg-blue-500/10 text-blue-300 animate-pulse'
              : 'border-zinc-800 text-zinc-500'}`}>{s}</li>
          )
        })}
        {failed && <li className="rounded border border-red-500/30 bg-red-500/10 px-2 py-1 text-xs text-red-300">failed</li>}
      </ol>
      {events.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-zinc-500">live log</summary>
          <ul aria-live="polite" className="mt-2 max-h-40 space-y-0.5 overflow-auto font-mono text-xs text-zinc-400">
            {events.filter(e => e.text).map((e, i) => <li key={i}>{e.text}</li>)}
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
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        {d.rule_fired && <code className="rounded bg-zinc-800 px-2 py-0.5 text-xs">{d.rule_fired}</code>}
        {prodGate && <span className="rounded border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-xs text-red-300">🔒 Human approval required — always (staging→production)</span>}
      </div>
      <dl className="space-y-1 text-sm">
        {sections.filter(s => trail[s]).map(s => (
          <div key={s} className="flex gap-2">
            <dt className="w-16 shrink-0 text-zinc-500 capitalize">{s}</dt>
            <dd className="text-zinc-300">{trail[s]}</dd>
          </div>
        ))}
      </dl>
      {d.actions_taken?.length ? (
        <div className="mt-2 text-xs text-zinc-500">
          actions: {d.actions_taken.map(a => `${a.action}${a.detail ? ` (${a.detail})` : ''}`).join(', ')}
        </div>
      ) : null}
      {d.approval_required && (
        <RoleGate need="approver">
          <div className="mt-3 border-t border-zinc-800 pt-3">
            <ApprovalControls runId={runId} onResolved={onResolved} />
          </div>
        </RoleGate>
      )}
    </Card>
  )
}

// Resolve the pending approval for a run inline (also used on the Approvals screen).
export function ApprovalControls({ runId, approvalId, onResolved }:
  { runId?: string; approvalId?: number; onResolved: () => void }) {
  const { data } = useApprovals('pending')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const id = approvalId ?? data?.approvals.find(a => a.run_id === runId)?.id
  if (id == null) return <span className="text-xs text-zinc-500">No pending approval.</span>
  const act = (action: 'approve' | 'reject') => {
    if (action === 'reject' && !comment.trim()) return
    setBusy(true)
    resolveApproval(id, action, comment).then(onResolved).finally(() => setBusy(false))
  }
  return (
    <div className="space-y-2">
      <textarea value={comment} onChange={e => setComment(e.target.value)}
        placeholder="Comment (required to reject)"
        className="w-full rounded border border-zinc-700 bg-zinc-900 p-2 text-sm" rows={2} />
      <div className="flex gap-2">
        <button disabled={busy} onClick={() => act('approve')}
          className="rounded bg-emerald-600/80 px-3 py-1 text-sm text-white hover:bg-emerald-600 disabled:opacity-50">Approve</button>
        <button disabled={busy || !comment.trim()} onClick={() => act('reject')}
          className="rounded bg-red-600/80 px-3 py-1 text-sm text-white hover:bg-red-600 disabled:opacity-50">Reject</button>
      </div>
    </div>
  )
}

function RiskScoreCard({ r }: { r: NonNullable<RD['risk_score']> }) {
  const contribs = r.contributions || []
  const max = Math.max(1, ...contribs.map(c => Math.abs(c.points)))
  const esc = r.llm_escalation
  return (
    <Card title="Risk Score" right={<code className="text-xs text-zinc-500">{r.formula_version}</code>}>
      <div className="flex flex-wrap items-center gap-6">
        <ScoreDial score={r.score} band={r.band} />
        <div className="min-w-56 flex-1 space-y-1">
          {contribs.length === 0 && <p className="text-sm text-zinc-500">No contributing factors.</p>}
          {contribs.map((c, i) => (
            <div key={i} className="text-xs">
              <div className="flex justify-between text-zinc-400">
                <span>{c.factor}{c.evidence_ref ? ` · ${c.evidence_ref}` : ''}</span>
                <span className="font-semibold text-zinc-200">+{c.points}</span>
              </div>
              <div className="h-1.5 rounded bg-zinc-800">
                <div className="h-1.5 rounded bg-zinc-500" style={{ width: `${(Math.abs(c.points) / max) * 100}%` }} />
              </div>
            </div>
          ))}
          {esc && esc.points_added > 0 && (
            <div className="mt-2 rounded border border-violet-500/40 bg-violet-500/10 px-2 py-1 text-xs text-violet-300">
              🤖 +{esc.points_added} pts — LLM escalation{esc.justification ? `: ${esc.justification}` : ''}
            </div>
          )}
        </div>
      </div>
      {r.explanation && <p className="mt-3 text-xs text-zinc-500">{r.explanation}</p>}
    </Card>
  )
}

function ReviewReportCard({ r }: { r: NonNullable<RD['review_report']> }) {
  const findings = r.findings || []
  return (
    <Card title="Review Report" right={<HealthGauge value={r.pr_health_score} />}>
      {r.executive_summary && <p className="mb-3 text-sm text-zinc-300">{r.executive_summary}</p>}
      {findings.length === 0 && <p className="text-sm text-zinc-500">No findings.</p>}
      <ul className="space-y-2">
        {findings.map((f: Finding) => (
          <li key={f.id} className="rounded border border-zinc-800 p-2">
            <div className="flex flex-wrap items-center gap-2">
              <SeverityChip s={f.severity} />
              <span className="text-sm text-zinc-200">{f.title}</span>
              {f.source === 'llm' && <span className="rounded bg-violet-500/15 px-1.5 text-xs text-violet-300">AI</span>}
              {f.file && <code className="ml-auto text-xs text-zinc-500">{f.file}{f.line_start ? `:${f.line_start}` : ''}</code>}
            </div>
            {f.explanation && <p className="mt-1 text-xs text-zinc-400">{f.explanation}</p>}
            {f.fix_suggestion && <p className="mt-1 text-xs text-emerald-400/80">fix: {f.fix_suggestion}</p>}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function TestResultsCard({ r }: { r: NonNullable<RD['test_results']> }) {
  const t = r.totals || { passed: 0, failed: 0, skipped: 0 }
  return (
    <Card title="Test Results" right={<code className="text-xs text-zinc-500">{r.runner}</code>}>
      <div className="flex gap-4 text-sm">
        <span className="text-emerald-300">{t.passed} passed</span>
        <span className={t.failed ? 'text-red-300' : 'text-zinc-500'}>{t.failed} failed</span>
        <span className="text-zinc-500">{t.skipped} skipped</span>
        {r.timed_out && <span className="text-red-300">timed out</span>}
        {r.duration_seconds != null && <span className="ml-auto text-zinc-500">{r.duration_seconds}s</span>}
      </div>
      {r.command && <code className="mt-2 block truncate text-xs text-zinc-600">{r.command}</code>}
    </Card>
  )
}

function TestPlanCard({ r }: { r: NonNullable<RD['test_plan']> }) {
  return (
    <Card title="Test Plan" right={r.selection_confidence
      ? <span className="text-xs text-zinc-400">confidence: {r.selection_confidence}</span> : undefined}>
      <ul className="space-y-1 text-sm">
        {(r.selected || []).map((s, i) => (
          <li key={i} className="flex gap-2">
            <code className="text-xs text-zinc-300">{s.test_id}</code>
            <span className="text-xs text-zinc-500">{s.mapping_source}{s.reason ? ` · ${s.reason}` : ''}</span>
          </li>
        ))}
      </ul>
      {r.excluded_summary && <p className="mt-2 text-xs text-zinc-500">{r.excluded_summary}</p>}
    </Card>
  )
}
