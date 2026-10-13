from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from origenlab_email_pipeline.core import research_automation as ra


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def test_prompt_rendering() -> None:
    text = ra.render_prompt(
        template_text="sector={sector} limit={limit_hint} dnr={canonical_dnr_path}",
        sector="broad",
        limit_hint=25,
        compact_seed_files={
            "canonical_dnr_path": Path("/tmp/dnr.csv"),
            "seed_known_institutions": Path("/tmp/i.csv"),
            "seed_known_domains": Path("/tmp/d.csv"),
            "seed_recent_contacted_emails_sample": Path("/tmp/e.csv"),
            "seed_exclusion_summary": Path("/tmp/s.json"),
        },
    )
    assert "sector=broad" in text
    assert "limit=25" in text
    assert "dnr=/tmp/dnr.csv" in text


def test_extract_csv_parsing_from_model_output() -> None:
    txt = (
        "foo\n```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n```\nbar"
    )
    csv_text = ra.extract_csv_text_from_model_output(txt)
    fields, rows, row_errors = ra.parse_csv_rows(csv_text)
    assert fields == ra.EXPECTED_COLUMNS
    assert rows[0]["contact_email"] == "a@x.cl"
    assert row_errors == []


def test_extract_csv_with_prose_and_bom() -> None:
    txt = (
        "\ufeffResult follows.\n"
        "institution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n"
        "Thanks."
    )
    csv_text = ra.extract_csv_text_from_model_output(txt)
    fields, rows, row_errors = ra.parse_csv_rows(csv_text)
    assert fields == ra.EXPECTED_COLUMNS
    assert len(rows) == 1
    assert row_errors == []


def test_extract_csv_with_spaced_quoted_fields_normalizes_values() -> None:
    txt = (
        "```csv\n"
        "institution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "\"Hospital X\", \"Atacama\", \"Copiapó\", \"Hospital\", \"admin@redsalud.gob.cl\", \"Procurement\", \"https://redsalud.gob.cl\", \"high\", \"Need for lab equipment\"\n"
        "```"
    )
    csv_text = ra.extract_csv_text_from_model_output(txt)
    fields, rows, row_errors = ra.parse_csv_rows(csv_text)
    assert fields == ra.EXPECTED_COLUMNS
    assert len(rows) == 1
    assert rows[0]["region"] == "Atacama"
    assert rows[0]["contact_email"] == "admin@redsalud.gob.cl"
    assert rows[0]["source_url"] == "https://redsalud.gob.cl"
    assert rows[0]["confidence"] == "high"
    assert row_errors == []


def test_malformed_schema_failure() -> None:
    bad = (
        "institution_name,region,city,type,contact_email,contact_label,source_url,confidence\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high\n"
    )
    try:
        ra.parse_csv_rows(bad)
        assert False, "Expected ValueError for missing fit_signal"
    except ValueError as exc:
        assert "missing required columns" in str(exc).lower()


def test_malformed_row_rejected_with_row_details() -> None:
    bad = (
        "institution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high\n"
    )
    fields, rows, row_errors = ra.parse_csv_rows(bad)
    assert fields == ra.EXPECTED_COLUMNS
    assert len(rows) == 1
    assert row_errors
    assert row_errors[0]["error"] == "wrong_column_count_missing_values"


def test_path_resolution(tmp_path: Path) -> None:
    out = ra.resolve_out_dir(out_dir=tmp_path / "abc")
    assert out == (tmp_path / "abc").resolve()


def test_local_exclusion_behavior(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.csv"
    _write_csv(
        candidates,
        ["institution_name", "contact_email"],
        [["A", "a@x.cl"], ["B", "b@x.cl"], ["C", "c@x.cl"]],
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [["a@x.cl"]])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [["b@x.cl"]])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [["c@x.cl"]])
    seeds = ra.SeedPaths(dnr, contacted, known)
    netnew = tmp_path / "netnew.csv"
    excluded = tmp_path / "excluded.csv"
    summary = ra.run_local_exclusion(
        candidates_csv=candidates,
        seed_paths=seeds,
        out_netnew_csv=netnew,
        out_excluded_csv=excluded,
    )
    assert summary["total_candidates"] == 3
    assert summary["excluded_count"] == 3
    assert summary["netnew_count"] == 0
    ex_rows = list(csv.DictReader(excluded.open(encoding="utf-8", newline="")))
    reasons = {r["contact_email"]: r["exclusion_reason"] for r in ex_rows}
    assert reasons["a@x.cl"] == "dnr_match"
    assert reasons["b@x.cl"] == "contacted_match"
    assert reasons["c@x.cl"] == "known_marketing_match"


