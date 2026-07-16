#!/usr/bin/env python3
"""
자산 데일리 브리핑 생성기.

Google 시트(입력 시트)의 마크다운 표현을 파싱하여
전일 스냅샷과 비교한 뒤, Slack에 보낼 브리핑 메시지를 생성한다.

동작:
  1. --input 의 마크다운(시트 read 결과)을 파싱해 보유 종목 목록을 만든다.
  2. --state 의 전일 스냅샷(JSON)을 불러와 전일 대비 변동을 계산한다.
  3. --out 에 Slack 메시지(마크다운)를 쓴다.
  4. --state 를 오늘 값으로 갱신하고, --history CSV에 하루 한 줄을 남기며,
     data/snapshots/<날짜>.json 에 스냅샷을 보관한다.

순수 표준 라이브러리만 사용한다.
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]

# 성격(자산 성격) 표시 순서 및 이모지
NATURE_ORDER = ["위험자산", "지수", "원자재", "안전자산", "현금성"]
NATURE_EMOJI = {
    "위험자산": "🚀",
    "지수": "📈",
    "원자재": "🛢️",
    "안전자산": "🛡️",
    "현금성": "💵",
}


def won(n):
    """정수 원화 포맷: 1234567 -> '1,234,567원'"""
    try:
        return f"{int(round(n)):,}원"
    except (TypeError, ValueError):
        return "-"


def signed_won(n):
    """부호 포함 원화: +1,234원 / -1,234원 / 0원"""
    n = int(round(n))
    sign = "+" if n > 0 else ("-" if n < 0 else "")
    return f"{sign}{abs(n):,}원"


def signed_pct(p, digits=2):
    if p is None:
        return "-"
    sign = "+" if p > 0 else ("-" if p < 0 else "")
    return f"{sign}{abs(p):.{digits}f}%"


def arrow(n):
    if n > 0:
        return "🔺"
    if n < 0:
        return "🔻"
    return "▪️"


def _to_int(s):
    """'1,234,567' -> 1234567, 빈 값 -> None"""
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if s == "" or s == "-":
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _to_pct(s):
    """'47.4%' -> 47.4, '(14.3%)' -> -14.3, 빈 값 -> None"""
    if s is None:
        return None
    s = s.strip()
    if s == "" or s == "-":
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("%", "").replace(",", "").strip()
    if s == "":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_holdings(text):
    """마크다운 표에서 보유 종목 행을 추출한다.

    기대 컬럼(선행 빈칸 제외 1-기준):
      1 No. | 2 증권사 | 3 계좌 | 4 종목명 | 5 티커 | 6 수량
      7 매입금액 | 8 평가금액 | 9 수익률 | 10 비중 | 11 테마 | 12 성격
    """
    holdings = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        # 선행/후행 빈 요소 제거 후 최소 12개 컬럼 필요
        # split('|') 결과: ['', c1, c2, ..., c12, '']  -> 14개
        if len(parts) < 13:
            continue
        cols = parts[1:13]
        no_raw = cols[0]
        # No. 컬럼이 정수인 행만 실제 보유 종목으로 인정
        if not re.fullmatch(r"\d+", no_raw):
            continue
        broker = cols[1]
        account = cols[2]
        name = cols[3]
        ticker = cols[4]
        qty = cols[5]
        cost = _to_int(cols[6])
        evalv = _to_int(cols[7])
        ret = _to_pct(cols[8])
        nature = cols[11] if len(cols) > 11 else ""
        if not name:
            continue
        holdings.append({
            "no": int(no_raw),
            "broker": broker,
            "account": account,
            "name": name,
            "ticker": ticker,
            "qty": qty,
            "cost": cost or 0,
            "eval": evalv or 0,
            "ret": ret,
            "nature": nature or "기타",
            "key": f"{broker}|{account}|{name}",
        })
    return holdings


def aggregate(holdings):
    total_eval = sum(h["eval"] for h in holdings)
    total_cost = sum(h["cost"] for h in holdings)
    by_nature = {}
    for h in holdings:
        nat = h["nature"]
        b = by_nature.setdefault(nat, {"eval": 0, "cost": 0})
        b["eval"] += h["eval"]
        b["cost"] += h["cost"]
    return {
        "total_eval": total_eval,
        "total_cost": total_cost,
        "by_nature": by_nature,
        "holdings": {h["key"]: h["eval"] for h in holdings},
    }


def nature_sort_key(nat):
    return (NATURE_ORDER.index(nat) if nat in NATURE_ORDER else len(NATURE_ORDER), nat)


def build_message(holdings, agg, prev, date_kst):
    total_eval = agg["total_eval"]
    total_cost = agg["total_cost"]
    total_ret = ((total_eval - total_cost) / total_cost * 100) if total_cost else None

    d = date_kst
    header_date = f"{d.strftime('%Y-%m-%d')} ({WEEKDAYS_KO[d.weekday()]})"

    lines = []
    lines.append(f"# 📊 자산 데일리 브리핑 — {header_date}")
    lines.append("")

    # ── 총자산 요약 ─────────────────────────────
    lines.append(f"**💰 총 평가금액  {won(total_eval)}**")
    if prev and prev.get("total_eval") is not None:
        dod = total_eval - prev["total_eval"]
        dod_pct = (dod / prev["total_eval"] * 100) if prev["total_eval"] else None
        lines.append(
            f"> 직전({prev.get('date','-')}) 대비  **{signed_won(dod)}**  "
            f"({signed_pct(dod_pct)})  {arrow(dod)}  · 이전 {won(prev['total_eval'])}"
        )
    else:
        lines.append("> _첫 브리핑입니다 — 전일 비교 데이터는 내일부터 제공됩니다._")
    if total_ret is not None:
        pl = total_eval - total_cost
        lines.append(
            f"📈 총 수익률  **{signed_pct(total_ret, 1)}**  "
            f"(평가손익 {signed_won(pl)} · 매입 {won(total_cost)})"
        )
    lines.append("")

    # ── 성격별 비중 / 전일 대비 ─────────────────
    lines.append("### 성격별 현황")
    lines.append("")
    lines.append("| 성격 | 평가금액 | 비중 | 전일 대비 |")
    lines.append("|---|---:|---:|---:|")
    prev_nat = (prev or {}).get("by_nature", {}) or {}
    for nat in sorted(agg["by_nature"].keys(), key=nature_sort_key):
        b = agg["by_nature"][nat]
        weight = (b["eval"] / total_eval * 100) if total_eval else 0
        emoji = NATURE_EMOJI.get(nat, "•")
        if prev and nat in prev_nat:
            dod = b["eval"] - prev_nat[nat]["eval"]
            dod_cell = f"{signed_won(dod)} {arrow(dod)}"
        else:
            dod_cell = "-"
        lines.append(
            f"| {emoji} {nat} | {won(b['eval'])} | {weight:.1f}% | {dod_cell} |"
        )
    lines.append("")

    # ── 오늘의 상승/하락 TOP ────────────────────
    if prev and prev.get("holdings"):
        prev_h = prev["holdings"]
        moves = []
        for h in holdings:
            if h["key"] in prev_h:
                change = h["eval"] - prev_h[h["key"]]
                base = prev_h[h["key"]]
                pct = (change / base * 100) if base else None
                if change != 0:
                    moves.append((h, change, pct))
        gainers = sorted([m for m in moves if m[1] > 0], key=lambda x: -x[1])[:5]
        losers = sorted([m for m in moves if m[1] < 0], key=lambda x: x[1])[:5]

        if gainers:
            lines.append("### 🔺 오늘의 상승 TOP")
            lines.append("")
            for h, change, pct in gainers:
                lines.append(
                    f"- **{h['name']}**  {signed_won(change)}"
                    f"  ({signed_pct(pct, 1)})  · {h['account']}"
                )
            lines.append("")
        if losers:
            lines.append("### 🔻 오늘의 하락 TOP")
            lines.append("")
            for h, change, pct in losers:
                lines.append(
                    f"- **{h['name']}**  {signed_won(change)}"
                    f"  ({signed_pct(pct, 1)})  · {h['account']}"
                )
            lines.append("")
        if not gainers and not losers:
            lines.append("_직전 대비 개별 종목 평가금액 변동이 없습니다._")
            lines.append("")
    else:
        # 첫 실행: 평가금액 상위 종목 안내
        top = sorted(holdings, key=lambda h: -h["eval"])[:5]
        lines.append("### 💎 평가금액 상위 종목")
        lines.append("")
        for h in top:
            w = (h["eval"] / total_eval * 100) if total_eval else 0
            lines.append(
                f"- **{h['name']}**  {won(h['eval'])}  ({w:.1f}%)  · {h['account']}"
            )
        lines.append("")

    # ── 계좌별 현황 ─────────────────────────────
    lines.append("### 📋 계좌별 현황")
    lines.append("")
    lines.append("| 증권사 · 계좌 | 평가금액 | 전일 대비 |")
    lines.append("|---|---:|---:|")
    acct = {}
    for h in holdings:
        k = f"{h['broker']} · {h['account']}"
        acct.setdefault(k, 0)
        acct[k] += h["eval"]
    prev_acct = (prev or {}).get("by_account", {}) or {}
    for k in sorted(acct.keys(), key=lambda x: -acct[x]):
        if prev and k in prev_acct:
            dod = acct[k] - prev_acct[k]
            dod_cell = f"{signed_won(dod)} {arrow(dod)}"
        else:
            dod_cell = "-"
        lines.append(f"| {k} | {won(acct[k])} | {dod_cell} |")
    lines.append("")
    lines.append(f"_생성: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} · 데이터 출처: 금융자산현황표_")

    return "\n".join(lines), acct


def compute_summary(holdings, agg, prev, date_kst):
    """세 가지 렌더러(마크다운/Slack/HTML)가 공유하는 계산 결과."""
    total_eval = agg["total_eval"]
    total_cost = agg["total_cost"]
    total_ret = ((total_eval - total_cost) / total_cost * 100) if total_cost else None

    has_prev = bool(prev and prev.get("total_eval") is not None)
    dod = (total_eval - prev["total_eval"]) if has_prev else None
    dod_pct = (dod / prev["total_eval"] * 100) if has_prev and prev["total_eval"] else None

    prev_nat = (prev or {}).get("by_nature", {}) or {}
    natures = []
    for nat in sorted(agg["by_nature"].keys(), key=nature_sort_key):
        b = agg["by_nature"][nat]
        weight = (b["eval"] / total_eval * 100) if total_eval else 0
        nat_dod = (b["eval"] - prev_nat[nat]["eval"]) if (prev and nat in prev_nat) else None
        natures.append({"nat": nat, "eval": b["eval"], "weight": weight, "dod": nat_dod})

    gainers, losers, top_holdings = [], [], []
    if prev and prev.get("holdings"):
        prev_h = prev["holdings"]
        moves = []
        for h in holdings:
            if h["key"] in prev_h:
                change = h["eval"] - prev_h[h["key"]]
                base = prev_h[h["key"]]
                pct = (change / base * 100) if base else None
                if change != 0:
                    moves.append({"h": h, "change": change, "pct": pct})
        gainers = sorted([m for m in moves if m["change"] > 0],
                         key=lambda x: -x["change"])[:5]
        losers = sorted([m for m in moves if m["change"] < 0],
                        key=lambda x: x["change"])[:5]
    else:
        for h in sorted(holdings, key=lambda h: -h["eval"])[:5]:
            w = (h["eval"] / total_eval * 100) if total_eval else 0
            top_holdings.append({"h": h, "weight": w})

    acct = {}
    for h in holdings:
        k = f"{h['broker']} · {h['account']}"
        acct[k] = acct.get(k, 0) + h["eval"]
    prev_acct = (prev or {}).get("by_account", {}) or {}
    accounts = []
    for k in sorted(acct.keys(), key=lambda x: -acct[x]):
        a_dod = (acct[k] - prev_acct[k]) if (prev and k in prev_acct) else None
        accounts.append({"name": k, "eval": acct[k], "dod": a_dod})

    d = date_kst
    return {
        "date": d,
        "header_date": f"{d.strftime('%Y-%m-%d')} ({WEEKDAYS_KO[d.weekday()]})",
        "total_eval": total_eval,
        "total_cost": total_cost,
        "total_ret": total_ret,
        "pl": total_eval - total_cost,
        "has_prev": has_prev,
        "prev_eval": prev["total_eval"] if has_prev else None,
        "prev_date": prev.get("date") if has_prev else None,
        "dod": dod,
        "dod_pct": dod_pct,
        "natures": natures,
        "gainers": gainers,
        "losers": losers,
        "top_holdings": top_holdings,
        "accounts": accounts,
        "acct_map": acct,
    }


def build_slack_blocks(summary):
    """Slack Incoming Webhook용 Block Kit blocks 리스트."""
    s = summary
    blocks = []
    blocks.append({"type": "header", "text": {
        "type": "plain_text", "text": f"📊 자산 데일리 브리핑 — {s['header_date']}",
        "emoji": True}})

    if s["has_prev"]:
        dod_line = (f"직전({s['prev_date']}) 대비 *{signed_won(s['dod'])}*  "
                    f"({signed_pct(s['dod_pct'])}) {arrow(s['dod'])}  ·  이전 {won(s['prev_eval'])}")
    else:
        dod_line = "_첫 브리핑입니다 — 전일 비교는 내일부터 제공됩니다._"
    top_text = f"*💰 총 평가금액*\n*{won(s['total_eval'])}*\n{dod_line}"
    if s["total_ret"] is not None:
        top_text += (f"\n📈 총 수익률 *{signed_pct(s['total_ret'], 1)}*  "
                     f"(평가손익 {signed_won(s['pl'])} · 매입 {won(s['total_cost'])})")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": top_text}})
    blocks.append({"type": "divider"})

    # 성격별 (2열 필드)
    nat_fields = []
    for n in s["natures"]:
        emoji = NATURE_EMOJI.get(n["nat"], "•")
        dod = f"  ({signed_won(n['dod'])} {arrow(n['dod'])})" if n["dod"] is not None else ""
        nat_fields.append({"type": "mrkdwn",
                           "text": f"{emoji} *{n['nat']}*\n{won(n['eval'])} · {n['weight']:.1f}%{dod}"})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*성격별 현황*"},
                   "fields": nat_fields[:10]})
    blocks.append({"type": "divider"})

    # 상승/하락 TOP 또는 상위 종목
    if s["gainers"] or s["losers"]:
        if s["gainers"]:
            txt = "*🔺 오늘의 상승 TOP*\n" + "\n".join(
                f"• *{m['h']['name']}*  {signed_won(m['change'])} ({signed_pct(m['pct'],1)}) · {m['h']['account']}"
                for m in s["gainers"])
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": txt}})
        if s["losers"]:
            txt = "*🔻 오늘의 하락 TOP*\n" + "\n".join(
                f"• *{m['h']['name']}*  {signed_won(m['change'])} ({signed_pct(m['pct'],1)}) · {m['h']['account']}"
                for m in s["losers"])
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": txt}})
    elif s["top_holdings"]:
        txt = "*💎 평가금액 상위 종목*\n" + "\n".join(
            f"• *{t['h']['name']}*  {won(t['h']['eval'])} ({t['weight']:.1f}%) · {t['h']['account']}"
            for t in s["top_holdings"])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": txt}})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": "_직전 대비 개별 종목 변동이 없습니다._"}})
    blocks.append({"type": "divider"})

    # 계좌별 (2열 필드)
    acct_fields = []
    for a in s["accounts"]:
        dod = f"  ({signed_won(a['dod'])} {arrow(a['dod'])})" if a["dod"] is not None else ""
        acct_fields.append({"type": "mrkdwn", "text": f"*{a['name']}*\n{won(a['eval'])}{dod}"})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*📋 계좌별 현황*"},
                   "fields": acct_fields[:10]})

    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                   "text": f"자동 브리핑 · {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')} · 출처: 금융자산현황표"}]})
    return blocks


def persist_snapshot(agg, acct, prev, date_str, state_path, history_path, snapshots_dir):
    """오늘 스냅샷을 저장하고 히스토리 CSV를 누적한다(같은 날짜는 덮어씀)."""
    state = {
        "date": date_str,
        "total_eval": agg["total_eval"],
        "total_cost": agg["total_cost"],
        "by_nature": agg["by_nature"],
        "by_account": acct,
        "holdings": agg["holdings"],
    }
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    os.makedirs(snapshots_dir, exist_ok=True)
    with open(os.path.join(snapshots_dir, f"{date_str}.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    total_ret = ((agg["total_eval"] - agg["total_cost"]) / agg["total_cost"] * 100
                 ) if agg["total_cost"] else 0
    dod = (agg["total_eval"] - prev["total_eval"]) if prev and prev.get("total_eval") else 0
    dod_pct = (dod / prev["total_eval"] * 100) if prev and prev.get("total_eval") else 0
    rows = {}
    if os.path.exists(history_path):
        with open(history_path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = r
    rows[date_str] = {
        "date": date_str, "total_eval": agg["total_eval"], "total_cost": agg["total_cost"],
        "return_pct": f"{total_ret:.2f}", "dod_change": dod, "dod_pct": f"{dod_pct:.2f}",
    }
    os.makedirs(os.path.dirname(history_path) or ".", exist_ok=True)
    with open(history_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "total_eval", "total_cost",
                                          "return_pct", "dod_change", "dod_pct"])
        w.writeheader()
        for dt in sorted(rows.keys()):
            w.writerow(rows[dt])
    return state


def _h(text):
    """HTML 이스케이프(최소)."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_html(summary):
    """단독 열람용 HTML 브리핑."""
    s = summary
    pos = "#e03131"  # 상승(빨강, 한국 관습)
    neg = "#1971c2"  # 하락(파랑)

    def col(n):
        return pos if n > 0 else (neg if n < 0 else "#868e96")

    parts = []
    parts.append(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>자산 데일리 브리핑 — {_h(s['header_date'])}</title>
<style>
:root{{color-scheme:light dark}}
body{{font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
max-width:760px;margin:0 auto;padding:24px 18px;line-height:1.55;
background:#fff;color:#212529}}
@media(prefers-color-scheme:dark){{body{{background:#1a1b1e;color:#e9ecef}}
.card{{background:#25262b!important;border-color:#373a40!important}}
th{{background:#2c2e33!important}}}}
h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:#868e96;font-size:13px;margin-bottom:18px}}
.card{{border:1px solid #e9ecef;border-radius:12px;padding:16px 18px;margin:14px 0;background:#f8f9fa}}
.total{{font-size:30px;font-weight:800;letter-spacing:-.5px}}
.dod{{font-size:15px;font-weight:600;margin-top:2px}}
.ret{{font-size:14px;color:#495057;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:14px;margin-top:6px}}
th,td{{padding:8px 6px;border-bottom:1px solid #e9ecef;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
th{{background:#f1f3f5;font-weight:600;font-size:12px;color:#495057}}
h2{{font-size:15px;margin:22px 0 6px}}
.mono{{font-variant-numeric:tabular-nums}}
</style></head><body>
<h1>📊 자산 데일리 브리핑</h1>
<div class="sub">{_h(s['header_date'])} · 자동 생성</div>
<div class="card">
<div class="total mono">{_h(won(s['total_eval']))}</div>""")

    if s["has_prev"]:
        c = col(s["dod"])
        parts.append(f'<div class="dod mono" style="color:{c}">직전({_h(s["prev_date"])}) 대비 {_h(signed_won(s["dod"]))} '
                     f'({_h(signed_pct(s["dod_pct"]))}) {arrow(s["dod"])} '
                     f'<span style="color:#868e96;font-weight:400">· 이전 {_h(won(s["prev_eval"]))}</span></div>')
    else:
        parts.append('<div class="dod" style="color:#868e96">첫 브리핑입니다 — 전일 비교는 내일부터 제공됩니다.</div>')
    if s["total_ret"] is not None:
        parts.append(f'<div class="ret mono">📈 총 수익률 <b>{_h(signed_pct(s["total_ret"],1))}</b> '
                     f'(평가손익 {_h(signed_won(s["pl"]))} · 매입 {_h(won(s["total_cost"]))})</div>')
    parts.append("</div>")

    # 성격별
    parts.append('<h2>성격별 현황</h2><table><tr><th>성격</th><th>평가금액</th><th>비중</th><th>전일 대비</th></tr>')
    for n in s["natures"]:
        emoji = NATURE_EMOJI.get(n["nat"], "•")
        if n["dod"] is not None:
            dcell = f'<span class="mono" style="color:{col(n["dod"])}">{_h(signed_won(n["dod"]))} {arrow(n["dod"])}</span>'
        else:
            dcell = "-"
        parts.append(f'<tr><td>{emoji} {_h(n["nat"])}</td><td class="mono">{_h(won(n["eval"]))}</td>'
                     f'<td class="mono">{n["weight"]:.1f}%</td><td>{dcell}</td></tr>')
    parts.append("</table>")

    # 상승/하락 또는 상위
    if s["gainers"] or s["losers"]:
        for title, arr in (("🔺 오늘의 상승 TOP", s["gainers"]), ("🔻 오늘의 하락 TOP", s["losers"])):
            if not arr:
                continue
            parts.append(f'<h2>{title}</h2><table>')
            for m in arr:
                parts.append(f'<tr><td>{_h(m["h"]["name"])}<br><span style="color:#868e96;font-size:12px">{_h(m["h"]["account"])}</span></td>'
                             f'<td class="mono" style="color:{col(m["change"])}">{_h(signed_won(m["change"]))}<br>{_h(signed_pct(m["pct"],1))}</td></tr>')
            parts.append("</table>")
    elif s["top_holdings"]:
        parts.append('<h2>💎 평가금액 상위 종목</h2><table>')
        for t in s["top_holdings"]:
            parts.append(f'<tr><td>{_h(t["h"]["name"])}<br><span style="color:#868e96;font-size:12px">{_h(t["h"]["account"])}</span></td>'
                         f'<td class="mono">{_h(won(t["h"]["eval"]))}<br>{t["weight"]:.1f}%</td></tr>')
        parts.append("</table>")

    # 계좌별
    parts.append('<h2>📋 계좌별 현황</h2><table><tr><th>증권사 · 계좌</th><th>평가금액</th><th>전일 대비</th></tr>')
    for a in s["accounts"]:
        if a["dod"] is not None:
            dcell = f'<span class="mono" style="color:{col(a["dod"])}">{_h(signed_won(a["dod"]))} {arrow(a["dod"])}</span>'
        else:
            dcell = "-"
        parts.append(f'<tr><td>{_h(a["name"])}</td><td class="mono">{_h(won(a["eval"]))}</td><td>{dcell}</td></tr>')
    parts.append("</table>")
    parts.append(f'<div class="sub" style="margin-top:20px">생성: {datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")} · 데이터 출처: 금융자산현황표</div>')
    parts.append("</body></html>")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="시트 read 마크다운 파일")
    ap.add_argument("--state", default="data/latest.json", help="전일 스냅샷(JSON)")
    ap.add_argument("--history", default="data/history.csv", help="일별 히스토리 CSV")
    ap.add_argument("--out", default="data/brief.md", help="Slack 메시지 출력")
    ap.add_argument("--snapshots", default="data/snapshots", help="스냅샷 보관 폴더")
    ap.add_argument("--date", default=None, help="기준일 YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--no-write", action="store_true",
                    help="상태/히스토리 갱신 없이 메시지만 생성(테스트용)")
    args = ap.parse_args()

    if args.date:
        date_kst = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=KST)
    else:
        date_kst = datetime.now(KST)
    date_str = date_kst.strftime("%Y-%m-%d")

    with open(args.input, encoding="utf-8") as f:
        text = f.read()

    holdings = parse_holdings(text)
    if not holdings:
        print("ERROR: 보유 종목을 하나도 파싱하지 못했습니다. 입력 형식을 확인하세요.",
              file=sys.stderr)
        sys.exit(2)

    agg = aggregate(holdings)

    prev = None
    if os.path.exists(args.state):
        try:
            with open(args.state, encoding="utf-8") as f:
                prev = json.load(f)
        except (json.JSONDecodeError, OSError):
            prev = None

    message, acct = build_message(holdings, agg, prev, date_kst)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(message)
    print(f"[ok] 브리핑 메시지: {args.out}  ({len(holdings)}개 종목, "
          f"총 {agg['total_eval']:,}원)")

    if args.no_write:
        print("[skip] --no-write: 상태/히스토리 미갱신")
        return

    # 상태 스냅샷 갱신
    state = {
        "date": date_str,
        "total_eval": agg["total_eval"],
        "total_cost": agg["total_cost"],
        "by_nature": agg["by_nature"],
        "by_account": acct,
        "holdings": agg["holdings"],
    }
    os.makedirs(os.path.dirname(args.state) or ".", exist_ok=True)
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    os.makedirs(args.snapshots, exist_ok=True)
    with open(os.path.join(args.snapshots, f"{date_str}.json"), "w",
              encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # 히스토리 CSV 누적(같은 날짜는 갱신)
    total_ret = ((agg["total_eval"] - agg["total_cost"]) / agg["total_cost"] * 100
                 ) if agg["total_cost"] else 0
    dod = (agg["total_eval"] - prev["total_eval"]) if prev and prev.get("total_eval") else 0
    dod_pct = (dod / prev["total_eval"] * 100) if prev and prev.get("total_eval") else 0
    rows = {}
    if os.path.exists(args.history):
        with open(args.history, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = r
    rows[date_str] = {
        "date": date_str,
        "total_eval": agg["total_eval"],
        "total_cost": agg["total_cost"],
        "return_pct": f"{total_ret:.2f}",
        "dod_change": dod,
        "dod_pct": f"{dod_pct:.2f}",
    }
    os.makedirs(os.path.dirname(args.history) or ".", exist_ok=True)
    with open(args.history, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "date", "total_eval", "total_cost", "return_pct",
            "dod_change", "dod_pct"])
        w.writeheader()
        for dt in sorted(rows.keys()):
            w.writerow(rows[dt])

    print(f"[ok] 상태 갱신: {args.state}")
    print(f"[ok] 히스토리: {args.history}")


if __name__ == "__main__":
    main()
