#!/usr/bin/env python3
"""
send_newsletter.py — MRM Weekly Newsletter
==========================================
Lê o data.json e o portfolio.json, pede o HTML ao modelo, grava, actualiza o
arquivo no index.html, faz commit e envia por Brevo.

Este ficheiro NÃO decide nada sobre a carteira. O job 1 do pipeline de sexta
(update_portfolio.py) corre primeiro, decide e escreve o portfolio.json; este
job relata o que aconteceu. Até Set 2026 havia aqui uma segunda cópia das regras
de rebalanceamento — escrita em função do score — e uma terceira cópia do mapa de
ETF sem a divisão FTQ/Stress. No dia em que o medidor B disparasse, a carteira
rodava para Critical e a newsletter dizia aos subscritores "No structural regime
change detected". As regras vivem agora todas em mrm_rules.py.

Estrutura: a derivação do contexto e a construção do prompt são funções puras,
para poderem ser testadas sem rede (tests/test_newsletter.py). Só o main() toca
em ficheiros, git, API do modelo e Brevo.
"""

import json, os, re, subprocess, requests, shutil
from datetime import datetime, date

import mrm_rules as rules

START_DATE = date(2026, 3, 13)
BAND_COLOR = {"Resilient": "#34D058", "Turbulence": "#F98C4F",
              "Critical": "#D73A49", "nd": "#8B949E"}

REBALANCE_STYLE = {          # (fundo, borda, ícone) por classe de motivo
    "stress_on":                 ("#2d1515", "#D73A49", "\u26a0"),
    "critical_subregime_switch": ("#2d1515", "#D73A49", "\u26a0"),
    "stress_off":                ("#0a1f18", "#34D058", "\u27f2"),
    "semestral":                 ("#1a3a5c", "#388BFD", "\u27f3"),
    "emergency":                 ("#122008", "#34D058", "\u26a0"),
    "no_allocation":             ("#2d1f0a", "#F98C4F", "\u26a0"),
}
REBALANCE_STYLE_DEFAULT = ("#122008", "#34D058", "\u25cf")


