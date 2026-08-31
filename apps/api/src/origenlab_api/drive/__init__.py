"""Google Drive quote-workspace boundary (CRM-Q1).

Everything Drive-specific lives behind ``QuoteDriveWorkspaceProvider``.
Durable CRM code never imports Google specifics; tests use deterministic
fakes and never touch the network.
"""
