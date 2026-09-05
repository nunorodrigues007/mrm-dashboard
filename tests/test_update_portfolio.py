"""
Testes do update_portfolio.py — classificacao de regime pelo medidor B,
vector de pesos de Critical, porta do sub-regime e gatilhos de rebalanceamento.

Sem rede: o yfinance e substituido por um stub antes do import, porque nenhuma das
funcoes aqui testadas o usa (so o fetch_prices, que nao entra nestes testes).
"""
import json, sys, types, tempfile, os
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import update_portfolio as up

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1

# ── classify_regime: Critical e decidido pelo medidor B, nao pelo score ──────────
eq(up.classify_regime(6.97, False, "Turbulence"), "Turbulence", "stress off, score normal")
eq(up.classify_regime(9.5,  False, "Turbulence"), "Turbulence", "score alto sozinho nao faz Critical")
eq(up.classify_regime(6.97, True,  "Turbulence"), "Critical",   "stress on faz Critical")
eq(up.classify_regime(2.0,  True,  "Turbulence"), "Critical",   "stress on ganha ao score baixo")
eq(up.classify_regime(3.5,  False, "Turbulence"), "Resilient",  "score <= 4 com stress off")
eq(up.classify_regime(4.0,  False, "Turbulence"), "Resilient",  "limiar Resilient inclusivo")
eq(up.classify_regime(None, False, "Turbulence"), "Turbulence", "sem score, stress off")

# n/d: ambos os gatilhos sem dados -> manter o estado, nunca assumir calma
eq(up.classify_regime(6.97, None, "Critical"),   "Critical",   "n/d mantem Critical")
eq(up.classify_regime(6.97, None, "Turbulence"), "Turbulence", "n/d mantem Turbulence")
eq(up.classify_regime(6.97, None, None),         "Turbulence", "n/d sem estado anterior")

# ── vector de pesos de Critical ─────────────────────────────────────────────────
for key, w in up.CRITICAL_WEIGHTS.items():
    eq(round(sum(w.values()), 6), 100.0, f"{key} soma 100%")
    eq(sorted(w), sorted(up.BUCKETS), f"{key} cobre os 6 buckets")
    eq(sorted(up.REGIME_ETF_MAP[key]), sorted(up.BUCKETS), f"{key} tem mapa de ETF completo")

alloc, src = up.effective_bucket_alloc("Critical", "Critical_FTQ", {"US_EQUITIES": 60.0})
eq(alloc["US_EQUITIES"], 15.0, "Critical ignora as % da newsletter")
eq("critical override" in src, True, "origem declarada como override")

alloc, src = up.effective_bucket_alloc("Turbulence", None, {"US_EQUITIES": 60.0})
eq(alloc, {"US_EQUITIES": 60.0}, "fora de Critical mandam as % da newsletter")
eq(src, "newsletter", "origem declarada como newsletter")

eq(up.effective_bucket_alloc("Turbulence", None, {})[0], {}, "sem newsletter nao inventa alocacao")

# ── porta do sub-regime ─────────────────────────────────────────────────────────
eq(up.determine_critical_subregime("FTQ", False)[0], "Critical_Stress", "entrada fresca e sempre defensiva")
eq(up.determine_critical_subregime("FTQ", True)[0],  "Critical_FTQ",    "FTQ confirmado reconquista o TLT")
eq(up.determine_critical_subregime("STRESS", True)[0], "Critical_Stress", "sem queda do 10Y fica defensivo")
eq(up.determine_critical_subregime(None, True)[0],   "Critical_Stress", "10Y indisponivel cai no lado defensivo")

eq(up.resolve_etf_map_key("Critical", None), "Critical_Stress", "Critical sem sub-regime -> defensivo")
eq(up.resolve_etf_map_key("Turbulence", None), "Turbulence", "regimes normais mapeiam 1:1")

# ── read_stress_gauge ───────────────────────────────────────────────────────────
eq(up.read_stress_gauge(Path("/nao/existe/data.json"))[0], None, "ficheiro em falta -> n/d")

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "data.json"
    p.write_text(json.dumps({"globalResilienceScore": 6.97}))
    eq(up.read_stress_gauge(p)[0], None, "data.json sem stressGauge -> n/d")

    p.write_text(json.dumps({"stressGauge": {"active": False, "subregime": None, "basis": "no trigger active"}}))
    eq(up.read_stress_gauge(p), (False, None, "no trigger active"), "stressGauge desligado")

    p.write_text(json.dumps({"stressGauge": {"active": True, "subregime": "FTQ", "basis": "Sahm"}}))
    eq(up.read_stress_gauge(p), (True, "FTQ", "Sahm"), "stressGauge ligado")

