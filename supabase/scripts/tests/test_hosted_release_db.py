"""Real PG17 transactions, snapshot and encrypted restore; synthetic data only.

Set OL_RELEASE_TEST_PORT to an ISOLATED loopback PostgreSQL 17 fixture. No DSN
or production secret is accepted. Each test creates its own random database.
Missing tools/database fail when OL_RELEASE_DB_TEST_REQUIRED=true (CI).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hosted_migrations as migrations
import hosted_backup as backup


class RealReleaseDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        port = os.environ.get("OL_RELEASE_TEST_PORT", "")
        if not port or not port.isdigit() or not all(shutil.which(x) for x in ("psql", "pg_dump", "pg_restore", "age", "age-keygen")):
            if os.environ.get("OL_RELEASE_DB_TEST_REQUIRED") == "true":
                raise RuntimeError("Required isolated PG17/age fixture missing")
            raise unittest.SkipTest("isolated PG17/age fixture not configured")
        cls.admin = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "OL_PROD_"))}
        cls.admin.update(PGHOST="127.0.0.1", PGPORT=port, PGUSER="postgres", PGDATABASE="postgres",
                         PGPASSWORD=os.environ.get("OL_RELEASE_TEST_PASSWORD", ""), PGSSLMODE="disable")
        version = cls.sql("show server_version_num")
        if not version.startswith("17"):
            raise RuntimeError("Isolated fixture must be PG17")
        cls.sql("""DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='origenlab_owner') THEN
            CREATE ROLE origenlab_owner NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='origenlab_migrator') THEN
            CREATE ROLE origenlab_migrator LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;
          END IF;
          END $$; GRANT origenlab_owner TO origenlab_migrator;""")

    @classmethod
    def sql(cls, query, env=None):
        result = subprocess.run(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query],
                                env=env or cls.admin, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError("Synthetic fixture SQL failed: " + result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.name = "origenlab_test_" + uuid.uuid4().hex[:8]
        self.sql(f'CREATE DATABASE {self.name}')
        self.env = dict(self.admin, PGDATABASE=self.name)
        self.sql("""CREATE SCHEMA crm AUTHORIZATION origenlab_owner;
          CREATE SCHEMA supabase_migrations;
          CREATE TABLE supabase_migrations.schema_migrations(version text PRIMARY KEY, name text);
          GRANT USAGE ON SCHEMA supabase_migrations TO origenlab_migrator;
          GRANT SELECT, INSERT ON supabase_migrations.schema_migrations TO origenlab_migrator;""", self.env)
        self.migrator = dict(self.env, PGUSER="origenlab_migrator", PGPASSWORD="")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "supabase/migrations").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()
        self.sql(f'DROP DATABASE {self.name} WITH (FORCE)')

    def migration(self, body):
        path = self.root / "supabase/migrations/20261008121212_test.sql"
        path.write_text("SET ROLE origenlab_owner;\n" + body.rstrip().rstrip(";") + ";\nRESET ROLE;\n")

    def apply(self):
        with mock.patch.dict(os.environ, GITHUB_REF="refs/heads/main", OL_PROD_BACKUP_VERIFIED="true"), \
             mock.patch.object(migrations, "ROOT", self.root), \
             mock.patch.object(migrations, "EXPECTED_DATABASE", self.name), \
             mock.patch.object(migrations, "database_env", return_value=self.migrator):
            migrations.run("apply")

    def test_real_atomic_success_and_idempotent_rerun(self):
        self.migration("CREATE TABLE crm.probe(id integer PRIMARY KEY); INSERT INTO crm.probe VALUES (7);")
        self.apply()
        self.apply()
        self.assertEqual(self.sql("select count(*) from crm.probe", self.env), "1")
        self.assertEqual(self.sql("select count(*) from supabase_migrations.schema_migrations", self.env), "1")

    def test_real_sql_failure_rolls_back_ddl_and_ledger(self):
        self.migration("CREATE TABLE crm.probe(id integer); SELECT 1/0;")
        with self.assertRaises(migrations.Refused):
            self.apply()
        self.assertEqual(self.sql("select to_regclass('crm.probe') is null", self.env), "t")
        self.assertEqual(self.sql("select count(*) from supabase_migrations.schema_migrations", self.env), "0")

    def test_real_ledger_failure_rolls_back_ddl(self):
        self.sql("ALTER TABLE supabase_migrations.schema_migrations ADD CHECK (version <> '20261008121212')", self.env)
        self.migration("CREATE TABLE crm.probe(id integer)")
        with self.assertRaises(migrations.Refused):
            self.apply()
        self.assertEqual(self.sql("select to_regclass('crm.probe') is null", self.env), "t")

    def test_snapshot_holds_data_and_ledger_at_one_recovery_point(self):
        self.sql("CREATE TABLE crm.probe(id integer); INSERT INTO crm.probe VALUES (1)", self.env)
        with backup.exported_snapshot(self.env) as snapshot:
            self.sql("INSERT INTO crm.probe VALUES (2); INSERT INTO supabase_migrations.schema_migrations VALUES ('20261008121212','later')", self.env)
            seen = self.sql(f"BEGIN ISOLATION LEVEL REPEATABLE READ; SET TRANSACTION SNAPSHOT '{snapshot}'; select count(*) from crm.probe; select count(*) from supabase_migrations.schema_migrations; COMMIT", self.env)
            self.assertEqual(seen, "1\n0")

    def test_encrypted_dump_decrypt_and_real_restore(self):
        # Schema-qualified dump must restore constraints, functions and queue data.
        self.sql("""CREATE SCHEMA procrastinate AUTHORIZATION origenlab_owner;
          SET ROLE origenlab_owner;
          CREATE TABLE crm.probe(id integer PRIMARY KEY, amount integer CHECK(amount>0));
          INSERT INTO crm.probe VALUES (7,9);
          CREATE FUNCTION crm.answer() RETURNS integer LANGUAGE sql AS 'select 42';
          CREATE TABLE procrastinate.synthetic_jobs(id integer PRIMARY KEY, status text);
          INSERT INTO procrastinate.synthetic_jobs VALUES (1,'failed'); RESET ROLE;""", self.env)
        dump = self.root / "fixture.dump"
        with backup.exported_snapshot(self.env) as snapshot:
            backup.execute(["pg_dump", "--format=custom", "--snapshot", snapshot, "-n", "crm", "-n", "procrastinate", "-f", str(dump)], self.env)
        key = self.root / "offline.key"
        backup.execute(["age-keygen", "-o", str(key)])
        recipient = backup.execute(["age-keygen", "-y", str(key)]).strip()
        cipher, restored_dump = self.root / "fixture.age", self.root / "decrypted.dump"
        backup.execute(["age", "-r", recipient, "-o", str(cipher), str(dump)])
        backup.execute(["age", "-d", "-i", str(key), "-o", str(restored_dump), str(cipher)])
        self.assertEqual(hashlib.sha256(dump.read_bytes()).digest(), hashlib.sha256(restored_dump.read_bytes()).digest())
        recovery = "origenlab_test_" + uuid.uuid4().hex[:8]
        self.sql(f'CREATE DATABASE {recovery}')
        try:
            env = dict(self.env, PGDATABASE=recovery)
            backup.execute(["pg_restore", "--exit-on-error", "--dbname", recovery, str(restored_dump)], env)
            self.assertEqual(self.sql("select amount from crm.probe; select crm.answer(); select status from procrastinate.synthetic_jobs", env), "9\n42\nfailed")
            self.assertEqual(self.sql("select count(*) from pg_constraint where conrelid='crm.probe'::regclass", env), "2")
        finally:
            self.sql(f'DROP DATABASE {recovery} WITH (FORCE)')

    def test_advisory_lock_timeout_does_not_apply_ddl(self):
        self.migration("CREATE TABLE crm.probe(id integer);")
        holder = subprocess.Popen(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
                                  env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
        try:
            holder.stdin.write("BEGIN; SELECT pg_advisory_xact_lock(72830224092701);\n\\echo LOCKED\n")
            holder.stdin.flush()
            while holder.stdout.readline().strip() != "LOCKED":
                if holder.poll() is not None:
                    self.fail("Isolated lock holder ended")
            with self.assertRaises(migrations.Refused):
                self.apply()
            self.assertEqual(self.sql("select to_regclass('crm.probe') is null", self.env), "t")
        finally:
            holder.communicate("ROLLBACK;\n\\q\n", timeout=10)

    def test_missing_ledger_grant_refuses_before_ddl(self):
        self.migration("CREATE TABLE crm.probe(id integer);")
        self.sql("REVOKE INSERT ON supabase_migrations.schema_migrations FROM origenlab_migrator", self.env)
        with self.assertRaises(migrations.Refused):
            self.apply()
        self.assertEqual(self.sql("select to_regclass('crm.probe') is null", self.env), "t")

    def test_full_backup_entrypoint_encrypts_one_snapshot_bundle(self):
        import tarfile
        # The actual backup entrypoint uses all eight schemas and realistic TOC checks.
        for schema in backup.SCHEMAS:
            if schema != "crm":
                self.sql(f"CREATE SCHEMA {schema} AUTHORIZATION origenlab_owner", self.env)
            for i in range(4):
                self.sql(f"SET ROLE origenlab_owner; CREATE TABLE {schema}.probe_{i}(id int); INSERT INTO {schema}.probe_{i} VALUES (1); RESET ROLE", self.env)
        key = self.root / "key"
        backup.execute(["age-keygen", "-o", str(key)])
        recipient = backup.execute(["age-keygen", "-y", str(key)]).strip()
        cipher, bundle = self.root / "bundle.tar.age", self.root / "decoded.tar"
        with mock.patch.dict(os.environ, GITHUB_REF="refs/heads/main",
                             OL_PROD_BACKUP_AGE_RECIPIENT=recipient, OL_PROD_BACKUP_OUT=str(cipher)), \
             mock.patch.object(backup, "database_env", return_value=self.migrator):
            backup.run()
        backup.execute(["age", "-d", "-i", str(key), "-o", str(bundle), str(cipher)])
        with tarfile.open(bundle) as archive:
            checksum = archive.extractfile("SHA256SUMS.txt").read().decode()
            for line in checksum.splitlines():
                digest, name = line.split("  ")
                self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), digest)
            toc = archive.extractfile("restore-toc.txt").read().decode()
            for schema in backup.SCHEMAS:
                self.assertIn("TABLE DATA " + schema, toc)
