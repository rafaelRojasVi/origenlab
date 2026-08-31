"""Redacted Drive provisioning errors.

A ``DriveProvisioningError`` carries only a safe category slug. Provider
payloads, exception details, URLs, hostnames, and credentials must never be
attached: the category is what reaches durable state and API responses.
"""

from __future__ import annotations


# The full closed set of persistable failure categories.
DRIVE_FAILURE_CATEGORIES = frozenset(
    {
        "drive_not_configured",
        "drive_auth_mode_not_configured",
        "drive_auth_mode_incompatible",
        "drive_credentials_not_configured",
        "drive_dependency_missing",
        "drive_root_invalid",
        "drive_template_invalid",
        "drive_timeout",
        "drive_unavailable",
        "drive_permission_denied",
        "drive_not_found",
        "drive_error",
    }
)


class DriveProvisioningError(RuntimeError):
    """Drive provisioning failed with a redacted category."""

    def __init__(self, category: str) -> None:
        if category not in DRIVE_FAILURE_CATEGORIES:
            category = "drive_error"

        super().__init__(category)
        self.category = category
