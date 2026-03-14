#!/usr/bin/env python3
"""
MRM Weekly Newsletter
Reads data.json → calls Claude API → generates HTML → sends via Brevo
"""

import json
import os
import requests
from datetime import datetime

# ── KEYS ──
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
BREVO_KEY     = os.environ["BREVO_API_KEY"]

# ── LOAD DATA ──
with open("data.json", "r") as f:
    data = json.load(f)

today = datetime.utcnow().strftime("%d %B %Y")

# ── BUILD PROMPT ──
prompt = f"""You are a Senior Risk Strategist and CIO. Generate a complete MRM Weekly Institutional Newsletter in HTML format based on the following live data from the US Macro-Resilience Matrix dashboard.

TODAY'S DATE: {today}

LIVE DATA:
- Global Resilience Score: {data.get('global_score', 'N/A')} / 10
- Regime: {data.get('regime', 'N/A')}
- Pillar I — Cycle (10Y-2Y Spread): {data.get('cycle_value', 'N/A')} | Score: {data.get('cycle_score', 'N/A')}/10
- Pillar II — Liquidity (MC/M2 Ratio): {data.get('liquidity_value', 'N/A')} | Score: {data.get('liquidity_score', 'N/A')}/10
- Pillar II — Premium (ERP): {data.get('premium_value', 'N/A')} | Score: {data.get('premium_score', 'N/A')}/10
- Pillar III — Solvency (Bank NPL): {data.get('solvency_value', 'N/A')} | Score: {data.get('solvency_score', 'N/A')}/10
- Pillar III — Debt (Household DSR): {data.get('debt_value', 'N/A')} | Score: {data.get('debt_score', 'N/A')}/10
- Sentinel — Initial Jobless Claims (ICSA): {data.get('icsa_value', 'N/A')}
- Sentinel — ERP Alert: {data.get('erp_alert', 'N/A')}

STRICT INSTRUCTIONS:
1. Tone: Institutional, dry, objective, risk-focused. No emotional adjectives.
2. Zero Asset Bias: NEVER mention individual tickers (ARCC, SCHD, etc.). Use only Sectors, Factors, or Asset Classes.
3. Language: English only.
4. Output: Return ONLY the complete HTML — no markdown, no backticks, no explanation.

Generate the newsletter using EXACTLY this HTML structure and styling:

<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MRM Weekly Audit — {today}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#F2F4F7; font-family:Arial, sans-serif; font-size:14px; color:#1A1D20; }}
  a {{ color:#388BFD; text-decoration:none; }}
  .wrapper {{ max-width:640px; margin:32px auto; background:#FFFFFF; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.08); }}
  .header {{ background:#0D1117; padding:0; }}
  .header-top {{ display:flex; align-items:center; justify-content:space-between; padding:14px 28px; border-bottom:1px solid #21262D; }}
  .logo {{ font-size:15px; font-weight:800; color:#E8ECF0; letter-spacing:0.08em; text-transform:uppercase; }}
  .logo span {{ color:#388BFD; }}
  .header-meta {{ font-family:'Courier New', monospace; font-size:9px; color:#586068; letter-spacing:0.12em; text-transform:uppercase; text-align:right; line-height:1.6; }}
  .header-subject {{ padding:18px 28px 20px; }}
  .header-subject .tag {{ font-family:'Courier New', monospace; font-size:9px; color:#388BFD; letter-spacing:0.16em; text-transform:uppercase; margin-bottom:6px; }}
  .header-subject h1 {{ font-size:22px; font-weight:800; color:#E8ECF0; line-height:1.2; }}
  .score-band {{ background:#161B22; padding:20px 28px; display:flex; align-items:center; gap:24px; border-bottom:3px solid #F98C4F; }}
  .score-circle {{ width:72px; height:72px; border-radius:50%; border:3px solid #F98C4F; display:flex; align-items:center; justify-content:center; flex-shrink:0; }}
  .score-num {{ font-family:'Courier New', monospace; font-size:26px; font-weight:700; color:#F98C4F; line-height:1; }}
  .score-info .regime {{ font-family:'Courier New', monospace; font-size:9px; color:#F98C4F; letter-spacing:0.16em; text-transform:uppercase; margin-bottom:4px; }}
  .score-info .score-label {{ font-size:16px; font-weight:700; color:#E8ECF0; margin-bottom:4px; }}
  .score-info .score-sub {{ font-family:'Courier New', monospace; font-size:10px; color:#586068; }}
  .content {{ padding:28px; }}
  .section-label {{ font-family:'Courier New', monospace; font-size:9px; font-weight:600; letter-spacing:0.16em; text-transform:uppercase; color:#8B949E; margin-bottom:10px; padding-bottom:6px; border-bottom:1px solid #E5E8EC; }}
  .summary p {{ font-size:13px; color:#2D3139; line-height:1.75; margin-bottom:10px; }}
  .pillars-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  .pillars-table thead tr {{ background:#1A1D20; }}
  .pillars-table thead th {{ padding:8px 10px; text-align:left; font-family:'Courier New', monospace; font-size:8px; letter-spacing:0.12em; text-transform:uppercase; color:#8B949E; border-bottom:2px solid #388BFD; }}
  .pillars-table tbody tr:nth-child(odd) {{ background:#F8F9FB; }}
  .pillars-table td {{ padding:9px 10px; border-bottom:1px solid #E5E8EC; vertical-align:middle; }}
  .status-pill {{ display:inline-block; padding:2px 7px; border-radius:4px; font-family:'Courier New', monospace; font-size:8px; font-weight:700; letter-spacing:0.1em; text-transform:uppercase; }}
  .status-critical {{ background:#FFEBEE; color:#D73A49; }}
  .status-elevated {{ background:#FFF3E0; color:#F98C4F; }}
  .status-caution {{ background:#FFF8E1; color:#E6A817; }}
  .status-stable {{ background:#E8F5E9; color:#2E7D32; }}
  .deep-dive {{ background:#FFF8E1; border-left:3px solid #F98C4F; padding:14px 16px; border-radius:0 6px 6px 0; }}
  .deep-dive p {{ font-size:12.5px; color:#2D3139; line-height:1.7; margin-bottom:8px; }}
  .page-divider {{ margin:28px 0; border:none; border-top:2px dashed #E5E8EC; }}
  .page-label {{ text-align:center; font-family:'Courier New', monospace; font-size:9px; color:#8B949E; letter-spacing:0.14em; text-transform:uppercase; margin:12px 0 20px; }}
  .ews-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  .ews-table thead tr {{ background:#1A1D20; }}
  .ews-table thead th {{ padding:8px 10px; text-align:left; font-family:'Courier New', monospace; font-size:8px; letter-spacing:0.12em; text-transform:uppercase; color:#8B949E; border-bottom:2px solid #388BFD; }}
  .ews-table tbody tr:nth-child(odd) {{ background:#F8F9FB; }}
  .ews-table td {{ padding:9px 10px; border-bottom:1px solid #E5E8EC; vertical-align:middle; }}
  .sector-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  .sector-col-ow {{ background:#F1FAF3; border:1px solid #C8E6C9; border-radius:8px; overflow:hidden; }}
  .sector-col-uw {{ background:#FFF5F5; border:1px solid #FFCDD2; border-radius:8px; overflow:hidden; }}
  .sector-col-header {{ padding:8px 12px; font-family:'Courier New', monospace; font-size:8px; letter-spacing:0.12em; text-transform:uppercase; font-weight:700; }}
  .sector-col-ow .sector-col-header {{ background:#E8F5E9; color:#2E7D32; }}
  .sector-col-uw .sector-col-header {{ background:#FFEBEE; color:#C62828; }}
  .sector-item {{ padding:7px 12px; border-bottom:1px solid rgba(0,0,0,0.05); }}
  .sector-item:last-child {{ border-bottom:none; }}
  .sector-name {{ font-size:11.5px; font-weight:600; margin-bottom:1px; }}
  .sector-col-ow .sector-name {{ color:#1B5E20; }}
  .sector-col-uw .sector-name {{ color:#B71C1C; }}
  .sector-rationale {{ font-size:10px; color:#586068; line-height:1.4; }}
  .alloc-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  .alloc-table thead tr {{ background:#1A1D20; }}
  .alloc-table thead th {{ padding:8px 10px; text-align:left; font-family:'Courier New', monospace; font-size:8px; letter-spacing:0.12em; text-transform:uppercase; color:#8B949E; border-bottom:2px solid #388BFD; }}
  .alloc-table tbody tr:nth-child(odd) {{ background:#F8F9FB; }}
  .alloc-table td {{ padding:9px 10px; border-bottom:1px solid #E5E8EC; vertical-align:middle; }}
  .alloc-pct {{ font-family:'Courier New', monospace; font-size:14px; font-weight:700; color:#388BFD; }}
  .verdict-box {{ background:#FFF8E1; border:2px solid #F98C4F; border-radius:8px; padding:16px 18px; }}
  .verdict-box p {{ font-size:13px; color:#2D3139; line-height:1.75; }}
  .footer {{ background:#0D1117; padding:20px 28px; text-align:center; }}
  .footer-logo {{ font-size:13px; font-weight:800; color:#8B949E; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:8px; }}
  .footer-logo span {{ color:#388BFD; }}
  .footer-links {{ margin-bottom:10px; }}
  .footer-links a {{ font-family:'Courier New', monospace; font-size:10px; color:#586068; margin:0 8px; }}
  .footer-disclaimer {{ font-family:'Courier New', monospace; font-size:9px; color:#3D4450; line-height:1.6; max-width:480px; margin:0 auto; }}
  .spacer {{ height:20px; }}
</style>
</head>
<body>
<div class="wrapper">
  <!-- HEADER -->
  <div class="header">
    <div class="header-top">
      <div class="logo">US<span>MRM</span></div>
      <div class="header-meta">MRM WEEKLY AUDIT<br>{today} · ISSUE #{{ISSUE_NUMBER}}</div>
    </div>
    <div class="header-subject">
      <div class="tag">Subject: Regime Diagnosis &amp; Tactical Execution</div>
      <h1>US Macro-Resilience Matrix<br>Weekly Institutional Memo</h1>
    </div>
  </div>
  <!-- SCORE BAND -->
  <div class="score-band">
    <div class="score-circle"><div class="score-num">{{SCORE}}</div></div>
    <div class="score-info">
      <div class="regime">● {{REGIME}} REGIME</div>
      <div class="score-label">Global Resilience Score</div>
      <div class="score-sub">Updated: {today} · FRED API Live · 5/5 Pillars Active</div>
    </div>
  </div>
  <!-- PAGE 1 CONTENT -->
  <div class="content">
    <!-- Executive Summary (3 paragraphs) -->
    <!-- 5 Pillars Matrix table -->
    <!-- Pillar Deep Dive (most stressed pillar) -->
  </div>
  <hr class="page-divider">
  <div class="page-label">— Tactical Execution —</div>
  <!-- PAGE 2 CONTENT -->
  <div class="content" style="padding-top:0;">
    <!-- EWS table -->
    <!-- Sector Matrix grid -->
    <!-- Clean Sheet Allocation table -->
    <!-- Final Risk Verdict box -->
  </div>
  <!-- FOOTER -->
  <div class="footer">
    <div class="footer-logo">US<span>MRM</span> Intelligence Hub</div>
    <div class="footer-links">
      <a href="https://nunorodrigues007.github.io/mrm-dashboard/">Live Terminal</a>
      <a href="https://nunorodrigues007.github.io/mrm-dashboard/">Academy</a>
      <a href="https://nunorodrigues007.github.io/mrm-dashboard/">BDCs</a>
      <a href="mailto:usmrm@proton.me">Contact</a>
    </div>
    <div class="footer-disclaimer">
      This newsletter is produced for educational and personal analysis purposes only.
      It does not constitute financial advice, investment recommendation, or solicitation of any kind.
      All data sourced from FRED API (Federal Reserve Bank of St. Louis).<br>
      © 2026 US MRM Intelligence Hub · usmrm@proton.me · To unsubscribe, reply with "unsubscribe".
    </div>
  </div>
</div>
</body>
</html>

Fill in ALL sections completely with real analytical content based on the live data provided. Return ONLY the complete HTML.
"""

