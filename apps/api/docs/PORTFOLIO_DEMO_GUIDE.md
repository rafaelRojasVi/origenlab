# OrigenLab API portfolio demo guide

This guide is for a short portfolio or interview demo of the OrigenLab API. It focuses on endpoints that are typed, read-only, and easy to explain without exposing unnecessary operational details.

## Demo framing

OrigenLab is an operator platform for laboratory-equipment commercial operations. The API is a read-only layer over internal pipeline data. SQLite/Gmail remain the operational source of truth, Postgres is a reporting mirror, and FastAPI exposes dashboard-friendly views.

Suggested framing:

> The API is read-only. It exposes typed operator views over the email and procurement pipeline. SQLite/Gmail remain the operational truth, Postgres is an eventually consistent mirror, and API/browser surfaces are for review rather than mutation.

## Best endpoints to show

### `GET /health`

Start here. It is small, typed, and confirms service mode without private business data.

### `GET /mirror/meta/dashboard-sync`

Use this to show mirror freshness. It shows the latest Postgres dashboard sync status, elapsed time, and aggregate mirrored counts.

### `GET /cases/warm?limit=2`

Use this to show the operator review workflow. Keep the limit low. Explain that warm cases are heuristic review suggestions, not automatic decisions.

### `GET /operator/procurement/status`

Use this to show business value. It exposes the W1 institution-prospect procurement summary — counts, operator queue sizes, and staleness — that drives the dashboard's actionable-opportunity view.

## Endpoints to avoid during a public screen-share

### `GET /emails/recent`

Useful internally, but it can show sender and subject previews. Prefer redacted data for demos.

### `GET /mirror/audits/gmail-interactions`

Useful internally, but too detailed for a portfolio demo because it contains interaction summaries by domain.

### `GET /operator/automation-status`

Useful for the dashboard, but broad. It combines several operational snapshots and still has flexible nested sections.

### `GET /mirror/health/dependencies`

Now more safely redacted, but still infrastructure-oriented. Show only briefly if needed.

## Honest strengths

- read-only API boundary
- separated FastAPI routes
- Pydantic response models on core endpoints
- service and repository layering
- request IDs and centralized error handling
- path and infrastructure redaction
- mirror freshness endpoint
- clear dashboard-facing resources

## Honest limitations and future improvements

- Some operator snapshot endpoints still use flexible nested dictionaries.
- Some values are string-normalized because they originate from reports or CSV artifacts.
- Audit endpoints should get pagination before being treated as general API resources.
- Human labels should eventually be split into stable machine codes plus display labels.
- Public demos should use redacted or mock data where possible.

## Suggested demo order

1. `GET /health`
2. `GET /mirror/meta/dashboard-sync`
3. `GET /cases/warm?limit=2`
4. `GET /operator/procurement/status`

End with this architecture sentence:

> Gmail/SQLite truth flows into a Postgres reporting mirror, and the read-only FastAPI layer exposes safe operator views for the React dashboard.
