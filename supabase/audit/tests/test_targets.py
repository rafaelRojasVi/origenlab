"""The two target boundaries, and the fact that neither can do the other's job."""

import os
import tempfile
import unittest
from pathlib import Path

from olaudit import target_hosted, target_local
from olaudit.target import Target, TargetError, is_loopback

PROJECT_REF = "abcdefghijklmnopqrst"
HOST = f"db.{PROJECT_REF}.supabase.co"
POOLER_HOST = "aws-0-sa-east-1.pooler.supabase.com"
POOLER_LOGIN = f"origenlab_migrator.{PROJECT_REF}"

BOTH_ROUTES = frozenset({target_hosted.ROUTE_DIRECT, target_hosted.ROUTE_SUPAVISOR_SESSION})

GOOD_TARGET = """
# a reviewed hosted target
OL_HOSTED_ROUTE=direct
OL_HOSTED_HOST={host}
OL_HOSTED_PORT=5432
OL_HOSTED_DATABASE=postgres
OL_HOSTED_USER=origenlab_migrator
OL_HOSTED_PROJECT_REF={ref}
OL_HOSTED_SSLMODE=verify-full
OL_HOSTED_SSLROOTCERT={ca}
OL_HOSTED_PASSWORD_ENV=OL_HOSTED_DB_PASSWORD
"""

POOLER_TARGET = """
# a reviewed hosted target on the official Supavisor shared pooler, session mode
OL_HOSTED_ROUTE=supavisor-session
OL_HOSTED_HOST={host}
OL_HOSTED_PORT=5432
OL_HOSTED_DATABASE=postgres
OL_HOSTED_USER={login}
OL_HOSTED_PROJECT_REF={ref}
OL_HOSTED_POOLER_CLUSTER=0
OL_HOSTED_POOLER_REGION=sa-east-1
OL_HOSTED_SSLMODE=verify-full
OL_HOSTED_SSLROOTCERT={ca}
OL_HOSTED_PASSWORD_ENV=OL_HOSTED_DB_PASSWORD
"""


def write_target(root: Path, text: str) -> Path:
    path = root / "supabase" / ".audit" / "hosted_target.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


class TestLoopback(unittest.TestCase):
    def test_loopback_recognition(self):
        for host in ("127.0.0.1", "127.0.0.53", "localhost", "::1", "[::1]"):
            self.assertTrue(is_loopback(host), host)
        for host in ("10.0.0.1", "93.184.216.34", HOST, "2001:db8::1"):
            self.assertFalse(is_loopback(host), host)


class TestLocalTarget(unittest.TestCase):
    def test_a_valid_local_url_parses(self):
        parts = target_local.parse_url("postgresql://postgres:pw@127.0.0.1:54322/postgres")
        self.assertEqual("127.0.0.1", parts["host"])
        self.assertEqual("54322", parts["port"])

    def test_an_unparseable_url_is_refused(self):
        for url in ("", "not a url", "mysql://x@127.0.0.1:3306/d", "postgresql://127.0.0.1/postgres"):
            with self.assertRaises(TargetError):
                target_local.parse_url(url)

    def test_local_mode_refuses_a_remote_host_even_if_the_guard_returned_one(self):
        """The second opinion. If the bash guard were ever wrong, this still refuses."""
        parts = target_local.parse_url(f"postgresql://u:p@{HOST}:5432/postgres")
        self.assertFalse(is_loopback(parts["host"]))

    def test_a_missing_bridge_is_refused_without_connecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TargetError):
                target_local.resolve(Path(tmp))


class TestHostedTargetFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ca = self.root / "ca.crt"
        self.ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        self.env = {"OL_HOSTED_DB_PASSWORD": "not-a-real-credential"}
        self.resolver = lambda host, port: ["93.184.216.34"]

    def tearDown(self):
        self._tmp.cleanup()

    def good(self, **overrides) -> str:
        text = GOOD_TARGET.format(host=HOST, ref=PROJECT_REF, ca=self.ca)
        for key, value in overrides.items():
            lines = []
            for line in text.splitlines():
                if line.startswith(f"{key}="):
                    line = f"{key}={value}" if value is not None else ""
                lines.append(line)
            text = "\n".join(lines)
        return text

    def resolve(self, text=None, **kwargs):
        write_target(self.root, text if text is not None else self.good())
        return target_hosted.resolve(
            self.root,
            self.env,
            resolver=kwargs.pop("resolver", self.resolver),
            authorized_routes=kwargs.pop("authorized_routes", BOTH_ROUTES),
        )

    def test_a_good_target_resolves_and_pins_its_address(self):
        target = self.resolve()
        self.assertEqual("hosted", target.mode)
        self.assertEqual("93.184.216.34", target.hostaddr)
        self.assertEqual(target_hosted.AUDIT_IDENTITY, target.user)
        self.assertEqual("verify-full", target.sslmode)

    def test_the_pinned_address_is_what_libpq_is_given(self):
        env = self.resolve().child_env()
        self.assertEqual("93.184.216.34", env["PGHOSTADDR"])
        self.assertEqual(HOST, env["PGHOST"])
        self.assertEqual("verify-full", env["PGSSLMODE"])

    def test_the_direct_route_is_what_a_good_target_resolves_to(self):
        self.assertEqual(target_hosted.ROUTE_DIRECT, self.resolve().route)

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(TargetError):
            target_hosted.resolve(self.root, self.env, resolver=self.resolver)

    def test_an_unknown_or_absent_route_is_refused(self):
        for route in ("", "pooler", "supavisor-transaction", "DIRECT"):
            with self.assertRaises(TargetError, msg=route):
                self.resolve(self.good(OL_HOSTED_ROUTE=route))

    def test_a_direct_target_may_not_carry_pooler_keys(self):
        """A target that is specific about two routes at once is ambiguous, not permissive."""
        with self.assertRaises(TargetError):
            self.resolve(self.good() + "\nOL_HOSTED_POOLER_REGION=sa-east-1\n")

    def test_a_world_readable_file_is_refused(self):
        path = write_target(self.root, self.good())
        path.chmod(0o644)
        with self.assertRaises(TargetError):
            target_hosted.resolve(self.root, self.env, resolver=self.resolver)

    def test_a_symlinked_target_file_is_refused(self):
        real = self.root / "elsewhere.env"
        real.write_text(self.good(), encoding="utf-8")
        real.chmod(0o600)
        link = self.root / "supabase" / ".audit" / "hosted_target.env"
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(real, link)
        with self.assertRaises(TargetError):
            target_hosted.resolve(self.root, self.env, resolver=self.resolver)

    def test_the_supported_route_is_enforced(self):
        for host in ("localhost", "127.0.0.1", "aws-0-us-east-1.pooler.supabase.com", "evil.example.com"):
            with self.assertRaises(TargetError, msg=host):
                self.resolve(self.good(OL_HOSTED_HOST=host))

    def test_a_pooler_port_is_refused(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_PORT="6543"))

    def test_the_login_role_is_pinned_to_the_audit_identity(self):
        for user in ("postgres", "origenlab_owner", "origenlab_api", "service_role"):
            with self.assertRaises(TargetError, msg=user):
                self.resolve(self.good(OL_HOSTED_USER=user))

    def test_tls_cannot_be_downgraded(self):
        for mode in ("require", "prefer", "allow", "disable", "verify-ca"):
            with self.assertRaises(TargetError, msg=mode):
                self.resolve(self.good(OL_HOSTED_SSLMODE=mode))

    def test_a_missing_ca_file_refuses_rather_than_downgrading(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_SSLROOTCERT=str(self.root / "absent.crt")))

    def test_the_host_and_the_declared_project_reference_must_agree(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_PROJECT_REF="tsrqponmlkjihgfedcba"))

    def test_a_missing_credential_environment_variable_is_refused(self):
        write_target(self.root, self.good())
        with self.assertRaises(TargetError):
            target_hosted.resolve(self.root, {}, resolver=self.resolver)

    def test_the_default_authorisation_admits_the_direct_route_only(self):
        """A caller that says nothing about routes cannot reach the pooler."""
        write_target(self.root, self.good())
        self.assertEqual(
            target_hosted.ROUTE_DIRECT,
            target_hosted.resolve(self.root, self.env, resolver=self.resolver).route,
        )

    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good() + "\nOL_HOSTED_PASSWORD=inline-secret\n")

    def test_a_password_cannot_be_placed_in_the_target_file(self):
        """The credential stays external: there is no key that accepts one inline."""
        self.assertNotIn("OL_HOSTED_PASSWORD", target_hosted.REQUIRED_KEYS)

    def test_the_file_is_not_sourced_by_a_shell(self):
        text = self.good(OL_HOSTED_DATABASE="$(touch /tmp/ol-audit-should-not-exist)")
        target = self.resolve(text)
        self.assertEqual("$(touch /tmp/ol-audit-should-not-exist)", target.database)
        self.assertFalse(Path("/tmp/ol-audit-should-not-exist").exists())

    def test_a_duplicate_key_is_refused(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good() + f"\nOL_HOSTED_PORT=5432\n")


