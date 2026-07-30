# Commercial Procurement Data Walkthrough — PR4

Status: redacted inspectable trace for draft PR #419  
As-of date: `2026-07-30`  
Resolver: `procurement_resolver_v5`  
Redaction: `procurement_walkthrough_redact_v1` — SHA-256 over UTF-8 bytes of '{kind}|{normalized_value}' where normalized_value is strip+lower for org/domain/email/account/source/tender keys; token is '{kind}_' + first 12 hex chars of the digest. No salt.  
**No salt is used or committed.** Production opened read-only only.

## 1. Pipeline overview

```text
external_leads_raw + lead_master
    ↓ source join (matched / raw_only / lead_only)
    ↓ field extraction + origin provenance markers
    ↓ display normalization (prefer_lead_then_raw on conflict)
    ↓ resolver-safe normalization (NULL on conflict/absent)
    ↓ PR2 account-resolution candidates (routes A–I)
    ↓ link / context / conflict decision
    ↓ planned commercial_procurement_* rows
    ↓ disposable SQLite materialization (fixture apply only)
```

## 2. Field mapping matrix

| Conceptual field | Display column | Resolver-safe column | Provenance marker |
|------------------|----------------|----------------------|-------------------|
| buyer name | buyer_display / buyer_name_norm | resolution_buyer_name_norm | origin_buyer_display |
| buyer domain | buyer_domain | resolution_buyer_domain | origin_buyer_domain |
| contact email | email_norm | resolution_contact_email | origin_contact_email |
| email domain | email_domain | resolution_email_domain | origin_email_domain |
| tender key | tender_key | (signal grain) | origin_tender_key |
| region/title/status/dates | same | n/a (not auto-link) | origin_* |


## 3. Case A — linked (production-derived)

- Selection: Among plan.resolutions with resolution_status=linked, choose the lexicographically smallest (link_route, procurement_id). Prefer routes in LINKED_ROUTES order already encoded by route string sort.

- Selected: route=`A_exact_institutional_domain`, procurement_id=`p_4f103151333f70201cabe017895cf6dd`, tender=`tender_9da3889cba98`, account=`account_c68945d71d0a`

- Join status: `matched`

- Context: `historical_tender` (close_date_in_past) as_of=2026-07-30

- Resolution: `linked` / `A_exact_institutional_domain` / `exact_institutional_domain`

- Rejected linked routes: `B_exact_canonical_name, C_exact_alias, E_explicit_email_domain`

- PR2 candidates (redacted): `{"accounts_for_alias": [], "accounts_for_buyer_domain": ["account_c68945d71d0a"], "accounts_for_canonical_name": [], "accounts_for_email_domain": ["account_c68945d71d0a"], "resolution_inputs_redacted": {"buyer_domain": "domain_36a5f22f482d", "buyer_name_norm": "org_04f654c127d7", "contact_email": "email_c5ff52777e25", "email_domain": "domain_36a5f22f482d"}}`

- Disposable materialization matches plan core fields: `True`; counts=`{'signal': 1, 'resolution': 1, 'evidence': 15, 'conflicts': 0, 'enrichment': 0}`


| Stage | Field | Input/source | Normalized/display | Resolver-safe | Decision/use |
| --- | --- | --- | --- | --- | --- |
| provenance | tender_key | external_leads_raw | tender_9da3889cba98 | None | display_only |
| provenance | buyer_display | both_equal | org_04f654c127d7 | org_04f654c127d7 | resolver_input |
| provenance | buyer_domain | lead_master | domain_36a5f22f482d | domain_36a5f22f482d | resolver_input |
| provenance | contact_email | lead_master | email_c5ff52777e25 | email_c5ff52777e25 | resolver_input |
| provenance | email_domain | lead_master | domain_36a5f22f482d | domain_36a5f22f482d | resolver_input |
| provenance | region | lead_master | Región de los Lagos | None | display_only |
| provenance | title | external_leads_raw | org_53031ef0dacd | None | display_only |
| provenance | status_code | external_leads_raw | 5 | None | display_only |
| provenance | status_name | external_leads_raw | Publicada | None | display_only |
| provenance | publication_date | external_leads_raw | 2026-02-02 | None | display_only |
| provenance | close_date | external_leads_raw | 2026-03-19 | None | display_only |



