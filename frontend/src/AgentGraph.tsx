import { useMemo } from 'react'
import type { RunEvent, RunState, Decision } from './types'

/* Live agent-network graph (the demo hero). Topology mirrors registries/sentinel.hocon:
   the frontman fans out to 9 pipeline stages (agents + two direct tools); each agent owns
   sub-tools. Nodes light up and data-flow packets animate as SSE progress streams in. */

type Node = { id: string; label: string; tools?: { id: string; label: string }[] }

const FRONTMAN: Node = { id: 'delivery_coordinator', label: 'Coordinator' }
const PIPELINE: Node[] = [
  { id: 'change_analysis_agent', label: 'Change', tools: [
    { id: 'git_diff', label: 'git diff' }, { id: 'ast_analyzer', label: 'ast' }, { id: 'dependency_graph', label: 'deps' }] },
  { id: 'security_review_agent', label: 'Security', tools: [
    { id: 'secret_scanner', label: 'secrets' }, { id: 'dependency_cve', label: 'cve' }, { id: 'contract_store', label: 'store' }] },
  { id: 'code_quality_agent', label: 'Quality', tools: [
    { id: 'complexity_metrics', label: 'complexity' }, { id: 'contract_store', label: 'store' }] },
  { id: 'report_publisher', label: 'Report' },
  { id: 'test_selection_agent', label: 'Test Select', tools: [{ id: 'test_mapper', label: 'map' }] },
  { id: 'test_runner', label: 'Test Run' },
  { id: 'environment_context_agent', label: 'Env', tools: [
    { id: 'incident_history', label: 'incidents' }, { id: 'deploy_window', label: 'window' }] },
  { id: 'risk_scoring_agent', label: 'Risk', tools: [{ id: 'risk_calculator', label: 'calc' }] },
  { id: 'promotion_gating_agent', label: 'Gate', tools: [
    { id: 'trust_ladder', label: 'ladder' }, { id: 'decision_logger', label: 'log' },
    { id: 'cicd_action', label: 'cicd' }, { id: 'notification', label: 'notify' }] },
]

type Status = 'pending' | 'active' | 'done' | 'failed'

function useStatus(events: RunEvent[], state: RunState) {
  return useMemo(() => {
    const invoked = events.filter(e => e.invoked).map(e => e.invoked as string)
    const seen = new Set(invoked)
    const terminalDone = state === 'done'
    const failed = state === 'failed'
    const entered = PIPELINE.map(s => seen.has(s.id) || (s.tools || []).some(t => seen.has(t.id)))
    let activeIdx = -1
    entered.forEach((e, i) => { if (e) activeIdx = i })
    const activeTool = [...invoked].reverse().find(id => PIPELINE.some(s => (s.tools || []).some(t => t.id === id)))
    const stageStatus = (i: number): Status =>
      terminalDone ? 'done'
      : i < activeIdx ? 'done'
      : i === activeIdx ? (failed ? 'failed' : 'active')
      : 'pending'
    const toolStatus = (id: string, stageActive: boolean): Status =>
      terminalDone || (seen.has(id) && !(stageActive && id === activeTool)) ? 'done'
      : id === activeTool && stageActive ? 'active' : 'pending'
    return { activeIdx, stageStatus, toolStatus, frontmanActive: !terminalDone && !failed && activeIdx >= 0 && activeIdx < PIPELINE.length }
  }, [events, state])
}

const NODE: Record<Status, string> = {
  pending: 'border-[var(--line)] text-[var(--ink-dim)] bg-[var(--panel)]',
  active: 'border-[var(--signal)] text-[var(--ink-hi)] bg-[var(--signal-dim)]',
  done: 'border-emerald-500/40 text-emerald-300 bg-emerald-500/5',
  failed: 'border-red-500/50 text-red-300 bg-red-500/10',
}

function Connector({ live }: { live: boolean }) {
  return (
    <div className="relative mt-[17px] h-px w-7 shrink-0 self-start"
         style={{ background: live ? 'var(--signal)' : 'var(--line)', boxShadow: live ? '0 0 6px var(--signal)' : undefined }}>
      {live && <span className="absolute -top-[3px] h-[7px] w-[7px] rounded-full bg-[var(--signal)]"
                     style={{ boxShadow: '0 0 8px var(--signal)', offsetPath: 'path("M0,0 H28")', animation: 'packet 1.1s linear infinite' }} />}
    </div>
  )
}

function NodeCard({ node, status, toolStatus }:
  { node: Node; status: Status; toolStatus: (id: string, active: boolean) => Status }) {
  const active = status === 'active'
  return (
    <div className="flex w-[104px] shrink-0 flex-col items-center gap-1.5">
      <div className={`flex h-9 w-full items-center justify-center rounded-sm border px-1 text-[11px] font-semibold uppercase tracking-wide transition-all ${NODE[status]}`}
           style={active ? { animation: 'pulse-ring 1.5s infinite', boxShadow: '0 0 10px var(--signal-dim)' } : undefined}>
        {node.label}
      </div>
      {node.tools && (
        <div className="flex flex-col items-stretch gap-0.5">
          {node.tools.map((t, i) => {
            const ts = toolStatus(t.id, active)
            return (
              <div key={i} className={`relative rounded-[3px] border px-1.5 py-0.5 text-center text-[9px] lowercase tracking-wide ${NODE[ts]}`}>
                {ts === 'active' && <span className="absolute -top-2 left-1/2 h-2 w-px -translate-x-1/2"
                                          style={{ background: 'var(--signal)', boxShadow: '0 0 4px var(--signal)' }} />}
                {t.label}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function AgentGraph({ events, state, decision }:
  { events: RunEvent[]; state: RunState; decision?: Decision | null }) {
  const { activeIdx, stageStatus, toolStatus, frontmanActive } = useStatus(events, state)
  const decColor = decision === 'promote' ? '#34d399' : decision === 'escalate' ? '#f87171' : '#94a3b8'
  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-max items-start gap-0">
        {/* frontman */}
        <div className="flex w-[104px] shrink-0 flex-col items-center">
          <div className={`flex h-9 w-full items-center justify-center rounded-sm border px-1 text-[11px] font-semibold uppercase tracking-wide ${frontmanActive ? NODE.active : state === 'done' ? NODE.done : NODE.pending}`}
               style={frontmanActive ? { boxShadow: '0 0 10px var(--signal-dim)' } : undefined}>
            {FRONTMAN.label}
          </div>
          <span className="mt-1 text-[9px] uppercase tracking-widest text-[var(--ink-dim)]">frontman</span>
        </div>
        {PIPELINE.map((node, i) => (
          <div key={node.id} className="flex items-start">
            <Connector live={state !== 'done' && state !== 'failed' && i === activeIdx} />
            <NodeCard node={node} status={stageStatus(i)} toolStatus={toolStatus} />
          </div>
        ))}
        {/* decision terminal */}
        <div className="flex items-start">
          <Connector live={false} />
          <div className="flex w-[92px] shrink-0 flex-col items-center">
            <div className="flex h-9 w-full items-center justify-center rounded-sm border px-1 text-[11px] font-bold uppercase tracking-wide"
                 style={{ borderColor: decision ? decColor : 'var(--line)', color: decision ? decColor : 'var(--ink-dim)',
                          background: decision ? `${decColor}14` : 'transparent',
                          boxShadow: decision ? `0 0 10px ${decColor}30` : undefined }}>
              {decision || '···'}
            </div>
            <span className="mt-1 text-[9px] uppercase tracking-widest text-[var(--ink-dim)]">decision</span>
          </div>
        </div>
      </div>
    </div>
  )
}