class TestSupavisorSessionRoute(unittest.TestCase):
    """The pooler route's own identity contract.

    The pooler host name carries no project reference, so the login does -- and that is precisely
    why the identity checks here are stricter than the direct route's, not looser. Every part of
    the endpoint is declared by the operator and proven against the host.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ca = self.root / "ca.crt"
        self.ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        self.env = {"OL_HOSTED_DB_PASSWORD": "not-a-real-credential"}
        self.resolver = lambda host, port: ["93.184.216.34"]

    def tearDown(self):
        self._tmp.cleanup()

    def good(self, **overrides) -> str:
        text = POOLER_TARGET.format(host=POOLER_HOST, login=POOLER_LOGIN, ref=PROJECT_REF, ca=self.ca)
        for key, value in overrides.items():
            lines = []
            for line in text.splitlines():
                if line.startswith(f"{key}="):
                    line = f"{key}={value}" if value is not None else ""
                lines.append(line)
            text = "\n".join(lines)
        return text

    def resolve(self, text=None, *, authorized_routes=BOTH_ROUTES, resolver=None):
        write_target(self.root, text if text is not None else self.good())
        return target_hosted.resolve(
            self.root,
            self.env,
            resolver=resolver or self.resolver,
            authorized_routes=authorized_routes,
        )

    # -- the valid route ---------------------------------------------------------------------

    def test_a_valid_session_pooler_target_resolves(self):
        target = self.resolve()
        self.assertEqual(target_hosted.ROUTE_SUPAVISOR_SESSION, target.route)
        self.assertEqual(POOLER_HOST, target.host)
        self.assertEqual(target_hosted.SUPAVISOR_SESSION_PORT, target.port)
        self.assertEqual(POOLER_LOGIN, target.user)
        self.assertEqual("0", target.pooler_cluster)
        self.assertEqual("sa-east-1", target.pooler_region)
        self.assertEqual("verify-full", target.sslmode)
        self.assertEqual("93.184.216.34", target.hostaddr)

    def test_the_route_must_be_authorised_as_well_as_declared(self):
        """Declaring the route is not selecting it; the caller must authorise it too."""
        with self.assertRaises(TargetError):
            self.resolve(authorized_routes=frozenset({target_hosted.ROUTE_DIRECT}))

    def test_there_is_no_fallback_from_direct_to_the_pooler(self):
        """A direct target stays direct even when the pooler route is authorised."""
        write_target(self.root, GOOD_TARGET.format(host=HOST, ref=PROJECT_REF, ca=self.ca))
        target = target_hosted.resolve(
            self.root, self.env, resolver=self.resolver, authorized_routes=BOTH_ROUTES
        )
        self.assertEqual(target_hosted.ROUTE_DIRECT, target.route)
        self.assertEqual(HOST, target.host)

    # -- identity ----------------------------------------------------------------------------

    def test_a_wrong_project_qualified_username_is_refused(self):
        for login in (
            "origenlab_migrator",                              # the direct route's bare login
            f"postgres.{PROJECT_REF}",                          # the documented default, not ours
            "origenlab_migrator.tsrqponmlkjihgfedcba",          # another project's reference
            f"origenlab_owner.{PROJECT_REF}",                   # a role this audit never logs in as
            f"origenlab_migrator.{PROJECT_REF}.extra",
            f"origenlab_migrator_{PROJECT_REF}",
        ):
            with self.assertRaises(TargetError, msg=login):
                self.resolve(self.good(OL_HOSTED_USER=login))

    def test_the_login_and_the_declared_project_reference_must_agree(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_PROJECT_REF="tsrqponmlkjihgfedcba"))

    def test_a_malformed_project_reference_is_refused(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_PROJECT_REF="tooshort", OL_HOSTED_USER="origenlab_migrator.tooshort"))

    # -- endpoint ----------------------------------------------------------------------------

    def test_a_wrong_hostname_is_refused(self):
        for host in (
            HOST,                                          # the direct endpoint, wrong route
            "aws-0-sa-east-1.pooler.supabase.com.evil.example.com",
            "aws-0-sa-east-1.pooler.example.com",
            "pooler.supabase.com",
            "aws-0-sa-east-1.pooler.supabase.co",
            "localhost",
        ):
            with self.assertRaises(TargetError, msg=host):
                self.resolve(self.good(OL_HOSTED_HOST=host))

    def test_a_wrong_cluster_index_is_refused(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_POOLER_CLUSTER="1"))

    def test_a_wrong_region_is_refused(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_POOLER_REGION="us-east-1"))
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_HOST="aws-0-us-east-1.pooler.supabase.com"))

    def test_the_transaction_mode_endpoint_is_refused_by_name(self):
        """6543 is transaction mode: it cannot preserve the audit contract, so it is not used."""
        with self.assertRaisesRegex(TargetError, "transaction"):
            self.resolve(self.good(OL_HOSTED_PORT=str(target_hosted.SUPAVISOR_TRANSACTION_PORT)))

    def test_any_other_port_is_refused(self):
        for port in ("5433", "443", "6544"):
            with self.assertRaises(TargetError, msg=port):
                self.resolve(self.good(OL_HOSTED_PORT=port))

    def test_a_pooler_target_missing_a_pooler_key_is_refused(self):
        for key in target_hosted.POOLER_KEYS:
            with self.assertRaises(TargetError, msg=key):
                self.resolve(self.good(**{key: ""}))

    # -- TLS ---------------------------------------------------------------------------------

    def test_tls_cannot_be_downgraded_on_the_pooler_route_either(self):
        for mode in ("require", "prefer", "allow", "disable", "verify-ca"):
            with self.assertRaises(TargetError, msg=mode):
                self.resolve(self.good(OL_HOSTED_SSLMODE=mode))

    def test_a_missing_ca_file_refuses_rather_than_downgrading(self):
        with self.assertRaises(TargetError):
            self.resolve(self.good(OL_HOSTED_SSLROOTCERT=str(self.root / "absent.crt")))

    def test_the_connection_verifies_the_certificate_against_the_pooler_hostname(self):
        env = self.resolve().child_env()
        self.assertEqual("verify-full", env["PGSSLMODE"])
        self.assertEqual(POOLER_HOST, env["PGHOST"])
        self.assertEqual("93.184.216.34", env["PGHOSTADDR"])
        self.assertEqual(str(self.ca), env["PGSSLROOTCERT"])

    # -- secrets -----------------------------------------------------------------------------

    def test_the_credential_never_reaches_the_target_file_or_an_argument(self):
        self.assertNotIn("OL_HOSTED_PASSWORD", target_hosted.required_keys_for(
            target_hosted.ROUTE_SUPAVISOR_SESSION
        ))
        self.assertEqual("not-a-real-credential", self.resolve().child_env()["PGPASSWORD"])

    def test_the_project_qualified_login_is_treated_as_a_secret(self):
        target = self.resolve()
        self.assertIn(POOLER_LOGIN, target.secrets)
        self.assertIn(PROJECT_REF, target.secrets)

    def test_no_project_reference_reaches_the_report_description(self):
        described = str(self.resolve().describe())
        self.assertNotIn(PROJECT_REF, described)
        self.assertNotIn(POOLER_LOGIN, described)
        self.assertNotIn("not-a-real-credential", described)
        self.assertIn("[project-ref-redacted]", described)

    def test_no_host_name_reaches_the_report_description_including_the_guard_notes(self):
        """A guard note that spelled the host out would break describe()'s promise from inside."""
        described = str(self.resolve().describe())
        self.assertNotIn(POOLER_HOST, described)
        self.assertNotIn("pooler.supabase.com", described)
        self.assertNotIn("93.184.216.34", described)

    def test_the_described_audit_identity_is_the_bare_role(self):
        self.assertEqual("origenlab_migrator", self.resolve().describe()["audit_identity"])


