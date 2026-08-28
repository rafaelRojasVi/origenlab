"""Pure helpers for the unified do-not-repeat email master (read-only export).

``export_do_not_repeat_master`` (scripts) opens read-only SQLite, reads reports tree
files, and writes CSV/TXT/JSON. This module holds merge, counting, and formatting
only — no database access and no network.

For operator entrypoint, see :mod:`scripts.qa.export_do_not_repeat_master`.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence, Tuple

from origenlab_email_pipeline.business_mart import emails_in

# Email-like keys scanned from JSON send manifests
CSV_EMAIL_FIELDS: Tuple[str, ...] = (
    "contact_email",
    "resolved_contact_email",
    "email",
    "to",
    "recipient",
    "real_to",
    "effective_to",
    "recipient_email",
)

# CSV / summary column contract (order matters for ``csv.DictWriter``)
MASTER_FIELDS: list[str] = [
    "email_norm",
    "source_kinds",
    "source_count",
    "first_seen_at",
    "last_seen_at",
    "notes",
]

_MANIFEST_NEST_KEYS: Tuple[str, ...] = (
    "recipients",
    "to",
    "emails",
    "sent_recipients",
    "results",
    "messages",
)


def norm_email_from_cell(text: str) -> str | None:
    found = emails_in(str(text or ""))
    if not found:
        return None
    return found[0].strip().lower()


def add_manifest_emails(node: Any, out: list[str], counter: dict[str, int]) -> None:
    if isinstance(node, str):
        counter["total"] += 1
        em = norm_email_from_cell(node)
        if em:
            out.append(em)
        return
    if isinstance(node, list):
        for item in node:
            add_manifest_emails(item, out, counter)
        return
    if isinstance(node, dict):
        for key in CSV_EMAIL_FIELDS:
            if key in node:
                counter["total"] += 1
                em = norm_email_from_cell(str(node.get(key) or ""))
                if em:
                    out.append(em)
        for key in _MANIFEST_NEST_KEYS:
            if key in node:
                add_manifest_emails(node[key], out, counter)


def parse_send_manifest_payload(payload: Any) -> tuple[list[str], dict[str, Any]]:
    out: list[str] = []
    counter = {"total": 0}
    add_manifest_emails(payload, out, counter)
    root = payload if isinstance(payload, dict) else {}
    return out, root


def rel_path_for_docs(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def discover_active_files(active_root: Path) -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    if not active_root.is_dir():
        return found
    for p in sorted(active_root.rglob("send_manifest.json")):
        found.append(("send_manifest", p.parent.name or "manifest", p))
    send_ready = active_root / "current" / "send_ready.csv"
    if send_ready.is_file():
        found.append(("send_ready_csv", "send_ready", send_ready))

    def _add_glob(label: str, pattern: str) -> None:
        for p in sorted(active_root.rglob(pattern)):
            if p.is_file():
                found.append(("marketing_csv", label, p))

    _add_glob("all_known_marketing_contacts_dedup", "all_known_marketing_contacts_dedup.csv")
    _add_glob("outreach_contacted_all", "outreach_contacted_all.csv")
    for p in sorted(active_root.rglob("chile_institutional_*.csv")):
        found.append(("marketing_csv", "chile_institutional", p))
    for p in sorted(active_root.rglob("deepsearch_*.csv")):
        found.append(("marketing_csv", "deepsearch", p))

    seen: set[Path] = set()
    deduped: list[tuple[str, str, Path]] = []
    for kind, name, path in found:
        rp = path.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        deduped.append((kind, name, path))
    return deduped


def _fieldnames_lower_map(fieldnames: Sequence[str] | None) -> dict[str, str]:
    return {str(h or "").strip().lower(): h for h in (fieldnames or []) if h is not None}


def scan_csv_emails(path: Path) -> set[str]:
    """Read a CSV and collect normalized emails from known contact columns."""
    emails: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = _fieldnames_lower_map(reader.fieldnames)
        for row in reader:
            em: str | None = None
            for name in CSV_EMAIL_FIELDS:
                lk = name.lower()
                if lk not in headers:
                    continue
                raw = row.get(headers[lk])
                em = norm_email_from_cell(str(raw or ""))
                if em:
                    break
            if em:
                emails.add(em)
    return emails


@dataclass
class DoNotRepeatAgg:
    kinds: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    merge_count: int = 0
    first: str = ""
    last: str = ""

    def touch(
        self,
        kind: str,
        *,
        path: str | None = None,
        dmin: str = "",
        dmax: str = "",
    ) -> None:
        self.kinds.add(kind)
        self.merge_count += 1
        if path:
            self.paths.add(path)
        for d in (dmin, dmax):
            ds = str(d or "").strip()
            if not ds:
                continue
            if not self.first or ds < self.first:
                self.first = ds
            if not self.last or ds > self.last:
                self.last = ds


def apply_gmail_sent_bounds(
    agg: MutableMapping[str, DoNotRepeatAgg], sent_bounds: Mapping[str, tuple[str, str]]
) -> None:
    for em, (dmin, dmax) in sent_bounds.items():
        a = agg.setdefault(em, DoNotRepeatAgg())
        a.touch("gmail_sent", dmin=dmin, dmax=dmax)


def apply_outreach_state_dates(
    agg: MutableMapping[str, DoNotRepeatAgg], outreach_dates: Mapping[str, tuple[str, str]]
) -> None:
    for em, (dmin, dmax) in outreach_dates.items():
        a = agg.setdefault(em, DoNotRepeatAgg())
        a.touch("outreach_state", dmin=dmin, dmax=dmax)


def apply_suppression_set(agg: MutableMapping[str, DoNotRepeatAgg], emails: Iterable[str]) -> None:
    for em in emails:
        a = agg.setdefault(em, DoNotRepeatAgg())
        a.touch("email_suppression")


def touch_source_with_paths(
    agg: MutableMapping[str, DoNotRepeatAgg],
    emails: Iterable[str],
    *,
    tag: str,
    rel: str,
) -> None:
    for em in emails:
        a = agg.setdefault(em, DoNotRepeatAgg())
        a.touch(tag, path=rel)


def kind_touches_marketing_or_manifest_file(kinds: Iterable[str]) -> bool:
    for k in kinds:
        if k.startswith("marketing_csv:") or k.startswith("send_manifest:") or k.startswith("send_ready_csv:"):
            return True
    return False


def build_master_csv_rows(agg: Mapping[str, DoNotRepeatAgg]) -> list[dict[str, str]]:
    rows_out: list[dict[str, str]] = []
    for em in sorted(agg.keys()):
        a = agg[em]
        kinds = ";".join(sorted(a.kinds))
        notes_paths = sorted(a.paths)[:8]
        notes = f"paths={','.join(notes_paths)}" if notes_paths else ""
        rows_out.append(
            {
                "email_norm": em,
                "source_kinds": kinds,
                "source_count": str(a.merge_count),
                "first_seen_at": a.first,
                "last_seen_at": a.last,
                "notes": notes[:2000],
            }
        )
    return rows_out


def format_email_list_txt(agg: Mapping[str, DoNotRepeatAgg]) -> str:
    return "\n".join(sorted(agg.keys())) + "\n"


def count_source_buckets(agg: Mapping[str, DoNotRepeatAgg]) -> tuple[int, int, int, int]:
    n_gmail = sum(1 for a in agg.values() if "gmail_sent" in a.kinds)
    n_state = sum(1 for a in agg.values() if "outreach_state" in a.kinds)
    n_supp = sum(1 for a in agg.values() if "email_suppression" in a.kinds)
    n_mkt = sum(1 for a in agg.values() if kind_touches_marketing_or_manifest_file(a.kinds))
    return n_gmail, n_state, n_supp, n_mkt


def top_file_paths_by_row_count(
    file_email_counts: Counter[str], *, limit: int = 12
) -> list[str]:
    return [p for p, _ in file_email_counts.most_common(limit)]


def build_do_not_repeat_summary(
    *,
    generated_at: str,
    db_path: Path,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    reports_out_dir: Path,
    agg: Mapping[str, DoNotRepeatAgg],
    file_email_counts: Counter[str],
    csv_path: Path,
    txt_path: Path,
) -> dict[str, Any]:
    n_gmail, n_state, n_supp, n_mkt = count_source_buckets(agg)
    top_files = top_file_paths_by_row_count(file_email_counts, limit=12)
    return {
        "schema_version": "1",
        "generated_at": generated_at,
        "db_path": str(db_path.resolve()),
        "gmail_user": gmail_user,
        "sent_folders": list(sent_folders),
        "reports_out_dir": str(reports_out_dir.resolve()),
        "unique_emails": len(agg),
        "from_gmail_sent": n_gmail,
        "from_outreach_state": n_state,
        "from_email_suppression": n_supp,
        "from_marketing_csv_or_manifest_or_send_ready_files": n_mkt,
        "top_source_files_by_email_rows_scanned": top_files,
        "outputs": {
            "csv": str(csv_path.resolve()),
            "txt": str(txt_path.resolve()),
        },
    }
