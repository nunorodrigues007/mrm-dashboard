"""
Testes do send_newsletter.py — o contexto, o prompt, o cartão do arquivo e o tweet.

Sem rede e sem chaves: importar o módulo não pode ter efeitos, e as funções
testadas são puras. O que estes testes protegem, acima de tudo, é a propriedade
que faltava ao sistema: a newsletter conta a mesma história que a carteira.
"""
import importlib.util, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# importar sem executar nada
spec = importlib.util.spec_from_file_location("sn", ROOT / "send_newsletter.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)

import mrm_rules as rules

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(cond, what): eq(bool(cond), True, what)
def has(text, needle, what): true(needle in text, what)
def hasnt(text, needle, what): true(needle not in text, what)

# ── dados sintéticos ─────────────────────────────────────────────────────────
def make_data(score=6.97, stress=False, subregime=None, nd=None):
    gauge = {
        "active": stress, "subregime": subregime,
        "basis": "no trigger active" if stress is False else ("Sahm" if stress else "n/d"),
        "triggers": {
            "sahmRealtime": {"value": None if stress is None else (0.62 if stress else -0.07),
                             "threshold": 0.5, "asOf": "2026-08-01"},
            "delinquencyAccel": {"value": None if stress is None else -0.06,
                                 "threshold": 0.81, "asOf": "2026-04-01"},
        },
    }
    return {
        "globalResilienceScore": score,
        "status": "Turbulence",
        "ndPillars": nd or [],
        "pillars": [
            {"id": "cycle", "name": "Cycle", "score": 5.5, "value": "+0.41%", "status": "caution"},
            {"id": "liquidity", "name": "Liquidity", "score": None if nd else 9.5, "value": "288.3%", "status": "nd" if nd else "critical"},
            {"id": "premium", "name": "Premium", "score": 10.0, "value": "-0.22%", "status": "critical"},
            {"id": "solvency", "name": "Solvency", "score": 2.5, "value": "1.4%", "status": "stable"},
            {"id": "debt", "name": "Debt", "score": 5.5, "value": "11.2%", "status": "caution"},
        ],
        "sentinels": [
            {"id": "jobless", "name": "Initial Jobless Claims", "displayValue": "206K", "thresholdDisplay": "275K", "alert": False},
            {"id": "erp", "name": "Equity Risk Premium", "displayValue": "-0.22%", "thresholdDisplay": "0.80%", "alert": True},
            {"id": "unemployment", "name": "Unemployment Rate", "displayValue": "4.1%", "thresholdDisplay": "5.2%", "alert": False},
        ],
        "stressGauge": gauge,
    }

def make_portfolio(regime="Turbulence", subregime=None, reason="hold", triggered=False):
    key = rules.resolve_etf_map_key(regime, subregime)
    alloc = (dict(rules.CRITICAL_WEIGHTS[key]) if key in rules.CRITICAL_WEIGHTS
             else {"US_EQUITIES": 20.0, "US_TREASURIES": 25.0, "IG_CREDIT": 15.0,
                   "COMMODITIES": 12.0, "CASH": 20.0, "ALTERNATIVES": 8.0})
    cur = {"regime": regime, "critical_subregime": subregime,
           "active_etf_map": dict(rules.REGIME_ETF_MAP[key]),
           "bucket_allocation_pct": alloc,
           "portfolio_value": 10617.97, "portfolio_pnl_pct": 6.18,
           "alpha_vs_benchmark_pct": -10.16}
    hist = [{"rebalance_reason": reason, "rebalance_triggered": triggered}]
    return {"current": cur, "history": hist}

PREV = {"globalResilienceScore": 6.72,
        "pillars": [{"id": "cycle", "score": 5.0}, {"id": "liquidity", "score": 9.5},
                    {"id": "premium", "score": 10.0}, {"id": "solvency", "score": 2.5},
                    {"id": "debt", "score": 5.5}]}

D = date(2026, 9, 11)

# ── semana calma ─────────────────────────────────────────────────────────────
c = sn.build_context(make_data(), make_portfolio(), PREV, D, 27)
eq(c["regime_label"], "Turbulence", "regime operativo")
eq(c["score_band"], "Turbulence", "banda do score")
eq(c["score_color"], "#F98C4F", "cor da banda")
eq(c["rb_alert"], "HOLD", "motivo em maiusculas")
eq(c["rb_status"], rules.REBALANCE_COPY["hold"], "texto do hold vem das regras")
eq(c["port_etfs"], "SPY | IEF | LQD | PDBC | BIL | VNQ", "instrumentos de Turbulence")
eq(c["gauge_b_line"], "OFF (no trigger active)", "medidor B desligado")
eq(c["pillars_live"], "5/5 Pillars Active", "cinco pilares vivos")
eq(c["wow_score"], "▲ +0.2 WoW", "WoW do score")
has(c["sentinel_line"], "Unemployment Rate: 4.1%", "sentinela do desemprego chega ao prompt")
has(c["sentinel_line"], "Initial Jobless Claims: 206K", "sentinela ICSA pelo id correcto")

p = sn.build_prompt(c)
has(p, "GAUGE B — CONCURRENT STRESS", "o prompt separa os dois medidores")
has(p, "THIS DECIDES THE REGIME", "diz quem decide o regime")
has(p, "Operative regime: Turbulence", "regime operativo no prompt")
has(p, "-0.07 (fires at >= 0.5", "valor do gatilho de Sahm")
hasnt(p, "No structural regime change detected", "texto legado desapareceu")
hasnt(p, "EMERGENCY REBALANCE ACTIVATED", "decisao legada por score desapareceu")

# ── semana em que o medidor dispara ──────────────────────────────────────────
c2 = sn.build_context(make_data(stress=True, subregime="STRESS"),
                      make_portfolio("Critical", "Critical_Stress", "stress_on", True),
                      PREV, D, 27)
eq(c2["regime_label"], "Critical · No Relief", "rotulo com sub-regime")
eq(c2["rb_alert"], "STRESS_ON", "motivo de entrada")
eq(c2["rb_color"], "#2d1515", "caixa vermelha na entrada")
eq(c2["port_etfs"], "USMV | SHY | SGOV | GLD | BIL | VNQ",
   "instrumentos de Critical_Stress — SHY, nao TLT")
eq(c2["rb_done"], True, "houve transaccoes")
has(c2["alloc_line"], "US_EQUITIES: 15%", "alocacao efectiva e o vector de Critical")

p2 = sn.build_prompt(c2)
has(p2, "Operative regime: Critical · No Relief", "o modelo ve o sub-regime")
has(p2, "Rebalance outcome: STRESS_ON", "o modelo ve o que aconteceu")
has(p2, rules.REBALANCE_COPY["stress_on"][:40], "com a explicacao canonica")
has(p2, "Executed: yes", "declara que houve execucao")
has(p2, "USMV | SHY", "instrumentos activos no prompt")

# a versao antiga anunciaria TLT em Critical_Stress; garantir que nao acontece
hasnt(p2.split("PORTFOLIO")[1].split("RULES:")[0], "TLT", "nao promete TLT quando a carteira tem SHY")

# ── flight-to-quality ────────────────────────────────────────────────────────
c3 = sn.build_context(make_data(stress=True, subregime="FTQ"),
                      make_portfolio("Critical", "Critical_FTQ", "critical_subregime_switch:Critical_Stress->Critical_FTQ", True),
                      PREV, D, 27)
eq(c3["regime_label"], "Critical · Flight to Quality", "rotulo FTQ")
eq(c3["port_etfs"], "USMV | TLT | SGOV | GLD | BIL | VNQ", "TLT no mapa FTQ")
eq(c3["rb_status"], rules.REBALANCE_COPY["critical_subregime_switch"], "texto da troca de sub-regime")

# ── corrida sem dados ────────────────────────────────────────────────────────
c4 = sn.build_context(make_data(stress=None), make_portfolio(), PREV, D, 27)
has(c4["gauge_b_line"], "n/d", "estado n/d declarado")
has(c4["sahm_line"], "n/d", "gatilho sem valor nao inventa numero")
has(sn.build_prompt(c4), "both triggers unavailable", "o modelo sabe que os dados faltaram")

# ── pilar em n/d ─────────────────────────────────────────────────────────────
c5 = sn.build_context(make_data(nd=["liquidity"]), make_portfolio(), PREV, D, 27)
eq(c5["pillars_live"], "4/5 Pillars Active (liquidity n/d)", "cabecalho reflecte o n/d")
has(sn.build_prompt(c5), "4/5 Pillars Active", "e chega ao HTML gerado")
hasnt(sn.build_prompt(c5), "5/5 Pillars Active", "o 5/5 escrito a mao desapareceu")

# ── bandas de score ──────────────────────────────────────────────────────────
eq(sn.build_context(make_data(score=3.0), make_portfolio(), PREV, D, 27)["score_color"], "#34D058", "score baixo verde")
eq(sn.build_context(make_data(score=9.0), make_portfolio(), PREV, D, 27)["score_color"], "#D73A49", "score alto vermelho")
eq(sn.build_context(make_data(score=9.0), make_portfolio(), PREV, D, 27)["regime_label"], "Turbulence",
   "score alto nao muda o regime da carteira")

# ── cartão do arquivo e tweet ────────────────────────────────────────────────
card = sn.render_archive_card(c, "MRM_Newsletter_Issue27_11Sep2026.html")
has(card, "ISSUE #27", "cartao identifica a edicao")
has(card, "MRM_Newsletter_Issue27_11Sep2026.html", "cartao aponta para o ficheiro")
has(card, "TURBULENCE", "cartao mostra o regime")
card_nd = sn.render_archive_card(c5, "x.html")
has(card_nd, "n/d", "pilar em n/d aparece como n/d e nao como None")
card_stress = sn.render_archive_card(c2, "x.html")
has(card_stress, "#D73A49", "badge vermelho quando o medidor esta ligado")

tw = sn.build_tweet(c2, "x.html")
has(tw, "Gauge B (concurrent stress): ON", "tweet declara o medidor")
has(tw, "Critical · No Relief", "tweet declara o regime operativo")

# ── regressao: as regras nao voltaram para dentro deste ficheiro ─────────────
src = (ROOT / "send_newsletter.py").read_text(encoding="utf-8")
for legacy in ("_REGIME_ETFS", "EMERGENCY_CRITICAL_CONFIRMED", "_h_now", "_l_prev",
               "No structural regime change detected"):
    hasnt(src, legacy, f"o codigo legado {legacy} nao voltou")
hasnt(src, ">= 8.0 confirmed", "a regra dos dois medidores nao esta duplicada aqui")
true("import mrm_rules" in src, "as regras sao importadas")

# ── numeração das edições ────────────────────────────────────────────────────
eq(sn.issue_number_for(date(2026, 3, 13)), 1, "edicao 1 na data de inicio")
eq(sn.issue_number_for(date(2026, 9, 4)), 26, "edicao 26 a 4 de Setembro")
eq(sn.issue_number_for(date(2026, 9, 11)), 27, "edicao 27 a 11 de Setembro")

print(f"TODOS OS {ok} TESTES PASSARAM")
