# AI Delivery Intelligence Layer — Design Document Set

Built on **Neuro-SAN** as the sole multi-agent orchestrator. Source problem statement: [../hackathon-delivery-intelligence.md](../hackathon-delivery-intelligence.md).

| #   | Document                                            | Purpose                                                                                                                                                                                                        |
| --- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | [Proposed Solution](01-proposed-solution.md)        | **Master specification** — the single source of truth: problems, solution, agent network, risk model, trust ladder, contracts, scope. All other documents derive from it.                                      |
| 02  | [Data Flow Diagrams](02-dfd.md)                     | DFD Level 0 (context), Level 1 (system), Level 2 (drill-downs) + data dictionary + flow invariants.                                                                                                            |
| 03  | [High-Level Design](03-hld.md)                      | Quality attributes, logical/component/runtime views, deployment (compose + Kubernetes), security, scalability, availability, observability, ADRs, risks.                                                       |
| 04  | [Low-Level Design](04-lld.md)                       | Project layout, full agent-network HOCON, JSON contracts, all 16 coded tools, LLM config (NVIDIA NIM), Gateway API + CI/CD adapters, PostgreSQL DDL, dashboard, deployment artifacts, testing, error handling. |
| 05  | [Architecture Diagrams](05-architecture-diagram.md) | Six views: landscape, containers, agent network topology, cross-stage signal flow, production K8s, hackathon compose.                                                                                          |
| 06  | [Frontend Design](06-frontend-design.md)            | Decision Dashboard SPA: stack (React+Vite+TS+Tailwind+shadcn/ui), routes, component hierarchy, data/SSE layer, design system, auth gating — derives from 01 §14 and 04 §9.                                     |

Reading order: 01 → 05 (visual overview) → 03 → 02 → 04 → 06.
Diagrams are Mermaid — render natively on GitHub and in VS Code (Markdown Preview Mermaid extension).
