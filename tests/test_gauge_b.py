"""
Testes do mrm_gauge_b.py — o medidor que decide o regime. Sem rede.

O que estes testes protegem: uma serie do FRED que deixa de ser actualizada nao
devolve erro. Devolve HTTP 200 com a ultima observacao que teve, semana apos
semana. Um SAHMREALTIME congelado em 0,20 dava `active: False` para sempre — o
medidor a declarar calma sobre uma leitura de ha anos, e a carteira a sair de
Critical por causa disso. A data ja era publicada; ninguem a lia.
"""
import importlib.util, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("gb", ROOT / "mrm_gauge_b.py")
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(cond, what): eq(bool(cond), True, what)

HOJE = date(2026, 9, 5)

def obs(d, v):
    return {"date": d, "value": str(v)}

def correr(sahm, npl, hoje=HOJE, **kw):
    """compute() com as duas series substituidas. `sahm` e `npl` sao listas de
    observacoes, ja por ordem decrescente de data."""
    def _fetch(series_id, limit, api_key, **_):
        return list(sahm if series_id == "SAHMREALTIME" else npl)
    real, gb._fetch = gb._fetch, _fetch
    try:
        return gb.compute(api_key="x", hoje=hoje, **kw)
    finally:
        gb._fetch = real

# Series frescas e quietas: o estado e calmo, como sempre foi.
SAHM_FRESCO = [obs("2026-08-01", 0.20), obs("2026-07-01", 0.17), obs("2026-06-01", 0.13)]
NPL_FRESCO  = [obs("2026-04-01", 1.55), obs("2026-01-01", 1.50), obs("2025-10-01", 1.45),
               obs("2025-07-01", 1.40), obs("2025-04-01", 1.35), obs("2025-01-01", 1.30)]

r = correr(SAHM_FRESCO, NPL_FRESCO)
eq(r["active"], False, "duas series frescas e quietas: stress OFF")
eq(r["triggers"]["sahmRealtime"]["stale"], False, "Sahm nao esta velho")
eq(r["triggers"]["sahmRealtime"]["ageDays"], 35, "a idade do Sahm e publicada em dias")
eq(r["triggers"]["delinquencyAccel"]["ageDays"], 157, "a idade do DRALACBN e publicada em dias")

# O caso que motivou o trabalho: o Sahm congelou ha anos.
SAHM_MORTO = [obs("2023-01-01", 0.20), obs("2022-12-01", 0.17), obs("2022-11-01", 0.13)]
r = correr(SAHM_MORTO, NPL_FRESCO)
eq(r["active"], None,
   "uma serie parada ao lado de um gatilho quieto NAO e calma — e n/d")
eq(r["triggers"]["sahmRealtime"]["stale"], True, "o gatilho e marcado como parado")
eq(r["triggers"]["sahmRealtime"]["fired"], None, "e nao e avaliado")
true("stale" in r["basis"], f"e a razao di-lo (obtido {r['basis']!r})")
true("days old" in r["basis"], "com a idade da observacao")

# Um gatilho que DISPARA continua a valer mesmo com o outro parado: um sinal
# presente nao precisa do outro para valer.
SAHM_DISPARA = [obs("2026-08-01", 0.62), obs("2026-07-01", 0.55), obs("2026-06-01", 0.40)]
NPL_MORTO = [obs("2019-04-01", 1.55), obs("2019-01-01", 1.50), obs("2018-10-01", 1.45),
             obs("2018-07-01", 1.40), obs("2018-04-01", 1.35), obs("2018-01-01", 1.30)]
r = correr(SAHM_DISPARA, NPL_MORTO)
eq(r["active"], True, "o Sahm a disparar vale mesmo com o DRALACBN parado")
eq(r["triggers"]["delinquencyAccel"]["fired"], None, "e o gatilho parado fica n/d")

# As duas paradas: n/d, com as duas razoes.
r = correr(SAHM_MORTO, NPL_MORTO)
eq(r["active"], None, "as duas paradas: n/d")
true(r["basis"].count("stale") == 2, f"as duas razoes aparecem (obtido {r['basis']!r})")

