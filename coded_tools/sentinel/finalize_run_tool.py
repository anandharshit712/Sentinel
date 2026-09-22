"""finalize_run_tool (08 §6) — the deterministic tail of the pipeline, in one call.

Environment context → risk score → trust ladder → decision → CI/CD action or notification used to
be three LLM agents (`environment_context_agent`, `risk_scoring_agent`, `promotion_gating_agent`).
Read their instructions and none of them decided anything: each called coded tools in a fixed order
and copied the results forward. What they did add was three more turns on the frontman's chain, and
on a long chain the frontman drifts — measured at a **1/3 decision rate** once the Phase 2 review
agents started returning real findings and the conversation grew (08 §6). A run that reviews the
code, runs the tests, and then never reaches a verdict is worth nothing.

So the tail is collapsed into this one coded tool. It composes the existing tools in process —
nothing is reimplemented — and every step stays exactly as deterministic as it was (rule 4: LLM
reasons, code decides).

**What this gives up:** `risk_scoring_agent` could add a raise-only `llm_escalation` to the score.
That permission is deferred, not replaced — the risk score is now purely the `risk-v1` formula over
the stored contracts. It is the safer direction (code decides), and the reviewers' findings still
drive the score through the review report. Restoring it means passing an escalation argument in,
not bringing the agent back.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts

logger = logging.getLogger("coded_tools.finalize_run")


def _err(step: str, res: Any) -> str | None:
    """Coded tools report failure as an 'Error: ...' string rather than raising."""
    return f"{step}: {res}" if isinstance(res, str) and res.startswith("Error") else None


class FinalizeRunTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        # Imported here so a single broken tail tool cannot stop the whole network loading.
        from coded_tools.sentinel.cicd_action_tool import CicdActionTool
        from coded_tools.sentinel.decision_logger_tool import DecisionLoggerTool
        from coded_tools.sentinel.deploy_window_tool import DeployWindowTool
        from coded_tools.sentinel.incident_history_tool import IncidentHistoryTool
        from coded_tools.sentinel.notification_tool import NotificationTool
        from coded_tools.sentinel.risk_calculator_tool import RiskCalculatorTool
        from coded_tools.sentinel.trust_ladder_tool import TrustLadderTool

        try:
            # ---- 1. environment context -------------------------------------------------
            inc = IncidentHistoryTool().invoke({}, sly_data)
            if (e := _err("incident_history", inc)):
                return f"Error: {e}"
            win = DeployWindowTool().invoke({}, sly_data)
            if (e := _err("deploy_window", win)):
                return f"Error: {e}"

            env = {
                "target_env": inc["target_env"],
                "incidents": {"count_7d": inc["count_7d"], "count_30d": inc["count_30d"]},
                "deploy_window": {"risky": bool(win["risky"]), "reason": win.get("reason", "")},
                # A richer stability signal needs a metrics source this project does not have; the
                # incident counts already feed the score, so this stays honest rather than invented.
                "env_stability": "stable",
                "flags": [],
                "summary": (f"{inc['count_7d']} incident(s) in 7d, {inc['count_30d']} in 30d for "
                            f"{inc['target_env']}; deploy window "
                            f"{'risky' if win['risky'] else 'normal'}."),
            }
            if inc.get("most_recent_at"):
                env["incidents"]["most_recent_at"] = str(inc["most_recent_at"])
            wrapped = contracts.wrap(env, run_id=str(run_id), produced_by="finalize_run")
            contracts.validate("env_context", wrapped)
            sly_data["env_context"] = wrapped

            # ---- 1b. coverage gaps (09 §4 P3.3) -----------------------------------------
            # Runs here rather than as its own chain step: it needs test_results, it is pure
            # arithmetic, and the promotion chain is the one thing that must always finish.
            # Advisory only — nothing below reads it, and an unmeasured run is not a clean one.
            from coded_tools.sentinel.coverage_gap_tool import CoverageGapTool
            gaps = CoverageGapTool().invoke({}, sly_data)
            if isinstance(gaps, dict):
                sly_data["coverage_gaps"] = contracts.wrap(
                    gaps, run_id=str(run_id), produced_by="coverage_gap")

            # ---- 2. risk score (risk-v1, deterministic) ---------------------------------
            risk = RiskCalculatorTool().invoke({}, sly_data)
            if (e := _err("risk_calculator", risk)):
                return f"Error: {e}"

            # ---- 3. trust ladder --------------------------------------------------------
            verdict = TrustLadderTool().invoke({}, sly_data)
            if (e := _err("trust_ladder", verdict)):
                return f"Error: {e}"
            decision = verdict["decision"]

            # ---- 4. decision record -----------------------------------------------------
            logged = DecisionLoggerTool().invoke({}, sly_data)
            if (e := _err("decision_logger", logged)):
                return f"Error: {e}"

            # ---- 5. act on it -----------------------------------------------------------
            if decision == "promote":
                action = CicdActionTool().invoke({"action": "promote"}, sly_data)
            else:
                action = NotificationTool().invoke(
                    {"kind": decision,
                     "summary": f"{decision}: risk {risk.get('score')} ({risk.get('band')}) "
                                f"via {verdict.get('rule_fired')}"}, sly_data)

            logger.info("run %s: finalize_run -> %s (risk %s/%s, rule %s)",
                        run_id, decision, risk.get("score"), risk.get("band"),
                        verdict.get("rule_fired"))
            return {"decision": decision, "score": risk.get("score"), "band": risk.get("band"),
                    "rule_fired": verdict.get("rule_fired"),
                    "env_summary": env["summary"],
                    "action": action if isinstance(action, dict) else str(action)}
        except Exception as e:
            logger.warning("run %s: finalize_run failed — %s", run_id, e)
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
