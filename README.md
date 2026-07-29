# 자산 데일리 브리핑 (Daily Asset Slack Brief)

금융자산 시트를 매일 자동으로 읽어 **전일 대비 변동**을 계산하고,
Slack 전용 채널로 요약+상세 브리핑을 발송하는 자동화입니다.
**GitHub Actions로 완전 무인 실행**되며 Claude/커넥터에 의존하지 않습니다.

- **집계 범위**: 시트의 **모든 입력 탭(이수진 · 박현익)** 을 합산한 가계 전체 기준
  (보유자별 비중·수익률을 별도 섹션으로 표시. 부부가 같은 계좌·종목을 보유해도
  보유자를 key에 포함해 각각 집계됩니다.)
- **데이터 출처**: Google 스프레드시트 `금융자산현황표` (링크 공개 = anyone reader)
- **발송 채널**: Slack `#자산-데일리브리핑` (`C0BHDG42Y2K`)
- **발송 시각**: 매일 **08:00 KST** (워크플로 cron `0 23 * * *` UTC)
- **비교 기준**: 저장소에 커밋된 전일 스냅샷과 오늘 값을 비교

## ⚙️ 활성화(최초 1회) — Slack 웹훅 등록

자동 발송을 켜려면 Slack 수신 웹훅 1개만 저장소 시크릿으로 등록하면 됩니다.

1. **Slack 수신 웹훅 생성**
   - https://api.slack.com/apps → *Create New App* → *From scratch* → 워크스페이스 선택
   - 좌측 *Incoming Webhooks* → **Activate Incoming Webhooks** 켜기
   - *Add New Webhook to Workspace* → 채널 **#자산-데일리브리핑** 선택 → *Allow*
   - 생성된 `https://hooks.slack.com/services/...` URL 복사
2. **GitHub 시크릿 등록**
   - 저장소 → *Settings* → *Secrets and variables* → *Actions* → **New repository secret**
   - Name: `SLACK_WEBHOOK_URL` / Value: 위 URL → *Add secret*

등록 후에는 매일 08:00 KST에 자동 발송됩니다.
시크릿이 없으면 워크플로는 실행되되 발송만 생략(로그에 미리보기 출력)합니다.

## 동작 방식

`.github/workflows/daily-brief.yml` 가 매일 실행하는 흐름:

1. `generate.py` 가 시트를 xlsx로 내려받아(공개 링크) 보유 종목 시트를 파싱
   - 탭 순서/gid에 의존하지 않고 `종목명`·`평가금액` 헤더가 있는 표를 자동 탐색
2. `data/latest.json`(전일 스냅샷)과 비교해 변동 계산 (`brief.py` 재사용)
3. Slack Block Kit 메시지 발송 + `data/brief.html`(열람용 HTML) 생성
4. `data/latest.json` 갱신 · `data/snapshots/<날짜>.json` 보관 · `data/history.csv` 누적 후 커밋

### 실시간 시세 자동 계산 (LIVE_PRICES)

워크플로는 `LIVE_PRICES=1` 로 실행되어, 시트의 **수량(수량 열)** 에 현재가를 곱해
평가금액을 자동 산출합니다. 즉 **시트에는 수량만 정확히 유지**하면 됩니다.

- **국내 ETF/주식**(티커에 숫자 포함, 예 `379810`·`0053L0`·`A395160`): 네이버 금융 현재가 × 수량
- **미국 주식/ETF**(순수 알파벳, 예 `GOOGL`·`SPY`·`SPCX`): Stooq 종가 × 수량 × USD/KRW 환율
- **티커 없음**(예수금·CMA 등): 시트 평가금액 그대로 사용
- **시트 평가금액이 0인 종목**(매도·이관 등): 수량이 남아 있어도 **0 유지**(유령 종목 부활 방지)
- **시세 조회 실패**: 해당 종목만 시트 평가금액으로 자동 폴백 → 브리핑은 정상 발송

> 시세는 브리핑 하단에 출처·환율·조회 실패 건수가 표기됩니다. 시세를 끄고 시트의
> 평가금액을 그대로 쓰려면 워크플로 env에서 `LIVE_PRICES` 를 제거하세요.

## 브리핑에 담기는 내용 (요약 + 상세)

- **총 평가금액** 및 전일 대비 증감액/증감률
- **총 수익률** (평가손익·매입금액)
- **성격별 현황**: 위험자산·지수·원자재·안전자산·현금성 비중 및 전일 대비
- **오늘의 상승/하락 TOP**: 개별 종목 평가금액 변동 상위 (첫 실행 시엔 상위 종목)
- **계좌별 현황**: 증권사·계좌별 평가금액 및 전일 대비

## 파일 구조

| 경로 | 설명 |
|---|---|
| `.github/workflows/daily-brief.yml` | 매일 08:00 KST 실행 워크플로 (수동 실행도 지원) |
| `generate.py` | 시트 xlsx 다운로드·파싱, 시세 반영, Slack 발송, 스냅샷 저장 (Actions용) |
| `prices.py` | 실시간 시세 조회(네이버·Stooq·환율) 및 수량×현재가 평가금액 산출 |
| `brief.py` | 파싱·집계·전일대비·렌더러(마크다운/Slack/HTML) 핵심 모듈 |
| `data/latest.json` | 가장 최근(전일) 스냅샷 — 다음 실행의 비교 기준 |
| `data/history.csv` | 일별 총자산·수익률·전일대비 누적 기록 |
| `data/snapshots/<날짜>.json` | 날짜별 스냅샷 보관 |
| `data/brief.html` | 최신 브리핑 HTML (열람용) |

## 수동 실행 / 테스트

- GitHub UI: *Actions* → **자산 데일리 브리핑** → *Run workflow*
  - `dry_run` 체크 시 스냅샷 저장·Slack 발송 없이 파싱만 검증
- 로컬(마크다운 입력으로 렌더만 확인, 네트워크 불필요):
  ```bash
  python3 generate.py --input data/today_raw.md --no-slack --dry-run
  ```

## 설정 변경

- **발송 시각**: 워크플로 `cron` (UTC 기준, 08:00 KST = `0 23 * * *`)
- **채널**: Slack 웹훅이 연결된 채널로 발송됨(웹훅 재생성으로 변경)
- **성격 순서/이모지**: `brief.py` 의 `NATURE_ORDER`, `NATURE_EMOJI`
- **TOP 개수**: `brief.py` 의 `compute_summary` 내 `[:5]`

## 관련 식별자

- 스프레드시트 ID: `1MLuYEdJUUuhQ6V3hp7WSmsIFeFRo9LIHL23_wcdezFw`
- Slack 채널: `#자산-데일리브리핑` (`C0BHDG42Y2K`)