| PR4 table | Redacted row key | Important persisted fields | Why emitted |
| --- | --- | --- | --- |
| commercial_procurement_signal | p_4f103151333f70201cabe017895cf6dd | {"canonical_tender_key":"tender_9da3889cba98","procurement_context":"historical_tender","procurement_id":"p_4f103151333f70201cabe017895cf6dd","review_status":"ok"} | verified tender-level signal |
| commercial_procurement_account_resolution | r_c4ef5379548d642319a38632fd276b31 | {"account_id":"account_c68945d71d0a","link_route":"A_exact_institutional_domain","procurement_id":"p_4f103151333f70201cabe017895cf6dd","reason_code":"exact_institutional_domain","resolution_status":"linked","review_status":"ok"} | linked/A_exact_institutional_domain |
| commercial_procurement_evidence | e_112c0045039f44049c8d8edecd34d74d | {"evidence_type":"contact_email","reason_code":"contact_email","source_table":"lead_master"} | contact_email/contact_email |
| commercial_procurement_evidence | e_134a368a7ac4e5a0dafe7a9940874942 | {"evidence_type":"region","reason_code":"region","source_table":"lead_master"} | region/region |
| commercial_procurement_evidence | e_3130312d5d994a395803ed5019d7126e | {"evidence_type":"contact_email_domain","reason_code":"contact_email_domain","source_table":"lead_master"} | contact_email_domain/contact_email_domain |
| commercial_procurement_evidence | e_465b832138806687e2112615dde40e3d | {"evidence_type":"publication_date","reason_code":"publication_date","source_table":"external_leads_raw"} | publication_date/publication_date |
| commercial_procurement_evidence | e_6d7ba5d4b488ba974c49b74420654306 | {"evidence_type":"close_date","reason_code":"close_date","source_table":"external_leads_raw"} | close_date/close_date |
| commercial_procurement_evidence | e_7b2fd0f8a60d33a7f4a2313cabb76139 | {"evidence_type":"tender_key","reason_code":"tender_key","source_table":"external_leads_raw"} | tender_key/tender_key |
| commercial_procurement_evidence | e_8f456a14fa0987505b27ab103104f5df | {"evidence_type":"buyer_institution","reason_code":"normalized_buyer_institution","source_table":"lead_master"} | buyer_institution/normalized_buyer_institution |
| commercial_procurement_evidence | e_9ec279d5eda4ad2bb3970eab6c21c435 | {"evidence_type":"lead_source_membership","reason_code":"verified_lead_membership","source_table":"lead_master"} | lead_source_membership/verified_lead_membership |
| commercial_procurement_evidence | e_a32e4963189897ac7346e128bdc08790 | {"evidence_type":"status_name","reason_code":"status_name","source_table":"external_leads_raw"} | status_name/status_name |
| commercial_procurement_evidence | e_ab6dd6632dd7aeafe1ff987543c15005 | {"evidence_type":"account_resolution","reason_code":"exact_institutional_domain","source_table":"commercial_identity_account"} | account_resolution/exact_institutional_domain |
| commercial_procurement_evidence | e_ac4144cd99e67a3fe6ace717a2393ccb | {"evidence_type":"title","reason_code":"title","source_table":"external_leads_raw"} | title/title |
| commercial_procurement_evidence | e_c47e5b3b7b30b8337182cdd3095f775c | {"evidence_type":"buyer_institution","reason_code":"normalized_buyer_institution","source_table":"external_leads_raw"} | buyer_institution/normalized_buyer_institution |
| commercial_procurement_evidence | e_dcb34e8f96741887672ffe1f625b3f4a | {"evidence_type":"raw_source_membership","reason_code":"verified_raw_membership","source_table":"external_leads_raw"} | raw_source_membership/verified_raw_membership |
| commercial_procurement_evidence | e_e2063c48ea9d6dca4844ad651f90c455 | {"evidence_type":"status_code","reason_code":"status_code","source_table":"external_leads_raw"} | status_code/status_code |
| commercial_procurement_evidence | e_f192b08760c00965f25ce0216e3975fc | {"evidence_type":"buyer_domain","reason_code":"normalized_buyer_domain","source_table":"lead_master"} | buyer_domain/normalized_buyer_domain |


