"""The verdict algebra, and the three things it must never do.

A missing check must not become a pass. A canned run must not say `PASS`. A local run must not
satisfy a hosted gate.
"""

import unittest
from dataclasses import dataclass

from olaudit import verdict


@dataclass
class FakeResult:
    check_id: str
    status: str
    required: bool = True


def all_passing(n=5):
    return [FakeResult(f"a{i:02d}", verdict.PASS) for i in range(n)]


class TestBase(unittest.TestCase):
    def test_all_passing_is_pass(self):
        base, blocking, incomplete = verdict.base_verdict(all_passing())
        self.assertEqual(verdict.BASE_PASS, base)
        self.assertEqual((), blocking)
        self.assertEqual((), incomplete)

    def test_a_failure_is_fail(self):
        base, blocking, _ = verdict.base_verdict(all_passing() + [FakeResult("a99", verdict.FAIL)])
        self.assertEqual(verdict.BASE_FAIL, base)
        self.assertEqual(("a99",), blocking)

    def test_an_evaluator_error_is_fail_not_a_skip(self):
        base, blocking, _ = verdict.base_verdict(all_passing() + [FakeResult("a99", verdict.ERROR)])
        self.assertEqual(verdict.BASE_FAIL, base)
        self.assertEqual(("a99",), blocking)

    def test_a_missing_required_check_is_incomplete_never_a_pass(self):
        base, _, incomplete = verdict.base_verdict(
            all_passing() + [FakeResult("a99", verdict.NOT_RUN, required=True)]
        )
        self.assertEqual(verdict.BASE_INCOMPLETE, base)
        self.assertEqual(("a99",), incomplete)

    def test_a_missing_optional_check_does_not_block(self):
        base, _, incomplete = verdict.base_verdict(
            all_passing() + [FakeResult("a99", verdict.NOT_RUN, required=False)]
        )
        self.assertEqual(verdict.BASE_PASS, base)
        self.assertEqual((), incomplete)

    def test_corroboration_alone_does_not_satisfy_a_required_check(self):
        base, _, incomplete = verdict.base_verdict([FakeResult("a13", verdict.CORROBORATED, required=True)])
        self.assertEqual(verdict.BASE_INCOMPLETE, base)
        self.assertEqual(("a13",), incomplete)

    def test_an_attestation_satisfies_a_required_check(self):
        base, _, _ = verdict.base_verdict([FakeResult("t01", verdict.ATTESTED, required=True)])
        self.assertEqual(verdict.BASE_PASS, base)

    def test_a_failure_outranks_an_incompleteness(self):
        base, blocking, incomplete = verdict.base_verdict(
            [FakeResult("a01", verdict.FAIL), FakeResult("a02", verdict.NOT_RUN)]
        )
        self.assertEqual(verdict.BASE_FAIL, base)
        self.assertEqual(("a01",), blocking)
        self.assertEqual(("a02",), incomplete)


class TestSimulated(unittest.TestCase):
    def test_a_fully_satisfied_simulated_run_is_simulated_pass_not_pass(self):
        outcome = verdict.decide(all_passing(), mode="hosted", simulated=True, hosted_contacted=False)
        self.assertEqual("SIMULATED_PASS", outcome.verdict)
        self.assertNotEqual("PASS", outcome.verdict)
        self.assertTrue(outcome.simulated)
        self.assertFalse(outcome.hosted_contacted)
        self.assertFalse(outcome.gate_eligible)

    def test_no_simulated_verdict_is_ever_the_bare_word_pass(self):
        for results in (all_passing(), [FakeResult("a1", verdict.FAIL)], [FakeResult("a1", verdict.NOT_RUN)]):
            outcome = verdict.decide(results, mode="hosted", simulated=True, hosted_contacted=False)
            self.assertTrue(outcome.verdict.startswith(verdict.SIMULATED_PREFIX))
            self.assertNotEqual(verdict.BASE_PASS, outcome.verdict)
            self.assertFalse(outcome.gate_eligible)

    def test_a_simulated_run_claiming_contact_is_still_not_eligible(self):
        outcome = verdict.decide(all_passing(), mode="hosted", simulated=True, hosted_contacted=True)
        self.assertEqual("SIMULATED_PASS", outcome.verdict)
        self.assertFalse(outcome.gate_eligible)


class TestLocal(unittest.TestCase):
    def test_a_clean_local_run_is_local_pass(self):
        outcome = verdict.decide(all_passing(), mode="local", simulated=False, hosted_contacted=False)
        self.assertEqual("LOCAL_PASS", outcome.verdict)
        self.assertFalse(outcome.gate_eligible)

    def test_no_local_verdict_is_ever_the_bare_word_pass(self):
        for results in (all_passing(), [FakeResult("a1", verdict.FAIL)], [FakeResult("a1", verdict.NOT_RUN)]):
            outcome = verdict.decide(results, mode="local", simulated=False, hosted_contacted=False)
            self.assertTrue(outcome.verdict.startswith(verdict.LOCAL_PREFIX))
            self.assertFalse(outcome.gate_eligible)


class TestHosted(unittest.TestCase):
    def test_only_a_real_hosted_run_that_connected_is_eligible(self):
        outcome = verdict.decide(all_passing(), mode="hosted", simulated=False, hosted_contacted=True)
        self.assertEqual("PASS", outcome.verdict)
        self.assertTrue(outcome.gate_eligible)
        self.assertEqual(0, outcome.exit_code)

    def test_a_hosted_run_that_did_not_connect_is_not_eligible(self):
        outcome = verdict.decide(all_passing(), mode="hosted", simulated=False, hosted_contacted=False)
        self.assertFalse(outcome.gate_eligible)

    def test_an_incomplete_hosted_run_is_not_eligible_and_exits_non_zero(self):
        outcome = verdict.decide(
            all_passing() + [FakeResult("t01", verdict.NOT_RUN)],
            mode="hosted",
            simulated=False,
            hosted_contacted=True,
        )
        self.assertEqual("INCOMPLETE", outcome.verdict)
        self.assertFalse(outcome.gate_eligible)
        self.assertEqual(1, outcome.exit_code)


if __name__ == "__main__":
    unittest.main()
