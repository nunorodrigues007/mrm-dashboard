"""
Teste de ponta a ponta, sem rede.

Corre a cadeia completa — fetch_data → update_portfolio → send_newsletter — em
dois mundos, um calmo e um sob stress, e verifica a propriedade que faltava ao
sistema: as três superfícies contam a mesma história.

Antes desta série de correcções, no mundo sob stress a carteira rodava para
Critical e a newsletter dizia aos subscritores "No structural regime change
detected. Holding current positions." Este teste falha se isso voltar a
acontecer.
"""
import importlib.util, json, os, shutil, sys, tempfile, types, logging
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
os.environ.setdefault("FRED_API_KEY", "test-key-not-used")

import fetch_data, mrm_gauge_b, mrm_rules as rules
import update_portfolio as up
import test_build_data as T

spec = importlib.util.spec_from_file_location("sn", ROOT / "send_newsletter.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(cond, what): eq(bool(cond), True, what)

PRICES = {"SPY": 600.0, "IEF": 95.0, "LQD": 108.0, "PDBC": 14.0, "BIL": 91.5, "VNQ": 88.0,
          "USMV": 90.0, "TLT": 95.0, "SHY": 82.0, "SGOV": 100.5, "GLD": 250.0,
          "QQQ": 500.0, "HYG": 79.0, "IWO": 280.0}


def run_world(stress):
    """Corre a cadeia inteira num directório temporário e devolve os três
    artefactos: data.json, portfolio.json e o contexto da newsletter."""
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "score_history.json", tmp)
    shutil.copy(ROOT / "portfolio.json", tmp)
    newsletter = sorted(ROOT.glob("MRM_Newsletter_Issue26*.html"))[0]
    shutil.copy(newsletter, tmp)

    obs = {k: list(v) for k, v in T.OBS.items()}
    if stress:
        # Sahm em 0.62: acima do gatilho publicado de 0.50
        obs["SAHMREALTIME"] = [{"date": "2026-08-01", "value": "0.62"},
                               {"date": "2026-07-01", "value": "0.55"}]
        # 10Y a cair 32 bp em 3 meses -> confirma flight-to-quality
        obs["DGS10"] = [{"date": "2026-09-02", "value": "4.15"}] * 40 + \
                       [{"date": "2026-06-02", "value": "4.47"}] * 40

    class FakeResp:
        def __init__(self, sid): self.sid = sid
        def raise_for_status(self): pass
        def json(self): return {"observations": list(obs.get(self.sid, []))}

    fetch_data.fetch_fred = lambda sid, limit=12, retries=3, backoff=5: obs.get(sid, [])[:limit]
    fetch_data.fetch_liquidity_percentile = T.fake_liquidity
    mrm_gauge_b.requests.get = lambda url, params=None, timeout=None: FakeResp((params or {}).get("series_id"))

    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        fetch_data.__file__ = str(tmp / "fetch_data.py")
        data = fetch_data.build_data()               # escreve data.json em tmp

        up.fetch_prices = lambda tickers, d, retries=3: (
            {t: PRICES[t] for t in tickers}, {t: str(d) for t in tickers}, {t: False for t in tickers})
        up.get_last_friday = lambda: date(2026, 9, 11)
        up.adjust_for_market_holiday = lambda d: d
        up.FORCE_REBALANCE = True
        try:
            up.main()
        except SystemExit:
            pass
        portfolio = json.loads((tmp / "portfolio.json").read_text())
        data = json.loads((tmp / "data.json").read_text())
        ctx = sn.build_context(data, portfolio, {}, date(2026, 9, 11), 27)
        prompt = sn.build_prompt(ctx)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    return data, portfolio, ctx, prompt


