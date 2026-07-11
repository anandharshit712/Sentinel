"""Launch the Delivery Gateway (04 §7). Needs the Neuro-SAN server + Postgres already up.

    PYTHONPATH=. python scripts/run_gateway.py            # :8000
    GATEWAY_PORT=9000 PYTHONPATH=. python scripts/run_gateway.py

Env (see gateway/settings.py): NEURO_SAN_HOST/PORT, NEURO_SAN_NETWORK, API_TOKENS, DATABASE_URL.
"""
import os
import socket
import sys

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("GATEWAY_PORT", "8000"))
    # Refuse to start if the port is already served — a second launch (manual on top of run.ps1,
    # venv vs system python) otherwise leaves a confusing half-bound duplicate. Fail loud instead.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            sys.exit(f"Gateway port {port} is already in use — a gateway is already running. "
                     f"Stop it first; run only ONE.")
    uvicorn.run("gateway.app:app", host="0.0.0.0", port=port, log_level="info")
