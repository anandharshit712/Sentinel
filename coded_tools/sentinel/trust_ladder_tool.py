"""trust_ladder_tool (04 §5.13) — deterministic promotion decision from risk band.

LLM reasons, code decides: this tool maps (transition, band) → {promote, hold, escalate}
via policy config, with two non-negotiable, in-code guarantees the policy cannot loosen:
  1. any transition to `production` ⇒ escalate (hard floor)
  2. unknown transition ⇒ escalate (fail-closed)
Reads `risk_score` + `event` from sly_data. Returns {decision, rule_fired, policy_version}.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Union

import yaml

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger("coded_tools.trust_ladder")

_STRICTNESS = {"promote": 0, "hold": 1, "escalate": 2}


class TrustLadderTool(CodedTool):
    def __init__(self, policy_path: str = "config/trust_ladder_policy.yaml"):
        self.policy_path = policy_path

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            risk = sly_data.get("risk_score")
            event = sly_data.get("event")
            if not risk or not event:
                return "Error: trust_ladder needs risk_score and event in sly_data"

            band = risk["band"]
            score = risk["score"]
            tr = event["target_transition"]
            frm, to = tr["from_env"], tr["to_env"]

            with open(self.policy_path, encoding="utf-8") as fh:
                policy = yaml.safe_load(fh)
            policy_version = policy["policy_version"]
            default = policy.get("default", "escalate")
            key = f"{frm}->{to}"
            trans = policy.get("transitions", {}).get(key)

            if trans is None:
                decision, rule = default, f"{key}/unknown->default({default})"  # fail-closed
            else:
                decision = trans.get(band, default)
                rule = f"{key}/{band}"
                # sub-band escalation threshold (e.g. dev->test critical, score>=90)
                eas = trans.get("escalate_at_score")
                if eas is not None and score >= eas and decision != "escalate":
                    decision, rule = "escalate", f"{key}/{band}>=score{eas}"

            # HARD FLOOR: production can only ever escalate — policy cannot override
            if to == "production" and decision != "escalate":
                decision, rule = "escalate", f"{key}/production-floor"

            logger.info("run %s: %s band=%s score=%s -> %s (%s)", run_id, key, band, score, decision, rule)
            return {"decision": decision, "rule_fired": rule, "policy_version": policy_version}
        except Exception as e:  # never raise through the framework
            return f"Error: {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
