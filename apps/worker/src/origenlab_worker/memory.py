"""Case memory, writer layer: read latest memory, call the model, insert version+1.

Called after the main triage recording (and after the forward + quote-refs readings), always
isolated in a try/except so a failure here never takes the triage down.

Controlled by:
* `ORIGENLAB_WORKER_MEMORY_ENABLED`  — ``true`` to activate; anything else = off (default).
* `ORIGENLAB_WORKER_MEMORY_MODEL`    — model id, default ``claude-haiku-5-5``.

The writer does nothing unless the message was in HUMAN_CLASSES (a deal email a person wrote).
It updates:

1. the opportunity (case) memory — summary, facts, open_questions, next_step;
2. the institution (organization) memory — summary + facts only, from the linked org;
3. the person (contact) memory — summary + facts only, from the primary contact.

If a linked case has a previous institution or person memory, it is seeded into the next memory
request for that subject — so the model always sees prior context.

The database writer (`TriageDb.insert_subject_memory`) is a single INSERT with no FK on
subject_id; duplicate (subject_kind, subject_id, version) is idempotent via ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ENV_MEMORY_ENABLED = "ORIGENLAB_WORKER_MEMORY_ENABLED"
ENV_MEMORY_MODEL = "ORIGENLAB_WORKER_MEMORY_MODEL"


def memory_enabled() -> bool:
    """`ORIGENLAB_WORKER_MEMORY_ENABLED` (default off): ``true`` activates the memory writer."""
    return os.environ.get(ENV_MEMORY_ENABLED, "").strip().lower() == "true"


def memory_model() -> str:
    from origenlab_worker.memory_model import DEFAULT_MEMORY_MODEL
    from origenlab_api.v2.catalog.enrichment import check_model_id

    raw = os.environ.get(ENV_MEMORY_MODEL, "").strip()
    if not raw:
        return DEFAULT_MEMORY_MODEL
    try:
        return check_model_id(raw)
    except ValueError:
        return DEFAULT_MEMORY_MODEL


def _next_version(previous: dict[str, Any] | None) -> int:
    if previous is None:
        return 1
    return int(previous.get("_version", 0)) + 1


def _update_one(
    db: Any,
    reader: Any,
    subject_kind: str,
    subject_id: str,
    email_body: str,
    *,
    source_record_id: str | None = None,
    model: str,
    stage: str | None = None,
    quote_numbers: list[str] | None = None,
    institution_name: str | None = None,
) -> bool:
    """Read the latest memory, call the model, insert the next version. Returns True on success."""
    from origenlab_worker.memory_model import build_memory_request, parse_memory
    from origenlab_worker.triage_model import ModelRefused

    previous = db.latest_subject_memory(subject_kind, subject_id)
    request = build_memory_request(
        previous, email_body,
        subject_kind=subject_kind, model=model,
        stage=stage, quote_numbers=quote_numbers, institution_name=institution_name,
    )
    try:
        message = reader(request)
    except Exception as exc:  # noqa: BLE001 - network/rate-limit: skip this update, retry next email
        logger.warning("memory model call failed (%s): %s", subject_kind, type(exc).__name__)
        return False
    try:
        memory = parse_memory(message, source_record_id=source_record_id)
    except ModelRefused as exc:
        if str(exc) != "model_refused":
            logger.warning("memory parse refused (%s): %s", subject_kind, exc)
        return False
    next_version = _next_version(previous)
    db.insert_subject_memory(subject_kind, subject_id, next_version, memory,
                             source_record_id=source_record_id, model=model)
    return True


def record_memory(db: Any, reader: Any, source_record_id: str, email_body: str, *,
                  opportunity_id: str | None,
                  organization_id: str | None,
                  person_id: str | None,
                  stage: str | None = None,
                  quote_numbers: list[str] | None = None,
                  institution_name: str | None = None,
                  model: str) -> int:
    """Write or update memories for the linked case, institution and person.

    Returns the number of memories successfully updated (0–3).
    """
    updated = 0
    if opportunity_id:
        try:
            if _update_one(db, reader, "opportunity", opportunity_id, email_body,
                           source_record_id=source_record_id, model=model,
                           stage=stage, quote_numbers=quote_numbers,
                           institution_name=institution_name):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("opportunity memory skipped: %s", type(exc).__name__)
    if organization_id:
        try:
            if _update_one(db, reader, "organization", organization_id, email_body,
                           source_record_id=source_record_id, model=model):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("organization memory skipped: %s", type(exc).__name__)
    if person_id:
        try:
            if _update_one(db, reader, "person", person_id, email_body,
                           source_record_id=source_record_id, model=model):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("person memory skipped: %s", type(exc).__name__)
    return updated


def backfill_memory(
    db: Any,
    store: Any,
    reader: Any,
    source_record_ids: list[str],
    *,
    apply: bool,
    model: str,
) -> dict[str, int]:
    """Emails already triaged as HUMAN_CLASSES that have no memory yet — write their first memory.

    A dry run reads and counts but writes nothing. The reader is an `anthropic_reader` compatible
    callable (the model call); caller supplies it so tests can inject a fake.
    """
    from origenlab_worker.triage import storage_key
    from origenlab_worker.mail_text import parse_mail
    from origenlab_worker.storage import StorageError

    counts: dict[str, int] = {"found": len(source_record_ids), "updated": 0, "skipped": 0, "no_cases": 0}
    for sid in source_record_ids:
        # load() returns the PendingMessage and some flags; we need eml_storage_path + thread_id.
        loaded = db.load(sid)
        if loaded is None or not loaded[0].eml_storage_path:
            counts["skipped"] += 1
            continue
        message = loaded[0]
        try:
            raw = store.get(storage_key(message.eml_storage_path))
            mail = parse_mail(raw)
        except (StorageError, Exception):  # noqa: BLE001
            counts["skipped"] += 1
            continue
        # Find linked cases; skip if none.
        cases = db.linked_cases(message.thread_id)
        if not cases:
            counts["no_cases"] += 1
            continue
        if apply:
            for case in cases:
                org_id, person_id = db.opportunity_org_and_person(case.opportunity_id)
                inst_name = db.opportunity_institution_name(case.opportunity_id)
                quote_nums = db.opportunity_quote_numbers(case.opportunity_id)
                updated = record_memory(
                    db, reader, sid, mail.body,
                    opportunity_id=case.opportunity_id,
                    organization_id=org_id,
                    person_id=person_id,
                    stage=case.stage,
                    quote_numbers=quote_nums,
                    institution_name=inst_name,
                    model=model,
                )
                counts["updated"] += updated
    return counts


__all__ = [
    "ENV_MEMORY_ENABLED", "ENV_MEMORY_MODEL",
    "backfill_memory", "memory_enabled", "memory_model", "record_memory",
]
