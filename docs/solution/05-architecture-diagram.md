# Sentinel — Architecture Diagrams

**Author:** Harshit Anand

**Derived from:** [01-proposed-solution.md](01-proposed-solution.md) · [HLD](03-hld.md) (structure) · [LLD](04-lld.md) (element names). Six views; each states what it shows and the invariants it makes visible. Element names match the LLD exactly.

| View                              | Question it answers                                  |
| --------------------------------- | ---------------------------------------------------- |
| V1 System landscape               | What talks to the system?                            |
| V2 Container view                 | What are the deployable units, tech, ports?          |
| V3 Agent network topology         | What runs inside Neuro-SAN?                          |
| V4 Cross-stage signal flow        | Why is this different from three disconnected tools? |
| V5 Production deployment (K8s)    | How does it run company-wide?                        |
| V6 Hackathon deployment (compose) | How does the demo run?                               |

---

## V1 — System Landscape

```mermaid
flowchart TB
    DEV(["Developer"])
    APR(["Release Manager / Approver"])

    subgraph PLATFORMS["Existing CI/CD (unchanged — D1: augment, don't replace)"]
        GHA["GitHub Actions"]
    end

    GIT["Git Hosting"]
    NIM["NVIDIA NIM<br/>hosted or self-hosted"]
    OSV["OSV.dev CVE DB"]
    SLK["Slack / Teams"]

    subgraph SYS["Sentinel"]
        CORE["Gateway + Neuro-SAN network<br/>+ PostgreSQL + Dashboard"]
    end

    DEV -->|"push / PR"| PLATFORMS
    PLATFORMS -->|"webhooks"| SYS
    SYS -->|"gate status, promotion dispatch,<br/>review comments"| PLATFORMS
    SYS <-->|"clone / diff"| GIT
    SYS <-->|"LLM inference"| NIM
    SYS <-->|"CVE queries"| OSV
    SYS -->|"hold / escalation notices"| SLK
    SLK --> APR
    DEV <-->|"reports, trails"| SYS
    APR <-->|"approval queue"| SYS
```

Invariants visible: the system sits **beside** CI/CD, not inside it; all human entry points converge on one dashboard; NIM is swappable between hosted and self-hosted without changing the picture.

## V2 — Container View (deployable units)

```mermaid
flowchart TB
    subgraph EXT["External"]
        CI["CI/CD platforms"]
        USERS["Browsers (dev / approver)"]
        NIMX["NIM endpoint"]
    end

    subgraph SYSTEM["System containers"]
        GW["Delivery Gateway<br/>FastAPI · :8000<br/>webhooks, adapters, invoker,<br/>REST+SSE, dashboard static"]
        NS["Neuro-SAN Server<br/>neuro-san 0.6.70 · :8080 HTTP / :30011 gRPC<br/>network: sentinel<br/>19 coded tools (AGENT_TOOL_PATH)"]
        RUN["Test Runner Sandbox<br/>subprocess (demo) /<br/>ephemeral K8s Job (prod)"]
        NF["NSFlow · :4173<br/>agent visualization"]
        PG[("PostgreSQL 17 · :5432<br/>runs · findings · scores ·<br/>decisions · approvals · audit")]
        WS[/"Workspace volume (RWX)<br/>ephemeral clones"/]
    end

    CI -->|"HTTPS webhooks (HMAC/token)"| GW
    GW -->|"POST /api/v1/sentinel/streaming_chat"| NS
    NS -->|"chat completions (nvidia class)"| NIMX
    NS -->|"spawn + parse results"| RUN
    RUN --- WS
    GW --- WS
    NS --- WS
    GW -->|"SQLAlchemy"| PG
    NS -->|"coded-tool DAO"| PG
    USERS -->|"HTTPS REST + SSE"| GW
    USERS -->|"demo/debug"| NF
    NF --> NS
    GW -->|"status / dispatch / comment"| CI
```

| Container        | Image                                                                     | Scale unit            |
| ---------------- | ------------------------------------------------------------------------- | --------------------- |
| Delivery Gateway | `Dockerfile.gateway` (python 3.12 slim)                                   | HPA 2–10              |
| Neuro-SAN server | `Dockerfile.neuro-san` (python:3.13-slim base pattern, non-root uid 1001) | HPA 2–8               |
| Runner sandbox   | `Dockerfile.runner-python` / `runner-node`                                | Job per run           |
| PostgreSQL       | managed / `postgres:16`                                                   | HA pair               |
| NSFlow           | studio image                                                              | 1 (non-prod-critical) |

