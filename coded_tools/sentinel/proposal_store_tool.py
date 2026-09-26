"""proposal_store_tool (09 §4 P3.8) — persist a generated test and the evidence for its verdict.

The end of the generation loop. It re-runs the evaluation itself rather than trusting whatever the
agent reports: the mutation score is the claim this whole phase rests on, and an agent that
summarised its own result — accurately or not — would make the record unfalsifiable. The evidence
stored is the evidence measured.

The stored row is a **proposal**. It is never written into the repository, never committed, and
nothing in the promotion chain reads it. Adoption is a human action (09 §2).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.proposal_store")


class ProposalStoreTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id")
        try:
            target_file = args.get("target_file")
            function = args.get("function")
            test_source = args.get("test_source")
            test_path = args.get("test_path") or "tests/test_sentinel_generated.py"
            if not (run_id and target_file and function and test_source):
                return "Error: run_id, target_file, function and test_source are required"

            # Measure here, don't take the agent's word for it.
            from coded_tools.sentinel.test_evaluator_tool import TestEvaluatorTool
            evaluation = TestEvaluatorTool().invoke(
                {"target_file": target_file, "function": function, "test_source": test_source,
                 "test_path": test_path,
                 "repo_workspace": args.get("repo_workspace") or sly_data.get("repo_workspace")},
                sly_data)
            if isinstance(evaluation, str):
                return f"Error: evaluation failed: {evaluation}"

            # Tier 2's verdict, from the agent that never saw the function body. A dispute
            # outranks any mutation score: a test written against code that contradicts its own
            # documentation asserts the code's side of that contradiction (11 §6).
            # The TOOL's own record wins. An agent that reports "confirms_intent" without the
            # oracle ever having run a comparison is asserting evidence it does not have — which
            # is what happened on every live run until 2026-09-26.
            measured = sly_data.get("intent_result") or {}
            claimed = (args.get("intent_verdict") or "").strip().lower()
            intent_verdict = (measured.get("verdict") or "").strip().lower()
            if not intent_verdict and claimed:
                # Unmeasured: keep the claim visible but never let it drive a classification.
                evaluation["intent_claimed_unverified"] = claimed
                logger.warning("run %s: agent claimed intent verdict %r but the oracle never "
                               "measured one — ignoring it", run_id, claimed)
            if intent_verdict and claimed and intent_verdict != claimed:
                evaluation["intent_claim_mismatch"] = {"claimed": claimed, "measured": intent_verdict}
                logger.warning("run %s: agent claimed %r, the oracle measured %r — using the "
                               "measurement", run_id, claimed, intent_verdict)
            if intent_verdict:
                evaluation["intent_verdict"] = intent_verdict
                evaluation.setdefault("oracle_evidence", {})["intent"] = {
                    "tier": "blind_oracle", "verdict": intent_verdict, "measured": True,
                    "cases_checked": measured.get("checked", 0),
                    "witness": "the documentation, read without sight of the implementation"}
                if intent_verdict in ("disputed", "confirms_intent"):
                    from coded_tools.sentinel.test_evaluator_tool import classify
                    cls, why = classify(
                        evaluation.get("verdict") == "accepted",
                        (evaluation.get("oracle_evidence") or {}).get("differential"),
                        ((evaluation.get("oracle_evidence") or {}).get("specification") or {})
                        .get("mismatches"),
                        intent_verdict)
                    evaluation["classification"] = cls
                    evaluation["classification_reason"] = why

            proposal_id = None
            persisted, persist_error = True, None
            try:
                from db import dao
                proposal_id = dao.insert_test_proposal(
                    str(run_id), target_file=target_file, target_function=function,
                    test_path=test_path, test_source=test_source,
                    verdict=evaluation.get("verdict", "inconclusive"),
                    mutation_score=evaluation.get("mutation_score"),
                    evaluation=evaluation)
            except Exception as e:
                # Same rule as every other tool here: a DB failure must not blank the result and
                # let an LLM invent numbers downstream — report it and return the real evidence.
                persisted, persist_error = False, str(e)[:300]

            stored = sly_data.setdefault("test_proposals", [])
            stored.append({"id": proposal_id, "target_file": target_file, "function": function,
                           "test_path": test_path, "verdict": evaluation.get("verdict"),
                           "mutation_score": evaluation.get("mutation_score")})

            logger.info("run %s: proposal_store %s::%s -> %s (score %s, id %s%s)",
                        run_id, target_file, function, evaluation.get("verdict"),
                        evaluation.get("mutation_score"), proposal_id,
                        "" if persisted else ", NOT persisted")
            out = {"proposal_id": proposal_id, "verdict": evaluation.get("verdict"),
                   "mutation_score": evaluation.get("mutation_score"),
                   "reason": evaluation.get("reason"),
                   "caught": len(evaluation.get("caught") or []),
                   "missed": evaluation.get("missed") or [],
                   "mutants_total": evaluation.get("mutants_total"),
                   "status": "proposed", "persisted": persisted}
            if persist_error:
                out["persist_error"] = persist_error
            return out
        except Exception as e:
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return self.invoke(args, sly_data)


def demo() -> None:
    """No database needed: a persistence failure must still return the measured evidence."""
    import os
    import tempfile

    ws = tempfile.mkdtemp(prefix="proposal-")
    with open(os.path.join(ws, "person.py"), "w", encoding="utf-8") as fh:
        fh.write("def is_adult(age):\n    return age >= 18\n")

    good = ("from person import is_adult\n\n\n"
            "def test_boundary():\n"
            "    assert is_adult(18) is True\n"
            "    assert is_adult(17) is False\n")
    out = ProposalStoreTool().invoke(
        {"target_file": "person.py", "function": "is_adult", "test_source": good,
         "repo_workspace": ws},
        {"run_id": "not-a-uuid"})          # forces the DB insert to fail
    assert out["verdict"] == "accepted", out
    assert out["mutation_score"] >= 0.5, out
    assert out["persisted"] is False and out["persist_error"], out
    assert out["status"] == "proposed", "adoption is a human action, never the tool's"
    print("proposal_store_tool OK")


if __name__ == "__main__":
    demo()