def issue_number_for(today):
    return ((today - START_DATE).days // 7) + 1


def _fmt_trigger(t, unit=""):
    v, thr, asof = t.get("value"), t.get("threshold"), t.get("asOf", "n/d")
    v_s = "n/d" if v is None else f"{v:+.2f}{unit}"
    return f"{v_s} (fires at >= {thr}{unit}, as of {asof})"


def build_context(data, portfolio_data, prev_data, today, issue_number):
    """Tudo o que o prompt, o cartão do arquivo e o tweet precisam. Função pura:
    não lê ficheiros nem rede."""
    global_score = data.get("globalResilienceScore")
    pillars   = {p["id"]: p for p in data.get("pillars", [])}
    sentinels = {s["id"]: s for s in data.get("sentinels", [])}
    prev_pillars = {p["id"]: p for p in (prev_data or {}).get("pillars", [])}
    prev_score   = (prev_data or {}).get("globalResilienceScore")

    def wow(pid, cur):
        prev = prev_pillars.get(pid, {}).get("score")
        if prev is None or cur is None:
            return "\u2014"
        d = round(float(cur) - float(prev), 1)
        return f"\u25b2 +{d}" if d > 0 else f"\u25bc {d}" if d < 0 else "\u2014 0.0"

    wow_score = ""
    if prev_score is not None and isinstance(global_score, (int, float)):
        d = round(float(global_score) - float(prev_score), 1)
        wow_score = f"\u25b2 +{d} WoW" if d > 0 else f"\u25bc {d} WoW" if d < 0 else "\u2014 0.0 WoW"

    band = rules.score_band(global_score if isinstance(global_score, (int, float)) else None)

    # ── Medidor B ────────────────────────────────────────────────────────────
    stress   = data.get("stressGauge") or {}
    active   = stress.get("active")
    sub      = stress.get("subregime")
    triggers = stress.get("triggers", {})
    if active is None:
        gauge_b_line = "n/d \u2014 both triggers unavailable this run; previous state retained"
    else:
        gauge_b_line = ("ON" if active else "OFF") + f" ({stress.get('basis', 'n/d')})"
        if active and sub:
            gauge_b_line += f" | sub-regime: {sub}"

    # ── Carteira: factos já executados ───────────────────────────────────────
    cur  = portfolio_data.get("current", {})
    hist = (portfolio_data.get("history") or [{}])[-1]
    port_regime    = cur.get("regime", "Turbulence")
    port_subregime = cur.get("critical_subregime")
    reason         = hist.get("rebalance_reason", "hold")
    etf_map = cur.get("active_etf_map") or rules.REGIME_ETF_MAP[
        rules.resolve_etf_map_key(port_regime, port_subregime)]
    alloc = cur.get("bucket_allocation_pct", {})

    regime_label = rules.REGIME_LABELS.get(port_regime, port_regime)
    if port_regime == "Critical" and port_subregime:
        regime_label += f" \u00b7 {rules.SUBREGIME_LABELS.get(port_subregime, port_subregime)}"

    style = REBALANCE_STYLE_DEFAULT
    for key, val in REBALANCE_STYLE.items():
        if reason.startswith(key):
            style = val
            break

    # As sentinelas eram lidas por id fixo ("icsa"), mas o motor escreve "jobless",
    # "erp" e "unemployment": durante 26 edicoes a newsletter reportou
    # "ICSA: N/A Alert:False" e a taxa de desemprego nunca chegou ao modelo.
    # Passa a ser construida a partir do que la estiver.
    sentinel_line = " | ".join(
        f"{s.get('name', sid)}: {s.get('displayValue', s.get('value', 'n/d'))}"
        f" (threshold {s.get('thresholdDisplay', s.get('threshold', 'n/d'))}, "
        f"alert {bool(s.get('alert'))})"
        for sid, s in sentinels.items()
    ) or "n/d"

    nd = data.get("ndPillars") or []
    pillars_live = f"{5 - len(nd)}/5 Pillars Active"
    if nd:
        pillars_live += f" ({', '.join(nd)} n/d)"

    nxt = rules.next_semestral_date(today)

    return {
        "today": today.strftime("%d %B %Y"),
        "today_file": today.strftime("%d%b%Y"),
        "issue_number": issue_number,
        "score": round(global_score, 1) if isinstance(global_score, (int, float)) else "N/A",
        "wow_score": wow_score,
        "score_band": band,
        "score_color": BAND_COLOR[band],
        "pillars_live": pillars_live,
        "pillars": pillars,
        "sentinels": sentinels,
        "sentinel_line": sentinel_line,
        "wow": wow,
        "stress_active": active,
        "gauge_b_line": gauge_b_line,
        "sahm_line": _fmt_trigger(triggers.get("sahmRealtime", {})),
        "npl_line": _fmt_trigger(triggers.get("delinquencyAccel", {}), " pp"),
        "regime_label": regime_label,
        "port_regime": port_regime,
        "port_subregime": port_subregime,
        "rb_reason": reason,
        "rb_alert": reason.upper(),
        "rb_status": rules.rebalance_copy(reason),
        "rb_done": bool(hist.get("rebalance_triggered", False)),
        "rb_color": style[0], "rb_border": style[1], "rb_icon": style[2],
        "port_etfs": " | ".join(etf_map.get(b, "?") for b in rules.BUCKETS),
        "alloc_line": (" | ".join(f"{b}: {alloc.get(b, 0):.0f}%" for b in rules.BUCKETS)
                       if alloc else "n/a"),
        "port_value": cur.get("portfolio_value", "N/A"),
        "port_pnl": cur.get("portfolio_pnl_pct", "N/A"),
        "port_alpha": cur.get("alpha_vs_benchmark_pct", "N/A"),
        "next_sem": nxt.strftime("%d %B %Y") if nxt else "N/A",
    }


def build_prompt(c):
    """O prompt do modelo. Os nomes locais existem para que o texto abaixo se
    mantenha legível como texto."""
    today          = c["today"];         issue_number = c["issue_number"]
    score          = c["score"];         wow_score    = c["wow_score"]
    score_band     = c["score_band"];    score_color  = c["score_color"]
    _pillars_live  = c["pillars_live"];  wow          = c["wow"]
    pil            = c["pillars"]
    cycle, liquidity, premium = pil.get("cycle", {}), pil.get("liquidity", {}), pil.get("premium", {})
    solvency, debt            = pil.get("solvency", {}), pil.get("debt", {})
    sentinel_line             = c["sentinel_line"]
    _gauge_b_line  = c["gauge_b_line"];  _sahm_line   = c["sahm_line"]
    _npl_line      = c["npl_line"];      _regime_label = c["regime_label"]
    _rb_alert      = c["rb_alert"];      _rb_status   = c["rb_status"]
    _rb_done       = c["rb_done"];       _port_etfs   = c["port_etfs"]
    _alloc_line    = c["alloc_line"];    _next_sem    = c["next_sem"]
    _port_value    = c["port_value"];    _port_pnl    = c["port_pnl"]
    _port_alpha    = c["port_alpha"]
    _rb_color      = c["rb_color"];      _rb_border   = c["rb_border"]
    _rb_icon       = c["rb_icon"]

    return f"""You are a Senior Risk Strategist and CIO. Generate a complete MRM Weekly Institutional Newsletter in HTML.

TODAY: {today} | ISSUE: #{issue_number}

THE SYSTEM HAS TWO GAUGES. They answer different questions and must never be conflated.

GAUGE A — GLOBAL RESILIENCE SCORE (leading fragility, 6-18 month horizon)
- Score: {score}/10 ({wow_score}) | Band: {score_band} | {_pillars_live}
- Cycle: {cycle.get('value','N/A')} | Score: {cycle.get('score','N/A')}/10 | WoW: {wow('cycle', cycle.get('score',0))} | {cycle.get('status','N/A')}
- Liquidity: {liquidity.get('value','N/A')} | Score: {liquidity.get('score','N/A')}/10 | WoW: {wow('liquidity', liquidity.get('score',0))} | {liquidity.get('status','N/A')}
- Premium (ERP): {premium.get('value','N/A')} | Score: {premium.get('score','N/A')}/10 | WoW: {wow('premium', premium.get('score',0))} | {premium.get('status','N/A')}
- Solvency: {solvency.get('value','N/A')} | Score: {solvency.get('score','N/A')}/10 | WoW: {wow('solvency', solvency.get('score',0))} | {solvency.get('status','N/A')}
- Debt: {debt.get('value','N/A')} | Score: {debt.get('score','N/A')}/10 | WoW: {wow('debt', debt.get('score',0))} | {debt.get('status','N/A')}
- Sentinels — {sentinel_line}

What Gauge A is: a measure of how much there is to go wrong over the next 6-18
months. What it is not: a measure of whether anything is going wrong now. In a
downturn three of its five pillars mechanically improve — the curve steepens,
valuations compress, the risk premium widens — so the composite falls into a
crisis rather than rising. Backtested on 2005-2026 it never reached 8.0, not
even in 2008.

GAUGE B — CONCURRENT STRESS (0-3 month horizon). THIS DECIDES THE REGIME.
- State: {_gauge_b_line}
- Sahm rule, real-time vintage: {_sahm_line}
- Bank delinquency, 4-quarter change: {_npl_line}

Two published triggers, neither calibrated on this sample. Backtested on
1996-2026 real-time vintages it fires in 2001, 2008, 2020 and 2024, and stays
silent through the 2011, 2018 and 2022 bear markets. The 2024 firing is a known
false positive of the Sahm rule.

PORTFOLIO — WHAT ALREADY HAPPENED THIS WEEK (facts, not forecasts)
- Operative regime: {_regime_label}
- Rebalance outcome: {_rb_alert} — {_rb_status}
- Executed: {"yes" if _rb_done else "no transactions"}
- Active instruments: {_port_etfs}
- Effective allocation now: {_alloc_line}
- Next scheduled semi-annual rebalance: {_next_sem}
- Portfolio Value: ${_port_value} | P&L: {_port_pnl}% | Alpha vs SPY: {_port_alpha}%

RULES:
1. Institutional tone. Dry. Objective. Risk-focused.
2. Do not name individual tickers anywhere in the analysis, the sector matrix or
   the allocation table — write sectors, factors and asset classes. The single
   exception is the PORTFOLIO REBALANCE STATUS box, where the active instruments
   above are a fact of the portfolio and are listed as such.
3. English only.
4. Include the WoW column in all tables.
5. Deep Dive on the biggest WoW mover.
6. Return ONLY complete HTML. No markdown. No backticks.
7. NEVER explain the current market state, or the portfolio regime, with the
   Gauge A score. The regime above was decided by Gauge B. If the two disagree —
   a high score with Gauge B off, or a falling score with Gauge B on — say so
   plainly: it is the system working as designed, not a contradiction.
8. The allocation table is the MACRO allocation for the coming period. It is
   parsed by the portfolio engine and executed at the next scheduled rebalance,
   so it must be internally consistent and defensible:
   - exactly ONE table in the whole document may have "Asset Class" as its first
     header cell, and that is the allocation table;
   - its rows must cover the six buckets and the percentages must total 100;
   - stay inside these bands: US equities 5-60, US treasuries 10-50,
     investment-grade credit 0-35, commodities 0-25, cash 0-40, alternatives
     0-20. An allocation outside them is rejected by the engine and the
     portfolio holds instead.
   - When the operative regime is Critical, the ACTIVE allocation is the fixed
     Critical vector shown above, which overrides this table for as long as
     stress persists; your table is then the macro allocation that resumes when
     Gauge B stands down. Say that explicitly in the allocation section.
9. After the CIO Verdict section, include a PORTFOLIO REBALANCE STATUS section
   using the facts above. Style it as a distinct box with background {_rb_color},
   border-left 4px solid {_rb_border}, icon {_rb_icon}. Show: operative regime,
   rebalance outcome and its explanation, active instruments, effective
   allocation, next semi-annual date, portfolio value / P&L / alpha.
10. Include a short GAUGE B section, before the CIO Verdict, reporting the two
   trigger values against their thresholds and what the state means for the
   portfolio. Two or three sentences, no speculation about when it might fire.

HTML STRUCTURE:
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>MRM Weekly Audit — Issue #{issue_number} — {today}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#F2F4F7;font-family:Arial,sans-serif;font-size:14px;color:#1A1D20;}}
a{{color:#388BFD;text-decoration:none;}}
.wrapper{{max-width:640px;margin:32px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);}}
.header{{background:#0D1117;}}
.header-top{{display:flex;align-items:center;justify-content:space-between;padding:14px 28px;border-bottom:1px solid #21262D;}}
.logo{{font-size:15px;font-weight:800;color:#E8ECF0;letter-spacing:0.08em;text-transform:uppercase;}}
.logo span{{color:#388BFD;}}
.header-meta{{font-family:'Courier New',monospace;font-size:9px;color:#586068;letter-spacing:0.12em;text-transform:uppercase;text-align:right;line-height:1.6;}}
.header-subject{{padding:18px 28px 20px;}}
.header-subject .tag{{font-family:'Courier New',monospace;font-size:9px;color:#388BFD;letter-spacing:0.16em;text-transform:uppercase;margin-bottom:6px;}}
.header-subject h1{{font-size:22px;font-weight:800;color:#E8ECF0;line-height:1.2;}}
.score-band{{background:#161B22;padding:20px 28px;display:flex;align-items:center;gap:24px;border-bottom:3px solid {score_color};}}
.score-circle{{width:72px;height:72px;border-radius:50%;border:3px solid {score_color};display:flex;align-items:center;justify-content:center;flex-shrink:0;}}
.score-num{{font-family:'Courier New',monospace;font-size:26px;font-weight:700;color:{score_color};line-height:1;}}
.score-info .regime{{font-family:'Courier New',monospace;font-size:9px;color:{score_color};letter-spacing:0.16em;text-transform:uppercase;margin-bottom:4px;}}
.score-info .score-label{{font-size:16px;font-weight:700;color:#E8ECF0;margin-bottom:4px;}}
.score-info .score-sub{{font-family:'Courier New',monospace;font-size:10px;color:#586068;}}
.content{{padding:28px;}}
.section-label{{font-family:'Courier New',monospace;font-size:9px;font-weight:600;letter-spacing:0.16em;text-transform:uppercase;color:#8B949E;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #E5E8EC;}}
.summary p{{font-size:13px;color:#2D3139;line-height:1.75;margin-bottom:10px;}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:4px;}}
thead tr{{background:#1A1D20;}}
thead th{{padding:8px 10px;text-align:left;font-family:'Courier New',monospace;font-size:8px;letter-spacing:0.12em;text-transform:uppercase;color:#8B949E;border-bottom:2px solid #388BFD;}}
tbody tr:nth-child(odd){{background:#F8F9FB;}}
td{{padding:9px 10px;border-bottom:1px solid #E5E8EC;vertical-align:middle;}}
.pill{{display:inline-block;padding:2px 7px;border-radius:4px;font-family:'Courier New',monospace;font-size:8px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;}}
.red{{background:#FFEBEE;color:#D73A49;}} .orange{{background:#FFF3E0;color:#F98C4F;}} .yellow{{background:#FFF8E1;color:#E6A817;}} .green{{background:#E8F5E9;color:#2E7D32;}}
.deep-dive{{background:#FFF8E1;border-left:3px solid #F98C4F;padding:14px 16px;border-radius:0 6px 6px 0;margin-bottom:4px;}}
.deep-dive p{{font-size:12.5px;color:#2D3139;line-height:1.7;margin-bottom:8px;}}
.page-divider{{margin:28px 0;border:none;border-top:2px dashed #E5E8EC;}}
.page-label{{text-align:center;font-family:'Courier New',monospace;font-size:9px;color:#8B949E;letter-spacing:0.14em;text-transform:uppercase;margin:12px 0 20px;}}
.sector-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
.sector-col-ow{{background:#F1FAF3;border:1px solid #C8E6C9;border-radius:8px;overflow:hidden;}}
.sector-col-uw{{background:#FFF5F5;border:1px solid #FFCDD2;border-radius:8px;overflow:hidden;}}
.sector-col-header{{padding:8px 12px;font-family:'Courier New',monospace;font-size:8px;letter-spacing:0.12em;text-transform:uppercase;font-weight:700;}}
.sector-col-ow .sector-col-header{{background:#E8F5E9;color:#2E7D32;}}
.sector-col-uw .sector-col-header{{background:#FFEBEE;color:#C62828;}}
.sector-item{{padding:7px 12px;border-bottom:1px solid rgba(0,0,0,0.05);}}
.sector-item:last-child{{border-bottom:none;}}
.sector-name{{font-size:11.5px;font-weight:600;margin-bottom:1px;}}
.sector-col-ow .sector-name{{color:#1B5E20;}} .sector-col-uw .sector-name{{color:#B71C1C;}}
.sector-rationale{{font-size:10px;color:#586068;line-height:1.4;}}
.alloc-pct{{font-family:'Courier New',monospace;font-size:14px;font-weight:700;color:#388BFD;}}
.verdict-box{{background:#FFF8E1;border:2px solid #F98C4F;border-radius:8px;padding:16px 18px;}}
.verdict-box p{{font-size:13px;color:#2D3139;line-height:1.75;}}
.footer{{background:#0D1117;padding:20px 28px;text-align:center;}}
.footer-logo{{font-size:13px;font-weight:800;color:#8B949E;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;}}
.footer-logo span{{color:#388BFD;}}
.footer-links{{margin-bottom:10px;}}
.footer-links a{{font-family:'Courier New',monospace;font-size:10px;color:#586068;margin:0 8px;}}
.footer-disclaimer{{font-family:'Courier New',monospace;font-size:9px;color:#3D4450;line-height:1.6;max-width:480px;margin:0 auto;}}
.spacer{{height:20px;}}
</style></head><body><div class="wrapper">
<div class="header"><div class="header-top"><div class="logo">US<span>MRM</span></div><div class="header-meta">MRM WEEKLY AUDIT<br>{today} · ISSUE #{issue_number}</div></div><div class="header-subject"><div class="tag">Subject: Regime Diagnosis &amp; Tactical Execution</div><h1>US Macro-Resilience Matrix<br>Weekly Institutional Memo</h1></div></div>
<div class="score-band"><div class="score-circle"><div class="score-num">SCORE_HERE</div></div><div class="score-info"><div class="regime">● REGIME_HERE REGIME</div><div class="score-label">Global Resilience Score</div><div class="score-sub">Updated: {today} · FRED API Live · {_pillars_live} · WOW_HERE</div></div></div>
<div class="content">EXECUTIVE_SUMMARY_HERE PILLARS_TABLE_HERE DEEP_DIVE_HERE</div>
<hr class="page-divider"><div class="page-label">— Tactical Execution —</div>
<div class="content" style="padding-top:0;">EWS_TABLE_HERE SECTOR_MATRIX_HERE ALLOCATION_TABLE_HERE VERDICT_BOX_HERE PORTFOLIO_REBALANCE_STATUS_HERE</div>
<div class="footer"><div class="footer-logo">US<span>MRM</span> Intelligence Hub</div><div class="footer-links"><a href="https://usmrm.net">Live Terminal</a><a href="https://usmrm.net">Newsletter</a><a href="https://usmrm.net">BDCs</a><a href="mailto:usmrm@proton.me">Contact</a></div><div class="footer-disclaimer">This newsletter is produced for educational and personal analysis purposes only. It does not constitute financial advice.<br>All data sourced from FRED API · © 2026 US MRM Intelligence Hub · usmrm.net</div></div>
</div></body></html>

Replace ALL_CAPS placeholders with complete real HTML content. Return ONLY the final HTML."""


def render_archive_card(c, filename):
    """Cartão da edição no arquivo do index.html."""
    issue_number = c["issue_number"]; today = c["today"]
    regime = c["regime_label"];       score = c["score"]
    score_color = c["score_color"];   wow_score = c["wow_score"]
    p = c["pillars"]
    def _sc(pid):
        v = p.get(pid, {}).get("score")
        return "n/d" if v is None else v
    cycle_s, liquidity_s = _sc("cycle"), _sc("liquidity")
    premium_s, solvency_s, debt_s = _sc("premium"), _sc("solvency"), _sc("debt")
    badge_color = "#D73A49" if c["stress_active"] else score_color
    return f"""
      <!-- ISSUE #{issue_number} -->
      <div style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px;">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid var(--border-subtle);">
          <div style="display:flex;align-items:center;gap:16px;">
            <div style="font-family:var(--mono);font-size:11px;font-weight:600;color:var(--text-muted);">ISSUE #{issue_number}</div>
            <div style="width:1px;height:16px;background:var(--border);"></div>
            <div style="font-family:var(--mono);font-size:11px;color:var(--text-muted);">{today}</div>
            <div style="padding:2px 8px;border-radius:4px;background:rgba(0,0,0,0.25);border:1px solid {badge_color};font-family:var(--mono);font-size:9px;font-weight:600;color:{badge_color};">{regime.upper()}</div>
          </div>
          <div style="display:flex;align-items:center;gap:20px;">
            <div style="text-align:right;"><div style="font-family:var(--mono);font-size:9px;color:var(--text-muted);text-transform:uppercase;">Score</div><div style="font-family:var(--mono);font-size:20px;font-weight:600;color:{score_color};">{score}</div></div>
            <div style="font-family:var(--mono);font-size:11px;color:{score_color};">{wow_score}</div>
          </div>
        </div>
        <div style="padding:16px 24px;display:grid;grid-template-columns:repeat(5,1fr);gap:12px;">
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Cycle</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{cycle_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Liquidity</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{liquidity_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Premium</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{premium_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Solvency</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--green);">{solvency_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Debt</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{debt_s}</div></div>
        </div>
        <div style="padding:0 24px 20px;display:flex;gap:12px;">
          <a href="/{filename}" target="_blank" style="padding:8px 16px;background:var(--blue-dim);border:1px solid rgba(56,139,253,0.3);border-radius:6px;color:var(--blue);font-size:12px;font-weight:500;text-decoration:none;">Read Full Issue →</a>
          <a href="https://twitter.com/intent/tweet?text=🧊+MRM+Weekly+Signal+Issue+%23{issue_number}+%7C+Score+{score}%2F10+%7C+{regime}%0Ausmrm.net%0A%23MacroInvesting+%23ERP+%23Finance" target="_blank" style="padding:8px 16px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;color:var(--text-secondary);font-size:12px;text-decoration:none;">𝕏 Share</a>
        </div>
      </div>"""


def build_tweet(c, filename):
    issue_number = c["issue_number"]; score = c["score"]; wow_score = c["wow_score"]
    regime = c["regime_label"]; p = c["pillars"]
    g = "ON" if c["stress_active"] else "OFF" if c["stress_active"] is False else "n/d"
    def _sc(pid):
        v = p.get(pid, {}).get("score")
        return "n/d" if v is None else v
    return f"""\U0001f9ca MRM Weekly Signal \u2014 Issue #{issue_number}

Score {score}/10 {wow_score} | {regime}
Gauge B (concurrent stress): {g}

Pillars:
\u00b7 Cycle: {_sc('cycle')}/10
\u00b7 Liquidity: {_sc('liquidity')}/10
\u00b7 Premium (ERP): {_sc('premium')}/10
\u00b7 Solvency: {_sc('solvency')}/10
\u00b7 Debt: {_sc('debt')}/10

Portfolio: ${c["port_value"]} | Alpha vs SPY: {c["port_alpha"]}%
Rebalance: {c["rb_alert"]}

Read \u2192 usmrm.net/{filename}

#MacroInvesting #ERP #Finance #WeekendReading"""


# ─────────────────────────────────────────────────────────────────────────────
# I/O, rede e efeitos — só a partir daqui
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: {what} ({path}) — {e}")
        return {}


def call_model(prompt, api_key):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-opus-4-6", "max_tokens": 8000,
              "messages": [{"role": "user", "content": prompt}]},
    )
    if r.status_code != 200:
        raise RuntimeError(f"Claude API error: {r.status_code} — {r.text}")
    html = r.json()["content"][0]["text"].strip()
    if html.startswith("```"):
        html = html.split("```html")[-1].split("```")[0].strip()
    return html