## 4. Case B — unresolved (production-derived)

- Selection: Among conflicts with subject_kind=unresolved_source, choose the lexicographically smallest conflict_id.

- Why not a signal: tender_key_kind is not in verified tender-level kinds (got 'unresolved_tender_key'); verified=False

- Keys: conflict=`c_0000015bf0d8c4bf8c60caed7f570c52`, source=`source_4efbcab6d2fd`, kind=`unresolved_tender_key`

- Fingerprint role: `{"in_source_fingerprint": true, "in_signal_count": false, "in_semantic_plan_digest_via": "conflict+evidence tables"}`

- Enrichment emitted: `False`; operator_queue_eligible: `None` — No enrichment_candidate row is emitted for unresolved_source rows. operator_queue_eligible applies only to verified-signal enrichment candidates.

- Operator note: Unresolved sources do not become signals; enrichment/operator eligibility is evaluated on verified signals only. This row contributes to source fingerprint / unresolved conflict count, not signal_count.


| Stage | Field | Input/source | Normalized/display | Resolver-safe | Decision/use |
| --- | --- | --- | --- | --- | --- |
| provenance | tender_key | external_leads_raw | tender_818144eb5c83 | None | display_only |
| provenance | buyer_display | lead_master | org_6214b41b1946 | org_6214b41b1946 | resolver_input |
| provenance | buyer_domain | absent | None | None | absent |
| provenance | contact_email | absent | None | None | absent |
| provenance | email_domain | absent | None | None | absent |
| provenance | region | absent | None | None | absent |
| provenance | title | external_leads_raw | org_de1ac1988874 | None | display_only |
| provenance | status_code | absent | None | None | absent |
| provenance | status_name | absent | None | None | absent |
| provenance | publication_date | absent | None | None | absent |
| provenance | close_date | absent | None | None | absent |


Conflict + evidence:


| PR4 table | Redacted row key | Important persisted fields | Why emitted |
| --- | --- | --- | --- |
| commercial_procurement_conflict | c_0000015bf0d8c4bf8c60caed7f570c52 | {"reason_code": "tender_key_unresolved_line_or_fallback", "source_record_id": "source_4efbcab6d2fd", "subject_kind": "unresolved_source"} | tender_key_unresolved_line_or_fallback |
| commercial_procurement_evidence | e_0e5dddfdec657de964538cdfaaa7c79f | {"evidence_type": "raw_source_membership", "reason_code": "unresolved_raw_membership", "source_table": "external_leads_raw"} | raw_source_membership/unresolved_raw_membership |
| commercial_procurement_evidence | e_149ac1192297bd0ddaec261cd0887c0a | {"evidence_type": "tender_key", "reason_code": "tender_key", "source_table": "external_leads_raw"} | tender_key/tender_key |
| commercial_procurement_evidence | e_3818b0a428d51bf47fe679531052005e | {"evidence_type": "title", "reason_code": "title", "source_table": "external_leads_raw"} | title/title |
| commercial_procurement_evidence | e_c721768aaecd65bf91ecca5a60b4e465 | {"evidence_type": "buyer_institution", "reason_code": "normalized_buyer_institution", "source_table": "lead_master"} | buyer_institution/normalized_buyer_institution |
| commercial_procurement_evidence | e_e01a12c503435568f5b5d5b07e88c6e3 | {"evidence_type": "lead_source_membership", "reason_code": "unresolved_lead_membership", "source_table": "lead_master"} | lead_source_membership/unresolved_lead_membership |


## 5. Case C — both_equal (production-derived)

- Selection: Among verified source lines with raw_lead_join_status=matched and any origin_* == both_equal, choose the lexicographically smallest (source_record_id, first both_equal field name).

- Field: `buyer_display` via `origin_buyer_display`

- Raw=`org_f71c76a330a5` Lead=`org_f71c76a330a5` Display=`org_f71c76a330a5` Resolver-safe=`org_f71c76a330a5`

- Normalization: safe_org_normalized / domain sanitize / email normalize as applicable; equality after normalization → origin both_equal

