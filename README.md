# FactLens AI — Meta Global AI Developer Hackathon 2026 제출본

> **English summary.** FactLens AI verifies investment claims found online against Korean
> DART annual-report filings. You paste a sentence **or upload a screenshot**; Meta Muse
> reads the claim, but every number is recomputed in Python `Decimal` from the filing, and the
> verdict is a pure function of those computed values. The model is never allowed to produce
> a number or a verdict, so results are deterministic even though the LLM runs every time.
> **Built during the hackathon:** `factlens/vision.py` (Muse vision), `MusePlanner` in
> `factlens/llm.py` (Meta Model API), the UI/CLI wiring, and 11 new tests. The calc / verdict /
> filing layers are the author's own earlier work and are reused unchanged — see §1.
> Repo: https://github.com/baekjoohoon/factlens-ai

온라인에서 접한 투자 관련 주장을 **금융감독원 OpenDART 공시**와 대조하고,
수치를 **별도 Python 계산 모듈**로 재계산해 판정과 근거·한계를 함께 제시하는 에이전트입니다.

- 공모전: Meta Global AI Developer Hackathon 2026 (제출 마감 2026-11-05 10:00 KST)
- 트랙: Indie · 테마: Build for Your World
- 팀: FactLens AI by Seonu_Papa (개인 참가 · 백주훈)
- 상태: **v1.1 해커톤 제출본** — Muse Spark 1.3 주장 구조화 + **Muse 비전 이미지 판독** + OpenDART 조회 + 결정적 계산 + 판정/보류
- 최종 확인: 2026-11-05 · 단위 테스트 62건 통과 · 시나리오 11건 통과

---

## 1. 지금 되는 것 / 안 되는 것

| 구분 | 항목 | 상태 |
|---|---|---|
| 구현 | 문장 → 검증 가능한 주장 분해 (**Meta Muse Spark 1.3 실호출**, 실패 시 규칙 기반 폴백) | 완료 |
| 구현 | **이미지(캡처·스크린샷) → 주장 문장 추출 (Muse 비전 입력)** | 완료 |
| 구현 | LLM 응답 JSON 스키마 강제 + 수치·근거문 2차 가드 | 완료 |
| 구현 | 기업명 → OpenDART 고유번호 식별(정확/별칭/종목코드/부분일치) | 완료 |
| 구현 | 사업보고서 연결·별도 재무제표 조회 | 완료 |
| 구현 | 매출 증가율·부채비율 계산(Decimal, 반올림 규칙 고정) | 완료 |
| 구현 | 주장값 대비 판정(일치/부분일치/불일치/기준 불명확/근거 부족) | 완료 |
| 구현 | 판정 보류 규칙(기업 미특정, 회계기준 충돌, 정성 표현, 단위 미확정) | 완료 |
| 구현 | 공시 접수번호·원문 링크 표시, 실행 기록 SQLite 저장 | 완료 |
| 구현 | API 오류(5xx·429·013)와 데이터 없음 구분 | 완료 |
| 미구현 | 분기·반기 보고서, 다중 기업 비교, 보고서 PDF 출력 | 본선 |
| 미구현 | 정확도(%) 측정 — 8건은 기능 확인 표본이며 정확도 지표가 아님 | 본선 |
| 미구현 | SNS 자동 수집, KIC 내부 데이터 연동, 주가/추천 | 범위 외 |

> **검증 상태**: OpenDART 실호출과 실제 Muse 호출은 2026-11-04~05에 완료했습니다.
> 결과는 `tests/results/`에 원본 JSON으로 남아 있습니다.

### 해커톤 기간에 만든 것

- `factlens/vision.py` — Muse 비전 입력으로 캡처에서 주장 문장 추출
  (MIME 판별, 1600px 다운스케일, NONE 처리, 추론토큰 진단)
- `factlens/llm.py`의 `MusePlanner` — Meta Model API 클라이언트, JSON 스키마 강제,
  수치 가드, 토큰 집계, 키 마스킹된 오류
- 이미지 탭 (UI) 과 `--image` (CLI) 배선
- `tests/test_vision.py` 11건 · 시나리오 9~11 (이미지 경로)

