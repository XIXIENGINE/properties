# 운영 런북 / 트러블슈팅

매일 08:00 KST에 `.github/workflows/daily-brief.yml` 이 실행됩니다.

## 정상 흐름

1. 시트 xlsx 다운로드 → 보유 종목 파싱
2. 전일 스냅샷(`data/latest.json`)과 비교
3. Slack 발송(웹훅) + `data/brief.html` 생성
4. 스냅샷/히스토리 커밋·푸시

## 자주 겪는 문제

| 증상 | 원인 / 조치 |
|---|---|
| Slack에 안 옴, 로그에 "SLACK_WEBHOOK_URL 없음" | 시크릿 미등록. README의 활성화 절차대로 `SLACK_WEBHOOK_URL` 등록 |
| "보유 종목을 파싱하지 못했습니다" | 시트 탭/헤더 구조 변경. `generate.py` 의 `HEADER_MAP` 또는 헤더 탐색 로직 확인 |
| Slack HTTP 404/410 | 웹훅 URL 만료/삭제. 웹훅 재생성 후 시크릿 갱신 |
| 시트 다운로드 403 | 시트 공유가 '링크 공개(anyone reader)'인지 확인 |
| 전일 대비가 항상 "첫 브리핑" | `data/latest.json` 이 커밋되지 않음. 워크플로 push 권한(`contents: write`) 및 커밋 스텝 확인 |
| 총액이 이상 | 시트에 새 계좌/종목 추가 시 자동 반영됨. 값 오류면 시트 원본 확인 |

## 발송 시각 변경

`daily-brief.yml` 의 cron 을 UTC로 지정. 예: 09:00 KST = `0 0 * * *`, 07:00 KST = `0 22 * * *`.
GitHub Actions 스케줄은 부하에 따라 수 분 지연될 수 있습니다.

## 수동 실행

GitHub → Actions → "자산 데일리 브리핑" → Run workflow
(`dry_run` 체크 시 파싱만 검증하고 저장·발송하지 않음)

## 데이터 재설정

- 전일 비교를 초기화하려면 `data/latest.json` 삭제 후 커밋 → 다음 실행이 '첫 브리핑'이 됨
- `data/history.csv` 는 같은 날짜를 덮어쓰므로 재실행해도 안전