def update_archive(index_path, card_html, issue_number):
    with open(index_path, "r", encoding="utf-8") as f:
        index = f.read()
    marker = "<!-- NEWSLETTER_ARCHIVE_START -->"
    if marker not in index:
        print("Warning: archive marker not found in index.html")
        return False
    # Remove um cartão existente da mesma edição, para que uma re-corrida
    # correctiva substitua em vez de duplicar.
    index = re.sub(rf'\s*<!-- ISSUE #{issue_number} -->.*?(?=<!-- ISSUE #|\Z)',
                   '', index, count=1, flags=re.S)
    index = index.replace(marker, marker + "\n\n      " + card_html)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index)
    return True


def git_publish(files, message):
    subprocess.run(["git", "config", "user.email", "action@github.com"], check=True)
    subprocess.run(["git", "config", "user.name", "MRM Newsletter Bot"], check=True)
    subprocess.run(["git", "add", *files], check=True)
    if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode != 0:
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("Pushed to GitHub")


def brevo_subscribers(api_key):
    r = requests.get("https://api.brevo.com/v3/contacts",
                     headers={"api-key": api_key, "accept": "application/json"},
                     params={"limit": 1000, "offset": 0})
    contacts = r.json().get("contacts", []) if r.status_code == 200 else []
    emails = [c["email"] for c in contacts
              if c.get("emailBlacklisted") is False and c.get("email")]
    return emails or ["usmrm@proton.me"]


