# CLAUDE.md

## Project

**AI Delivery Intelligence Layer** — Cognizant internal hackathon project: multi-agent code review + smart test selection + explainable promotion gating, built on **Neuro-SAN as the sole multi-agent orchestrator**. Currently in **design phase**: documentation is the deliverable; implementation not started.

## Repo layout

- `docs/hackathon-delivery-intelligence.md` — original problem statement (source of the idea; do not edit).
- `docs/personal-project-delivery-intelligence-full.md` — post-hackathon evolution vision. Architecture must stay compatible with it, but hackathon docs must **never mention** these future features (test generation, mutation testing, learning loop).
- `docs/solution/` — the design document set. `01-proposed-solution.md` is the **master spec / single source of truth**; 02 (DFD), 03 (HLD), 04 (LLD), 05 (architecture) derive from it.
- `neuro-san-studio/` — clone of cognizant-ai-lab/neuro-san-studio. **Read-only reference** for framework facts (HOCON schema, AAOSA, CodedTool interface, server/deploy). Never modify it.
- `graphify-out/` — knowledge graph of the docs (gitignored, regenerable).

## Hard rules

1. Any design change starts in `01-proposed-solution.md`, then propagates to 02–05. Docs must never contradict 01.
2. Framework claims must be grounded in `neuro-san-studio/` (docs or code) — no guessed Neuro-SAN behavior. Key refs: `docs/user_guide.md`, `registries/*.hocon`, `coded_tools/`.
3. Locked design decisions (revisit only if user asks): NVIDIA NIM primary LLM (`nvidia-llama-3.3-70b-instruct`) with fallback chain; PostgreSQL everywhere; K8s production / docker-compose hackathon; all three CI/CD platforms (GitHub Actions, Jenkins, GitLab CI) at equal depth; Mermaid for all diagrams.
4. Core design principle in every decision: **LLM reasons, code decides** — risk formula, trust ladder, test execution are deterministic coded tools; LLM may only raise risk, never lower it; staging→production never auto-promotes.
5. Naming: HOCON coded-tool names drop the `_tool` suffix; module files keep it (`test_runner` ↔ `coded_tools/delivery_intelligence/test_runner_tool.py`, class `TestRunnerTool`).

## Knowledge graph (graphify)

`graphify-out/graph.json` maps all docs (98 nodes, 9 communities). Before answering cross-document questions, prefer querying it (`/graphify query "<question>"`) over re-reading the full doc set. After editing docs, refresh with `/graphify docs --update`.

## Conventions

- Diagrams: Mermaid in fenced blocks (GitHub/VS Code renderable). Avoid raw `{}` in flowchart labels.
- Section cross-references between docs use the form `[04-lld.md §5](04-lld.md)` — verify the target section exists after renumbering.