# Uma serie parada NAO pode fazer sair de Critical: e o mesmo n/d de sempre, que
# o classify_regime resolve mantendo o estado anterior.
import mrm_rules as rules
eq(rules.classify_regime(3.0, None, "Critical"), "Critical",
   "com o medidor em n/d, quem estava em Critical la fica")

# Limites: exactamente no limite ainda vale; um dia depois ja nao.
lim = gb.MAX_OBS_AGE_DAYS["SAHMREALTIME"]
eq(gb.observacao_velha("SAHMREALTIME", str(HOJE - timedelta(days=lim)), HOJE), (False, lim),
   f"exactamente {lim} dias ainda conta")
eq(gb.observacao_velha("SAHMREALTIME", str(HOJE - timedelta(days=lim + 1)), HOJE)[0], True,
   f"{lim + 1} dias ja nao conta")
eq(gb.observacao_velha("SAHMREALTIME", "nao-e-uma-data", HOJE), (True, None),
   "uma data ilegivel nao e de confianca")
eq(gb.observacao_velha("SAHMREALTIME", str(HOJE + timedelta(days=30)), HOJE)[0], True,
   "uma observacao no futuro nao e de confianca")
eq(gb.observacao_velha("SAHMREALTIME", None, HOJE), (False, None),
   "sem data nao ha idade a avaliar — o caminho de serie indisponivel ja trata disso")

# Os limites tem de ter folga sobre a cadencia real de publicacao de cada serie,
# senao o medidor entra em n/d todas as semanas por causa do atraso normal.
true(gb.MAX_OBS_AGE_DAYS["SAHMREALTIME"] >= 90,
     "o limite do Sahm (mensal, ~1 mes de atraso) tem folga")
true(gb.MAX_OBS_AGE_DAYS["DRALACBN"] >= 270,
     "o limite do DRALACBN (trimestral, ~5 meses de atraso) tem folga")

# ── data_freshness.series_paradas: as duas fontes do data.json ────────────
# O ficheiro declara as series paradas em dois sitios — `meta.fredSeriesStale`
# (com a idade) e `meta.freshness.staleSeries` (so os nomes). Uma newsletter
# gerada a partir de um data.json antigo pode ter so o segundo; ler so o
# primeiro fazia o aviso desaparecer sem que nada dissesse porque.
import data_freshness as _df
eq(_df.series_paradas({"fredSeriesStale": {"T10Y2Y": 578}}), [("T10Y2Y", 578)],
   "le a fonte com idade")
eq(_df.series_paradas({"freshness": {"staleSeries": ["ICSA"]}}), [("ICSA", None)],
   "e a fonte so com nomes, sem inventar uma idade")
eq(_df.series_paradas({"fredSeriesStale": {"T10Y2Y": 578},
                       "freshness": {"staleSeries": ["ICSA", "T10Y2Y"]}}),
   [("ICSA", None), ("T10Y2Y", 578)],
   "junta as duas sem duplicar, e a idade conhecida ganha")
eq(_df.series_paradas({}), [], "sem series paradas, nada")
eq(_df.series_paradas(None), [], "e um meta ausente nao rebenta")

# ── O `_fetch` normaliza o que a FRED devolve ─────────────────────────────
#
# Os testes acima substituem o `_fetch` inteiro, portanto nunca correm estas
# duas linhas — e as duas decidem regimes.
#
# A ordem: o `compute` mede a aceleracao da delinquencia com `obs[0]` menos
# `obs[4]`. Se a ordem vier trocada — a API aceita `sort_order` como pedido,
# nao como garantia — a variacao mede-se ao contrario e o gatilho INVERTE-SE
# sem dar erro: numa deterioracao de credito real o medidor fica calado e o
# regime Critical nao e declarado.
#
# O ponto: a FRED publica um valor em falta como ".", e o `compute` faz
# `float(npl_obs[0]["value"])` fora de qualquer try. Um "." levanta ValueError,
# o fetch_data morre, e os dois jobs de sexta sao saltados.
class _RespFake:
    def __init__(self, obs): self._obs = obs
    def raise_for_status(self): pass
    def json(self): return {"observations": self._obs}