## V3 — Agent Network Topology (inside Neuro-SAN)

```mermaid
flowchart TD
    GWC["Gateway invoker<br/>(client)"] ==>|"streaming_chat<br/>sly_data: event, run_id,<br/>git_token, repo_workspace"| FM

    subgraph NET["registries/sentinel.hocon — llm_config: NIM llama-3.3-70b + fallbacks"]
        FM["delivery_coordinator<br/><b>frontman</b> · structure_formats: json<br/>allow.to_upstream: run_id, review_report,<br/>test_results, risk_score, decision"]

        subgraph STAGE_A["Stage 2–4: understand & review"]
            CA["change_analysis_agent"]
            RP["review_planner<br/><b>CodedTool (no LLM)</b><br/>sizes review → 1–4 shards"]
            SR["security_reviewer_1..4<br/>1–4 adaptive fan-out<br/>(invoked sequentially)"]
            SS["senior_security_agent"]
            QR["code_quality_agent<br/><i>LIGHT_MODEL slot</i>"]
            RSY["report_publisher<br/><b>CodedTool (no LLM)</b><br/>review synthesis"]
        end
        subgraph STAGE_B["Stage 5–6: test"]
            TS["test_selection_agent"]
            TE["test_runner<br/><b>CodedTool (no LLM)</b>"]
        end
        subgraph STAGE_C["Stage 7–9: decide"]
            EC["environment_context_agent<br/><i>LIGHT_MODEL slot</i>"]
            RK["risk_scoring_agent"]
            PGA["promotion_gating_agent"]
        end

        T_CA["git_diff · ast_analyzer ·<br/>dependency_graph"]
        T_SEC["secret_scanner<br/>(per shard n)"]
        T_CVE["dependency_cve<br/>(reviewer 1 only)"]
        T_RD["review_digest<br/>→ senior_summary"]
        T_QR["complexity_metrics"]
        T_TS["test_mapper"]
        T_EC["incident_history ·<br/>deploy_window"]
        T_RK["risk_calculator<br/>(risk-v1 weights)"]
        T_PG["trust_ladder (prod floor) ·<br/>decision_logger · cicd_action ·<br/>notification"]
        T_CS["contract_store<br/>(validates + writes contracts to<br/>sly_data; security_findings_shard_n)"]

        FM --> CA & RP & SR & SS & QR & RSY & TS & TE & EC & RK & PGA
        RP -.->|"selects 1–4 shards"| SR
        CA --- T_CA
        SR --- T_SEC
        SR --- T_CVE
        SS --- T_RD
        QR --- T_QR
        TS --- T_TS
        EC --- T_EC
        RK --- T_RK
        PGA --- T_PG
        SR & QR & TS & EC --- T_CS
    end

    RSY & T_RK & T_PG & T_EC -.->|"DAO"| PG[("PostgreSQL")]
    T_CVE -.-> OSV["OSV.dev"]
    T_PG -.-> SLKX["Slack/Teams · Gateway internal API"]
```

Invariants visible: exactly one frontman; specialists own only their leaf tools (no agent-to-agent shortcuts); the security review is an adaptive 1–4-shard fan-out sized by a deterministic planner, not a fixed specialist count; every deterministic element (`review_planner` sharding, `report_publisher` synthesis, `risk_calculator`, `trust_ladder`, `test_runner`) is a tool, not an LLM; sly_data egress is a five-key allow-list.

## V4 — Cross-Stage Signal Flow (the differentiator)

```mermaid
flowchart LR
    subgraph S1["Review stage"]
        F["2 Critical findings:<br/>SQL injection + hardcoded secret<br/>in auth module<br/>(security_reviewer → report_publisher)"]
    end
    subgraph S2["Test stage"]
        T["All selected tests PASS<br/>(test_runner)"]
    end
    subgraph S3["Decision stage"]
        R["risk_calculator:<br/>+40 SQLi critical +40 secret critical<br/>+15 auth sensitive flag<br/>= 95 (critical band)"]
        G["trust_ladder test→qa:<br/>critical ⇒ ESCALATE"]
        H["Human approval required —<br/>reasoning trail cites findings<br/>SEC-001, SEC-002"]
    end
    F -->|"review_report (F7)"| R
    T -->|"test_results (F9)"| R
    R --> G --> H

    style F fill:#7f1d1d,stroke:#ef4444,color:#fff
    style T fill:#14532d,stroke:#22c55e,color:#fff
    style G fill:#7c2d12,stroke:#f97316,color:#fff
```

