import sqlite3

from origenlab_email_pipeline.marketing_supplier_domains import (
    is_supplier_email_domain,
    supplier_email_domains,
)


def test_supplier_email_domains_empty_when_no_table() -> None:
    conn = sqlite3.connect(":memory:")
    assert supplier_email_domains(conn) == frozenset()


def test_supplier_email_domains_reads_domain_norm() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE supplier_master (
          domain_norm TEXT
        );
        INSERT INTO supplier_master VALUES ('ohaus.com ');
        INSERT INTO supplier_master VALUES (NULL);
        """
    )
    d = supplier_email_domains(conn)
    assert d == frozenset({"ohaus.com"})


def test_is_supplier_email_domain_subdomain() -> None:
    doms = frozenset({"ohaus.com"})
    assert is_supplier_email_domain("x@ohaus.com", doms) is True
    assert is_supplier_email_domain("x@mail.ohaus.com", doms) is True
    assert is_supplier_email_domain("x@example.com", doms) is False
