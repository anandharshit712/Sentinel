"""contract_store_tool (04 §5.17, A7) — generic writer for LLM-produced contracts.

sly_data is writable only by coded tools (01 §5.4), yet the security/quality/test-selection/
environment agents each produce a contract. This tool is their mandatory final step: it stamps the
envelope, JSON-schema-validates the payload against the named contract, and writes it to sly_data.
The contract_name enum is restricted so it can never overwrite a tool-owned contract
(change_profile, review_plan, review_report, risk_score, decision).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts

logger = logging.getLogger("coded_tools.contract_store")

_ALLOWED = {
    "security_findings", "quality_findings", "test_plan", "env_context",
    # adaptive security fan-out: per-shard reviewer findings + senior narrative
    "security_findings_shard_1", "security_findings_shard_2",
    "security_findings_shard_3", "security_findings_shard_4", "senior_summary",
    # Phase 2 review cluster (08). Every name an agent is INSTRUCTED to write must be here: a
    # missing name is not a loud failure, it is an agent retrying the same rejected call dozens of
    # times and then giving up silently. tests/coded_tools/test_p2.py guards this against the HOCON.
    "performance_findings", "compliance_findings",
}


class ContractStoreTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            name = args.get("contract_name")
            if name not in _ALLOWED:
                # Logged, not just returned: a rejected write is invisible otherwise — the agent
                # simply retries until it gives up, the floor covers for it, and the run still
                # looks green. That cost a full P2 trial round to find (08 §6).
                logger.warning("run %s: contract_store REJECTED unknown contract %r (allowed: %s)",
                               run_id, name, sorted(_ALLOWED))
                return f"Error: contract_name must be one of {sorted(_ALLOWED)}, got {name!r}"
            payload = args.get("payload")
            if isinstance(payload, str):
                # Models routinely send the payload as a JSON *string* rather than an object, and
                # the schema declares an object, so this used to be a hard reject. The agent then
                # retried the identical call dozens of times and gave up silently — measured at 89
                # contract_store calls for 1 successful write in a single run, with the whole LLM
                # review layer lost behind the deterministic floor. Parse it: lenient at the door,
                # strict at validation below.
                try:
                    payload = json.loads(payload)
                except ValueError as e:
                    logger.warning("run %s: contract_store REJECTED %s — payload is a string that "
                                   "is not JSON: %s", run_id, name, e)
                    return ("Error: payload must be an object (or a JSON string encoding one); "
                            f"could not parse it: {e}")
            if not isinstance(payload, dict):
                logger.warning("run %s: contract_store REJECTED %s — payload is %s, not an object",
                               run_id, name, type(payload).__name__)
                return "Error: payload must be an object"
            wrapped = contracts.wrap(payload, run_id=str(run_id), produced_by=f"agent:{name}")
            contracts.validate(name, wrapped)  # raises on schema mismatch -> Error below
            sly_data[name] = wrapped
            logger.info("run %s: contract_store wrote %s", run_id, name)
            return {"stored": name}
        except Exception as e:
            logger.warning("run %s: contract_store REJECTED %s — %s",
                           run_id, args.get("contract_name"), e)
            return f"Error: schema {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
