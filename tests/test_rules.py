"""
Testes do mrm_rules.py — as regras canónicas — e da sua unicidade.

O ponto destes testes não é só verificar cada função: é garantir que mais nenhum
módulo redefine as regras. Os testes de identidade abaixo falham se alguém voltar
a escrever uma segunda cópia do mapa de ETF ou do vector de pesos.

Sem rede: o yfinance é substituído por um stub antes do import.
"""
import sys, types, json
from datetime import date
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mrm_rules as rules
import update_portfolio as up

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1

def true(cond, what):
    eq(bool(cond), True, what)

# ── Unicidade: os outros módulos apontam para estes objectos, não para cópias ──
true(up.REGIME_ETF_MAP is rules.REGIME_ETF_MAP, "update_portfolio usa o mapa canonico")
true(up.CRITICAL_WEIGHTS is rules.CRITICAL_WEIGHTS, "update_portfolio usa os pesos canonicos")
true(up.BUCKETS is rules.BUCKETS, "update_portfolio usa os buckets canonicos")
true(up.classify_regime is rules.classify_regime, "classify_regime nao esta duplicado")
true(up.decide_rebalance is rules.decide_rebalance, "decide_rebalance nao esta duplicado")
true(up.effective_bucket_alloc is rules.effective_bucket_alloc, "effective_bucket_alloc nao esta duplicado")

# ── Coerência interna do mapa e dos pesos ─────────────────────────────────────
for key, etfs in rules.REGIME_ETF_MAP.items():
    eq(sorted(etfs), sorted(rules.BUCKETS), f"{key} cobre os 6 buckets")
for key, w in rules.CRITICAL_WEIGHTS.items():
    eq(round(sum(w.values()), 6), 100.0, f"{key} soma 100%")
    eq(sorted(w), sorted(rules.BUCKETS), f"{key} pesa os 6 buckets")
    true(key in rules.REGIME_ETF_MAP, f"{key} tem mapa de ETF")
eq(sorted(rules.ALLOCATION_BANDS), sorted(rules.BUCKETS), "ha uma banda por bucket")
for b, (lo, hi) in rules.ALLOCATION_BANDS.items():
    true(0 <= lo < hi <= 100, f"banda de {b} e um intervalo valido")

# ── classify_regime ───────────────────────────────────────────────────────────
eq(rules.classify_regime(9.5, False, "Turbulence"), "Turbulence", "score alto sozinho nao faz Critical")
eq(rules.classify_regime(6.97, True, "Turbulence"), "Critical", "medidor B faz Critical")
eq(rules.classify_regime(3.5, False, "Turbulence"), "Resilient", "score baixo com stress off")
eq(rules.classify_regime(6.97, None, "Critical"), "Critical", "n/d mantem o estado")

# ── score_band: rotula o medidor A, nao o regime da carteira ─────────────────
eq(rules.score_band(9.0), "Critical", "score 9 e Critical como rotulo")
eq(rules.classify_regime(9.0, False, "Turbulence"), "Turbulence", "mas a carteira fica em Turbulence")
eq(rules.score_band(4.0), "Resilient", "limiar Resilient inclusivo")
eq(rules.score_band(None), "nd", "sem score, n/d")

# ── sub-regime ────────────────────────────────────────────────────────────────
eq(rules.subregime_from_gauge("FTQ", False)[0], "Critical_Stress", "entrada fresca e defensiva")
eq(rules.subregime_from_gauge("FTQ", True)[0], "Critical_FTQ", "FTQ confirmado")
eq(rules.subregime_from_gauge(None, True)[0], "Critical_Stress", "sinal ausente e defensivo")

# ── decide_rebalance ──────────────────────────────────────────────────────────
D = rules.decide_rebalance
eq(D("Critical", "Turbulence", "Critical_Stress", None, False), "stress_on", "entrada")
eq(D("Turbulence", "Critical", None, "Critical_FTQ", False), "stress_off", "saida")
eq(D("Turbulence", "Turbulence", None, None, True), "semestral_rebalance", "semestral")
eq(D("Turbulence", "Turbulence", None, None, False), None, "sem evento")
eq(D("Critical", "Turbulence", "Critical_Stress", None, True), "stress_on", "stress ganha ao semestral")
eq(D("Turbulence", "Resilient", None, None, False), "resilient_off", "saida de Resilient e imediata")
eq(D("Resilient", "Turbulence", None, None, False), None, "entrada em Resilient exige confirmacao")
eq(D("Resilient", "Turbulence", None, None, False, "emergency_resilient_3.8"),
   "emergency_resilient_3.8", "entrada em Resilient com confirmacao")