def brevo_send(api_key, sender, to, subject, html):
    r = requests.post("https://api.brevo.com/v3/smtp/email",
                      headers={"api-key": api_key, "content-type": "application/json",
                               "accept": "application/json"},
                      json={"sender": sender, "to": [{"email": e} for e in to],
                            "subject": subject, "htmlContent": html})
    return r.status_code in (200, 201, 202), r


def main():
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]
    brevo_key     = os.environ["BREVO_API_KEY"]

    today = datetime.utcnow().date()
    issue_number = issue_number_for(today)

    data      = _load_json("data.json", "data.json is required")
    portfolio = _load_json("portfolio.json", "portfolio.json not found — portfolio section will be thin")
    prev      = _load_json("data_prev.json", "no previous week — WoW skipped")

    c = build_context(data, portfolio, prev, today, issue_number)
    print(f"Issue #{issue_number} — {c['today']} | score {c['score']} | "
          f"regime {c['regime_label']} | gauge B {c['gauge_b_line']} | rebalance {c['rb_reason']}")

    html_content = call_model(build_prompt(c), anthropic_key)
    print(f"HTML generated ({len(html_content)} chars)")

    filename = f"MRM_Newsletter_Issue{issue_number}_{c['today_file']}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Saved: {filename}")

    shutil.copy("data.json", "data_prev.json")

    if update_archive("index.html", render_archive_card(c, filename), issue_number):
        print("index.html updated")

    git_publish([filename, "index.html", "data_prev.json"],
                f"Auto: Newsletter Issue #{issue_number} — {c['today']}")

    subscribers = brevo_subscribers(brevo_key)
    print(f"Sending to {len(subscribers)} subscribers...")
    ok, r = brevo_send(
        brevo_key,
        {"name": "US MRM Intelligence Hub", "email": "noreply@usmrm.net"},
        subscribers,
        f"MRM Weekly Signal — Issue #{issue_number} | {c['today']} | "
        f"Score {c['score']}/10 · {c['regime_label']}",
        html_content)
    if not ok:
        print(f"Brevo send error: {r.status_code} — {r.text}")
        raise SystemExit(1)
    print(f"Issue #{issue_number} sent to {len(subscribers)} subscribers")

    tweet_text = build_tweet(c, filename)
    tweet_html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#F2F4F7;padding:24px;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;">
  <div style="font-family:Courier New,monospace;font-size:9px;font-weight:600;letter-spacing:0.16em;text-transform:uppercase;color:#8B949E;margin-bottom:12px;">SATURDAY TWEET — Copy &amp; Paste on Twitter/X</div>
  <div style="background:#F8F9FB;border:1px solid #E5E8EC;border-radius:8px;padding:16px;font-family:Courier New,monospace;font-size:13px;color:#1A1D20;line-height:1.8;white-space:pre-wrap;">{tweet_text}</div>
  <div style="margin-top:16px;padding:12px;background:#EBF5FF;border-radius:8px;font-size:12px;color:#586068;">
    Newsletter Issue #{issue_number} sent to {len(subscribers)} subscriber(s).<br>
    Live at: <a href="https://usmrm.net/{filename}">usmrm.net/{filename}</a><br><br>
    Gauge B: {c['gauge_b_line']}<br>
    Portfolio: {c['regime_label']} — {c['rb_reason']}
  </div>
</div>
</body></html>"""
    ok, r = brevo_send(brevo_key, {"name": "MRM System", "email": "noreply@usmrm.net"},
                       ["usmrm@proton.me"],
                       f"MRM Issue #{issue_number} sent — Saturday tweet ready", tweet_html)
    print("Owner briefing sent" if ok else f"Owner briefing error: {r.status_code}")


if __name__ == "__main__":
    main()