- Evidence policy: v5 emits field evidence against BOTH external_leads_raw and lead_master when origin=both_equal. v3 attributed buyer fields to lead_master and status/title to raw only, and also emitted tender_key_kind evidence.

- Reconciliation: Removing tender_key_kind evidence and correcting plane attribution yields net −1195 evidence rows vs v3 (= unresolved count) after both_equal dual emission offsets verified matched lines.


| Stage | Field | Input/source | Normalized/display | Resolver-safe | Decision/use |
| --- | --- | --- | --- | --- | --- |
| provenance | tender_key | external_leads_raw | tender_c0d91398da51 | None | display_only |
| provenance | buyer_display | both_equal | org_f71c76a330a5 | org_f71c76a330a5 | resolver_input |
| provenance | buyer_domain | absent | None | None | absent |
| provenance | contact_email | absent | None | None | absent |
| provenance | email_domain | absent | None | None | absent |
| provenance | region | lead_master | Región del Biobío | None | display_only |
| provenance | title | external_leads_raw | org_92ea2a57f173 | None | display_only |
| provenance | status_code | external_leads_raw | 6 | None | display_only |
| provenance | status_name | external_leads_raw | Cerrada | None | display_only |
| provenance | publication_date | external_leads_raw | 2026-02-06 | None | display_only |
| provenance | close_date | external_leads_raw | 2026-03-09 | None | display_only |


## 6. Case D — synthetic plane conflict

**Production currently has zero field-plane conflicts** (`field_plane_conflict_distribution={}`).


Case D is **SYNTHETIC**, derived from Case A structure with mutated identity planes only.


- Selection: Clone Case A constituent structure into a disposable fixture; mutate only identity-bearing lead vs raw values (buyer org, domain, contact email) and label every mutated value SYNTHETIC.

- Mutations: `{"label": "Every identity-bearing mutated value is SYNTHETIC", "lead_domain_redacted": "domain_6323f838d4ba", "lead_email_redacted": "email_0c02d94c99d9", "lead_org_redacted": "org_613bc9b72819", "raw_domain_redacted": "domain_d135992e9a4b", "raw_email_redacted": "email_a4ad96951f85", "raw_org_redacted": "org_c3fc19d0c274", "source_record_id_redacted": "source_ab221e2e184a", "tender_key_redacted": "tender_7589c5d55e7c"}`

- Display policy: `prefer_lead_then_raw`; review_status=`needs_review`; status=`unlinked` route=`F_no_match`

- Blocked: `{"route_A_E": "domain conflict \u2192 resolution_buyer_domain NULL", "route_B_C": "name conflict \u2192 resolution_buyer_name_norm NULL", "email_identity": "contact email conflict \u2192 resolution_contact_email NULL"}`

- Hashes: `{"buyer_display_lead": "f281b814981082cc", "buyer_display_raw": "841ee363ea57abdb", "buyer_domain_lead": "e955fa980c524c02", "buyer_domain_raw": "f90db04d7d257fca", "contact_email_lead": "8d70d3b6ee8a9aca", "contact_email_raw": "eb4c8035bbea83d2"}`

- Apply: applied=True, semantic=9fab35bc8557766eb988767bb6f18ed5a0bab945eed3c6f1adb4b52fd1d6864e, readback=9fab35bc8557766eb988767bb6f18ed5a0bab945eed3c6f1adb4b52fd1d6864e


| Stage | Field | Input/source | Normalized/display | Resolver-safe | Decision/use |
| --- | --- | --- | --- | --- | --- |
| provenance | tender_key | external_leads_raw | tender_7589c5d55e7c | None | display_only |
| provenance | buyer_display | conflict | org_613bc9b72819 | None | withheld_from_resolution |
| provenance | buyer_domain | conflict | domain_6323f838d4ba | None | withheld_from_resolution |
| provenance | contact_email | conflict | email_0c02d94c99d9 | None | withheld_from_resolution |
| provenance | email_domain | conflict | domain_6323f838d4ba | None | withheld_from_resolution |
| provenance | region | both_equal | RM | None | display_only |
| provenance | title | external_leads_raw | org_e55a22fa7c2e | None | display_only |
| provenance | status_code | external_leads_raw | 6 | None | display_only |
| provenance | status_name | external_leads_raw | Cerrada | None | display_only |
| provenance | publication_date | absent | None | None | absent |
| provenance | close_date | external_leads_raw | 2025-02-01 | None | display_only |



