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

export type Dimension = 'security' | 'quality' | 'performance' | 'compliance' | 'spec'
export type Verdict = 'real' | 'false_positive' | 'wont_fix'

export interface Finding {
  id: string
  kind?: Dimension
  dimension?: Dimension
  fingerprint?: string
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
  coverage_gaps?: CoverageGaps | null
  test_proposals?: TestProposal[]
  findings?: FindingRow[]        // one DB row per report finding, carrying the human verdict
  error?: string | null          // run_failed reason (present when state === 'failed')
}

export interface FindingRow {
  id: number                     // row id — what the verdict endpoint takes
  finding_id: string             // matches Finding.id in the report
  kind: Dimension
  source?: 'tool' | 'llm'
  verdict?: Verdict | null
  verdict_by?: string | null
  note?: string | null
  previous?: { verdict: Verdict; verdict_at?: string; run_id: string } | null
}

export interface CoverageGap {
  file: string; function: string; status: 'uncovered' | 'partial' | 'covered'
  line_start?: number; line_end?: number; uncovered_lines?: number[]
  covered_lines?: number; total_lines?: number; is_new?: boolean; sensitive?: boolean
  priority: number
}

export interface CoverageGaps {
  gaps: CoverageGap[]
  measured: boolean              // false = no coverage data; NOT the same as "all covered"
  reason?: string
  totals?: { functions_changed?: number; uncovered?: number; partial?: number; covered?: number
             changed_lines_measured?: number; changed_lines_covered?: number }
}

export interface TestProposal {
  id: number
  target_file: string
  target_function: string
  test_path: string
  test_source: string
  verdict: 'accepted' | 'rejected' | 'inconclusive'
  mutation_score: number | null
  status: 'proposed' | 'adopted' | 'discarded'
  evaluation: {
    reason?: string
    // What KIND of test this is, and which witness vouched for it (11 §6). A bare "accepted"
    // used to read as "correct" when it only meant "agrees with the implementation".
    classification?: 'regression_guard' | 'change_documented' | 'characterization' | 'disputed' | 'rejected'
    classification_reason?: string
    oracle_evidence?: {
      mutation?: { score?: number; caught?: number; total?: number; witness?: string }
      differential?: { result?: string; reason?: string; base_passed?: boolean | null }
      specification?: { mismatches?: { evidence?: string; caveat?: string }[] }
    }
    caught?: { id: string; operator: string; description: string }[]
    missed?: { id: string; operator: string; description: string }[]
    mutants_total?: number
    threshold?: number
  }
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

// ---------------------------------------------------------------- accounts / tenancy (Phase 4)
export interface Me {
  role: string
  auth_mode: 'oidc'
  account: { id: string; email?: string; display_name?: string; is_admin: boolean; retention_days?: number }
  links: Record<string, { username: string; linked_at?: string }>
  link_errors: Record<string, string>
  link_required: boolean
}

export interface RepoKey { id: number; prefix: string; created_at?: string; revoked_at?: string | null }

export interface Repo {
  id: string
  host: string
  full_name: string
  clone_url: string
  status: 'pending' | 'active' | 'suspended'
  verified_via?: string | null
  verify_token?: string
  created_at?: string
  keys: RepoKey[]
}

export interface LlmKey {
  id: number; repo_id?: string | null; provider: string; hint: string
  validated_at?: string; created_at?: string
}

export interface AccountRow {
  id: string; email?: string; display_name?: string; status: string; is_admin: boolean
  created_at?: string; runs_24h: number; retention_days?: number
}
