"""Launch the Neuro-SAN network server (:8080) with .env loaded.

The stock `neuro_san.service.main_loop.server_main_loop` reads AGENT_MANIFEST_FILE /
AGENT_TOOL_PATH / AGENT_LLM_INFO_FILE / AGENT_HTTP_PORT + NVIDIA_API_KEY from the environment
but does NOT auto-load .env. This wrapper loads .env first, then runs it.

    PYTHONPATH=. python scripts/run_server.py
"""
import runpy

from dotenv import load_dotenv

load_dotenv()
runpy.run_module("neuro_san.service.main_loop.server_main_loop", run_name="__main__")
