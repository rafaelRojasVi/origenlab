from origenlab_email_pipeline.marketing_contact_noise import (
    marketing_outreach_noise_email,
    marketing_outreach_noise_organization_guess,
)


def test_newsletter_patterns() -> None:
    assert marketing_outreach_noise_email("newsletter@universidad.cl")
    assert marketing_outreach_noise_email("hello@newsletter.substack.com")
    assert marketing_outreach_noise_email("updates@convertkit.com")


def test_noise_domains_and_locals() -> None:
    assert marketing_outreach_noise_email("avisos@mercadopublico.cl")
    assert marketing_outreach_noise_email("cl.notificacion@dhl.com")
    # LinkedIn domain block (audit: platform noreply groups)
    assert marketing_outreach_noise_email("groups-noreply@linkedin.com")
    assert marketing_outreach_noise_email("system@wherex.com")
    assert marketing_outreach_noise_email("c6@facebookmail.com")
    assert marketing_outreach_noise_email("info@twitter.com")
    assert marketing_outreach_noise_email("invitations@linkedin.com")
    assert marketing_outreach_noise_email("support@dhl.com")
    assert marketing_outreach_noise_email("mensajeria@santander.cl")
    assert marketing_outreach_noise_email("alertas@itau.cl")
    assert marketing_outreach_noise_email("noreply@bancoitau.cl")


def test_not_noise_typical_lab_contact() -> None:
    assert not marketing_outreach_noise_email("compras@hospital.cl")
    assert not marketing_outreach_noise_email("laboratorio@universidad.cl")
    # Institutional / procurement-style (must stay eligible for cold gate noise check)
    assert not marketing_outreach_noise_email("adquisiciones@hospitalregional.cl")
    assert not marketing_outreach_noise_email("licitaciones@universidad.cl")
    assert not marketing_outreach_noise_email("contacto.investigacion@instituto.cl")


def test_audit_regression_vendor_newsletter_domains() -> None:
    """Real audit rows that were eligible before expanding vendor/newsletter coverage."""
    assert marketing_outreach_noise_email("newsletters@labx.com")
    assert marketing_outreach_noise_email("reply@email.engineering360.com")
    assert marketing_outreach_noise_email("do-not-reply@email.globalspec.com")
    assert marketing_outreach_noise_email("newsletters@biocompare.com")


def test_newsletters_plural_local_part() -> None:
    assert marketing_outreach_noise_email("newsletters@cliente.cl")


def test_do_not_reply_hyphenated_local() -> None:
    assert marketing_outreach_noise_email("do-not-reply@cliente.cl")


def test_boletin_promociones_locals() -> None:
    assert marketing_outreach_noise_email("boletin@empresa.cl")
    assert marketing_outreach_noise_email("promociones@retail.cl")


def test_marketing_local_part() -> None:
    assert marketing_outreach_noise_email("marketing@agency.com")


def test_strict_contact_graph_reply_local() -> None:
    assert not marketing_outreach_noise_email("reply@cliente.cl", strict_contact_graph=False)
    assert marketing_outreach_noise_email("reply@cliente.cl", strict_contact_graph=True)
    assert marketing_outreach_noise_email("reply+campaign@vendor.com", strict_contact_graph=True)


def test_noise_org_guess() -> None:
    assert marketing_outreach_noise_organization_guess("Mercadopublico")
    assert not marketing_outreach_noise_organization_guess("Hospital Regional")


def test_noise_org_guess_vendor_media() -> None:
    assert marketing_outreach_noise_organization_guess("LabX Media")
    assert marketing_outreach_noise_organization_guess("Biocompare Inc")
    assert marketing_outreach_noise_organization_guess("Banco Santander")
    assert marketing_outreach_noise_organization_guess("Itau")


def test_second_pass_audit_ecosystem_marketplace_and_research_media() -> None:
    """Second hardening pass: audit survivors (contact_master cold-export noise)."""
    # Local mercadopublico@ on non-official host (e.g. ecapital ecosystem)
    assert marketing_outreach_noise_email("mercadopublico@ecapital.cl")
    # Solostocks cluster (domains cover mailer./mkt./notify. subdomains)
    assert marketing_outreach_noise_email("info@mailer.solostocks.com")
    assert marketing_outreach_noise_email("mail@solostocks.com")
    assert marketing_outreach_noise_email("info@solostocks.cl")
    assert marketing_outreach_noise_email("comercial@mkt.solostocks.com")
    assert marketing_outreach_noise_email("info@notify.solostocks.com")
    # Research / market-research media (domain-only; no global news@ rule)
    assert marketing_outreach_noise_email("news@rapidmicrobiology.com")
    assert marketing_outreach_noise_email("reports@leadingmarketresearch.com")
    assert marketing_outreach_noise_email("info@leadingmarketresearch.com")


def test_ecapital_non_mercadopublico_local_still_allowed() -> None:
    """Do not block whole ecapital.cl—only mercadopublico@ and similar locals."""
    assert not marketing_outreach_noise_email("contacto@ecapital.cl")

def test_cccampaigns_esp_relay_is_noise() -> None:
    assert marketing_outreach_noise_email("email@sstocks.cccampaigns.net")
    assert marketing_outreach_noise_email("reply@foo.cccampaigns.net")

