"""OpenDART 연결 테스트.

실행: python scripts/test_connection.py

- 인증키 값은 출력하지 않는다(설정 여부만 표시).
- 각 단계의 성공/실패를 그대로 보고하고, 결과를 tests/results/ 에 JSON 으로 남긴다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factlens import config  # noqa: E402
from factlens.calc import growth_rate  # noqa: E402
from factlens.metrics import amount  # noqa: E402
from factlens.opendart import FS_DIVS, NO_DATA, OpenDartClient, OpenDartError  # noqa: E402

TARGET = "삼성전자"
results: list[dict] = []


def record(test: str, ok: bool, detail: str, **extra) -> None:
    results.append({"test": test, "ok": ok, "detail": detail, **extra})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {test}: {detail}")


def main() -> int:
    print("=" * 72)
    print("FactLens AI · OpenDART 연결 테스트")
    print(f"실행 시각: {datetime.now().isoformat(timespec='seconds')}")
    print(f"인증키 상태: {config.key_status()}  (값은 출력하지 않습니다)")
    print("=" * 72)

    if not config.has_api_key():
        record("환경변수", False, "DART_API_KEY 미설정")
        return 2

    client = OpenDartClient()

    # 1) 고유번호 전체 목록 ------------------------------------------------
    try:
        corps = client.corp_codes()
        record("corpCode.xml 수신", True, f"법인 {len(corps):,}건 파싱")
    except OpenDartError as exc:
        record("corpCode.xml 수신", False, f"[{exc.code}] {exc.message}")
        return 3
    except Exception as exc:  # noqa: BLE001
        record("corpCode.xml 수신", False, f"{type(exc).__name__}")
        return 3

    # 2) 대상 기업 식별 ----------------------------------------------------
    target = next((c for c in corps if c.name == TARGET), None)
    if target is None:
        record("기업 식별", False, f"{TARGET} 를 목록에서 찾지 못했습니다")
        return 4
    record("기업 식별", True, f"{target.name} / 고유번호 {target.code} / 종목코드 {target.stock_code or '없음'}",
           corp_code=target.code, stock_code=target.stock_code)

    # 3) 사업보고서 재무제표 조회 (최근 연도부터 역순) ----------------------
    year = datetime.now().year - 1
    data: dict[str, dict] = {}
    used_year = None
    for candidate in range(year, year - 4, -1):
        got = {}
        for fs in ("CFS", "OFS"):
            try:
                resp = client.financial_statements(target.code, candidate, fs_div=fs)
            except OpenDartError as exc:
                if exc.code == NO_DATA:
                    continue
                record(f"{candidate} {FS_DIVS[fs]} 조회", False, f"[{exc.code}] {exc.message}")
                continue
            rev_curr, row = amount(resp.rows, "revenue", "thstrm")
            if rev_curr is None:
                continue
            got[fs] = {"resp": resp, "row": row, "curr": rev_curr}
        if got:
            used_year = candidate
            data = got
            break

    if not data:
        record("재무제표 조회", False, "최근 4개 사업연도에서 매출액을 찾지 못했습니다")
        return 5

    for fs, item in data.items():
        row = item["row"]
        record(
            f"{used_year} {FS_DIVS[fs]} 매출액",
            True,
            f"{row.get('account_nm')} 당기 {row.get('thstrm_amount')} / 전기 {row.get('frmtrm_amount')} "
            f"(접수번호 {row.get('rcept_no')})",
            rcept_no=row.get("rcept_no"),
            thstrm=row.get("thstrm_amount"),
            frmtrm=row.get("frmtrm_amount"),
        )

    # 4) 매출 증가율 계산 --------------------------------------------------
    computed: dict[str, str | None] = {}
    for fs, item in data.items():
        prev, _ = amount(item["resp"].rows, "revenue", "frmtrm")
        rate = growth_rate(prev, item["curr"])
        computed[fs] = str(rate) if rate is not None else None
        record(
            f"{used_year} {FS_DIVS[fs]} 매출증가율",
            rate is not None,
            f"({item['curr']:,} - {prev:,}) / {prev:,} × 100 = {rate}%",
            growth=str(rate) if rate else None,
        )

    # 5) 전기 수치 정합성(재작성 점검) -------------------------------------
    for fs, item in data.items():
        try:
            prev_resp = client.financial_statements(target.code, used_year - 1, fs_div=fs)
        except OpenDartError as exc:
            record(f"{used_year - 1} {FS_DIVS[fs]} 전기 대조", False, f"[{exc.code}] {exc.message}")
            continue
        prior_thstrm, _ = amount(prev_resp.rows, "revenue", "thstrm")
        prev_frmtrm, _ = amount(item["resp"].rows, "revenue", "frmtrm")
        if prior_thstrm is None or prev_frmtrm is None:
            record(f"{used_year - 1} {FS_DIVS[fs]} 전기 대조", False, "비교 가능한 값 없음")
            continue
        same = prior_thstrm == prev_frmtrm
        record(
            f"{used_year - 1} {FS_DIVS[fs]} 전기 대조",
            True,
            f"{used_year - 1} 보고서 당기 {prior_thstrm:,} vs {used_year} 보고서 전기 {prev_frmtrm:,} "
            f"→ {'일치' if same else '불일치(재작성 가능성)'}",
            same=same,
        )

    print("-" * 72)
    print(f"API 호출 횟수: {client.call_count}회 / 사업연도 {used_year}")
    print(f"계산된 매출증가율: {computed}")

    out_dir = ROOT / "tests" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"api_connection_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_path.write_text(
        json.dumps(
            {
                "executed_at": datetime.now().isoformat(timespec="seconds"),
                "target": TARGET,
                "corp_code": target.code,
                "stock_code": target.stock_code,
                "year": used_year,
                "growth": computed,
                "api_calls": client.call_count,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    print(f"결과 저장: {out_path.relative_to(ROOT)}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
