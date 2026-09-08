"""
Backtest final de validacao — 2007-2026.

Diferenca essencial face aos backtests anteriores: a logica nao esta
reimplementada aqui. Este script importa mrm_rules.py do repositorio e chama
classify_regime, subregime_from_gauge, decide_rebalance e effective_bucket_alloc.
O que e testado e o codigo que esta em producao.
"""
import json, math, csv, sys, statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent))          # mrm_rules.py e mrm_gauge_b.py
import mrm_rules as rules

# ── precos mensais e substituicoes declaradas ────────────────────────────────
def load(p):
    px = {}
    for line in open(p):
        t = line.split()
        if len(t) == 3 and len(t[1]) == 2:
            px[f"{t[0]}-{t[1]}"] = float(t[2]); continue
        for i, v in enumerate(t[1:], 1):
            px[f"{t[0]}-{i:02d}"] = float(v)
    return px

PX = {t: load(DATA / "px" / f"{t}.txt") for t in ("SPY", "IEF", "LQD", "DBC", "SHV", "VNQ", "TLT", "GLD")}
for line in open(DATA / "px" / "RESIL2021.txt"):
    t = line.split(); PX.setdefault(t[0], {})
    for i in range(1, len(t), 2):
        PX[t[0]][t[i]] = float(t[i + 1])

# Nem todos os ETF de producao existem em 2007. As substituicoes sao declaradas,
# nao escondidas, e nenhuma delas favorece o sistema novo:
SUBSTITUTIONS = {
    "PDBC": "DBC",   # PDBC cotado desde 2014-02 -> DBC, mesmo cabaz, desde 2006-02
    "BIL":  "SHV",   # BIL desde 2007-05 -> SHV, bilhetes do Tesouro 0-1 ano
    "SGOV": "SHV",   # SGOV desde 2020-05 -> SHV
    "USMV": "SPY",   # USMV (baixa volatilidade) desde 2011-10 -> SPY, o indice.
                     # Penaliza o sistema novo: em Critical fica com beta total.
    "SHY":  "SHV",   # sem serie completa de SHY -> SHV, ainda mais curto
}
sub = lambda t: SUBSTITUTIONS.get(t, t)

# ── scores reconstruidos e series dos gatilhos ───────────────────────────────
SC = json.load(open(DATA / "scores.json"))
S = {r["month"]: r for r in SC}
MONTHS = [r["month"] for r in SC if r["month"] >= "2007-02"]
mm = {r["month"]: float(r["y10"]) for r in csv.DictReader(open(DATA / "monthly.csv"))}

# Serie real-time da regra de Sahm (vintages, nao revista). 2025-10 nao existe na
# serie publicada e e interpolado — assinalado aqui e nao escondido.
sahm = {}
for line in open(DATA / "sahm_realtime.txt"):
    if not line.strip(): continue
    a, b = line.split(); sahm[a] = float(b)
sahm["2025-10"] = round((sahm["2025-09"] + sahm["2025-11"]) / 2, 2)

q = {r["date"]: float(r["dralacbn"]) for r in csv.DictReader(open(DATA / "fred_quarterly.csv")) if r["dralacbn"]}
qk = sorted(q); d4 = {qk[i]: round(q[qk[i]] - q[qk[i - 4]], 2) for i in range(4, len(qk))}

def madd(m, n):
    y, mo = int(m[:4]), int(m[5:7]); mo += n; y += (mo - 1) // 12; mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"

def npl_accel(m, lag=5):
    ok = [d for d in sorted(d4) if madd(d[:7], lag) <= m]
    return d4[ok[-1]] if ok else None

from mrm_gauge_b import SAHM_TRIGGER, NPL_ACCEL_TRIGGER, TENY_FTQ_BP

def gauge_b(m):
    """Os dois gatilhos publicados, com os limiares do modulo de producao."""
    s = sahm.get(madd(m, -1))                      # relatorio do emprego sai em M+1
    n = npl_accel(m)
    if s is None and n is None:
        return None
    return (s is not None and s >= SAHM_TRIGGER) or (n is not None and n >= NPL_ACCEL_TRIGGER)

