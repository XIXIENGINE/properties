#!/usr/bin/env python3
"""
냥돌폴리오 앱(https://…vercel.app)의 CSV 내보내기를 브리핑 데이터 소스로 쓴다.

왜 앱에서 받나:
  앱이 이미 거래를 접어 보유수량·평단을 내고, 실시간 시세·환율까지 반영한다.
  브리핑이 시트를 따로 파싱하고 시세를 또 조회하면 **같은 자산이 두 곳에서
  다른 금액으로 계산된다.** 계산 경로는 앱 하나로 둔다.

인증:
  앱은 공용 비밀번호 게이트 뒤에 있다. 쿠키 값은 APP_PASSWORD 의 해시라
  (앱 src/lib/auth.ts 와 같은 식) 비밀번호만 있으면 만들 수 있다.

순수 표준 라이브러리만 쓴다.
"""

import csv
import hashlib
import io
import os
import urllib.error
import urllib.request

AUTH_COOKIE = "ndp_auth"
TIMEOUT = 30


class AppUnavailable(Exception):
    """앱에서 데이터를 받지 못했다. 사용자에게 그대로 보여줄 문장을 담는다."""

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.message = message
        self.hint = hint


def auth_token(password):
    """앱 src/lib/auth.ts 의 authToken 과 같은 값이어야 한다."""
    return hashlib.sha256(f"nyangdolfolio:{password}".encode()).hexdigest()


def _fetch_csv(base_url, kind, password):
    url = f"{base_url.rstrip('/')}/api/export/{kind}"
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": f"{AUTH_COOKIE}={auth_token(password)}",
            "User-Agent": "asset-brief/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            # 미들웨어가 로그인으로 돌려보내면 200 HTML 이 온다. CSV 가 아니면 인증 실패다.
            ctype = r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8-sig")
            if "text/csv" not in ctype:
                raise AppUnavailable(
                    f"앱 인증에 실패했습니다 ({kind}).",
                    "APP_PASSWORD 시크릿이 앱의 비밀번호와 같은지 확인해 주세요.",
                )
            return body
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace").strip()[:200]
        except OSError:
            pass
        if e.code == 503:
            # 앱이 Supabase 에 닿지 못하는 상태 — 일시정지가 가장 흔한 원인이다
            raise AppUnavailable(
                "앱이 데이터베이스에 연결하지 못했습니다.",
                "Supabase 프로젝트가 일시정지되었을 수 있습니다. "
                "대시보드에서 Restore 를 눌러 주세요.",
            ) from e
        raise AppUnavailable(
            f"앱이 오류를 반환했습니다 (HTTP {e.code}). {detail}".strip(),
            "Vercel 배포 상태와 Supabase 프로젝트 상태를 확인해 주세요.",
        ) from e
    except urllib.error.URLError as e:
        raise AppUnavailable(
            f"앱에 접속하지 못했습니다: {e.reason}",
            "앱 주소(NYANGDOL_APP_URL)와 배포 상태를 확인해 주세요.",
        ) from e


def _rows(csv_text):
    # 앱은 엑셀 때문에 BOM 을 붙여 보낸다. utf-8-sig 로 읽으면 벗겨지지만,
    # 남아 있으면 첫 헤더가 '﻿보유자' 가 되어 열을 통째로 못 찾는다.
    return list(csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))))


def _num(s):
    s = (s or "").strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(s):
    v = _num(s)
    return 0 if v is None else int(round(v))


def _split_account(text):
    """'미래에셋 연금저축펀드' -> ('미래에셋', '연금저축펀드')"""
    parts = (text or "").strip().split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", parts[0] if parts else ""


def load_holdings(base_url, password):
    """앱의 보유현황 CSV를 brief.py 가 쓰는 보유 종목 형태로 바꾼다.

    현금잔액 행도 그대로 들어온다(분류=현금성). 빼면 총액이 앱과 어긋난다.
    """
    rows = _rows(_fetch_csv(base_url, "holdings", password))
    if not rows:
        raise AppUnavailable(
            "앱에서 받은 보유현황이 비어 있습니다.",
            "앱 배분 탭에 보유 종목이 보이는지 확인해 주세요.",
        )

    required = {"보유자", "계좌", "종목", "분류", "평가액(원)"}
    missing = required - set(rows[0].keys())
    if missing:
        raise AppUnavailable(
            f"앱 CSV 형식이 예상과 다릅니다 (없는 열: {', '.join(sorted(missing))}).",
            "앱의 내보내기 컬럼이 바뀌었다면 app_source.py 도 함께 고쳐야 합니다.",
        )

    holdings = []
    for r in rows:
        owner = (r.get("보유자") or "").strip()
        broker, account = _split_account(r.get("계좌"))
        name = (r.get("종목") or "").strip()
        if not name:
            continue

        evaluation = _int(r.get("평가액(원)"))
        # 매입액이 없는 옛 배포 대비 — 수익률로 역산하고, 그것도 없으면 평가액을 쓴다
        cost = _int(r.get("매입액(원)"))
        if cost == 0:
            ret = _num(r.get("수익률(%)"))
            cost = int(round(evaluation / (1 + ret / 100))) if ret not in (None, -100) else evaluation

        holdings.append({
            "no": len(holdings) + 1,
            "owner": owner,
            "broker": broker,
            "account": account,
            "name": name,
            "ticker": (r.get("티커") or "").strip(),
            "qty": (r.get("수량") or "").strip(),
            "cost": cost,
            "eval": evaluation,
            "ret": _num(r.get("수익률(%)")),
            "nature": (r.get("분류") or "").strip() or "기타",
            "key": f"{owner}|{broker}|{account}|{name}",
        })

    return holdings


def load_allocation(base_url, password):
    """배분 CSV — 브리핑 성격별 집계를 앱과 대조하는 데 쓴다."""
    out = {}
    for r in _rows(_fetch_csv(base_url, "allocation", password)):
        category = (r.get("분류") or "").strip()
        if category:
            out[category] = _int(r.get("평가액(원)"))
    return out


def config_from_env():
    """(base_url, password). 둘 중 하나라도 없으면 앱 소스를 쓸 수 없다."""
    url = os.environ.get("NYANGDOL_APP_URL", "").strip()
    password = os.environ.get("NYANGDOL_APP_PASSWORD", "").strip()
    if not url or not password:
        missing = [
            n for n, v in (("NYANGDOL_APP_URL", url), ("NYANGDOL_APP_PASSWORD", password)) if not v
        ]
        raise AppUnavailable(
            f"앱 접속 설정이 없습니다 (미설정: {', '.join(missing)}).",
            "저장소 Settings → Secrets and variables → Actions 에 등록해 주세요.",
        )
    return url, password
