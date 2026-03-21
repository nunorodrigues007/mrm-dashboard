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
start_date = date(2026, 3, 14)
issue_number = ((date.today() - start_date).days // 7) + 1
print(f"Generating Issue #{issue_number} — {today}")

with open("data.json", "r") as f:
    data = json.load(f)

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

prompt = f"""You are a Senior Risk Strategist and CIO. Generate a complete MRM Weekly Institutional Newsletter in HTML.

TODAY: {today} | ISSUE: #{issue_number}

DATA:
- Global Score: {score}/10 ({wow_score}) | Regime: {regime}
- Cycle: {cycle.get('value','N/A')} | Score: {cycle.get('score','N/A')}/10 | WoW: {wow('cycle', cycle.get('score',0))} | {cycle.get('status','N/A')}
- Liquidity: {liquidity.get('value','N/A')} | Score: {liquidity.get('score','N/A')}/10 | WoW: {wow('liquidity', liquidity.get('score',0))} | {liquidity.get('status','N/A')}
- Premium (ERP): {premium.get('value','N/A')} | Score: {premium.get('score','N/A')}/10 | WoW: {wow('premium', premium.get('score',0))} | {premium.get('status','N/A')}
- Solvency: {solvency.get('value','N/A')} | Score: {solvency.get('score','N/A')}/10 | WoW: {wow('solvency', solvency.get('score',0))} | {solvency.get('status','N/A')}
- Debt: {debt.get('value','N/A')} | Score: {debt.get('score','N/A')}/10 | WoW: {wow('debt', debt.get('score',0))} | {debt.get('status','N/A')}
- ICSA: {icsa.get('value','N/A')} Alert:{icsa.get('alert',False)} | ERP Sentinel: {erp.get('value','N/A')} Alert:{erp.get('alert',False)}

RULES:
1. Institutional tone. Dry. Objective. Risk-focused.
2. NEVER mention individual tickers. Only Sectors/Factors/Asset Classes.
3. English only.
4. Include WoW column in all tables.
5. Deep Dive on biggest WoW mover.
6. Return ONLY complete HTML. No markdown. No backticks.

HTML STRUCTURE (fill ALL placeholders with real content):
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
.score-band{{background:#161B22;padding:20px 28px;display:flex;align-items:center;gap:24px;border-bottom:3px solid #F98C4F;}}
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
<div class="score-band"><div class="score-circle"><div class="score-num">SCORE_HERE</div></div><div class="score-info"><div class="regime">● REGIME_HERE REGIME</div><div class="score-label">Global Resilience Score</div><div class="score-sub">Updated: {today} · FRED API Live · 5/5 Pillars Active · WOW_HERE</div></div></div>
<div class="content">EXECUTIVE_SUMMARY_HERE PILLARS_TABLE_HERE DEEP_DIVE_HERE</div>
<hr class="page-divider"><div class="page-label">— Tactical Execution —</div>
<div class="content" style="padding-top:0;">EWS_TABLE_HERE SECTOR_MATRIX_HERE ALLOCATION_TABLE_HERE VERDICT_BOX_HERE</div>
<div class="footer"><div class="footer-logo">US<span>MRM</span> Intelligence Hub</div><div class="footer-links"><a href="https://usmrm.net">Live Terminal</a><a href="https://usmrm.net">Newsletter</a><a href="https://usmrm.net">BDCs</a><a href="mailto:usmrm@proton.me">Contact</a></div><div class="footer-disclaimer">This newsletter is produced for educational and personal analysis purposes only. It does not constitute financial advice.<br>All data sourced from FRED API · © 2026 US MRM Intelligence Hub · usmrm.net</div></div>
</div></body></html>

Replace ALL_CAPS placeholders with complete real HTML content based on the data above. Return ONLY the final HTML."""

print("Calling Claude API...")
response = requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    json={"model": "claude-opus-4-5", "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]}
)
if response.status_code != 200:
    print(f"Claude API error: {response.status_code}"); exit(1)

html_content = response.json()["content"][0]["text"].strip()
if html_content.startswith("```"):
    html_content = html_content.split("```html")[-1].split("```")[0].strip()
print(f"HTML generated ({len(html_content)} chars)")

filename = f"MRM_Newsletter_Issue{issue_number}_{today_file}.html"
with open(filename, "w", encoding="utf-8") as f:
    f.write(html_content)
print(f"Saved: {filename}")

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
            <div style="padding:2px 8px;border-radius:4px;background:var(--orange-dim);border:1px solid rgba(249,140,79,0.3);font-family:var(--mono);font-size:9px;font-weight:600;color:var(--orange);">{regime.upper()}</div>
          </div>
          <div style="display:flex;align-items:center;gap:20px;">
            <div style="text-align:right;"><div style="font-family:var(--mono);font-size:9px;color:var(--text-muted);text-transform:uppercase;">Score</div><div style="font-family:var(--mono);font-size:20px;font-weight:600;color:{score_color};">{score}</div></div>
            <div style="font-family:var(--mono);font-size:11px;color:{score_color};">{wow_score}</div>
          </div>
        </div>
        <div style="padding:16px 24px;display:grid;grid-template-columns:repeat(5,1fr);gap:12px;">
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Cycle</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{cycle.get('score','?')}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Liquidity</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{liquidity.get('score','?')}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Premium</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{premium.get('score','?')}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Solvency</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--green);">{solvency.get('score','?')}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Debt</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{debt.get('score','?')}</div></div>
        </div>
        <div style="padding:0 24px 20px;display:flex;gap:12px;">
          <a href="/{filename}" target="_blank" style="padding:8px 16px;background:var(--blue-dim);border:1px solid rgba(56,139,253,0.3);border-radius:6px;color:var(--blue);font-size:12px;font-weight:500;text-decoration:none;">Read Full Issue →</a>
          <a href="https://twitter.com/intent/tweet?text=🧊+MRM+Weekly+Signal+Issue+%23{issue_number}+%7C+Score+{score}%2F10+%7C+{regime}%0Ausmrm.net%0A%23MacroInvesting+%23ERP+%23Finance" target="_blank" style="padding:8px 16px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;color:var(--text-secondary);font-size:12px;text-decoration:none;">𝕏 Share</a>
        </div>
      </div>"""

marker = "<!-- ISSUE #2 -->"
if marker in index:
    index = index.replace(marker, new_card + "\n\n      " + marker)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(index)
    print("index.html updated!")
else:
    print("Warning: archive marker not found in index.html")

# Git commit & push
subprocess.run(["git", "config", "user.email", "action@github.com"], check=True)
subprocess.run(["git", "config", "user.name", "MRM Newsletter Bot"], check=True)
subprocess.run(["git", "add", filename, "index.html", "data_prev.json"], check=True)
r = subprocess.run(["git", "diff", "--staged", "--quiet"])
if r.returncode != 0:
    subprocess.run(["git", "commit", "-m", f"Auto: Newsletter Issue #{issue_number} — {today}"], check=True)
    subprocess.run(["git", "push"], check=True)
    print("✅ Pushed to GitHub")

# Send via Brevo
subs_r = requests.get("https://api.brevo.com/v3/contacts",
    headers={"api-key": BREVO_KEY, "accept": "application/json"},
    params={"limit": 500, "offset": 0})
subscribers = []
if subs_r.status_code == 200:
    subscribers = [c["email"] for c in subs_r.json().get("contacts", []) if not c.get("emailBlacklisted", False)]
if "usmrm@proton.me" not in subscribers:
    subscribers.insert(0, "usmrm@proton.me")

# ── BUILD TWEET TEXT ──
icsa_val = icsa.get('value', 'N/A')
erp_val = premium.get('value', 'N/A')
icsa_alert = "🔴" if icsa.get('alert', False) else "✅"
erp_alert = "🔴 Red Alert Active" if erp.get('alert', False) else "✅"

tweet_text = f"""🧊 MRM Weekly Signal — Issue #{issue_number}

