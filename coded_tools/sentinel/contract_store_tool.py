"""contract_store_tool (04 §5.17, A7) — generic writer for LLM-produced contracts.

sly_data is writable only by coded tools (01 §5.4), yet the security/quality/test-selection/
environment agents each produce a contract. This tool is their mandatory final step: it stamps the
envelope, JSON-schema-validates the payload against the named contract, and writes it to sly_data.
The contract_name enum is restricted so it can never overwrite a tool-owned contract
(change_profile, review_report, risk_score, decision).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Union

from neuro_san.interfaces.coded_tool import CodedTool
from lib import contracts

logger = logging.getLogger("coded_tools.contract_store")

_ALLOWED = {"security_findings", "quality_findings", "test_plan", "env_context"}


class ContractStoreTool(CodedTool):
    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        run_id = sly_data.get("run_id", "?")
        try:
            name = args.get("contract_name")
            if name not in _ALLOWED:
                return f"Error: contract_name must be one of {sorted(_ALLOWED)}, got {name!r}"
            payload = args.get("payload")
            if not isinstance(payload, dict):
                return "Error: payload must be an object"
            wrapped = contracts.wrap(payload, run_id=str(run_id), produced_by=f"agent:{name}")
            contracts.validate(name, wrapped)  # raises on schema mismatch -> Error below
            sly_data[name] = wrapped
            logger.info("run %s: contract_store wrote %s", run_id, name)
            return {"stored": name}
        except Exception as e:
            return f"Error: schema {e}"

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        return await asyncio.to_thread(self.invoke, args, sly_data)
