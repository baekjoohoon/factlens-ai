"""주장 분해 규칙 테스트 (네트워크 불필요)."""
from decimal import Decimal

from factlens.claims import parse_claim, parse_text, split_claims

SAMPLE = "삼성전자 매출이 지난해보다 40% 증가했고 부채가 거의 없다."


def test_split_splits_conjunction():
    parts = split_claims(SAMPLE)
    assert len(parts) == 2


def test_growth_claim_extraction():
    claim = parse_text(SAMPLE)[0]
    assert claim.metric == "revenue"
    assert claim.claim_type == "growth_rate"
    assert claim.claimed_value == Decimal("40")
    assert claim.period.mode == "yoy"
    assert claim.basis is None


def test_qualitative_claim():
    claim = parse_text(SAMPLE)[1]
    assert claim.metric == "liabilities"
    assert claim.claim_type == "qualitative"
    assert claim.claimed_value is None


def test_basis_and_year_detection():
    claim = parse_claim("2024년 연결 기준 매출이 10% 증가했다")
    assert claim.basis == "CFS"
    assert claim.period.year == 2024
    assert claim.period.mode == "year"


def test_decrease_becomes_negative():
    claim = parse_claim("영업이익이 10% 감소했다")
    assert claim.metric == "operating_income"
    assert claim.claimed_value == Decimal("-10")


def test_decimal_is_not_split_as_sentence_end():
    parts = split_claims("삼성전자 매출이 지난해보다 10.9% 증가했다.")
    assert len(parts) == 1
    claim = parse_claim(parts[0])
    assert claim.claimed_value == Decimal("10.9")


def test_unsupported_sentence():
    claim = parse_claim("이 회사는 앞으로 좋아질 것 같다")
    assert claim.claim_type == "unsupported"
    assert not claim.supported