_guardado_get = gb.requests.get
try:
    _fora_de_ordem = [
        {"date": "2025-01-01", "value": "1.20"},
        {"date": "2026-04-01", "value": "1.90"},
        {"date": "2025-07-01", "value": "1.40"},
    ]
    gb.requests.get = lambda url, params=None, timeout=None: _RespFake(_fora_de_ordem)
    _obs = gb._fetch("DRALACBN", 6, "k")
    eq([o["date"] for o in _obs],
       ["2026-04-01", "2025-07-01", "2025-01-01"],
       "o _fetch ordena do mais recente para o mais antigo, venha a API como vier")

    _com_ponto = [
        {"date": "2026-04-01", "value": "."},
        {"date": "2026-01-01", "value": ""},
        {"date": "2025-10-01", "value": None},
        {"date": "2025-07-01", "value": "1.40"},
    ]
    gb.requests.get = lambda url, params=None, timeout=None: _RespFake(_com_ponto)
    _obs2 = gb._fetch("DRALACBN", 6, "k")
    eq([o["value"] for o in _obs2], ["1.40"],
       "e deixa cair os valores em falta que a FRED publica como '.', '' ou null")
    # E a prova de que isso importa: o compute converte obs[0] para float sem
    # rede de seguranca.
    _rebentou = False
    try:
        float(_com_ponto[0]["value"])
    except ValueError:
        _rebentou = True
    true(_rebentou, "um '.' nao e um numero — sem o filtro, o fetch_data morre")
finally:
    gb.requests.get = _guardado_get

# ── A janela da delinquencia mede-se pela DATA, nao pela posicao ──────────
#
# `obs[0] - obs[4]` supunha que as seis observacoes pedidas chegavam todas. A
# FRED publica um valor em falta como ".", o `_fetch` filtra-o (e bem), e a
# lista fica com cinco: o `len >= 5` continuava a passar e `obs[4]` deixava de
# ser ha quatro trimestres — passava a ser ha cinco. O limiar publicado e o
# decil 90 da variacao a QUATRO trimestres; uma variacao a cinco comparada com
# ele dispara o medidor, poe a carteira em Critical, vende tudo para o vector
# defensivo — transaccao real — e publica aos subscritores um numero que
# nenhuma janela de quatro trimestres produz.
_NPL_COM_BURACO = [
    {"date": "2026-04-01", "value": "2.20"},
    {"date": "2025-10-01", "value": "1.90"},   # o trimestre de Janeiro veio "."
    {"date": "2025-07-01", "value": "1.70"},
    {"date": "2025-04-01", "value": "1.50"},   # ha um ano
    {"date": "2025-01-01", "value": "1.30"},
]
_g_ant = gb._fetch
try:
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.10"}] if sid == "SAHMREALTIME"
        else list(_NPL_COM_BURACO))
    _r = gb.compute("k", 4.5, 4.5, hoje=date(2026, 9, 11))
    _t = _r["triggers"]["delinquencyAccel"]
    eq(_t["value"], 0.7,
       f"com um trimestre em falta, a variacao continua a ser a de QUATRO "
       f"trimestres (2.20 - 1.50), nao a de cinco ({_t['value']})")
    eq(_t["fired"], False,
       f"e nao dispara: 0.70 esta abaixo do limiar de {gb.NPL_ACCEL_TRIGGER}")
    eq(_r["active"], False, "e o medidor fica desligado")

    # E um disparo genuino continua a disparar.
    _quente = [{"date": "2026-04-01", "value": "2.50"}] + _NPL_COM_BURACO[1:]
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.10"}] if sid == "SAHMREALTIME"
        else list(_quente))
    _r2 = gb.compute("k", 4.5, 4.5, hoje=date(2026, 9, 11))
    eq(_r2["triggers"]["delinquencyAccel"]["value"], 1.0,
       "um agravamento real a quatro trimestres continua a ser medido")
    eq(_r2["active"], True, "e dispara")

    # Sem observacao na janela de um ano, o gatilho fica em n/d — e o protocolo
    # n/d mantem o estado anterior, em vez de comparar com o que houver.
    _sem_janela = [
        {"date": "2026-04-01", "value": "2.20"},
        {"date": "2026-01-01", "value": "2.10"},
        {"date": "2023-04-01", "value": "1.00"},
    ]
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.10"}] if sid == "SAHMREALTIME"
        else list(_sem_janela))
    _r3 = gb.compute("k", 4.5, 4.5, hoje=date(2026, 9, 11))
    eq(_r3["triggers"]["delinquencyAccel"]["value"], None,
       "sem observacao ha cerca de um ano, o gatilho fica em n/d")
    eq(_r3["active"], None, "e o medidor devolve n/d, nao 'calmo'")
