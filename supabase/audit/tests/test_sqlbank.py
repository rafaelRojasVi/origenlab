"""The check bank: the shipped files are reads, and anything that is not a read is refused."""

import unittest

from olaudit import checks, sqlbank


class TestShippedBank(unittest.TestCase):
    def test_every_shipped_file_is_a_single_read(self):
        bank = sqlbank.load()
        self.assertTrue(bank)
        for check in bank:
            self.assertEqual(check.check_id, sqlbank.validate(check.sql, name=check.name))

    def test_the_bank_answers_exactly_the_registered_sql_checks(self):
        ids = tuple(check.check_id for check in sqlbank.load())
        self.assertEqual(sorted(checks.SQL_CHECK_IDS), sorted(ids))

    def test_no_shipped_file_contains_a_mutation_verb(self):
        for check in sqlbank.load():
            code = sqlbank.to_code(check.sql).lower()
            for verb in ("insert", "update", "delete", "truncate", "drop", "alter", "grant", "revoke"):
                self.assertNotRegex(code, rf"\b{verb}\b", f"{check.name} contains {verb}")


class TestRejection(unittest.TestCase):
    def reject(self, text, name="probe"):
        with self.assertRaises(sqlbank.SqlBankError):
            sqlbank.validate(text, name=name)

    def test_data_modification_is_refused(self):
        for text in (
            "insert into crm.organization (name) values ('x')",
            "update outbound.send_control set marketing_enabled = true",
            "delete from crm.domain_event",
            "truncate crm.organization",
        ):
            self.reject(text)

    def test_a_mutation_hidden_after_a_read_is_refused(self):
        self.reject("select 1, 'check', 'zz'; insert into crm.organization (name) values ('x')")

    def test_ddl_and_privilege_changes_are_refused(self):
        for text in (
            "create table t (id int)",
            "alter role origenlab_api bypassrls",
            "grant usage on schema crm to anon",
            "revoke all on schema crm from anon",
        ):
            self.reject(text)

    def test_transaction_and_session_control_are_refused(self):
        for text in ("commit", "rollback", "set role origenlab_owner", "reset role", "begin"):
            self.reject(text)

    def test_psql_meta_commands_are_refused(self):
        self.reject("select 1, 'check', 'zz' \\g /tmp/out")
        self.reject("\\copy crm.organization to '/tmp/x'")

    def test_dollar_quoting_is_refused(self):
        self.reject("select $$ anything $$, 'check', 'zz'")

    def test_filesystem_and_process_functions_are_refused(self):
        for text in (
            "select pg_read_file('/etc/passwd'), 'check', 'zz'",
            "select pg_ls_dir('/'), 'check', 'zz'",
            "select pg_sleep(60), 'check', 'zz'",
            "select pg_terminate_backend(1), 'check', 'zz'",
            "select set_config('x', 'y', false), 'check', 'zz'",
            "select query_to_xml('select 1', true, true, ''), 'check', 'zz'",
        ):
            self.reject(text)

    def test_a_file_without_a_declared_check_id_is_refused(self):
        self.reject("select 1")

    def test_a_keyword_inside_a_literal_is_data(self):
        self.assertEqual(
            "zz", sqlbank.validate("select 'delete from t' as note, 'check', 'zz'", name="probe")
        )

    def test_a_keyword_inside_a_comment_is_prose(self):
        self.assertEqual(
            "zz",
            sqlbank.validate("-- drop table t\nselect 1 as x, 'check', 'zz'", name="probe"),
        )
        self.assertEqual(
            "zz",
            sqlbank.validate("/* grant all */ select 1 as x, 'check', 'zz'", name="probe"),
        )

    def test_column_names_that_contain_a_keyword_are_not_false_positives(self):
        self.assertEqual(
            "zz",
            sqlbank.validate(
                "select updated_at, rolcreatedb, relforcerowsecurity, set_option, offset_x, "
                "'check', 'zz' from t",
                name="probe",
            ),
        )

    def test_an_unterminated_literal_is_refused(self):
        self.reject("select 'oops, 'check', 'zz'")

    def test_load_refuses_a_directory_with_no_files(self, ):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(sqlbank.SqlBankError):
                sqlbank.load(Path(tmp))

    def test_load_refuses_a_duplicate_check_id(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            for name in ("010_a.sql", "020_b.sql"):
                (Path(tmp) / name).write_text("select 1 as x, 'check', 'dup';", encoding="utf-8")
            with self.assertRaises(sqlbank.SqlBankError):
                sqlbank.load(Path(tmp))


if __name__ == "__main__":
    unittest.main()