# ── CALL CLAUDE API ──
print("Calling Claude API...")
response = requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    },
    json={
        "model": "claude-opus-4-5",
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}]
    }
)

if response.status_code != 200:
    print(f"Claude API error: {response.status_code} {response.text}")
    exit(1)

result = response.json()
html_content = result["content"][0]["text"].strip()

# Strip markdown backticks if present
if html_content.startswith("```"):
    html_content = html_content.split("```html")[-1].split("```")[0].strip()

print(f"Newsletter HTML generated ({len(html_content)} chars)")

# ── GET SUBSCRIBER LIST FROM BREVO ──
print("Fetching subscribers from Brevo...")
subs_response = requests.get(
    "https://api.brevo.com/v3/contacts",
    headers={
        "api-key": BREVO_KEY,
        "accept": "application/json",
    },
    params={"limit": 500, "offset": 0}
)

subscribers = []
if subs_response.status_code == 200:
    contacts = subs_response.json().get("contacts", [])
    subscribers = [c["email"] for c in contacts if not c.get("emailBlacklisted", False)]
    print(f"Found {len(subscribers)} active subscribers")
else:
    print(f"Brevo contacts error: {subs_response.status_code}")
    # Always send to owner even if no subscribers
    subscribers = []

# Always include owner
owner_email = "usmrm@proton.me"
if owner_email not in subscribers:
    subscribers.insert(0, owner_email)

# ── SEND VIA BREVO ──
print(f"Sending newsletter to {len(subscribers)} recipients...")

score = data.get('global_score', '?')
regime = data.get('regime', 'UNKNOWN')

send_payload = {
    "sender": {"name": "US MRM Intelligence Hub", "email": "usmrm@proton.me"},
    "to": [{"email": email} for email in subscribers],
    "subject": f"MRM Weekly Audit — {today} | Score {score}/10 · {regime}",
    "htmlContent": html_content,
}

send_response = requests.post(
    "https://api.brevo.com/v3/smtp/email",
    headers={
        "api-key": BREVO_KEY,
        "content-type": "application/json",
        "accept": "application/json",
    },
    json=send_payload
)

if send_response.status_code in [200, 201, 202]:
    print(f"✅ Newsletter sent successfully to {len(subscribers)} recipients!")
    print(f"   Subject: MRM Weekly Audit — {today} | Score {score}/10 · {regime}")
else:
    print(f"❌ Brevo send error: {send_response.status_code}")
    print(send_response.text)
    exit(1)
