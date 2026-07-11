import { useMemo } from 'react'
import type { RunEvent, RunState, Decision } from './types'

/* Live agent-network graph (the demo hero). Topology mirrors registries/sentinel.hocon:
   the frontman fans out to 9 pipeline stages (agents + two direct tools); each agent owns
   sub-tools. Nodes light up and data-flow packets animate as SSE progress streams in. */

type Node = { id: string; label: string; tools?: { id: string; label: string }[]; shard?: boolean }

const FRONTMAN: Node = { id: 'delivery_coordinator', label: 'Coordinator' }
// Security fans out adaptively (B5/A8): review_planner sizes 1-4 shards, each scanned by a
// security_reviewer_N, then senior_security_agent digests. All 4 reviewer slots are always shown;
// a slot the planner didn't allocate stays dim (`shard` nodes take seen-based, not positional, status).
const PIPELINE: Node[] = [
  { id: 'change_analysis_agent', label: 'Change', tools: [
    { id: 'git_diff', label: 'git diff' }, { id: 'ast_analyzer', label: 'ast' }, { id: 'dependency_graph', label: 'deps' }] },
  { id: 'review_planner', label: 'Plan', tools: [{ id: 'review_planner', label: 'shard' }] },
  { id: 'security_reviewer_1', label: 'Sec 1', shard: true },
  { id: 'security_reviewer_2', label: 'Sec 2', shard: true },
  { id: 'security_reviewer_3', label: 'Sec 3', shard: true },
  { id: 'security_reviewer_4', label: 'Sec 4', shard: true },
  { id: 'senior_security_agent', label: 'Senior', tools: [
    { id: 'review_digest', label: 'digest' }, { id: 'dependency_cve', label: 'cve' }, { id: 'contract_store', label: 'store' }] },
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
    // Shard reviewers are allocated dynamically: a reviewer the planner never invoked must stay
    // 'pending' (dim = not used) even after the run finishes, so status is per-node seen, not positional.
    const lastAgent = [...invoked].reverse().find(id => PIPELINE.some(s => s.id === id))
    const shardStatus = (id: string): Status =>
      seen.has(id) ? (!terminalDone && !failed && id === lastAgent ? 'active' : failed && id === lastAgent ? 'failed' : 'done') : 'pending'
    return { activeIdx, stageStatus, toolStatus, shardStatus, frontmanActive: !terminalDone && !failed && activeIdx >= 0 && activeIdx < PIPELINE.length }
  }, [events, state])
}

const NODE: Record<Status, string> = {
  pending: 'border-[var(--line)] text-[var(--ink-dim)] bg-[var(--panel)]',
  active: 'border-[var(--signal)] text-[var(--ink-hi)] bg-[var(--signal-dim)]',
  done: 'border-emerald-500/40 text-emerald-300 bg-emerald-500/5',
  failed: 'border-red-500/50 text-red-300 bg-red-500/10',
}

function Connector({ live, w = 28 }: { live: boolean; w?: number }) {
  return (
    <div className="relative mt-[17px] h-px shrink-0 self-start" style={{ width: w }}>
      <div className="h-px w-full" style={{ background: live ? 'var(--signal)' : 'var(--line)', boxShadow: live ? '0 0 6px var(--signal)' : undefined }} />
      {live && <span className="absolute -top-[3px] h-[7px] w-[7px] rounded-full bg-[var(--signal)]"
                     style={{ boxShadow: '0 0 8px var(--signal)', offsetPath: `path("M0,0 H${w}")`, animation: 'packet 1.1s linear infinite' }} />}
    </div>
  )
}

/* Fan block: the security_reviewer shards branch out of `Plan` and converge into `Senior`, so the
   parallel fan-out (B5/A8) reads as parallel, not a misleading sequential row. Geometry: rows are
   uniform h-9 (36px) with gap-2.5 (10px) → 46px pitch; the box center sits 17px down (matches
   Connector's mt-[17px]). A vertical bus on each side spans first→last box center, fed from `Plan`
   at the top and feeding `Senior` at the top; a horizontal stub bridges each bus to its node. */