finally:
    gb._fetch = _g_ant

# ── Sem o 10Y medido, o sub-regime cai para o lado DEFENSIVO ──────────────
#
# O `fetch_data` poe `_d10_3m = None` sempre que a serie do 10Y devolve menos de
# 55 pontos. Nessa semana nao ha descida confirmada — e o sub-regime que a
# carteira detem decide entre 35% em TLT (FTQ) e a manga curta em SHY (STRESS).
# Cair para FTQ sem ter medido nada seria entregar mais de um terco da carteira
# a duracao longa por falta de dados, e publicar "Gauge B confirms a 10Y
# decline" sobre uma medicao que nao houve.
_g_ant2 = gb._fetch
try:
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.62"}] if sid == "SAHMREALTIME" else [])
    _sem10y = gb.compute("k", None, None, hoje=date(2026, 9, 11))
    eq(_sem10y["active"], True, "o gatilho de Sahm liga o medidor")
    # O medidor DECLARA a ausencia em vez de a substituir por "STRESS".
    #
    # Cair para FTQ sem ter medido nada seria entregar mais de um terco da
    # carteira a duracao longa por falta de dados — e isso continua vedado, pela
    # porta, tanto na entrada fresca como aqui. Mas devolver "STRESS" fazia o
    # consumidor ver uma LEITURA onde nao ha nenhuma: uma carteira ja em FTQ
    # vendia o TLT — 35% dela — por causa de uma falha de rede, que e
    # exactamente a transaccao que o motor se recusa a fazer quando o medidor
    # inteiro fica sem dados. Quem decide o que fazer com a ausencia e quem sabe
    # o que a carteira detem.
    eq(_sem10y["subregime"], None,
       f"e sem o 10Y medido o medidor declara a ausencia, nao inventa uma "
       f"leitura ({_sem10y['subregime']})")
    eq(_sem10y["triggers"]["tenY3m"]["stale"], True,
       "e publica-a como gatilho, para os consumidores a jusante a verem")
    eq(_sem10y["triggers"]["tenY3m"]["value"], None,
       f"sem valor ({_sem10y['triggers']['tenY3m']['value']})")
    true("n/d" in _sem10y["label"] or "N/D" in _sem10y["label"].upper(),
         f"e o rotulo nao promete um dos dois vectores ({_sem10y['label']})")
    # E a PORTA, que e quem decide a carteira: sem leitura nunca se ENTRA em
    # FTQ, mas ja estando la mantem-se — a ausencia nao e uma leitura negativa.
    eq(rules.subregime_from_gauge(None, was_critical_last_week=False)[0],
       "Critical_Stress", "entrada fresca sem leitura vai para o defensivo")
    eq(rules.subregime_from_gauge(None, was_critical_last_week=True,
                                  was_subregime="Critical_Stress")[0],
       "Critical_Stress", "e em Stress, sem leitura, fica em Stress")
    eq(rules.subregime_from_gauge(None, was_critical_last_week=True,
                                  was_subregime="Critical_FTQ")[0],
       "Critical_FTQ",
       "mas em FTQ, sem leitura, NAO se vende o TLT por uma falha de rede")
    eq(rules.subregime_from_gauge(None, was_critical_last_week=True,
                                  was_subregime=None)[0],
       "Critical_Stress", "e sem sub-regime anterior legivel, o lado defensivo")
    eq(rules.subregime_from_gauge("STRESS", was_critical_last_week=True,
                                  was_subregime="Critical_FTQ")[0],
       "Critical_Stress",
       "uma leitura que NAO confirma a descida continua a degradar o FTQ — a "
       "ausencia e que nao")
    # E com o 10Y medido a cair, ai sim.
    _com10y = gb.compute("k", 4.15, 4.47, hoje=date(2026, 9, 11))
    eq(_com10y["subregime"], "FTQ",
       f"com o 10Y a cair 32 bp, o sub-regime e FTQ ({_com10y['subregime']})")
    _sobe = gb.compute("k", 4.47, 4.15, hoje=date(2026, 9, 11))
    eq(_sobe["subregime"], "STRESS",
       "e com o 10Y a subir volta a ser o defensivo — era isto que punha 35% em "
       "TLT em 2022")
    # ── E o `fired` do gatilho e a comparacao que o `threshold` anuncia ─────
    #
    # Era `subregime == "FTQ"`, e o `subregime` so e calculado com o medidor ON.
    # Numa semana CALMA, com a janela medida e muito abaixo do limiar,
    # publicava-se `fired: false` ao lado de "-32 bp, fires at <= -10 bp" — uma
    # contradicao dentro do mesmo cartao, com a mesma forma dos dois gatilhos
    # que os consumidores ja sabem ler.
    _t10 = lambda g_: g_["triggers"]["tenY3m"]
    eq(_t10(_com10y)["fired"], True,
       f"com o 10Y a cair 32 bp o gatilho disparou ({_t10(_com10y)})")
    eq(_t10(_sobe)["fired"], False,
       f"e a subir nao disparou ({_t10(_sobe)})")
    eq(_t10(_sem10y)["fired"], None,
       f"e sem leitura nao ha comparacao nenhuma ({_t10(_sem10y)})")
    # O caso que revelava a divergencia: janela medida, medidor DESLIGADO.
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "-0.07"}] if sid == "SAHMREALTIME" else
        [{"date": "2026-04-01", "value": "1.38"}, {"date": "2025-04-01", "value": "1.44"}])
    _calmo = gb.compute("k", 4.15, 4.47, hoje=date(2026, 9, 11))
    eq(_calmo["active"], False, "medidor desligado")
    eq(_calmo["subregime"], None,
       "e sem Critical nao ha sub-regime nenhum para calcular")
    eq(_t10(_calmo)["value"], -32.0,
       f"mas a janela FOI medida ({_t10(_calmo)['value']} bp)")
    eq(_t10(_calmo)["fired"], True,
       f"e -32 bp esta abaixo de {_t10(_calmo)['threshold']} bp: o gatilho "
       f"disparou, diga o sub-regime o que disser ({_t10(_calmo)})")
    # E o limiar publicado e o do codigo, na mesma unidade do valor.
    eq(_t10(_calmo)["threshold"], gb.TENY_FTQ_BP * 100,
       "o limiar publicado e o do codigo")
    true(_t10(_calmo)["value"] <= _t10(_calmo)["threshold"],
       "e o valor e o limiar estao na mesma unidade e no mesmo sentido")
