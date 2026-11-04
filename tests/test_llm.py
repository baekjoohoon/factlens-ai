"""LLM 구조화 계층 테스트 (네트워크 불필요).

실제 모델 호출은 `scripts/scenarios.py` 에서 수행하고, 여기서는
스키마 검증·수치 가드·폴백 동작만 고정한다.
"""
from decimal import Decimal

import pytest

from factlens.claims import Claim
from factlens.llm import (AutoPlanner, PlannerInfo, RulePlanner, SchemaError,
                          payload_to_claims, validate_payload)

TEXT = "삼성전자 매출이 작년보다 40% 늘었고 부채가 거의 없대."

GOOD = {
    "claims": [
        {
            "evidence_text": "삼성전자 매출이 작년보다 40% 늘었고",
            "company_name": "삼성전자",
            "metric": "revenue",
            "claim_type": "growth_rate",
            "claimed_value": 40,
            "period": {"mode": "yoy", "year": None},
            "basis": None,
            "needs_check": ["회계기준(연결/별도) 미지정"],
        },
        {
            "evidence_text": "부채가 거의 없대",
            "company_name": "삼성전자",
            "metric": "liabilities",
            "claim_type": "qualitative",
            "claimed_value": None,
            "period": {"mode": "unspecified", "year": None},
            "basis": None,
            "needs_check": ["임계값 없음"],
        },
    ]
}


def test_valid_payload_passes():
    claims = validate_payload(GOOD, TEXT)
    assert len(claims) == 2


def test_payload_maps_to_claim_objects():
    claims = payload_to_claims(validate_payload(GOOD, TEXT))
    assert claims[0].metric == "revenue"
    assert claims[0].claimed_value == Decimal("40")
    assert claims[0].claim_type == "growth_rate"
    assert claims[1].claim_type == "qualitative"
    assert any("추가 확인" in n for n in claims[1].notes)


def test_missing_key_rejected():
    bad = {"claims": [dict(GOOD["claims"][0])]}
    del bad["claims"][0]["basis"]
    with pytest.raises(SchemaError):
        validate_payload(bad, TEXT)


def test_extra_key_rejected():
    bad = {"claims": [dict(GOOD["claims"][0], extra="x")]}
    with pytest.raises(SchemaError):
        validate_payload(bad, TEXT)


def test_bad_enum_rejected():
    bad = {"claims": [dict(GOOD["claims"][0], metric="profit")]}
    with pytest.raises(SchemaError):
        validate_payload(bad, TEXT)


def test_number_not_in_source_rejected():
    bad = {"claims": [dict(GOOD["claims"][0], claimed_value=75)]}
    with pytest.raises(SchemaError):
        validate_payload(bad, TEXT)


def test_evidence_not_in_source_rejected():
    bad = {"claims": [dict(GOOD["claims"][0], evidence_text="삼성전자 매출이 90% 늘었고")]}
    with pytest.raises(SchemaError):
        validate_payload(bad, TEXT)


def test_growth_rate_requires_value():
    bad = {"claims": [dict(GOOD["claims"][0], claimed_value=None)]}
    with pytest.raises(SchemaError):
        validate_payload(bad, TEXT)


def test_top_level_must_be_only_claims():
    with pytest.raises(SchemaError):
        validate_payload({"claims": GOOD["claims"], "note": "x"}, TEXT)


def test_auto_planner_falls_back_to_rules():
    class Boom:
        def parse(self, text):
            raise SchemaError("JSON 파싱 실패")

    planner = AutoPlanner(primary=Boom(), fallback=RulePlanner())
    claims, info = planner.parse(TEXT)
    assert info.fallback is True
    assert info.name == "rule"
    assert claims and claims[0].metric == "revenue"


def test_rule_planner_reports_name():
    claims, info = RulePlanner().parse(TEXT)
    assert info.name == "rule"
    assert isinstance(info, PlannerInfo)
    assert claims[0].claimed_value == Decimal("40")