| PR4 table | Redacted row key | Important persisted fields | Why emitted |
| --- | --- | --- | --- |
| commercial_procurement_signal | p_b528e209c45d73cea85039298946fa8f | {"canonical_tender_key":"tender_7589c5d55e7c","procurement_context":"historical_tender","procurement_id":"p_b528e209c45d73cea85039298946fa8f","review_status":"needs_review"} | verified tender-level signal |
| commercial_procurement_account_resolution | r_1b434ba41f5432648ba92b6f21a1881c | {"account_id":null,"link_route":"F_no_match","procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"buyer_contact_missing","resolution_status":"unlinked","review_status":"needs_review"} | unlinked/F_no_match |
| commercial_procurement_evidence | e_0e70deab02eea289c89a0a320b2dadb0 | {"evidence_type":"contact_email_domain","reason_code":"contact_email_domain","source_table":"lead_master"} | contact_email_domain/contact_email_domain |
| commercial_procurement_evidence | e_11750451addae35d085660d144765c04 | {"evidence_type":"title","reason_code":"title","source_table":"external_leads_raw"} | title/title |
| commercial_procurement_evidence | e_174e7d793547a2f1a7465a43431fd913 | {"evidence_type":"status_name","reason_code":"status_name","source_table":"external_leads_raw"} | status_name/status_name |
| commercial_procurement_evidence | e_1e5716bd664159cdea517e11f578530a | {"evidence_type":"account_resolution","reason_code":"buyer_contact_missing","source_table":"commercial_identity_account"} | account_resolution/buyer_contact_missing |
| commercial_procurement_evidence | e_463d5dbe5ae02e6aa5c000fb76e6e666 | {"evidence_type":"contact_email_domain","reason_code":"contact_email_domain","source_table":"external_leads_raw"} | contact_email_domain/contact_email_domain |
| commercial_procurement_evidence | e_4e5e05070a9126791e7b17d2b3a22226 | {"evidence_type":"raw_source_membership","reason_code":"verified_raw_membership","source_table":"external_leads_raw"} | raw_source_membership/verified_raw_membership |
| commercial_procurement_evidence | e_51faea17985127f6dcd5cd51eb65f90b | {"evidence_type":"buyer_institution","reason_code":"normalized_buyer_institution","source_table":"external_leads_raw"} | buyer_institution/normalized_buyer_institution |
| commercial_procurement_evidence | e_65e4ce5cb1c6bf6aa1235594169fb328 | {"evidence_type":"buyer_domain","reason_code":"normalized_buyer_domain","source_table":"external_leads_raw"} | buyer_domain/normalized_buyer_domain |
| commercial_procurement_evidence | e_6841e0cbf2e555f0c7a05a65dc312c63 | {"evidence_type":"close_date","reason_code":"close_date","source_table":"external_leads_raw"} | close_date/close_date |
| commercial_procurement_evidence | e_6f5b6b8ad94e72ba16d4dc653dc2d07e | {"evidence_type":"region","reason_code":"region","source_table":"external_leads_raw"} | region/region |
| commercial_procurement_evidence | e_72df19f053f7160f4579620da3879bc4 | {"evidence_type":"status_code","reason_code":"status_code","source_table":"external_leads_raw"} | status_code/status_code |
| commercial_procurement_evidence | e_738dbb86d98064561b83926a8611706d | {"evidence_type":"region","reason_code":"region","source_table":"lead_master"} | region/region |
| commercial_procurement_evidence | e_73f740bde2dd76a76dab1985bdf21754 | {"evidence_type":"contact_email","reason_code":"contact_email","source_table":"lead_master"} | contact_email/contact_email |
| commercial_procurement_evidence | e_9cbc20ce912932fc8aca96d831016fe9 | {"evidence_type":"contact_email","reason_code":"contact_email","source_table":"external_leads_raw"} | contact_email/contact_email |
| commercial_procurement_evidence | e_b77613608cc98908d1fd953bbff6cb27 | {"evidence_type":"buyer_domain","reason_code":"normalized_buyer_domain","source_table":"lead_master"} | buyer_domain/normalized_buyer_domain |
| commercial_procurement_evidence | e_be9e6bb8bf0f387e5e173aee83522a75 | {"evidence_type":"buyer_institution","reason_code":"normalized_buyer_institution","source_table":"lead_master"} | buyer_institution/normalized_buyer_institution |
| commercial_procurement_evidence | e_c3da6ab05ea62260310d787424848c8b | {"evidence_type":"lead_source_membership","reason_code":"verified_lead_membership","source_table":"lead_master"} | lead_source_membership/verified_lead_membership |
| commercial_procurement_evidence | e_e26e223a4c063cf6b1e847ff952f94e3 | {"evidence_type":"tender_key","reason_code":"tender_key","source_table":"external_leads_raw"} | tender_key/tender_key |
| commercial_procurement_conflict | c_0eae6c92e4cf7b7d8a8a6a8fdf397939 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |
| commercial_procurement_conflict | c_781c99ebcf60bb9ed143b45f73be94b6 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |
| commercial_procurement_conflict | c_c7ab8910aa7adc52a6804568d12265a6 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |
| commercial_procurement_conflict | c_e294910603fcf0e99558a651ffa922c8 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |
| commercial_procurement_enrichment_candidate | q_3fc4b08f424030dd803b6ab946731e04 | {"account_id":null,"operator_queue_eligible":0,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"buyer_contact_missing"} | buyer_contact_missing |
| commercial_procurement_conflict | c_0eae6c92e4cf7b7d8a8a6a8fdf397939 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |
| commercial_procurement_conflict | c_781c99ebcf60bb9ed143b45f73be94b6 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |
| commercial_procurement_conflict | c_c7ab8910aa7adc52a6804568d12265a6 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |
| commercial_procurement_conflict | c_e294910603fcf0e99558a651ffa922c8 | {"account_id":null,"procurement_id":"p_b528e209c45d73cea85039298946fa8f","reason_code":"source_field_plane_conflict"} | source_field_plane_conflict |


## 7. Before/after persistence table

| Concern | Production | Disposable fixture (Case D) |
|---------|------------|-----------------------------|
| commercial_procurement_* tables | absent (dry-run only) | created + populated |
| PR2/PR3/source mutation | none | none outside fixture DB |
| Semantic digest | plan-only | plan == readback |
| Production PR4 tables after walkthrough | `[]` | n/a |


## 8. Aggregate reconciliation

- Source outcomes: 17643 (verified 16448, unresolved 1195)
- Signals: 16448; resolutions: {'ambiguous': 1, 'linked': 42, 'unlinked': 16405}
- Evidence: 203348 (v3 204543, delta -1195)
- Explanation: Removed tender_key_kind evidence; both_equal dual-plane emission offsets verified matched lines; unresolved lack that offset → net −1195.
- Source FP: `dec18b1f68ef69647dba7e33b9ab70677385bc0118c4116bc1c6d7906aae155d`
- Build FP: `188e01e68a868112ebf5246ceb46e4d663e6ddedb1246cc87752591c7ad6ca8d`
- Semantic: `e542b0107214aff4beb242542770c250f83a90fc2304e0fd6f415ca3729e4f9a`
- Plane conflicts in production: **0**


## 9. Reviewer conclusions

- Linked Case A shows institutional route selection with redacted org/domain/account.
- Unresolved Case B never becomes a signal; it still affects source fingerprint.
- both_equal Case C shows dual-plane evidence and explains evidence-count drift.
- Synthetic Case D proves conflict withholding because production conflict count is zero.
- All committed examples are deterministically redacted; production remained read-only.


## 10. Reproduction commands

```bash
cd apps/email-pipeline
DB=/home/rafael/data/origenlab-email/sqlite/emails.sqlite
uv run python scripts/commercial/generate_commercial_procurement_data_walkthrough.py \
  --sqlite-path "$DB" \
  --as-of-date 2026-07-30 \
  --write-markdown \
  --write-json
# Markdown: docs/audits/COMMERCIAL_PROCUREMENT_DATA_WALKTHROUGH_PR4.md
# JSON (gitignored): reports/out/active/current/commercial_procurement_data_walkthrough_2026-07-30/
```