logging.disable(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
# Mundo calmo
# ─────────────────────────────────────────────────────────────────────────────
data, pf, ctx, prompt = run_world(stress=False)
cur = pf["current"]

eq(data["stressGauge"]["active"], False, "calmo: medidor B desligado")
eq(cur["regime"], "Turbulence", "calmo: carteira em Turbulence")
eq(cur["critical_subregime"], None, "calmo: sem sub-regime")
eq(pf["history"][-1]["rebalance_reason"], "hold", "calmo: sem rebalanceamento")
eq(ctx["regime_label"], "Turbulence", "calmo: newsletter diz Turbulence")
eq(ctx["port_etfs"], "SPY | IEF | LQD | PDBC | BIL | VNQ", "calmo: instrumentos coerentes")
true("Operative regime: Turbulence" in prompt, "calmo: o prompt leva o regime certo")
true("State: OFF" in prompt, "calmo: o prompt declara o medidor desligado")

# Sem gatilho nao ha rebalanceamento: a alocacao efectiva e a que foi executada
# da ultima vez (o semestral de Junho), e a da newsletter desta semana fica
# guardada a espera do proximo rebalanceamento. Sao diferentes de propriedade.
true(cur["bucket_allocation_pct"] not in rules.CRITICAL_WEIGHTS.values(),
     "calmo: a alocacao efectiva nao e o vector de Critical")
true(cur["newsletter_bucket_allocation_pct"],
     "calmo: a alocacao da newsletter fica guardada mesmo sem rebalanceamento")
eq(round(sum(cur["newsletter_bucket_allocation_pct"].values())), 100,
   "calmo: a alocacao guardada soma 100%")
eq(ctx["alloc_line"], " | ".join(f"{b}: {cur['bucket_allocation_pct'].get(b, 0):.0f}%" for b in rules.BUCKETS),
   "calmo: a newsletter mostra a alocacao efectivamente detida")

# ─────────────────────────────────────────────────────────────────────────────
# Mundo sob stress
# ─────────────────────────────────────────────────────────────────────────────
data2, pf2, ctx2, prompt2 = run_world(stress=True)
cur2 = pf2["current"]
hist2 = pf2["history"][-1]

eq(data2["stressGauge"]["active"], True, "stress: medidor B ligado")
eq(data2["stressGauge"]["triggers"]["sahmRealtime"]["fired"], True, "stress: gatilho de Sahm disparou")
eq(cur2["regime"], "Critical", "stress: carteira em Critical")
eq(cur2["critical_subregime"], "Critical_Stress",
   "stress: entrada fresca cai no lado defensivo mesmo com o 10Y a cair")
eq(hist2["rebalance_reason"], "stress_on", "stress: motivo do rebalanceamento")
eq(hist2["rebalance_triggered"], True, "stress: houve transaccoes")

# o score nao mudou de banda — quem mudou o regime foi o medidor B
eq(data2["status"], "Turbulence", "stress: o score continua a dizer Turbulence")
true(data2["globalResilienceScore"] < rules.CRITICAL_MIN,
     "stress: o score nem se aproxima do limiar Critical")

# a carteira usa o mapa e os pesos canonicos de Critical_Stress
eq(cur2["active_etf_map"], rules.REGIME_ETF_MAP["Critical_Stress"], "stress: mapa canonico")
eq(cur2["bucket_allocation_pct"], rules.CRITICAL_WEIGHTS["Critical_Stress"],
   "stress: pesos de Critical sobrepoem-se as % da newsletter")
true(cur2["newsletter_bucket_allocation_pct"] != cur2["bucket_allocation_pct"],
     "stress: a alocacao da newsletter fica guardada para a saida")

# ── a propriedade central: a newsletter conta a mesma historia ───────────────
eq(ctx2["regime_label"], "Critical · No Relief", "stress: newsletter diz Critical · No Relief")
eq(ctx2["rb_alert"], "STRESS_ON", "stress: newsletter reporta a entrada")
eq(ctx2["port_etfs"], " | ".join(rules.REGIME_ETF_MAP["Critical_Stress"][b] for b in rules.BUCKETS),
   "stress: newsletter lista os instrumentos que a carteira comprou")
true("Operative regime: Critical · No Relief" in prompt2, "stress: o prompt leva o regime real")
true("State: ON" in prompt2, "stress: o prompt declara o medidor ligado")
true("Executed: yes" in prompt2, "stress: o prompt declara a execucao")
true("No structural regime change detected" not in prompt2,
     "stress: a frase que contradizia a carteira desapareceu")
true("US_EQUITIES: 15%" in ctx2["alloc_line"], "stress: newsletter mostra o corte para 15% em accoes")

# ── as regras publicadas sao as mesmas em todo o lado ────────────────────────
eq(data["rules"]["etfMap"], rules.as_dict()["etfMap"], "data.json publica o mapa canonico")
eq(data["rules"]["criticalWeights"]["Critical_Stress"], rules.CRITICAL_WEIGHTS["Critical_Stress"],
   "data.json publica os pesos canonicos")
eq(data["rules"]["resilientMax"], rules.RESILIENT_MAX, "data.json publica o limiar Resilient")
eq(data["rules"]["criticalMin"], rules.CRITICAL_MIN, "data.json publica o limiar Critical")

logging.disable(logging.NOTSET)
print(f"TODOS OS {ok} TESTES PASSARAM")
