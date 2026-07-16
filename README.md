# 자산 데일리 브리핑 (Daily Asset Slack Brief)

금융자산 시트를 매일 자동으로 읽어 **전일 대비 변동**을 계산하고,
Slack 전용 채널로 요약+상세 브리핑을 발송하는 자동화입니다.

- **데이터 출처**: Google 스프레드시트 `금융자산현황표` (비공개 시트, 소유자 계정으로 인증 접근)
- **발송 채널**: Slack `#자산-데일리브리핑` (`C0BHDG42Y2K`)
- **발송 시각**: 매일 오전 8시 (KST)
- **비교 기준**: 저장소에 보관된 전일 스냅샷과 오늘 값을 비교

## 동작 방식

매일 오전 8시, 예약 트리거(Routine)가 새 세션을 띄우고 아래 순서를 수행합니다.

1. Google Drive 연동으로 시트를 읽어 `data/today_raw.md` 로 저장
2. `python3 brief.py --input data/today_raw.md` 실행
   - 보유 종목 파싱 → 총자산·성격별·계좌별 집계
   - `data/latest.json`(전일 스냅샷)과 비교해 변동 계산
   - `data/brief.md`(Slack 메시지) 생성
   - `data/latest.json` 갱신 · `data/snapshots/<날짜>.json` 보관 · `data/history.csv` 누적
3. `data/brief.md` 를 Slack 채널로 발송
4. 갱신된 스냅샷/히스토리를 이 브랜치에 커밋·푸시

> 시트의 평가금액은 사용자가 앱에서 직접 갱신합니다. 브리핑은 "브리핑 시점의 시트 값"을
> 읽어 전일 스냅샷과 비교하므로, 아침에 값을 갱신해두면 그 변동이 반영됩니다.

## 브리핑에 담기는 내용

- **총 평가금액** 및 전일 대비 증감액/증감률
- **총 수익률** (평가손익·매입금액)
- **성격별 현황**: 위험자산·지수·원자재·안전자산·현금성 비중 및 전일 대비
- **오늘의 상승/하락 TOP**: 개별 종목 평가금액 변동 상위 (첫 실행 시엔 평가금액 상위 종목)
- **계좌별 현황**: 증권사·계좌별 평가금액 및 전일 대비

## 파일 구조

| 경로 | 설명 |
|---|---|
| `brief.py` | 파싱·집계·비교·메시지 생성 스크립트 (표준 라이브러리만 사용) |
| `data/latest.json` | 가장 최근(전일) 스냅샷 — 다음 실행의 비교 기준 |
| `data/history.csv` | 일별 총자산·수익률·전일대비 누적 기록 |
| `data/snapshots/<날짜>.json` | 날짜별 스냅샷 보관 |
| `data/today_raw.md`, `data/brief.md` | 매 실행 시 생성되는 전이 파일(.gitignore) |

## 수동 실행 / 테스트

```bash
# 시트 원본 마크다운을 data/today_raw.md 에 저장한 뒤:
python3 brief.py --input data/today_raw.md            # 상태·히스토리까지 갱신
python3 brief.py --input data/today_raw.md --no-write  # 메시지만 생성(상태 미갱신)
python3 brief.py --input data/today_raw.md --date 2026-07-16  # 기준일 지정
```

## 설정 변경

- **발송 시각/채널/문구**: 예약 트리거(Routine)의 프롬프트에서 조정
- **성격 표시 순서/이모지**: `brief.py` 의 `NATURE_ORDER`, `NATURE_EMOJI`
- **TOP 개수**: `brief.py` 의 `build_message` 내 `[:5]` 값

## 관련 식별자

- 스프레드시트 ID: `1MLuYEdJUUuhQ6V3hp7WSmsIFeFRo9LIHL23_wcdezFw`
- Slack 채널 ID: `C0BHDG42Y2K` (`#자산-데일리브리핑`)