def gauge_b_subregime(m):
    a, b = mm.get(m), mm.get(madd(m, -3))
    if a is None or b is None:
        return None
    return "FTQ" if (a - b) <= TENY_FTQ_BP else "STRESS"

# ── alocacao macro fora de Critical ──────────────────────────────────────────
# Em producao vem da newsletter semanal, que nao existe antes de Mar 2026. Usa-se
# um vector fixo, igual nos dois sistemas, para que a comparacao isole o efeito
# do medidor B e nao o do julgamento semanal.
_t = {"US_EQUITIES": 40, "US_TREASURIES": 19, "IG_CREDIT": 15,
      "COMMODITIES": 6, "CASH": 14, "ALTERNATIVES": 2.5}
_s = sum(_t.values())
MACRO_ALLOC = {k: v / _s * 100 for k, v in _t.items()}
RESILIENT_ALLOC = {"US_EQUITIES": 55, "US_TREASURIES": 5, "IG_CREDIT": 15,
                   "COMMODITIES": 5, "CASH": 5, "ALTERNATIVES": 15}

def alloc_for(regime, subregime):
    if regime == "Resilient":
        return dict(RESILIENT_ALLOC)
    a, _ = rules.effective_bucket_alloc(regime, subregime, MACRO_ALLOC)
    return a

def value(shares, m):
    return sum(n * PX[t][m] for t, n in shares.items())

def rebalance(v, regime, subregime, m):
    key = rules.resolve_etf_map_key(regime, subregime)
    etfs = rules.REGIME_ETF_MAP[key]
    w = alloc_for(regime, subregime)
    sh = {}
    for bucket, pct in w.items():
        t = sub(etfs[bucket])
        sh[t] = sh.get(t, 0.0) + v * pct / 100 / PX[t][m]   # buckets partilham ticker: somar
    return sh

def is_semestral(m):
    return m[5:7] in ("01", "06")

# ── v2: o sistema tal como esta em producao ─────────────────────────────────
def run_v2():
    m0 = MONTHS[0]
    regime, subregime = "Turbulence", None
    sh = rebalance(10000.0, regime, subregime, m0)
    ser = [(m0, 10000.0)]; log = []; low_streak = 0
    for m in MONTHS[1:]:
        v = value(sh, m)
        score = S[m]["score_frozen"]
        stress = gauge_b(m)
        want = rules.classify_regime(score, stress, regime)

        want_sub = None
        if want == "Critical":
            want_sub, _ = rules.subregime_from_gauge(gauge_b_subregime(m), regime == "Critical")

        low_streak = low_streak + 1 if (score is not None and score <= rules.RESILIENT_MAX) else 0
        emergency = f"emergency_resilient_{score}" if (want == "Resilient" and low_streak >= rules.CONSECUTIVE_WEEKS) else None

        reason = rules.decide_rebalance(want, regime, want_sub, subregime, is_semestral(m), emergency)
        if reason:
            if want != regime or want_sub != subregime:
                log.append((m, f"{regime}{'/' + subregime if subregime else ''} -> "
                               f"{want}{'/' + want_sub if want_sub else ''}  [{reason}]"))
            regime = want
            subregime = want_sub if want == "Critical" else None
            sh = rebalance(v, regime, subregime, m)
        ser.append((m, v))
    return ser, log

# ── v1: o sistema anterior — regime pelo score, confirmacao de 2 meses ──────
def run_v1():
    m0 = MONTHS[0]
    cur = "Turbulence"
    sh = rebalance(10000.0, cur, None, m0)
    ser = [(m0, 10000.0)]; pend = None; streak = 0
    for m in MONTHS[1:]:
        v = value(sh, m)
        want = S[m]["regime_frozen"]
        if want != cur:
            streak = streak + 1 if want == pend else 1; pend = want
        else:
            streak = 0; pend = None
        do = streak >= rules.CONSECUTIVE_WEEKS or is_semestral(m)
        if streak >= rules.CONSECUTIVE_WEEKS:
            cur = want; streak = 0; pend = None
        if do:
            sh = rebalance(v, cur, None if cur != "Critical" else "Critical_Stress", m)
        ser.append((m, v))
    return ser, []