finally:
    gb._fetch = _g_ant2

# ── O `basis` e o `ndReason` sao o MESMO facto, contado uma so vez ────────
#
# Sao dois narradores da mesma ausencia, e so um conhecia os tres motivos. Com a
# serie a publicar a horas mas sem o trimestre a que se compara — o caso exacto
# para que o `_um_ano_antes` foi escrito, porque a FRED publica um valor em
# falta como "." — o `ndReason` dizia "no-window" e o `basis`, que e a frase que
# o SITE e o PROMPT publicam a letra, dizia "unavailable": a acusacao a FRED que
# duas rondas existiram para tirar dos outros gatilhos.
_g_ant3 = gb._fetch
try:
    # A serie publica, mas falta-lhe a observacao de ha um ano.
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.10"}] if sid == "SAHMREALTIME" else
        [{"date": "2026-04-01", "value": "1.38"}])
    _g_jan = gb.compute("k", 4.15, 4.47, hoje=date(2026, 9, 11))
    _t_jan = _g_jan["triggers"]["delinquencyAccel"]
    eq(_t_jan["ndReason"], "no-window", "o motivo declarado e a janela")
    true("no comparison observation" in _g_jan["basis"],
         f"e o `basis` conta a MESMA coisa ({_g_jan['basis']})")
    true("unavailable" not in _g_jan["basis"],
         f"e NAO acusa a FRED de nao publicar ({_g_jan['basis']})")
    # E a data da observacao mais recente regista-se na mesma: ficando presa
    # dentro do `if` da janela, uma serie parada ha dois anos que tambem nao
    # tivesse comparacao publicava `stale: false` — e a edicao afirmava "the
    # series published" sobre uma serie que nao publica.
    eq(_t_jan["asOf"], "2026-04-01",
       f"a data da ultima observacao publica-se ({_t_jan['asOf']})")
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.10"}] if sid == "SAHMREALTIME" else
        [{"date": "2024-01-01", "value": "1.38"}])
    _g_velha_j = gb.compute("k", 4.15, 4.47, hoje=date(2026, 9, 11))
    _t_velha_j = _g_velha_j["triggers"]["delinquencyAccel"]
    eq(_t_velha_j["stale"], True,
       f"uma serie parada ha dois anos SEM comparacao continua a ser parada "
       f"({_t_velha_j})")
    eq(_t_velha_j["ndReason"], "stale", "e o motivo e esse")
    true("stale" in _g_velha_j["basis"],
         f"e o `basis` di-lo ({_g_velha_j['basis']})")
    # E a falha de rede continua a ser indisponibilidade, nos dois narradores.
    gb._fetch = lambda sid, limit, key, **kw: (
        [] if sid == "SAHMREALTIME" else
        [{"date": "2026-04-01", "value": "1.38"},
         {"date": "2025-04-01", "value": "1.44"}])
    _g_rede_j = gb.compute("k", 4.15, 4.47, hoje=date(2026, 9, 11))
    eq(_g_rede_j["triggers"]["sahmRealtime"]["ndReason"], "unavailable",
       "sem observacoes, o motivo e a indisponibilidade")
    true("Sahm unavailable" in _g_rede_j["basis"],
         f"e o `basis` concorda ({_g_rede_j['basis']})")