Global Resilience Score: {score}/10 | {regime}
ERP: {erp_val} — {erp_alert}
ICSA: {icsa_val} {icsa_alert}

Full institutional memo 👇
usmrm.net/{filename}

#MacroInvesting #ERP #FedWatch #Finance #WeekendReading"""

tweet_block = f"""
<div style="margin:0;padding:24px 28px;background:#F8F9FB;border-top:2px dashed #E5E8EC;">
  <div style="font-family:'Courier New',monospace;font-size:9px;font-weight:600;letter-spacing:0.16em;text-transform:uppercase;color:#8B949E;margin-bottom:12px;">📱 SATURDAY TWEET — Copy & Paste</div>
  <div style="background:#fff;border:1px solid #E5E8EC;border-radius:8px;padding:16px;font-family:'Courier New',monospace;font-size:12px;color:#1A1D20;line-height:1.8;white-space:pre-wrap;">{tweet_text}</div>
  <div style="margin-top:10px;font-family:'Courier New',monospace;font-size:10px;color:#8B949E;">Post on Saturday morning for maximum reach. Tag @usmrm if you have a Twitter account set up.</div>
</div>
"""

# Append tweet block before closing </div></body></html>
full_html = html_content.replace('</div></body></html>', tweet_block + '</div></body></html>')

print(f"Sending to {len(subscribers)} recipients...")
send_r = requests.post("https://api.brevo.com/v3/smtp/email",
    headers={"api-key": BREVO_KEY, "content-type": "application/json", "accept": "application/json"},
    json={"sender": {"name": "US MRM Intelligence Hub", "email": "noreply@usmrm.net"},
          "to": [{"email": e} for e in subscribers],
          "subject": f"MRM Weekly Signal — Issue #{issue_number} | {today} | Score {score}/10 · {regime}",
          "htmlContent": full_html})

if send_r.status_code in [200, 201, 202]:
    print(f"✅ Issue #{issue_number} sent to {len(subscribers)} recipients!")
else:
    print(f"❌ Brevo error: {send_r.status_code} — {send_r.text}"); exit(1)
