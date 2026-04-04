#!/usr/bin/env python3
"""
MRM Weekly Newsletter — Full Automation
Reads data.json → calculates issue number → calls Claude API →
saves newsletter HTML → updates index.html archive → sends via Brevo
"""

import json, os, re, subprocess, requests, shutil
from datetime import datetime, date

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
BREVO_KEY     = os.environ["BREVO_API_KEY"]

today      = datetime.utcnow().strftime("%d %B %Y")
today_file = datetime.utcnow().strftime("%d%b%Y")
start_date = date(2026, 3, 13)
issue_number = ((date.today() - start_date).days // 7) + 1
print(f"Generating Issue #{issue_number} — {today}")

with open("data.json", "r") as f:
    data = json.load(f)

# Load portfolio state for rebalance status
portfolio_data = {}
try:
    with open("portfolio.json", "r") as f:
        portfolio_data = json.load(f)
except Exception:
    print("Warning: portfolio.json not found — skipping rebalance status")

global_score = data.get('globalResilienceScore', 'N/A')
regime       = data.get('status', 'N/A')
pillars      = {p['id']: p for p in data.get('pillars', [])}
sentinels    = {s['id']: s for s in data.get('sentinels', [])}
cycle        = pillars.get('cycle', {})
liquidity    = pillars.get('liquidity', {})
premium      = pillars.get('premium', {})
solvency     = pillars.get('solvency', {})
debt         = pillars.get('debt', {})
icsa         = sentinels.get('icsa', {})
erp          = sentinels.get('erp', {})
score        = round(global_score, 1) if isinstance(global_score, (int, float)) else global_score

try:
    with open("data_prev.json", "r") as f:
        prev_data = json.load(f)
    prev_score   = prev_data.get('globalResilienceScore', None)
    prev_pillars = {p['id']: p for p in prev_data.get('pillars', [])}
except:
    prev_score, prev_pillars = None, {}
    print("No previous week data — skipping WoW")

def wow(pid, cur):
    prev = prev_pillars.get(pid, {}).get('score')
    if prev is None: return "—"
    d = round(float(cur) - float(prev), 1)
    return f"▲ +{d}" if d > 0 else f"▼ {d}" if d < 0 else "— 0.0"

wow_score = ""
if prev_score:
    d = round(float(global_score) - float(prev_score), 1)
    wow_score = f"▲ +{d} WoW" if d > 0 else f"▼ {d} WoW" if d < 0 else "— 0.0 WoW"

score_color = "#D73A49" if float(global_score) >= 8 else "#F98C4F" if float(global_score) >= 5 else "#34D058"

# ── Portfolio rebalance status ────────────────────────────────────────────────
from datetime import date as _date, timedelta as _td

_REGIME_ETFS = {
    "Turbulence": "SPY | IEF | LQD | PDBC | BIL | VNQ",
    "Critical":   "USMV | TLT | SGOV | GLD | BIL | VNQ",
    "Resilient":  "QQQ | SHY | HYG | PDBC | BIL | IWO",
}

def _next_semestral_date():
    yr = _date.today().year
    for offset in [0, 1]:
        for month in [1, 6]:
            for d in range(31, 24, -1):
                try:
                    dt = _date(yr + offset, month, d)
                    if dt.weekday() == 4 and dt >= _date.today():
                        return dt.strftime("%d %B %Y")
                except ValueError:
                    continue
    return "January 2027"

_port_cur    = portfolio_data.get("current", {})
_port_regime = _port_cur.get("regime", "Turbulence")
_port_etfs   = _REGIME_ETFS.get(_port_regime, _REGIME_ETFS["Turbulence"])
_port_value  = _port_cur.get("portfolio_value", "N/A")
_port_alpha  = _port_cur.get("alpha_vs_benchmark_pct", "N/A")
_port_pnl    = _port_cur.get("portfolio_pnl_pct", "N/A")
_next_sem    = _next_semestral_date()

_curr_s  = float(global_score) if isinstance(global_score, (int, float)) else None
_prev_s  = float(prev_score) if prev_score is not None else None
_today_d = _date.today()
_friday  = _today_d - _td(days=(_today_d.weekday() - 4) % 7)
_is_sem  = _friday.month in {1, 6} and (_friday + _td(days=7)).month != _friday.month

_h_now  = _curr_s is not None and _curr_s >= 8.0
_l_now  = _curr_s is not None and _curr_s <= 4.0
_h_prev = _prev_s is not None and _prev_s >= 8.0
_l_prev = _prev_s is not None and _prev_s <= 4.0

if _is_sem:
    _rb_alert  = "SEMESTRAL"
    _rb_status = f"SEMESTRAL REBALANCE THIS WEEK — Regime: {_port_regime}. ETFs: {_port_etfs}. Executes Saturday 10:00 UTC."
    _rb_color  = "#1a3a5c"
    _rb_border = "#388BFD"
    _rb_icon   = "⟳"
elif _h_now and _h_prev:
    _rb_alert  = "EMERGENCY_CRITICAL_CONFIRMED"
    _rb_status = f"EMERGENCY REBALANCE ACTIVATED — Score ≥8.0 confirmed 2 consecutive weeks. Rotating to Critical ETFs: USMV | TLT | SGOV | GLD | BIL | VNQ. Executes Saturday 10:00 UTC."
    _rb_color  = "#2d1515"
    _rb_border = "#D73A49"
    _rb_icon   = "⚠"
elif _l_now and _l_prev:
    _rb_alert  = "EMERGENCY_RESILIENT_CONFIRMED"
    _rb_status = f"EMERGENCY REBALANCE ACTIVATED — Score ≤4.0 confirmed 2 consecutive weeks. Rotating to Resilient ETFs: QQQ | SHY | HYG | PDBC | BIL | IWO. Executes Saturday 10:00 UTC."
    _rb_color  = "#122008"
    _rb_border = "#34D058"
    _rb_icon   = "⚠"
elif _h_now:
    _rb_alert  = "EMERGENCY_WEEK1_CRITICAL"
    _rb_status = f"EMERGENCY TRIGGER — WEEK 1 OF 2. Score this week: {_curr_s} | Last week: {_prev_s}. If score remains ≥8.0 next week → ETFs rotate to: USMV | TLT | SGOV | GLD | BIL | VNQ."
    _rb_color  = "#2d1f0a"
    _rb_border = "#F98C4F"
    _rb_icon   = "⚠"
elif _l_now:
    _rb_alert  = "EMERGENCY_WEEK1_RESILIENT"
    _rb_status = f"EMERGENCY TRIGGER — WEEK 1 OF 2. Score this week: {_curr_s} | Last week: {_prev_s}. If score remains ≤4.0 next week → ETFs rotate to: QQQ | SHY | HYG | PDBC | BIL | IWO."
    _rb_color  = "#0a1f18"
    _rb_border = "#34D058"
    _rb_icon   = "⚠"
else:
    _rb_alert  = "INACTIVE"
    _rb_status = f"No structural regime change detected. Holding current positions."
    _rb_color  = "#122008"
    _rb_border = "#34D058"
    _rb_icon   = "●"

prompt = f"""You are a Senior Risk Strategist and CIO. Generate a complete MRM Weekly Institutional Newsletter in HTML.

TODAY: {today} | ISSUE: #{issue_number}

DATA:
- Global Score: {score}/10 ({wow_score}) | Regime: {regime}
- Cycle: {cycle.get('value','N/A')} | Score: {cycle.get('score','N/A')}/10 | WoW: {wow('cycle', cycle.get('score',0))} | {cycle.get('status','N/A')}
- Liquidity: {liquidity.get('value','N/A')} | Score: {liquidity.get('score','N/A')}/10 | WoW: {wow('liquidity', liquidity.get('score',0))} | {liquidity.get('status','N/A')}
- Premium (ERP): {premium.get('value','N/A')} | Score: {premium.get('score','N/A')}/10 | WoW: {wow('premium', premium.get('score',0))} | {premium.get('status','N/A')}
- Solvency: {solvency.get('value','N/A')} | Score: {solvency.get('score','N/A')}/10 | WoW: {wow('solvency', solvency.get('score',0))} | {solvency.get('status','N/A')}
- Debt: {{debt.get('value','N/A')}} | Score: {{debt.get('score','N/A')}}/10 | WoW: {{wow('debt', debt.get('score',0))}} | {{debt.get('status','N/A')}}
- ICSA: {icsa.get('value','N/A')} Alert:{icsa.get('alert',False)} | ERP Sentinel: {erp.get('value','N/A')} Alert:{erp.get('alert',False)}

PORTFOLIO REBALANCE STATUS:
- Alert Level: {_rb_alert}
- Status: {_rb_status}
- Next Scheduled Semestral Rebalance: {_next_sem}
- Current Portfolio Regime: {_port_regime} | Active ETFs: {_port_etfs}
- Portfolio Value: ${_port_value} | P&L: {_port_pnl}% | Alpha vs SPY: {_port_alpha}%
- Score This Week: {_curr_s} | Score Last Week: {_prev_s}

RULES:
1. Institutional tone. Dry. Objective. Risk-focused.
2. NEVER mention individual tickers. Only Sectors/Factors/Asset Classes.
3. English only.
4. Include WoW column in all tables.
5. Deep Dive on biggest WoW mover.
6. Return ONLY complete HTML. No markdown. No backticks.
7. After the CIO Verdict section, include a PORTFOLIO REBALANCE STATUS section using the data above.
   Style it as a distinct box with:
   - Background: {_rb_color}
   - Border-left: 4px solid {_rb_border}
   - Icon: {_rb_icon}
   - Title: "PORTFOLIO REBALANCE STATUS"
   - Show: Alert level, status text, next semestral date, current ETFs, portfolio value/P&L/alpha.
   - If INACTIVE: calm green tone. If EMERGENCY_WEEK1: orange warning tone. If EMERGENCY_CONFIRMED: red urgent tone. If SEMESTRAL: blue informational tone.

HTML STRUCTURE (fill ALL placeholders with real content):
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>MRM Weekly Audit — Issue #{issue_number} — {today}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#F2F4F7;font-family:Arial,sans-serif;font-size:14px;color:#1A1D20;}}
a{{color:#388BBD;text-decoration:none;}}
.wrapper{{max-width:640px;margin:32px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);}}
.header{{background:#00D117;}}
.header-top{{display:flex;align-items:center;justify-content:space-between;padding:14px 28px;border-bottom:1px solid #21262D;}}
.logo{{font-size:15px;font-weight:800;color:#E8ECF0;letter-spacing:0.08em;text-transform:uppercase;}}
.logo span{{color:#388BFD;}}
.header-meta{{font-family:'Courier New',monospace;font-size:9px;color:#586068;letter-spacing:0.12em;text-transform:uppercase;text-align:right;line-height:1.6;}}
.header-subject{{padding:18px 28px 20px;}}
.header-subject .tag{{font-family:'Courier New',monospace;font-size:9px;color:#388BFD;letter-spacing:0.16em;text-transform:uppercase;margin-bottom:6px;}}
.header-subject h1{{font-size:22px;font-weight:800;color:#E8ECF0;line-height:1.2;}}
.score-band{{background:#161B22;padding:20px 28px;display:flex;align-items:center;justify-content:center;gap:24px;border-bottom:3px solid #F98C4F;}}
.score-circle{{width:72px;height:72px;border-radius:50%;border:3px solid #F98C4F;display:flex;align-items:center;justify-content:center;flex-shrink:0;}}
.score-num{{font-family:'Courier New',monospace;font-size:26px;font-weight:700;color:#F98C4F;line-height:1;}}
.score-info .regime{{font-family:'Courier New',monospace;font-size:9px;color:#F98C4F;letter-spacing:0.16em;text-transform:uppercase;margin-bottom:4px;}}
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
.red{{background:#FFEBEE;color:#D73A49;}} .orange{{background:#FFF3E0;color:#F98C4F;}} .yellow{{background:#FFF8E1;color:#F6A817;}} .green{{background:#E8F5E9;color:#2E7D32;}}
.deep-dive{{background:#FFFBF1;border-left:3px solid #F98C4F;padding:14px 16px;border-radius:0 6px 6px 0;margin-bottom:4px;}}
.deep-dive p{{font-size:12.5px;color:#2D3139;line-height:1.7;margin-bottom:8px;}}
.page-divider{{margin:28px 0;border:none;border-top:2px dashed #E5E8EC;}}
.page-label{{text-align:center;font-family:'Courier New',monospace;font-size:9px;color:#8B949E;letter-spacing:0.14em;text-transform:uppercase;margin:12px 0 20px;}}
.sector-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
.sector-col-ow{{background:#F1FAF3;border:1px solid #C8E6C9;border-radius:8px;overflow:hidden;}}
.sector-col-uw{{background:#FFF5F5;border:1px solid #FFCDD2;border-radius:8px;overflow:hidden;}}
.sector-col-header{{padding:8px 12px;font-family:'Courier New',monospace;font-size:8px;letter-spacing:0.12em;text-transform:uppercase;font-weight:700;}}
.sector-col-ow .sector-col-header{{background:#FFEBEE;color:#2E7D32;}}
.sector-col-uw .sector-col-header{{background:#FFEBEE;color:#C62828;}}
.sector-col ow{{background:#E8F5E9;color:#2E7D32;}}
.sector-item{{padding:7px 12px;border-bottom:1px solid rgba(0,0,0,0.05);}}
.sector-item:last-child{{border-bottom:none;}}
.sector-name{{font-size:11.5px;font-weight:600;margin-bottom:1px;}}
.sector-col-ow .sector-name{{color:#1B5E20;}} .sector-col-uw .sector-name{{color:#B71C1C;}}
.sector-rationale{{font-size:10px;color:#586068;line-height:1.4;}}
.alloc-pct{{font-family:'Courier New',monospace;font-size:14px;font-weight:700;color:#388BFD;}}
.verdict-box{{background:#FFF8E1;border:2px solid #F98C4F;border-radius:8px;padding:16px 18px;}}
.verdict-box p{{font-size:13px;color:#2D3139;line-height:1.75;}}
.rebalance-box{{margin-top:16px;padding:14px 18px;border-radius:6px;border-left:4px solid;}}
.rebalance-box .rb-title{{font-family:'Courier New',monospace;font-size:9px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;margin-bottom:8px;}}
.rebalance-box .rb-status{{font-size:12px;line-height:1.6;margin-bottom:8px;}}
.rebalance-box .rb-meta{{font-family:'Courier New',monospace;font-size:10px;color:#586068;line-height:1.5;}}
.footer{{background:#0D1117;padding:20px 28px;text-align:center;}}
.footer-logo{{font-size:13px;font-weight:800;color:#8B949E;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;}}
.footer-logo span{{color:#388BFD;}}
.footer-links{{margin-bottom:10px;}}
.footer-links a{{font-family:'Courier New',monospace;font-size:10px;color:#586068;margin:0 8px;}}
.footer-disclaimer{{font-family:'Courier New',monospace;font-size:9px;color:#3D4450;line-height:1.6;max-width:480px;margin:0 auto;}}
.spacer{{height:20px;}}
</style>
</head><body><div class="wrapper">
<div class="header"><div class="header-top"><div class="logo">US<span>MRM</span></div><div class="header-meta">MRM WEEKLY AUDIT<br>{today} · ISSUE #{issue_number}</div></div><div class="header-subject"><div class="tag">Subject: Regime Diagnosis &amp; Tactical Execution</div><h1>US Macro-Resilience Matrix Weekly Institutional Memo</h1></div></div>
<div class="score-band"><div class="score-circle"><div class="score-num">{score}</div></div><div class="score-info"><div class="regime">● {regime} REGIME</div><div class="score-label">Global Resilience Score</div><div class="score-sub">Updated: {today} · FRED API Live · 5/5 Pillars Active · {wow_score}</div></div></div>
<div class="content">EXECUTIVE_SUMMARY_HERE PILLARS_TABLE_HERE DEEP_DIVE_HERE</div>
<hr class="page-divider"><div class="page-label">— Tactical Execution —</div>
<div class="content" style="padding-top:0;">EWS_TABLE_HERE SECTOR_MATRIX_HERE ALLOCATION_TABLE_HERE VERDICT_BOX_HERE PORTFOLIO_REBALANCE_STATUS_HERE</div>
<div class="footer"><div class="footer-logo">US<span>MRM</span> Intelligence Hub</div><div class="footer-links"><a href="https://usmrm.net">Live Terminal</a><a href="https://usmrm.net">Newsletter</a><a href="https://usmrm.net">BDCs</a><a href="mailto:usmrm@proton.me">Contact</a></div><div class="footer-disclaimer">This newsletter is produced for educational and personal analysis purposes only. It does not constitute financial advice.<br>All data sourced from FRED API · © 2026 US MRM Intelligence Hub · usmrm.net</div></div>
</div></body></html>

Replace ALL_CAPS placeholders with complete real HTML content based on the data above.
For PORTFOLIO_REBALANCE_STATUS_HERE, generate a styled div with class="rebalance-box" using background-color:{_rb_color} and border-color:{_rb_border}, showing the icon {_rb_icon}, alert level, status text, next semestral date, current ETFs, portfolio value, P&L, and alpha.
Return ONLY the final HTML."""

print("Calling Claude API...")
response = requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={{"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}},
    json={{"model": "claude-opus-4-6", "max_tokens": 8000, "messages": [{{"role": "user", "content": prompt}}]}}
)

if response.status_code != 200:
    print(f"Claude API error: {{response.status_code}} — {{response.text}}"); exit(1)

html_content = response.json()["content"][0]["text"].strip()
if html_content.startswith("```"):
    html_content = html_content.split("```html")[-1].split("```")[0].strip()
print(f"HTML generated ({{len(html_content)}} chars)")

filename = f"MRM_Newsletter_Issue{{issue_number}}_{today_file}.html"
with open(filename, "w", encoding="utf-8") as f:
    f.write(html_content)
print(f"Saved: {{filename}}")

shutil.copy("data.json", "data_prev.json")

# Update index.html archive
with open("index.html", "r", encoding="utf-8") as f:
    index = f.read()

new_card = f"""
    <!-- ISSUE #{issue_number} -->
    <div style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px;">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid var(--border-subtle);">
        <div style="display:flex;align-items:center;gap:16px;">
          <div style="font-family:var(--mono);font-size:11px;font-weight:600;color:var(--text-muted);">ISSUE #{issue_number}</div>
          <div style="width:1px;height:16px;background:var(--border);"></div>
          <div style="font-family:var(--mono);font-size:11px;color:var(--text-muted);">{today}</div>
          <div style="padding:2px 8px;border-radius:4px;background:var(--orange-dim);border:1px solid rgba(249,140,79,0.3);font-family:var(--mono);font-size:9px;font-weight:600;color:var(--orange);">{regime}</div>
        </div>
        <div style="display:flex;align-items:center;gap:20px;">
          <div style="text-align:right;">
            <div style="font-family:var(--mono);font-size:9px;color:var(--text-muted);text-transform:uppercase;">Score</div>
            <div style="font-family:var(--mono);font-size:20px;font-weight:600;color:{score_color};">{score}</div>
          </div>
          <div style="font-family:var(--mono);font-size:11px;color:var(--text-muted);">{wow_score}</div>
        </div>
      </div>
      <div style="padding:16px 24px;display:grid;grid-template-columns:repeat(5,1fr);gap:12px;">
        <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Cycle</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{cycle.get('score','N/A')}</div></div>
        <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Liquidity</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{liquidity.get('score','N/A')}</div></div>
        <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Premium</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{premium.get('score','N/A')}</div></div>
        <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Solvency</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--green);">{solvency.get('score','N/A')}</div></div>
        <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Debt</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{debt.get('score','N/A')}</div></div>
      </div>
      <div style="padding:0 24px 20px;display:flex;gap:12px;">
        <a href="/{filename}" target="_blank" style="padding:8px 16px;background:var(--blue-dim);border:1px solid rgba(56,139,253,0.3);border-radius:6px;color:var(--blue);font-size:12px;font-weight:500;text-decoration:none;">Read Full Issue →</a>
        <a href="https://twitter.com/intent/tweet?text=🧊+MRM+Weekly+Signal+—+Issue+%23{issue_number}%0A%0AScore+{score}%2F10+|+{regime}%0A%0Ausmrm.net%2F{filename}%0A%0A%23MacroInvesting+%23ERP+%23Finance+%23WeekendReading" target="_blank" style="padding:8px 16px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;color:var(--text-secondary);font-size:12px;text-decoration:none;">𝕏 Share</a>
      </div>
    </div>"""

marker = "<!-- NEWSLETTER_ARCHIVE_START -->"
if marker in index:
    index = index.replace(marker, marker + "\n\n" + new_card)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(index)
    print("index.html archive updated")

    result = subprocess.run(
        ["git", "add", "index.html", filename, "data_prev.json"],
        capture_output=True, text=True
    )
    result = subprocess.run(
        ["git", "commit", "-m", f"Auto: Newsletter Issue #{issue_number} — {today}"],
        capture_output=True, text=True
    )
    result = subprocess.run(["git", "push"], capture_output=True, text=True)
    print("Committed and pushed")

# ── Get subscribers from Brevo ────────────────────────────────────────────────
sub_r = requests.get(
    "https://api.brevo.com/v3/contacts",
    headers={"api-key": BREVO_KEY, "accept": "application/json"},
    params={"limit": 1000, "offset": 0}
)
all_contacts = sub_r.json().get("contacts", []) if sub_r.status_code == 200 else []
clean_subscribers = [
    c["email"] for c in all_contacts
    if c.get("emailBlacklisted") is False and c.get("email")
]
print(f"Sending to {len(clean_subscribers)} subscribers...")

# ── Send newsletter via Brevo ─────────────────────────────────────────────────
send_r = requests.post("https://api.brevo.com/v3/smtp/email",
    headers={"api-key": BREVO_KEY, "content-type": "application/json", "accept": "application/json"},
    json={"sender": {"name": "US MRM Intelligence Hub", "email": "noreply@usmrm.net"},
          "to": [{"email": e} for e in clean_subscribers],
          "subject": f"MRM Weekly Signal — Issue #{issue_number} | {today} | Score {score}/10 · {regime}",
          "htmlContent": html_content})

if send_r.status_code in [200, 201, 202]:
    print(f"✅ Issue #{issue_number} sent to {len(clean_subscribers)} subscribers!")
else:
    print(f"❌ Brevo send error: {send_r.status_code} — {send_r.text}"); exit(1)

# ── Build tweet text ──────────────────────────────────────────────────────────
tweet_text = f"""🧊 MRM Weekly Signal — Issue #{issue_number}

Score {score}/10 {wow_score} | {regime} Regime

Pillars:
· Cycle: {cycle.get('score','N/A')}/10
· Liquidity: {liquidity.get('score','N/A')}/10
· Premium (ERP): {premium.get('score','N/A')}/10
· Solvency: {solvency.get('score','N/A')}/10
· Debt: {debt.get('score','N/A')}/10

Portfolio: ${_port_value} | Alpha vs SPY: {_port_alpha}%
Rebalance: {_rb_alert}

Read → usmrm.net/{filename}

#MacroInvesting #ERP #Finance #WeekendReading"""

# ── Send owner briefing ───────────────────────────────────────────────────────
tweet_html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#F2F4F7;padding:24px;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;">
  <div style="font-family:Courier New,monospace;font-size:9px;font-weight:600;letter-spacing:0.16em;text-transform:uppercase;color:#8B949E;margin-bottom:12px;">🐦 SATURDAY TWEET — Copy &amp; Paste on Twitter/X</div>
  <div style="background:#F8F9FB;border:1px solid #E5E8EC;border-radius:8px;padding:16px;font-family:Courier New,monospace;font-size:13px;color:#1A1D20;line-height:1.8;white-space:pre-wrap;">{tweet_text}</div>
  <div style="margin-top:16px;padding:12px;background:#EBF5FF;border-radius:8px;font-size:12px;color:#586068;">
    📧 Newsletter Issue #{issue_number} sent successfully to {len(clean_subscribers)} subscriber(s).<br>
    🌐 Live at: <a href="https://usmrm.net/{filename}">usmrm.net/{filename}</a>
  </div>
</div>
</body></html>"""

print("Sending owner briefing with tweet...")
owner_r = requests.post("https://api.brevo.com/v3/smtp/email",
    headers={"api-key": BREVO_KEY, "content-type": "application/json", "accept": "application/json"},
    json={"sender": {"name": "MRM System", "email": "noreply@usmrm.net"},
          "to": [{"email": "usmrm@proton.me"}],
          "subject": f"📬 MRM Issue #{issue_number} Sent — Saturday Tweet Ready",
          "htmlContent": tweet_html})

if owner_r.status_code in [200, 201, 202]:
    print("✅ Owner briefing sent to usmrm@proton.me!")
else:
    print(f"⚠ Owner briefing error: {owner_r.status_code}")
