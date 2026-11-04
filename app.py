"""FactLens AI - 예선 프로토타입 (Streamlit).

실행:
    streamlit run app.py

표시되는 모든 판정은 OpenDART 공시 응답과 로컬 계산 결과이며, LLM 이 만든 수치가 아니다.
"""
from __future__ import annotations

import streamlit as st

from factlens import config
from factlens.agent import VerificationAgent
from factlens.llm import build_planner
from factlens.opendart import OpenDartError
from factlens.store import RunStore

SAMPLES = [
    "삼성전자 매출이 지난해보다 40% 증가했고 부채가 거의 없다.",
    "삼성전자 매출이 지난해보다 16% 증가했다.",
]

VERDICT_STYLE = {
    "일치": ("#2C5F4F", "#EAF1ED"),
    "부분 일치": ("#9A6B22", "#F7F1E4"),
    "불일치": ("#8A3A30", "#F6EAE7"),
    "기준 불명확": ("#5A4A7A", "#EFEBF5"),
    "근거 부족": ("#6B7885", "#F0F2F4"),
}
STEP_COLOR = {"ok": "#2C5F4F", "warn": "#9A6B22", "hold": "#8A3A30", "error": "#8A3A30"}

st.set_page_config(page_title="FactLens AI", layout="wide")

st.markdown(
    """
<style>
  .block-container {padding-top: 2.2rem; max-width: 1180px;}
  h1, h2, h3 {color: #0F2740; letter-spacing: -0.02em;}
  .fl-sub {color: #6B7885; font-size: 0.95rem; margin-top: -0.6rem;}
  .fl-badge {display:inline-block; padding:0.35rem 0.8rem; border-radius:3px;
             font-size:1.05rem; font-weight:700; letter-spacing:-0.01em;}
  .fl-quote {border-left:3px solid #0F2740; padding:0.5rem 0 0.5rem 0.9rem;
             color:#22262B; font-size:1.02rem; background:#F4F6F8;}
  .fl-label {color:#6B7885; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.04em;}
  .fl-num {color:#0F2740; font-size:1.35rem; font-weight:700;}
  .fl-step {font-size:0.9rem; color:#22262B; margin:0.15rem 0;}
  .fl-dot {font-weight:700;}
  .fl-src {font-size:0.85rem; color:#24425F;}
  .fl-note {font-size:0.83rem; color:#6B7885;}
</style>
""",
    unsafe_allow_html=True,
)

