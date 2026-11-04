"""에이전트 파이프라인 오프라인 테스트.

네트워크를 쓰지 않기 위해 가짜 OpenDART 클라이언트를 주입한다.
아래 수치는 실제 공시가 아니라 파이프라인 검증용 합성값이다(SYNTHETIC).
"""
from decimal import Decimal

import pytest

from factlens.agent import VerificationAgent
from factlens.opendart import Corp, FsResponse, OpenDartError

CORP = Corp(code="00000001", name="테스트기업", stock_code="000001")


def _rows(cfs_growth: Decimal, ofs_growth: Decimal, year: int, fs_div: str):
    growth = cfs_growth if fs_div == "CFS" else ofs_growth
    prev = Decimal(1000)
    curr = prev * (Decimal(1) + growth / Decimal(100))
    return [
        {
            "rcept_no": f"{year}0000000001",
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_nm": f"제{year}기",
            "thstrm_amount": f"{curr:,.0f}",
            "frmtrm_nm": f"제{year - 1}기",
            "frmtrm_amount": f"{prev:,.0f}",
            "currency": "KRW",
        },
        {
            "rcept_no": f"{year}0000000001",
            "sj_div": "BS",
            "account_id": "ifrs-full_Liabilities",
            "account_nm": "부채총계",
            "thstrm_amount": "300,000",
            "frmtrm_amount": "280,000",
            "currency": "KRW",
        },
        {
            "rcept_no": f"{year}0000000001",
            "sj_div": "BS",
            "account_id": "ifrs-full_Equity",
            "account_nm": "자본총계",
            "thstrm_amount": "1,000,000",
            "frmtrm_amount": "950,000",
            "currency": "KRW",
        },
    ]


class FakeClient:
    """SYNTHETIC: 실제 API 를 호출하지 않는 테스트 더블."""

    def __init__(self, cfs_growth="20", ofs_growth="20", *, only_latest_year=True):
        self.cfs_growth = Decimal(cfs_growth)
        self.ofs_growth = Decimal(ofs_growth)
        self.only_latest_year = only_latest_year
        self.call_count = 0
        self.latest_year = 2025

    def corp_codes(self):
        return [CORP]

    def financial_statements(self, corp_code, year, *, reprt_code="11011", fs_div="CFS"):
        self.call_count += 1
        if self.only_latest_year and year != self.latest_year:
            raise OpenDartError("013", "조회된 데이터가 없습니다")
        return FsResponse(
            corp_code=corp_code,
            year=year,
            reprt_code=reprt_code,
            fs_div=fs_div,
            rows=_rows(self.cfs_growth, self.ofs_growth, year, fs_div),
        )


def _agent(**kwargs) -> VerificationAgent:
    client = FakeClient(**kwargs)
    return VerificationAgent(client=client, store=None, cross_check=False)


def test_mismatch_claim():
    report = _agent().run("테스트기업 매출이 지난해보다 40% 증가했다.")
    assert report["verdict"] == "불일치"
    claim = report["claims"][0]
    assert claim["computed"]["CFS"] == "20"
    assert claim["gap"] == "20"
    assert claim["source"]["rcept_no"].endswith("0000000001")
    assert "× 100" in claim["formula"]


def test_match_claim():
    report = _agent().run("테스트기업 매출이 지난해보다 20% 증가했다.")
    assert report["verdict"] == "일치"


def test_basis_conflict_holds_judgement():
    report = _agent(cfs_growth="20", ofs_growth="41").run("테스트기업 매출이 지난해보다 40% 증가했다.")
    assert report["verdict"] == "기준 불명확"


def test_explicit_basis_resolves_conflict():
    report = _agent(cfs_growth="20", ofs_growth="41").run("테스트기업 매출이 연결 기준으로 지난해보다 40% 증가했다.")
    assert report["claims"][0]["verdict"] == "불일치"


def test_qualitative_claim_is_held_with_reference():
    report = _agent().run("테스트기업은 부채가 거의 없다.")
    claim = report["claims"][0]
    assert claim["verdict"] == "기준 불명확"
    assert claim["computed"]["CFS"] == "30"


def test_unknown_company_returns_insufficient():
    report = _agent().run("없는회사 매출이 40% 증가했다.")
    assert report["verdict"] == "근거 부족"
    assert report["claims"] == []


def test_ambiguous_company_name_holds_judgement():
    from factlens.corp import CorpIndex

    index = CorpIndex([Corp("00000001", "삼성", ""), Corp("00000002", "삼성전자", "005930"),
                       Corp("00000003", "삼성물산", "028260")], aliases={})
    agent = VerificationAgent(client=FakeClient(), index=index, store=None, cross_check=False)
    report = agent.run("삼성 매출이 40% 증가했다.")
    assert report["verdict"] == "기준 불명확"
    assert report["claims"] == []


def test_steps_are_recorded():
    report = _agent().run("테스트기업 매출이 지난해보다 40% 증가했다.")
    titles = [s["title"] for s in report["steps"]]
    assert "주장 분해" in titles
    assert "공시 조회" in titles
    assert "검증 보고서 생성" in titles
