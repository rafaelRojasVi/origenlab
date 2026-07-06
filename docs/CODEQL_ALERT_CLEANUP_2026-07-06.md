# CodeQL alert cleanup — 2026-07-06

**Branch:** `fix/codeql-security-alerts-2026-07`  
**Type:** Security hardening only — safer redaction, URL parsing, response-header sanitization.  
**Safety:** No Gmail actions, database writes, outreach/send/NDR/mirror-sync mutations, env files, secrets, or production API auth/proxy changes.

## Alerts addressed

| Alert | Severity | File | Fix |
|-------|----------|------|-----|
| #16 | High | `audit_tatiana_identity_signals.py` | Default stdout: aggregate counts + redacted sender samples; raw samples require `--include-sensitive-samples --ack-sensitive-output` |
| #15 | High | `audit_tatiana_identity_signals.py` | Stop printing full trusted-domain list and DB path details by default |
| #4 | High | `warmCaseDetailStrategy.ts` | URL redaction via `parseAllowedHttpUrl`; email domain match via `emailDomain()` |
| #14 | Medium | `run_contact_hunt_web_server.py` | `sanitize_header_value` + `content_disposition_attachment` block CRLF in headers |
| #12 | High (test) | `test_warm_case_sender_rules.py` | `domain_fixture.assert_domain_in_collection` (urlparse) |
| #11 | High (test) | `test_marketing_supplier_domains.py` | Exact `frozenset` equality |
| #10 | High (test) | `test_export_supplier_domain_false_positive_audit.py` | `domain_fixture` helper |
| #9 | High (test) | `test_build_archive_send_batch.py` | `domain_fixture` helper |

## Files changed

**Production-relevant**

- `apps/email-pipeline/scripts/dataset/audit_tatiana_identity_signals.py`
- `apps/email-pipeline/scripts/leads/advanced/run_contact_hunt_web_server.py`
- `apps/dashboard/src/lib/warmCaseDetailStrategy.ts`

**Tests / helpers**

- `apps/email-pipeline/tests/test_audit_tatiana_identity_signals_output.py` (new)
- `apps/email-pipeline/tests/domain_fixture.py` (new)
- `apps/email-pipeline/tests/test_run_contact_hunt_web_server.py`
- `apps/email-pipeline/tests/test_warm_case_sender_rules.py`
- `apps/email-pipeline/tests/test_marketing_supplier_domains.py`
- `apps/email-pipeline/tests/test_export_supplier_domain_false_positive_audit.py`
- `apps/email-pipeline/tests/test_build_archive_send_batch.py`
- `apps/dashboard/src/lib/warmCaseDetailStrategy.test.ts`

## Validation commands

```bash
git diff --check

cd apps/dashboard && npm test -- --run src/lib/warmCaseDetailStrategy.test.ts src/test/dashboard0Safety.test.ts

cd apps/email-pipeline && uv run pytest \
  tests/test_audit_tatiana_identity_signals_output.py \
  tests/test_warm_case_sender_rules.py \
  tests/test_marketing_supplier_domains.py \
  tests/test_export_supplier_domain_false_positive_audit.py \
  tests/test_build_archive_send_batch.py \
  tests/test_run_contact_hunt_web_server.py \
  tests/test_public_repo_security.py -q

cd apps/api && uv run pytest \
  tests/test_http_security.py \
  tests/test_no_write_policy.py \
  tests/test_api_response_contract.py -q
```

**Optional post-merge:** confirm GitHub Security CodeQL alerts #4, #9–#12, #14–#16 close after workflow rerun on `main`.
