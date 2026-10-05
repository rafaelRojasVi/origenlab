"""The one refusal every configuration check raises: a code, never a value."""

from __future__ import annotations


class ConfigRefused(ValueError):
    """The worker's configuration is unsafe or incomplete; nothing was opened.

    `code` is a short, fixed identifier (`database_url_has_options`, `storage_endpoint_not_https`)
    that the run log prints. It never carries a value taken from the environment.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