st.title("FactLens AI")
st.markdown(
    '<p class="fl-sub">투자 관련 주장을 공식 공시와 대조하고, 수치를 별도 계산 모듈로 재계산해 '
    "판정 근거와 한계를 함께 제시합니다. · LLM 주장 구조화 + OpenDART 조회 + Python 재계산</p>",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_agent(mode: str) -> VerificationAgent:
    return VerificationAgent(store=RunStore(), planner=build_planner(mode))


def render_badge(verdict: str) -> str:
    fg, bg = VERDICT_STYLE.get(verdict, ("#6B7885", "#F0F2F4"))
    return (
        f'<span class="fl-badge" style="color:{fg};background:{bg};'
        f'border:1px solid {fg}33">{verdict}</span>'
    )


def render_report(report: dict) -> None:
    company = report.get("company") or {}
    period = report.get("period") or {}
    st.markdown(f"### 판정 &nbsp; {render_badge(report['verdict'])}", unsafe_allow_html=True)
    planner = report.get("planner") or {}
    planner_label = {"muse": "Muse", "codex": "LLM", "llm": "LLM", "rule": "규칙 기반"}.get(
        planner.get("name") or "", "규칙 기반")
    if planner.get("model"):
        planner_label += f" {planner['model']}"
    if planner.get("latency_ms"):
        planner_label += f" {planner['latency_ms'] / 1000:.1f}s"
    if planner.get("fallback"):
        planner_label += " (폴백)"
    vision_label = st.session_state.get("vision_meta")
    meta = " · ".join(
        filter(
            None,
            [
                company.get("name", ""),
                f"고유번호 {company.get('corp_code', '')}" if company.get("corp_code") else "",
                period.get("label", ""),
                f"분석 {planner_label}",
                vision_label or "",
                f"API 호출 {report.get('api_calls', 0)}회",
            ],
        )
    )
    st.markdown(f'<p class="fl-note">{meta}</p>', unsafe_allow_html=True)

    for claim in report["claims"]:
        st.markdown("---")
        st.markdown(f'<div class="fl-quote">{claim["raw"]}</div>', unsafe_allow_html=True)
        st.write("")
        cfs = claim["computed"].get("CFS")
        ofs = claim["computed"].get("OFS")
        cols = st.columns([1.1, 1.3, 1.3, 1.0, 1.1])
        labels = [
            ("주장", f'{claim["claimed"]}%' if claim["claimed"] else "수치 없음"),
            ("계산 · 연결(CFS)", f"{cfs}%" if cfs else "없음"),
            ("계산 · 별도(OFS)", f"{ofs}%" if ofs else "없음"),
            ("차이", f'{claim["gap"]}%p' if claim["gap"] else "-"),
            ("판정", claim["verdict"]),
        ]
        for col, (label, value) in zip(cols, labels):
            with col:
                st.markdown(f'<div class="fl-label">{label}</div>', unsafe_allow_html=True)
                if label == "판정":
                    st.markdown(render_badge(str(value)), unsafe_allow_html=True)
                else:
                    st.markdown(f'<div class="fl-num">{value}</div>', unsafe_allow_html=True)

        if claim.get("formula"):
            st.code(claim["formula"], language="text")
        st.markdown(f'<p class="fl-note">판정 근거: {claim["reason"]}</p>', unsafe_allow_html=True)

        src = claim.get("source") or {}
        if src:
            link = f' · <a href="{src.get("url")}">원문 열기</a>' if src.get("url") else ""
            st.markdown(
                f'<p class="fl-src">출처: {src.get("report", "")} {src.get("fs_label", "")} · '
                f'{src.get("account_nm", "")} · 접수번호 {src.get("rcept_no", "")}{link}</p>',
                unsafe_allow_html=True,
            )
        if claim.get("reference"):
            ref = ", ".join(f"{k} {v}%" for k, v in claim["reference"].items() if v)
            st.markdown(f'<p class="fl-src">참고 부채비율: {ref}</p>', unsafe_allow_html=True)
        for note in claim.get("notes", []):
            st.markdown(f'<p class="fl-note">· {note}</p>', unsafe_allow_html=True)

    with st.expander("실행 기록 (단계별)", expanded=False):
        for s in report["steps"]:
            color = STEP_COLOR.get(s["status"], "#6B7885")
            st.markdown(
                f'<p class="fl-step"><span class="fl-dot" style="color:{color}">●</span> '
                f'<b>{s["order"]}. {s["title"]}</b> — {s["detail"]}</p>',
                unsafe_allow_html=True,
            )
    with st.expander("한계 및 적용 범위", expanded=False):
        for item in report.get("limitations", []):
            st.markdown(f"- {item}")
    st.caption(
        "본 화면은 공시 수치 대조 결과이며 투자 권유가 아닙니다. "
        f"엔진: {report.get('engine', '')} · 실행 ID {report['run_id']}"
    )


with st.sidebar:
    st.markdown("### 주장 구조화")
    mode_label = st.radio("방식", ["LLM 우선 (실패 시 규칙 폴백)", "규칙 기반"], index=0,
                          label_visibility="collapsed")
    planner_mode = "auto" if mode_label.startswith("LLM") else "rule"
    st.caption("LLM은 문장 해석만 담당합니다. 수치는 OpenDART와 Python이 만듭니다.")
    st.markdown("---")
    st.markdown("### 상태")
    key_ok = config.has_api_key()
    st.markdown(("🟢 " if key_ok else "🔴 ") + f"DART_API_KEY: {config.key_status()}")
    if not key_ok:
        st.caption("프로젝트 루트 .env 파일에 DART_API_KEY 값을 입력한 뒤 새로고침하세요.")
    st.markdown("---")
    st.markdown("### 최근 검증 기록")
    try:
        rows = RunStore().list(10)
    except Exception:
        rows = []
    if not rows:
        st.caption("기록 없음")
    for row in rows:
        st.markdown(
            f'<p class="fl-note">{row["created_at"][5:16]} · <b>{row["verdict"]}</b><br>'
            f'{row["input_text"][:38]}</p>',
            unsafe_allow_html=True,
        )
    st.markdown("---")
    st.markdown("### 범위")
    st.caption("연간 사업보고서 · 연결/별도 재무제표 · 매출/이익/부채비율")
    st.caption("주가 예측, 매매 추천, 계좌 연동은 제공하지 않습니다.")

default_text = st.session_state.get("input_text", SAMPLES[0])

input_tab, image_tab = st.tabs(["문장으로 검증", "이미지로 검증"])

with image_tab:
    st.caption(
        "캡처·메신저 스크린샷을 그대로 올리면 Muse가 이미지 속 주장 문장을 읽어 검증합니다. "
        "이미지에서 읽은 것은 **문장뿐**이고, 수치는 여전히 공시와 Python이 만듭니다."
    )
    up = st.file_uploader(
        "이미지 업로드", type=["png", "jpg", "jpeg", "webp"], label_visibility="collapsed"
    )
    if up is not None:
        st.image(up, caption=f"{up.name} · {up.size // 1024}KB", width=420)
    img_run = st.button("이미지에서 문장 읽고 검증", type="primary", use_container_width=False)
    if img_run:
        if not up:
            st.warning("이미지를 선택하세요.")
        elif not key_ok:
            st.error("DART_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요.")
        else:
            with st.spinner("이미지에서 문장을 읽는 중..."):
                try:
                    from factlens.vision import VisionError, extract_claim_text

                    extracted = extract_claim_text(up.getvalue(), filename=up.name)
                except VisionError as exc:
                    extracted = None
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001
                    extracted = None
                    st.error(f"이미지 처리 중 오류: {type(exc).__name__}")
            if extracted is not None:
                for note in extracted.notes:
                    st.caption(f"· {note}")
                if extracted.ok:
                    st.session_state["input_text"] = extracted.text
                    st.session_state["vision_meta"] = (
                        f"이미지 판독 {extracted.model} · {extracted.latency_ms / 1000:.1f}s"
                    )
                    with st.spinner("공시 조회 및 재계산 중..."):
                        try:
                            st.session_state["report"] = get_agent(planner_mode).run(extracted.text)
                        except OpenDartError as exc:
                            st.error(f"OpenDART 오류 [{exc.code}] {exc.message}")
                        except RuntimeError as exc:
                            st.error(str(exc))
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"예상하지 못한 오류: {type(exc).__name__}")
                else:
                    st.warning("이미지에서 검증할 주장을 찾지 못했습니다.")