이 프로젝트의 기반이 된 **계산·판정·공시 조회 계층은 참가자 본인이 이전에 작성한
자신의 코드**이며, 변경 없이 그대로 사용했습니다. 해커톤 기간에 새로 작성한 것은
위 목록입니다.

---

## 2. 설치

```bash
cd C:/AI_Agent/FactLens_AI
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3. 인증키 설정

1. https://opendart.fss.or.kr/ 에서 오픈 API 인증키 발급
2. 프로젝트 루트의 `.env` 파일을 열어 아래처럼 입력

```dotenv
DART_API_KEY=발급받은키
```

- `.env` 는 `.gitignore` 에 등록되어 있어 커밋되지 않습니다.
- 키는 코드·로그·보고서에 출력하지 않습니다(`config.key_status()` 는 "설정됨"만 반환).
- `requests` 예외 문자열에는 요청 URL(키 포함)이 들어가므로 예외 타입만 기록합니다.

## 4. LLM 설정 — Meta Model API (Muse Spark)

주장 분해는 **Meta Model API**의 `muse-spark-1.3` 을 호출합니다.
OpenAI 호환 엔드포인트를 쓰므로 별도 SDK 없이 표준 라이브러리만으로 동작합니다.

```dotenv
MODEL_API_KEY=LLM_...            # https://dev.meta.ai/platform > API keys
FACTLENS_MUSE_MODEL=muse-spark-1.3
```

- Base URL `https://api.meta.ai/v1` · `Authorization: Bearer $MODEL_API_KEY`
- 호출 형태: `POST /chat/completions` + `response_format={"type":"json_schema", ...}` (`strict: true`)
- Muse 계열은 **추론 토큰을 먼저 소비**하므로 `max_tokens=2000`, `reasoning_effort="minimal"` 을 씁니다.
  토큰이 부족하면 본문이 비는데, 이 경우 `SchemaError` 로 명시적 실패 후 규칙 기반으로 폴백합니다.
- 실패(키 없음·HTTP 오류·빈 본문·JSON 위반)하면 **규칙 기반 파서로 자동 폴백**하고 사유를 실행 기록에 남깁니다.

> **지역 제약**: Muse Wonder(`muse-wonder-1.0`)는 대한민국에서 제공되지 않습니다.
> 본 프로젝트는 한국에서 사용 가능한 `muse-spark-1.3` 만 사용합니다.

### LLM이 하지 않는 일

- 재무 수치 생성 — 수치는 OpenDART 응답과 `calc.py` 만 사용합니다.
- 문장에 없는 숫자·근거문 — `_number_in_text` / 부분문자열 검사로 **판정 전에 차단**합니다.
- 최종 판정 — 판정은 `verdict.py` 의 순수 함수가 계산값으로만 결정합니다.

## 5. 실행

```bash
# (1) API 연결 테스트 - 가장 먼저 실행
python scripts/test_connection.py

# (2) 화면(Streamlit)
streamlit run app.py                 # 사이드바에서 LLM 우선 / 규칙 기반 선택

# (3) 명령행
python run_cli.py "삼성전자 매출이 지난해보다 40% 증가했고 부채가 거의 없다."
python run_cli.py --planner muse "삼성전자 매출이 작년보다 40% 늘었고 부채가 거의 없대."
python run_cli.py --planner rule "삼성전자 매출이 지난해보다 16% 증가했다."
python run_cli.py --json --planner muse "삼성전자 매출이 연결 기준으로 지난해보다 10.9% 증가했다."

# 이미지에서 문장을 읽어 검증 (Muse 비전)
python run_cli.py --image capture.png

# (4) 테스트
python -m pytest -q                  # 단위 62건 (네트워크 불필요)
python scripts/scenarios.py          # 시나리오 11건 (실제 Muse + OpenDART 호출)
```

`--planner` 값: `auto`(기본, Muse→실패 시 규칙) · `muse`(Muse만) · `rule`(규칙만) · `codex`(레거시)

---

## 6. 처리 흐름