const ROW_PITCH = 46 // h-9 (36) + gap-2.5 (10)
const BOX_MID = 17   // vertical center of the 36px box, matches Connector mt-[17px]
function bus(live: boolean, side: 'left' | 'right', height: number) {
  return <span className={`absolute ${side}-0 w-px`}
               style={{ top: BOX_MID, height, background: live ? 'var(--signal)' : 'var(--line)',
                        boxShadow: live ? '0 0 6px var(--signal)' : undefined }} />
}
function FanBlock({ shards, shardStatus, toolStatus, outLive, inLive }: {
  shards: Node[]; shardStatus: (id: string) => Status
  toolStatus: (id: string, active: boolean) => Status; outLive: boolean; inLive: boolean }) {
  const busH = Math.max(0, (shards.length - 1) * ROW_PITCH)
  const stub = (on: boolean) =>
    <div className="mt-[17px] h-px w-3 shrink-0"
         style={{ background: on ? 'var(--signal)' : 'var(--line)', boxShadow: on ? '0 0 4px var(--signal)' : undefined }} />
  return (
    <div className="flex items-start">
      <Connector live={outLive} />
      <div className="relative flex flex-col gap-2.5">
        {bus(outLive, 'left', busH)}
        {bus(inLive, 'right', busH)}
        {shards.map(s => {
          const st = shardStatus(s.id)
          return (
            <div key={s.id} className="flex items-start">
              {stub(st !== 'pending')}
              <NodeCard node={s} status={st} toolStatus={toolStatus} />
              {stub(st === 'done')}
            </div>
          )
        })}
      </div>
      <Connector live={inLive} />
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

export function AgentGraph({ events, state, decision, shardCount }:
  { events: RunEvent[]; state: RunState; decision?: Decision | null; shardCount?: number }) {
  const { activeIdx, stageStatus, toolStatus, shardStatus, frontmanActive } = useStatus(events, state)
  const decColor = decision === 'promote' ? '#34d399' : decision === 'escalate' ? '#f87171' : '#94a3b8'
  const live = state !== 'done' && state !== 'failed'
  const allShards = PIPELINE.filter(n => n.shard)
  // Show only the SEC agents this run actually allocated — not a fixed 4. review_planner sizes
  // 1-4 shards; showing phantom slots makes a 1-shard run look like "3 reviewers failed". Count =
  // review_plan.shards.length (authoritative, once persisted) else the number invoked so far (live,
  // grows as reviewers run), floored at 1.
  const seenShards = allShards.filter(s => shardStatus(s.id) !== 'pending').length
  const shownCount = Math.min(allShards.length, Math.max(1, shardCount ?? seenShards))
  const shards = allShards.slice(0, shownCount)
  const firstShardIdx = PIPELINE.findIndex(n => n.shard)
  const seniorIdx = PIPELINE.findIndex(n => n.id === 'senior_security_agent')
  const fanOutLive = live && shards.some(s => shardStatus(s.id) !== 'pending')
  const fanInLive = live && activeIdx >= seniorIdx
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
        {PIPELINE.map((node, i) => {
          if (node.shard) {
            // The whole shard group is drawn once, as a fan, at the first shard's slot.
            return i === firstShardIdx
              ? <FanBlock key="fan" shards={shards} shardStatus={shardStatus} toolStatus={toolStatus} outLive={fanOutLive} inLive={fanInLive} />
              : null
          }
          // Senior's inbound edge is the fan-in connector, so it gets no separate connector.
          return (
            <div key={node.id} className="flex items-start">
              {i === seniorIdx ? null : <Connector live={live && i === activeIdx} />}
              <NodeCard node={node} status={stageStatus(i)} toolStatus={toolStatus} />
            </div>
          )
        })}
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
