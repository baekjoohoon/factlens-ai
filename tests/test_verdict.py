"""판정 규칙 테스트 (네트워크 불필요)."""
from decimal import Decimal

from factlens.verdict import Verdict, judge_across_bases, judge_numeric, overall


def test_match_within_tolerance():
    assert judge_numeric(Decimal(40), Decimal("39.5")).verdict is Verdict.MATCH


def test_partial_match():
    assert judge_numeric(Decimal(40), Decimal("38")).verdict is Verdict.PARTIAL


def test_mismatch():
    assert judge_numeric(Decimal(40), Decimal("16.2")).verdict is Verdict.MISMATCH


def test_missing_value_is_insufficient():
    assert judge_numeric(Decimal(40), None).verdict is Verdict.INSUFFICIENT


def test_basis_conflict_is_ambiguous():
    result = judge_across_bases(Decimal(40), {"CFS": Decimal(20), "OFS": Decimal("41")}, None)
    assert result.verdict is Verdict.AMBIGUOUS


def test_same_verdict_across_bases():
    result = judge_across_bases(Decimal(40), {"CFS": Decimal(20), "OFS": Decimal("21")}, None)
    assert result.verdict is Verdict.MISMATCH


def test_explicit_basis_is_used():
    result = judge_across_bases(Decimal(40), {"CFS": Decimal(20), "OFS": Decimal("41")}, "OFS")
    assert result.verdict is Verdict.MATCH
    assert result.basis_used == "OFS"


def test_overall_takes_worst():
    assert overall([Verdict.MATCH, Verdict.MISMATCH]) is Verdict.MISMATCH