def buy_hold(w):
    sh = {t: 10000.0 * x / PX[t][MONTHS[0]] for t, x in w.items()}
    return [(m, sum(n * PX[t][m] for t, n in sh.items())) for m in MONTHS]

def annual_rebal(w):
    v = 10000.0; sh = {t: v * x / PX[t][MONTHS[0]] for t, x in w.items()}; out = [(MONTHS[0], v)]
    for m in MONTHS[1:]:
        v = sum(n * PX[t][m] for t, n in sh.items()); out.append((m, v))
        if m[5:7] == "01":
            sh = {t: v * x / PX[t][m] for t, x in w.items()}
    return out

shv = [PX["SHV"][MONTHS[i]] / PX["SHV"][MONTHS[i - 1]] - 1 for i in range(1, len(MONTHS))]

def stats(ser):
    v = [x for _, x in ser]; r = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]
    yrs = len(r) / 12
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    vol = st.stdev(r) * math.sqrt(12)
    ex = [r[i] - shv[i] for i in range(len(r))]
    sharpe = (st.mean(ex) * 12) / vol
    dn = [x for x in ex if x < 0]
    sortino = (st.mean(ex) * 12) / (st.stdev(dn) * math.sqrt(12))
    pk = v[0]; mdd = 0; mdd_m = None
    for m, x in ser:
        pk = max(pk, x)
        if x / pk - 1 < mdd: mdd = x / pk - 1; mdd_m = m
    return dict(cagr=cagr, vol=vol, sharpe=sharpe, sortino=sortino, mdd=mdd, mdd_m=mdd_m, final=v[-1])

if __name__ == "__main__":
    v2, log = run_v2()
    v1, _ = run_v1()
    print(f"Periodo: {MONTHS[0]} a {MONTHS[-1]}  ({len(MONTHS)} meses)")
    print(f"Gatilhos: Sahm >= {SAHM_TRIGGER} | delinquencia 4T >= {NPL_ACCEL_TRIGGER} pp | FTQ: 10Y <= {TENY_FTQ_BP} em 3 meses\n")
    print(f"Mudancas de estado no v2 ({len(log)}):")
    for m, t in log: print(f"   {m}  {t}")

    rows = [("v1 — sistema anterior", stats(v1)),
            ("v2 — sistema actual", stats(v2)),
            ("SPY buy & hold", stats(buy_hold({"SPY": 1.0}))),
            ("60/40 SPY-IEF anual", stats(annual_rebal({"SPY": 0.6, "IEF": 0.4})))]
    print(f"\n{'':24}{'CAGR':>8}{'Vol':>8}{'Sharpe':>8}{'Sortino':>9}{'MaxDD':>9}{'(mes)':>9}{'Final':>10}")
    for n, s in rows:
        print(f"{n:24}{s['cagr']*100:7.2f}%{s['vol']*100:7.2f}%{s['sharpe']:8.3f}{s['sortino']:9.3f}"
              f"{s['mdd']*100:8.1f}%{str(s['mdd_m']):>9}{s['final']:10,.0f}")

    d1, d2 = dict(v1), dict(v2); dspy = dict(buy_hold({"SPY": 1.0}))
    print(f"\n{'ano':>6}{'v1':>9}{'v2':>9}{'SPY':>9}   medidor B")
    prev = MONTHS[0]
    for y in range(2007, 2027):
        ms = [m for m in MONTHS if m[:4] == str(y)]
        if not ms: continue
        f = lambda d: d[ms[-1]] / d[prev] - 1
        nb = sum(1 for m in ms if gauge_b(m))
        print(f"{y:>6}{f(d1)*100:8.1f}%{f(d2)*100:8.1f}%{f(dspy)*100:8.1f}%   {'—' if nb == 0 else f'ON {nb}/{len(ms)} meses'}")
        prev = ms[-1]
