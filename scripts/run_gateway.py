"""Launch the Delivery Gateway (04 §7). Needs the Neuro-SAN server + Postgres already up.

    PYTHONPATH=. python scripts/run_gateway.py            # :8000
    GATEWAY_PORT=9000 PYTHONPATH=. python scripts/run_gateway.py

Env (see gateway/settings.py): NEURO_SAN_HOST/PORT, NEURO_SAN_NETWORK, API_TOKENS, DATABASE_URL.
"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("gateway.app:app", host="0.0.0.0",
                port=int(os.environ.get("GATEWAY_PORT", "8000")), log_level="info")