finally:
    gb._fetch = _g_ant3

# ── A FRONTEIRA de cada limiar, derivada da constante ─────────────────────
#
# Um piso escrito como `>= X` cujo ensaio nunca passa por X e um piso por
# afirmar: trocar `>=` por `>` sobrevivia a suite inteira em todos eles. E a
# fronteira e o que a regra publicada promete — o site diz, a letra, "a
# confirmed 10Y decline of -10bp OR MORE" e "SAHMREALTIME >= 0.50". As tres
# leituras (limiar-e, limiar, limiar+e) saem da constante, nao de um numero
# escrito a mao ao lado dela.
_g_ant4 = gb._fetch
try:
    _eps = 0.001
    # Sahm: dispara EM 0.50.
    for _v_s, _quer_s in ((gb.SAHM_TRIGGER - _eps, False),
                          (gb.SAHM_TRIGGER, True),
                          (gb.SAHM_TRIGGER + _eps, True)):
        gb._fetch = lambda sid, limit, key, _v=_v_s, **kw: (
            [{"date": "2026-08-01", "value": f"{_v:.6f}"}] if sid == "SAHMREALTIME"
            else [{"date": "2026-04-01", "value": "1.38"},
                  {"date": "2025-04-01", "value": "1.44"}])
        _g_s = gb.compute("k", 4.15, 4.47, hoje=date(2026, 9, 11))
        eq(_g_s["triggers"]["sahmRealtime"]["fired"], _quer_s,
           f"Sahm em {_v_s:.4f} (limiar {gb.SAHM_TRIGGER}): disparo={_quer_s}")
        eq(_g_s["active"], _quer_s,
           f"e o medidor acompanha-o em {_v_s:.4f}")
    # Delinquencia: a variacao a 4 trimestres dispara EM +0.81 pp.
    for _d_n, _quer_n in ((gb.NPL_ACCEL_TRIGGER - 0.01, False),
                          (gb.NPL_ACCEL_TRIGGER, True),
                          (gb.NPL_ACCEL_TRIGGER + 0.01, True)):
        _base_n = 1.00
        gb._fetch = lambda sid, limit, key, _d=_d_n, _b=_base_n, **kw: (
            [{"date": "2026-08-01", "value": "-0.07"}] if sid == "SAHMREALTIME"
            else [{"date": "2026-04-01", "value": f"{_b + _d:.2f}"},
                  {"date": "2025-04-01", "value": f"{_b:.2f}"}])
        _g_n = gb.compute("k", 4.15, 4.47, hoje=date(2026, 9, 11))
        eq(_g_n["triggers"]["delinquencyAccel"]["fired"], _quer_n,
           f"delinquencia em {_d_n:+.2f} pp (limiar {gb.NPL_ACCEL_TRIGGER}): "
           f"disparo={_quer_n}")
    # 10Y: uma descida de EXACTAMENTE 10 bp confirma FTQ — e o que o site
    # promete ("-10bp or more"). Com `<` em vez de `<=`, essa semana punha 20%
    # em SHY em vez de 35% em TLT.
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.62"}] if sid == "SAHMREALTIME"
        else [{"date": "2026-04-01", "value": "1.38"},
              {"date": "2025-04-01", "value": "1.44"}])
    _base10 = 4.50
    # O passo e a RESOLUCAO do numero publicado (0,1 bp): a decisao usa o mesmo
    # numero que o cartao mostra, portanto e a esse nivel que a fronteira existe.
    # Mais fino do que isso pediria ao motor que decidisse sobre um digito que
    # ele nao publica — e era ai que a virgula flutuante fazia um -10,00 bp
    # aparecer como `value: -10.0` ao lado de `fired: false`.
    for _mv, _quer_t in ((gb.TENY_FTQ_BP + 0.001, "STRESS"),
                         (gb.TENY_FTQ_BP, "FTQ"),
                         (gb.TENY_FTQ_BP - 0.001, "FTQ")):
        _g_t = gb.compute("k", round(_base10 + _mv, 6), _base10,
                          hoje=date(2026, 9, 11))
        eq(_g_t["subregime"], _quer_t,
           f"10Y a mover {_mv * 100:+.2f} bp (limiar {gb.TENY_FTQ_BP * 100:.0f} bp): "
           f"{_quer_t}")
        eq(_g_t["triggers"]["tenY3m"]["fired"], _quer_t == "FTQ",
           f"e o gatilho publicado concorda ({_mv * 100:+.2f} bp)")
        # E o NUMERO publicado e o que a decisao usou: um `value: -10.0` ao lado
        # de `fired: false` e uma contradicao dentro do mesmo cartao.
        _t10_f = _g_t["triggers"]["tenY3m"]
        eq(_t10_f["value"] <= _t10_f["threshold"], _t10_f["fired"],
           f"o valor publicado e o limiar publicado explicam o disparo "
           f"({_t10_f['value']} vs {_t10_f['threshold']}, fired={_t10_f['fired']})")