Binary CI gating sees only the green box and promotes. This system promotes the red box's signal across stage boundaries mechanically (`review_report` is a structural input to `risk_calculator` — DFD invariant 3). This is Demo Run 2 on one slide.

## V5 — Production Deployment (Kubernetes, cloud-agnostic)

> **Status: specified, not yet built** — post-hackathon packaging. No K8s manifests, Dockerfiles, or compose file exist today (dev is host-native, no Docker); this view is the target topology.

```mermaid
flowchart TB
    INET(("Company network / VPN"))

    subgraph CLUSTER["Kubernetes cluster"]
        subgraph ZONE_EDGE["Edge zone"]
            ING["Ingress (TLS)<br/>sentinel.company.internal"]
        end
        subgraph NSPACE["namespace: sentinel"]
            subgraph ZONE_APP["Application zone"]
                GWP["gateway ×2–10 (HPA)"]
                NSP["neuro-san ×2–8 (HPA)<br/>AGENT_MAX_CONCURRENT_REQUESTS=50"]
                NFP["nsflow ×1 (internal only)"]
            end
            subgraph ZONE_SAND["Sandbox zone — default-deny egress"]
                JOBS["test-runner Jobs (ephemeral)<br/>activeDeadlineSeconds · no secrets ·<br/>package-registry egress only"]
            end
            subgraph ZONE_AI["AI zone (optional GPU pool)"]
                NIMP["self-hosted NIM<br/>llama-3.3-70b (+8b)"]
            end
            CMS["ConfigMaps: policy · weights ·<br/>repo config · llm_config · logging"]
            SECS["Secrets (vault-backed):<br/>NVIDIA_API_KEY · webhook secrets ·<br/>git tokens · DB creds"]
            WSV[/"RWX PVC: /workspaces (TTL-swept)"/]
        end
    end

    PGM[("Managed PostgreSQL<br/>HA + PITR")]
    NIMH["Hosted NIM<br/>(integrate.api.nvidia.com)"]
    OBS["Observability stack:<br/>logs · Prometheus · Phoenix/Langfuse (OTEL)"]

    INET --> ING --> GWP
    GWP <--> NSP
    NSP --> JOBS
    NSP -->|"either"| NIMP
    NSP -.->|"or"| NIMH
    GWP & NSP --> PGM
    GWP & NSP --- CMS & SECS
    GWP & NSP & JOBS --- WSV
    GWP & NSP -.-> OBS
    NFP --> NSP
```

Security posture visible: sandbox zone isolated by NetworkPolicy; secrets never reach Jobs; self-hosted NIM keeps code in-cluster (QA8); single TLS entry point.

## V6 — Hackathon Deployment (docker-compose)

> **Status: specified, not yet built** — dev is currently host-native (local Postgres 17, `run.ps1` launcher, no Docker). This compose view is the target packaging.

```mermaid
flowchart LR
    subgraph HOST["Laptop / demo VM — docker compose"]
        GW["gateway :8000<br/>SIMULATE_CICD=true<br/>GW_AUTH_MODE=token"]
        NS["neuro-san :8080/:30011"]
        PG[("postgres :5432<br/>seeded: incidents,<br/>repo_config")]
        NF["nsflow :4173"]
        WS[/"workspaces volume"/]
    end
    B1["Browser: Dashboard<br/>localhost:8000"]
    B2["Browser: NSFlow<br/>localhost:4173"]
    SIM["scripts/demo_run_1.sh · demo_run_2.sh<br/>POST /api/v1/simulate"]
    NIMH["NIM hosted API<br/>(only NVIDIA_API_KEY leaves the box)"]
    SREPOS["samples/python-payments-service<br/>samples/node-catalog-service"]

    SIM --> GW
    B1 --> GW
    B2 --> NF --> NS
    GW <--> NS
    GW & NS --- WS
    GW & NS --> PG
    NS --> NIMH
    GW -.->|"local clone"| SREPOS
```

Same images and code paths as V5 minus scale-out (HLD A9: the demo is a subset, not a fork). Demo choreography: Run 1 (clean change → auto-promote) and Run 2 (planted SQLi + hardcoded secret → escalate despite green tests) both visible side-by-side as dashboard reasoning trails + NSFlow agent choreography.