class TestAddressClassification(unittest.TestCase):
    def test_reserved_ranges_are_refused(self):
        cases = {
            "127.0.0.1": "loopback",
            "10.1.2.3": "private",
            "192.168.1.1": "private",
            "172.16.0.1": "private",
            "169.254.1.1": "link-local",
            "100.64.0.1": "carrier-grade-nat",
            "224.0.0.1": "multicast",
            "0.0.0.0": "unspecified",
            "::1": "loopback",
            "fe80::1": "link-local",
            "fc00::1": "private",
        }
        for address, expected in cases.items():
            self.assertEqual(expected, target_hosted.classify_address(address), address)

    def test_a_globally_routable_address_is_accepted(self):
        self.assertEqual("global", target_hosted.classify_address("93.184.216.34"))
        self.assertEqual("global", target_hosted.classify_address("2001:4860:4860::8888"))

    def test_documentation_ranges_are_refused_too(self):
        # RFC 5737 / RFC 3849. Not routable, so not a hosted project.
        for address in ("192.0.2.10", "198.51.100.7", "203.0.113.9", "2001:db8::1"):
            self.assertNotEqual("global", target_hosted.classify_address(address), address)

    def test_any_reserved_answer_refuses_the_whole_target(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ca = root / "ca.crt"
        ca.write_text("x", encoding="utf-8")
        write_target(root, GOOD_TARGET.format(host=HOST, ref=PROJECT_REF, ca=ca))
        # A name that answers with one good address and one loopback address is a rebinding
        # attempt, not a usable target.
        with self.assertRaises(TargetError):
            target_hosted.resolve(
                root,
                {"OL_HOSTED_DB_PASSWORD": "x"},
                resolver=lambda host, port: ["93.184.216.34", "127.0.0.1"],
            )


class TestChildEnvironment(unittest.TestCase):
    def make(self, mode="hosted"):
        return Target(
            mode=mode,
            host=HOST if mode == "hosted" else "127.0.0.1",
            hostaddr="93.184.216.34" if mode == "hosted" else None,
            port=5432,
            user="origenlab_migrator",
            database="postgres",
            sslmode="verify-full" if mode == "hosted" else "disable",
            sslrootcert="/tmp/ca.crt" if mode == "hosted" else None,
            password="not-a-real-credential",
            project_ref=PROJECT_REF if mode == "hosted" else None,
        )

    def test_nothing_inherited_reaches_the_child(self):
        os.environ["PGHOST"] = "192.0.2.10"
        os.environ["SUPABASE_ACCESS_TOKEN"] = "sbp_" + "EXAMPLENOTAREALTOKEN0000000000000000"
        self.addCleanup(os.environ.pop, "PGHOST", None)
        self.addCleanup(os.environ.pop, "SUPABASE_ACCESS_TOKEN", None)
        env = self.make().child_env()
        self.assertEqual(HOST, env["PGHOST"])
        self.assertNotIn("SUPABASE_ACCESS_TOKEN", env)
        self.assertNotIn("PGSERVICE", env)
        self.assertNotIn("PGSERVICEFILE", env)

    def test_the_connection_is_read_only_before_the_first_statement(self):
        options = self.make().child_env()["PGOPTIONS"]
        self.assertIn("default_transaction_read_only=on", options)
        self.assertIn("statement_timeout=", options)
        self.assertIn("idle_in_transaction_session_timeout=", options)

    def test_the_report_description_carries_no_identifier(self):
        described = str(self.make().describe())
        self.assertNotIn(PROJECT_REF, described)
        self.assertNotIn("not-a-real-credential", described)
        self.assertNotIn(HOST, described)

    def test_the_hosted_secret_set_covers_every_identifier_it_holds(self):
        secrets = self.make().secrets
        for value in ("not-a-real-credential", PROJECT_REF, HOST, "93.184.216.34"):
            self.assertIn(value, secrets)

    def test_a_local_target_reports_itself_as_loopback(self):
        described = self.make("local").describe()
        self.assertEqual("loopback", described["host_class"])
        self.assertEqual("127.0.0.1", described["host_shown"])


if __name__ == "__main__":
    unittest.main()