def test_review_summary_generation(tmp_path: Path) -> None:
    send_ready = tmp_path / "send_ready_marketing.csv"
    _write_csv(
        send_ready,
        ["institution_name", "contact_email", "contact_label", "type"],
        [["Inst A", "lab@a.cl", "lab", "universidad"], ["Inst B", "oficina@b.cl", "oficina_partes", "instituto"]],
    )
    blocked = tmp_path / "blocked.csv"
    _write_csv(blocked, ["contact_email"], [["x@y.cl"]])
    summary_json = tmp_path / "summary.json"
    summary_json.write_text(
        json.dumps(
            {
                "counts": {"blocked": 1, "needs_manual_review": 0, "send_ready_marketing": 2},
                "quality_review_reason_counts": {
                    "domain_mismatch": 1,
                    "weak_source_match": 2,
                    "generic_contact_weak_evidence": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    artifacts = ra.RunArtifacts(
        out_dir=tmp_path,
        raw_response_json=tmp_path / "raw_response.json",
        raw_response_txt=tmp_path / "raw_response.txt",
        candidates_raw_csv=tmp_path / "raw.csv",
        candidates_netnew_csv=tmp_path / "netnew.csv",
        candidates_excluded_csv=tmp_path / "excluded.csv",
        validation_json=tmp_path / "validation_result.json",
        review_summary_md=tmp_path / "review_summary.md",
        run_metadata_json=tmp_path / "run_metadata.json",
        prompt_preview_txt=tmp_path / "prompt_preview.txt",
        api_error_json=tmp_path / "api_error.json",
        api_error_txt=tmp_path / "api_error.txt",
        api_request_summary_json=tmp_path / "api_request_summary.json",
        api_response_summary_json=tmp_path / "api_response_summary.json",
        retry_attempts_json=tmp_path / "retry_attempts.json",
        process_workspace=tmp_path / "ws",
    )
    text = ra.build_review_summary_markdown(
        exclusion_summary={"total_candidates": 5, "excluded_count": 1, "netnew_count": 4, "excluded": []},
        send_ready_csv=send_ready,
        blocked_csv=blocked,
        summary_json_path=summary_json,
        netnew_csv=tmp_path / "netnew.csv",
        output_paths=artifacts,
        prompt_file=tmp_path / "prompt.txt",
        sector="broad",
        research_mode="heavy",
        model_used=ra.DEFAULT_HEAVY_MODEL,
        limits={
            "limits_triggered": True,
            "truncation_applied": True,
            "max_candidates": 200,
            "max_send_ready": 50,
            "send_ready_over_limit": False,
        },
    )
    assert "Ready for review; no live send performed." in text
    assert "Top institutions" in text
    assert "Research mode" in text
    assert "Model" in text
    assert "Prompt file" in text
    assert "Quality hardening signals" in text
    assert "Suspicious domain mismatches" in text
    assert "Truncation applied" in text


def test_dry_run_no_send_safety(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if "process_broad_marketing_contacts.py" in " ".join(args):
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [
                    [
                        "MKT-00001",
                        "a@x.cl",
                        "marketing_contacts",
                        "A",
                        "RM",
                        "Santiago",
                        "universidad",
                        "lab",
                        "https://x.cl",
                        "high",
                        "fit",
                        "broad_marketing",
                        "pass_quality_gate",
                        "",
                    ]
                ],
            )
            _write_csv(
                ws / "marketing_blocked_already_known.csv",
                ["institution_name", "contact_email", "block_reason"],
                [],
            )
            _write_csv(
                ws / "marketing_needs_manual_review.csv",
                ["institution_name", "contact_email", "review_reason"],
                [],
            )
            _write_csv(
                ws / "marketing_safe_to_send.csv",
                ["institution_name", "contact_email"],
                [["A", "a@x.cl"]],
            )
            (ws / "marketing_contacts_summary.json").write_text(
                json.dumps(
                    {
                        "counts": {
                            "input_rows": 1,
                            "safe_to_send": 1,
                            "blocked": 0,
                            "needs_manual_review": 0,
                            "send_ready_marketing": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    artifacts = ra.run_research_automation(
        model="o4-mini-deep-research",
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="broad",
        limit_hint=5,
        dry_run=True,
        sample_response=sample,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=200,
        max_send_ready=50,
        fail_on_over_limit=False,
        run_contacted_coverage_check=False,
        strict_contacted_coverage=False,
    )
    meta = json.loads(artifacts.run_metadata_json.read_text(encoding="utf-8"))
    assert meta["safety"]["gmail_send_called"] is False
    assert meta["safety"]["sqlite_mutation_intended"] is False
    assert meta["dry_run"] is True
    assert meta["max_candidates"] == 200
    assert meta["max_send_ready"] == 50
    assert "output_directory" in meta
    assert "compact_seed_artifacts" in meta
    assert "Ready for review; no live send performed." in artifacts.review_summary_md.read_text(encoding="utf-8")


def test_candidate_limit_truncation(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a1@x.cl,lab,https://x.cl,high,fit\n"
        "B,RM,Santiago,universidad,a2@x.cl,lab,https://x.cl,high,fit\n"
        "C,RM,Santiago,universidad,a3@x.cl,lab,https://x.cl,high,fit\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if "process_broad_marketing_contacts.py" in " ".join(args):
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [
                    [
                        "MKT-00001",
                        "a1@x.cl",
                        "marketing_contacts",
                        "A",
                        "RM",
                        "Santiago",
                        "universidad",
                        "lab",
                        "https://x.cl",
                        "high",
                        "fit",
                        "broad_marketing",
                        "pass_quality_gate",
                        "",
                    ]
                ],
            )
            _write_csv(ws / "marketing_blocked_already_known.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_needs_manual_review.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_safe_to_send.csv", ["contact_email"], [["a1@x.cl"]])
            (ws / "marketing_contacts_summary.json").write_text(
                json.dumps({"counts": {"send_ready_marketing": 1, "blocked": 0, "needs_manual_review": 0}}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    artifacts = ra.run_research_automation(
        model="o4-mini-deep-research",
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="broad",
        limit_hint=10,
        dry_run=True,
        sample_response=sample,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=2,
        max_send_ready=50,
        fail_on_over_limit=False,
        run_contacted_coverage_check=False,
        strict_contacted_coverage=False,
    )
    meta = json.loads(artifacts.run_metadata_json.read_text(encoding="utf-8"))
    assert meta["truncation_applied"] is True
    assert meta["candidate_count"] == 2


def test_fail_on_over_limit_behavior(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a1@x.cl,lab,https://x.cl,high,fit\n"
        "B,RM,Santiago,universidad,a2@x.cl,lab,https://x.cl,high,fit\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    try:
        ra.run_research_automation(
            model="o4-mini-deep-research",
            prompt_file=prompt,
            out_dir=tmp_path / "out",
            sector="broad",
            limit_hint=10,
            dry_run=True,
            sample_response=sample,
            seed_paths=seeds,
            use_background=False,
            app_root=Path(__file__).resolve().parents[1],
            max_candidates=1,
            max_send_ready=50,
            fail_on_over_limit=True,
            run_contacted_coverage_check=False,
            strict_contacted_coverage=False,
        )
        assert False, "Expected over-limit failure"
    except RuntimeError as exc:
        assert "max-candidates" in str(exc)
    # Failure artifacts should still be generated.
    out = tmp_path / "out"
    assert (out / "run_metadata.json").is_file()
    assert (out / "api_error.json").is_file()
    assert (out / "api_error.txt").is_file()
    assert (out / "prompt_preview.txt").is_file()


def test_cli_help() -> None:
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "research" / "run_deep_research_prospecting.py"
    cp = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert cp.returncode == 0
    assert "dry-run" in cp.stdout
    assert "--max-candidates" in cp.stdout
    assert "--day-rotation" in cp.stdout
    assert "--research-mode" in cp.stdout
    assert "--daily-mode" in cp.stdout
    assert "--max-retries" in cp.stdout
    assert "--initial-backoff-seconds" in cp.stdout
    assert "--max-backoff-seconds" in cp.stdout
    assert "--fallback-sector" in cp.stdout
    assert "--tpm-safe" in cp.stdout
    assert "--tiny-run" in cp.stdout
    assert "--verbose-progress" in cp.stdout
    assert "--run-contacted-coverage-check" in cp.stdout
    assert "--print-research-config" in cp.stdout


def test_day_rotation_mapping() -> None:
    assert ra.resolve_sector_for_day_rotation(weekday=0) == "broad"
    assert ra.resolve_sector_for_day_rotation(weekday=1) == "water_env"
    assert ra.resolve_sector_for_day_rotation(weekday=2) == "universities_regional"
    assert ra.resolve_sector_for_day_rotation(weekday=3) == "hospitals_clinical"
    assert ra.resolve_sector_for_day_rotation(weekday=4) == "industry_qc"
    assert ra.resolve_sector_for_day_rotation(weekday=5) == "thin_regions"


def test_contacted_coverage_hook_non_strict(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(args)
        if "validate_contacted_csv_coverage.py" in cmd:
            out = Path(args[args.index("--json-out") + 1])
            out.write_text('{"ok": true}\n', encoding="utf-8")
            return subprocess.CompletedProcess(args=args, returncode=3, stdout="coverage warning", stderr="")
        if "process_broad_marketing_contacts.py" in cmd:
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [
                    [
                        "MKT-00001",
                        "a@x.cl",
                        "marketing_contacts",
                        "A",
                        "RM",
                        "Santiago",
                        "universidad",
                        "lab",
                        "https://x.cl",
                        "high",
                        "fit",
                        "broad_marketing",
                        "pass_quality_gate",
                        "",
                    ]
                ],
            )
            _write_csv(ws / "marketing_blocked_already_known.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_needs_manual_review.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_safe_to_send.csv", ["contact_email"], [["a@x.cl"]])
            (ws / "marketing_contacts_summary.json").write_text(
                json.dumps({"counts": {"send_ready_marketing": 1, "blocked": 0, "needs_manual_review": 0}}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    artifacts = ra.run_research_automation(
        model="o4-mini-deep-research",
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="broad",
        limit_hint=5,
        dry_run=True,
        sample_response=sample,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=200,
        max_send_ready=50,
        fail_on_over_limit=False,
        run_contacted_coverage_check=True,
        strict_contacted_coverage=False,
    )
    meta = json.loads(artifacts.run_metadata_json.read_text(encoding="utf-8"))
    assert meta["contacted_coverage_check"]["enabled"] is True
    assert meta["contacted_coverage_check"]["returncode"] == 3


def test_compact_seed_generation_caps(tmp_path: Path) -> None:
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [[f"user{i}@a.cl"] for i in range(10)])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(
        contacted,
        ["contact_email", "institution_name"],
        [[f"c{i}@b.cl", f"Inst {i%4}"] for i in range(20)],
    )
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(
        known,
        ["contact_email", "institution_name"],
        [[f"k{i}@c.cl", f"Known {i%3}"] for i in range(20)],
    )
    compact = ra.build_compact_seed_artifacts(
        out_dir=tmp_path / "out",
        seed_paths=ra.SeedPaths(dnr, contacted, known),
        max_seed_email_sample=5,
        max_seed_institutions=4,
        max_seed_domains=3,
    )
    sample_rows = list(csv.DictReader(Path(compact["seed_recent_contacted_emails_sample"]).open(encoding="utf-8")))
    inst_rows = list(csv.DictReader(Path(compact["seed_known_institutions"]).open(encoding="utf-8")))
    dom_rows = list(csv.DictReader(Path(compact["seed_known_domains"]).open(encoding="utf-8")))
    assert len(sample_rows) == 5
    assert len(inst_rows) <= 4
    assert len(dom_rows) <= 3


def test_backoff_seconds_monotonic() -> None:
    a = ra._backoff_seconds(attempt=1, initial=5.0, cap=120.0)
    b = ra._backoff_seconds(attempt=2, initial=5.0, cap=120.0)
    c = ra._backoff_seconds(attempt=3, initial=5.0, cap=120.0)
    assert a >= 5.0
    assert b >= 10.0
    assert c >= 20.0


def test_retry_attempts_written_on_retryable_failure(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ra, "OpenAI", lambda api_key: object())
    state = {"n": 0}

    def _fake_research(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise ra.DeepResearchApiError(
                message="rate limited",
                code="rate_limit_exceeded",
                status="failed",
                retryable=True,
            )
        return "{}", "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\nA,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n```", {}

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if "process_broad_marketing_contacts.py" in " ".join(args):
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [
                    [
                        "MKT-1",
                        "a@x.cl",
                        "marketing_contacts",
                        "A",
                        "RM",
                        "Santiago",
                        "universidad",
                        "lab",
                        "https://x.cl",
                        "high",
                        "fit",
                        "broad_marketing",
                        "pass_quality_gate",
                        "",
                    ]
                ],
            )
            _write_csv(ws / "marketing_blocked_already_known.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_needs_manual_review.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_safe_to_send.csv", ["contact_email"], [["a@x.cl"]])
            (ws / "marketing_contacts_summary.json").write_text(json.dumps({"counts": {"send_ready_marketing": 1, "blocked": 0, "needs_manual_review": 0}}), encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "run_deep_research_response", _fake_research)
    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    monkeypatch.setattr(ra.time, "sleep", lambda _: None)
    artifacts = ra.run_research_automation(
        model="o4-mini-deep-research",
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="broad",
        limit_hint=5,
        dry_run=False,
        sample_response=None,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=200,
        max_send_ready=50,
        fail_on_over_limit=False,
        run_contacted_coverage_check=False,
        strict_contacted_coverage=False,
        max_retries=3,
        initial_backoff_seconds=0.1,
        max_backoff_seconds=0.2,
    )
    attempts = json.loads(artifacts.retry_attempts_json.read_text(encoding="utf-8"))["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["retryable"] is True
    assert attempts[0]["result"] == "error"
    assert attempts[1]["result"] == "success"


def test_preflight_summary_prints_sizes_and_guardrails(tmp_path: Path, capsys) -> None:
    inst = tmp_path / "seed_known_institutions.csv"
    dom = tmp_path / "seed_known_domains.csv"
    ems = tmp_path / "seed_recent_contacted_emails_sample.csv"
    summ = tmp_path / "seed_exclusion_summary.json"
    for p in (inst, dom, ems, summ):
        p.write_text("x\n", encoding="utf-8")

    compact = {
        "seed_known_institutions": str(inst),
        "seed_known_domains": str(dom),
        "seed_recent_contacted_emails_sample": str(ems),
        "seed_exclusion_summary": str(summ),
        "counts": {
            "dnr_total": 100,
            "contacted_total": 90,
            "known_total": 80,
            "sample_email_count": 50,
            "institution_rows": 20,
            "domain_rows": 20,
        },
    }
    ra._print_preflight_summary(
        selected_sector="water_env",
        prompt_text="hello world",
        compact=compact,
        max_candidates=20,
        max_send_ready=10,
        max_seed_email_sample=50,
        max_seed_institutions=80,
        max_seed_domains=80,
        max_retries=2,
    )
    out = capsys.readouterr().out
    assert "Preflight size summary (before API submission)" in out
    assert "sector: water_env" in out
    assert "rendered_prompt_chars: 11" in out
    assert "compact_seed_file_sizes_bytes" in out
    assert "max_retries: 2" in out


def test_non_retryable_error_no_retry(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ra, "OpenAI", lambda api_key: object())
    calls = {"n": 0}

    def _fake_research(**kwargs):
        calls["n"] += 1
        raise ra.DeepResearchApiError(
            message="bad request",
            code="invalid_prompt",
            status="failed",
            retryable=False,
        )

    monkeypatch.setattr(ra, "run_deep_research_response", _fake_research)
    monkeypatch.setattr(ra.time, "sleep", lambda _: None)
    try:
        ra.run_research_automation(
            model="o4-mini-deep-research",
            prompt_file=prompt,
            out_dir=tmp_path / "out",
            sector="broad",
            limit_hint=5,
            dry_run=False,
            sample_response=None,
            seed_paths=seeds,
            use_background=False,
            app_root=Path(__file__).resolve().parents[1],
            max_candidates=200,
            max_send_ready=50,
            fail_on_over_limit=False,
            run_contacted_coverage_check=False,
            strict_contacted_coverage=False,
            max_retries=4,
            initial_backoff_seconds=0.1,
            max_backoff_seconds=0.2,
        )
        assert False, "Expected non-retryable failure"
    except ra.DeepResearchApiError:
        pass
    assert calls["n"] == 1


def test_light_prompt_template_loads() -> None:
    text = ra.load_prompt_template(ra.DEFAULT_LIGHT_PROMPT_PATH)
    assert "{sector}" in text
    assert "{limit_hint}" in text
    assert "local exact exclusion" in text.lower()
    assert "do not guess firstname.lastname patterns" in text.lower()
    assert "avoid generic university inboxes" in text.lower()


def test_heavy_prompt_template_has_university_negative_guidance() -> None:
    text = ra.load_prompt_template(ra.DEFAULT_PROMPT_PATH)
    low = text.lower()
    assert "do not guess firstname.lastname patterns" in low
    assert "avoid generic university contact emails" in low
    assert "gmail/hotmail/etc." in low


def test_light_mode_uses_light_executor_and_sets_metadata(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "light_prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ra, "OpenAI", lambda api_key: object())

    called = {"light": 0}

    def _fake_light(**kwargs):
        called["light"] += 1
        return (
            "{}",
            "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
            "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n```",
            {},
        )

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if "process_broad_marketing_contacts.py" in " ".join(args):
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [
                    [
                        "MKT-1",
                        "a@x.cl",
                        "marketing_contacts",
                        "A",
                        "RM",
                        "Santiago",
                        "universidad",
                        "lab",
                        "https://x.cl",
                        "high",
                        "fit",
                        "broad_marketing",
                        "pass_quality_gate",
                        "",
                    ]
                ],
            )
            _write_csv(ws / "marketing_blocked_already_known.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_needs_manual_review.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_safe_to_send.csv", ["contact_email"], [["a@x.cl"]])
            (ws / "marketing_contacts_summary.json").write_text(
                json.dumps({"counts": {"send_ready_marketing": 1, "blocked": 0, "needs_manual_review": 0}}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "run_light_research_response", _fake_light)
    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    artifacts = ra.run_research_automation(
        model=ra.DEFAULT_LIGHT_MODEL,
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="water_env",
        limit_hint=10,
        dry_run=False,
        sample_response=None,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=20,
        max_send_ready=10,
        fail_on_over_limit=False,
        run_contacted_coverage_check=False,
        strict_contacted_coverage=False,
        max_retries=2,
        initial_backoff_seconds=0.1,
        max_backoff_seconds=0.2,
        research_mode="light",
    )
    meta = json.loads(artifacts.run_metadata_json.read_text(encoding="utf-8"))
    assert called["light"] == 1
    assert meta["research_mode"] == "light"
    assert meta["model"] == ra.DEFAULT_LIGHT_MODEL
    assert meta["safety"]["gmail_send_called"] is False
    assert meta["safety"]["sqlite_mutation_intended"] is False


def test_evidence_verification_warnings_flow_into_processing_input(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,contacto@uchile.cl,Contacto,https://www.uchile.cl,high,laboratorio\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    observed = {"processing_has_review_reason": False}

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(args)
        if "verify_research_candidate_evidence.py" in cmd:
            out_csv = Path(args[args.index("--out-csv") + 1])
            _write_csv(
                out_csv,
                [
                    "source_line",
                    "institution_name",
                    "contact_email",
                    "source_url",
                    "http_status",
                    "email_visible_on_source",
                    "relevance_keywords_present",
                    "evidence_warning",
                    "evidence_ok",
                    "fetch_error",
                ],
                [["2", "A", "contacto@uchile.cl", "https://www.uchile.cl", "200", "0", "0", "email_not_visible_on_source", "0", ""]],
            )
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="warn", stderr="")
        if "process_broad_marketing_contacts.py" in cmd:
            inp = Path(args[args.index("--input") + 1])
            rows = list(csv.DictReader(inp.open(encoding="utf-8", newline="")))
            observed["processing_has_review_reason"] = "review_reason" in rows[0] and (
                "email_not_visible_on_source" in str(rows[0].get("review_reason") or "")
            )
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [],
            )
            _write_csv(ws / "marketing_blocked_already_known.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_needs_manual_review.csv", ["contact_email"], [["contacto@uchile.cl"]])
            _write_csv(ws / "marketing_safe_to_send.csv", ["contact_email"], [])
            (ws / "marketing_contacts_summary.json").write_text(
                json.dumps({"counts": {"send_ready_marketing": 0, "blocked": 0, "needs_manual_review": 1}}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    artifacts = ra.run_research_automation(
        model="o4-mini-deep-research",
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="broad",
        limit_hint=5,
        dry_run=True,
        sample_response=sample,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=200,
        max_send_ready=50,
        fail_on_over_limit=False,
        run_contacted_coverage_check=False,
        strict_contacted_coverage=False,
    )
    meta = json.loads(artifacts.run_metadata_json.read_text(encoding="utf-8"))
    assert observed["processing_has_review_reason"] is True
    assert int(meta["evidence_verification"]["warnings_row_count"]) == 1


def test_evidence_first_mode_never_trusts_model_emails(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,search_query,expected_unit_type,fit_rationale\n"
        "USACH,site:usach.cl laboratorio microbiologia contacto email,laboratorio,match icp\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)

    def _fail_if_called(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if "process_broad_marketing_contacts.py" in " ".join(args):
            raise AssertionError("processing should not run in evidence_first mode")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "_run_subprocess", _fail_if_called)
    artifacts = ra.run_research_automation(
        model=ra.DEFAULT_LIGHT_MODEL,
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="universities_regional",
        limit_hint=5,
        dry_run=True,
        sample_response=sample,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=200,
        max_send_ready=50,
        fail_on_over_limit=False,
        run_contacted_coverage_check=False,
        strict_contacted_coverage=False,
        research_mode="light",
        research_output_mode="evidence_first",
    )
    meta = json.loads(artifacts.run_metadata_json.read_text(encoding="utf-8"))
    assert meta["research_output_mode"] == "evidence_first"
    assert meta["send_ready_count"] == 0
    assert (tmp_path / "out" / "evidence_plan.csv").is_file()


def test_heavy_and_light_both_run_evidence_verification(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl/lab,high,laboratorio\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    seen = {"verify_calls": 0}

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(args)
        if "verify_research_candidate_evidence.py" in cmd:
            seen["verify_calls"] += 1
            out_csv = Path(args[args.index("--out-csv") + 1])
            _write_csv(
                out_csv,
                ["source_line", "institution_name", "contact_email", "source_url", "http_status", "email_visible_on_source", "relevance_keywords_present", "evidence_warning", "evidence_ok", "fetch_error"],
                [["2", "A", "a@x.cl", "https://x.cl/lab", "200", "1", "1", "", "1", ""]],
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")
        if "process_broad_marketing_contacts.py" in cmd:
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [],
            )
            _write_csv(ws / "marketing_blocked_already_known.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_needs_manual_review.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_safe_to_send.csv", ["contact_email"], [])
            (ws / "marketing_contacts_summary.json").write_text(
                json.dumps({"counts": {"send_ready_marketing": 0, "blocked": 0, "needs_manual_review": 0}}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    for mode in ("light", "heavy"):
        ra.run_research_automation(
            model=ra.DEFAULT_LIGHT_MODEL if mode == "light" else ra.DEFAULT_HEAVY_MODEL,
            prompt_file=prompt,
            out_dir=tmp_path / f"out-{mode}",
            sector="broad",
            limit_hint=5,
            dry_run=True,
            sample_response=sample,
            seed_paths=seeds,
            use_background=False,
            app_root=Path(__file__).resolve().parents[1],
            max_candidates=200,
            max_send_ready=50,
            fail_on_over_limit=False,
            run_contacted_coverage_check=False,
            strict_contacted_coverage=False,
            research_mode=mode,
        )
    assert seen["verify_calls"] == 2


def test_heavy_default_model_resolution() -> None:
    model = ra.resolve_model_for_mode(research_mode="heavy", explicit_model=None)
    assert model == ra.DEFAULT_HEAVY_MODEL
    assert ra.is_deep_research_model(model) is True


def test_heavy_invalid_env_model_fails_before_api_call(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    monkeypatch.setenv("ORIGENLAB_RESEARCH_HEAVY_MODEL", "gpt-4o-mini")
    resolved = ra.resolve_model_for_mode(research_mode="heavy", explicit_model=None)
    assert resolved == "gpt-4o-mini"
    called = {"api": 0}

    def _never_api(api_key):
        called["api"] += 1
        return object()

    monkeypatch.setattr(ra, "OpenAI", _never_api)
    try:
        ra.run_research_automation(
            model=resolved,
            prompt_file=prompt,
            out_dir=tmp_path / "out",
            sector="broad",
            limit_hint=5,
            dry_run=True,
            sample_response=sample,
            seed_paths=seeds,
            use_background=False,
            app_root=Path(__file__).resolve().parents[1],
            max_candidates=200,
            max_send_ready=50,
            fail_on_over_limit=False,
            run_contacted_coverage_check=False,
            strict_contacted_coverage=False,
            research_mode="heavy",
        )
        assert False, "Expected fail-closed heavy model error"
    except RuntimeError as exc:
        assert "requires an OpenAI Deep Research model" in str(exc)
    assert called["api"] == 0


def test_heavy_post_response_model_mismatch_fails_closed(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ra, "OpenAI", lambda api_key: object())
    monkeypatch.setattr(
        ra,
        "run_deep_research_response",
        lambda **kwargs: (
            json.dumps({"id": "resp_1", "model": "gpt-4o-mini", "status": "completed"}),
            "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\nA,RM,Santiago,universidad,a@x.cl,lab,https://x.cl,high,fit\n```",
            {},
        ),
    )
    try:
        ra.run_research_automation(
            model=ra.DEFAULT_HEAVY_MODEL,
            prompt_file=prompt,
            out_dir=tmp_path / "out",
            sector="broad",
            limit_hint=5,
            dry_run=False,
            sample_response=None,
            seed_paths=seeds,
            use_background=False,
            app_root=Path(__file__).resolve().parents[1],
            max_candidates=200,
            max_send_ready=50,
            fail_on_over_limit=False,
            run_contacted_coverage_check=False,
            strict_contacted_coverage=False,
            research_mode="heavy",
        )
        assert False, "Expected heavy mismatch fail-closed"
    except RuntimeError as exc:
        assert "fail-closed" in str(exc)
    assert not (tmp_path / "out" / "candidates_raw.csv").is_file()


def test_api_request_and_response_summaries_written(tmp_path: Path, monkeypatch) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("sector={sector} limit={limit_hint}", encoding="utf-8")
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "```csv\ninstitution_name,region,city,type,contact_email,contact_label,source_url,confidence,fit_signal\n"
        "A,RM,Santiago,universidad,a@x.cl,lab,https://x.cl/lab,high,laboratorio\n```",
        encoding="utf-8",
    )
    dnr = tmp_path / "do_not_repeat_master.csv"
    _write_csv(dnr, ["email_norm"], [])
    contacted = tmp_path / "outreach_contacted_all.csv"
    _write_csv(contacted, ["contact_email"], [])
    known = tmp_path / "all_known_marketing_contacts_dedup.csv"
    _write_csv(known, ["contact_email"], [])
    seeds = ra.SeedPaths(dnr, contacted, known)

    def _fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(args)
        if "verify_research_candidate_evidence.py" in cmd:
            out_csv = Path(args[args.index("--out-csv") + 1])
            _write_csv(
                out_csv,
                ["source_line", "institution_name", "contact_email", "source_url", "http_status", "email_visible_on_source", "relevance_keywords_present", "evidence_warning", "evidence_ok", "fetch_error"],
                [["2", "A", "a@x.cl", "https://x.cl/lab", "200", "1", "1", "", "1", ""]],
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")
        if "process_broad_marketing_contacts.py" in cmd:
            ws = Path(args[args.index("--workspace") + 1])
            _write_csv(
                ws / "send_ready_marketing.csv",
                [
                    "case_id",
                    "contact_email",
                    "email_source",
                    "institution_name",
                    "region",
                    "city",
                    "type",
                    "contact_label",
                    "source_url",
                    "confidence",
                    "fit_signal",
                    "variant_type",
                    "quality_decision",
                    "quality_reasons",
                ],
                [],
            )
            _write_csv(ws / "marketing_blocked_already_known.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_needs_manual_review.csv", ["contact_email"], [])
            _write_csv(ws / "marketing_safe_to_send.csv", ["contact_email"], [])
            (ws / "marketing_contacts_summary.json").write_text(
                json.dumps({"counts": {"send_ready_marketing": 0, "blocked": 0, "needs_manual_review": 0}}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ra, "_run_subprocess", _fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ra, "OpenAI", lambda api_key: object())
    monkeypatch.setattr(
        ra,
        "run_deep_research_response",
        lambda **kwargs: (
            json.dumps({"id": "resp_1", "model": ra.DEFAULT_HEAVY_MODEL, "status": "completed", "usage": {"total_tokens": 12}}),
            sample.read_text(encoding="utf-8"),
            {},
        ),
    )
    ra.run_research_automation(
        model=ra.DEFAULT_HEAVY_MODEL,
        prompt_file=prompt,
        out_dir=tmp_path / "out",
        sector="broad",
        limit_hint=5,
        dry_run=False,
        sample_response=None,
        seed_paths=seeds,
        use_background=False,
        app_root=Path(__file__).resolve().parents[1],
        max_candidates=200,
        max_send_ready=50,
        fail_on_over_limit=False,
        run_contacted_coverage_check=False,
        strict_contacted_coverage=False,
        research_mode="heavy",
        tpm_safe=True,
    )
    req = json.loads((tmp_path / "out" / "api_request_summary.json").read_text(encoding="utf-8"))
    resp = json.loads((tmp_path / "out" / "api_response_summary.json").read_text(encoding="utf-8"))
    assert req["selected_model"] == ra.DEFAULT_HEAVY_MODEL
    assert req["expected_model_family"] == "deep_research"
    assert req["fail_closed_validation_passed"] is True
    assert req["tpm_safe"] is True
    assert resp["actual_model"] == ra.DEFAULT_HEAVY_MODEL
    assert resp["response_id"] == "resp_1"

