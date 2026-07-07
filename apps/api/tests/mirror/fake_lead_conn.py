"""Mock Postgres connection for lead_intel mirror API tests."""

from __future__ import annotations

from typing import Any

from fake_conn import MirrorFakeConn, _FakeCursor

_PUBLIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "yahoo.com",
        "yahoo.cl",
        "yahoo.es",
        "icloud.com",
        "live.com",
    }
)


class LeadFakeConn(MirrorFakeConn):
    """Postgres fake with sample prospects in lead_intel.* tables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables[("lead_intel", "prospect")] = True
        self.tables[("lead_intel", "evidence")] = True
        self.tables[("lead_intel", "recommendation")] = True
        self.tables[("lead_intel", "block_reason")] = True
        self.tables[("outbound", "outreach_contact_state")] = True
        self.prospects: list[dict[str, Any]] = [
            {
                "prospect_key": "contacto-acme-cl",
                "organization_name": "Acme Labs",
                "contact_name": None,
                "email": "contacto@acme.cl",
                "domain": "acme.cl",
                "sector": "Laboratorios privados",
                "region": "RM",
                "buyer_type": "laboratorio_privado",
                "likely_need": "Equipos",
                "product_angle": "centrífugas",
                "evidence_url": "https://www.acme.cl/",
                "evidence_note": "Sitio oficial",
                "source": "sitio_oficial",
                "final_score": 95,
                "confidence": "alta",
                "classification": "net_new_safe_review",
                "spanish_message_angle": "Equipos de laboratorio",
                "risk_flags": "",
                "block_or_review_reason": "prospecto_nuevo_seguro",
                "recommended_next_action": "Redactar correo inicial",
                "status": "net_new_safe_review",
                "campaign_bucket": "private_lab",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "gmail-historico-cl",
                "organization_name": "Gmail Histórico Co",
                "contact_name": "Ana",
                "email": "ana@gmailhist.cl",
                "domain": "gmailhist.cl",
                "sector": "Laboratorios",
                "region": "RM",
                "buyer_type": "laboratorio_privado",
                "likely_need": None,
                "product_angle": "equipos",
                "evidence_url": None,
                "evidence_note": None,
                "source": "gmail_archive",
                "final_score": 80,
                "confidence": "alta",
                "classification": "old_gmail_prospect_review",
                "spanish_message_angle": "Presentación OrigenLab",
                "risk_flags": "",
                "block_or_review_reason": "gmail_historico",
                "recommended_next_action": "Revisar antes de presentación",
                "status": "revision_individual",
                "campaign_bucket": "private_lab",
                "is_blocked": False,
                "source_type": "gmail_historico",
                "dataset_label": "presentacion_batch1_final_send_25.csv",
                "gmail_first_contacted_at": "2024-01-15",
                "gmail_last_contacted_at": "2025-06-01",
                "gmail_sent_count": 4,
                "gmail_received_count": 1,
                "gmail_latest_subject_safe": "Consulta equipos",
            },
            {
                "prospect_key": "followup-antiguo-cl",
                "organization_name": "Follow-up Antiguo Co",
                "contact_name": None,
                "email": "old@followup.cl",
                "domain": "followup.cl",
                "sector": "Laboratorios",
                "region": "RM",
                "buyer_type": "laboratorio_privado",
                "likely_need": None,
                "product_angle": "balances",
                "evidence_url": None,
                "evidence_note": None,
                "source": "gmail_archive",
                "final_score": 75,
                "confidence": "media",
                "classification": "old_followup_review",
                "spanish_message_angle": "Seguimiento",
                "risk_flags": "",
                "block_or_review_reason": "followup_antiguo",
                "recommended_next_action": "Seguimiento personalizado",
                "status": "revision_individual",
                "campaign_bucket": "private_lab",
                "is_blocked": False,
                "source_type": "followup_antiguo",
                "dataset_label": "presentacion_batch2_followup_old_25.csv",
                "gmail_first_contacted_at": "2023-03-01",
                "gmail_last_contacted_at": "2024-11-20",
                "gmail_sent_count": 6,
                "gmail_received_count": 2,
                "gmail_latest_subject_safe": "Re: cotización",
            },
            {
                "prospect_key": "caso-activo-cl",
                "organization_name": "Caso Activo Hold",
                "contact_name": "Pedro",
                "email": "pedro@casoactivo.cl",
                "domain": "casoactivo.cl",
                "sector": None,
                "region": None,
                "buyer_type": None,
                "likely_need": None,
                "product_angle": None,
                "evidence_url": None,
                "evidence_note": None,
                "source": "warm_case_hold",
                "final_score": 0,
                "confidence": None,
                "classification": "active_case_hold",
                "spanish_message_angle": "Caso personalizado",
                "risk_flags": "",
                "block_or_review_reason": "CESMEC hold",
                "recommended_next_action": "hold_personalized_no_generic_campaign",
                "status": "hold_personalizado",
                "campaign_bucket": "active_case",
                "is_blocked": False,
                "source_type": "caso_activo",
                "dataset_label": "presentacion_hold_active_personalized.csv",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "blocked-blocked-cl",
                "organization_name": "Blocked Co",
                "contact_name": None,
                "email": "blocked@blocked.cl",
                "domain": "blocked.cl",
                "sector": None,
                "region": None,
                "buyer_type": None,
                "likely_need": None,
                "product_angle": None,
                "evidence_url": None,
                "evidence_note": "Ya contactado",
                "source": None,
                "final_score": 0,
                "confidence": None,
                "classification": "already_contacted_block",
                "spanish_message_angle": None,
                "risk_flags": "dominio_en_historial_origenlab",
                "block_or_review_reason": "email_en_lista_contactados",
                "recommended_next_action": "No contactar",
                "status": "blocked",
                "campaign_bucket": "blocked",
                "is_blocked": True,
                "source_type": "deepsearch",
                "dataset_label": None,
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "other-origenlab-cl",
                "organization_name": "Old Domain Co",
                "contact_name": None,
                "email": "other@origenlab.cl",
                "domain": "origenlab.cl",
                "sector": "Laboratorios",
                "region": "RM",
                "buyer_type": "laboratorio_privado",
                "likely_need": None,
                "product_angle": "balances",
                "evidence_url": "https://example.com/",
                "evidence_note": "Evidencia",
                "source": "test",
                "final_score": 70,
                "confidence": "alta",
                "classification": "same_domain_contacted_review",
                "spanish_message_angle": "Revisar dominio",
                "risk_flags": "dominio_en_historial_origenlab",
                "block_or_review_reason": "dominio_con_envios_previos",
                "recommended_next_action": "Revisar historial",
                "status": "same_domain_review",
                "campaign_bucket": "same_domain",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "redsalud-gob-cl",
                "organization_name": "RedSalud",
                "contact_name": "Compras",
                "email": "compras@redsalud.gob.cl",
                "domain": "redsalud.gob.cl",
                "sector": "Salud",
                "region": "RM",
                "buyer_type": "hospital_publico",
                "likely_need": "Equipamiento",
                "product_angle": "centrífugas",
                "evidence_url": "https://www.redsalud.gob.cl/",
                "evidence_note": "Red de salud",
                "source": "gmail_archive",
                "final_score": 10,
                "confidence": "alta",
                "classification": "old_gmail_prospect_review",
                "spanish_message_angle": "Seguimiento",
                "risk_flags": "",
                "block_or_review_reason": "gmail_historico",
                "recommended_next_action": "Revisar historial",
                "status": "revision_individual",
                "campaign_bucket": "hospital",
                "is_blocked": False,
                "source_type": "gmail_historico",
                "dataset_label": "contact_universe_review",
                "gmail_first_contacted_at": "2024-02-01",
                "gmail_last_contacted_at": "2025-03-01",
                "gmail_sent_count": 2,
                "gmail_received_count": 0,
                "gmail_latest_subject_safe": "Presentación equipos",
            },
            {
                "prospect_key": "hospitaldemo-cl",
                "organization_name": "Hospital Demo",
                "contact_name": None,
                "email": None,
                "domain": "hospitaldemo.cl",
                "sector": "Licitaciones",
                "region": "Valparaíso",
                "buyer_type": "public_tender",
                "likely_need": None,
                "product_angle": "incubadoras",
                "evidence_url": "https://www.mercadopublico.cl/",
                "evidence_note": "Base pública",
                "source": "mercadopublico",
                "final_score": 88,
                "confidence": "media",
                "classification": "public_tender_review",
                "spanish_message_angle": "Licitación equipamiento",
                "risk_flags": "",
                "block_or_review_reason": "licitacion_publica",
                "recommended_next_action": "Revisar bases",
                "status": "public_tender_review",
                "campaign_bucket": "public_tender",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "sidecar-exact-cl",
                "organization_name": "Sidecar Exact Co",
                "contact_name": None,
                "email": "ops@sidecar-exact.cl",
                "domain": "sidecar-exact.cl",
                "sector": "Laboratorios",
                "region": "RM",
                "buyer_type": "laboratorio_privado",
                "likely_need": None,
                "product_angle": "centrífugas",
                "evidence_url": None,
                "evidence_note": None,
                "source": "deepsearch",
                "final_score": 72,
                "confidence": "media",
                "classification": "net_new_safe_review",
                "spanish_message_angle": "Equipos",
                "risk_flags": "",
                "block_or_review_reason": "prospecto_nuevo_seguro",
                "recommended_next_action": "Revisar historial operacional",
                "status": "net_new_safe_review",
                "campaign_bucket": "private_lab",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "sidecar-domain-cl",
                "organization_name": "Institutional Domain Co",
                "contact_name": None,
                "email": "new@institutional.cl",
                "domain": "institutional.cl",
                "sector": "Salud",
                "region": "RM",
                "buyer_type": "hospital_privado",
                "likely_need": None,
                "product_angle": "equipos",
                "evidence_url": None,
                "evidence_note": None,
                "source": "deepsearch",
                "final_score": 71,
                "confidence": "media",
                "classification": "net_new_safe_review",
                "spanish_message_angle": "Equipos",
                "risk_flags": "",
                "block_or_review_reason": "prospecto_nuevo_seguro",
                "recommended_next_action": "Revisar historial operacional",
                "status": "net_new_safe_review",
                "campaign_bucket": "hospital",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "institutional-empty-cl",
                "organization_name": "Institutional Empty Email",
                "contact_name": None,
                "email": None,
                "domain": "institutional.cl",
                "sector": "Salud",
                "region": "RM",
                "buyer_type": "hospital_privado",
                "likely_need": None,
                "product_angle": "equipos",
                "evidence_url": None,
                "evidence_note": None,
                "source": "deepsearch",
                "final_score": 68,
                "confidence": "media",
                "classification": "research_only_contact_needed",
                "spanish_message_angle": "Equipos",
                "risk_flags": "sin_email_publico",
                "block_or_review_reason": "falta_contacto",
                "recommended_next_action": "Investigar contacto público",
                "status": "research_needed",
                "campaign_bucket": "hospital",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "freemail-domain-cl",
                "organization_name": "Freemail Consumer",
                "contact_name": None,
                "email": "buyer@gmail.com",
                "domain": "gmail.com",
                "sector": "Consumo",
                "region": "RM",
                "buyer_type": "consumidor",
                "likely_need": None,
                "product_angle": None,
                "evidence_url": None,
                "evidence_note": None,
                "source": "deepsearch",
                "final_score": 40,
                "confidence": "baja",
                "classification": "net_new_safe_review",
                "spanish_message_angle": None,
                "risk_flags": "",
                "block_or_review_reason": "prospecto_nuevo_seguro",
                "recommended_next_action": "Revisar",
                "status": "net_new_safe_review",
                "campaign_bucket": "private_lab",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "sidecar-followup-cl",
                "organization_name": "Sidecar Follow-up Co",
                "contact_name": None,
                "email": "followup@sidecar-followup.cl",
                "domain": "sidecar-followup.cl",
                "sector": "Laboratorios",
                "region": "RM",
                "buyer_type": "laboratorio_privado",
                "likely_need": None,
                "product_angle": "balances",
                "evidence_url": None,
                "evidence_note": None,
                "source": "deepsearch",
                "final_score": 68,
                "confidence": "media",
                "classification": "net_new_safe_review",
                "spanish_message_angle": "Seguimiento",
                "risk_flags": "",
                "block_or_review_reason": "prospecto_nuevo_seguro",
                "recommended_next_action": "Seguimiento",
                "status": "net_new_safe_review",
                "campaign_bucket": "private_lab",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
            {
                "prospect_key": "sidecar-replied-cl",
                "organization_name": "Sidecar Replied Co",
                "contact_name": None,
                "email": "active@sidecar-replied.cl",
                "domain": "sidecar-replied.cl",
                "sector": "Laboratorios",
                "region": "RM",
                "buyer_type": "laboratorio_privado",
                "likely_need": None,
                "product_angle": "equipos",
                "evidence_url": None,
                "evidence_note": None,
                "source": "deepsearch",
                "final_score": 90,
                "confidence": "alta",
                "classification": "net_new_safe_review",
                "spanish_message_angle": "Conversación activa",
                "risk_flags": "",
                "block_or_review_reason": "prospecto_nuevo_seguro",
                "recommended_next_action": "Responder",
                "status": "net_new_safe_review",
                "campaign_bucket": "private_lab",
                "is_blocked": False,
                "source_type": "deepsearch",
                "dataset_label": "phase10b_deepsearch",
                "gmail_first_contacted_at": None,
                "gmail_last_contacted_at": None,
                "gmail_sent_count": None,
                "gmail_received_count": None,
                "gmail_latest_subject_safe": None,
            },
        ]
        self.outreach_contact_state: list[dict[str, Any]] = [
            {
                "contact_email_norm": "ops@sidecar-exact.cl",
                "state": "contacted",
                "source": "mark_sent",
            },
            {
                "contact_email_norm": "peer@institutional.cl",
                "state": "contacted",
                "source": "mark_sent",
            },
            {
                "contact_email_norm": "other@gmail.com",
                "state": "contacted",
                "source": "mark_sent",
            },
            {
                "contact_email_norm": "followup@sidecar-followup.cl",
                "state": "contacted",
                "source": "mark_sent",
            },
            {
                "contact_email_norm": "active@sidecar-replied.cl",
                "state": "replied",
                "source": "mark_sent",
            },
        ]
        self.evidence = [
            {
                "prospect_key": "contacto-acme-cl",
                "evidence_kind": "public_url",
                "evidence_url": "https://www.acme.cl/",
                "evidence_note": "Sitio oficial",
                "source": "sitio_oficial",
                "confidence": "alta",
            }
        ]
        self.recommendation = {
            "prospect_key": "contacto-acme-cl",
            "campaign_bucket": "private_lab",
            "recommended_message_angle": "Equipos de laboratorio",
            "recommended_next_action": "Redactar correo inicial",
            "why_this_lead": "Equipos · centrífugas",
            "suggested_subject": "Consulta técnica — Acme Labs",
            "suggested_body_preview": "Estimados/as equipo de Acme Labs...",
            "safety_note": "Revisión humana requerida. No enviar automáticamente.",
        }
        self.block_reasons = [
            {
                "prospect_key": "blocked-blocked-cl",
                "reason_code": "already_contacted_block",
                "reason_label": "Ya contactado",
            }
        ]

    @staticmethod
    def _email_domain(email: str) -> str:
        parts = email.rsplit("@", 1)
        return parts[-1].strip().lower() if len(parts) == 2 else ""

    @classmethod
    def _is_public_domain(cls, domain: str) -> bool:
        return domain.strip().lower() in _PUBLIC_EMAIL_DOMAINS

    def _outreach_matches_prospect(
        self,
        row: dict[str, Any],
        states: frozenset[str] | set[str],
        *,
        exact: bool = True,
        same_domain: bool = True,
    ) -> bool:
        email = (row.get("email") or "").strip().lower()
        domain = (row.get("domain") or "").strip().lower()
        for ocs in self.outreach_contact_state:
            if ocs["state"] not in states:
                continue
            ocs_email = (ocs.get("contact_email_norm") or "").strip().lower()
            if exact and email and ocs_email == email:
                return True
            if same_domain and domain and not self._is_public_domain(domain):
                if self._email_domain(ocs_email) == domain:
                    return True
        return False

    def _matches_contact_scope(self, row: dict[str, Any], scope: str) -> bool:
        sent = int(row.get("gmail_sent_count") or 0)
        received = int(row.get("gmail_received_count") or 0)
        source_type = str(row.get("source_type") or "")
        if scope == "contacted":
            return (
                sent > 0
                or received > 0
                or source_type
                in (
                    "gmail_historico",
                    "followup_antiguo",
                    "caso_activo",
                    "same_domain_contacted_review",
                )
                or self._outreach_matches_prospect(row, {"contacted", "replied"})
            )
        if scope == "followup":
            gmail_followup = sent > 0 and received == 0 and not row["is_blocked"]
            outreach_followup = (
                received == 0
                and not row["is_blocked"]
                and self._outreach_matches_prospect(row, {"contacted"})
            )
            return gmail_followup or outreach_followup
        if scope == "active":
            return (
                received > 0
                or source_type == "caso_activo"
                or self._outreach_matches_prospect(row, {"replied"})
            )
        if scope == "deepsearch":
            return source_type == "deepsearch"
        if scope == "net_new":
            return (
                sent == 0
                and received == 0
                and not row["is_blocked"]
                and not self._outreach_matches_prospect(row, {"contacted", "replied"})
            )
        if scope == "blocked":
            return bool(row["is_blocked"])
        return True

    def _has_outreach_scope(self, sql: str) -> bool:
        return "outbound.outreach_contact_state" in " ".join(sql.split()).lower()

    def _skip_scope_params(self, scope: str | None, sql: str) -> int:
        if not scope:
            return 0
        has_outreach = self._has_outreach_scope(sql)
        public_n = len(_PUBLIC_EMAIL_DOMAINS)
        if scope == "contacted":
            n = 4
            if has_outreach:
                n += 2 + public_n
            return n
        if scope == "followup" and has_outreach:
            return 1 + public_n
        if scope == "active":
            n = 1
            if has_outreach:
                n += 1 + public_n
            return n
        if scope == "deepsearch":
            return 1
        if scope == "net_new" and has_outreach:
            return 2 + public_n
        return 0

    def _detect_contact_scope(self, sql: str) -> str | None:
        s = " ".join(sql.split()).lower()
        if "not exists (" in s and "outbound.outreach_contact_state" in s:
            return "net_new"
        if "coalesce(gmail_received_count, 0) > 0 or source_type = %s" in s:
            return "active"
        if (
            "coalesce(gmail_sent_count, 0) > 0" in s
            and "coalesce(gmail_received_count, 0) = 0" in s
            and "is_blocked = false" in s
            and "or exists (" in s
            and "outbound.outreach_contact_state" in s
        ):
            return "followup"
        if "or source_type in (" in s or (
            "or exists (" in s and "outbound.outreach_contact_state" in s
        ):
            return "contacted"
        if (
            "coalesce(gmail_sent_count, 0) > 0" in s
            and "coalesce(gmail_received_count, 0) = 0" in s
            and "or source_type in" not in s
        ):
            return "followup"
        if (
            "coalesce(gmail_sent_count, 0) = 0" in s
            and "coalesce(gmail_received_count, 0) = 0" in s
            and "or source_type in" not in s
            and "not exists" not in s
        ):
            return "net_new"
        where = s.split("where", 1)[-1] if "where" in s else ""
        if where.strip().startswith("source_type = %s"):
            return "deepsearch"
        if where.strip().startswith("is_blocked = true") and "coalesce(" not in where:
            return "blocked"
        return None

    def _apply_filters(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        rows = list(self.prospects)
        s = " ".join(sql.split()).lower()
        p = list(params)
        i = 0

        scope = self._detect_contact_scope(sql)
        if scope:
            rows = [r for r in rows if self._matches_contact_scope(r, scope)]
            i += self._skip_scope_params(scope, sql)

        if "is_blocked = true" in s and scope != "blocked":
            rows = [r for r in rows if r["is_blocked"]]
        elif "is_blocked = false" in s:
            rows = [r for r in rows if not r["is_blocked"]]

        if "source_type = %s" in s and scope not in {"deepsearch", "active"}:
            st = str(p[i])
            rows = [r for r in rows if r.get("source_type") == st]
            i += 1
        if "classification = %s" in s:
            cls = str(p[i])
            rows = [r for r in rows if r["classification"] == cls]
            i += 1
        if "buyer_type = %s" in s:
            bt = str(p[i])
            rows = [r for r in rows if r.get("buyer_type") == bt]
            i += 1
        if "sector ilike" in s:
            needle = str(p[i]).strip("%").lower()
            rows = [r for r in rows if needle in (r.get("sector") or "").lower()]
            i += 1
        if "region ilike" in s:
            needle = str(p[i]).strip("%").lower()
            rows = [r for r in rows if needle in (r.get("region") or "").lower()]
            i += 1
        if "organization_name ilike" in s:
            like = str(p[i]).strip("%").lower()
            rows = [
                r
                for r in rows
                if like in r["organization_name"].lower()
                or like in (r.get("email") or "").lower()
                or like in (r.get("contact_name") or "").lower()
                or like in (r.get("domain") or "").lower()
            ]
            i += 4
        return rows

    def execute(self, sql: str, params: Any = None) -> _FakeCursor:
        s = " ".join(sql.split()).lower()
        p = list(params) if params is not None else []

        if "information_schema.tables" in s:
            schema = p[0] if p else ""
            table = p[1] if len(p) > 1 else ""
            ok = self.tables.get((schema, table), False)
            return _FakeCursor([{"?": 1}] if ok else [])

        if "count(*) as c from lead_intel.prospect" in s:
            filtered = self._apply_filters(sql, p) if "where" in s else list(self.prospects)
            return _FakeCursor([{"c": len(filtered)}])

        if "from lead_intel.prospect" in s and "order by final_score" in s:
            if "where" in s:
                filter_params = p[:-1]
                limit = int(p[-1])
            else:
                filter_params = []
                limit = int(p[0])
            filtered = self._apply_filters(sql, filter_params)
            return _FakeCursor(filtered[:limit])

        if "from lead_intel.prospect" in s and "prospect_key = %s" in s:
            key = str(p[0])
            for row in self.prospects:
                if row["prospect_key"] == key:
                    return _FakeCursor([row])
            return _FakeCursor([])

        if "from lead_intel.evidence" in s:
            key = str(p[0])
            return _FakeCursor([e for e in self.evidence if e["prospect_key"] == key])

        if "from lead_intel.recommendation" in s:
            key = str(p[0])
            if self.recommendation.get("prospect_key") == key:
                return _FakeCursor([self.recommendation])
            return _FakeCursor([])

        if "from lead_intel.block_reason" in s:
            key = str(p[0])
            return _FakeCursor([b for b in self.block_reasons if b["prospect_key"] == key])

        if "from outbound.outreach_contact_state" in s:
            if "any(%s)" in s:
                norms = {str(x).strip().lower() for x in p[0]}
                return _FakeCursor(
                    [
                        r
                        for r in self.outreach_contact_state
                        if (r.get("contact_email_norm") or "").strip().lower() in norms
                    ]
                )
            return _FakeCursor(self.outreach_contact_state)

        if (
            "prospect_key, classification, status, is_blocked, source_type, email"
            in s
            and "from lead_intel.prospect" in s
            and "count(*)" not in s
        ):
            return _FakeCursor(
                [
                    {
                        "prospect_key": r["prospect_key"],
                        "classification": r["classification"],
                        "status": r["status"],
                        "is_blocked": r["is_blocked"],
                        "source_type": r.get("source_type"),
                        "email": r.get("email"),
                        "domain": r.get("domain"),
                        "gmail_sent_count": r.get("gmail_sent_count"),
                        "gmail_received_count": r.get("gmail_received_count"),
                        "gmail_last_contacted_at": r.get("gmail_last_contacted_at"),
                    }
                    for r in self.prospects
                ]
            )

        if "count(*) as total" in s and "filter (where" in s:
            total = len(self.prospects)
            review = sum(1 for r in self.prospects if not r["is_blocked"])
            blocked = total - review
            return _FakeCursor(
                [
                    {
                        "total": total,
                        "review_count": review,
                        "blocked_count": blocked,
                        "net_new_safe": sum(
                            1 for r in self.prospects if r["classification"] == "net_new_safe_review"
                        ),
                        "public_tender_review": sum(
                            1
                            for r in self.prospects
                            if r["classification"] == "public_tender_review"
                        ),
                        "same_domain_review": sum(
                            1
                            for r in self.prospects
                            if r["classification"] == "same_domain_contacted_review"
                        ),
                        "research_needed": sum(
                            1
                            for r in self.prospects
                            if r["classification"] == "research_only_contact_needed"
                        ),
                        "gmail_historico": sum(
                            1
                            for r in self.prospects
                            if r.get("source_type") == "gmail_historico" and not r["is_blocked"]
                        ),
                        "followup_antiguo": sum(
                            1
                            for r in self.prospects
                            if r.get("source_type") == "followup_antiguo" and not r["is_blocked"]
                        ),
                        "caso_activo": sum(
                            1 for r in self.prospects if r.get("source_type") == "caso_activo"
                        ),
                    }
                ]
            )

        return super().execute(sql, params)
