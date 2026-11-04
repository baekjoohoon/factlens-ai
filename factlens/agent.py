"""FactLens 검증 에이전트 (단일 오케스트레이터 + 결정적 도구).

역할 분리
- 계획/구조화 : claims.py (규칙 기반, LLM 어댑터로 교체 가능)
- 도구 실행   : OpenDART 조회 (opendart.py)
- 계산        : calc.py (순수 함수, LLM 미개입)
- 판정        : verdict.py (임계값 규칙)
- 기록        : store.py (SQLite)
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .calc import debt_ratio, fmt, growth_rate
from .claims import Claim, parse_text
from .corp import CorpIndex
from .llm import Planner, RulePlanner
from .metrics import amount
from .opendart import FS_DIVS, NO_DATA, REPRT_CODES, Corp, FsResponse, OpenDartClient, OpenDartError
from .verdict import Verdict, judge_across_bases, judge_numeric, overall

DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

BASE_LIMITATIONS = [
    "금액 단위는 공시 응답의 표시 단위를 그대로 사용하며 원 단위로 임의 환산하지 않습니다. "
    "증가율·부채비율은 같은 단위의 비율이므로 단위 표기 문제의 영향을 받지 않습니다.",
    "사업보고서(연간)의 연결·별도 재무제표만 조회합니다. 분기·반기 주장은 지원하지 않습니다.",
    "기업명 매칭은 문자열 기반이며, 별칭 사전에 없는 표기는 인식하지 못할 수 있습니다.",
    "주장 분해는 규칙 기반이며 복문·비유 표현은 일부만 분해됩니다.",
    "이 결과는 공시 수치 대조 결과이며 투자 판단의 권유가 아닙니다.",
]


@dataclass
class Step:
    order: int
    title: str
    status: str          # ok | warn | hold | error
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaimResult:
    raw: str
    metric: str
    metric_label: str
    claim_type: str
    company_name: str
    corp_code: str
    claimed: str | None
    computed: dict[str, str | None]
    gap: str | None
    verdict: str
    reason: str
    basis_used: str | None
    formula: str
    period_label: str
    source: dict[str, Any]
    reference: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _s(value: Decimal | None) -> str | None:
    """Decimal 을 화면 표시용 문자열로. 불필요한 0 을 남기지 않는다."""
    if value is None:
        return None
    return format(value.normalize(), "f")


class VerificationAgent:
    def __init__(
        self,
        client: OpenDartClient | None = None,
        index: CorpIndex | None = None,
        store: Any = None,
        *,
        cross_check: bool = True,
        planner: Planner | None = None,
    ):
        self._client = client
        self._index = index
        self._store = store
        self.cross_check = cross_check
        self.planner: Planner = planner or RulePlanner()
        self._bundle_cache: dict[tuple[str, int], dict[str, Any]] = {}
        self._year_cache: dict[str, int | None] = {}
        self._last_error: str = ""

    # ------------------------------------------------------------- resources
    @property
    def client(self) -> OpenDartClient:
        if self._client is None:
            self._client = OpenDartClient()
        return self._client

    @property
    def index(self) -> CorpIndex:
        if self._index is None:
            self._index = CorpIndex(self.client.corp_codes())
        return self._index

    # ------------------------------------------------------------------ data
    def _fetch(self, corp_code: str, year: int) -> dict[str, Any]:
        key = (corp_code, year)
        if key in self._bundle_cache:
            return self._bundle_cache[key]

        bundle: dict[str, Any] = {"responses": {}, "errors": {}}
        for fs in ("CFS", "OFS"):
            try:
                bundle["responses"][fs] = self.client.financial_statements(corp_code, year, fs_div=fs)
            except OpenDartError as exc:
                if exc.code == NO_DATA:
                    bundle["responses"][fs] = None
                    bundle["errors"][fs] = exc.message
                else:
                    bundle["responses"][fs] = None
                    bundle["errors"][fs] = f"[{exc.code}] {exc.message}"
                    self._last_error = f"[{exc.code}] {exc.message}"
        self._bundle_cache[key] = bundle
        return bundle

    def _latest_year(self, corp_code: str) -> int | None:
        if corp_code in self._year_cache:
            return self._year_cache[corp_code]
        start = datetime.now().year - 1
        found: int | None = None
        for year in range(start, start - 4, -1):
            bundle = self._fetch(corp_code, year)
            for fs, resp in bundle["responses"].items():
                if resp is None:
                    continue
                revenue, _ = amount(resp.rows, "revenue")
                if revenue is not None:
                    found = year
                    break
            if found:
                break
        self._year_cache[corp_code] = found
        return found

    # ------------------------------------------------------------------ run
    def run(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        self._last_error = ""
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        steps: list[Step] = []
        limitations = list(BASE_LIMITATIONS)

        def step(title: str, status: str, detail: str, **evidence: Any) -> None:
            steps.append(Step(len(steps) + 1, title, status, detail, evidence))

        # 1. 주장 분해 ------------------------------------------------------
        claims, planner_info = self.planner.parse(text)
        self._planner_info = planner_info
        supported = [c for c in claims if c.supported]
        engine = {"muse": "Muse", "llm": "LLM", "codex": "LLM"}.get(planner_info.name, "규칙 기반")
        label = f"{engine}"
        if planner_info.model:
            label += f" ({planner_info.model})"
        detail = f"{label} · 검증 가능한 주장 {len(supported)}건 / 전체 {len(claims)}건"
        if planner_info.fallback:
            detail += " · 폴백 발생"
        step(
            "주장 분해",
            "ok" if supported else "hold",
            detail,
            planner=planner_info.name,
            model=planner_info.model,
            latency_ms=planner_info.latency_ms,
            fallback=planner_info.fallback,
            claims=[{"text": c.raw, "metric": c.metric, "type": c.claim_type,
                     "value": None if c.claimed_value is None else str(c.claimed_value)} for c in claims],
        )

        # 2. 기업 식별 ------------------------------------------------------
        hits = self.index.find_in_text(text)
        company: Corp | None = None
        if len(hits) == 1:
            company = hits[0][1]
            step("기업 식별", "ok", f"{company.name} (고유번호 {company.code}, 종목코드 {company.stock_code or '없음'})",
                 method="문장 내 기업명", corp_code=company.code)
        elif len(hits) > 1:
            names = ", ".join(n for n, _ in hits[:5])
            step("기업 식별", "hold", f"기업이 여러 개 등장합니다({names}). 어느 기업의 주장인지 확정할 수 없습니다.")
            return self._report(run_id, text, Verdict.AMBIGUOUS, [], steps, limitations,
                                company=None, period=None)
        else:
            head = re.split(r"[\s,]+", text)[0] if text else ""
            guess = self.index.resolve(head) if len(head) >= 2 else None
            if guess is not None and guess.corp is not None:
                company = guess.corp
                step("기업 식별", "ok", f"{company.name} (문장 첫 어절 '{head}' 기준, {guess.method})",
                     method=guess.method, corp_code=company.code)
            elif guess is not None and guess.ambiguous:
                names = ", ".join(c.name for c in guess.candidates[:4])
                step("기업 식별", "hold",
                     f"'{head}' 만으로는 후보가 여러 개입니다({names} 등). 기업을 특정할 수 없습니다.")
                return self._report(run_id, text, Verdict.AMBIGUOUS, [], steps, limitations,
                                    company=None, period=None)
            else:
                step("기업 식별", "hold", "문장에서 상장 법인명을 찾지 못했습니다. 기업명을 포함해 다시 입력하세요.")
                return self._report(run_id, text, Verdict.INSUFFICIENT, [], steps, limitations,
                                    company=None, period=None)

        # 3. 회계기간 확정 ---------------------------------------------------
        explicit_years = {c.period.year for c in claims if c.period.mode == "year" and c.period.year}
        target_year = explicit_years.pop() if len(explicit_years) == 1 else self._latest_year(company.code)
        if target_year is None:
            if self._last_error:
                step("회계기간 확정", "error", f"OpenDART 조회 실패: {self._last_error}")
                limitations.append(
                    "OpenDART 호출이 실패해 공시를 확보하지 못했습니다. 이는 '데이터 없음'과 다른 상태이며, "
                    "같은 요청을 재시도해야 합니다."
                )
            else:
                step("회계기간 확정", "hold", "최근 사업보고서 재무제표를 찾지 못했습니다.")
            return self._report(run_id, text, Verdict.INSUFFICIENT, [], steps, limitations,
                                company=company, period=None)

        bundle = self._fetch(company.code, target_year)
        available = [fs for fs, r in bundle["responses"].items() if r is not None]
        if not available:
            detail = "; ".join(f"{k}: {v}" for k, v in bundle["errors"].items()) or "데이터 없음"
            status = "error" if self._last_error else "hold"
            step("공시 조회", status, f"{target_year} 사업보고서 재무제표를 확보하지 못했습니다. ({detail})")
            return self._report(run_id, text, Verdict.INSUFFICIENT, [], steps, limitations,
                                company=company, period={"year": target_year})

        period_label = f"{target_year} 사업보고서 (당기 {target_year} vs 전기 {target_year - 1})"
        step(
            "회계기간 확정",
            "ok",
            f"{target_year} 사업보고서 기준. 비교 대상은 같은 보고서의 전기({target_year - 1}) 수치입니다.",
            requested=[c.period.mode for c in claims],
            resolved_year=target_year,
        )
        step(
            "공시 조회",
            "ok",
            f"{company.name} {target_year} 사업보고서 재무제표 확보: " + ", ".join(FS_DIVS[fs] for fs in available),
            fs_divs=available,
            rcept_no=sorted({r.rows[0].get("rcept_no", "") for r in bundle["responses"].values() if r and r.rows}),
        )

        # 4. 전기 재작성 점검 ------------------------------------------------
        if self.cross_check:
            self._cross_check(company, target_year, bundle, step)

        # 5~7. 주장별 계산·대조·판정 ----------------------------------------
        results: list[ClaimResult] = []
        for claim in claims:
            if not claim.supported:
                results.append(self._unsupported(claim, company))
                continue
            results.append(self._evaluate(claim, company, target_year, bundle, available, period_label))

        # 7. 수치 재계산 결과 기록 -------------------------------------------
        calc_summary = []
        for r in results:
            if r.computed:
                values = ", ".join(f"{k} {v}%" for k, v in r.computed.items() if v)
                calc_summary.append(f"{r.metric_label} {values}")
        step(
            "수치 재계산",
            "ok" if calc_summary else "warn",
            "; ".join(calc_summary) if calc_summary else "재계산할 수치가 없습니다.",
            formulas=[r.formula for r in results if r.formula],
        )

        # 8. 증거 충분성 확인 ------------------------------------------------
        verdict = overall([Verdict(r.verdict) for r in results]) if results else Verdict.INSUFFICIENT
        held = [r for r in results if r.verdict in (Verdict.AMBIGUOUS.value, Verdict.INSUFFICIENT.value)]
        step(
            "증거 충분성 확인",
            "warn" if held else "ok",
            f"판정 보류 {len(held)}건 / 전체 {len(results)}건. "
            + ("일부 주장은 근거가 부족해 결론을 확정하지 않았습니다." if held else "모든 주장에 대해 비교 근거를 확보했습니다."),
            verdict=verdict.value,
        )
        step("검증 보고서 생성", "ok", "출처(접수번호)와 계산식을 포함한 보고서를 생성했습니다.")

        report = self._report(run_id, text, verdict, results, steps, limitations,
                              company=company, period={"year": target_year, "label": period_label})
        if self._store is not None:
            self._store.save(report)
        return report

    # -------------------------------------------------------------- helpers
    def _cross_check(self, company: Corp, year: int, bundle: dict[str, Any], step) -> None:
        prev_bundle = self._fetch(company.code, year - 1)
        notes: list[str] = []
        checked = 0
        for fs, resp in bundle["responses"].items():
            if resp is None:
                continue
            prev_resp = prev_bundle["responses"].get(fs)
            if prev_resp is None:
                continue
            curr_prev, _ = amount(resp.rows, "revenue", "frmtrm")
            prior_curr, _ = amount(prev_resp.rows, "revenue", "thstrm")
            if curr_prev is None or prior_curr is None or prior_curr == 0:
                continue
            checked += 1
            diff = abs(curr_prev - prior_curr) / abs(prior_curr) * Decimal(100)
            if diff > Decimal("0.5"):
                notes.append(f"{FS_DIVS[fs]} 전기 매출 불일치 {diff:.2f}% (재작성 가능성)")
        if notes:
            step("기간 정합성 점검", "warn", "; ".join(notes) + " - 전기 수치가 재작성되었을 수 있습니다.")
        elif checked:
            step("기간 정합성 점검", "ok", f"{year - 1} 사업보고서의 당기 수치와 {year} 사업보고서의 전기 수치가 일치합니다.")
        else:
            step("기간 정합성 점검", "warn", "전기 사업보고서를 확보하지 못해 정합성 점검을 생략했습니다.")

    def _unsupported(self, claim: Claim, company: Corp) -> ClaimResult:
        return ClaimResult(
            raw=claim.raw, metric=claim.metric or "", metric_label=claim.metric_label,
            claim_type=claim.claim_type, company_name=company.name, corp_code=company.code,
            claimed=None, computed={}, gap=None, verdict=Verdict.INSUFFICIENT.value,
            reason="검증 가능한 재무지표·수치를 문장에서 찾지 못했습니다",
            basis_used=None, formula="", period_label="", source={}, notes=claim.notes,
        )

    def _evaluate(self, claim: Claim, company: Corp, year: int, bundle: dict[str, Any],
                  available: list[str], period_label: str) -> ClaimResult:
        metric = claim.metric or "revenue"
        responses: dict[str, FsResponse] = {fs: bundle["responses"][fs] for fs in available}
        computed: dict[str, Decimal | None] = {}
        formula = ""
        source: dict[str, Any] = {}
        reference: dict[str, Any] = {}

        # 정성 주장 ---------------------------------------------------------
        if claim.claim_type == "qualitative":
            for fs, resp in responses.items():
                liab, liab_row = amount(resp.rows, "liabilities")
                eq, _ = amount(resp.rows, "equity")
                reference[fs] = _s(debt_ratio(liab, eq))
                if liab_row is not None and not source:
                    source = self._row_source(liab_row, resp, year)
            return ClaimResult(
                raw=claim.raw, metric=metric, metric_label=claim.metric_label,
                claim_type=claim.claim_type, company_name=company.name, corp_code=company.code,
                claimed=None, computed={k: v for k, v in reference.items()}, gap=None,
                verdict=Verdict.AMBIGUOUS.value,
                reason="‘거의 없다’는 판정 임계값이 없어 참·거짓을 확정할 수 없습니다. 참고 지표만 제시합니다.",
                basis_used=None, formula="부채비율 = 부채총계 / 자본총계 × 100",
                period_label=period_label, source=source, reference=reference,
                notes=claim.notes,
            )

        # 수준(level) 주장: 비율 지표만 비교 가능 ---------------------------
        if claim.claim_type == "level":
            if metric == "debt_ratio":
                for fs, resp in responses.items():
                    liab, row = amount(resp.rows, "liabilities")
                    eq, _ = amount(resp.rows, "equity")
                    computed[fs] = debt_ratio(liab, eq)
                    if row and not source:
                        source = self._row_source(row, resp, year)
                formula = "부채비율 = 부채총계 / 자본총계 × 100"
            else:
                return ClaimResult(
                    raw=claim.raw, metric=metric, metric_label=claim.metric_label,
                    claim_type=claim.claim_type, company_name=company.name, corp_code=company.code,
                    claimed=_s(claim.claimed_value), computed={}, gap=None,
                    verdict=Verdict.INSUFFICIENT.value,
                    reason="절대금액 주장은 공시 표시 단위 확정 전에는 비교하지 않습니다(단위 오인 위험).",
                    basis_used=None, formula="", period_label=period_label, source={}, notes=claim.notes,
                )

        # 증감률 주장 -------------------------------------------------------
        else:
            for fs, resp in responses.items():
                curr, row_c = amount(resp.rows, metric, "thstrm")
                prev, _ = amount(resp.rows, metric, "frmtrm")
                computed[fs] = growth_rate(prev, curr)
                if row_c is not None and not formula and computed[fs] is not None:
                    formula = (
                        f"{claim.metric_label} 증감률 = ({curr:,} - {prev:,}) / {prev:,} × 100 "
                        f"= {_s(computed[fs])}%"
                    )
                    source = self._row_source(row_c, resp, year)

        judgement = judge_across_bases(claim.claimed_value, computed, claim.basis)
        return ClaimResult(
            raw=claim.raw, metric=metric, metric_label=claim.metric_label,
            claim_type=claim.claim_type, company_name=company.name, corp_code=company.code,
            claimed=_s(claim.claimed_value),
            computed={k: _s(v) for k, v in computed.items()},
            gap=_s(judgement.gap), verdict=judgement.verdict.value, reason=judgement.reason,
            basis_used=judgement.basis_used, formula=formula, period_label=period_label,
            source=source, notes=claim.notes,
        )

    def _row_source(self, row: dict[str, Any], resp: FsResponse, year: int) -> dict[str, Any]:
        rcept = str(row.get("rcept_no", "")).strip()
        return {
            "rcept_no": rcept,
            "url": DART_VIEWER.format(rcept_no=rcept) if rcept else "",
            "report": f"{year} {REPRT_CODES.get(resp.reprt_code, resp.reprt_code)}",
            "fs_div": resp.fs_div,
            "fs_label": FS_DIVS.get(resp.fs_div, resp.fs_div),
            "account_nm": row.get("account_nm", ""),
            "account_id": row.get("account_id", ""),
            "thstrm_nm": row.get("thstrm_nm", ""),
            "frmtrm_nm": row.get("frmtrm_nm", ""),
            "currency": row.get("currency", ""),
        }

    def _source(self, responses: dict[str, FsResponse]) -> dict[str, Any]:
        for resp in responses.values():
            if resp.rows:
                return self._row_source(resp.rows[0], resp, resp.year)
        return {}

    def _report(self, run_id, text, verdict, results, steps, limitations, *, company, period) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "input_text": text,
            "verdict": verdict.value,
            "company": {"name": company.name, "corp_code": company.code, "stock_code": company.stock_code}
            if company else None,
            "period": period,
            "claims": [asdict(r) for r in results],
            "steps": [asdict(s) for s in steps],
            "limitations": limitations,
            "api_calls": getattr(self.client, "call_count", 0),
            "planner": self._planner_dict(),
            "engine": "FactLens prototype v0.2",
        }

    def _planner_dict(self) -> dict[str, Any]:
        info = getattr(self, "_planner_info", None)
        if info is None:
            return {"name": "rule", "model": "", "latency_ms": 0, "fallback": False, "detail": ""}
        return {
            "name": info.name,
            "model": info.model,
            "latency_ms": info.latency_ms,
            "fallback": info.fallback,
            "detail": info.detail,
        }
