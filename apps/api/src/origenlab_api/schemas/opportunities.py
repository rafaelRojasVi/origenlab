"""Equipment-first operator opportunities (read-only read model)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EquipmentOpportunitiesMeta(BaseModel):
    data_source: Literal["active_current_csv", "postgres_mirror"] = "active_current_csv"
    read_only: bool = True
    count: int = 0
    source_path: str = ""
    source_path_info: dict[str, Any] | None = None
    source_kind: str = ""
    artifact_basename: str = ""
    canonical_reason: str = ""
    campaign_mode: str | None = None
    reduced_mode: bool = False
    note: str = ""


class EquipmentAnexoItem(BaseModel):
    nombre: str = ""
    tipo: str = ""
    descripcion: str = ""
    tamano: str = ""
    fecha_adjunto: str = ""
    url: str = ""


class EquipmentOpportunityItem(BaseModel):
    opportunity_key: str = ""
    priority_rank: int
    codigo_licitacion: str = ""
    buyer: str = ""
    region: str = ""
    close_date: str = ""
    equipment_category: str = ""
    item_description: str = ""
    next_action: str = ""
    safe_channel: str = ""
    supplier_needed: str = ""
    contact_status: str = ""
    contact_email: str = ""
    operator_note: str = ""
    fecha_publicacion: str = ""
    close_at: str = ""
    validity_status: str = ""
    chilecompra_status: str = ""
    chilecompra_status_code: str = ""
    api_checked_at_utc: str = ""
    source: str = ""
    mercado_publico_url: str = ""
    title: str = ""
    unspsc_code: str = ""
    unidad: str = ""
    cantidad: str = ""
    producto: str = ""
    nivel_1: str = ""
    nivel_2: str = ""
    nivel_3: str = ""
    anexos: list[EquipmentAnexoItem] = Field(default_factory=list)


class EquipmentOpportunitiesResponse(BaseModel):
    meta: EquipmentOpportunitiesMeta
    items: list[EquipmentOpportunityItem] = Field(default_factory=list)
