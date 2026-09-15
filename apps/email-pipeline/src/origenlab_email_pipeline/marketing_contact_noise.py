"""Heuristics to skip obvious non-prospect rows when building cold-outreach lists.

``contact_master`` reflects the email archive: it legitimately contains
transactional / platform / carrier addresses. Do not delete those rows from the
mart; filter them when exporting marketing batches.

``strict_contact_graph`` tightens rules for mail-graph exports (``contact_master``):
extra machine-style locals (e.g. ``reply@``) that can appear on legitimate-looking
domains in the archive. The lead path typically keeps ``strict_contact_graph=False``.
"""

from __future__ import annotations

# Domains that are almost never a human commercial buyer for lab outreach.
_NOISE_DOMAINS: frozenset[str] = frozenset(
    {
        "mercadopublico.cl",
        "chilecompra.cl",
        "dhl.com",
        # Banking transactional senders (archive noise for outbound prospecting context).
        "santander.cl",
        "itau.cl",
        "bancoitau.cl",
        "wherex.com",
        "facebookmail.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "google.com",
        "microsoft.com",
        "mailchimp.com",
        "sendgrid.net",
        "mandrillapp.com",
        "mailgun.org",
        "amazonses.com",
        "postmarkapp.com",
        # Newsletter / marketing automation senders (when the From is on these domains).
        "substack.com",
        "substack.email",
        "beehiiv.com",
        "convertkit.com",
        "klaviyo.com",
        "e.klaviyo.com",
        "customeriomail.com",
        "hubspotemail.net",
        "rsgsv.net",
        "createsend.com",
        "campaign-archive.com",
        # Observed Campaign/ESP relay false-positive in Sept 2026 campaign.
        "cccampaigns.net",
        "list-manage.com",
        # B2B / vendor media and newsletter platforms (archive junk, not prospect mailboxes).
        "labx.com",
        "biocompare.com",
        "globalspec.com",
        "engineering360.com",
        "thomasnet.com",
        "scienceconnect.com",
        # Marketplace / promo senders (audit: mailer.* / mkt.* / notify.* on these registrable domains).
        # FP: a human buyer who only ever uses a marketplace relay address is rare for OrigenLab cold lists.
        "solostocks.com",
        "solostocks.cl",
        # B2B “market research” / newsletter domains (audit: reports@ / info@ promos). FP: a real buyer
        # employed there contacting you directly is possible but uncommon vs newsletter traffic.
        "leadingmarketresearch.com",
        # Vendor media newsletter (audit: news@… promos). FP: editorial staff as a named prospect—low vs blast news@.
        "rapidmicrobiology.com",
    }
)

# If local-part looks like an automated mailbox, skip (unless you later narrow the list).
_NOISE_LOCAL_PREFIXES: tuple[str, ...] = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "mailer-daemon",
    "postmaster",
    "bounce",
    "bounces",
    "notificacion",
    "notificaciones",
    "notification",
    "notifications",
    "avisos",
    "alerts",
    "invitations",
    "invite",
    "system",
    "newsletter",
    "newsletters",
    "unsub",
    "unsubscribe",
    "mailing",
    "digest",
    "promo",
    "promos",
    "campaign",
    "boletin",
    "boletines",
    "promociones",
    "updates",
    "marketing",
    # Chile procurement ecosystem: automated / role mailboxes on non-mercadopublico.cl hosts (audit).
    # FP: a mailbox literally named mercadopublico@ at a real org (very rare).
    "mercadopublico",
)


def _marketing_outreach_noise_contact_graph_strict(email: str) -> bool:
    """Extra exclusions for ``contact_master`` path only (mail graph is noisier)."""
    raw = (email or "").strip().lower()
    if not raw or "@" not in raw:
        return False
    local, _, _domain = raw.partition("@")
    local = local.strip()
    if not local:
        return False
    # Vendor / ESP style one-word locals (high false-positive risk on lead path; OK on mail graph).
    if local == "reply" or local.startswith("reply+") or local.startswith("reply."):
        return True
    return False


def marketing_outreach_noise_email(email: str, *, strict_contact_graph: bool = False) -> bool:
    """True if ``email`` should be excluded from cold marketing exports.

    When ``strict_contact_graph`` is True (``contact_master`` exports), apply
    additional machine-local heuristics on top of the shared domain/local lists.
    """
    raw = (email or "").strip().lower()
    if not raw or "@" not in raw:
        return True
    local, _, domain = raw.partition("@")
    local = local.strip()
    domain = domain.strip().lower()
    if not local or not domain:
        return True
    if domain in _NOISE_DOMAINS:
        return True
    for d in _NOISE_DOMAINS:
        if domain.endswith("." + d) or domain == d:
            return True
    for p in _NOISE_LOCAL_PREFIXES:
        if local == p or local.startswith(p + ".") or local.startswith(p + "+"):
            return True
    if strict_contact_graph and _marketing_outreach_noise_contact_graph_strict(raw):
        return True
    return False


def marketing_outreach_noise_organization_guess(name: str | None) -> bool:
    """True if organization_name_guess clearly matches a known noise source."""
    n = (name or "").strip().lower()
    if not n:
        return False
    noise_names = (
        "mercadopublico",
        "mercado publico",
        "dhl",
        "wherex",
        "facebookmail",
        "twitter",
        "linkedin",
        "biocompare",
        "globalspec",
        "engineering360",
        "labx",
        "thomasnet",
        "scienceconnect",
        "solostocks",
        "leadingmarketresearch",
        "rapidmicrobiology",
        "santander",
        "itau",
        "banco itau",
    )
    return any(x in n for x in noise_names)
