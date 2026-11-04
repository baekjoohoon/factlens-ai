"""예선 제출용 시나리오 테스트 (실제 LLM + 실제 OpenDART).

실행: python scripts/scenarios.py
결과: tests/results/scenarios_<timestamp>.json + 콘솔 표
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factlens import llm as llm_mod  # noqa: E402
from factlens.agent import VerificationAgent  # noqa: E402
from factlens.llm import AutoPlanner, MusePlanner, RulePlanner  # noqa: E402
from factlens.opendart import Corp, OpenDartError  # noqa: E402

CASES = [
    ("정확한 매출 증가율", "삼성전자 매출이 연결 기준으로 지난해보다 10.9% 증가했다.", "일치"),
    ("잘못된 매출 증가율", "삼성전자 매출이 작년보다 40% 늘었고 부채가 거의 없대.", "불일치"),
    ("기준 누락", "삼성전자 매출이 지난해보다 10.9% 증가했다.", "기준 불명확"),
    ("기업명 모호", "삼성 매출이 작년보다 40% 늘었다.", "기준 불명확"),
    ("정성적 부채 주장", "삼성전자 부채가 거의 없다더라.", "기준 불명확"),
    ("공시 데이터 없음", "존재하지않는회사 매출이 40% 증가했다.", "근거 부족"),
]


class FailingClient:
    def __init__(self):
        self.call_count = 0

    def corp_codes(self):
        return [Corp("00000001", "테스트기업", "000001")]

    def financial_statements(self, corp_code, year, *, reprt_code="11011", fs_div="CFS"):
        self.call_count += 1
        raise OpenDartError("HTTP_500", "서버 오류", retryable=True)


def _fake_run(payload_text):
    def fake(cmd, **kwargs):
        out_path = None
        for i, part in enumerate(cmd):
            if part == "-o" and i + 1 < len(cmd):
                out_path = cmd[i + 1]
        if out_path:
            Path(out_path).write_text(payload_text, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"model: test-model\n", b"")
    return fake


def run_real(text: str) -> dict:
    agent = VerificationAgent(store=None, planner=AutoPlanner(primary=MusePlanner(),
                                                              fallback=RulePlanner()))
    return agent.run(text)


def main() -> int:
    results = []
    for name, text, expected in CASES:
        try:
            report = run_real(text)
            actual = report["verdict"]
            planner = report.get("planner") or {}
            note = "; ".join(
                f"{c['metric_label']} 주장 {c['claimed']} / 계산 {c['computed']} / {c['verdict']}"
                for c in report["claims"]
            ) or (report["steps"][1]["detail"] if len(report["steps"]) > 1 else "")
            results.append({
                "case": name, "input": text, "expected": expected, "actual": actual,
                "pass": actual == expected,
                "planner": planner.get("name"), "model": planner.get("model"),
                "latency_ms": planner.get("latency_ms"), "fallback": planner.get("fallback"),
                "detail": note,
                "rcept": next((c["source"].get("rcept_no") for c in report["claims"] if c.get("source")), ""),
            })
        except Exception as exc:  # noqa: BLE001
            results.append({"case": name, "input": text, "expected": expected,
                            "actual": f"예외 {type(exc).__name__}", "pass": False, "detail": str(exc)[:120]})

    # 7) API 장애
    try:
        agent = VerificationAgent(client=FailingClient(), store=None, cross_check=False,
                                  planner=RulePlanner())
        report = agent.run("테스트기업 매출이 지난해보다 40% 증가했다.")
        statuses = [s["status"] for s in report["steps"]]
        results.append({
            "case": "API 장애 (5xx)", "input": "테스트기업 매출이 지난해보다 40% 증가했다.",
            "expected": "예외 없이 '근거 부족' + error 단계", "actual": f"{report['verdict']} / 단계 {statuses}",
            "pass": report["verdict"] == "근거 부족" and "error" in statuses,
            "detail": " ".join(s["detail"] for s in report["steps"] if s["status"] == "error")[:160],
        })
    except Exception as exc:  # noqa: BLE001
        results.append({"case": "API 장애 (5xx)", "expected": "예외 없이 처리",
                        "actual": f"예외 {type(exc).__name__}", "pass": False, "detail": str(exc)[:120]})

    # 8) LLM 이 잘못된 JSON 을 반환 (Muse 응답 본문을 직접 주입)
    class BadJsonPlanner(MusePlanner):
        def _post(self, payload):
            return {"model": self.model, "choices": [
                {"finish_reason": "stop", "message": {"content": "이건 JSON 이 아닙니다"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    try:
        planner = AutoPlanner(primary=BadJsonPlanner(), fallback=RulePlanner())
        claims, info = planner.parse("삼성전자 매출이 작년보다 40% 증가했다.")
        results.append({
            "case": "LLM 잘못된 JSON", "input": "삼성전자 매출이 작년보다 40% 증가했다.",
            "expected": "규칙 기반 폴백 + 주장 1건", "actual": f"폴백={info.fallback}, 주장 {len(claims)}건",
            "pass": info.fallback and len(claims) == 1,
            "detail": info.detail[:160],
        })
    except Exception as exc:  # noqa: BLE001
        results.append({"case": "LLM 잘못된 JSON", "expected": "규칙 기반 폴백 + 주장 1건",
                        "actual": f"예외 {type(exc).__name__}", "pass": False, "detail": str(exc)[:120]})

    print("\n| # | 사례 | 예상 | 실제 | 통과 |")
    print("|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        print(f"| {i} | {r['case']} | {r['expected']} | {r['actual']} | {'O' if r['pass'] else 'X'} |")

    out_dir = ROOT / "tests" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"scenarios_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
    print("\nsaved:", out.relative_to(ROOT))
    print("passed:", sum(1 for r in results if r["pass"]), "/", len(results))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
