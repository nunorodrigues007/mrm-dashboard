"""
Teste de integração do fetch_data.py sem tocar na rede.

Substitui fetch_fred() e fetch_liquidity_percentile() por valores gravados
(os que o site servia a 2026-09-04) e verifica que o pipeline completo produz
o mesmo score e o mesmo estado do Medidor B.

Correr:  python tests/test_build_data.py
"""
import json, os, re, sys, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRED_API_KEY", "test-key-not-used")

import fetch_data, mrm_gauge_b
from datetime import date, timedelta

# A data em que estas observacoes foram gravadas. Fixa-la e o que impede este
# teste de comecar a falhar sozinho daqui a umas semanas, quando as observacoes
# de Setembro passarem do prazo de validade das suas series.
HOJE = date(2026, 9, 4)

# ── valores gravados: os que o data.json servia a 2026-09-04 ───────────────────
OBS = {
    "T10Y2Y":  [{"date": "2026-09-03", "value": "0.43"}, {"date": "2026-08-03", "value": "0.22"}] * 10,
    "DGS10":   [{"date": "2026-09-02", "value": "4.79"}] * 40 + [{"date": "2026-06-02", "value": "4.47"}] * 40,
    "M2SL":    [{"date": "2026-07-01", "value": "22500.0"}] + [{"date": "2026-06-01", "value": "22400.0"}] * 11
               + [{"date": "2025-07-01", "value": "21345.0"}] * 3,
    "DRALACBN":[{"date": "2026-04-01", "value": "1.38"}, {"date": "2026-01-01", "value": "1.46"},
                {"date": "2025-10-01", "value": "1.50"}, {"date": "2025-07-01", "value": "1.44"},
                {"date": "2025-04-01", "value": "1.44"}, {"date": "2025-01-01", "value": "1.53"}],
    "TDSP":    [{"date": "2026-01-01", "value": "11.164138"}] * 5,
    "ICSA":    [{"date": "2026-08-29", "value": "206000"}, {"date": "2026-08-22", "value": "204000"},
                {"date": "2026-08-15", "value": "205000"}],
    "UNRATE":  [{"date": "2026-08-01", "value": "4.1"}, {"date": "2026-07-01", "value": "4.1"},
                {"date": "2026-06-01", "value": "4.2"}],
    "SAHMREALTIME": [{"date": "2026-08-01", "value": "-0.07"}, {"date": "2026-07-01", "value": "-0.03"},
                     {"date": "2026-06-01", "value": "0.07"}],
    # Mesmo nivel na data de referencia e hoje: E/P fica igual a referencia, para
    # que as ancoras acima (score 6.97, Premium 10.0) continuem a valer. O efeito
    # do movimento do preco e testado a parte, mais abaixo.
    "SP500":   [{"date": "2026-09-04", "value": "7670.00"},
                {"date": "2026-08-31", "value": "7670.00"},
                {"date": "2026-06-30", "value": "7460.00"}],
}

# NAO se substitui o fetch_fred: substitui-se a camada HTTP por baixo dele.
#
# Enquanto o fetch_fred era substituido, tudo o que ele faz — ordenar as
# observacoes, e agora recusar uma serie cuja observacao mais recente passou do
# prazo — ficava de fora do teste. A verificacao de idade foi posta la dentro
# precisamente porque ha caminhos (o history_values do DGS10, que decide entre
# TLT e SHY) que nao passam pelo latest_value; um teste que salte o fetch_fred
# nao ve nenhum desses caminhos.

def fake_liquidity(hoje=None):
    # valores reproduzidos independentemente a partir das séries Z.1/GDP
    return 2.883, 99.3, "2026-01-01", {"totalEquitiesB": 91857.2, "gdpB": 31865.7,
                                       "historyPoints": 302, "historyStart": "1947-10-01"}

class FakeResp:
    def __init__(self, series_id, limit=None, asc=False):
        self.series_id, self.limit, self.asc = series_id, limit, asc
    def raise_for_status(self): pass
    def json(self):
        # A API do FRED devolve por ordem de `sort_order` e corta em `limit`.
        obs = sorted(OBS.get(self.series_id, []), key=lambda o: o["date"],
                     reverse=not self.asc)
        if self.limit:
            obs = obs[:int(self.limit)]
        return {"observations": obs}

def fake_requests_get(url, params=None, timeout=None):
    p = params or {}
    return FakeResp(p.get("series_id"), p.get("limit"),
                    asc=(p.get("sort_order") == "asc"))

