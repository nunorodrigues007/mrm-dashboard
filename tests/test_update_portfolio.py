"""
Testes do update_portfolio.py — classificacao de regime pelo medidor B,
vector de pesos de Critical, porta do sub-regime e gatilhos de rebalanceamento.

Sem rede: o yfinance e substituido por um stub antes do import, porque nenhuma das
funcoes aqui testadas o usa (so o fetch_prices, que nao entra nestes testes).
"""
import json, sys, types, tempfile
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import update_portfolio as up

ok = 0
def true(c, what):
    eq(bool(c), True, what)

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
eq("rules (Critical_FTQ)" in src, True, "origem declarada como as regras")

# ── a newsletter NAO decide, em regime nenhum ───────────────────────────────
# Ate Set 2026 a linha de cima valia so para Critical: fora de Critical o
# `effective_bucket_alloc` devolvia a tabela da newsletter tal e qual, e era
# essa tabela que o semestral executava. Um numero errado escrito pelo modelo
# — ou lido da coluna errada — era dinheiro colocado errado. Agora o regime
# escolhe o vector e mais nada o escolhe, e estes tres ensaios sao a forma
# EXECUTADA dessa afirmacao: passa-se lixo no terceiro argumento e o resultado
# nao se mexe.
_TURB = dict(up.rules.REGIME_WEIGHTS["Turbulence"])

alloc, src = up.effective_bucket_alloc("Turbulence", None, {"US_EQUITIES": 60.0})
eq(alloc, _TURB, "fora de Critical a alocacao vem das regras, nao da newsletter")
eq(src, "rules (Turbulence)", "origem declarada como as regras")

eq(up.effective_bucket_alloc("Turbulence", None, {})[0], _TURB,
   "sem newsletter nenhuma a alocacao continua a existir — nao vinha de la")

eq(up.effective_bucket_alloc("Turbulence", None,
                             {"US_EQUITIES": 95.0, "CASH": 5.0})[0], _TURB,
   "uma tabela absurda do modelo nao move um unico ponto da carteira")

for _reg, _sub in (("Resilient", None), ("Turbulence", None),
                   ("Critical", "Critical_FTQ"), ("Critical", "Critical_Stress")):
    _a, _ = up.effective_bucket_alloc(_reg, _sub)
    eq(round(sum(_a.values()), 6), 100.0, f"{_reg}/{_sub}: o vector soma 100%")
    eq(sorted(_a), sorted(up.BUCKETS), f"{_reg}/{_sub}: cobre os seis buckets")

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

    # O data.json tem de declarar quando foi gerado. Um ficheiro sem carimbo e
    # tratado como velho — era assim que uma corrida falhada do fetch_data
    # deixava a carteira a decidir sobre os dados de ontem sem dar sinal.
    from datetime import datetime, timedelta
    AGORA = datetime(2026, 9, 11, 22, 0, 0)
    def doc(active, sub, basis, horas):
        gen = (AGORA - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return json.dumps({"meta": {"generatedAt": gen,
                                    "freshness": {"warnAfterHours": 30, "refuseAfterHours": 48}},
                           "stressGauge": {"active": active, "subregime": sub, "basis": basis}})

    p.write_text(doc(False, None, "no trigger active", 4))
    eq(up.read_stress_gauge(p, now=AGORA), (False, None, "no trigger active"), "stressGauge desligado")

    p.write_text(doc(True, "FTQ", "Sahm", 4))
    eq(up.read_stress_gauge(p, now=AGORA), (True, "FTQ", "Sahm"), "stressGauge ligado")

    # 28 h: uma corrida falhada, tolerada com aviso
    p.write_text(doc(False, None, "no trigger active", 28))
    eq(up.read_stress_gauge(p, now=AGORA)[0], False, "28 h ainda decide, com aviso")

    # 50 h: duas corridas falhadas, recusa
    p.write_text(doc(False, None, "no trigger active", 50))
    a, s, b = up.read_stress_gauge(p, now=AGORA)
    eq(a, None, "50 h e velho demais -> n/d, mantem o regime anterior")
    true("50.0h old" in b, "e o motivo diz a idade")

    # um ficheiro sem carimbo nenhum e velho por definicao
    p.write_text(json.dumps({"stressGauge": {"active": False, "subregime": None, "basis": "x"}}))
    eq(up.read_stress_gauge(p, now=AGORA)[0], None, "sem carimbo de geracao -> n/d")

    # e um medidor LIGADO num ficheiro velho tambem nao passa: velho e velho
    p.write_text(doc(True, "FTQ", "Sahm", 72))
    eq(up.read_stress_gauge(p, now=AGORA)[0], None, "ficheiro velho nao afirma stress tambem")

# ── emergencia: o ramo defensivo por score desapareceu ──────────────────────────
hi = {"history": [{"mrm_score": 9.0, "score_complete": True}]}
eq(up.check_emergency(hi, 9.5)[0], False, "score alto ja nao dispara emergencia")
lo = {"history": [{"mrm_score": 3.5, "score_complete": True}]}
eq(up.check_emergency(lo, 3.8), (True, "emergency_resilient_3.8"), "score baixo continua a disparar")
mix = {"history": [{"mrm_score": 5.0, "score_complete": True}]}
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
# O `news` que sobra aqui e deliberado: e o que a edicao dessa semana publicou,
# e a entrada e a saida tem de ser as mesmas COM ele e SEM ele. A propriedade
# que interessa e a simetria — entrar corta accoes, sair devolve-as ao nivel de
# Turbulence — e essa propriedade deixou de depender de alguem ter escrito uma
# tabela legivel nessa semana.
news = {"US_EQUITIES": 40.0, "US_TREASURIES": 20.0, "IG_CREDIT": 15.0,
        "COMMODITIES": 10.0, "CASH": 10.0, "ALTERNATIVES": 5.0}
a_in,  _ = up.effective_bucket_alloc("Critical", "Critical_Stress", news)
a_out, _ = up.effective_bucket_alloc("Turbulence", None, news)
eq(a_in["US_EQUITIES"],  15.0, "em Critical corta accoes para 15%")
eq(a_out, _TURB, "a saida devolve o vector de Turbulence das regras")
eq(a_out["US_EQUITIES"] > a_in["US_EQUITIES"], True,
   "sair de Critical volta a subir a exposicao a accoes")
eq(up.effective_bucket_alloc("Turbulence", None)[0], a_out,
   "e a saida e a mesma sem edicao nenhuma pelo meio")
eq(sorted(a_out), sorted(up.BUCKETS),
   "a saida restitui a alocacao macro inteira — os seis buckets, nao um subconjunto")

# ── Feriados do NYSE: calculados, nao escritos a mao para um ano so ───────
# A tabela anterior tinha 2026 e mais nada. A partir de 1 de Janeiro de 2027 o
# ajuste degradava em silencio para "so fins-de-semana", e uma sexta-feira de
# feriado passava a ser tratada como dia de negociacao.
from datetime import date as _date, timedelta as _td

# A prova de que as regras reproduzem a tabela que ca estava, feriado a feriado.
_TABELA_2026 = {_date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16),
                _date(2026, 4, 3), _date(2026, 5, 25), _date(2026, 6, 19),
                _date(2026, 7, 3), _date(2026, 9, 7), _date(2026, 11, 26),
                _date(2026, 12, 25)}