```
사용자 문장
   │
   ├─ 0. 이미지 판독      vision.py       (이미지 입력 시) Muse 비전 → 문장만 추출
   ├─ 1. 주장 분해        llm.py          Muse 호출 → JSON 스키마 강제 → 수치·근거문 가드
   │                                      (실패 시 claims.py 규칙 기반 폴백)
   ├─ 2. 기업 식별        corp.py         기업명 → corp_code (모호하면 보류)
   ├─ 3. 회계기간 확정    agent.py        사업보고서 연도 확정(최근 연도 역순 조회)
   ├─ 4. 공시 조회        opendart.py     fnlttSinglAcntAll (CFS·OFS)
   ├─ 5. 기간 정합성      agent.py        전기 보고서 당기값 ↔ 당해 보고서 전기값 대조
   ├─ 6. 수치 재계산      calc.py         Decimal, ROUND_HALF_UP, 허용오차 규칙
   ├─ 7. 주장 대조·판정   verdict.py      ±1.0%p 일치 / ±3.0%p 부분일치 / 그 외 불일치
   └─ 8. 보고서 생성      agent.py        계산식 + 접수번호 + 한계 → SQLite 기록
```

LLM은 **1단계(문장 해석)** 에서만 호출됩니다. 2~8단계는 LLM이 개입하지 않는 결정적 계층입니다.

### 판정 규칙

| 판정 | 조건 |
|---|---|
| 일치 | \|주장 − 계산\| ≤ 1.0%p |
| 부분 일치 | ≤ 3.0%p |
| 불일치 | > 3.0%p |
| 기준 불명확 | 회계기준 미지정 상태에서 연결/별도 판정이 갈림 · 정성 표현 · 기업 후보 다수 |
| 근거 부족 | 계정 미발견 · 비교 가능 기간 없음 · 절대금액 주장(단위 미확정) |

허용오차는 "주장에 표기된 자릿수에서 발생하는 반올림 오차"를 흡수하기 위한 값이며,
정확도 지표가 아닙니다.

### 단위(unit) 처리 원칙

OpenDART `fnlttSinglAcntAll` 응답은 금액을 **회사가 보고한 표시 단위**로 반환하며,
응답에 단위 필드가 없습니다. 따라서:

- 증가율·부채비율처럼 **분자·분모가 같은 단위인 비율**만 판정에 사용합니다(단위 영향 없음).
- 절대금액 주장("매출 300조")은 단위 확정 전까지 **판정하지 않고 보류**합니다.
- 화면에는 공시 표시값을 그대로 보여주고 임의로 원 단위 환산을 하지 않습니다.

---

## 7. 디렉터리

```
FactLens_AI/
├─ app.py                    Streamlit 화면
├─ run_cli.py                명령행 실행 (--planner auto|llm|rule)
├─ scripts/
│  ├─ test_connection.py     API 연결 테스트
│  ├─ scenarios.py           시나리오 11건 실행 → tests/results/*.json
│  ├─ llm_smoke.py           LLM 단일 호출 확인
│  └─ check_original.py      공시 원문 대조
├─ factlens/
│  ├─ config.py              환경변수·경로
│  ├─ opendart.py            API 클라이언트(재시도·오류코드·캐시)
│  ├─ corp.py                기업명 → 고유번호
│  ├─ llm.py                 MusePlanner·JSON 스키마·2차 가드·폴백
│  ├─ vision.py              이미지 → 주장 문장 추출 (Muse 비전)
│  ├─ claims.py              주장 분해(규칙)
│  ├─ metrics.py             계정 선택 규칙(IFRS 태그 우선)
│  ├─ calc.py                결정적 계산
│  ├─ verdict.py             판정 규칙
│  ├─ agent.py               오케스트레이터
│  └─ store.py               SQLite 실행 기록
├─ data/corp_alias.json      별칭 사전(휴리스틱)
└─ tests/                    단위 테스트 62건
   ├─ test_calc.py 6 · test_claims.py 7 · test_verdict.py 8 · test_corp.py 6
   ├─ test_agent_offline.py 8 · test_llm.py 11 · test_faults.py 5 · test_vision.py 11
   └─ results/               실호출 원본 기록(JSON)
```

---

## 8. 데이터 출처와 이용 조건

