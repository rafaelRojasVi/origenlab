# Web App Context

Status: canonical  
Owner: web-maintainers  
Last reviewed: 2026-03-24

Primary context for [`apps/web/`](../).

<a id="m-web-purpose"></a>
## Purpose

Static Spanish-first B2B website for OrigenLab that helps visitors understand offerings and request quotations.

<a id="m-web-agent-start"></a>
## Agent start path

0. Monorepo factual entry (when unsure which app): [`../../../docs/PROJECT_CONTEXT.md`](../../../docs/PROJECT_CONTEXT.md#m-proj-start)
1. Business/scope constraints → [`BUSINESS_CONTEXT.md`](BUSINESS_CONTEXT.md)
2. Technical structure → [`ARCHITECTURE.md`](ARCHITECTURE.md)
3. Operations/deploy → [`RUNBOOK.md`](RUNBOOK.md#m-webrun-local)

<a id="m-web-facts"></a>
## Canonical facts

- Business facts and contacts: [`src/data/`](../src/data/)
- Agent policy overlay (subordinate to monorepo factual docs): [`../AGENTS.md`](../AGENTS.md)

<a id="m-web-ops"></a>
## Current operational reality

- Deployment model: static build → HostGator shared hosting.
- Mail operations: documented in [`email-setup.md`](email-setup.md).
- Security baseline: [`security-audit-v1.md`](security-audit-v1.md).

<a id="m-web-historical"></a>
## Historical boundary

Canonical email-pipeline docs are under [`../../email-pipeline/docs/`](../../email-pipeline/docs/) — this app does not duplicate them.