eq(D("Critical", "Resilient", "Critical_Stress", None, False), "stress_on",
   "de Resilient para Critical continua a ser entrada em stress")

# ── validate_allocation ───────────────────────────────────────────────────────
good = {"US_EQUITIES": 20.0, "US_TREASURIES": 25.0, "IG_CREDIT": 15.0,
        "COMMODITIES": 12.0, "CASH": 20.0, "ALTERNATIVES": 8.0}
eq(rules.validate_allocation(good), (True, []), "alocacao tipica passa")
eq(rules.validate_allocation({})[0], False, "alocacao vazia reprova")
eq(rules.validate_allocation({**good, "US_EQUITIES": 80.0})[0], False, "80% em accoes reprova")
eq(rules.validate_allocation({**good, "US_TREASURIES": 0.0})[0], False, "0% em treasuries reprova")
half = {k: v / 2 for k, v in good.items()}
eq(rules.validate_allocation(half)[0], False, "total a 50% reprova")
eq(rules.validate_allocation({**good, "GOLD_BARS": 0.0})[0], False, "bucket desconhecido reprova")

# as 25 edições legíveis têm de continuar a passar as bandas
import glob, logging
logging.disable(logging.ERROR)
lidas = rejeitadas = 0
for f in glob.glob(str(Path(__file__).resolve().parent.parent / "MRM_Newsletter*.html")):
    alloc, _ = up.parse_newsletter(Path(f))
    if alloc: lidas += 1
    else: rejeitadas += 1
logging.disable(logging.NOTSET)
true(lidas >= 25, f"pelo menos 25 newsletters continuam legiveis (lidas={lidas})")
true(rejeitadas <= 1, f"no maximo 1 rejeitada, a edicao 1 pre-framework (rejeitadas={rejeitadas})")

# ── calendário ────────────────────────────────────────────────────────────────
eq(rules.is_semestral_rebalance_week(date(2026, 1, 30)), True, "ultima sexta de Janeiro 2026")
eq(rules.is_semestral_rebalance_week(date(2026, 1, 23)), False, "penultima sexta nao")
eq(rules.is_semestral_rebalance_week(date(2026, 6, 26)), True, "ultima sexta de Junho 2026")
eq(rules.is_semestral_rebalance_week(date(2026, 9, 25)), False, "Setembro nao e semestral")
nxt = rules.next_semestral_date(date(2026, 9, 8))
eq((nxt.month, nxt.weekday()), (1, 4), "proxima semestral e uma sexta de Janeiro")
true(rules.is_semestral_rebalance_week(nxt), "e de facto uma semana semestral")

# ── rebalance_copy cobre todos os motivos que o motor produz ─────────────────
for reason in ("stress_on", "stress_off", "resilient_off", "semestral_rebalance", "hold",
               "no_allocation_available",
               "critical_subregime_switch:Critical_Stress->Critical_FTQ",
               "emergency_resilient_3.8"):
    txt = rules.rebalance_copy(reason)
    true(txt and txt != reason, f"ha texto publicavel para {reason}")
eq(rules.rebalance_copy(None), rules.REBALANCE_COPY["hold"], "sem motivo = hold")

# ── as_dict é serializável e completo ─────────────────────────────────────────
d = rules.as_dict()
json.dumps(d)  # levanta se nao for serializavel
for field in ("resilientMax", "criticalMin", "regimeLabels", "etfMap",
              "criticalWeights", "allocationBands", "gaugeACaption", "gaugeBCaption"):
    true(field in d, f"as_dict inclui {field}")
eq(d["etfMap"]["Turbulence"]["US_EQUITIES"], "SPY", "as_dict leva o mapa real")
d["etfMap"]["Turbulence"]["US_EQUITIES"] = "XXX"
eq(rules.REGIME_ETF_MAP["Turbulence"]["US_EQUITIES"], "SPY", "as_dict devolve copias, nao os originais")

print(f"TODOS OS {ok} TESTES PASSARAM")