eq(up.feriados_nyse(2026), _TABELA_2026,
   "as regras reproduzem exactamente a tabela de 2026 escrita a mao")

# E continuam a valer nos anos seguintes.
eq(up.feriados_nyse(2027) & {_date(2027, 3, 26)}, {_date(2027, 3, 26)},
   "a Sexta-feira Santa de 2027 (26 Mar) e feriado")
eq(up.adjust_for_market_holiday(_date(2027, 3, 26)), _date(2027, 3, 25),
   "e uma sexta-feira alvo nessa data recua para a quinta")
eq(up.adjust_for_market_holiday(_date(2027, 12, 24)), _date(2027, 12, 23),
   "o Natal observado a 24 Dez 2027 tambem")
# O Ano Novo ao sabado NAO fecha a sexta anterior — e a excepcao do NYSE.
true(_date(2027, 12, 31) not in up.feriados_nyse(2028),
   "o Ano Novo de 2028 cai a sabado e o NYSE nao fecha a sexta anterior")
eq(up.adjust_for_market_holiday(_date(2027, 12, 31)), _date(2027, 12, 31),
   "por isso 31 Dez 2027 e um dia de negociacao normal")
# As DATAS, ano a ano, e nao so a contagem: contar feriados nao apanha um que
# esteja uma semana adiantado. O `_ultima` partia do dia 28 e nunca chegava aos
# dias 29-31, e o Memorial Day saia errado em 5 dos proximos 10 anos.
_MEMORIAL = {2026: _date(2026, 5, 25), 2027: _date(2027, 5, 31),
             2028: _date(2028, 5, 29), 2029: _date(2029, 5, 28),
             2030: _date(2030, 5, 27), 2031: _date(2031, 5, 26),
             2032: _date(2032, 5, 31), 2033: _date(2033, 5, 30),
             2034: _date(2034, 5, 29), 2035: _date(2035, 5, 28)}
for _ano, _md in _MEMORIAL.items():
    _mai = sorted(d for d in up.feriados_nyse(_ano) if d.month == 5)
    eq(_mai, [_md], f"Memorial Day de {_ano} e {_md}")
_THANKS = {2026: _date(2026, 11, 26), 2027: _date(2027, 11, 25),
           2028: _date(2028, 11, 23), 2029: _date(2029, 11, 22)}
for _ano, _td_ in _THANKS.items():
    _nov = sorted(d for d in up.feriados_nyse(_ano) if d.month == 11)
    eq(_nov, [_td_], f"Thanksgiving de {_ano} e {_td_}")
eq(sorted(d for d in up.feriados_nyse(2027) if d.month == 9), [_date(2027, 9, 6)],
   "Labor Day de 2027 e 6 Set")

# Cada ano tem dez feriados (nove quando o Ano Novo cai a sabado).
for _ano in range(2026, 2036):
    _n = len(up.feriados_nyse(_ano))
    true(_n in (9, 10), f"{_ano} tem 9 ou 10 feriados (obtido {_n})")
# E o ajuste nunca devolve um fim-de-semana nem um feriado.
for _ano in range(2026, 2031):
    _d = _date(_ano, 1, 1)
    while _d.year == _ano:
        _a = up.adjust_for_market_holiday(_d)
        true(_a.weekday() < 5 and _a not in up.feriados_nyse(_a.year),
             f"o ajuste de {_d} da um dia de negociacao ({_a})")
        _d += _td(days=97)

print(f"TODOS OS {ok} TESTES PASSARAM")
