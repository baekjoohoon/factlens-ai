"""장애 상황 테스트: OpenDART 오류, LLM 오류 응답."""
import json
import subprocess
from decimal import Decimal

import pytest

from factlens import llm as llm_mod
from factlens.agent import VerificationAgent
from factlens.llm import AutoPlanner, CodexPlanner, RulePlanner
from factlens.opendart import Corp, OpenDartError

CORP = Corp(code="00000001", name="테스트기업", stock_code="000001")


class FailingClient:
    """모든 재무제표 호출이 5xx 로 실패하는 상황."""

    def __init__(self, code="HTTP_500", message="서버 오류"):
        self.code = code
        self.message = message
        self.call_count = 0

    def corp_codes(self):
        return [CORP]

    def financial_statements(self, corp_code, year, *, reprt_code="11011", fs_div="CFS"):
        self.call_count += 1
        raise OpenDartError(self.code, self.message, retryable=True)


def test_api_failure_holds_instead_of_crashing():
    agent = VerificationAgent(client=FailingClient(), store=None, cross_check=False,
                              planner=RulePlanner())
    report = agent.run("테스트기업 매출이 지난해보다 40% 증가했다.")
    assert report["verdict"] == "근거 부족"
    assert report["claims"] == []
    join = " ".join(s["detail"] for s in report["steps"])
    assert "API" in join or "HTTP_500" in join or "조회" in join


def test_rate_limit_is_reported_as_error_status():
    agent = VerificationAgent(client=FailingClient("020", "요청 제한을 초과했습니다"),
                              store=None, cross_check=False, planner=RulePlanner())
    report = agent.run("테스트기업 매출이 지난해보다 40% 증가했다.")
    statuses = [s["status"] for s in report["steps"]]
    assert "error" in statuses


def _fake_run_factory(payload_text):
    def fake_run(cmd, **kwargs):
        out_path = None
        for i, part in enumerate(cmd):
            if part == "-o" and i + 1 < len(cmd):
                out_path = cmd[i + 1]
        if out_path:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(payload_text)
        return subprocess.CompletedProcess(cmd, 0, b"model: test-model\n", b"")
    return fake_run


def test_bad_json_from_llm_falls_back(monkeypatch):
    monkeypatch.setattr(llm_mod.subprocess, "run", _fake_run_factory("여기 JSON 이 아닌 문장"))
    planner = AutoPlanner(primary=CodexPlanner(), fallback=RulePlanner())
    claims, info = planner.parse("테스트기업 매출이 지난해보다 40% 증가했다.")
    assert info.fallback is True
    assert "JSON" in info.detail
    assert claims[0].claimed_value == Decimal("40")


def test_schema_violation_from_llm_falls_back(monkeypatch):
    bad = json.dumps({"claims": [{"evidence_text": "테스트기업 매출이 지난해보다 40% 증가했다",
                                  "company_name": "테스트기업", "metric": "revenue",
                                  "claim_type": "growth_rate", "claimed_value": 40}]})
    monkeypatch.setattr(llm_mod.subprocess, "run", _fake_run_factory(bad))
    planner = AutoPlanner(primary=CodexPlanner(), fallback=RulePlanner())
    claims, info = planner.parse("테스트기업 매출이 지난해보다 40% 증가했다.")
    assert info.fallback is True
    assert claims


def test_llm_number_guard_blocks_invented_value(monkeypatch):
    invented = json.dumps({"claims": [{
        "evidence_text": "테스트기업 매출이 지난해보다 40% 증가했다",
        "company_name": "테스트기업", "metric": "revenue", "claim_type": "growth_rate",
        "claimed_value": 55, "period": {"mode": "yoy", "year": None}, "basis": None,
        "needs_check": []}]})
    monkeypatch.setattr(llm_mod.subprocess, "run", _fake_run_factory(invented))
    planner = AutoPlanner(primary=CodexPlanner(), fallback=RulePlanner())
    claims, info = planner.parse("테스트기업 매출이 지난해보다 40% 증가했다.")
    assert info.fallback is True
    assert claims[0].claimed_value == Decimal("40")  # 규칙 파서가 원문에서 읽은 값