| 데이터 | 출처 | 접근 | 비고 |
|---|---|---|---|
| 기업 고유번호 | OpenDART `corpCode.xml` | API | 7일 로컬 캐시 |
| 재무제표 | OpenDART `fnlttSinglAcntAll.json` | API | 사업보고서(11011), CFS/OFS |
| 공시 원문 | DART `dart.fss.or.kr/dsaf001/main.do?rcpNo=…` | 링크 | 원문 재배포하지 않고 접수번호·링크만 표시 |

- OpenDART는 금융감독원이 제공하는 공시 API이며, 이용 시 공식 안내
  (https://opendart.fss.or.kr/guide/main.do)의 이용약관·저작권 정책을 따릅니다.
- 호출 한도가 존재하므로(정책 변동 가능) 캐시와 재시도를 두었습니다. 한도 초과 시
  `[020]`/`HTTP_429` 로 사용자에게 그대로 알립니다.
- 실호출 기록: `tests/results/api_connection_*.json`, `tests/results/scenarios_*.json`

---

## 9. 한계 (숨기지 않고 기록)

1. 연간 사업보고서만 지원합니다(분기·반기 미지원).
2. 기업명 매칭은 문자열 기반이라 별칭 사전에 없는 표기는 인식되지 않을 수 있습니다.
3. LLM 폴백 시 규칙 기반 파서는 복문·비유·생략된 주어를 완전히 처리하지 못합니다.
4. 연결/별도 판정이 갈리면 결론을 내지 않고 보류합니다(의도된 동작).
5. 절대금액 주장은 단위 확정 전까지 보류합니다.
6. 정확도·시간절감 효과는 **측정하지 않았습니다.** 8건은 기능 확인 표본입니다.
7. Muse 응답 지연은 4~8초로 변동합니다(추론 토큰 포함). 판정은 계산값으로만 결정되므로 결과는 같습니다.
8. Muse는 추론 토큰을 먼저 쓰기 때문에 `max_tokens` 가 작으면 본문이 비어 실패합니다.
   기본값 2000 으로 두고, 비었을 때는 폴백 사유를 화면에 그대로 노출합니다.
8. codex CLI OAuth 세션이 없으면 LLM 단계가 규칙 기반으로 폴백됩니다(정상 동작이지만 LLM 기능은 비활성).

## 10. 테스트 결과

```
python -m pytest -q
62 passed
```

| 파일 | 건수 | 검증 대상 |
|---|---|---|
| `test_calc.py` | 6 | 쉼표·괄호 음수, 증가율, 0 나누기 가드, 부채비율, 차이·표기 |
| `test_claims.py` | 7 | 접속 어미 분해, 소수점 오분해 방지, 정성 주장, 기준·연도 |
| `test_verdict.py` | 8 | 허용오차, 부분 일치, 기준 충돌, 최악값 채택 |
| `test_corp.py` | 6 | 정확 일치, 종목코드, 비상장 동명 법인 다수 → 보류 |
| `test_agent_offline.py` | 8 | 오프라인 8단계 파이프라인, 보류 규칙, 단계 기록 |
| `test_llm.py` | 11 | 스키마 위반 6종 거부, 수치·근거문 가드, 폴백 |
| `test_faults.py` | 5 | API 5xx·429, LLM 깨진 JSON·스키마 위반·수치 창작 |
| `test_vision.py` | 11 | 빈/과대 이미지 거부, MIME 추정, NONE 처리, 추론토큰 소진 안내, 키 마스킹 |

- 파이프라인 테스트의 수치는 **합성값(SYNTHETIC)** 이며 실제 공시가 아닙니다.
- 시나리오 11건은 실제 Muse·OpenDART를 호출합니다: `python scripts/scenarios.py`
  (1~8: 문장 입력 / 9~11: 이미지 입력 경로)

## 11. 보안

- 인증키는 `DART_API_KEY` 환경변수로만 읽습니다.
- `.env` 는 Git 제외, `.env.example` 만 커밋합니다.
- 예외·로그·보고서에 키를 남기지 않습니다(요청 URL 미출력).
- 저장 데이터(SQLite)에는 공시 응답 요약과 판정만 저장하고 키는 저장하지 않습니다.
- LLM 실행은 `-s read-only`(쓰기 금지)·`--ephemeral`(세션 미보존)로 격리합니다.
