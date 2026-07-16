#!/usr/bin/env python3
"""
실시간 시세 조회 및 평가금액 자동 계산.

- KRX ETF/주식: 네이버 금융(비공식 JSON 엔드포인트, 폴백 다중화)
- 미국 주식/ETF: Stooq CSV
- 환율(USD/KRW): exchangerate.host → Stooq 폴백

정책:
  · 티커에 숫자가 있으면 KRX 코드(선행 'A' 제거), 순수 알파벳이면 미국.
  · 티커가 없으면(예수금·CMA) 시트 평가금액 유지.
  · 시트 평가금액이 0이거나 수량이 0이면(매도/이관 등) 0 유지 → 유령 종목 부활 방지.
  · 시세 조회 실패 시 해당 종목만 시트 값으로 폴백(브리핑은 계속 진행).

순수 표준 라이브러리만 사용.
"""

import csv
import io
import json
import re
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
TIMEOUT = 8


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def classify(ticker):
    """('kr', code) | ('us', symbol) | (None, None)"""
    t = (ticker or "").strip()
    if not t:
        return (None, None)
    if any(ch.isdigit() for ch in t):
        code = t[1:] if (t[:1] in ("A", "a") and t[1:2].isdigit()) else t
        return ("kr", code.upper())
    if re.fullmatch(r"[A-Za-z.\-]+", t):
        return ("us", t.upper())
    return (None, None)


# ── 개별 시세 ─────────────────────────────────────────────

def _find_num(obj, keys):
    """중첩 dict/list에서 keys 중 하나에 해당하는 첫 숫자를 찾는다."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in keys:
                    n = _to_float(v)
                    if n is not None and n > 0:
                        return n
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def fetch_krx_price(code):
    """KRX 현재가(원). 여러 네이버 엔드포인트를 순차 시도."""
    # 1) itemSummary: 평평한 JSON의 'now'
    try:
        data = json.loads(_get(
            f"https://api.finance.naver.com/service/itemSummary.naver?itemcode={code}"))
        n = _to_float(data.get("now"))
        if n and n > 0:
            return n
    except Exception:
        pass
    # 2) 실시간 폴링: result.areas[].datas[].nv (또는 중첩 어딘가의 nv)
    try:
        data = json.loads(_get(
            f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"))
        n = _find_num(data, {"nv", "now", "closePrice"})
        if n and n > 0:
            return n
    except Exception:
        pass
    return None


def fetch_us_price(symbol):
    """미국 종가/현재가(USD). Stooq → Yahoo 폴백."""
    try:  # 1) Stooq CSV
        txt = _get(f"https://stooq.com/q/l/?s={symbol.lower()}.us&f=sd2t2ohlcv&h&e=csv")
        row = list(csv.DictReader(io.StringIO(txt)))
        if row:
            c = _to_float(row[0].get("Close"))
            if c and c > 0:
                return c
    except Exception:
        pass
    try:  # 2) Yahoo chart API
        data = json.loads(_get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"))
        meta = (((data.get("chart") or {}).get("result") or [{}])[0] or {}).get("meta") or {}
        c = _to_float(meta.get("regularMarketPrice"))
        if c and c > 0:
            return c
    except Exception:
        pass
    return None


def fetch_usdkrw():
    """USD/KRW 환율. 키 불필요 소스 다중 폴백."""
    # 1) open.er-api.com (무키)
    try:
        data = json.loads(_get("https://open.er-api.com/v6/latest/USD"))
        n = _to_float((data.get("rates") or {}).get("KRW"))
        if n and n > 0:
            return n
    except Exception:
        pass
    # 2) frankfurter.app (무키)
    try:
        data = json.loads(_get("https://api.frankfurter.app/latest?from=USD&to=KRW"))
        n = _to_float((data.get("rates") or {}).get("KRW"))
        if n and n > 0:
            return n
    except Exception:
        pass
    # 3) Yahoo (KRW=X)
    try:
        data = json.loads(_get(
            "https://query1.finance.yahoo.com/v8/finance/chart/KRW=X?interval=1d&range=1d"))
        meta = (((data.get("chart") or {}).get("result") or [{}])[0] or {}).get("meta") or {}
        n = _to_float(meta.get("regularMarketPrice"))
        if n and n > 0:
            return n
    except Exception:
        pass
    # 4) Stooq
    try:
        txt = _get("https://stooq.com/q/l/?s=usdkrw&f=sd2t2ohlcv&h&e=csv")
        row = list(csv.DictReader(io.StringIO(txt)))
        if row:
            n = _to_float(row[0].get("Close"))
            if n and n > 0:
                return n
    except Exception:
        pass
    return None


# ── 보유 종목 평가금액 갱신 ────────────────────────────────

def enrich_holdings(holdings):
    """holdings의 eval을 실시간 시세로 갱신(정책에 따라). (holdings, report) 반환.

    report = {fx, live, fallback, held_skipped, failures:[names], us_priced}
    """
    need_us = any(classify(h["ticker"])[0] == "us"
                  and (h.get("eval") or 0) > 0 and _to_float(h.get("qty"))
                  for h in holdings)
    fx = fetch_usdkrw() if need_us else None

    report = {"fx": fx, "live": 0, "fallback": 0, "held_skipped": 0,
              "failures": [], "us_priced": 0, "debug": [],
              "sheet_total": sum(h.get("eval") or 0 for h in holdings)}

    for h in holdings:
        market, code = classify(h["ticker"])
        qty = _to_float(h.get("qty")) or 0
        sheet_eval = h.get("eval") or 0

        if market is None:
            continue  # 현금성 등: 시트값 유지
        if sheet_eval <= 0 or qty <= 0:
            report["held_skipped"] += 1  # 매도/미보유: 0 유지
            continue

        if market == "kr":
            price = fetch_krx_price(code)
            if price:
                new_eval = int(round(qty * price))
                report["debug"].append((h["name"], qty, price, sheet_eval, new_eval))
                h["eval"] = new_eval
                report["live"] += 1
            else:
                report["fallback"] += 1
                report["failures"].append(h["name"])
        else:  # us
            price = fetch_us_price(code)
            if price and fx:
                new_eval = int(round(qty * price * fx))
                report["debug"].append((h["name"], qty, price, sheet_eval, new_eval))
                h["eval"] = new_eval
                report["live"] += 1
                report["us_priced"] += 1
            else:
                report["fallback"] += 1
                report["failures"].append(h["name"])

    report["live_total"] = sum(h.get("eval") or 0 for h in holdings)
    return holdings, report


def report_note(report):
    """브리핑 하단에 붙일 한 줄 요약."""
    if report is None:
        return None
    parts = ["실시간 시세 반영"]
    if report.get("fx"):
        parts.append(f"USD/KRW {report['fx']:,.1f}")
    parts.append(f"시세 {report['live']}건")
    if report["fallback"]:
        fails = ", ".join(report["failures"][:4])
        parts.append(f"조회실패 {report['fallback']}건→시트값({fails})")
    return " · ".join(parts)