def main():
    fetch_data.requests.get = fake_requests_get
    fetch_data.fetch_liquidity_percentile = fake_liquidity
    mrm_gauge_b.requests.get = fake_requests_get

    tmp = tempfile.mkdtemp()
    real_dir = os.path.dirname(os.path.abspath(fetch_data.__file__))
    shutil.copy(os.path.join(real_dir, "score_history.json"), tmp)
    fetch_data.__file__ = os.path.join(tmp, "fetch_data.py")

    # A publicacao anterior e DECLARADA aqui: "nao ha nenhuma comparavel".
    #
    # O `build_data` escreve o data.json pelo `__file__` (isolado acima) mas le a
    # publicacao anterior por caminho RELATIVO — `load_previous_metrics()` abre
    # "data_prev.json" a partir do directorio de trabalho. Este e o unico
    # ficheiro da suite que chama o `build_data` sem mudar de directorio,
    # portanto lia o data_prev.json do repositorio. Hoje esse ficheiro e um
    # legado sem `metricValue`, e por isso as asserçoes de "sem base de
    # comparacao" passavam. Mas o job da newsletter roda o data_prev.json em
    # TODAS as sextas bem sucedidas: a partir da segunda sexta publicada, o
    # ficheiro passa a ter metricValue, tres asserçoes ficam falsas, e como este
    # ficheiro e portao do job da carteira, o que se ve nao e um teste vermelho:
    # e uma sexta sem carteira e sem newsletter, e todas as seguintes — porque
    # quem rodava o data_prev.json era justamente o job que deixou de correr.
    #
    # A dependencia nao aparecia em lado nenhum do teste: nao ha um
    # `ROOT / "data_prev.json"` para procurar. Entrava pelo directorio corrente.
    fetch_data.load_previous_metrics = lambda path="data_prev.json": ({}, {}, None)

    data = fetch_data.build_data(HOJE)
    fails = []
    def check(cond, msg):
        print(("  OK   " if cond else "  FALHA ") + msg)
        if not cond: fails.append(msg)

    p = {x["id"]: x for x in data["pillars"]}
    print("\n── verificações ──")
    # O composto sai das regras sobre os pilares desta corrida. Estava cravado
    # em 6,97 — um numero que so sai de uma ancora do E-P de 3,84: qualquer
    # ancora >= 4,79 (o 10Y do fixture) baixa o pilar Premium, muda o composto,
    # e a actualizacao trimestral que o proprio sistema manda fazer punha o
    # portao vermelho.
    import mrm_rules as _regras
    _esperado_composto = _regras.global_score({x["id"]: x["score"] for x in data["pillars"]})[0]
    check(data["globalResilienceScore"] == _esperado_composto,
          f"score composto sai das regras sobre os pilares desta corrida "
          f"({data['globalResilienceScore']} vs {_esperado_composto})")
    check(p["cycle"]["score"] == 5.5,     f"pilar Cycle = 5.5 ({p['cycle']['score']})")
    check(p["liquidity"]["score"] == 9.5, f"pilar Liquidity = 9.5 ({p['liquidity']['score']})")
    _erp_fixture = round(fetch_data.SP500_EARNINGS_YIELD - 4.79, 2)
    check(p["premium"]["score"] == fetch_data.score_premium(_erp_fixture),
          f"pilar Premium sai do ERP desta corrida ({p['premium']['score']} para "
          f"ERP {_erp_fixture}, com a ancora em {fetch_data.SP500_EARNINGS_YIELD})")
    check(p["solvency"]["score"] == 2.5,  f"pilar Solvency = 2.5 ({p['solvency']['score']})")
    check(p["debt"]["score"] == 5.5,      f"pilar Debt = 5.5 ({p['debt']['score']})")
    check(data["status"] == "Turbulence", f"status inalterado = Turbulence ({data['status']})")
    check(data["ndPillars"] == [],        f"nenhum pilar em n/d ({data['ndPillars']})")

    sg = data["stressGauge"]
    check(sg["active"] is False,          f"Medidor B desligado ({sg['active']})")
    check(sg["subregime"] is None,        f"sem sub-regime ({sg['subregime']})")
    check(sg["triggers"]["sahmRealtime"]["fired"] is False, "gatilho Sahm nao dispara (-0.07 < 0.50)")
    check(sg["triggers"]["delinquencyAccel"]["value"] == -0.06,
          f"aceleracao da delinquencia = -0.06 pp ({sg['triggers']['delinquencyAccel']['value']})")
    check(sg["triggers"]["delinquencyAccel"]["fired"] is False, "gatilho delinquencia nao dispara")

    hs = data["historicalScores"]
    check(len(hs) == 24, f"sparkline com 24 pontos reais ({len(hs)})")
    # Era `hs[0] != hs[-1]`: um empate entre o primeiro ponto historico e o
    # composto desta corrida — coincidencia aritmetica, nao um defeito — punha o
    # portao vermelho. O que se afirma e que a serie tem mesmo variacao.
    check(len({x["score"] for x in hs}) > 1,
          f"sparkline deixou de ser plana por construcao "
          f"({len({x['score'] for x in hs})} valores distintos em {len(hs)} pontos)")
    check(json.dumps(data) and True, "data.json serializavel")
    check(p["premium"].get("epEstimated") is True, "Premium declarado como estimativa")
    check(p["premium"].get("epAsOf") == fetch_data.SP500_EARNINGS_YIELD_ASOF,
          "Premium com data do E/P")

    print("\n── E/P ancorado nos earnings ──")
    ep = p["premium"]
    anchor = ep.get("epAnchor", {})
    check(ep["epValue"] == fetch_data.SP500_EARNINGS_YIELD,
          f"indice ao nivel da referencia -> E/P igual a referencia ({ep['epValue']})")
    check(anchor.get("indexRef") == 7670.0 and anchor.get("indexNow") == 7670.0,
          f"o data.json declara os dois niveis do indice ({anchor.get('indexRef')}, {anchor.get('indexNow')})")
    check(anchor.get("earningsPerIndexUnit") is not None,
          "o data.json declara os earnings por unidade de indice")
    check("marked to the latest close" in anchor.get("basis", ""),
          f"a base do calculo e declarada ({anchor.get('basis')})")
    check(anchor.get("ageDays") is not None, "a idade da referencia e publicada")

    # o movimento do preco tem de mover o E/P — era isto que faltava ao pilar
    ref, ref_date = fetch_data.SP500_EARNINGS_YIELD, fetch_data.SP500_EARNINGS_YIELD_ASOF
    # O fecho "de hoje" tem de vir DEPOIS da referencia, seja qual for a data da
    # referencia. Estava fixo em 2026-09-04: bastava o operador avancar a ancora
    # para uma data posterior — que e o que o sistema lhe manda fazer — para o
    # `at_or_before` escolher o mesmo fecho para os dois lados, o E/P deixar de
    # se mexer, e este ficheiro-portao ficar vermelho.
    _depois = (date.fromisoformat(ref_date) + timedelta(days=4)).isoformat()
    crash = [{"date": _depois, "value": "6136.00"}, {"date": ref_date, "value": "7670.00"}]
    rally = [{"date": _depois, "value": "9204.00"}, {"date": ref_date, "value": "7670.00"}]
    y_crash, _ = fetch_data.earnings_yield_now(ref, ref_date, crash)
    y_rally, _ = fetch_data.earnings_yield_now(ref, ref_date, rally)
    # As duas linhas seguintes eram 4.8 e 3.2 — os valores que saem de uma
    # referencia de 3.84. Actualizar a ancora manual do E/P (que e o que o
    # proprio alarme abaixo manda fazer) punha este ficheiro-portao vermelho, e
    # com ele os dois jobs de sexta. O que se afirma e a RELACAO: o preco a cair
    # 20% levanta o E/P na mesma proporcao.
    check(y_crash == round(ref / 0.8, 2),
          f"queda de 20% do indice -> E/P sobe de {ref} para {y_crash}")
    check(y_rally == round(ref / 1.2, 2),
          f"subida de 20% do indice -> E/P desce de {ref} para {y_rally}")
    # Uma queda do mercado NUNCA pode agravar o pilar Premium. O `<` estrito so
    # e verdade quando os dois ERP caem em bandas diferentes: com uma ancora
    # baixa os dois saturam no mesmo extremo, e a actualizacao da ancora punha o
    # portao vermelho por uma coincidencia de bandas.
    check(fetch_data.score_premium(round(y_crash - 4.79, 2))
          <= fetch_data.score_premium(round(ref - 4.79, 2)),
          f"o pilar Premium nunca agrava numa queda do mercado "
          f"(ERP {round(y_crash - 4.79, 2)} vs {round(ref - 4.79, 2)})")
    # E a regra por tras, que nao depende de ancora nenhuma: o score do Premium
    # e monotono nao-crescente no ERP — um premio maior nunca da um pilar pior.
    _sequencia = [fetch_data.score_premium(round(x / 10, 2)) for x in range(-40, 61)]
    check(all(b <= a for a, b in zip(_sequencia, _sequencia[1:])),
          "score_premium e monotono: um ERP maior nunca agrava o pilar")

    # sem serie do indice nao inventa: devolve a referencia e diz porque
    y_none, d_none = fetch_data.earnings_yield_now(ref, ref_date, [])
    check(y_none == ref and "unavailable" in d_none["basis"],
          "sem serie do indice devolve a referencia com o motivo declarado")
    y_old, d_old = fetch_data.earnings_yield_now(ref, "2000-01-01",
                                                 [{"date": "2026-09-04", "value": "7670.00"}])
    check(y_old == ref and "on or before" in d_old["basis"],
          "referencia anterior a serie disponivel cai no mesmo caminho seguro")

    # A referencia manual nao pode apodrecer em silencio — mas o alarme nao pode
    # ser este ficheiro a ficar vermelho.
    #
    # Era: `days_since(ASOF)` sem `hoje`, comparado com EP_MAX_AGE_DAYS. Duas
    # coisas erradas de uma vez. Primeira, `days_since` sem `hoje` le o
    # `_HOJE` que o `build_data` desta suite deixou fixado, portanto a idade era
    # a constante 4 e o alarme NUNCA disparava. Segunda, se disparasse, o que
    # dava era um ficheiro-portao vermelho — sexta sem carteira e sem
    # newsletter — e a correccao que a mensagem mandava fazer partia outras
    # quatro asserçoes deste mesmo ficheiro.
    #
    # O que se verifica agora e o MECANISMO: com a ancora velha, o data.json
    # declara-a velha, e o gerador poe um aviso de qualidade de dados na edicao.
    # Quem tem de ler o alarme sao os subscritores e o operador, nao o CI.
    _hoje_sim = date.fromisoformat(HOJE) if isinstance(HOJE, str) else HOJE
    check(anchor.get("stale") is False,
          f"com a ancora de {fetch_data.SP500_EARNINGS_YIELD_ASOF} a {anchor.get('ageDays')} "
          f"dias da semana simulada, o data.json nao a declara velha")
    _velho = fetch_data.days_since(
        fetch_data.SP500_EARNINGS_YIELD_ASOF,
        date.fromisoformat(fetch_data.SP500_EARNINGS_YIELD_ASOF)
        + timedelta(days=fetch_data.EP_STALE_AFTER_DAYS + 1))
    check(_velho > fetch_data.EP_STALE_AFTER_DAYS,
          f"e passado o prazo a idade e mesmo maior que o limite ({_velho} > "
          f"{fetch_data.EP_STALE_AFTER_DAYS})")
    check(fetch_data.days_since(fetch_data.SP500_EARNINGS_YIELD_ASOF, _hoje_sim)
          == anchor.get("ageDays"),
          "e a idade publicada e a idade contada a partir da data da corrida, "
          "nao a partir do relogio de quem corre os testes")

    print("\n── regras canonicas exportadas para o front-end ──")
    import mrm_rules
    r = data.get("rules", {})
    check(r.get("etfMap", {}).get("Turbulence", {}).get("US_EQUITIES") == "SPY",
          "data.json leva o mapa de ETF canonico")
    check(r.get("criticalWeights", {}).get("Critical_FTQ", {}).get("US_TREASURIES") == 35.0,
          "data.json leva o vector de pesos de Critical")
    check(r.get("resilientMax") == mrm_rules.RESILIENT_MAX and r.get("criticalMin") == mrm_rules.CRITICAL_MIN,
          "limiares publicados = limiares do motor")
    check(r.get("regimeDecidedBy") == "gaugeB", "o JSON declara quem decide o regime")

    print("\n── deltas dos pilares ──")
    for pid in ("cycle", "liquidity", "premium", "solvency", "debt"):
        check(p[pid].get("metricValue") is not None, f"{pid} publica metricValue para comparacao futura")
    check(all(p[pid]["delta"] == "—" for pid in ("cycle", "premium", "solvency", "debt")),
          "sem publicacao anterior comparavel, os deltas sao travessao e nao numeros inventados")
    check("+0.02" not in json.dumps(data) and "\"+0.3\"" not in json.dumps(data),
          "os deltas escritos a mao desapareceram")

    real_prev = fetch_data.load_previous_metrics
    fetch_data.load_previous_metrics = lambda path="data_prev.json": (
        {"cycle": 0.22, "premium": -0.50, "solvency": 1.46, "debt": 11.0, "liquidity": 280.0},
        {"cycle": 5.0, "premium": 9.5, "solvency": 3.0, "debt": 5.5, "liquidity": 9.0},
        "2026-09-04T18:00:00Z")
    d3 = fetch_data.build_data(HOJE)
    p3 = {x["id"]: x for x in d3["pillars"]}
    fetch_data.load_previous_metrics = real_prev
    check(p3["cycle"]["delta"] == "+0.21%", f"delta do Cycle real ({p3['cycle']['delta']})")
    check(p3["solvency"]["delta"] == "-0.08 pp", f"delta da Solvency real ({p3['solvency']['delta']})")
    # O ERP desta corrida contra o -0.50 da publicacao anterior: o premio
    # comprimiu-se mais. O numero era um literal "-0.45%", que sai de uma ancora
    # do E/P de 3,84 — mudar a ancora punha o portao vermelho. O que se afirma e
    # a subtraccao.
    _erp_agora = p3["premium"]["metricValue"]
    check(p3["premium"]["delta"] == f"{_erp_agora - (-0.50):+.2f}%",
          f"delta do Premium real ({p3['premium']['delta']}, ERP {_erp_agora})")
    check(d3["meta"]["deltaBasis"] == "vs. 2026-09-04", f"base do delta declarada ({d3['meta']['deltaBasis']})")
    # A direccao segue o SCORE do pilar, nao o sinal do numero: um ERP a descer
    # e um numero negativo mas um agravamento. Estava cravada em "worse", o que
    # so e verdade com a ancora do E-P em 3,84 — com uma ancora mais alta o ERP
    # sobe e a direccao passa a ser "better", correctamente. O que se afirma e a
    # REGRA, contra o score anterior que o fixture declara.
    _score_premium_anterior = 9.5
    _dir_esperada = ("worse" if p3["premium"]["score"] > _score_premium_anterior else
                     "better" if p3["premium"]["score"] < _score_premium_anterior
                     else "flat")
    check(p3["premium"]["deltaDirection"] == _dir_esperada,
          f"Premium: a direccao segue o score do pilar "
          f"({p3['premium']['deltaDirection']}, score {p3['premium']['score']} "
          f"contra {_score_premium_anterior})")
    check(p3["solvency"]["deltaDirection"] == "better",
          f"Solvency: delinquencia a cair e melhoria ({p3['solvency']['deltaDirection']})")
    check(p3["debt"]["deltaDirection"] == "flat", f"Debt sem mudanca de score ({p3['debt']['deltaDirection']})")
    check(all(p[pid]["deltaDirection"] is None for pid in ("cycle", "premium")),
          "sem base de comparacao a direccao e neutra")

    print("\n── n/d: simular DGS10 indisponivel ──")
    _dgs10_original = OBS["DGS10"]
    OBS["DGS10"] = []
    d2 = fetch_data.build_data(HOJE)
    p2 = {x["id"]: x for x in d2["pillars"]}
    check(p2["premium"]["score"] is None, "Premium fica n/d em vez de inventar 6.0")
    check(d2["ndPillars"] == ["premium"], f"n/d declarado no JSON ({d2['ndPillars']})")
    check(d2["globalResilienceScore"] == 5.97,
          f"composto renormalizado sobre 4 pilares = 5.97 (obtido {d2['globalResilienceScore']})")

    print("\n── serie parada: o T10Y2Y deixou de ser actualizado ──")
    # Uma serie descontinuada no FRED nao devolve erro: devolve 200 com a ultima
    # observacao que teve, todas as semanas. Ate aqui o pilar continuava a
    # pontuar sobre esse numero e nada no ficheiro dizia que era de ha um ano.
    OBS["DGS10"] = _dgs10_original
    _t10_original = OBS["T10Y2Y"]
    OBS["T10Y2Y"] = [{"date": "2025-02-03", "value": "0.43"}] * 10
    d4 = fetch_data.build_data(HOJE)
    p4 = {x["id"]: x for x in d4["pillars"]}
    check(p4["cycle"]["score"] is None, f"Cycle fica n/d, nao 5.5 ({p4['cycle']['score']})")
    check("cycle" in d4["ndPillars"], f"declarado em ndPillars ({d4['ndPillars']})")
    check(d4["meta"]["fredSeriesStale"].get("T10Y2Y") == 578,
          f"a idade da observacao e publicada ({d4['meta'].get('fredSeriesStale')})")
    check(d4["meta"]["fredSeriesDates"]["T10Y2Y"] == "2025-02-03",
          "a data continua publicada, para se ver porque ficou n/d")
    check(d4["meta"]["freshness"]["degraded"] is True, "a corrida e marcada como degradada")
    check(d4["meta"]["freshness"]["staleSeries"] == ["T10Y2Y"],
          f"e a serie parada e nomeada ({d4['meta']['freshness']['staleSeries']})")
    # E uma serie fresca volta a pontuar: o prazo nao pode ser um interruptor
    # que fica ligado.
    OBS["T10Y2Y"] = _t10_original
    d5 = fetch_data.build_data(HOJE)
    p5 = {x["id"]: x for x in d5["pillars"]}
    check(p5["cycle"]["score"] == 5.5, f"com a serie fresca volta a 5.5 ({p5['cycle']['score']})")
    check(d5["meta"]["fredSeriesStale"] == {}, "e nao fica nada pendurado do teste anterior")

    # Uma serie parada que NAO e um pilar — o ICSA e sentinela — tem de marcar
    # a corrida como degradada na mesma. Sem isto, `degraded` so apanhava as
    # series paradas por acidente, quando calhava tambem porem um pilar em n/d.
    _icsa_original = OBS["ICSA"]
    OBS["ICSA"] = [{"date": "2024-08-29", "value": "206000"}] * 3
    d6 = fetch_data.build_data(HOJE)
    check(d6["ndPillars"] == [], f"nenhum pilar afectado ({d6['ndPillars']})")
    check(d6["meta"]["freshness"]["degraded"] is True,
          "uma sentinela parada marca a corrida como degradada na mesma")
    check(d6["meta"]["freshness"]["staleSeries"] == ["ICSA"],
          f"e e nomeada ({d6['meta']['freshness']['staleSeries']})")

    # Serie parada numa corrida, indisponivel na seguinte: o registo da anterior
    # nao pode ficar pendurado a dizer que esta parada quando ja nem responde.
    OBS["ICSA"] = []
    d7 = fetch_data.build_data(HOJE)
    check(d7["meta"]["fredSeriesStale"] == {},
          f"o registo da corrida anterior nao transita ({d7['meta']['fredSeriesStale']})")
    OBS["ICSA"] = _icsa_original

    print("\n── uma sentinela sem leitura nao leva veredicto ──")
    # A UNRATE era a unica das tres sem estado de ausencia. Sem leitura,
    # publicava `status "normal"` e `delta "+0.0%"` — e o capitulo da Academia
    # lia isso e escrevia, a verde, que a taxa esta abaixo do gatilho de 5,2%.
    # Nao esta nada: nao ha taxa. Verifica-se a PROPRIEDADE nas tres, para que
    # a proxima sentinela que alguem acrescentar nao volte a ser a excepcao.
    _guardado = {k: OBS[k] for k in ("UNRATE", "ICSA", "DGS10", "SP500")}
    for _sid, _series in (("unemployment", ["UNRATE"]),
                          ("jobless", ["ICSA"]),
                          ("erp", ["DGS10"])):
        for _s in _series:
            OBS[_s] = []
        _dnd = fetch_data.build_data(HOJE)
        _sent = {x["id"]: x for x in _dnd["sentinels"]}[_sid]
        check(_sent["value"] is None,
              f"{_sid}: sem serie nao ha valor ({_sent['value']})")
        check(_sent["status"] == "nd",
              f"{_sid}: e o estado publicado e 'nd' ({_sent['status']})")
        check(_sent["displayValue"] == "n/d",
              f"{_sid}: e o que se le e 'n/d' ({_sent['displayValue']})")
        check(_sent["trend"] == "nd",
              f"{_sid}: e nao se declara direccao nenhuma ({_sent['trend']})")
        # A propriedade e "nao se inventa uma variacao", nao "escreve-se esta
        # string": o Premium partilha o delta com o cartao do pilar, que usa
        # "—". Qualquer marcador serve, desde que nao seja um numero.
        check(not any(ch.isdigit() for ch in str(_sent["delta"])),
              f"{_sid}: e nao se inventa uma variacao ({_sent['delta']})")
        check(_sent["alert"] is False,
              f"{_sid}: e a ausencia nao dispara o alerta ({_sent['alert']})")
        check("n/d" in _sent["description"],
              f"{_sid}: e a descricao diz que nao ha leitura "
              f"({_sent['description'][:80]})")
        for k, v in _guardado.items():
            OBS[k] = v

    # E com leitura, nenhuma das tres se declara sem leitura — senao o teste
    # acima passaria com um sistema que nunca publica nada.
    _dok = fetch_data.build_data(HOJE)
    for _s in _dok["sentinels"]:
        check(_s["status"] != "nd" and _s["displayValue"] != "n/d",
              f"{_s['id']}: com serie a responder ha leitura "
              f"({_s['status']}/{_s['displayValue']})")
    # Uma variacao de zero e uma semana ESTAVEL, nao uma semana sem leitura:
    # o `trend` do ICSA saia de `if icsa_delta_val`, que confunde as duas.
    # E uma leitura SOZINHA nao e uma comparacao. `latest_value` e o `fetch_fred`
    # das tres observacoes sao dois pedidos independentes: o segundo pode falhar
    # sozinho, ou a serie pode trazer uma so observacao valida. O anterior caia
    # de volta no PROPRIO valor, e publicava-se "+0.0%" e "stable" — uma
    # variacao da leitura consigo mesma, ao lado do numero, como se fosse
    # informacao. E era exactamente a frase que o site copia para o capitulo:
    # "UNRATE currently stands at 4.1% (stable, +0.0% vs. prior reading)".
    _so_uma = {k: OBS[k] for k in ("UNRATE", "ICSA")}
    for _sid_u, _serie_u in (("unemployment", "UNRATE"), ("jobless", "ICSA")):
        OBS[_serie_u] = [_so_uma[_serie_u][0]]
        _d1 = fetch_data.build_data(HOJE)
        _s1 = {x["id"]: x for x in _d1["sentinels"]}[_sid_u]
        check(_s1["value"] is not None,
              f"{_sid_u}: com uma observacao ha leitura ({_s1['value']})")
        check(_s1["trend"] == "nd",
              f"{_sid_u}: mas nao ha direccao — nao ha com que comparar "
              f"({_s1['trend']})")
        check(not any(ch.isdigit() for ch in str(_s1["delta"])),
              f"{_sid_u}: e nao se inventa uma variacao ({_s1['delta']})")
        OBS[_serie_u] = _so_uma[_serie_u]

    _icsa_o2 = OBS["ICSA"]
    OBS["ICSA"] = [{"date": "2026-08-29", "value": "206000"},
                   {"date": "2026-08-22", "value": "206000"},
                   {"date": "2026-08-15", "value": "206000"}]
    _dzero = fetch_data.build_data(HOJE)
    _sz = {x["id"]: x for x in _dzero["sentinels"]}["jobless"]
    check(_sz["trend"] == "stable",
          f"uma semana sem variacao e 'stable', nao 'nd' ({_sz['trend']})")
    check(_sz["displayValue"] != "n/d",
          f"e continua a haver leitura ({_sz['displayValue']})")
    OBS["ICSA"] = _icsa_o2

    print("\n── o pilar Premium nao afirma marcacao a mercado sem indice ──")
    # `earnings_yield_now` devolve a referencia INALTERADA quando nao ha serie do
    # indice, e declara-o no `basis`. Ao lado, a descricao do pilar afirmava
    # sempre "marked to the latest S&P 500 close, so the pillar moves with both
    # the market and the 10Y" — falso nessa semana, e publicado no site. Repare-
    # se que uma falha de FETCH da SP500 nem sequer entra em `staleSeries` (nao
    # ha observacao para marcar como velha), portanto a edicao tambem nao leva
    # aviso nenhum: a unica coisa que o leitor via era a frase errada.
    _sp_o3 = OBS["SP500"]
    OBS["SP500"] = []
    _d_sp = fetch_data.build_data(HOJE)
    _p_sp = {x["id"]: x for x in _d_sp["pillars"]}["premium"]
    check(_d_sp["meta"]["freshness"]["staleSeries"] == [],
          f"uma falha de fetch da SP500 nao e uma serie parada "
          f"({_d_sp['meta']['freshness']['staleSeries']})")
    check("NOT marked to market" in _p_sp["description"],
          f"e a descricao do Premium diz que nao houve marcacao a mercado "
          f"({_p_sp['description'][:160]})")
    check("moves with both the market" not in _p_sp["description"],
          f"e NAO afirma o contrario ({_p_sp['description'][:160]})")
    _basis_sp = _p_sp["epAnchor"]["basis"]
    check(_basis_sp in _p_sp["description"],
          f"e o motivo publicado e o que o produtor declarou ({_basis_sp})")
    OBS["SP500"] = _sp_o3
    # E com o indice a responder, a frase de sempre.
    _d_sp_ok = fetch_data.build_data(HOJE)
    _p_sp_ok = {x["id"]: x for x in _d_sp_ok["pillars"]}["premium"]
    check("moves with both the market" in _p_sp_ok["description"],
          f"com indice, o Premium volta a mexer com o mercado "
          f"({_p_sp_ok['description'][:160]})")
    check("NOT marked to market" not in _p_sp_ok["description"],
          "e nao diz o contrario")

    print("\n── serie parada num caminho que NAO passa pelo latest_value ──")
    # O DGS10 alimenta duas coisas: o E/P do pilar Premium (via latest_value) e a
    # janela de 3 meses do 10Y (via history_values), que escolhe entre
    # Critical_FTQ — TLT, 35% da carteira — e Critical_Stress. So o primeiro
    # caminho estava protegido. Com a serie parada, a janela congelava e o
    # sistema podia declarar Flight-to-Quality indefinidamente sobre uma leitura
    # morta.
    _dgs_orig = OBS["DGS10"]
    OBS["DGS10"] = [{"date": "2024-09-02", "value": "4.79"}] * 40 + \
                   [{"date": "2024-06-02", "value": "4.47"}] * 40
    d8 = fetch_data.build_data(HOJE)
    check(d8["meta"]["fredSeriesStale"].get("DGS10") is not None,
          f"o DGS10 parado e detectado ({d8['meta'].get('fredSeriesStale')})")
    check(d8["stressGauge"]["subregime"] is None or d8["stressGauge"]["active"] is not True,
          "e a janela do 10Y nao produz um sub-regime a partir de dados mortos")
    p8 = {x["id"]: x for x in d8["pillars"]}
    check(p8["premium"]["score"] is None,
          f"o pilar que depende dele fica n/d ({p8['premium']['score']})")
    OBS["DGS10"] = _dgs_orig

    # E o mesmo para o SP500, que ancora o E/P e nem sequer tinha prazo declarado.
    _sp_orig = OBS["SP500"]
    OBS["SP500"] = [{"date": "2024-09-04", "value": "7670.00"}] * 3
    d9 = fetch_data.build_data(HOJE)
    check(d9["meta"]["fredSeriesStale"].get("SP500") is not None,
          f"o SP500 parado e detectado ({d9['meta'].get('fredSeriesStale')})")
    # O Premium NAO fica n/d: a serie do indice so marca a mercado um E/P de
    # referencia declarado no codigo. Sem indice, o E/P fica na referencia e o
    # motivo e publicado — degradacao declarada, nao um numero inventado. O que
    # importa e que o preco morto NAO entra na conta.
    _p9 = {x["id"]: x for x in d9["pillars"]}["premium"]
    check(_p9["score"] is not None, "o Premium continua a pontuar")
    check("unavailable" in (_p9.get("epAnchor", {}).get("basis", "")),
          f"e o ficheiro diz que o indice nao entrou na conta "
          f"({_p9.get('epAnchor', {}).get('basis')})")
    check(_p9["epValue"] == fetch_data.SP500_EARNINGS_YIELD,
          "o E/P fica na referencia declarada, sem marcar a um preco morto")
    OBS["SP500"] = _sp_orig

    print("\n── a corrida nao depende de quando corre ──")
    # `days_since` era a unica leitura do relogio real dentro do build_data: a
    # idade da referencia do E/P mudava conforme o dia em que se corresse, e
    # nao conforme a data que se desse.
    _d10 = fetch_data.build_data(HOJE)
    _d11 = fetch_data.build_data(HOJE + timedelta(days=400))
    _a10 = {x["id"]: x for x in _d10["pillars"]}["premium"]["epAnchor"]
    _a11 = {x["id"]: x for x in _d11["pillars"]}["premium"]["epAnchor"]
    check(_a10["ageDays"] != _a11["ageDays"],
          f"a idade da referencia do E/P segue a data DADA "
          f"({_a10['ageDays']} vs {_a11['ageDays']})")
    check(_a11["ageDays"] - _a10["ageDays"] == 400,
          f"exactamente os 400 dias de diferenca "
          f"({_a11['ageDays'] - _a10['ageDays']})")
    check(_a11["stale"] is True and _a10["stale"] is False,
          f"e a referencia fica velha na data futura, nao na de hoje "
          f"({_a10['stale']} -> {_a11['stale']})")


    # ── O historico do score E MANTIDO, nao so lido ───────────────────────
    #
    # O `score_history.json` era estatico: 260 pontos a acabar em Ago '26 e
    # nenhum workflow lhe tocava. O grafico do site desenha os pontos
    # EQUIDISTANTES, pelo que a distancia entre o ultimo ponto historico e o
    # ponto corrente crescia um mes por mes — desenhada como um passo mensal
    # normal, sob um titulo escrito a mao a dizer "24-Month History". Uma
    # mentira na capa do produto que se agrava sozinha, e que o unico piso
    # existente (`len(hs) == 24`) nunca via.
    import tempfile as _tf_hist
    from datetime import date as _date_hist
    from pathlib import Path as _Path_hist

    _dir_hist = _Path_hist(_tf_hist.mkdtemp())
    _p_hist = str(_dir_hist / "score_history.json")
    _esc = lambda o: _Path_hist(_p_hist).write_text(json.dumps(o))
    _le = lambda: json.loads(_Path_hist(_p_hist).read_text())
    _esc([{"date": "Jun '26", "score": 6.72, "month": "2026-06"},
          {"date": "Jul '26", "score": 6.97, "month": "2026-07"},
          {"date": "Aug '26", "score": 6.97, "month": "2026-08"}])

    # 1. Um mes novo acrescenta um ponto.
    check(fetch_data.actualiza_score_history(7.1, _date_hist(2026, 9, 11), _p_hist),
          "uma corrida num mes novo actualiza o historico")
    _h = _le()
    check(len(_h) == 4, f"e o ponto do mes entra ({_h[-1]})")
    check(_h[-1]["month"] == "2026-09", f"com o mes correcto ({_h[-1]})")
    check(_h[-1]["score"] == 7.1, f"e o score da corrida ({_h[-1]})")

    # 2. Uma segunda corrida no MESMO mes CORRIGE, nao duplica: ha quatro ou
    #    cinco sextas por mes, e um ponto por sexta partia a escala mensal.
    check(fetch_data.actualiza_score_history(7.4, _date_hist(2026, 9, 18), _p_hist),
          "uma segunda corrida no mesmo mes mexe no ficheiro")
    _h = _le()
    check(len(_h) == 4, f"mas nao acrescenta um segundo ponto do mes "
                        f"({[x['month'] for x in _h]})")
    check(_h[-1]["score"] == 7.4, f"corrige o valor ({_h[-1]})")
    check(fetch_data.actualiza_score_history(7.4, _date_hist(2026, 9, 25), _p_hist)
          is False, "e uma corrida que nao muda nada nao reescreve o ficheiro")

    # 3. Uma semana n/d NAO escreve ponto nenhum: a ausencia de sinal nao e um
    #    sinal, e inventar um ponto no historico e pior do que uma lacuna.
    check(fetch_data.actualiza_score_history(None, _date_hist(2026, 10, 2), _p_hist)
          is False, "uma semana n/d nao escreve ponto no historico")
    check(len(_le()) == 4, "e o ficheiro fica como estava")
    # Mas LIMPA um ponto futuro na mesma: um ponto que nao devia la estar e um
    # erro a corrigir, e nao ha nada de n/d nisso.
    _esc([{"date": "Aug '26", "score": 6.6, "month": "2026-08"},
          {"date": "Mar '27", "score": 9.9, "month": "2027-03"}])
    check(fetch_data.actualiza_score_history(None, _date_hist(2026, 9, 4), _p_hist),
          "uma semana n/d limpa um ponto de um mes futuro")
    check([x["month"] for x in _le()] == ["2026-08"],
          f"e fica so com o passado ({[x['month'] for x in _le()]})")
    _esc([{"date": "Jun '26", "score": 6.5, "month": "2026-06"},
          {"date": "Jul '26", "score": 6.6, "month": "2026-07"},
          {"date": "Aug '26", "score": 6.7, "month": "2026-08"},
          {"date": "Sep '26", "score": 7.4, "month": "2026-09"}])

    # 4. Um ficheiro ilegivel NAO e substituido por um ponto so — isso apagava
    #    vinte anos de historico por causa de um JSON truncado.
    _Path_hist(_p_hist).write_text("{ isto nao e JSON")
    check(fetch_data.actualiza_score_history(7.4, _date_hist(2026, 11, 6), _p_hist)
          is False, "um historico ilegivel nao e reescrito")
    check(_Path_hist(_p_hist).read_text() == "{ isto nao e JSON",
          "e fica intacto, para ser corrigido a mao")
    # 4b. Um ponto de um mes FUTURO nao existe: e o rasto de um ensaio com uma
    #     data a frente (este proprio ficheiro corre o `build_data` a 400 dias
    #     para verificar a idade da referencia do E/P). A janela `[-24:]`
    #     puxava-o para o grafico, e o site publicava como ultimo ponto do
    #     historico um mes que ainda nao aconteceu, com o score de outro mes —
    #     na capa do produto, com `historicalScoresContiguo: true`.
    _esc([{"date": "Jul '26", "score": 6.5, "month": "2026-07"},
          {"date": "Aug '26", "score": 6.6, "month": "2026-08"},
          {"date": "Mar '27", "score": 9.9, "month": "2027-03"}])
    fetch_data.actualiza_score_history(7.0, _date_hist(2026, 9, 4), _p_hist)
    _h_fut = _le()
    check(all(x["month"] <= "2026-09" for x in _h_fut),
          f"o ponto de um mes futuro e removido do historico "
          f"({[x['month'] for x in _h_fut]})")
    check(_h_fut[-1]["month"] == "2026-09",
          f"e o ultimo passa a ser o mes da corrida ({_h_fut[-1]})")
    # Mas com a folga do RELOGIO, que sao dois dias e nao um mes: no ultimo dia
    # do mes, com o runner adiantado, o ponto do mes seguinte e legitimo e
    # apaga-lo seria uma perda permanente num ficheiro commitado. Um mes inteiro
    # de folga era outra coisa — abria uma janela em que o produtor preservava
    # exactamente o que o portao recusa.
    _esc([{"date": "Aug '26", "score": 6.6, "month": "2026-08"},
          {"date": "Oct '26", "score": 7.1, "month": "2026-10"}])
    fetch_data.actualiza_score_history(7.0, _date_hist(2026, 9, 30), _p_hist)
    check(any(x["month"] == "2026-10" for x in _le()),
          f"a 30 de Setembro, o ponto de Outubro sobrevive — a folga do relogio "
          f"({[x['month'] for x in _le()]})")
    _esc([{"date": "Aug '26", "score": 6.6, "month": "2026-08"},
          {"date": "Oct '26", "score": 7.1, "month": "2026-10"}])
    fetch_data.actualiza_score_history(7.0, _date_hist(2026, 9, 4), _p_hist)
    check(not any(x["month"] == "2026-10" for x in _le()),
          f"mas a 4 de Setembro nao: nao ha relogio nenhum com essa deriva "
          f"({[x['month'] for x in _le()]})")
    # E um ponto sem etiqueta e preservado: um ponto por etiquetar nao e um
    # ponto errado, e apagar historico e sempre pior do que o deixar por
    # arrumar.
    _esc([{"date": "Jul '26", "score": 6.4},
          {"date": "Aug '26", "score": 6.6, "month": "2026-08"}])
    fetch_data.actualiza_score_history(7.0, _date_hist(2026, 9, 4), _p_hist)
    check(len(_le()) == 3,
          f"um ponto sem `month` e preservado, nao apagado ({_le()})")
    # E mesmo que sobreviva no ficheiro que o `build_data` usa, nao chega ao
    # data.json.
    _hist_bd = os.path.join(os.path.dirname(fetch_data.__file__),
                            "score_history.json")
    with open(_hist_bd, "w") as _f_bd:
        json.dump([{"date": "Aug '26", "score": 6.6, "month": "2026-08"},
                   {"date": "Mar '27", "score": 9.9, "month": "2027-03"}], _f_bd)
    _d_fut = fetch_data.build_data(HOJE)
    check(all(x.get("month", "") <= HOJE.strftime("%Y-%m")
              for x in _d_fut["historicalScores"]),
          f"e o data.json nunca publica um ponto de um mes futuro "
          f"({[x.get('month') for x in _d_fut['historicalScores']]})")

    # 4c. E a escrita do historico NAO pode derrubar o `build_data`: ele e o
    #     primeiro job da sexta, e um disco cheio deixaria o data.json por
    #     escrever — e os dois jobs seguintes saltados — por causa de um passo
    #     que nao decide nada.
    # E com o ficheiro impossivel de gravar, o ponto futuro NAO pode chegar ao
    # data.json: a limpeza na escrita nao aconteceu, e o unico filtro que resta
    # e o da LEITURA. Sem ele, o site publicava um mes que ainda nao aconteceu
    # precisamente na semana em que o historico nao pode ser corrigido.
    with open(_hist_bd, "w") as _f_bd:
        json.dump([{"date": "Aug '26", "score": 6.6, "month": "2026-08"},
                   {"date": "Mar '27", "score": 9.9, "month": "2027-03"}], _f_bd)
    _replace_real = os.replace
    def _replace_rebenta(a_, b_):
        if str(b_).endswith("score_history.json"):
            raise OSError(28, "No space left on device")
        return _replace_real(a_, b_)
    os.replace = _replace_rebenta
    try:
        _d_disco = fetch_data.build_data(HOJE)
    finally:
        os.replace = _replace_real
    check(_d_disco.get("globalResilienceScore") is not None,
          "com o historico impossivel de gravar, o build_data corre na mesma")
    check(any(x.get("month") == HOJE.strftime("%Y-%m")
              for x in _d_disco["historicalScores"]),
          f"e o ponto da semana entra em memoria "
          f"({[x.get('month') for x in _d_disco['historicalScores'][-2:]]})")
    check(all(x.get("month", "") <= HOJE.strftime("%Y-%m")
              for x in _d_disco["historicalScores"]),
          f"e o ponto futuro que ficou no ficheiro por gravar NAO chega ao "
          f"data.json ({[x.get('month') for x in _d_disco['historicalScores']]})")

    shutil.rmtree(_dir_hist, ignore_errors=True)

    # E o motivo de um n/d causado por uma SERIE parada e "series" — o outro
    # ramo da mesma propriedade.
    _d_ser = fetch_data.build_data(HOJE)
    if _d_ser.get("ndPillars"):
        check(all(v == "series" for k, v in (_d_ser.get("ndReasons") or {}).items()),
              f"um n/d por serie parada e declarado como tal "
              f"({_d_ser.get('ndReasons')})")

    # ── A ancora do E/P: dois limiares, e o site diz os MESMOS ────────────
    #
    # O capitulo do Premium anunciava um controlo que nao existia ("past 180 the
    # build fails"): com a referencia podre, o pilar continuava a pontuar — e a
    # pontuar 10,0, o maximo da escala. Um pilar cravado no extremo mantem o
    # composto alto e SUPRIME a entrada em Resilient, que roda 100% da carteira.
    # O controlo passa a existir, pela via do protocolo n/d (que nao fecha a
    # sexta), e os numeros do site saem dos mesmos limiares.
    _guardado_ep = fetch_data.SP500_EARNINGS_YIELD_ASOF
    try:
        fetch_data.SP500_EARNINGS_YIELD_ASOF = (
            HOJE - timedelta(days=fetch_data.EP_ND_AFTER_DAYS + 10)).isoformat()
        _d_ep = fetch_data.build_data(HOJE)
        _prem = {x["id"]: x for x in _d_ep["pillars"]}["premium"]
        check(_prem.get("score") is None,
              f"com a referencia do E/P passada do limite n/d, o pilar Premium "
              f"NAO pontua ({_prem.get('score')})")
        check("premium" in (_d_ep.get("ndPillars") or []),
              f"e entra em ndPillars, saindo do composto "
              f"({_d_ep.get('ndPillars')})")
        # E o MOTIVO e publicado. Enquanto o consumidor teve de o adivinhar,
        # escrevia sempre "a serie subjacente nao publicou a tempo" — e com este
        # limiar novo passou a imputar a um fornecedor de dados uma falha que e
        # interna, numa semana em que ele publicou tudo a horas.
        check((_d_ep.get("ndReasons") or {}).get("premium") == "stale-anchor",
              f"e o motivo do n/d e publicado ({_d_ep.get('ndReasons')})")
        # E o NUMERO tambem sai: o site diz, a letra, que passado o prazo a
        # leitura "stops counting as a reading" e que nao se publica como
        # medicao um numero que ninguem verificou. Publicar o score em n/d e
        # continuar a publicar o ERP, a banda e a sentinela calculados sobre ele
        # e cumprir metade da afirmacao.
        check(_prem.get("metricValue") is None,
              f"o ERP do pilar sai em n/d ({_prem.get('metricValue')})")
        check(_prem.get("value") == "n/d",
              f"e o valor mostrado tambem ({_prem.get('value')})")
        check(_prem.get("band") in (None, {}) or _prem.get("band", {}).get("score") is None,
              f"e a banda nao pontua ({_prem.get('band')})")
        _sent_erp = {x["id"]: x for x in _d_ep["sentinels"]}.get("erp") or {}
        check(_sent_erp.get("alert") is False and _sent_erp.get("status") == "nd",
              f"e a sentinela do ERP nao acende sobre um numero que ja nao "
              f"conta como leitura ({_sent_erp.get('status')}, "
              f"alert={_sent_erp.get('alert')})")
        # E logo abaixo do limite continua a pontuar: o controlo tem de ter uma
        # fronteira, nao ser um interruptor sempre ligado.
        fetch_data.SP500_EARNINGS_YIELD_ASOF = (
            HOJE - timedelta(days=fetch_data.EP_ND_AFTER_DAYS - 10)).isoformat()
        _d_ep2 = fetch_data.build_data(HOJE)
        _prem2 = {x["id"]: x for x in _d_ep2["pillars"]}["premium"]
        check(_prem2.get("score") is not None,
              f"e logo abaixo do limite continua a pontuar ({_prem2.get('score')})")
        check(_prem2.get("epAnchor", {}).get("stale") is True,
              "declarando-se velha, que e o primeiro limiar")
        check("premium" not in (_d_ep2.get("ndReasons") or {}),
              f"e sem n/d nao ha motivo a declarar ({_d_ep2.get('ndReasons')})")
        check(_prem2.get("metricValue") is not None,
              f"e o ERP continua a ser publicado ({_prem2.get('metricValue')})")
    finally:
        fetch_data.SP500_EARNINGS_YIELD_ASOF = _guardado_ep
    # E os numeros que o site anuncia sao os do codigo — nao duas copias que
    # possam divergir. Foi por divergirem que o site anunciou durante meses um
    # controlo que nao existia.
    _html_ep = (_Path_hist(__file__).resolve().parent.parent
                / "index.html").read_text(encoding="utf-8")
    for _attr, _valor in (("data-mrm-ep-stale-days", fetch_data.EP_STALE_AFTER_DAYS),
                          ("data-mrm-ep-nd-days", fetch_data.EP_ND_AFTER_DAYS)):
        _m_ep = re.search(rf"<span {_attr}>(\d+)</span>", _html_ep)
        check(_m_ep is not None, f"o site marca o limiar {_attr}")
        check(_m_ep is not None and int(_m_ep.group(1)) == _valor,
              f"e diz o mesmo numero que o codigo ({_m_ep and _m_ep.group(1)} "
              f"vs {_valor})")

    # ── Toda a serie VIGIADA publica a sua data ───────────────────────────
    #
    # O consumidor le a data da ultima observacao daqui, para nao a ter de
    # reconstruir a partir da idade e do seu proprio relogio — reconstruir da
    # coisa diferente numa re-corrida ou numa corrida que atravesse a meia-noite
    # UTC, e o aviso e obrigatorio PALAVRA POR PALAVRA na edicao. Uma serie
    # vigiada sem data aqui e uma serie cujo aviso volta ao calculo fragil.
    _d_sv = fetch_data.build_data(HOJE)
    _datas_sv = (_d_sv.get("meta") or {}).get("fredSeriesDates") or {}
    # O conjunto que interessa e o das series que PODEM aparecer em
    # `fredSeriesStale` — as que este modulo foi mesmo buscar — e nao a tabela
    # de limites, que inclui series lidas por outros modulos (o SAHMREALTIME e
    # do medidor B, e a sua data vai no `stressGauge.triggers[...].asOf`).
    _sem_data = [_s for _s in fetch_data._ULTIMA_DATA if _s not in _datas_sv]
    check(not _sem_data,
          f"toda a serie que este modulo le publica a data da sua ultima "
          f"observacao (faltam: {_sem_data})")
    check("SP500" in _datas_sv,
          f"incluindo a SP500, que e vigiada e nao alimenta pilar nenhum "
          f"({sorted(_datas_sv)})")

    # ── E o ARTEFACTO COMMITADO nao pode vir do futuro ────────────────────
    #
    # Tudo o que esta acima e sobre o produtor com input sintetico. O ficheiro
    # que esta no repositorio e outra coisa: e o que usmrm.net SERVE, e ja
    # aconteceu ser substituido pela saida de um ensaio — leituras fabricadas,
    # com datas de observacao cinco semanas no futuro, apresentadas como
    # leituras da FRED. Nenhum portao olhava para ele.
    #
    # A propriedade e garantida pelo produtor (uma observacao da FRED e sempre
    # do passado), portanto isto nao pode ficar vermelho numa sexta saudavel.
    _raiz_art = _Path_hist(__file__).resolve().parent.parent
    _art = json.loads((_raiz_art / "data.json").read_text(encoding="utf-8"))
    _meta_art = _art.get("meta") or {}
    _carimbo = (_meta_art.get("generatedAt") or _meta_art.get("lastUpdated") or "")[:10]
    check(bool(_carimbo), f"o data.json commitado diz quando foi gerado ({_carimbo!r})")
    _futuras = {k: v for k, v in (_meta_art.get("fredSeriesDates") or {}).items()
                if v and v[:10] > _carimbo}
    check(not _futuras,
          f"nenhuma observacao da FRED no data.json commitado e POSTERIOR a data "
          f"em que ele foi gerado ({_carimbo}): {_futuras}")
    _meses_art = [x.get("month") for x in (_art.get("historicalScores") or [])
                  if isinstance(x, dict)]
    _mes_art = _carimbo[:7]
    check(all((m or "") <= _mes_art for m in _meses_art),
          f"nem nenhum ponto do historico publicado ({_mes_art}): {_meses_art[-3:]}")
    _hist_art = json.loads((_raiz_art / "score_history.json").read_text(encoding="utf-8"))
    # A MESMA janela que o produtor usa para limpar, importada dele — nao uma
    # copia. Com uma folga na escrita e zero na verificacao, o unico ponto que
    # este portao acusava era precisamente o unico que o produtor se recusava a
    # limpar: sexta sem carteira e sem newsletter durante semanas, com o job da
    # limpeza travado pelo proprio portao.
    _limite_hist = fetch_data.mes_limite(date.today())
    _fut_hist = [x.get("month") for x in _hist_art
                 if isinstance(x, dict) and (x.get("month") or "") > _limite_hist]
    check(not _fut_hist,
          f"e o score_history.json commitado nao tem pontos posteriores a "
          f"{_limite_hist} — a mesma janela que o produtor limpa ({_fut_hist})")
    # E as duas janelas sao mesmo a mesma: o que o portao acusa, o produtor
    # limpa. Sem isto, uma folga de um lado sem a folga do outro trava as sextas
    # e nao ha corrida que se cure.
    _dir_jan = _Path_hist(_tf_hist.mkdtemp())
    _p_jan = str(_dir_jan / "score_history.json")
    for _delta_m in (1, 2, 3):
        _mes_fut = fetch_data.mes_limite(date.today())
        _ano_f, _m_f = int(_mes_fut[:4]), int(_mes_fut[5:7]) + _delta_m
        _ano_f, _m_f = _ano_f + (_m_f - 1) // 12, (_m_f - 1) % 12 + 1
        _alvo_m = f"{_ano_f:04d}-{_m_f:02d}"
        _Path_hist(_p_jan).write_text(json.dumps([
            {"date": "Aug '26", "score": 6.6, "month": "2026-08"},
            {"date": "X", "score": 9.9, "month": _alvo_m}]))
        fetch_data.actualiza_score_history(7.0, date.today(), _p_jan)
        _restam = [x["month"] for x in json.loads(_Path_hist(_p_jan).read_text())]
        check(_alvo_m not in _restam,
              f"um ponto de {_alvo_m} — que o portao acusaria — e limpo pelo "
              f"produtor ({_restam})")
    shutil.rmtree(_dir_jan, ignore_errors=True)

    # 5. E as lacunas sao DECLARADAS: o grafico desenha os pontos equidistantes,
    #    portanto meses em falta nao podem ser aproximados em silencio.
    check(fetch_data._meses_contiguos(
        [{"month": "2026-06"}, {"month": "2026-07"}, {"month": "2026-08"}]),
        "tres meses seguidos sao contiguos")
    check(not fetch_data._meses_contiguos(
        [{"month": "2026-06"}, {"month": "2026-09"}]),
        "e um salto de tres meses nao e")
    check(fetch_data._meses_contiguos(
        [{"month": "2026-12"}, {"month": "2027-01"}]),
        "a viragem do ano continua a ser um mes")
    check(not fetch_data._meses_contiguos(
        [{"month": "2026-12"}, {"month": "2026-12"}]),
        "e dois pontos do mesmo mes nao sao consecutivos")

    # 5b. E o `build_data` CHAMA-O mesmo. Sem esta asserçao, apagar a chamada
    #     deixava a suite verde e o ficheiro voltava a ficar para tras — que era
    #     o defeito.
    _hist_repo = os.path.join(os.path.dirname(fetch_data.__file__),
                              "score_history.json")
    # O historico e posto num estado DECLARADO antes de medir. Os blocos
    # anteriores deste ficheiro chamam o `build_data` com datas FUTURAS (a idade
    # da referencia do E/P e verificada a 400 dias), e cada uma dessas chamadas
    # deixa la o seu ponto. Copiar "o do repositorio" tambem nao serve: esse
    # ficheiro cresce em producao. Afirmar sobre o rasto dos outros e a familia
    # de defeitos que este repositorio passou trinta rondas a fechar.
    _mes_ant = (HOJE.replace(day=1) - timedelta(days=1))
    _mes_ant2 = (_mes_ant.replace(day=1) - timedelta(days=1))
    with open(_hist_repo, "w") as _f_hist:
        json.dump([{"date": d.strftime("%b '%y"), "score": 6.5,
                    "month": d.strftime("%Y-%m")}
                   for d in (_mes_ant2, _mes_ant)], _f_hist)
    _antes_hist = json.load(open(_hist_repo))
    _d_hist = fetch_data.build_data(HOJE)
    _depois_hist = json.load(open(_hist_repo))
    _mes_hoje = HOJE.strftime("%Y-%m")
    check(any(x.get("month") == _mes_hoje for x in _depois_hist),
          f"o build_data acrescenta o ponto do mes ao score_history.json "
          f"({[x.get('month') for x in _depois_hist[-3:]]})")
    check(len(_depois_hist) >= len(_antes_hist),
          "e nao perde pontos pelo caminho")
    check(_d_hist["historicalScores"][-1].get("month") == _mes_hoje,
          f"e o data.json publica-o como ultimo ponto "
          f"({_d_hist['historicalScores'][-1]})")
    check(_d_hist.get("historicalScoresContiguo") is True,
          f"e a serie publicada fica contigua "
          f"({[x.get('month') for x in _d_hist['historicalScores'][-4:]]})")
    check(_d_hist.get("historicalScoresSpan", "n/d") != "n/d",
          f"e o intervalo desenhado e declarado ({_d_hist.get('historicalScoresSpan')})")

    # 6. O rotulo do grafico sai do que esta DESENHADO. Escrito a mao no HTML,
    #    passou a ser falso no dia em que o historico ficou para tras: o grafico
    #    cobria trinta meses e o titulo dizia vinte e quatro.
    _html_rot = (_Path_hist(__file__).resolve().parent.parent
                 / "index.html").read_text(encoding="utf-8")
    check("24-Month History" not in _html_rot,
          "o index.html nao escreve o intervalo do grafico a mao")
    check("historicalScoresSpan" in _html_rot,
          "le-o do data.json, que o deriva dos pontos que desenha")
    check("historicalScoresContiguo" in _html_rot,
          "e declara a lacuna quando ela existe")

    # ── Os limiares de frescura sao os do modulo partilhado ────────────────
    #
    # O `data_freshness.py` existe porque a mesma pergunta nao pode ter duas
    # respostas em dois ficheiros. O motor e o gerador ja os importavam de la; o
    # PRODUTOR, que e quem os escreve no `data.json`, tinha a sua propria copia.
    # Alargar o limite no modulo partilhado nao tinha efeito nenhum, porque o
    # `limiar_declarado` faz `min(declarado, leitor)` e o documento continuava a
    # declarar o numero antigo.
    # Prova-se MOVENDO a definicao partilhada e voltando a carregar o produtor:
    # uma comparacao de igualdade nao distingue uma copia de uma importacao
    # (`30 is 30` e True em Python), e uma copia com o mesmo numero e exactamente
    # o estado que se quer impedir.
    import data_freshness as _df_c
    import importlib as _il_c
    _guard_c = (_df_c.DATA_WARN_AFTER_HOURS, _df_c.DATA_REFUSE_AFTER_HOURS)
    try:
        _df_c.DATA_WARN_AFTER_HOURS = 31
        _df_c.DATA_REFUSE_AFTER_HOURS = 49
        _fd_c = _il_c.reload(fetch_data)
        _fd_c.__file__ = fetch_data.__file__
        check(_fd_c.DATA_WARN_AFTER_HOURS == 31,
              f"o limiar de aviso vem do modulo partilhado, nao de uma segunda "
              f"copia ({_fd_c.DATA_WARN_AFTER_HOURS})")
        check(_fd_c.DATA_REFUSE_AFTER_HOURS == 49,
              f"e o de recusa tambem ({_fd_c.DATA_REFUSE_AFTER_HOURS})")
    finally:
        (_df_c.DATA_WARN_AFTER_HOURS, _df_c.DATA_REFUSE_AFTER_HOURS) = _guard_c
        _il_c.reload(fetch_data)
        fetch_data.requests.get = fake_requests_get
        fetch_data.fetch_liquidity_percentile = fake_liquidity
        import os as _os_c
        fetch_data.__file__ = _os_c.path.join(str(tmp), "fetch_data.py")
    _d_lim = fetch_data.build_data(HOJE)
    check(_d_lim["meta"]["freshness"]["warnAfterHours"] == _df_c.DATA_WARN_AFTER_HOURS,
          f"e o que o data.json DECLARA e o mesmo "
          f"({_d_lim['meta']['freshness']['warnAfterHours']})")
    check(_d_lim["meta"]["freshness"]["refuseAfterHours"] == _df_c.DATA_REFUSE_AFTER_HOURS,
          f"nos dois ({_d_lim['meta']['freshness']['refuseAfterHours']})")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'TODOS OS TESTES PASSARAM' if not fails else str(len(fails)) + ' TESTE(S) FALHARAM'}")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
