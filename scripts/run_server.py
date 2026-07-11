"""Launch the Neuro-SAN network server (:8080) with .env loaded.

The stock `neuro_san.service.main_loop.server_main_loop` reads AGENT_MANIFEST_FILE /
AGENT_TOOL_PATH / AGENT_LLM_INFO_FILE / AGENT_HTTP_PORT + NVIDIA_API_KEY from the environment
but does NOT auto-load .env. This wrapper loads .env first, then runs it.

    PYTHONPATH=. python scripts/run_server.py
"""
import os
import runpy
import socket
import sys

from dotenv import load_dotenv

load_dotenv()
# Refuse to start if :8080 is already served — a duplicate neuro-san (two launches) fights for the
# port and the Gateway's invoker intermittently hits the dead one. Fail loud instead.
_port = int(os.environ.get("AGENT_HTTP_PORT", "8080"))
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
    if _s.connect_ex(("127.0.0.1", _port)) == 0:
        sys.exit(f"Neuro-SAN port {_port} is already in use — a server is already running. "
                 f"Stop it first; run only ONE.")
runpy.run_module("neuro_san.service.main_loop.server_main_loop", run_name="__main__")