finally:
    gb._fetch = _g_ant4

# ── A TOLERANCIA da janela de um ano: a fronteira, dos dois lados ─────────
#
# `NPL_JANELA_TOLERANCIA_DIAS = 45` e a folga dada ao calendario da publicacao —
# e nenhum caso de teste chegava perto dela: todas as fixtures tinham a
# observacao a exactamente 365 dias, portanto a tolerancia nunca decidia nada.
# Trocar o 45 por 200 nao punha um unico teste vermelho, e o que isso compra e
# uma "variacao a 4 trimestres" medida sobre 18 meses: com a delinquencia a
# subir devagar, a diferenca a 18 meses passa o limiar de 0,81 pp que a 12 meses
# nao passava, o gatilho dispara, o regime vai a Critical e a carteira roda.
# Trocar por 10 tem o efeito simetrico: o trimestre publicado com duas semanas
# de atraso deixa de contar, e o gatilho fica cego numa recessao.
#
# Os offsets sao LITERAIS: derivar a fronteira da constante fazia o teste
# mover-se junto com a mutacao e nada ficava vermelho.
_recente_j = date(2026, 6, 30)
_alvo_j = _recente_j - timedelta(days=365)


def _obs_j(dias_do_alvo):
    """Duas observacoes: a recente, e uma a `dias_do_alvo` do alvo de ha um ano."""
    return [{"date": _recente_j.strftime("%Y-%m-%d"), "value": "2.20"},
            {"date": (_alvo_j + timedelta(days=dias_do_alvo)).strftime("%Y-%m-%d"),
             "value": "1.00"}]


