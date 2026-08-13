"""Tests for ChileCompra API → equipment-first queue integration (mocked HTTP)."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from origenlab_email_pipeline.chilecompra_api import (
    ChileCompraHttpError,
    ChileCompraTicketMissingError,
    VALIDITY_STATUS_NOT_PUBLICADA,
    VALIDITY_STATUS_OPEN,
)
from origenlab_email_pipeline.equipment_first_chilecompra_queue import (
    CHILECOMPRA_QUEUE_FIELDS,
    CANDIDATE_AUDIT_FIELDS,
    build_equipment_queue_from_chilecompra_api,
    read_detail_cache,
    summary_passes_keyword_prefilter,
    write_candidate_audit_csv,
    write_chilecompra_api_queue_outputs,
)
from origenlab_email_pipeline.equipment_first_licitacion_queue import (
    build_equipment_queue_rows_from_normalized_rows,
)

_SECRET_TICKET = "00000000-0000-0000-0000-000000000099"
_T0 = datetime(2026, 6, 14, 12, 0, 0)


def _summary_row(
    *,
    codigo: str,
    title: str,
    descripcion: str = "",
) -> dict[str, str]:
    return {
        "codigo": codigo,
        "tipo_licitacion": "LP",
        "title": title,
        "descripcion": descripcion,
        "buyer": "Hospital Demo",
        "region": "Región Metropolitana",
        "fecha_publicacion": "01/06/2026 10:00:00",
        "close_date": "20/06/2026 17:00:00",
        "line_description": "",
        "unspsc_code": "",
        "unidad": "",
        "cantidad": "",
        "producto": "",
        "nivel_1": "",
        "nivel_2": "",
        "nivel_3": "",
        "chilecompra_status_code": "5",
        "chilecompra_status": "Publicada",
    }


def test_summary_keyword_prefilter_accepts_equipment_terms() -> None:
    row = _summary_row(codigo="1-1-LP26", title="Adquisición centrifuga laboratorio")
    assert summary_passes_keyword_prefilter(row) is True
    assert (
        summary_passes_keyword_prefilter(
            _summary_row(codigo="2-2-LP26", title="Pavimentación calle principal")
        )
        is False
    )


def test_summary_keyword_prefilter_accepts_bascula_and_vortex_terms() -> None:
    assert summary_passes_keyword_prefilter(
        _summary_row(
            codigo="2981-205-LE26",
            title="Básculas analíticas de precisión",
        )
    )
    assert summary_passes_keyword_prefilter(
        _summary_row(
            codigo="1947-146-LE26",
            title="Adquisición agitador vórtex",
        )
    )


def test_build_equipment_queue_from_normalized_rows_centrifuge() -> None:
    rows = [
        {
            **_summary_row(codigo="1057898-51-LP26", title="CENTRIFUGAS UMT"),
            "line_description": "Bombas centrífugas de laboratorio",
            "producto": "Centrifuga refrigerada",
            "nivel_1": "Equipamiento para laboratorios",
        }
    ]
    out = build_equipment_queue_rows_from_normalized_rows(rows, now=_T0)
    assert len(out) == 1
    assert out[0]["codigo_licitacion"] == "1057898-51-LP26"
    assert out[0]["equipment_category"] == "centrifuge"
    assert out[0]["next_action"] == "quote_now"


def test_build_equipment_queue_from_normalized_rows_balance() -> None:
    rows = [
        {
            **_summary_row(codigo="2000-1-LP26", title="Balanza analítica laboratorio"),
            "line_description": "Balanza analítica 0.1 mg para laboratorio clínico",
            "producto": "Balanza",
        }
    ]
    out = build_equipment_queue_rows_from_normalized_rows(rows, now=_T0)
    assert out[0]["equipment_category"] == "balance"


def test_build_equipment_queue_from_normalized_rows_sonicator() -> None:
    rows = [
        {
            **_summary_row(codigo="3000-1-LP26", title="Sonicador laboratorio"),
            "line_description": "Procesador ultrasónico para laboratorio",
            "producto": "Sonicador",
        }
    ]
    out = build_equipment_queue_rows_from_normalized_rows(rows, now=_T0)
    assert out[0]["equipment_category"] == "lab_ultrasonic_processor"


def test_build_equipment_queue_from_normalized_rows_excludes_consumables_only() -> None:
    rows = [
        {
            **_summary_row(codigo="1497-6-LE26", title="INSUMOS PARA ANALISIS"),
            "line_description": "Reactivos microbiológicos y medios de cultivo",
            "producto": "Reactivos",
        }
    ]
    assert build_equipment_queue_rows_from_normalized_rows(rows, now=_T0) == []


def test_build_equipment_queue_from_chilecompra_api_mocked(tmp_path: Path) -> None:
    summaries = {
        "Listado": [
            {
                "CodigoExterno": "1051-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Descripcion": "Equipos para laboratorio clínico",
                "Comprador": {"NombreOrganismo": "Hospital Demo", "Region": "RM"},
                "FechaCierre": "20/06/2026 17:00:00",
                "CodigoEstado": "5",
                "Estado": "Publicada",
            },
            {
                "CodigoExterno": "9000-1-LP26",
                "Nombre": "Pavimentación vial urbana",
                "Descripcion": "Obras viales",
            },
        ]
    }
    detail = {
        "Listado": [
            {
                "CodigoExterno": "1051-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Comprador": {"NombreOrganismo": "Hospital Demo", "Region": "RM"},
                "FechaCierre": "20/06/2026 17:00:00",
                "Items": {
                    "Listado": [
                        {
                            "Descripcion": "Centrifuga refrigerada para laboratorio clínico",
                            "NombreProducto": "Centrifuga",
                            "Cantidad": "2",
                        }
                    ]
                },
                "Anexos": {
                    "Listado": [
                        {
                            "Nombre": "Bases técnicas.pdf",
                            "Tipo": "Bases",
                            "Url": "https://www.mercadopublico.cl/archivos/bases.pdf",
                        }
                    ]
                },
            }
        ]
    }

    fetch_list = MagicMock(return_value=summaries)
    fetch_detail = MagicMock(return_value=detail)

    rows, manifest, _audit = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=100,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    assert manifest["fetched_summaries"] == 2
    assert manifest["candidate_summaries"] == 1
    assert manifest["detail_requests"] == 1
    assert manifest["output_rows"] == 1
    fetch_list.assert_called_once()
    fetch_detail.assert_called_once_with("1051-1-LP26", ticket=_SECRET_TICKET)
    assert rows[0]["equipment_category"] == "centrifuge"
    assert rows[0]["close_date"] == "20/06/2026 17:00:00"
    assert rows[0]["validity_status"] == VALIDITY_STATUS_OPEN
    assert rows[0]["chilecompra_status_code"] == "5"
    assert "source:chilecompra_api" in rows[0]["reason"]
    anexos = json.loads(rows[0]["anexos_json"])
    assert anexos[0]["nombre"] == "Bases técnicas.pdf"
    assert "mercadopublico.cl" in anexos[0]["url"]


def test_summary_prefilter_skips_are_counted_and_audited() -> None:
    summaries = {
        "Listado": [
            {
                "CodigoExterno": "1051-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Descripcion": "Equipos para laboratorio clínico",
                "Comprador": {"NombreOrganismo": "Hospital Demo", "Region": "RM"},
                "FechaCierre": "20/06/2026 17:00:00",
            },
            {
                "CodigoExterno": "9000-1-LP26",
                "Nombre": "Pavimentación vial urbana",
                "Descripcion": "Obras viales",
                "Comprador": {"NombreOrganismo": "Municipalidad Demo", "Region": "RM"},
                "FechaCierre": "21/06/2026 17:00:00",
            },
        ]
    }
    fetch_list = MagicMock(return_value=summaries)
    fetch_detail = MagicMock(
        side_effect=lambda codigo, ticket: _equipment_detail_payload(codigo)
    )

    _rows, manifest, audit_rows = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=10,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    assert manifest["fetched_summaries"] == 2
    assert manifest["candidate_summaries"] == 1
    assert manifest["prefilter_skipped_summaries"] == 1
    fetch_detail.assert_called_once_with("1051-1-LP26", ticket=_SECRET_TICKET)

    by_codigo = {row["codigo"]: row for row in audit_rows}
    assert by_codigo["1051-1-LP26"]["prefilter_match"] == "true"
    assert by_codigo["1051-1-LP26"]["detail_requested"] == "true"
    assert by_codigo["9000-1-LP26"]["prefilter_match"] == "false"
    assert by_codigo["9000-1-LP26"]["detail_requested"] == "false"
    assert by_codigo["9000-1-LP26"]["reject_reason"] == "summary_prefilter_no_match"


def test_build_equipment_queue_respects_max_details() -> None:
    summaries = {
        "Listado": [
            {
                "CodigoExterno": f"{index}-1-LP26",
                "Nombre": f"Adquisición centrifuga laboratorio {index}",
                "Descripcion": "Equipos laboratorio",
            }
            for index in range(5)
        ]
    }
    fetch_list = MagicMock(return_value=summaries)
    fetch_detail = MagicMock(
        return_value={
            "Listado": [
                {
                    "CodigoExterno": "x",
                    "Nombre": "Adquisición centrifuga laboratorio",
                    "Items": {
                        "Descripcion": "Centrifuga refrigerada laboratorio clínico",
                    },
                }
            ]
        }
    )

    _rows, manifest, _audit = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=2,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    assert manifest["candidate_summaries"] == 5
    assert manifest["detail_requests"] == 2
    assert fetch_detail.call_count == 2


def test_write_chilecompra_api_queue_outputs_csv_fields(tmp_path: Path) -> None:
    rows = [
        {
            "codigo_licitacion": "1051-1-LP26",
            "buyer": "Hospital Demo",
            "region": "RM",
            "close_date": "20/06/2026 17:00:00",
            "title": "Centrifuga",
            "item_description": "Centrifuga refrigerada",
            "equipment_category": "centrifuge",
            "fit_score": "75",
            "reason": "source:chilecompra_api; equipment:centrifuge",
            "next_action": "quote_now",
            "api_checked_at_utc": _T0.isoformat(),
            "validity_status": VALIDITY_STATUS_OPEN,
            "chilecompra_status_code": "5",
            "chilecompra_status": "Publicada",
            "source": "chilecompra_api",
        }
    ]
    out_csv = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260614.csv"
    manifest = {
        "source": "chilecompra_api",
        "generated_at_utc": _T0.isoformat(),
        "fetched_summaries": 1,
        "candidate_summaries": 1,
        "detail_requests": 1,
        "normalized_item_rows": 1,
        "output_rows": 1,
        "by_next_action": {"quote_now": 1},
    }
    stats = write_chilecompra_api_queue_outputs(
        rows=rows,
        manifest=manifest,
        out_csv=out_csv,
    )

    assert out_csv.is_file()
    assert Path(stats["manifest_path"]).is_file()
    with out_csv.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(CHILECOMPRA_QUEUE_FIELDS)
        row = next(reader)
        assert row["codigo_licitacion"] == "1051-1-LP26"
        assert row["close_date"] == "20/06/2026 17:00:00"
        assert row["validity_status"] == VALIDITY_STATUS_OPEN
        assert "source:chilecompra_api" in row["reason"]

    manifest_data = json.loads(Path(stats["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest_data["output_rows"] == 1
    assert manifest_data["by_next_action"]["quote_now"] == 1


def test_build_equipment_queue_missing_ticket_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHILECOMPRA_API_TICKET", raising=False)
    with pytest.raises(ChileCompraTicketMissingError, match="CHILECOMPRA_API_TICKET"):
        build_equipment_queue_from_chilecompra_api(
            fetch_licitaciones_fn=MagicMock(),
            fetch_licitacion_by_codigo_fn=MagicMock(),
        )


def _equipment_summaries(count: int) -> dict[str, list[dict[str, str]]]:
    return {
        "Listado": [
            {
                "CodigoExterno": f"{index}-1-LP26",
                "Nombre": f"Adquisición centrifuga laboratorio {index}",
                "Descripcion": "Equipos laboratorio",
            }
            for index in range(1, count + 1)
        ]
    }


def _equipment_detail_payload(codigo: str) -> dict[str, list[dict[str, object]]]:
    return {
        "Listado": [
            {
                "CodigoExterno": codigo,
                "Nombre": "Adquisición centrifuga laboratorio",
                "Items": {
                    "Descripcion": "Centrifuga refrigerada laboratorio clínico",
                },
            }
        ]
    }


def test_detail_lookups_sleep_between_requests() -> None:
    fetch_list = MagicMock(return_value=_equipment_summaries(3))
    fetch_detail = MagicMock(
        side_effect=lambda codigo, ticket: _equipment_detail_payload(codigo)
    )
    sleep_fn = MagicMock()

    build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=3,
        detail_sleep_seconds=1.5,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
        sleep_fn=sleep_fn,
    )

    assert fetch_detail.call_count == 3
    assert sleep_fn.call_count == 2
    assert sleep_fn.call_args_list == [call(1.5), call(1.5)]


def test_http_429_on_detail_lookup_records_error_and_continues() -> None:
    fetch_list = MagicMock(return_value=_equipment_summaries(2))

    def _detail_side_effect(codigo: str, ticket: str) -> dict[str, object]:
        if codigo == "1-1-LP26":
            raise ChileCompraHttpError(
                f"HTTP 429 while fetching https://api.mercadopublico.cl/?ticket={ticket}"
            )
        return _equipment_detail_payload(codigo)

    fetch_detail = MagicMock(side_effect=_detail_side_effect)

    rows, manifest, _audit = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=2,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    assert manifest["detail_error_count"] == 1
    assert manifest["detail_error_codes"] == ["1-1-LP26"]
    assert manifest["detail_requests"] == 2
    assert manifest["detail_errors"][0]["codigo"] == "1-1-LP26"
    manifest_blob = json.dumps(manifest)
    assert _SECRET_TICKET not in manifest_blob
    assert "<redacted>" in manifest["detail_errors"][0]["error"]
    assert rows


def test_fail_fast_detail_errors_reraises() -> None:
    fetch_list = MagicMock(return_value=_equipment_summaries(1))
    fetch_detail = MagicMock(
        side_effect=ChileCompraHttpError("HTTP 429 while fetching https://example.test")
    )

    with pytest.raises(ChileCompraHttpError, match="HTTP 429"):
        build_equipment_queue_from_chilecompra_api(
            ticket=_SECRET_TICKET,
            max_details=1,
            continue_on_detail_error=False,
            now=_T0,
            fetch_licitaciones_fn=fetch_list,
            fetch_licitacion_by_codigo_fn=fetch_detail,
        )


def test_max_details_zero_skips_detail_lookups() -> None:
    fetch_list = MagicMock(return_value=_equipment_summaries(3))
    fetch_detail = MagicMock()

    _rows, manifest, _audit = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=0,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    fetch_detail.assert_not_called()
    assert manifest["detail_requests"] == 0
    assert manifest["detail_error_count"] == 0


def test_detail_cache_hit_avoids_fetch_detail(tmp_path: Path) -> None:
    cache_dir = tmp_path / "chilecompra_detail_cache"
    codigo = "1-1-LP26"
    cached_payload = _equipment_detail_payload(codigo)
    from origenlab_email_pipeline.equipment_first_chilecompra_queue import (
        write_detail_cache,
    )

    write_detail_cache(cache_dir, codigo, cached_payload)

    fetch_list = MagicMock(return_value=_equipment_summaries(1))
    fetch_detail = MagicMock()

    rows, manifest, audit_rows = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=1,
        detail_cache_dir=cache_dir,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    fetch_detail.assert_not_called()
    assert manifest["detail_cache_hits"] == 1
    assert manifest["detail_cache_writes"] == 0
    assert manifest["detail_requests"] == 0
    assert audit_rows[0]["detail_cache_hit"] == "true"
    assert rows


def test_successful_detail_fetch_writes_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "chilecompra_detail_cache"
    codigo = "1-1-LP26"
    fetch_list = MagicMock(return_value=_equipment_summaries(1))
    fetch_detail = MagicMock(
        side_effect=lambda requested_codigo, ticket: _equipment_detail_payload(
            requested_codigo
        )
    )

    _rows, manifest, _audit = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=1,
        detail_cache_dir=cache_dir,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    assert manifest["detail_cache_writes"] == 1
    cached = read_detail_cache(cache_dir, codigo)
    assert cached is not None
    cache_text = (cache_dir / "1-1-LP26.json").read_text(encoding="utf-8")
    assert _SECRET_TICKET not in cache_text


def test_candidate_audit_includes_matched_and_rejected_rows(tmp_path: Path) -> None:
    summaries = {
        "Listado": [
            {
                "CodigoExterno": "1051-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Descripcion": "Equipos laboratorio",
                "Comprador": {"NombreOrganismo": "Hospital Demo", "Region": "RM"},
                "FechaCierre": "20/06/2026 17:00:00",
                "CodigoEstado": "5",
                "Estado": "Publicada",
            },
            {
                "CodigoExterno": "2000-1-LP26",
                "Nombre": "Adquisición insumos laboratorio",
                "Descripcion": "Equipos para laboratorio",
                "Comprador": {"NombreOrganismo": "Universidad Demo", "Region": "RM"},
                "FechaCierre": "21/06/2026 17:00:00",
            },
            {
                "CodigoExterno": "3000-1-LP26",
                "Nombre": "Adquisición reactivos laboratorio",
                "Descripcion": "Insumos microbiológicos",
                "Comprador": {"NombreOrganismo": "SEREMI Demo", "Region": "RM"},
                "FechaCierre": "22/06/2026 17:00:00",
            },
        ]
    }

    def _detail_side_effect(codigo: str, ticket: str) -> dict[str, object]:
        if codigo == "1051-1-LP26":
            return _equipment_detail_payload(codigo)
        if codigo == "2000-1-LP26":
            return {
                "Listado": [
                    {
                        "CodigoExterno": codigo,
                        "Nombre": "Adquisición insumos laboratorio",
                        "Items": {
                            "Descripcion": "Papel bond y carpetas administrativas"
                        },
                    }
                ]
            }
        return _equipment_detail_payload(codigo)

    fetch_list = MagicMock(return_value=summaries)
    fetch_detail = MagicMock(side_effect=_detail_side_effect)

    rows, manifest, audit_rows = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=2,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    audit_path = tmp_path / "chilecompra_equipment_candidate_audit_20260614.csv"
    write_candidate_audit_csv(audit_rows, audit_path)
    audit_text = audit_path.read_text(encoding="utf-8")
    assert _SECRET_TICKET not in audit_text

    with audit_path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(CANDIDATE_AUDIT_FIELDS)
        by_codigo = {row["codigo"]: row for row in reader}

    assert by_codigo["1051-1-LP26"]["detected_output_rows"] == "1"
    assert by_codigo["1051-1-LP26"]["reject_reason"] == ""
    assert (
        by_codigo["2000-1-LP26"]["reject_reason"] == "no_equipment_match_after_detail"
    )
    assert by_codigo["3000-1-LP26"]["reject_reason"] == "not_detailed_max_details"
    assert manifest["output_rows"] == len(rows)


def test_detail_lookup_preserves_summary_close_date_when_detail_missing() -> None:
    summaries = {
        "Listado": [
            {
                "CodigoExterno": "1051-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Descripcion": "Equipos para laboratorio clínico",
                "Comprador": {"NombreOrganismo": "Hospital Demo", "Region": "RM"},
                "FechaCierre": "20/06/2026 17:00:00",
                "CodigoEstado": "5",
                "Estado": "Publicada",
            }
        ]
    }
    detail = {
        "Listado": [
            {
                "CodigoExterno": "1051-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Comprador": {"NombreOrganismo": "Hospital Demo", "Region": "RM"},
                "Items": {
                    "Listado": [
                        {
                            "Descripcion": "Centrifuga refrigerada para laboratorio clínico",
                            "NombreProducto": "Centrifuga",
                        }
                    ]
                },
            }
        ]
    }
    fetch_list = MagicMock(return_value=summaries)
    fetch_detail = MagicMock(return_value=detail)

    rows, _manifest, _audit = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=1,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    assert rows[0]["close_date"] == "20/06/2026 17:00:00"
    assert rows[0]["validity_status"] == VALIDITY_STATUS_OPEN


def test_closed_status_gets_validity_not_publicada_in_queue() -> None:
    summaries = {
        "Listado": [
            {
                "CodigoExterno": "5000-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Descripcion": "Equipos laboratorio",
                "FechaCierre": "20/06/2026 17:00:00",
                "CodigoEstado": "6",
                "Estado": "Cerrada",
            }
        ]
    }
    detail = {
        "Listado": [
            {
                "CodigoExterno": "5000-1-LP26",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Items": {
                    "Descripcion": "Centrifuga refrigerada laboratorio clínico",
                },
            }
        ]
    }
    fetch_list = MagicMock(return_value=summaries)
    fetch_detail = MagicMock(return_value=detail)

    rows, manifest, _audit = build_equipment_queue_from_chilecompra_api(
        ticket=_SECRET_TICKET,
        max_details=1,
        now=_T0,
        fetch_licitaciones_fn=fetch_list,
        fetch_licitacion_by_codigo_fn=fetch_detail,
    )

    assert rows[0]["validity_status"] == VALIDITY_STATUS_NOT_PUBLICADA
    assert manifest["by_validity_status"][VALIDITY_STATUS_NOT_PUBLICADA] == 1