# ── emergencia: o ramo defensivo por score desapareceu ──────────────────────────
hi = {"history": [{"mrm_score": 9.0}]}
eq(up.check_emergency(hi, 9.5)[0], False, "score alto ja nao dispara emergencia")
lo = {"history": [{"mrm_score": 3.5}]}
eq(up.check_emergency(lo, 3.8), (True, "emergency_resilient_3.8"), "score baixo continua a disparar")
mix = {"history": [{"mrm_score": 5.0}]}
eq(up.check_emergency(mix, 3.8)[0], False, "so uma semana abaixo nao chega")

# ── rebalance_shares: conservacao de valor com o mapa de Critical ───────────────
prices = {"USMV": 90.0, "TLT": 95.0, "SHY": 82.0, "SGOV": 100.5, "GLD": 250.0,
          "BIL": 91.5, "VNQ": 88.0, "SPY": 600.0, "IEF": 95.0, "LQD": 108.0,
          "PDBC": 14.0, "QQQ": 500.0, "HYG": 79.0, "IWO": 280.0}
for key in ("Critical_FTQ", "Critical_Stress", "Turbulence"):
    w = up.CRITICAL_WEIGHTS.get(key) or {b: 100.0 / 6 for b in up.BUCKETS}
    sh = up.rebalance_shares(10000.0, w, key, prices)
    # tolerancia de um cent: os pesos de teste de Turbulence sao 100/6 e nao fecham exacto
    eq(abs(up.calculate_value(sh, prices) - 10000.0) < 0.05, True, f"{key} conserva o valor")

# ── decide_rebalance: matriz de gatilhos ────────────────────────────────────────
D = up.decide_rebalance
eq(D("Critical", "Turbulence", "Critical_Stress", None, False), "stress_on", "entrada em Critical")
eq(D("Turbulence", "Critical", None, "Critical_FTQ", False), "stress_off", "saida de Critical")
eq(D("Turbulence", "Turbulence", None, None, False), None, "sem evento nao rebalanceia")
eq(D("Turbulence", "Turbulence", None, None, True), "semestral_rebalance", "calendario semestral")
eq(D("Resilient", "Turbulence", None, None, False), None, "Resilient sozinho nao rebalanceia")
eq(D("Resilient", "Turbulence", None, None, False, "emergency_resilient_3.8"),
   "emergency_resilient_3.8", "emergencia por score baixo")
eq(D("Critical", "Critical", "Critical_FTQ", "Critical_Stress", False),
   "critical_subregime_switch:Critical_Stress->Critical_FTQ", "troca de sub-regime")
eq(D("Critical", "Critical", "Critical_FTQ", "Critical_FTQ", False), None, "sub-regime igual, nada a fazer")
# precedencia: o evento de stress ganha ao calendario
eq(D("Critical", "Turbulence", "Critical_Stress", None, True), "stress_on", "stress ganha ao semestral")
eq(D("Turbulence", "Critical", None, "Critical_FTQ", True), "stress_off", "saida ganha ao semestral")

# ── ciclo completo: entrar em Critical e sair, com as % a voltarem ao sitio ─────
news = {"US_EQUITIES": 40.0, "US_TREASURIES": 20.0, "IG_CREDIT": 15.0,
        "COMMODITIES": 10.0, "CASH": 10.0, "ALTERNATIVES": 5.0}
a_in,  _ = up.effective_bucket_alloc("Critical", "Critical_Stress", news)
a_out, _ = up.effective_bucket_alloc("Turbulence", None, news)
eq(a_in["US_EQUITIES"],  15.0, "em Critical corta accoes para 15%")
eq(a_out["US_EQUITIES"], 40.0, "a saida devolve as % da newsletter")
eq(a_out, news, "a saida restitui a alocacao macro inteira")

print(f"TODOS OS {ok} TESTES PASSARAM")
