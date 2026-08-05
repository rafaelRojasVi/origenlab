"""PR5D — procurement product relevance (deterministic, explainable, no persistence)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    FORBIDDEN_CLI_FLAGS,
    PRODUCT_RELEVANCE_PLANNER_VERSION,
    PRODUCT_RELEVANCE_RULES_VERSION,
    RELEVANCE_CLASSES_V1,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.field_sufficiency import (
    field_sufficiency_document,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
    build_product_relevance_plan,
    write_relevance_outputs,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

__all__ = [
    "FORBIDDEN_CLI_FLAGS",
    "PRODUCT_RELEVANCE_PLANNER_VERSION",
    "PRODUCT_RELEVANCE_RULES_VERSION",
    "RELEVANCE_CLASSES_V1",
    "build_product_relevance_plan",
    "classify_product_text_unit",
    "field_sufficiency_document",
    "write_relevance_outputs",
]