with input_tab:
    text = st.text_area("검증할 문장", value=default_text, height=90, key="input_text")

    col_a, col_b, _ = st.columns([1, 1, 3])
    run = col_a.button("검증 실행", type="primary", use_container_width=True)
    if col_b.button("예시 2", use_container_width=True):
        st.session_state["input_text"] = SAMPLES[1]
        st.rerun()

    if run:
        if not key_ok:
            st.error("DART_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요.")
        elif not text.strip():
            st.warning("검증할 문장을 입력하세요.")
        else:
            with st.spinner("공시 조회 및 재계산 중..."):
                try:
                    st.session_state["report"] = get_agent(planner_mode).run(text)
                except OpenDartError as exc:
                    st.error(f"OpenDART 오류 [{exc.code}] {exc.message}")
                    if exc.code in {"020", "HTTP_429"}:
                        st.caption("호출 한도 초과입니다. 잠시 후 다시 시도하세요.")
                    elif exc.code in {"010", "011", "012"}:
                        st.caption("인증키 또는 접근 IP 설정을 확인하세요.")
                except RuntimeError as exc:
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"예상하지 못한 오류: {type(exc).__name__}")
                    st.caption("로그에 인증키가 포함되지 않도록 예외 문자열을 그대로 출력하지 않습니다.")

report = st.session_state.get("report")
if report:
    render_report(report)
else:
    st.info("문장을 입력하고 '검증 실행'을 누르면 공시 조회 → 재계산 → 판정 순서로 실행됩니다.")
