"""Redaction: what it removes, what it deliberately keeps, and that it can prove its own work."""

import unittest

from olaudit import redact

PROJECT_REF = "abcdefghijklmnopqrst"
HOST = f"db.{PROJECT_REF}.supabase.co"
PASSWORD = "not-a-real-credential-value"

# Fabricated, deliberately entropy-free, and assembled rather than written out. These fixtures have
# to have the shape of real credentials for the patterns to be worth testing; none of them is one,
# and no complete token literal is committed anywhere in this repository.
SECRET_KEY = "sb_secret_" + "EXAMPLE_NOT_A_REAL_KEY"
PUBLISHABLE_KEY = "sb_publishable_" + "EXAMPLE_NOT_A_REAL_KEY"
ACCESS_TOKEN = "sbp_" + "EXAMPLENOTAREALTOKEN0000000000000000"
JWT = ".".join(["ey" + "JhbGciOiJub25lIn0", "ey" + "Jub3RlIjoiZmFrZSJ9", "notarealsignature"])


class TestRedact(unittest.TestCase):
    def test_connection_string_is_removed_whole(self):
        text = f"postgresql://origenlab_migrator:{PASSWORD}@{HOST}:5432/postgres"
        out = redact.redact(text, [PASSWORD, PROJECT_REF, HOST])
        self.assertNotIn(PASSWORD, out)
        self.assertNotIn(PROJECT_REF, out)
        self.assertEqual(out, "postgresql://[redacted]")

    def test_known_secret_is_removed_even_without_a_pattern(self):
        out = redact.redact(f"the value is {PASSWORD} here", [PASSWORD])
        self.assertNotIn(PASSWORD, out)

    def test_supabase_keys_and_jwts(self):
        text = f"{SECRET_KEY} {PUBLISHABLE_KEY} {ACCESS_TOKEN} {JWT}"
        out = redact.redact(text)
        for fragment in ("EXAMPLE_NOT_A_REAL_KEY", "EXAMPLENOTAREALTOKEN", "notarealsignature"):
            self.assertNotIn(fragment, out)
        self.assertIn("[jwt-redacted]", out)

    def test_supabase_host_is_removed(self):
        self.assertNotIn(PROJECT_REF, redact.redact(f"connecting to {HOST}"))

    def test_non_loopback_addresses_are_removed(self):
        out = redact.redact("resolved 93.184.216.34 and 2001:4860:4860::8888")
        self.assertNotIn("93.184.216.34", out)
        self.assertNotIn("2001:4860:4860::8888", out)

    def test_loopback_is_kept_because_it_is_evidence(self):
        out = redact.redact("local target 127.0.0.1:54322 and ::1")
        self.assertIn("127.0.0.1", out)
        self.assertIn("::1", out)

    def test_password_assignment_forms(self):
        for text in ("password=hunter2", "PGPASSWORD: hunter2", 'password = "hunter2"'):
            self.assertNotIn("hunter2", redact.redact(text))

    def test_redaction_is_idempotent_and_provably_clean(self):
        text = (
            f"postgresql://u:{PASSWORD}@{HOST}:5432/postgres "
            f"{SECRET_KEY} 93.184.216.34 password=hunter2"
        )
        once = redact.redact(text, [PASSWORD, PROJECT_REF, HOST])
        twice = redact.redact(once, [PASSWORD, PROJECT_REF, HOST])
        self.assertEqual(once, twice)
        self.assertEqual([], redact.find_leaks(once, [PASSWORD, PROJECT_REF, HOST]))

    def test_find_leaks_reports_an_unredacted_string(self):
        leaks = redact.find_leaks(f"host {HOST}", [PROJECT_REF])
        self.assertTrue(leaks)
        self.assertIn("supabase-host", {leak.kind for leak in leaks})

    def test_leak_excerpts_do_not_reprint_the_leak(self):
        for leak in redact.find_leaks(f"host {HOST} key {SECRET_KEY}", [PROJECT_REF]):
            self.assertNotIn(PROJECT_REF, leak.excerpt)
            self.assertNotIn("EXAMPLE_NOT_A_REAL_KEY", leak.excerpt)

    def test_assert_clean_raises_on_a_leak(self):
        with self.assertRaises(redact.LeakError):
            redact.assert_clean(f"host {HOST}", [PROJECT_REF], what="a test string")

    def test_assert_clean_accepts_redacted_text(self):
        redact.assert_clean(redact.redact(f"host {HOST}", [PROJECT_REF]), [PROJECT_REF])

    def test_tail_is_redacted(self):
        log = "\n".join([f"line {i}" for i in range(30)] + [f"error at {HOST}"])
        out = redact.tail(log, lines=3, secrets=[PROJECT_REF])
        self.assertNotIn(PROJECT_REF, out)
        self.assertEqual(3, len(out.splitlines()))


if __name__ == "__main__":
    unittest.main()
