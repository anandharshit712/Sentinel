"""risk_calculator_tool (04 §5.12) — deterministic, versioned risk score.

Core principle "LLM reasons, code decides": the score is computed here, in code, from the
authoritative contracts already in sly_data (NOT the LLM-passed `risk_input` — anti-tamper).
The LLM may only RAISE risk via `llm_escalation.points_added` (clamped ≥ 0); it can never lower it.
Every contribution is line-itemed. Writes the validated `risk_score` contract to sly_data.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts

logger = logging.getLogger("coded_tools.risk_calculator")


class RiskCalculatorTool(CodedTool):
    def __init__(self, weights_path: str = "config/risk_weights_v1.yaml"):
        self.weights_path = weights_path

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            with open(self.weights_path, encoding="utf-8") as fh:
                w = yaml.safe_load(fh)

            contribs: list[dict] = []

            def add(factor: str, pts: float, cap_applied: bool = False, ev: str = "") -> None:
                if pts > 0:
                    contribs.append({"factor": factor, "points": float(pts),
                                     "cap_applied": cap_applied, "evidence_ref": ev})

            # --- security: from review_report.counts (deduped, authoritative) ---
            rr = sly_data.get("review_report") or {}
            counts = rr.get("counts") or {}
            for sev in ("critical", "high", "medium", "low"):
                n = counts.get(sev, 0)
                if n:
                    c = w["security"][sev]
                    raw = n * c["points"]
                    add(f"security.{sev}", min(raw, c["cap"]), cap_applied=raw > c["cap"], ev=f"{n} {sev}")

            # --- quality: (100 - pr_health_score) * factor, capped ---
            health = rr.get("pr_health_score")
            if health is not None:
                q = (100 - health) * w["quality"]["health_factor"]
                add("quality.health", round(min(q, w["quality"]["cap"]), 2),
                    cap_applied=q > w["quality"]["cap"], ev=f"health={health}")

            # --- tests ---
            tr = sly_data.get("test_results") or {}
            tp = sly_data.get("test_plan") or {}
            tc = w["tests"]
            if tr.get("stage_failure") or tr.get("timed_out"):
                add("tests.stage_failure", tc["stage_failure"], ev="tests could not run")
            else:
                totals = tr.get("totals") or {}
                failed_ids = [c.get("test_id", "") for c in (tr.get("cases") or [])
                              if c.get("status") in ("failed", "error")]
                if totals.get("failed", 0) > 0:
                    suites = {tid.split("::")[0].split("#")[0] for tid in failed_ids} or {"?"}
                    raw = tc["selected_failure_base"] + (len(suites) - 1) * tc["selected_failure_additional_suite"]
                    add("tests.selected_failure", min(raw, tc["selected_failure_cap"]),
                        cap_applied=raw > tc["selected_failure_cap"],
                        ev=f"{totals['failed']} failed / {len(suites)} suite(s)")
                    smoke = set(tp.get("smoke_set") or [])
                    if smoke and any(tid in smoke for tid in failed_ids):
                        add("tests.smoke_failure", tc["smoke_failure"], ev="smoke test failed")
                if tp.get("selection_confidence") == "low":
                    add("tests.low_confidence", tc["low_confidence"], ev="low selection confidence")

            # --- change profile ---
            cp = sly_data.get("change_profile") or {}
            cc = w["change"]
            flags = cp.get("sensitive_flags") or []
            if flags:
                raw = len(flags) * cc["sensitive_flag"]["points"]
                add("change.sensitive_flag", min(raw, cc["sensitive_flag"]["cap"]),
                    cap_applied=raw > cc["sensitive_flag"]["cap"],
                    ev=",".join(f.get("flag", "") for f in flags))
            br = (cp.get("blast_radius") or {}).get("count", 0)
            if br > 50:
                add("change.blast_radius", cc["blast_radius"]["gt_50"], ev=f"blast={br}")
            elif br > 20:
                add("change.blast_radius", cc["blast_radius"]["gt_20"], ev=f"blast={br}")
            loc = cp.get("loc_added", 0) + cp.get("loc_removed", 0)
            if loc > 2000:
                add("change.change_size", cc["change_size"]["gt_2000_loc"], ev=f"loc={loc}")
            elif loc > 500:
                add("change.change_size", cc["change_size"]["gt_500_loc"], ev=f"loc={loc}")

            # --- environment context ---
            ec = sly_data.get("env_context") or {}
            envc = w["env"]
            if (ec.get("incidents") or {}).get("count_7d", 0) > 0:
                add("env.incident_recent", envc["incident_recent"], ev="incident in 7d")
            if (ec.get("deploy_window") or {}).get("risky"):
                add("env.deploy_window", envc["deploy_window"], ev="risky deploy window")
            if ec.get("env_stability") == "unstable":
                add("env.env_unstable", envc["env_unstable"], ev="target env unstable")
            if "oversized_batch" in (ec.get("flags") or []):
                add("env.oversized_batch", envc["oversized_batch"], ev="oversized batch")

            # --- LLM escalation: raise-only, clamp ≥ 0 (enforced here, not by prompt) ---
            esc = args.get("llm_escalation") or {}
            raw_pts = esc.get("points_added", 0)
            pts = max(0, int(raw_pts)) if isinstance(raw_pts, (int, float)) else 0
            justification = esc.get("justification", "")

            total = sum(c["points"] for c in contribs) + pts
            score = int(min(w["score_cap"], round(total)))
            band = self._band(score, w["bands"])

            explanation = "; ".join(
                f"{c['factor']} +{c['points']:g}" + (f" ({c['evidence_ref']})" if c["evidence_ref"] else "")
                for c in contribs
            )
            if pts > 0:
                explanation += f"; llm.escalation +{pts}" + (f" ({justification})" if justification else "")

            payload = {
                "score": score,
                "band": band,
                "formula_version": w["formula_version"],
                "contributions": contribs,
                "llm_escalation": {"points_added": pts, "justification": justification},
                "explanation": explanation or "no risk factors identified",
            }
            wrapped = contracts.wrap(payload, run_id=str(run_id), produced_by="risk_calculator")
            contracts.validate("risk_score", wrapped)  # never emit an invalid contract
            sly_data["risk_score"] = wrapped
            logger.info("run %s: risk score=%s band=%s (%d factors)", run_id, score, band, len(contribs))
            return payload
        except Exception as e:  # never raise through the framework
            return f"Error: {e}"

    @staticmethod
    def _band(score: int, bands: dict) -> str:
        for name, (lo, hi) in bands.items():
            if lo <= score <= hi:
                return name
        return "critical"  # score_cap guarantees ≤100, so this is unreachable defensively

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
