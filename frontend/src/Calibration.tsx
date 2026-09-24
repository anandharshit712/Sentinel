import { Card, useCalibration } from './lib'

/* Calibration screen (10 §3 P4.5) — was the gate right?

   Two rules this screen exists to honour:
   - A rate computed from a handful of runs is not a rate. Cells below the sample floor show their
     count and no percentage, rather than a confident-looking number.
   - Nothing here has changed anything. The recommendations are arguments with their evidence
     attached, for a person to weigh and apply by hand. */

type Cell = { n: number; rate: number | null; insufficient_data: boolean }

function Rate({ c }: { c?: Cell }) {
  if (!c) return <span className="text-(--ink-dim)">—</span>
  if (c.insufficient_data || c.rate == null)
    return <span className="text-(--ink-dim)" title={`only ${c.n} observation(s)`}>
      not enough data <span className="tabular-nums">(n={c.n})</span></span>
  return <span><span className="font-bold tabular-nums text-(--ink-hi)">{Math.round(c.rate * 100)}%</span>
    <span className="ml-1 text-(--ink-dim) tabular-nums">(n={c.n})</span></span>
}

export function Calibration() {
  const { data, loading, error } = useCalibration()
  if (loading && !data) return <div className="p-6 text-(--ink-dim)">Loading calibration…</div>
  if (error) return <div className="p-6 text-red-400">Error: {error}</div>
  if (!data) return null

  const t = data.totals || {}
  const repos: Record<string, number> = data.repos || {}
  const recs: any[] = data.recommendations || []
  const promo: Record<string, any> = data.promotion_by_band || {}
  const esc: Record<string, any> = data.escalation_by_band || {}
  const tests: Record<string, any> = data.generated_tests || {}

  // A fresh install has no history. Say what to do about it rather than rendering empty tables.
  if (!t.decisions) {
    return (
      <Card title="Calibration">
        <p className="text-sm text-(--ink-dim)">No decisions recorded yet.</p>
        <p className="mt-2 text-[11px] text-(--ink-dim)">
          Calibration needs history: run the pipeline, then record what happened afterwards
          (<code>POST /api/v1/runs/&lt;id&gt;/outcome</code>). Until a band has at least
          {' '}{data.min_sample} observations, this screen will not show a rate for it.
        </p>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card title="Calibration" right={
        <span className="text-[10px] uppercase tracking-widest text-(--ink-dim)">
          {t.decisions} decisions · {t.outcomes_recorded} outcomes recorded
        </span>}>
        <p className="text-[11px] text-(--ink-dim)">
          Every other screen judges a change on what was known at the time. This one asks whether
          those judgements held up. Nothing here changes a threshold — the gate only changes when a
          person edits <code>config/risk.yaml</code>.
        </p>
        {Object.keys(repos).length > 0 && (
          <p className="mt-2 text-[11px] text-(--ink-dim)">
            evidence from: {Object.entries(repos).slice(0, 8)
              .map(([r, n]) => `${r} (${n})`).join(' · ')}
          </p>
        )}
      </Card>

      <Card title="Promotions" right={<span className="text-[10px] uppercase tracking-widest text-(--ink-dim)">how often a promotion went wrong</span>}>
        {Object.keys(promo).length === 0 && <p className="text-sm text-(--ink-dim)">No promotions yet.</p>}
        <div className="space-y-1.5 text-[11px]">
          {Object.entries(promo).map(([band, c]: [string, any]) => (
            <div key={band} className="flex flex-wrap items-center gap-3">
              <span className="w-20 uppercase tracking-wider text-(--signal)">{band}</span>
              <Rate c={c.bad_rate} />
              <span className="text-(--ink-dim)">
                {c.promoted} promoted · {c.bad} went bad · {c.unknown} with no recorded outcome
              </span>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Escalations" right={<span className="text-[10px] uppercase tracking-widest text-(--ink-dim)">did the human agree?</span>}>
        {Object.keys(esc).length === 0 && <p className="text-sm text-(--ink-dim)">Nothing gated yet.</p>}
        <div className="space-y-1.5 text-[11px]">
          {Object.entries(esc).map(([band, c]: [string, any]) => (
            <div key={band} className="flex flex-wrap items-center gap-3">
              <span className="w-20 uppercase tracking-wider text-(--signal)">{band}</span>
              <span className="text-(--ink-dim)">approved</span>
              <Rate c={c.approval_rate} />
              <span className="text-(--ink-dim)">
                {c.gated} gated · {c.approved} approved · {c.rejected} rejected · {c.unresolved} still open
              </span>
            </div>
          ))}
        </div>
      </Card>

      {Object.keys(tests).length > 0 && (
        <Card title="Generated Tests" right={<span className="text-[10px] uppercase tracking-widest text-(--ink-dim)">are accepted ones kept?</span>}>
          <div className="space-y-1.5 text-[11px]">
            {Object.entries(tests).map(([verdict, c]: [string, any]) => (
              <div key={verdict} className="flex flex-wrap items-center gap-3">
                <span className="w-20 uppercase tracking-wider text-(--signal)">{verdict}</span>
                <span className="text-(--ink-dim)">adopted</span>
                <Rate c={c.adoption_rate} />
                <span className="text-(--ink-dim)">
                  {c.proposed} proposed · {c.caught_bug} later caught a real bug
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title="Recommendations" right={
        <span className="rounded-sm border border-(--line) px-2 py-0.5 text-[10px] uppercase tracking-wider text-(--ink-dim)">
          nothing applied
        </span>}>
        {recs.length === 0 && (
          <p className="text-sm text-(--ink-dim)">
            None. Either the gate looks calibrated, or there is not enough evidence to say.
          </p>
        )}
        <div className="space-y-3">
          {recs.map((r, i) => (
            <div key={i} className="rounded-sm border border-(--line) p-2">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className={`rounded-sm border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
                  r.kind === 'possibly_too_loose' ? 'border-amber-500/40 text-amber-300'
                  : 'border-slate-500/40 text-slate-300'}`}>{r.kind.replace(/_/g, ' ')}</span>
                <span className="uppercase tracking-wider text-(--signal)">{r.band}</span>
                <span className="text-(--ink-dim)">{r.confidence} confidence</span>
              </div>
              <p className="mt-1.5 text-[11px] text-(--ink)"><span className="text-(--ink-dim)">saw: </span>{r.evidence}</p>
              <p className="mt-1 text-[11px] text-(--ink)"><span className="text-(--ink-dim)">suggest: </span>{r.suggestion}</p>
              {/* The caveat travels with the claim so the number cannot be quoted without it. */}
              <p className="mt-1 text-[11px] text-amber-300/80"><span className="text-(--ink-dim)">caveat: </span>{r.caveat}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
