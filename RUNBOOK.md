# 운영 런북 — 매일 오전 8시 브리핑 세션

예약 트리거(Routine)는 매일 KST 08:00에 새 세션을 띄우고, 아래 프롬프트를 실행합니다.
이 문서는 그 프롬프트를 버전 관리하기 위한 사본입니다. 프롬프트를 바꾸려면 Routine을
업데이트하고 이 파일도 함께 수정하세요.

## 트리거 설정

- 스케줄: `0 8 * * *` (KST)
- 실행 모드: 매 발화 시 새 세션 생성 (create_new_session_on_fire)
- 대상 저장소/브랜치: `xixiengine/properties` · `claude/daily-asset-slack-brief-lkmg43`

## 프롬프트 (Routine이 매일 실행)

```
매일 자산 브리핑을 생성해 Slack으로 보낸다. 아래를 순서대로 정확히 수행하라.

1. 저장소 준비:
   git fetch origin claude/daily-asset-slack-brief-lkmg43
   git checkout claude/daily-asset-slack-brief-lkmg43
   git pull --ff-only origin claude/daily-asset-slack-brief-lkmg43

2. 시트 읽기: Google Drive 도구 read_file_content 로
   fileId=1MLuYEdJUUuhQ6V3hp7WSmsIFeFRo9LIHL23_wcdezFw 를 읽는다.
   반환된 마크다운 표 전체를 그대로 data/today_raw.md 에 저장한다.

3. 브리핑 생성: python3 brief.py --input data/today_raw.md
   (오류가 나면 파싱 실패이므로 중단하고, Slack 채널 C0BHDG42Y2K 에
    "⚠️ 자산 브리핑 생성 실패: <오류요약>" 만 보낸다.)

4. 발송: data/brief.md 내용을 읽어 Slack 채널 C0BHDG42Y2K 로 그대로 보낸다.

5. 커밋: git add data/latest.json data/history.csv data/snapshots
   git commit -m "chore: 자산 브리핑 스냅샷 <오늘 날짜>"
   git push origin claude/daily-asset-slack-brief-lkmg43

브리핑 메시지 본문은 절대 창작하지 말고 brief.py 가 생성한 data/brief.md 를 그대로 쓴다.
```

## 장애 대응

- **시트 접근 실패 / 연결 없음**: 새 세션에 Google Drive·Slack 연동이 없으면 실패한다.
  이 경우 GitHub Actions + 서비스계정(시트 공유) + Slack Webhook 방식으로 전환한다.
- **파싱 실패**: 시트 컬럼 구조가 바뀌면 `brief.py` 의 `parse_holdings` 컬럼 인덱스를 조정.
- **중복/누락**: `data/history.csv` 는 같은 날짜를 덮어쓰므로 재실행해도 안전하다.
