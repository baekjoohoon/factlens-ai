"""명령행 실행.

예: python run_cli.py "삼성전자 매출이 지난해보다 40% 증가했고 부채가 거의 없다."
"""
from __future__ import annotations

import argparse
import json
import sys

from factlens import config
from factlens.agent import VerificationAgent
from factlens.llm import build_planner
from factlens.opendart import OpenDartError
from factlens.store import RunStore

DEFAULT_TEXT = "삼성전자 매출이 지난해보다 40% 증가했고 부채가 거의 없다."


def main() -> int:
    parser = argparse.ArgumentParser(description="FactLens AI 검증 CLI")
    parser.add_argument("text", nargs="*", default=None, help="검증할 문장")
    parser.add_argument("--json", action="store_true", help="보고서를 JSON 으로 출력")
    parser.add_argument("--no-cross-check", action="store_true", help="전기 재작성 점검 생략")
    parser.add_argument("--planner", choices=["auto", "muse", "llm", "rule", "codex"], default="auto",
                        help="주장 구조화 방식 (auto=LLM 우선, 실패 시 규칙)")
    parser.add_argument("--image", help="이미지 파일에서 주장 문장을 읽어 검증 (Muse 비전)")
    args = parser.parse_args()

    text = " ".join(args.text) if args.text else DEFAULT_TEXT
    if not config.has_api_key():
        print("DART_API_KEY 미설정: .env 파일에 인증키를 입력하세요.", file=sys.stderr)
        return 2

    if args.image:
        from factlens.vision import VisionError, extract_claim_text

        try:
            with open(args.image, "rb") as fh:
                extracted = extract_claim_text(fh.read(), filename=args.image)
        except VisionError as exc:
            print(f"이미지 판독 실패: {exc}", file=sys.stderr)
            return 4
        except OSError as exc:
            print(f"이미지를 열 수 없습니다: {exc}", file=sys.stderr)
            return 4
        for note in extracted.notes:
            print(f"· {note}", file=sys.stderr)
        if not extracted.ok:
            print("이미지에서 검증할 주장을 찾지 못했습니다.", file=sys.stderr)
            return 5
        print(f"이미지 판독({extracted.model}, {extracted.latency_ms}ms): {extracted.text}", file=sys.stderr)
        text = extracted.text

    agent = VerificationAgent(store=RunStore(), cross_check=not args.no_cross_check,
                              planner=build_planner(args.planner))
    try:
        report = agent.run(text)
    except OpenDartError as exc:
        print(f"OpenDART 오류 [{exc.code}] {exc.message}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"\n입력: {text}")
    print(f"판정: {report['verdict']}  (기업: {(report.get('company') or {}).get('name')})")
    planner = report.get("planner") or {}
    print(f"분석: {planner.get('name')} {planner.get('model') or ''} "
          f"{planner.get('latency_ms', 0)}ms {'(폴백)' if planner.get('fallback') else ''}")
    for step in report["steps"]:
        print(f"  [{step['status']:>4}] {step['order']}. {step['title']}: {step['detail']}")
    for claim in report["claims"]:
        print(f"\n- 주장: {claim['raw']}")
        print(f"  주장값 {claim['claimed']} / 계산 {claim['computed']} / 차이 {claim['gap']} / {claim['verdict']}")
        if claim["formula"]:
            print(f"  식: {claim['formula']}")
        if claim["source"]:
            print(f"  출처: {claim['source'].get('report')} {claim['source'].get('fs_label')} "
                  f"접수번호 {claim['source'].get('rcept_no')}")
        print(f"  근거: {claim['reason']}")
    print(f"\nAPI 호출 {report['api_calls']}회 · 실행 ID {report['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
