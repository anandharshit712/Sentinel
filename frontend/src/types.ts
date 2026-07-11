// Wire types — TS mirrors of the backend contracts (06 §7, 04 §4).
export type Band = 'low' | 'medium' | 'high' | 'critical'
export type Decision = 'promote' | 'hold' | 'escalate'
export type Severity = 'critical' | 'high' | 'medium' | 'low'
export type RunState =
  | 'received' | 'analyzing' | 'reviewing' | 'testing' | 'scoring' | 'gated' | 'done' | 'failed'

export interface RunRow {
  run_id: string
  repo: string
  from_env: string
  to_env: string
  state: RunState
  created_at?: string
  finished_at?: string | null
  score?: number | null
  band?: Band | null
  decision?: Decision | null
  approval_required?: boolean | null
}

export interface Finding {
  id: string
  kind?: 'security' | 'quality'
  category?: string
  severity: Severity
  file?: string
  line_start?: number
  line_end?: number
  cwe?: string
  title: string
  explanation?: string
  fix_suggestion?: string
  source?: 'tool' | 'llm'
}

export interface ReviewReport {
  executive_summary?: string
  findings?: Finding[]
  counts?: Record<Severity, number>
  pr_health_score?: number
  recommendation?: string
}

export interface RiskScore {
  score: number
  band: Band
  formula_version?: string
  contributions?: { factor: string; points: number; evidence_ref?: string }[]
  llm_escalation?: { points_added: number; justification: string }
  explanation?: string
}

export interface TestResults {
  runner?: string
  command?: string
  totals?: { passed: number; failed: number; skipped: number; errors?: number }
  cases?: { test_id: string; status: string; duration_ms?: number; failure_message?: string }[]
  timed_out?: boolean
  duration_seconds?: number
  suite_total?: number          // total tests pytest would collect for the whole suite
  executed?: number             // tests actually run (the selected subset)
  excluded?: number             // suite_total - executed (tests skipped by selection)
  selection_mode?: 'subset' | 'full_suite_fallback'
  selected_ids?: string[]
  stage_failure?: string
}

export interface TestPlan {
  selected?: { test_id: string; reason?: string; mapping_source?: string }[]
  smoke_set?: string[]
  excluded_summary?: string
  selection_confidence?: 'high' | 'medium' | 'low'
  estimated_runtime_seconds?: number
}

export interface DecisionContract {
  run_id?: string
  decision: Decision
  policy_version?: string
  rule_fired?: string
  reasoning_trail?: {
    review?: string; testing?: string; results?: string; context?: string; policy?: string
  } | Record<string, string>
  approval_required?: boolean
  actions_taken?: { action: string; detail: string; at?: string }[]
}

export interface RunDetail {
  run: RunRow & { event?: any }
  review_report?: ReviewReport | null
  test_plan?: TestPlan | null
  test_results?: TestResults | null
  env_context?: any
  risk_score?: RiskScore | null
  review_plan?: { shards?: any[]; metrics?: any } | null   // audit fan-out sizing (shards.length = # SEC agents)
  decision?: (DecisionContract & { reasoning_trail?: any }) | null
  error?: string | null          // run_failed reason (present when state === 'failed')
}

export interface RunEvent {
  seq: number
  run_id: string
  ts: string
  kind: 'stage_started' | 'stage_done' | 'agent_message' | 'state_change'
  state?: RunState
  stage?: string
  text?: string
  invoked?: string
  decision?: Decision
}

export interface Approval {
  id: number
  run_id: string
  status: string
  approver?: string
  comment?: string
  created_at?: string
  resolved_at?: string
}

export interface AuditEvent {
  id: number
  run_id?: string
  actor: string
  action: string
  payload?: any
  at?: string
}
