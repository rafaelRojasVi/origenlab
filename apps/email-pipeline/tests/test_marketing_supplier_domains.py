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


def test_supplier_email_domains_ignores_is_exclusion_flag() -> None:
    """``is_exclusion`` marks domains already known from history (deduped out of a
    DeepSearch new-opportunity ranking) -- it does not mean "not a supplier". A row
    must block cold outreach purely by being in supplier_master, whichever value
    is_exclusion holds; regression for the mistaken 0/1 filter in commit 41569b1."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE supplier_master (
          domain_norm TEXT,
          is_exclusion INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO supplier_master VALUES ('known-supplier.com', 1);
        INSERT INTO supplier_master VALUES ('manually-added-supplier.com', 0);
        """
    )

    assert supplier_email_domains(conn) == frozenset(
        {"known-supplier.com", "manually-added-supplier.com"}
    )


def test_supplier_email_domains_excludes_domains_absent_from_table() -> None:
    """A prospect/customer domain that was never entered into supplier_master must
    not be treated as a supplier domain -- absence from the table, not any flag on
    a row, is what keeps a prospect out of the block-list."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE supplier_master (
          domain_norm TEXT,
          is_exclusion INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO supplier_master VALUES ('known-supplier.com', 1);
        """
    )

    doms = supplier_email_domains(conn)
    assert doms == frozenset({"known-supplier.com"})
    assert is_supplier_email_domain("buyer@prospect.example", doms) is False


def test_is_supplier_email_domain_subdomain() -> None:
    doms = frozenset({"ohaus.com"})
    assert is_supplier_email_domain("x@ohaus.com", doms) is True
    assert is_supplier_email_domain("x@mail.ohaus.com", doms) is True
    assert is_supplier_email_domain("x@example.com", doms) is False
