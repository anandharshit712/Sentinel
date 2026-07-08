"""Pytest bootstrap: run from repo root so top-level packages import and config/ paths resolve."""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