eq(gb.NPL_JANELA_TOLERANCIA_DIAS, 45,
   "a folga declarada da janela sao 45 dias — muda-la e uma decisao")
for _d_j in (0, 45, -45, 30, -30):
    _achou = gb._um_ano_antes(_obs_j(_d_j))
    true(_achou is not None,
         f"uma observacao a {_d_j:+d} dias do alvo esta DENTRO da folga de 45 "
         f"e serve de comparacao ({_achou})")
for _d_j in (46, -46, 100, -100, 200, -200):
    eq(gb._um_ano_antes(_obs_j(_d_j)), None,
       f"e a {_d_j:+d} dias ja esta FORA: nao ha comparacao que valha o limiar")
# E dentro da folga escolhe-se a MAIS PROXIMA, nao a primeira da lista: com um
# trimestre em falta, a quinta entrada deixa de ser ha um ano.
_duas_j = [{"date": _recente_j.strftime("%Y-%m-%d"), "value": "2.20"},
           {"date": (_alvo_j + timedelta(days=40)).strftime("%Y-%m-%d"), "value": "1.40"},
           {"date": (_alvo_j + timedelta(days=5)).strftime("%Y-%m-%d"), "value": "1.00"}]
eq(gb._um_ano_antes(_duas_j)["value"], "1.00",
   "entre duas observacoes dentro da folga, vale a mais proxima do alvo")

# E a consequencia em DINHEIRO, nao so a funcao: com a observacao a 149 dias do
# alvo, nao ha janela — e o gatilho tem de ficar n/d em vez de medir 1,20 pp
# sobre 18 meses e disparar contra um limiar calibrado a 12.
_g_ant_j = gb._fetch
try:
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-06-30", "value": "0.10"}] if sid == "SAHMREALTIME"
        else _obs_j(-149))
    _g_j = gb.compute("k", 4.15, 4.20, hoje=date(2026, 7, 3))
    _t_j = _g_j["triggers"]["delinquencyAccel"]
    eq(_t_j["value"], None,
       f"sem observacao a um ano de distancia, a variacao a 4 trimestres e n/d "
       f"e NAO uma diferenca medida sobre 18 meses ({_t_j})")
    eq(_t_j["ndReason"], "no-window", f"e o motivo declarado e a janela ({_t_j})")
    true(not _t_j["fired"],
         f"e um gatilho sem leitura NAO dispara — 1,20 pp sobre 18 meses nao e "
         f"a grandeza que o limiar de {gb.NPL_ACCEL_TRIGGER} pp mede ({_t_j})")
    # E com a observacao DENTRO da folga a mesma subida ja conta: a regra tem de
    # ser exercitada nos dois sentidos, senao esta verde por nunca medir nada.
    gb._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-06-30", "value": "0.10"}] if sid == "SAHMREALTIME"
        else _obs_j(-45))
    _g_j2 = gb.compute("k", 4.15, 4.20, hoje=date(2026, 7, 3))
    _t_j2 = _g_j2["triggers"]["delinquencyAccel"]
    true(_t_j2["value"] is not None,
         f"a 45 dias do alvo ha janela e a variacao mede-se ({_t_j2})")
    eq(_t_j2["fired"], True,
       f"e +1,20 pp passa o limiar de {gb.NPL_ACCEL_TRIGGER} pp ({_t_j2})")
finally:
    gb._fetch = _g_ant_j

print(f"TODOS OS {ok} TESTES PASSARAM")
