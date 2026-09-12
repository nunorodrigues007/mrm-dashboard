"""
Testes do send_newsletter.py — o contexto, o prompt, o cartão do arquivo e o tweet.

Sem rede e sem chaves: importar o módulo não pode ter efeitos, e as funções
testadas são puras. O que estes testes protegem, acima de tudo, é a propriedade
que faltava ao sistema: a newsletter conta a mesma história que a carteira.
"""
import importlib.util, json, os, shutil, sys
from datetime import date, datetime, timedelta
import re as _re_comp

# O fetch_data recusa importar sem chave — e ficheiro servido publicamente, nao
# pode ter chave embutida. Aqui so se lhe chama uma funcao pura.
os.environ.setdefault("FRED_API_KEY", "test-key-not-used")
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
AGORA = datetime(2026, 9, 11, 22, 0, 0)

def make_data(score=6.97, stress=False, subregime=None, nd=None, idade_horas=2.0,
              paradas=None, motivos=None, sentinelas=None, basis=None,
              score_das_regras=False):
    # O `score` era FIXO em 6.97 qualquer que fosse o `nd`. Isso torna
    # inalcancavel, em todo o ficheiro de testes, o estado em que o produtor
    # ABANDONA o composto: o `global_score` devolve None assim que o peso
    # sobrevivente cai abaixo do minimo — tres pilares em n/d chegam — e e
    # nessa semana que os avisos que falam de renormalizacao mentem. Com
    # `score_das_regras`, o fixture calcula-o como o produtor calcula.
    if score_das_regras:
        _sc = {pid: (None if pid in (nd or []) else 5.5)
               for pid in rules.PILLAR_ORDER}
        score, _nd_calc = rules.global_score(_sc)
        nd = _nd_calc
    gauge = {
        "active": stress, "subregime": subregime,
        # O `basis` na FORMA que o produtor escreve. "n/d" seco nao e nada do
        # que o `mrm_gauge_b` publica: ele nomeia SEMPRE qual dos gatilhos
        # faltou e porque. Com o fixture reduzido, o consumidor que o le nao era
        # exercitado, e a frase escrita a mao ao lado dele ("both triggers
        # unavailable") passou anos a dizer "os dois" no caso em que so um
        # faltou.
        "basis": (basis if basis is not None else
                  ("no trigger active" if stress is False else
                   ("Sahm" if stress else
                    "n/d \u2014 Sahm stale (last observation is 253 days old) "
                    "and the other trigger is quiet; absence of a signal is not "
                    "a signal. Retain previous state."))),
        # Os gatilhos na FORMA que o produtor escreve: `series`, `fired`,
        # `stale`, `decides` e `ndReason`. Sem o `fired`, o consumidor — que
        # dispara sobre "este gatilho NAO foi avaliado" — via `None` em todos e
        # o teste media outra coisa. Um fixture reduzido e um consumidor por
        # testar.
        "triggers": {
            # No produtor o valor e a data sao atribuidos na MESMA linha: sem
            # valor nao ha data. Datar um gatilho sem leitura era uma forma que
            # ele nunca escreve — e era isso que tornava inalcancavel, em todo o
            # ficheiro, o ramo que entregava `as of None` ao modelo.
            "sahmRealtime": {"series": "SAHMREALTIME", "decides": "regime",
                             "value": None if stress is None else (0.62 if stress else -0.07),
                             "fired": None if stress is None else bool(stress),
                             "stale": False,
                             "ndReason": "unavailable" if stress is None else None,
                             "threshold": 0.5,
                             "asOf": None if stress is None else "2026-08-01"},
            "delinquencyAccel": {"series": "DRALACBN", "decides": "regime",
                                 "value": None if stress is None else -0.06,
                                 "fired": None if stress is None else False,
                                 "stale": False,
                                 "ndReason": "unavailable" if stress is None else None,
                                 "threshold": 0.81,
                                 "asOf": None if stress is None else "2026-04-01"},
        },
    }
    return {
        "globalResilienceScore": score,
        "status": "Turbulence",
        "ndPillars": nd or [],
        # O MOTIVO de cada n/d, tal como o produtor o publica. Por omissao, o
        # caso comum: a serie subjacente nao deu leitura.
        "ndReasons": dict(motivos) if motivos is not None
                     else {_p: "series" for _p in (nd or [])},
        "pillars": [
            {"id": "cycle", "name": "Cycle", "score": 5.5, "value": "+0.41%", "status": "caution"},
            {"id": "liquidity", "name": "Liquidity", "score": None if nd else 9.5, "value": "288.3%", "status": "nd" if nd else "critical"},
            {"id": "premium", "name": "Premium", "score": 10.0, "value": "-0.22%", "status": "critical"},
            {"id": "solvency", "name": "Solvency", "score": 2.5, "value": "1.4%", "status": "stable"},
            {"id": "debt", "name": "Debt", "score": 5.5, "value": "11.2%", "status": "caution"},
        ],
        # A FORMA que o produtor publica, nao uma reducao dela: sem `fredSeries`,
        # `value` e `status`, o consumidor que os le nao era exercitado por
        # teste nenhum e o ramo ficava por cobrir com a suite verde. `sentinelas`
        # permite pôr uma delas sem leitura.
        "sentinels": [
            {**s_, **(dict(sentinelas or {}).get(s_["id"]) or {})}
            for s_ in (
                {"id": "jobless", "name": "Initial Jobless Claims",
                 "fredSeries": "ICSA", "value": 206000, "displayValue": "206K",
                 "thresholdDisplay": "275K", "status": "normal",
                 "trend": "rising", "delta": "+2K", "alert": False},
                {"id": "erp", "name": "Equity Risk Premium",
                 "fredSeries": "DGS10", "value": -0.22, "displayValue": "-0.22%",
                 "thresholdDisplay": "0.80%", "status": "alert",
                 "trend": "falling", "delta": "-0.05%", "alert": True},
                {"id": "unemployment", "name": "Unemployment Rate",
                 "fredSeries": "UNRATE", "value": 4.1, "displayValue": "4.1%",
                 "thresholdDisplay": "5.2%", "status": "normal",
                 "trend": "stable", "delta": "+0.0%", "alert": False},
            )
        ],
        "stressGauge": gauge,
        "meta": {
            "generatedAt": (AGORA - timedelta(hours=idade_horas)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "freshness": {"warnAfterHours": 30, "refuseAfterHours": 48,
                          "staleSeries": sorted(paradas or {})},
            "fredSeriesStale": dict(paradas or {}),
        },
    }

def make_portfolio(regime="Turbulence", subregime=None, reason="hold", triggered=False,
                   issue=27):
    key = rules.resolve_etf_map_key(regime, subregime)
    # O vector das regras, e nao um plausivel escrito a mao: desde Set 2026 uma
    # carteira so pode ter o que o regime dela manda ter, e a edicao e recusada
    # se publicar outra coisa. Um fixture com numeros proprios descreveria uma
    # carteira que o motor nunca produz.
    alloc = dict(rules.REGIME_WEIGHTS[key])
    cur = {"issue": issue, "regime": regime, "critical_subregime": subregime,
           "active_etf_map": dict(rules.REGIME_ETF_MAP[key]),
           "bucket_allocation_pct": alloc,
           "portfolio_value": 10617.97, "portfolio_pnl_pct": 6.18,
           "alpha_vs_benchmark_pct": -10.16}
    # A edicao tem de estar no historico: o gerador recusa publicar a carteira
    # de outra semana, que era o que acontecia em todas as edicoes desde a #5.
    hist = [{"issue": issue - 1, "date": "2026-09-04",
             "rebalance_reason": "hold", "rebalance_triggered": False},
            {"issue": issue, "date": "2026-09-11",
             "rebalance_reason": reason, "rebalance_triggered": triggered}]
    return {"current": cur, "history": hist}

PREV = {"globalResilienceScore": 6.72,
        "pillars": [{"id": "cycle", "score": 5.0}, {"id": "liquidity", "score": 9.5},
                    {"id": "premium", "score": 10.0}, {"id": "solvency", "score": 2.5},
                    {"id": "debt", "score": 5.5}]}

D = date(2026, 9, 11)

# ── semana calma ─────────────────────────────────────────────────────────────
c = sn.build_context(make_data(), make_portfolio(), PREV, D, 27, AGORA)
eq(c["regime_label"], "Turbulence", "regime operativo")
eq(c["score_band"], "Turbulence", "banda do score")
eq(c["score_color"], "#F98C4F", "cor da banda")
eq(c["rb_alert"], "HOLD", "motivo em maiusculas")
eq(c["rb_status"], rules.REBALANCE_COPY["hold"], "texto do hold vem das regras")
eq(c["port_etfs"], "SPY | IEF | LQD | PDBC | BIL | VNQ", "instrumentos de Turbulence")
eq(c["gauge_b_line"], "OFF (no trigger active)", "medidor B desligado")
eq(c["pillars_live"], "5/5 Pillars Active", "cinco pilares vivos")
# E o total sai das REGRAS, nao do numero 5 escrito a mao. Com o "5" cravado, o
# dia em que o sistema ganhar um sexto pilar produz uma edicao que diz "5/5
# Pillars Active" e, ao lado, a caixa a explicar que um pilar saiu do composto.
# Prova-se movendo a definicao, que e a unica maneira de distinguir "deriva" de
# "por acaso da o mesmo".
eq(c["pillars_live"],
   f"{len(rules.PILLAR_ORDER)}/{len(rules.PILLAR_ORDER)} Pillars Active",
   "e o total e o das regras")
_ordem_real = rules.PILLAR_ORDER
try:
    rules.PILLAR_ORDER = list(_ordem_real) + ["ficticio"]
    _c_seis = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)
    eq(_c_seis["pillars_live"], "6/6 Pillars Active",
       "com seis pilares declarados, o cabecalho conta seis")
    _c_seis_nd = sn.build_context(make_data(nd=["liquidity"]),
                                  make_portfolio(issue=27), PREV, D, 27, AGORA)
    true(_c_seis_nd["pillars_live"].startswith("5/6 "),
         f"e um em n/d da 5 de 6 ({_c_seis_nd['pillars_live']})")
finally:
    rules.PILLAR_ORDER = _ordem_real
eq(c["wow_score"], "▲ +0.2 WoW", "WoW do score")
has(c["sentinel_line"], "Unemployment Rate: 4.1%", "sentinela do desemprego chega ao prompt")
has(c["sentinel_line"], "Initial Jobless Claims: 206K", "sentinela ICSA pelo id correcto")

p = sn.build_prompt(c)
# Guardado para o bloco da ancora, mais abaixo: o esqueleto tem de trazer a
# ancora NA etiqueta do cartao, senao o modelo obediente devolve, nas tres
# tentativas, uma edicao que a validacao recusa — e a semana fica sem newsletter
# por causa do proprio esqueleto.
_PROMPT_ESQUELETO = p
has(p, "GAUGE B — CONCURRENT STRESS", "o prompt separa os dois medidores")
has(p, "THIS DECIDES THE REGIME", "diz quem decide o regime")
has(p, "Operative regime: Turbulence", "regime operativo no prompt")
# A macro em vigor tem de ir no prompt, e SEPARADA da efectiva.
#
# Enquanto o unico vector de seis buckets do prompt foi a alocacao efectiva, um
# modelo obediente em Critical — a quem a regra 8 manda publicar "a alocacao
# macro que retoma" — nao tinha outra ancora senao o vector de crise que lhe
# era mostrado. Reproduzia-o, o motor lia-o de volta como macro, e a saida de
# Critical executava o vector de crise em instrumentos de Turbulence.
has(p, "Macro allocation on record", "a macro em vigor vai no prompt")
# E um pilar em n/d nao chega ao modelo como "None/10". `.get(k, "N/A")` nao
# apanha um valor NULO — a chave existe e o que la esta e None — e um modelo
# obediente escrevia "None/10" na tabela que vai para os subscritores.
_c_none = sn.build_context(make_data(nd=["liquidity"]), make_portfolio(issue=27),
                           PREV, D, 27, AGORA)
_p_none = sn.build_prompt(_c_none)
hasnt(_p_none, "None/10", "um pilar em n/d nao vai ao modelo como 'None/10'")
hasnt(_p_none, "None |", "nem o seu valor")
# As bandas de alocacao SAIRAM do prompt, e o teste que verificava a sua
# formatacao saiu com elas. Existiam para enquadrar uma escolha do modelo; desde
# que o vector vem do `REGIME_WEIGHTS` nao ha escolha nenhuma para enquadrar, e
# um envelope a volta de um numero fixo so ensinaria o modelo que ha margem.
# O que se verifica agora e o oposto: que o prompt leva o vector EXACTO e que a
# validacao recusa quem dele se afaste.
_c_al = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)
_p_al = sn.build_prompt(_c_al)
hasnt(_p_al, "stay inside these bands", "o prompt ja nao manda bandas ao modelo")
has(_p_al, "Effective allocation now:", "leva o vector que a carteira tem")
eq(_c_al["alloc_efectiva"], dict(make_portfolio(issue=27)["current"]["bucket_allocation_pct"]),
   "e o contexto guarda esse vector para a validacao o comparar com a tabela")

# E a validacao usa-o: uma tabela que se afasta e recusada, uma que bate certo passa.
_al_boa = {b: _c_al["alloc_efectiva"][b] for b in rules.BUCKETS}
eq(rules.allocation_matches(_al_boa, _c_al["alloc_efectiva"])[0], True,
   "a tabela igual a carteira passa")
_al_ma = dict(_al_boa); _al_ma["US_EQUITIES"] = _al_boa["US_EQUITIES"] + 5
eq(rules.allocation_matches(_al_ma, _c_al["alloc_efectiva"])[0], False,
   "cinco pontos a mais em accoes nao passam")
# Meio ponto passa: a tabela e escrita para uma pessoa e arredonda ao inteiro.
_al_red = dict(_al_boa); _al_red["US_EQUITIES"] = round(_al_boa["US_EQUITIES"])
eq(rules.allocation_matches(_al_red, _c_al["alloc_efectiva"])[0], True,
   "arredondar ao inteiro nao e divergir")

has(_p_none, "Score: n/d/10", "vai como n/d, que e o que o resto do sistema usa")
# E a coluna WoW de um pilar em n/d nao pode ser um NUMERO.
#
# `x.get('score') or 0` converte um None em zero, e o guarda do `wow()` — que
# devolve "—" quando falta um dos lados — deixa de ser alcancavel: um pilar que
# passa de 9,5 a n/d aparecia como "▼ -9.5". A regra 5 do prompt manda escrever
# o Deep Dive sobre o MAIOR movimento WoW, portanto um movimento que nunca
# existiu passa a governar o capitulo principal da edicao enviada.
for _linha_nd in _p_none.splitlines():
    if _linha_nd.startswith("- ") and "Score: n/d/10" in _linha_nd:
        true("WoW: \u2014" in _linha_nd,
             f"um pilar em n/d nao traz um movimento WoW inventado ({_linha_nd})")
true(any(l.startswith("- ") and "Score: n/d/10" in l
         for l in _p_none.splitlines()),
     "o ensaio tem mesmo um pilar em n/d no prompt")
# Aqui verificava-se que as bandas anunciadas ao modelo eram as que o motor
# aplicava — duas copias do mesmo par de numeros, e apertar uma sem a outra dava
# um modelo obediente a escrever uma tabela que o motor rejeitava. A duplicacao
# desapareceu com as bandas: o prompt leva UM vector, que e o mesmo objecto que a
# validacao compara. Nao ha duas copias para divergirem.
_al_prompt = [l for l in p.splitlines() if l.strip().startswith("- Effective allocation now:")]
true(len(_al_prompt) == 1, f"o prompt leva a alocacao efectiva uma vez ({_al_prompt})")
for _b in rules.BUCKETS:
    has(_al_prompt[0], f"{_b}:", f"e leva o bucket {_b}")

# A macro em vigor continua a ir ao prompt, mas ja nao e uma memoria do que
# alguma edicao escreveu: e o vector de Turbulence das regras, que e o que a
# carteira volta a executar quando o medidor B desligar. Continuam a ser duas
# linhas distintas, porque em Critical as duas coisas sao mesmo diferentes.
has(p, "resumes when Gauge B stands down",
    "e o prompt diz para que serve — senao e mais um numero")
_i_ef = p.index("Effective allocation now")
_i_ma = p.index("Macro allocation on record")
true(_i_ef != _i_ma, "sao duas linhas distintas, nao a mesma")
for _b in rules.BUCKETS:
    has(c["macro_line"], f"{_b}: {rules.REGIME_WEIGHTS['Turbulence'][_b]:.0f}%",
        f"a macro anunciada e a das regras, em {_b}")
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
c4 = sn.build_context(make_data(stress=None), make_portfolio(), PREV, D, 27, AGORA)
has(c4["gauge_b_line"], "n/d", "estado n/d declarado")
has(c4["sahm_line"], "n/d", "gatilho sem valor nao inventa numero")
# O modelo tem de saber que os dados faltaram — E porque. A frase escrita a mao
# dizia sempre "both triggers unavailable"; mas `active is None` tem DOIS casos,
# e no segundo (um gatilho em falta ao lado de um gatilho quieto, porque a
# ausencia ao lado do silencio nao e calma) um deles FOI lido. A linha passa a
# citar o `basis` que o produtor escreve, que ja distingue os dois.
_d_um_so = make_data(stress=None)
_c_um_so = sn.build_context(_d_um_so, make_portfolio(), PREV, D, 27, AGORA)
has(_c_um_so["gauge_b_line"], "n/d", "o estado n/d continua declarado")
# E uma so vez: o `basis` do produtor ja comeca por "n/d — ", e prefixa-lo outra
# vez dava "n/d — n/d — ..." ao modelo.
eq(_c_um_so["gauge_b_line"].count("n/d \u2014"), 1,
   f"o prefixo n/d nao se duplica ({_c_um_so['gauge_b_line'][:80]})")

# ── E nenhum `None` chega ao modelo como se fosse uma data ────────────────
#
# `t.get("asOf", "n/d")` nao apanha um valor NULO, so uma chave ausente — e o
# produtor ESCREVE a chave com null, porque o valor e a data sao atribuidos na
# mesma linha. O prompt entregava "as of None" ao modelo na mesma edicao em que
# o aviso obrigatorio diz, correctamente, que a leitura nao se obteve. E o mesmo
# defeito ja fechado no "Score: None/10".
_c_sem_data = sn.build_context(make_data(stress=None), make_portfolio(), PREV,
                               D, 27, AGORA)
for _campo_sd in ("sahm_line", "npl_line", "gauge_b_line"):
    _v_sd = str(_c_sem_data.get(_campo_sd) or "")
    true("None" not in _v_sd,
         f"{_campo_sd} nao entrega um None ao modelo ({_v_sd})")
_p_sd = sn.build_prompt(_c_sem_data)
true("as of None" not in _p_sd and "as of null" not in _p_sd,
     "e o prompt inteiro nao tem uma data nula")
true("as of n/d" in _p_sd,
     "declara-a como n/d, que e o que ela e")
# E com leitura, a data verdadeira continua a chegar ao modelo.
_p_com = sn.build_prompt(sn.build_context(make_data(), make_portfolio(), PREV,
                                          D, 27, AGORA))
true("as of 2026-08-01" in _p_com,
     "e com leitura, a data publicada e a do produtor")
true(_d_um_so["stressGauge"]["basis"] in _c_um_so["gauge_b_line"],
     f"e a razao publicada e a do produtor ({_c_um_so['gauge_b_line']})")
true("both triggers" not in sn.build_prompt(_c_um_so),
     "e nao se afirma que os DOIS faltaram quando so um faltou")
true("Sahm stale" in sn.build_prompt(_c_um_so),
     "o modelo sabe que os dados faltaram, e qual deles")
# E quando faltam MESMO os dois, a razao do produtor di-lo.
_c_dois = sn.build_context(
    make_data(stress=None,
              basis="n/d \u2014 Sahm unavailable and \u0394NPL unavailable; "
                    "retain previous state"),
    make_portfolio(), PREV, D, 27, AGORA)
true("Sahm unavailable" in _c_dois["gauge_b_line"]
     and "NPL unavailable" in _c_dois["gauge_b_line"],
     f"com os dois em falta, a linha nomeia os dois ({_c_dois['gauge_b_line']})")

# ── pilar em n/d ─────────────────────────────────────────────────────────────
c5 = sn.build_context(make_data(nd=["liquidity"]), make_portfolio(), PREV, D, 27, AGORA)
eq(c5["pillars_live"], "4/5 Pillars Active (liquidity n/d)", "cabecalho reflecte o n/d")
has(sn.build_prompt(c5), "4/5 Pillars Active", "e chega ao HTML gerado")
hasnt(sn.build_prompt(c5), "5/5 Pillars Active", "o 5/5 escrito a mao desapareceu")

# ── bandas de score ──────────────────────────────────────────────────────────
eq(sn.build_context(make_data(score=3.0), make_portfolio(), PREV, D, 27, AGORA)["score_color"], "#34D058", "score baixo verde")
eq(sn.build_context(make_data(score=9.0), make_portfolio(), PREV, D, 27, AGORA)["score_color"], "#D73A49", "score alto vermelho")
eq(sn.build_context(make_data(score=9.0), make_portfolio(), PREV, D, 27, AGORA)["regime_label"], "Turbulence",
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

# ── a carteira publicada e a DESTA edicao, ou nao se publica ───────────────
# Todas as edicoes desde a #5 publicaram o P&L da semana anterior: o job 2 lia
# o portfolio.json do commit anterior ao push do job 1. A causa esta no
# workflow, mas o gerador nao pode confiar no ficheiro que recebe.
def recusa(pf, issue, porque):
    global ok
    try:
        sn.build_context(make_data(), pf, PREV, D, issue, AGORA)
    except ValueError as e:
        assert "semana passada" in str(e) or "Mesma causa" in str(e), str(e)
        ok += 1
        return
    raise AssertionError(f"devia ter recusado: {porque}")

atrasado = make_portfolio(issue=26)
recusa(atrasado, 27, "historico sem a edicao 27")
sem_hist = {"current": make_portfolio()["current"], "history": []}
recusa(sem_hist, 27, "historico vazio")
desalinhado = make_portfolio(issue=27)
desalinhado["current"]["issue"] = 26
recusa(desalinhado, 27, "current de outra edicao")

# Um `current` SEM o campo issue passava: os numeros publicados vem todos de la.
sem_issue = make_portfolio(issue=27)
del sem_issue["current"]["issue"]
recusa(sem_issue, 27, "current sem numero de edicao")

# E o numero pode bater certo com a semana errada.
data_errada = make_portfolio(issue=27)
data_errada["history"][-1]["date"] = "2026-08-07"
try:
    sn.build_context(make_data(), data_errada, PREV, D, 27, AGORA)
    raise AssertionError("devia ter recusado: entrada com data de outra semana")
except ValueError as e:
    true("a semana nao" in str(e), "recusa uma entrada datada de outra semana")

# e o caminho normal continua a passar, com a entrada certa
cok = sn.build_context(make_data(), make_portfolio(issue=27, reason="stress_on",
                                                   triggered=True), PREV, D, 27)
eq(cok["rb_alert"], "STRESS_ON", "com a edicao certa, publica o que aconteceu nela")

# ── call_model: 200 nao quer dizer resposta completa ─────────────────────────
# Uma geracao que bate no tecto de tokens devolve HTTP 200, stop_reason
# "max_tokens" e o HTML cortado a meio. O codigo anterior lia
# r.json()["content"][0]["text"] e seguia em frente com o fragmento.
_respostas = []
class _FakeResp:
    status_code = 200
    text = ""
    def __init__(self, payload): self._p = payload
    def json(self): return self._p

def _post_falso(payload):
    def post(url, headers=None, json=None, timeout=None):
        _respostas.append(json)
        return _FakeResp(payload)
    return post

_post_real = sn.requests.post
try:
    sn.requests.post = _post_falso({"stop_reason": "max_tokens",
                                    "content": [{"type": "text", "text": "<html><body>corta"}]})
    cortada = ""
    try:
        sn.call_model("prompt", "k", retries=1)
    except RuntimeError as e:
        cortada = str(e)
    true("truncada" in cortada, f"call_model recusa uma resposta truncada (obtido {cortada[:80]!r})")
    true("Nao e publicada nem enviada" in cortada, "e diz que nao vai ser publicada")
    eq(_respostas[0]["max_tokens"], sn.MAX_TOKENS, "pede o tecto de tokens declarado")
    true(sn.MAX_TOKENS >= 16000, "o tecto tem folga sobre a maior edicao ja escrita")

    sn.requests.post = _post_falso({"stop_reason": "end_turn", "content": []})
    vazia = ""
    try:
        sn.call_model("prompt", "k", retries=1)
    except RuntimeError as e:
        vazia = str(e)
    true("sem texto" in vazia, f"call_model recusa uma resposta sem blocos de texto (obtido {vazia[:80]!r})")

    sn.requests.post = _post_falso({"stop_reason": "end_turn",
                                    "content": [{"type": "text", "text": "```html\n<html>ok</html>\n```"}]})
    eq(sn.call_model("prompt", "k", retries=1), "<html>ok</html>",
       "uma resposta completa continua a passar, sem as cercas de markdown")
finally:
    sn.requests.post = _post_real

# ── validate_newsletter: o contrato com quem le a seguir ─────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_newsletter
from fixture_newsletter import edicao

_aviso = ("DATA QUALITY: at least one price this week is a fallback "
          "(previous close), because the fresh quote failed.")
# O contexto declara o score, como o build_context o declara: sem ele, estas
# verificacoes corriam pelo ramo "semana n/d" contra uma edicao que mostra 7,0.
_c = {"issue_number": 30, "avisos": [_aviso], "score": 6.97}
eq(sn.validate_newsletter(edicao(30, avisos=[_aviso]), _c), [],
   "uma edicao completa e coerente passa")

def _porque(html, c=_c):
    return " | ".join(f"[{t}] {m}" for t, m in sn.validate_newsletter(html, c))

true("cortada" in _porque(edicao(30, avisos=[_aviso], fechar=False)),
     "apanha a edicao truncada")
true("parser do motor" in _porque(edicao(30, avisos=[_aviso], com_tabela=False)),
     "apanha a edicao sem tabela de alocacao legivel")
true("parser do motor" in _porque(edicao(30, avisos=[_aviso],
                                         alloc=[("US Equities", 90), ("Cash", 10)])),
     "apanha a alocacao que o motor rejeitaria")
true("aviso de qualidade" in _porque(edicao(30)),
     "apanha a edicao que omite o aviso de qualidade de dados")
true("#30" in _porque(edicao(31, avisos=[_aviso])),
     "apanha a edicao com o numero errado")
eq(sn.validate_newsletter("", _c), [(sn.FALHA_FORMA, "o modelo devolveu texto vazio")],
   "apanha a resposta vazia")

# Os dois tipos de falha sao distinguidos: uma edicao truncada nao tem salvacao,
# uma tabela de alocacao invalida tem.
_tipos = lambda html: sorted({t for t, _ in sn.validate_newsletter(html, _c)})
eq(_tipos(edicao(30, avisos=[_aviso], fechar=False)), [sn.FALHA_FORMA],
   "uma edicao truncada e falha de FORMA")
eq(_tipos(edicao(30, avisos=[_aviso], alloc=[("US Equities", 90), ("Cash", 10)])),
   [sn.FALHA_ALOCACAO], "uma alocacao fora das bandas e falha de ALOCACAO, so isso")
eq(_tipos(edicao(30)), [sn.FALHA_FORMA],
   "um aviso de qualidade em falta e falha de FORMA — nao se publica sem ele")
# O aviso conta como presente mesmo partido por etiquetas e entidades — o que
# importa e o que o leitor ve, nao os bytes.
_ancora = '<div class="score" data-mrm-score="7.0">7.0</div>'
_partido = edicao(30).replace(_ancora,
                              _ancora + "<div><p>DATA QUALITY: at least one "
                              "price this\n  week is a <b>fallback</b> (previous close), because "
                              "the fresh quote failed.</p></div>")
eq(sn.validate_newsletter(_partido, _c), [],
   "o aviso conta mesmo repartido por etiquetas e quebras de linha")

# ── Um gatilho do medidor B parado chega ao subscritor ───────────────────────
_d_parada = make_data()
_d_parada["stressGauge"]["triggers"]["sahmRealtime"].update(
    {"stale": True, "ageDays": 980, "fired": None, "ndReason": "stale"})
_c_parada = sn.build_context(_d_parada, make_portfolio(issue=27), PREV, D, 27, AGORA)
true(any("SAHMREALTIME" in a and "stopped updating" in a for a in _c_parada["avisos"]),
     f"a serie parada vira aviso ao subscritor (obtido {_c_parada['avisos']})")
true("980 days old" in _c_parada["sahm_line"],
     f"e a linha do gatilho di-lo (obtido {_c_parada['sahm_line']!r})")
true("SERIES STALE" in _c_parada["sahm_line"], "marcada como parada, nao como leitura da semana")
# E o prompt leva-o: o aviso e obrigatorio no texto publicado.
true("stopped updating" in sn.build_prompt(_c_parada),
     "o modelo recebe a instrucao de o publicar")
# Sem serie parada, nenhum destes avisos aparece.
_c_ok = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)
true(not any("stopped updating" in a for a in _c_ok["avisos"]),
     "sem series paradas, nenhum aviso destes")

# ── A ancora manual dos earnings tambem apodrece, e tambem tem de se ler ────
# O E/P do pilar Premium sai de uma referencia escrita a mao no fetch_data e
# marcada ao preco do indice. Passado o prazo, os earnings ficaram para tras e o
# pilar — que entra no composto, logo no score, logo na alocacao — diz uma coisa
# que ja nao e verdade. Ate agora, a unica coisa que o assinalava era um [WARN]
# no log de um workflow verde.
# O `epAnchor` sai do PRODUTOR, nao de um dicionario escrito a mao aqui.
#
# A primeira versao deste teste inventou um `basis` — "reference marked to the
# latest index close" — que o fetch_data nunca escreve. O gerador procurava essa
# frase, o ramo verdadeiro nunca corria em producao, e o aviso passava a dizer a
# TODOS os subscritores que a serie do indice estava indisponivel numa semana em
# que estava disponivel. A suite ficava verde: um teste que fabrica o ficheiro
# do produtor nao esta a testar o produtor, esta a testar-se a si proprio.
import fetch_data as _fd
_OBS_INDICE = [{"date": "2026-09-04", "value": "7900.00"},
               {"date": "2026-02-27", "value": "7670.00"}]
_ep_valor, _ep_detalhe = _fd.earnings_yield_now(3.84, "2026-02-27", _OBS_INDICE)
true(_ep_detalhe.get("indexNow") is not None,
     f"o produtor marca o E-P ao ultimo fecho quando ha serie do indice "
     f"({_ep_detalhe.get('basis')})")

_d_ep = make_data()
for _pil in _d_ep["pillars"]:
    if _pil["id"] == "premium":
        _pil["epAsOf"] = "2026-02-27"
        _pil["epAnchor"] = {**_ep_detalhe, "stale": True, "ageDays": 210}
_c_ep = sn.build_context(_d_ep, make_portfolio(issue=27), PREV, D, 27, AGORA)
true(any("earnings reference" in a and "2026-02-27" in a for a in _c_ep["avisos"]),
     f"a ancora velha do E-P vira aviso ao subscritor (obtido {_c_ep['avisos']})")
# A frase leva a DATA e nao a idade: um numero que muda todas as semanas seria
# uma frase nova todas as semanas para o modelo copiar palavra por palavra, e a
# validacao recusa a edicao se falhar.
true(not any("210 days" in a for a in _c_ep["avisos"]),
     "e a frase nao leva um numero que muda todas as semanas")
# E diz a VERDADE sobre o que aconteceu: houve fecho do indice, o E-P foi
# marcado a mercado, e a frase tem de o dizer. A primeira versao do gerador
# procurava uma expressao que o fetch_data nunca escreve — o ramo verdadeiro
# nunca corria e todas as edicoes, a partir do dia em que a ancora ficasse
# velha, afirmavam o contrario do que se tinha passado.
true(any("marked to the latest index close" in a for a in _c_ep["avisos"]),
     f"com fecho do indice o aviso diz que o E-P foi marcado a mercado "
     f"(obtido {_c_ep['avisos']})")
true(not any("index series was unavailable" in a for a in _c_ep["avisos"]),
     "e nao afirma uma indisponibilidade que nao houve")
# E sem a serie do indice a frase muda: nao houve fecho nenhum para marcar, e
# dizer que houve seria mentir ao subscritor na semana em que os dados falharam.
# E o caso sem serie do indice tambem sai do produtor: `earnings_yield_now` com
# a serie vazia e exactamente o que o fetch_data faz quando a FRED nao devolve
# o indice.
_ep_sem_valor, _ep_sem_detalhe = _fd.earnings_yield_now(3.84, "2026-02-27", [])
eq(_ep_sem_detalhe.get("indexNow"), None,
   f"sem serie do indice o produtor nao marca nada ({_ep_sem_detalhe.get('basis')})")
eq(_ep_sem_valor, 3.84, "e o E-P publicado e a referencia crua")

_d_ep2 = make_data()
for _pil in _d_ep2["pillars"]:
    if _pil["id"] == "premium":
        _pil["epAsOf"] = "2026-02-27"
        _pil["epAnchor"] = {**_ep_sem_detalhe, "stale": True}
_c_ep2 = sn.build_context(_d_ep2, make_portfolio(issue=27), PREV, D, 27, AGORA)
true(any("index series was unavailable" in a for a in _c_ep2["avisos"]),
     f"sem a serie do indice o aviso di-lo (obtido {_c_ep2['avisos']})")
true(not any("marked to the latest index close" in a for a in _c_ep2["avisos"]),
     "e nao afirma uma marcacao a mercado que nao aconteceu")
# ── O ficheiro que vai para o site nao pode levar enderecos ───────────────
#
# O `sent_issues.json` e commitado e empurrado para `main`, e a raiz do ramo e
# servida em usmrm.net: gravar aqui os enderecos publicava a lista de
# subscritores e deixava-a no historico do git para sempre. O `brevo_send`
# existe precisamente para nao pôr a lista no cabecalho de cada mensagem — "uma
# exposicao de dados pessoais, nao um detalhe de estilo" — e este ficheiro fazia
# pior. A retoma so precisa de saber SE um endereco ja foi servido.
import tempfile as _tf_marca
_dir_marca = Path(_tf_marca.mkdtemp())
_lista_real = ["ana@exemplo.pt", "Bruno@Exemplo.PT", "carla@exemplo.pt"]
_reg_marca = sn.mark_sent(9, 3, path=str(_dir_marca / "sent_issues.json"),
                          servidos=_lista_real[:1], falhados=_lista_real[1:2],
                          restantes=_lista_real[2:])
_texto_marca = (_dir_marca / "sent_issues.json").read_text(encoding="utf-8")
true("@" not in _texto_marca,
     f"o ficheiro publicado nao leva um unico endereco ({_texto_marca[:200]})")
for _e in _lista_real:
    true(_e.lower() not in _texto_marca.lower(),
         f"nem sequer em minusculas ({_e})")
_ent_marca = _reg_marca["sent"][-1]
for _campo in ("served", "failed", "pending"):
    true(all(sn._MARCA_RE.match(x) for x in _ent_marca.get(_campo, [])),
         f"o campo {_campo} leva so marcas ({_ent_marca.get(_campo)})")
# A marca nao se pode desfazer. Sem esta linha, as asserçoes acima — sem "@", 16
# hexadecimais, distinta, idempotente — sao todas cumpridas por uma "marca" que
# e o proprio endereco em hexadecimal, e o ficheiro publicado passava a levar o
# prefixo de cada endereco com a suite verde. Prende-se a PROPRIEDADE, nao a
# forma: a marca e um digest criptografico do endereco com um prefixo.
import hashlib as _hl_marca
eq(sn.marca_destinatario("ana@exemplo.pt"),
   _hl_marca.sha256(b"mrm:ana@exemplo.pt").hexdigest()[:16],
   "a marca e um digest sha256 do endereco, nao uma codificacao dele")
_marca_ana = sn.marca_destinatario("ana@exemplo.pt")
true(all(_p not in _marca_ana for _p in ("ana", "exemplo", "616e61")),
     f"e nao contem o endereco nem a sua codificacao ({_marca_ana})")

# As entradas HERDADAS tambem sao limpas: o mark_sent so sanitizava a sua, e as
# outras iam intactas para o ficheiro publicado, semana apos semana.
_dir_h = Path(_tf_marca.mkdtemp())
(_dir_h / "sent_issues.json").write_text(json.dumps({"sent": [
    {"issue": 25, "recipients": 3, "served": ["ana@exemplo.pt"], "complete": False,
     "entregas": {"ana@exemplo.pt": "ok"}},       # endereco numa CHAVE
    "uma entrada escrita a mao que nem sequer e um dicionario",
    None,
    {"recipients": 3, "complete": True},          # sem `issue`
], "fila@exemplo.pt": "por servir"}), encoding="utf-8")   # e fora de `sent`
_reg_h = sn.mark_sent(26, 1, path=str(_dir_h / "sent_issues.json"),
                      servidos=["novo@exemplo.pt"])
_texto_h = (_dir_h / "sent_issues.json").read_text(encoding="utf-8")
true("@" not in _texto_h,
     f"as entradas herdadas tambem saem sem enderecos ({_texto_h[:200]})")
true(any(isinstance(e, dict) and e.get("issue") == 25 for e in _reg_h["sent"]),
     "e a entrada herdada legivel e preservada, so que limpa")
true(all(isinstance(e.get("issue"), int)
         for e in _reg_h["sent"] if isinstance(e, dict) and "issue" in e),
     f"e as legiveis ficam todas com o numero em forma canonica ({_reg_h['sent']})")
# E as de forma desconhecida sao PRESERVADAS — nao rebentam nem desaparecem. O
# `mark_sent` corre DEPOIS do primeiro envio, e ate aqui filtrava-as: a linha
# escrita a mao numa recuperacao — a unica prova de que aquela edicao saiu —
# desaparecia do ficheiro commitado, em silencio, na primeira semana boa. O
# `sanear_marca` preserva-as de proposito; os dois lados passam a concordar.
true(any(not isinstance(e, dict) for e in _reg_h["sent"]),
     f"a linha escrita a mao que nem sequer e um dicionario e PRESERVADA "
     f"({_reg_h['sent']})")
true(any(isinstance(e, dict) and "issue" not in e for e in _reg_h["sent"]),
     f"e a entrada sem `issue` tambem ({_reg_h['sent']})")
true(sum(1 for e in _reg_h["sent"] if e is None) >= 0, "e nada rebenta pelo caminho")
shutil.rmtree(_dir_h, ignore_errors=True)

# E com as formas MISTAS do numero da edicao, sem saneamento nenhum a passar
# antes. O filtro aceitava `"issue": "25"` mas preservava a string, e o `sorted`
# comparava-a com o int desta edicao: TypeError levantado DEPOIS de as primeiras
# mensagens terem saido e ANTES de a marca ser gravada — o job morria e a
# re-corrida, sem marca, gerava texto novo e reenviava a lista toda. Estava
# tapado por o `sanear_marca` correr primeiro em `main()`; um guarda contra o
# reenvio nao pode depender de um remedio que pode nao chegar a gravar.
_dir_m = Path(_tf_marca.mkdtemp())
_p_m = str(_dir_m / "sent_issues.json")
(_dir_m / "sent_issues.json").write_text(json.dumps({"sent": [
    {"issue": "25", "recipients": 3, "complete": True},      # string
    {"issue": 26.0, "recipients": 4, "complete": True},      # float
    {"issue": 24, "recipients": 2, "complete": True},        # int
]}), encoding="utf-8")
_erro_m = None
try:
    _reg_m = sn.mark_sent(27, 5, path=_p_m, servidos=["a@exemplo.pt"])
except BaseException as _e_m:                                # noqa: BLE001
    _erro_m = _e_m
eq(_erro_m, None,
   f"o mark_sent aguenta as tres formas do numero da edicao no mesmo ficheiro — "
   f"ele corre DEPOIS do primeiro envio ({_erro_m!r})")
true(all(isinstance(e.get("issue"), int) for e in _reg_m["sent"]),
     f"e normaliza-as todas, em vez de as preservar para o sorted rebentar "
     f"({[e.get('issue') for e in _reg_m['sent']]})")
for _n_m in (24, 25, 26, 27):
    true(bool(sn.already_sent(_n_m, path=_p_m)),
         f"e a edicao {_n_m} continua marcada como enviada — nenhum registo se perde")
shutil.rmtree(_dir_m, ignore_errors=True)

# ── A PROPRIEDADE, nao os tres campos com nome ────────────────────────────
#
# A versao anterior limpava `served`, `failed` e `pending` — e um teste que
# construisse `served` provava que `served` era limpo, mais nada. O RUNBOOK
# manda o operador editar este ficheiro a mao as 22:41 de uma sexta, e
# `recipients` e o nome que convida a receber a lista. O que se garante e "nao
# ha enderecos no ficheiro", venham eles de onde vierem.
_dir_p = Path(_tf_marca.mkdtemp())
# Inclui um endereco numa CHAVE de dicionario: `{"entregas": {"ana@...": "ok"}}`
# e a forma natural de escrever "quem recebeu e o que aconteceu", e o RUNBOOK
# manda mesmo reconstruir este ficheiro a mao. A limpeza percorria a estrutura
# toda mas so os VALORES, e a chave ficava commitada e servida em usmrm.net.
(_dir_p / "sent_issues.json").write_text(json.dumps({"sent": [
    {"issue": 25, "recipients": ["ana@exemplo.pt", "bruno@exemplo.pt"],
     "nota": "faltou o carlos@exemplo.pt", "complete": True,
     "entregas": {"ana@exemplo.pt": "ok", "bruno@exemplo.pt": "falhou"}},
    {"issue": 24, "emails": {"lista": ["diana@exemplo.pt"]}, "complete": True},
    "#23 -> eva@exemplo.pt",
    # E enderecos que NAO sao ASCII. Todos os enderecos deste ficheiro eram
    # `nome@exemplo.pt` sem um unico acento, portanto o alfabeto do reconhecedor
    # nunca decidia nada: `[A-Za-z0-9._%+-]+@` nao encontra match NENHUM em
    # `jose@` com acento — o caractere imediatamente antes do `@` nao esta na
    # classe — e o endereco inteiro era commitado em claro para `main` e servido
    # em usmrm.net. Numa lista de subscritores portuguesa isto nao e um caso
    # exotico: e a metade dos nomes proprios. O mesmo para um dominio acentuado
    # e para um alfabeto que nao seja o latino.
    {"issue": 22, "recipients": ["jos\u00e9@exemplo.pt", "ana@m\u00fcnchen.de"],
     "nota": "e ainda \u5f20\u4f1f@exemplo.cn", "complete": True},
], "fila@exemplo.pt": "por servir",
   "\u00e9lia@exemplo.pt": "por servir"}), encoding="utf-8")
sn.sanear_marca(str(_dir_p / "sent_issues.json"))
_txt_p = (_dir_p / "sent_issues.json").read_text(encoding="utf-8")
true("@" not in _txt_p,
     f"nenhum endereco fica no ficheiro publicado, esteja em que campo estiver "
     f"({_txt_p[:240]})")
for _nome_p in ("ana", "bruno", "carlos", "diana", "eva", "fila",
                "jos", "nchen", "lia", "exemplo", "\u5f20\u4f1f"):
    true(_nome_p not in _txt_p, f"nem o nome de {_nome_p} ({_txt_p[:200]})")
# Nem em JSON escapado: `json.dump` escreve `jos\u00e9@exemplo.pt` com o `@` em
# claro, e uma procura pela forma legivel nao o veria.
true("\\u" not in _txt_p or "@" not in _txt_p,
     f"nem escapado em \\uXXXX ({_txt_p[:240]})")
# E o registo continua a servir: as entradas nao desaparecem.
_reg_p = json.loads(_txt_p)
true(len(_reg_p["sent"]) == 4,
     f"e as quatro entradas sao preservadas, limpas ({_reg_p['sent']})")
shutil.rmtree(_dir_p, ignore_errors=True)

# ── O preco congelado ha tempo a mais DECLARA-SE, e a fronteira decide ────
#
# `PRICE_FROZEN_AFTER_DAYS = 21` existe para distinguir "aproximacao" de "ja nao
# e credivel", e o unico efeito que tinha era um `log.error` no runner do
# GitHub. Ninguem le esse log antes de a edicao sair: um ETF renomeado ou
# retirado de bolsa produzia, semana apos semana, o mesmo aviso morno de sempre,
# e a mutacao 21 -> 210 sobrevivia a suite inteira porque o limiar nao tinha
# consumidor nenhum. Declarar para um log e nao declarar. Agora o veredicto e
# ESTADO PUBLICADO no portfolio.json, e o gerador escreve dois avisos
# diferentes para duas avarias diferentes.
def _pf_congelado(idade):
    _pf = make_portfolio(issue=27)
    _pf["history"][-1].update({
        "valuation_frozen": ["TLT"],
        "valuation_frozen_days": {"TLT": idade},
        "valuation_frozen_dates": {"TLT": str(D - timedelta(days=idade))},
        "valuation_not_credible": ["TLT"] if idade > 21 else [],
        "price_frozen_after_days": 21,
    })
    return _pf


for _idade_c, _credivel in ((7, True), (21, True), (22, False), (300, False)):
    _av_c = sn.build_context(make_data(), _pf_congelado(_idade_c), PREV, D, 27,
                             AGORA)["avisos"]
    _junto_c = " ".join(_av_c)
    true("TLT" in _junto_c,
         f"um preco congelado ha {_idade_c} dias e declarado ({_av_c})")
    eq("not credible" in _junto_c, not _credivel,
       f"congelado ha {_idade_c} dias (limite 21): "
       f"{'aproximacao' if _credivel else 'JA NAO e credivel'} ({_junto_c})")
    eq("is an approximation" in _junto_c, _credivel,
       f"e o aviso morno so sai do lado credivel ({_junto_c})")
    if not _credivel:
        # A DATA, nao a idade: o aviso e copiado palavra por palavra pelo modelo
        # e uma idade la dentro e uma frase nova todas as sextas. O contrato
        # anterior EXIGIA o numero de dias — congelava o comportamento errado.
        true(str(D - timedelta(days=_idade_c)) in _junto_c,
             f"e a edicao diz de quando e o ultimo preco ({_junto_c})")
        true(f"{_idade_c} days" not in _junto_c,
             f"e NAO uma idade que muda todas as semanas ({_junto_c})")
        true("21-day limit" in _junto_c,
             f"e contra que limite ({_junto_c})")
        # E a frase e ESTAVEL: a mesma avaria, uma semana depois, da a mesma
        # frase. Era isto que falhava, e o custo era `FALHA_FORMA` — tres
        # tentativas e nenhuma edicao, durante a avaria que o aviso conta.
        _pf_sem = _pf_congelado(_idade_c)
        _pf_sem["history"][-1]["valuation_frozen_days"] = {"TLT": _idade_c + 7}
        _av_sem = sn.build_context(make_data(), _pf_sem, PREV, D, 27,
                                   AGORA)["avisos"]
        eq([_a for _a in _av_sem if "not credible" in _a],
           [_a for _a in _av_c if "not credible" in _a],
           "a mesma avaria uma semana depois da a MESMA frase, palavra por "
           "palavra — senao o modelo tem de a copiar de novo todas as sextas")

# ── A propriedade verificada na SAIDA, nao no padrao ──────────────────────
#
# Duas rondas seguidas escreveram um reconhecedor a que faltava uma forma real
# de endereco — a primeira um alfabeto ASCII (`jose@` com acento nao casava de
# todo), a segunda uma lista de delimitadores com o apostrofo la dentro, que e
# `atext` legal da RFC 5322 e comum em nomes portugueses e irlandeses. Nos dois
# casos o que ficava publicado nao era o endereco intacto: era o endereco
# PARTIDO AO MEIO, com a primeira parte em claro — pior do que nao limpar nada,
# porque parecia limpo.
#
# A licao nao e "acrescentar o apostrofo a lista". E que a propriedade
# declarada — "nao ha enderecos no ficheiro" — tem de ser verificada no
# RESULTADO. O que se afirma aqui e isso, sobre formas que o proximo padrao
# tambem vai falhar.
for _end_ad in ("ana'silva@exemplo.pt", "o'neill@example.ie",
                "jos\u00e9@exemplo.pt", "ana@m\u00fcnchen.de",
                "\u5f20\u4f1f@example.cn", "a.b+c!d@sub.exemplo.co.uk",
                "ana%maria@exemplo.pt", "ana_maria@exemplo.pt",
                "ana@localhost", "x@y",
                '"ana silva"@exemplo.pt', "Ana Silva <ana@exemplo.pt>",
                "https://usmrm.net/p?u=ana@exemplo.pt"):
    _limpo_ad = sn._sem_enderecos(f"nota: {_end_ad} ficou por servir")
    true("@" not in _limpo_ad,
         f"nenhum `@` sobrevive a limpeza de {_end_ad!r} ({_limpo_ad!r})")
    # E nao chega o `@` desaparecer, nem a parte local INTEIRA desaparecer:
    # nenhum PEDACO dela pode ficar. A asserçao anterior exigia a ausencia da
    # parte local completa, e o que os tres reconhecedores publicaram foi
    # sempre o endereco PARTIDO AO MEIO — `"ana` de `"ana silva"@x.pt`, `ana'`
    # de `ana'silva@x.pt`. Um pedaco conta: a propriedade declarada e mais
    # forte do que a asserçao que a representava.
    _local_ad = _end_ad.split("@")[0].split("/")[-1].split("=")[-1]
    _nu_ad = _local_ad.strip('"')
    for _n_ad in range(len(_nu_ad), 2, -1):
        for _i_ad in range(0, len(_nu_ad) - _n_ad + 1):
            _frag = _nu_ad[_i_ad:_i_ad + _n_ad]
            true(_frag not in _limpo_ad,
                 f"nenhum pedaco da parte local sobrevive: {_frag!r} de "
                 f"{_end_ad!r} aparece em {_limpo_ad!r}")
            break        # o maior fragmento de cada tamanho chega
        if _n_ad <= 3:
            break
# E a rede de saida nao pode ser o unico narrador. Ela garante a PRIVACIDADE
# mesmo com o reconhecedor errado — mas a marca que ela produz e a do token
# inteiro, que nao bate com a que o `marcas()` grava para quem foi servido. Com
# o reconhecedor a falhar, a retoma de uma entrega parcial deixa de reconhecer
# quem ja recebeu, e a segunda passagem SERVE OUTRA VEZ essas pessoas. Portanto
# o reconhecedor tem de acertar SOZINHO em cada forma legal de endereco: a marca
# de um endereco isolado tem de ser exactamente a que o `marca_destinatario` da.
for _end_ex in ("ana'silva@exemplo.pt", "o'neill@example.ie",
                "jos\u00e9@exemplo.pt", "ana@m\u00fcnchen.de",
                "\u5f20\u4f1f@example.cn", "a.b+c!d@sub.exemplo.co.uk",
                "ana%maria@exemplo.pt", "ana_maria@exemplo.pt",
                "ana=maria@exemplo.pt", "ana&co@exemplo.pt",
                '"ana silva"@exemplo.pt'):
    # A marca exacta NAO chega para distinguir os dois narradores: num endereco
    # isolado, a rede de saida produz a mesma marca. Quem diz qual deles agiu e
    # o AVISO que a rede imprime — ela existe para ser um caso excepcional, e um
    # aviso aqui significa que o reconhecedor falhou esta forma.
    import contextlib as _ctx_ad, io as _io_ad
    _cap_ad = _io_ad.StringIO()
    with _ctx_ad.redirect_stdout(_cap_ad):
        _saida_ad = sn._sem_enderecos(f"nota: {_end_ex} ficou por servir")
    eq(_saida_ad, f"nota: {sn.marca_destinatario(_end_ex)} ficou por servir",
       f"o endereco {_end_ex!r} sai marcado, com a marca exacta")
    true("rede de saida" not in _cap_ad.getvalue(),
         f"e foi o RECONHECEDOR a apanhar {_end_ex!r}, nao a rede de saida — "
         f"quando e a rede a agir, a marca e a do token inteiro e deixa de bater "
         f"com a de quem foi servido: a retoma serve essa pessoa outra vez "
         f"({_cap_ad.getvalue().strip()!r})")

# ── `marcas()` e a limpeza tem de dar a MESMA marca ───────────────────────
#
# Eram duas escritas da mesma ideia: o `marcas()` digeria a LINHA INTEIRA e o
# `_sem_enderecos` extraia o endereco de dentro dela. `Ana Silva
# <ana@exemplo.pt>` — a forma que o RUNBOOK convida o operador a escrever numa
# recuperacao as 22:41 de uma sexta — dava aqui uma marca e ali outra. A retoma
# de uma entrega parcial compara os dois conjuntos: nao batiam, e a Ana recebia
# a edicao uma SEGUNDA VEZ.
for _forma_m in ("ana@exemplo.pt", "Ana Silva <ana@exemplo.pt>",
                 " <ana@exemplo.pt> ", "ana@exemplo.pt "):
    eq(sn.marcas([_forma_m]), [sn.marca_destinatario("ana@exemplo.pt")],
       f"a marca de {_forma_m!r} e a marca do ENDERECO — se nao for, a retoma "
       f"nao reconhece quem ja recebeu e serve essa pessoa outra vez")
# E o mesmo endereco, escrito nas quatro formas, colapsa numa marca so.
eq(len(sn.marcas(["ana@exemplo.pt", "Ana Silva <ana@exemplo.pt>",
                  "ANA@Exemplo.PT", " ana@exemplo.pt "])), 1,
   "as quatro formas do mesmo endereco dao UMA marca")
# E uma marca ja feita passa incolume: a segunda passagem da retoma junta o que
# veio do ficheiro (marcado) com o que foi servido agora (enderecos).
_m_feita = sn.marca_destinatario("ana@exemplo.pt")
eq(sn.marcas([_m_feita]), [_m_feita], "uma marca ja feita nao e marcada outra vez")

# ── E a rede de saida, com o reconhecedor DESLIGADO ───────────────────────
#
# A rede so age quando o reconhecedor falha, e um reconhecedor que hoje acerta
# em tudo esconde-a: as duas tapam-se uma a outra e nenhuma fica armada. Aqui
# desliga-se o reconhecedor de proposito, para provar o que a rede faz sozinha —
# marcar o CAMPO INTEIRO. Marcar so o token delimitado por espacos deixava a
# parte citada de fora e publicava `"ana` em claro.
_re_guardado = sn._EMAIL_RE
try:
    sn._EMAIL_RE = _re_comp.compile(r"(?!x)x")      # nunca casa
    for _end_rede in ('"ana silva"@exemplo.pt', "ana@exemplo.pt",
                      "ana'silva@exemplo.pt"):
        _saida_rede = sn._sem_enderecos(f'nota: {_end_rede} ficou')
        true("@" not in _saida_rede,
             f"com o reconhecedor desligado, a rede tira o `@` ({_saida_rede!r})")
        for _frag_r in ("ana", "silva", "exemplo"):
            true(_frag_r not in _saida_rede,
                 f"e nao deixa {_frag_r!r} em claro ({_saida_rede!r}) — marcar so "
                 f"o token delimitado por espacos publicava o endereco partido "
                 f"ao meio, que e pior do que nao limpar nada")
finally:
    sn._EMAIL_RE = _re_guardado

# E o que NAO e endereco fica intacto: uma limpeza histerica come o registo.
for _nao_ad in ("Issue #27: 412 enviados", "2026-09-04T22:05:00Z", "MRM v2.0",
                "re-corrida (2026-09-04)", "ver https://usmrm.net/x",
                "#23 -> servido a toda a gente", "total 105.1% fora de 100"):
    eq(sn._sem_enderecos(_nao_ad), _nao_ad,
       f"o que nao e endereco fica tal e qual ({_nao_ad!r})")

# E limpar as chaves nao pode APAGAR registos. A marca normaliza maiusculas e
# espacos, portanto duas chaves diferentes escritas a mao — "Ana@Exemplo.pt" e
# "ana@exemplo.pt" — davam o mesmo digest e a segunda sobrepunha-se a primeira
# em silencio. E o mesmo principio que ja governa as entradas de forma
# desconhecida: uma limpeza de privacidade nao e um apagador de registos.
_colide = sn._sem_enderecos({"entregas": {"Ana@Exemplo.pt": "ok",
                                          "ana@exemplo.pt": "falhou"}})
eq(len(_colide["entregas"]), 2,
   f"duas chaves que dao a mesma marca ficam as duas no ficheiro ({_colide})")
eq(sorted(_colide["entregas"].values()), ["falhou", "ok"],
   f"e nenhum dos dois valores se perde ({_colide})")
true(all("@" not in _k for _k in _colide["entregas"]),
     f"e nenhuma das duas leva o endereco ({_colide})")

# ── O numero da edicao vale nas tres formas que aparecem na vida real ──────
#
# `27` sai do codigo, `"27"` de um JSON reconstruido a mao, `27.0` de um editor
# distraido. O `already_sent` comparava com `==`: `"27" == 27` e False, a marca
# ficava INERTE, a edicao era gerada de novo — com texto novo, porque quem a
# escreve e um modelo —, publicada por cima e enviada a lista toda uma segunda
# vez. E o `mark_sent` descartava `27.0` que o `already_sent` honrava.
# E `"#27"`: a linha que o RUNBOOK manda o operador procurar no log e
# "Issue #27: 412 enviados", e copia-la da o cardinal. Sem esta forma, a marca
# escrita a mao ficava inerte e a edicao saia uma segunda vez.
for _forma in (27, "27", " 27 ", 27.0, "#27", " #27 "):
    eq(sn.numero_de_edicao(_forma), 27,
       f"o numero da edicao le-se de {_forma!r}")
# E NENHUMA forma pode levantar: o `sanear_marca` e a PRIMEIRA coisa que o
# `main()` faz, e uma excepcao ali mata o job antes de gerar seja o que for —
# exactamente o que aquela funcao existe para nao ser. `"\u00b2".isdigit()` e True
# em Python e `int("\u00b2")` levanta.
for _mau in ("vinte e sete", "27.5", None, True, "", "#", "#27.5", "2#7",
             "\u00b2", "\u2075", "27\u00b2", [], {}, object()):
    eq(sn.numero_de_edicao(_mau), None,
       f"e o que nao e um numero de edicao nao e inventado ({_mau!r})")
for _forma in ("27", 27.0, " 27 ", "#27"):
    _dir_f = Path(_tf_marca.mkdtemp())
    _p_f = str(_dir_f / "sent_issues.json")
    (_dir_f / "sent_issues.json").write_text(json.dumps({"sent": [
        {"issue": _forma, "recipients": 412, "complete": True}]}), encoding="utf-8")
    # SEM sanear primeiro. O `sanear_marca` normaliza o ficheiro e escondia esta
    # asserçao: com ele a correr antes, o `already_sent` recebia sempre um int e
    # a comparacao ingenua passava. Mas o saneamento e um remedio que pode nao
    # chegar a gravar — o push das 22:00 falha, o disco esta cheio, a forma do
    # ficheiro nao e saneavel — e o guarda contra o reenvio nao pode depender
    # dele. O `already_sent` sozinho tem de honrar a marca.
    true(bool(sn.already_sent(27, path=_p_f)),
         f"uma marca escrita como {_forma!r} IMPEDE o reenvio sem saneamento nenhum")
    sn.sanear_marca(_p_f)
    true(bool(sn.already_sent(27, path=_p_f)),
         f"e continua a impedi-lo depois de saneada ({_forma!r})")
    eq(json.loads(Path(_p_f).read_text())["sent"][0]["issue"], 27,
       f"e o saneamento deixa o numero em forma canonica ({_forma!r})")
    shutil.rmtree(_dir_f, ignore_errors=True)
# E o inverso: uma marca de OUTRA edicao, em qualquer das formas, nao pode ser
# lida como esta — senao a semana era dada por enviada sem nunca ter saido.
for _outra in ("26", 26.0, 26):
    _dir_o = Path(_tf_marca.mkdtemp())
    (_dir_o / "sent_issues.json").write_text(json.dumps({"sent": [
        {"issue": _outra, "recipients": 412, "complete": True}]}), encoding="utf-8")
    eq(sn.already_sent(27, path=str(_dir_o / "sent_issues.json")), None,
       f"e a marca da edicao {_outra!r} nao faz passar a 27 por enviada")
    shutil.rmtree(_dir_o, ignore_errors=True)

# E a marca continua a servir para o que existe: reconhecer quem ja recebeu,
# sem olhar a maiusculas nem a espacos.
eq(sn.marca_destinatario(" ANA@Exemplo.pt "), sn.marca_destinatario("ana@exemplo.pt"),
   "a marca nao depende de maiusculas nem de espacos")
true(sn.marca_destinatario("ana@exemplo.pt") != sn.marca_destinatario("bruno@exemplo.pt"),
     "e distingue duas pessoas")
# E marcar duas vezes da o mesmo: a retoma junta o que veio do ficheiro (ja
# marcado) com o que foi servido agora (enderecos).
eq(sn.marcas(sn.marcas(_lista_real)), sn.marcas(_lista_real),
   "marcar o que ja esta marcado nao muda nada — senao a retoma reenviava")
shutil.rmtree(_dir_marca, ignore_errors=True)

# O orcamento de tempo do envio: o caminho esta testado com o relogio injectado,
# mas o VALOR da constante nao estava preso — pô-la a 1 segundo deixava a suite
# verde e fazia a corrida real desistir antes do primeiro destinatario.
# A carga minima: o que ela apanha e o fragmento que sai de uma geracao
# truncada. Sem estar presa, podia descer para um valor que aceita qualquer
# coisa — a assimetria com o orcamento de tempo, que esta preso, era gratuita.
true(6000 <= sn.MIN_NEWSLETTER_CHARS <= 20000,
     f"a carga minima de uma edicao fica entre 6k e 20k caracteres "
     f"(obtido {sn.MIN_NEWSLETTER_CHARS}) — e o que apanha uma geracao truncada")
true(5 * 60 <= sn.SEND_BUDGET_SECONDS <= 20 * 60,
     f"o orcamento de tempo do envio fica entre 5 e 20 minutos "
     f"(obtido {sn.SEND_BUDGET_SECONDS}s) — o job tem 60 min e a lista cresce")

true("earnings reference" in sn.build_prompt(_c_ep),
     "e o modelo recebe a instrucao de o publicar")
true(not any("earnings reference" in a for a in _c_ok["avisos"]),
     "com a ancora fresca, nenhum aviso destes")

# ── Todos os motivos canonicos tem estilo proprio ────────────────────────────
# O `valuation_incomplete_held` foi criado no motor e nunca chegou aqui nem ao
# index.html: a semana em que o motor recusa rebalancar por nao conseguir
# valorizar a carteira era desenhada com o estilo neutro por omissao, sem aviso.
# Este teste falha da proxima vez que se acrescentar um motivo sem o ligar.
_sem_estilo = []
for _r in rules.REBALANCE_COPY:
    _achou = next((v for k, v in sn.REBALANCE_STYLE.items() if _r.startswith(k)), None)
    if _achou is None:
        _sem_estilo.append(_r)
eq(_sem_estilo, [], "todos os motivos de mrm_rules tem estilo em REBALANCE_STYLE")
# E os motivos de "nao rebalanceou porque algo falhou" nao podem ser verdes.
for _r in ("valuation_incomplete_held", "missing_prices_held",
           "aborted_invalid_shares", "no_allocation_available"):
    _cor = next(v for k, v in sn.REBALANCE_STYLE.items() if _r.startswith(k))
    true(_cor[1] == "#F98C4F", f"{_r} e desenhado como aviso, nao como sucesso")
# A lista canonica e publicada para o frontend a partir da mesma fonte.
eq(rules.as_dict()["rebalanceReasons"], sorted(rules.REBALANCE_COPY),
   "as_dict publica a lista canonica de motivos")

# E o ciclo fecha-se do lado do PRODUTOR: todo o motivo que o motor chega a
# escrever no portfolio.json tem de existir na copia canonica. Sem isto, a
# ligacao entre o motor e os tres consumidores (copia, estilo, site) so
# existia para os motivos de que alguem se lembrou: renomear a chave da copia
# deixava a suite verde e a semana era desenhada com o estilo neutro por
# omissao, sem aviso nenhum, que e exactamente o defeito que o
# `valuation_incomplete_held` teve desde o dia em que foi criado.
_fonte_motor = (ROOT / "update_portfolio.py").read_text(encoding="utf-8")
_motivos_motor = set(_re_comp.findall(
    r'rebalance_reason\s*=\s*\(?\s*"([a-z_]+)"', _fonte_motor))
_motivos_motor |= set(_re_comp.findall(r'else\s+"([a-z_]+)"\)', _fonte_motor))
true(len(_motivos_motor) >= 6,
     f"a leitura do motor encontrou mesmo motivos ({sorted(_motivos_motor)}) — "
     f"zero aqui significa que esta regra se desligou sozinha")
# Os motivos que vem do `trigger` (stress_on, semestral, ...) sao os de
# `decide_rebalance`, e esses tambem tem de estar cobertos.
_motivos_motor |= {_m for _m in rules.REBALANCE_COPY}  # sanity: nao remove nada
_orfaos = sorted(_m for _m in _motivos_motor
                 if not any(_m.startswith(_k) for _k in rules.REBALANCE_COPY))
eq(_orfaos, [],
   f"todo o motivo que o motor escreve tem copia canonica — sem ela nao ha "
   f"estilo, nao ha texto no site, e a semana e desenhada como se nada tivesse "
   f"acontecido ({_orfaos})")

# ── generate_newsletter: o que acontece quando as tentativas se esgotam ────
_ctx = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)

_pedidos = []
def _gerador(html_por_tentativa):
    def g(prompt, key):
        _pedidos.append(prompt)
        return html_por_tentativa[min(len(_pedidos) - 1, len(html_por_tentativa) - 1)]
    return g

# Boa a primeira: uma tentativa so.
_pedidos.clear()
_boa = edicao(27)
eq(sn.generate_newsletter(_ctx, "k", gerar=_gerador([_boa])), _boa, "uma edicao boa passa a primeira")
eq(len(_pedidos), 1, "e nao se pede uma segunda")

# Boa a segunda: os problemas da primeira vao no prompt.
_pedidos.clear()
_saida = sn.generate_newsletter(_ctx, "k", gerar=_gerador([edicao(27, fechar=False), _boa]))
eq(_saida, _boa, "a segunda tentativa e aceite")
eq(len(_pedidos), 2, "foram duas tentativas")
true("REJEITADA" in _pedidos[1], "e a segunda leva os motivos da primeira")

# Truncada sempre: NAO se publica.
_pedidos.clear()
_erro = ""
try:
    sn.generate_newsletter(_ctx, "k", gerar=_gerador([edicao(27, fechar=False)]))
except RuntimeError as e:
    _erro = str(e)
true("NAO foi publicada" in _erro, f"uma edicao sempre truncada nao se publica ({_erro[:80]!r})")
true("forma" in _erro.lower(), "e a razao diz que e problema de forma")

# So a alocacao invalida: publica-se, COM a faixa de aviso.
_pedidos.clear()
_ma_alloc = edicao(27, alloc=[("US Equities", 90), ("Cash", 10)])
_pub = sn.generate_newsletter(_ctx, "k", gerar=_gerador([_ma_alloc]))
true(sn.AVISO_ALOCACAO_HTML in _pub,
     "uma edicao inteira com a alocacao invalida e publicada COM a faixa de aviso")
true("will NOT execute them" in _pub, "que diz que o motor nao vai executar aquelas percentagens")
true(_pub.index(sn.AVISO_ALOCACAO_HTML) < _pub.index('<div class="wrapper"'),
     "e a faixa fica no topo, logo a seguir ao <body>, antes do corpo da edicao")
true(_pub.index("<body") < _pub.index(sn.AVISO_ALOCACAO_HTML),
     "dentro do <body>, nao antes dele")
eq(len(_pedidos), sn.NEWSLETTER_TENTATIVAS,
   "so depois de esgotar as tentativas de a corrigir")
# E a faixa nao se duplica se a funcao for chamada duas vezes.
eq(sn.marcar_alocacao_invalida(_pub), _pub, "a faixa nao se duplica")

# ── A idade do proprio data.json chega ao subscritor ──────────────────────
# O motor recusa decidir sobre um ficheiro com mais de 48 horas. A newsletter
# publicava os mesmos numeros sem nunca perguntar a idade: numa re-corrida dias
# depois saia uma edicao a apresentar as leituras da semana passada como se
# fossem desta.
def _avisos(**kw):
    return sn.build_context(make_data(**kw), make_portfolio(issue=27), PREV, D, 27, AGORA)["avisos"]

true(not any("hours old" in a for a in _avisos(idade_horas=2)),
     "com o ficheiro fresco, nenhum aviso de idade")
true(any("did not run on its usual schedule" in a for a in _avisos(idade_horas=36)),
     f"com 36 horas, avisa que a actualizacao nao correu ({_avisos(idade_horas=36)})")
true(any("past the 48-hour limit" in a for a in _avisos(idade_horas=60)),
     f"com 60 horas, diz que passou o limite ({_avisos(idade_horas=60)})")
true(any("not this week's" in a for a in _avisos(idade_horas=60)),
     "e que as leituras nao sao desta semana")

_sem_carimbo = make_data(); _sem_carimbo["meta"].pop("generatedAt")
_c_sc = sn.build_context(_sem_carimbo, make_portfolio(issue=27), PREV, D, 27, AGORA)
true(any("does not say when it was generated" in a for a in _c_sc["avisos"]),
     f"sem carimbo, di-lo ({_c_sc['avisos']})")
# E o ficheiro nao pode ser juiz de si proprio: um limiar frouxo nao desliga o aviso.
_frouxo = make_data(idade_horas=60)
_frouxo["meta"]["freshness"]["refuseAfterHours"] = 9999
_c_fr = sn.build_context(_frouxo, make_portfolio(issue=27), PREV, D, 27, AGORA)
true(any("past the 48-hour limit" in a for a in _c_fr["avisos"]),
     "um data.json que declare um limite enorme nao desliga a proteccao")

# ── Uma serie do medidor A parada tambem chega ao subscritor ──────────────
# O pilar sai do composto e o score RENORMALIZA — ou seja, muda — e isso chegava
# aos subscritores sem uma palavra sobre porque mudou.
_c_pa = sn.build_context(make_data(paradas={"T10Y2Y": 578}, nd=["cycle"]),
                         make_portfolio(issue=27), PREV, D, 27, AGORA)
true(any("T10Y2Y" in a and "stopped updating" in a for a in _c_pa["avisos"]),
     f"a serie parada do medidor A vira aviso ({_c_pa['avisos']})")
# ── E a CONSEQUENCIA tem de ser a verdadeira ─────────────────────────────
#
# O aviso dizia sempre "o pilar saiu do composto e os pesos foram
# renormalizados" — para TODAS as series paradas. Mas a ICSA e uma sentinela e a
# SP500 so marca o E/P a mercado: nenhuma delas alimenta um pilar. Com uma
# delas parada, a edicao publicava, no mesmo documento, "5/5 Pillars Active" e
# uma caixa a dizer que um pilar tinha saido. E o aviso vai ao prompt com a
# ordem de o copiar PALAVRA POR PALAVRA sob pena de recusa: a falsidade era
# obrigatoria, e um modelo que se recusasse a escreve-la deixava a semana sem
# newsletter nenhuma.
true(any("cycle pillar was excluded" in a for a in _c_pa["avisos"]),
     f"com o pilar mesmo em n/d, o aviso NOMEIA-O e diz que saiu ({_c_pa['avisos']})")
for _s_sem_pilar in ("ICSA", "SP500"):
    _c_sp = sn.build_context(make_data(paradas={_s_sem_pilar: 90}),
                             make_portfolio(issue=27), PREV, D, 27, AGORA)
    _av_sp = [a for a in _c_sp["avisos"] if _s_sem_pilar in a]
    true(_av_sp, f"a serie {_s_sem_pilar} parada tambem vira aviso ({_c_sp['avisos']})")
    true(not any("was excluded from the Resilience Score" in a for a in _av_sp),
         f"mas o aviso da {_s_sem_pilar} NAO afirma que um pilar saiu do "
         f"composto — nenhuma delas e a serie NOMEADA de um pilar ({_av_sp})")
    true(not any("renormalised" in a for a in _av_sp),
         f"nem que os pesos foram renormalizados ({_av_sp})")

# ── "Nao alimenta pilar nenhum" e verdade para uma delas so ───────────────
#
# A ICSA e uma sentinela: parada, o composto fica mesmo onde estava. A SP500 e
# o PRECO do E/P do pilar Premium — earnings a dividir por ela. Parada, o
# Premium continua a pontuar, mas pontua sobre o preco de outro dia, e o
# composto e construido sobre esse ponto. A assercao anterior exigia que ambos
# os avisos dissessem "feeds no pillar, so the Resilience Score is unchanged":
# cravava no teste a falsidade que o aviso obrigatorio ia mandar copiar palavra
# por palavra.
# E a segunda metade da frase tem de ser a verdade DESTA semana. Desde que
# existe o quarto estado, uma sentinela cuja serie parou fica em n/d — nao
# "reported on the last reading available", que era o comportamento anterior.
# O aviso vai ao prompt com ordem de o copiar palavra por palavra.
_c_sent_nd = sn.build_context(
    make_data(paradas={"UNRATE": 90},
              sentinelas={"unemployment": {"value": None, "status": "nd",
                                           "displayValue": "n/d", "trend": "nd",
                                           "delta": "n/d"}}),
    make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_sent_nd = [a for a in _c_sent_nd["avisos"] if "UNRATE" in a]
true(_av_sent_nd, f"a UNRATE parada vira aviso ({_c_sent_nd['avisos']})")
true(not any("last reading available" in a for a in _av_sent_nd),
     f"e NAO afirma que a leitura anterior esta a ser reportada ({_av_sent_nd})")
true(any("no reading this week" in a for a in _av_sent_nd),
     f"diz que a sentinela ficou sem leitura ({_av_sent_nd})")
# E com a sentinela AINDA a reportar a leitura anterior, a frase de sempre.
_c_sent_ok = sn.build_context(make_data(paradas={"UNRATE": 90}),
                              make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_sent_ok = [a for a in _c_sent_ok["avisos"] if "UNRATE" in a]
true(any("last reading available" in a for a in _av_sent_ok),
     f"com leitura retida, o aviso volta a dize-lo ({_av_sent_ok})")

# ── E sobre um consumidor DESCONHECIDO nao se afirma nada ─────────────────
#
# A M2SL nao e pilar nem sentinela: alimenta o filtro 3 da Golden Rule. Caia no
# ramo por omissao e a edicao afirmava, sob pena de recusa, "what it feeds is
# reported on the last reading available" — no mesmo dia em que a pagina dizia
# "1 of the 3 entry filters could not be evaluated this week". E a forma exacta
# do defeito historico "icsa" vs "jobless": o mapa serie->consumidor
# incompleto, e o ramo por omissao a AFIRMAR em vez de se calar.
_av_m2 = [a for a in sn.build_context(make_data(paradas={"M2SL": 90}),
                                      make_portfolio(issue=27), PREV, D, 27,
                                      AGORA)["avisos"] if "M2SL" in a]
true(_av_m2, f"a M2SL parada vira aviso ({_av_m2})")
true(any("feeds no pillar" in a for a in _av_m2),
     f"e diz o que se sabe: nao alimenta pilar nenhum ({_av_m2})")
true(not any("last reading available" in a for a in _av_m2),
     f"mas NAO afirma nada sobre um consumidor que nao conhece ({_av_m2})")
true(not any("sentinel" in a for a in _av_m2),
     f"nem lhe chama sentinela, que nao e ({_av_m2})")
# Prova de que o silencio e mesmo por DESCONHECIMENTO e nao por preguica: uma
# serie que E sentinela continua a levar a segunda metade da frase.
true("M2SL" not in [s.get("fredSeries") for s in make_data()["sentinels"]],
     "a M2SL nao e a serie de nenhuma sentinela publicada")

_av_icsa = [a for a in sn.build_context(make_data(paradas={"ICSA": 90}),
                                        make_portfolio(issue=27), PREV, D, 27,
                                        AGORA)["avisos"] if "ICSA" in a]
true(any("feeds no pillar" in a for a in _av_icsa),
     f"a ICSA e sentinela: o aviso pode dizer que nao alimenta pilar ({_av_icsa})")
true(any("unchanged" in a for a in _av_icsa),
     f"e que o score fica inalterado ({_av_icsa})")

_av_sp5 = [a for a in sn.build_context(make_data(paradas={"SP500": 90}),
                                       make_portfolio(issue=27), PREV, D, 27,
                                       AGORA)["avisos"] if "SP500" in a]
true(not any("feeds no pillar" in a for a in _av_sp5),
     f"a SP500 NAO pode dizer que nao alimenta pilar nenhum ({_av_sp5})")
true(not any("Resilience Score is unchanged" in a for a in _av_sp5),
     f"nem que o score esta inalterado — esta construido sobre um preco "
     f"parado ({_av_sp5})")
true(any("premium" in a and "stale price" in a for a in _av_sp5),
     f"diz de que pilar e input e o que lhe aconteceu ({_av_sp5})")

# E a dependencia declarada tem de ser real: se a SP500 deixasse de mexer no
# Premium, esta frase passava a ser a falsidade seguinte. Mede-se no produtor.
true("SP500" in (rules.PILLAR_EXTRA_SERIES.get("premium") or []),
     "a dependencia SP500 -> premium esta declarada nas regras")
true(all(_s_decl not in str(rules.PILLAR_SCORING[_p_decl].get("fredSeries") or "")
         for _p_decl, _ss_decl in rules.PILLAR_EXTRA_SERIES.items()
         for _s_decl in _ss_decl),
     "e nenhuma delas e ao mesmo tempo a serie nomeada do pilar")

# E nenhuma das duas pode dizer "inalterado" numa semana em que o composto
# mexeu por outra razao. Passado o prazo da ancora dos earnings o pilar Premium
# sai do composto; com a serie tambem parada, o aviso obrigatorio afirmava "the
# Resilience Score is unchanged" no mesmo documento cujo cabecalho diz "4/5
# Pillars Active" e cujo score caiu um ponto.
for _s_sem_pilar in ("ICSA", "SP500"):
    _c_sp_nd = sn.build_context(make_data(paradas={_s_sem_pilar: 90}, nd=["premium"]),
                                make_portfolio(issue=27), PREV, D, 27, AGORA)
    _av_sp_nd = [a for a in _c_sp_nd["avisos"] if _s_sem_pilar in a]
    true(not any("Resilience Score is unchanged" in a for a in _av_sp_nd),
         f"com um pilar fora do composto, o aviso da {_s_sem_pilar} NAO afirma "
         f"que o score esta inalterado ({_av_sp_nd})")
    true(any("premium" in a for a in _av_sp_nd),
         f"e nomeia o que saiu ({_av_sp_nd})")
    # E o que a serie alimenta nao muda por haver, ou nao, um pilar em n/d
    # nessa semana. A ordem dos ramos chegava para trocar a frase: com um pilar
    # fora do composto, a SP500 caia no ramo generico e voltava a dizer "It
    # feeds no pillar" — a mesma falsidade, agora escondida atras de uma
    # condicao que so acontece nas semanas mas.
    # E nem o contrario: com o Premium fora do composto, o aviso da SP500 nao
    # pode afirmar que esse pilar "still scored this week". Nao pontuou nada.
    true(not any("still scored this week" in a for a in _av_sp_nd),
         f"o aviso da {_s_sem_pilar} nao diz que um pilar em n/d pontuou "
         f"({_av_sp_nd})")
    eq(any("feeds no pillar" in a for a in _av_sp_nd), _s_sem_pilar == "ICSA",
       f"o que a {_s_sem_pilar} alimenta nao depende de haver pilares em n/d "
       f"({_av_sp_nd})")
# ── Um pilar fora do composto avisa-se por SI, nao por tabela ─────────────
#
# O aviso da exclusao existia so como apendice do aviso de "serie parada". Mas
# a via mais comum para um pilar entrar em n/d nem sequer produz uma serie
# parada: se a FRED devolve a serie VAZIA — avaria, serie descontinuada, chave
# recusada — nao ha observacao nenhuma para marcar como velha, `staleSeries`
# fica vazio, e a edicao saia sem uma unica linha obrigatoria a dizer que o
# score dessa semana foi calculado sobre quatro pilares. O mesmo para o Premium
# com a ancora dos earnings vencida, que nao e serie da FRED nenhuma.
for _pid_nd_t, _mot_nd_t, _pedaco_t in (
        ("liquidity", "series", "FRED series"),
        ("premium", "stale-anchor", "earnings reference")):
    _c_nd = sn.build_context(
        make_data(nd=[_pid_nd_t], motivos={_pid_nd_t: _mot_nd_t}),
        make_portfolio(issue=27), PREV, D, 27, AGORA)
    _av_nd = [a for a in _c_nd["avisos"] if _pid_nd_t in a]
    true(_av_nd,
         f"um pilar em n/d SEM serie parada gera aviso obrigatorio "
         f"({_c_nd['avisos']})")
    true(any("excluded from the Resilience Score" in a for a in _av_nd),
         f"e diz que saiu do composto ({_av_nd})")
    true(any("renormalised" in a for a in _av_nd),
         f"e que os pesos renormalizaram ({_av_nd})")
    true(any(_pedaco_t in a for a in _av_nd),
         f"e diz a CAUSA que o ficheiro declara, nao uma suposicao ({_av_nd})")
    # E a consequencia para a CARTEIRA, que desde que um composto incompleto
    # deixou de decidir o regime ja nao e so de leitura.
    true(any("either direction" in a for a in _av_nd),
         f"e que o regime fica onde esta enquanto faltar a leitura ({_av_nd})")
    true(any("no rebalance was triggered this week" in a for a in _av_nd),
         f"e, numa semana sem transaccao, que nao houve transaccao ({_av_nd})")
    true(not set(" ".join(_av_nd)) & set("&/"),
         f"e sem os caracteres que o modelo reescreve ({_av_nd})")

# ── Mas numa semana em que HOUVE transaccao, nao pode dizer que nao houve ──
#
# "positions were held rather than rotated" e verdade sobre o SCORE — um
# composto incompleto nao move o regime — e falso sobre a SEMANA: o medidor B
# decide Critical sem passar pelo score, e a ultima sexta de Janeiro ou de Junho
# rebalanceia por calendario. Numa dessas semanas a edicao afirmava, palavra por
# palavra e sob pena de recusa, que as posicoes se mantiveram — no mesmo
# documento que anuncia STRESS_ON e a carteira toda trocada. Um modelo que se
# recusasse a escrever a contradicao tres vezes deixava a semana sem newsletter.
for _razao_t, _executou_t in (("stress_on", True), ("semestral_rebalance", True),
                              ("stress_off", True), ("hold", False)):
    _c_tx = sn.build_context(
        make_data(nd=["liquidity"]),
        make_portfolio(issue=27, reason=_razao_t, triggered=_executou_t),
        PREV, D, 27, AGORA)
    _av_tx = [a for a in _c_tx["avisos"] if "liquidity pillar has no reading" in a]
    true(_av_tx, f"o aviso do pilar sai na mesma com {_razao_t} ({_c_tx['avisos']})")
    eq(any("no rebalance was triggered" in a for a in _av_tx), not _executou_t,
       f"com rebalance_triggered={_executou_t} o aviso NAO afirma o contrario "
       f"({_av_tx})")
    # E a parte que e sempre verdade — o score nao move o regime — fica sempre.
    true(any("either direction" in a for a in _av_tx),
         f"e a afirmacao sobre o score mantem-se em {_razao_t} ({_av_tx})")
    # E em caso nenhum se escreve que as posicoes se mantiveram quando rodaram.
    if _executou_t:
        true(not any("were held" in a for a in _av_tx),
             f"nada de 'were held' numa semana com transaccao ({_av_tx})")

# ── E o aviso GEMEO, o do data.json recusado, tinha o mesmo defeito ───────
#
# O `data_refused` impede o SCORE e o MEDIDOR de decidir; o rebalanceamento
# semestral e calendario e executa na mesma. Na ultima sexta de Janeiro ou de
# Junho com o data.json recusado, a edicao anunciava SEMESTRAL_REBALANCE,
# `rb_done: True`, e afirmava — palavra por palavra, sob pena de recusa — que as
# posicoes se mantiveram. Duas vezes por ano.
# E a conclusao sai do MOTIVO, nao do booleano: sob `data_refused` o semestral
# nao e o unico gatilho possivel — uma troca de sub-regime tambem executa — e
# escrever "decided by the calendar" sobre ela seria a falsidade seguinte.
for _razao_dr, _exec_dr in (("hold", False), ("semestral_rebalance", True),
                            ("critical_subregime_switch:Critical_FTQ->Critical_Stress", True)):
    _pf_dr = make_portfolio(issue=27, reason=_razao_dr, triggered=_exec_dr)
    _pf_dr["history"][-1]["data_refused"] = True
    _av_dr = [a for a in sn.build_context(make_data(idade_horas=60.0), _pf_dr,
                                          PREV, D, 27, AGORA)["avisos"]
              if "without fresh data" in a]
    true(_av_dr, f"{_razao_dr}: o aviso do data.json recusado sai ({_av_dr})")
    eq(any("positions were held" in a for a in _av_dr), not _exec_dr,
       f"{_razao_dr}: e so afirma que as posicoes se mantiveram quando se "
       f"mantiveram ({_av_dr})")
    eq(any("semi-annual" in a for a in _av_dr),
       _razao_dr == "semestral_rebalance",
       f"{_razao_dr}: so a semana do semestral e que o e ({_av_dr})")
    if _exec_dr and _razao_dr != "semestral_rebalance":
        true(any(_razao_dr in a for a in _av_dr),
             f"{_razao_dr}: e o motivo verdadeiro fica escrito ({_av_dr})")

    true(not set(" ".join(_av_dr)) & set("&/"),
         f"{_razao_dr}: e sem caracteres que o modelo reescreve ({_av_dr})")

# Sem pilar nenhum em n/d, nenhum destes avisos aparece — senao o teste acima
# passava com um gerador que avisa sempre.
_c_ok_nd = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)
true(not any("has no reading this week" in a for a in _c_ok_nd["avisos"]),
     f"e numa semana com os cinco pilares nao ha aviso de exclusao "
     f"({_c_ok_nd['avisos']})")

# E nao se diz duas vezes: com a serie NOMEADA do pilar parada, o aviso da
# serie ja nomeia a exclusao — repeti-la noutra linha obrigaria o modelo a
# escrever o mesmo facto duas vezes na mesma edicao.
_c_dup = sn.build_context(make_data(nd=["cycle"], paradas={"T10Y2Y": 90}),
                          make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_dup = [a for a in _c_dup["avisos"]
           if "cycle" in a and "excluded from the Resilience Score" in a]
eq(len(_av_dup), 1,
   f"a exclusao do cycle e dita uma vez so ({_c_dup['avisos']})")

# E a serie COMPOSTA — a Liquidez sai de tres series, e o produtor marca-a
# parada sob a chave "NCBEILQ027S_FBCELLQ027S_GDP", que nao e nenhuma das tres.
# Era a UNICA serie composta do sistema e caia no ramo "nao alimenta pilar
# nenhum": a edicao publicava, no mesmo documento, "4/5 Pillars Active

# (liquidity n/d)" e uma caixa a dizer que o composto estava inalterado.
_CHAVE_COMPOSTA = "NCBEILQ027S_FBCELLQ027S_GDP"
_c_comp = sn.build_context(make_data(paradas={_CHAVE_COMPOSTA: 400}, nd=["liquidity"]),
                           make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_comp = [a for a in _c_comp["avisos"] if _CHAVE_COMPOSTA in a]
true(_av_comp, f"a serie composta parada vira aviso ({_c_comp['avisos']})")
true(any("liquidity pillar was excluded" in a for a in _av_comp),
     f"e o aviso NOMEIA o pilar que saiu — a chave composta resolve para ele "
     f"({_av_comp})")
true(not any("feeds no pillar" in a for a in _av_comp),
     f"e nao diz que nao alimenta pilar nenhum ({_av_comp})")
# E a PROPRIEDADE, nao esta chave: toda a chave que o produtor pode pôr em
# `fredSeriesStale` tem de resolver para o pilar certo. Enumerar a mao ja falhou
# uma vez — foi assim que a chave composta ficou de fora.
import mrm_rules as _r_comp
for _pid_c, _spec_c in _r_comp.PILLAR_SCORING.items():
    _partes_c = [x.strip() for x in _re_comp.split(r"[+,]", str(_spec_c.get("fredSeries") or ""))
                 if x.strip()]
    _chave_c = "_".join(_partes_c)
    _c_x = sn.build_context(make_data(paradas={_chave_c: 400}, nd=[_pid_c]),
                            make_portfolio(issue=27), PREV, D, 27, AGORA)
    _av_x = [a for a in _c_x["avisos"] if _chave_c in a]
    true(any(f"{_pid_c} pillar was excluded" in a for a in _av_x),
         f"a chave {_chave_c!r} que o produtor publica resolve para o pilar "
         f"{_pid_c} ({_av_x})")

# E uma serie que alimenta um pilar mas cujo pilar CONTINUOU a pontuar tambem
# nao pode dizer que ele saiu.
_c_viva = sn.build_context(make_data(paradas={"T10Y2Y": 90}),
                           make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_viva = [a for a in _c_viva["avisos"] if "T10Y2Y" in a]
true(not any("was excluded from the Resilience Score" in a for a in _av_viva),
     f"com o pilar a pontuar na mesma, o aviso nao afirma que ele saiu ({_av_viva})")
true(any("still scored this week" in a for a in _av_viva),
     f"e diz o que aconteceu de facto ({_av_viva})")

# ── O aviso da ancora do E/P diz o que ACONTECEU, nao a idade ────────────
#
# Passado o segundo prazo o pilar Premium sai mesmo do composto e os pesos
# renormalizam. O aviso continuava a dizer "may be stale" — que e o que se diz
# quando ele ainda la esta. O leitor via "4/5 Pillars Active", um score um ponto
# abaixo, e uma caixa a dizer "pode estar desactualizado".
_d_anc = make_data(nd=["premium"])
for _p_anc in _d_anc["pillars"]:
    if _p_anc["id"] == "premium":
        _p_anc["score"] = None
        _p_anc["epAnchor"] = {"asOf": "2026-01-01", "ageDays": 260, "stale": True,
                              "indexNow": 5200.0}
_c_anc = sn.build_context(_d_anc, make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_anc = [a for a in _c_anc["avisos"] if "earnings reference" in a]
true(_av_anc, f"a ancora velha vira aviso ({_c_anc['avisos']})")
true(any("was excluded from the Resilience Score" in a for a in _av_anc),
     f"e com o pilar FORA do composto o aviso di-lo ({_av_anc})")
true(not any("may be stale" in a for a in _av_anc),
     f"em vez de dizer que 'pode estar desactualizado' ({_av_anc})")
# E com o pilar ainda DENTRO do composto — so velho, nao expirado — continua a
# ser o aviso de sempre.
_d_anc2 = make_data()
for _p_anc2 in _d_anc2["pillars"]:
    if _p_anc2["id"] == "premium":
        _p_anc2["epAnchor"] = {"asOf": "2026-01-01", "ageDays": 130, "stale": True,
                               "indexNow": 5200.0}
_c_anc2 = sn.build_context(_d_anc2, make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_anc2 = [a for a in _c_anc2["avisos"] if "earnings reference" in a]
true(any("may be stale" in a for a in _av_anc2),
     f"com o pilar ainda no composto, o aviso e o de sempre ({_av_anc2})")
true(not any("was excluded" in a for a in _av_anc2),
     f"e nao afirma uma exclusao que nao houve ({_av_anc2})")

# ── A tabela publicada tem de ser a carteira que existe ───────────────────
#
# Aqui vivia o aviso do "eco": em Critical, a tabela publicada era muitas vezes
# o proprio vector de crise que o prompt mostrara ao modelo, o motor descartava-a
# e retinha a macro anterior, e a edicao tinha de avisar os subscritores de que
# aquilo que lhes dissera nao era o que ia acontecer. Toda essa figura desapareceu
# com o `REGIME_WEIGHTS`: nao ha tabela a descartar porque nao ha tabela a
# executar.
#
# O que ficou no lugar e mais simples e mais forte — a edicao NAO SAI se a tabela
# nao for a carteira. Nao e um aviso a posteriori: e uma recusa antes do envio.
_c_tab = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)
_efectiva = _c_tab["alloc_efectiva"]

_ed_boa = edicao(27, alloc=[(nome, round(_efectiva[b]))
                            for nome, b in fixture_newsletter._NOMES_ALLOC])
eq([t for t, _ in sn.validate_newsletter(_ed_boa, _c_tab)], [],
   "a edicao que publica a carteira que existe passa")

# O desvio move seis pontos de accoes para cash: o total continua em 100, e
# portanto a tabela continua a passar no parser. O que a apanha e a comparacao
# com a carteira — que e exactamente a verificacao nova.
_desviada = dict(_efectiva)
_desviada["US_EQUITIES"] = _efectiva["US_EQUITIES"] + 6
_desviada["CASH"] = _efectiva["CASH"] - 6
_ed_ma = edicao(27, alloc=[(nome, round(_desviada[b]))
                           for nome, b in fixture_newsletter._NOMES_ALLOC])
_probs_tab = sn.validate_newsletter(_ed_ma, _c_tab)
true(any(t == sn.FALHA_ALOCACAO for t, _ in _probs_tab),
     f"e a que inventa seis pontos em accoes e recusada ({_probs_tab})")
true(any("US_EQUITIES" in m for _, m in _probs_tab),
     f"com o bucket e o desvio nomeados ({[m for _, m in _probs_tab]})")

# E em Critical vale o mesmo, contra o vector de crise que a carteira tem.
_c_crit = sn.build_context(make_data(stress=True, subregime="STRESS"),
                           make_portfolio("Critical", "Critical_Stress", "stress_on", True),
                           PREV, D, 27, AGORA)
_ed_crit = edicao(27, alloc=[(nome, round(_c_crit["alloc_efectiva"][b]))
                             for nome, b in fixture_newsletter._NOMES_ALLOC],
                  avisos=_c_crit["avisos"])
true(not any(t == sn.FALHA_ALOCACAO for t, _ in sn.validate_newsletter(_ed_crit, _c_crit)),
     "em Critical passa a edicao que publica o vector de crise que a carteira tem")

# A DATA da ultima observacao, nao a idade em dias: a idade cresce sete dias por
# semana enquanto a avaria durar, e o aviso tem de ser copiado PALAVRA POR
# PALAVRA para a edicao sob pena de FALHA_FORMA. Uma frase nova todas as semanas
# e uma oportunidade nova, todas as semanas, de a edicao ser recusada tres vezes
# e a semana ficar sem newsletter — precisamente durante a avaria que o aviso
# existe para relatar.
_data_578 = (D - timedelta(days=578)).isoformat()
true(any(_data_578 in a for a in _c_pa["avisos"]),
     f"com a DATA da ultima observacao ({_data_578}): {_c_pa['avisos']}")
# E quando o data.json TRAZ a data, e essa que se publica — nao uma
# reconstruida a partir do relogio do gerador, que da outra coisa numa corrida
# que atravesse a meia-noite UTC ou numa re-corrida sobre um data.json de
# ontem.
_d_dt = make_data(paradas={"T10Y2Y": 578}, nd=["cycle"])
_d_dt["meta"]["fredSeriesDates"] = {"T10Y2Y": "2025-01-02"}
_c_dt = sn.build_context(_d_dt, make_portfolio(issue=27), PREV, D, 27, AGORA)
true(any("2025-01-02" in a for a in _c_dt["avisos"]),
     f"a data publicada e a que o data.json declara ({_c_dt['avisos']})")
true(not any(_data_578 in a for a in _c_dt["avisos"]),
     f"e nao a reconstruida a partir da idade ({_c_dt['avisos']})")
true(not any("days old" in a for a in _c_pa["avisos"]),
     f"e nao com a idade, que muda todas as semanas ({_c_pa['avisos']})")
# E a frase e a MESMA uma semana depois, com a serie na mesma parada.
_c_pa2 = sn.build_context(make_data(paradas={"T10Y2Y": 585}, nd=["cycle"]),
                          make_portfolio(issue=28), PREV,
                          D + timedelta(days=7), 28, AGORA + timedelta(days=7))
_av_pa = [a for a in _c_pa["avisos"] if "T10Y2Y" in a]
_av_pa2 = [a for a in _c_pa2["avisos"] if "T10Y2Y" in a]
eq(_av_pa2, _av_pa,
   f"e uma semana depois o aviso e exactamente o mesmo texto "
   f"({_av_pa} vs {_av_pa2})")

# ── E o gatilho do medidor B parado: o mesmo aviso, a mesma regra ─────────
#
# O medidor B e quem decide o regime. Um gatilho por avaliar tem de chegar ao
# subscritor — e, pela mesma razao de cima, com a DATA da observacao e nao com a
# idade. Este aviso nao tinha teste nenhum: podia desaparecer inteiro com a
# suite verde, e a semana em que o regime foi decidido com meio medidor chegava
# aos subscritores sem uma palavra.
_d_gat = make_data()
_d_gat["stressGauge"]["triggers"]["sahmRealtime"].update(
    {"series": "SAHMREALTIME", "stale": True, "ageDays": 400, "fired": None,
     "ndReason": "stale", "asOf": "2025-08-01"})
_c_gat = sn.build_context(_d_gat, make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_gat = [a for a in _c_gat["avisos"] if "Gauge B trigger" in a]
true(_av_gat, f"um gatilho do medidor B parado vira aviso ({_c_gat['avisos']})")
true(any("SAHMREALTIME" in a for a in _av_gat), f"e nomeia a serie ({_av_gat})")
true(any("2025-08-01" in a for a in _av_gat),
     f"com a DATA da ultima observacao ({_av_gat})")
true(not any("days old" in a for a in _av_gat),
     f"e nao com a idade, que muda todas as semanas ({_av_gat})")
true(any("decided without it" in a for a in _av_gat),
     f"e diz o que isso significa para a decisao da semana ({_av_gat})")
# Uma semana depois, com o gatilho na mesma parado, a frase e a mesma.
_d_gat2 = make_data()
_d_gat2["stressGauge"]["triggers"]["sahmRealtime"].update(
    {"series": "SAHMREALTIME", "stale": True, "ageDays": 407, "fired": None,
     "ndReason": "stale", "asOf": "2025-08-01"})
_c_gat2 = sn.build_context(_d_gat2, make_portfolio(issue=28), PREV,
                           D + timedelta(days=7), 28, AGORA + timedelta(days=7))
eq([a for a in _c_gat2["avisos"] if "Gauge B trigger" in a], _av_gat,
   "e uma semana depois o aviso e exactamente o mesmo texto")
# E sem `asOf`, o aviso deriva a data da idade em vez de a publicar.
_d_gat3 = make_data()
_d_gat3["stressGauge"]["triggers"]["sahmRealtime"].update(
    {"series": "SAHMREALTIME", "stale": True, "ageDays": 400, "fired": None,
     "ndReason": "stale"})
_d_gat3["stressGauge"]["triggers"]["sahmRealtime"].pop("asOf", None)
_c_gat3 = sn.build_context(_d_gat3, make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_gat3 = [a for a in _c_gat3["avisos"] if "Gauge B trigger" in a]
true(any((D - timedelta(days=400)).isoformat() in a for a in _av_gat3),
     f"sem asOf, a data e derivada da idade ({_av_gat3})")

# ── E o gatilho que NAO decide o regime nao pode dizer que o decidiu ───────
#
# A janela de 3 meses do 10Y e o terceiro input do medidor e ficou anos por
# publicar. Ela nao decide Critical — decide qual dos DOIS vectores de Critical
# se executa, e a diferenca entre eles sao 35% da carteira em TLT contra 20% em
# SHY. O ciclo dos gatilhos escrevia, para todos, "This week's regime was
# decided without it": falso para este, e obrigatorio palavra por palavra sob
# pena de a edicao ser recusada. O que cada gatilho decide passa a vir
# DECLARADO pelo produtor.
_d_10y = make_data()
_d_10y["stressGauge"]["triggers"]["tenY3m"] = {
    "series": "DGS10", "value": None, "asOf": "2026-06-02",
    "decides": "subregime", "stale": True, "fired": None}
_c_10y = sn.build_context(_d_10y,
                          make_portfolio(issue=27, regime="Critical",
                                         subregime="Critical_FTQ",
                                         reason="hold"),
                          PREV, D, 27, AGORA)
_av_10y = [a for a in _c_10y["avisos"] if "DGS10" in a and "Gauge B trigger" in a]
true(_av_10y, f"a janela do 10Y por medir vira aviso ({_c_10y['avisos']})")
true(not any("This week's regime was decided without it" in a for a in _av_10y),
     f"e NAO afirma que o regime foi decidido sem ela ({_av_10y})")
true(any("sub-regime" in a for a in _av_10y),
     f"diz o que ficou por decidir ({_av_10y})")
true(any("retained" in a for a in _av_10y),
     f"e, numa semana em que houve retencao, di-lo ({_av_10y})")
true(not set(" ".join(_av_10y)) & set("&"),
     f"e sem os caracteres que o modelo reescreve ({_av_10y})")

# ── O gerador le o estado da carteira pelo MESMO ponto unico que o motor ──
#
# Ele lia os dois campos crus, com o default "Turbulence" que a ronda anterior
# tirou do motor por devolver um regime LEGIVEL para uma chave ausente. Numa
# re-corrida — o Caso 2 e o Caso 3 do RUNBOOK — o job da carteira sai pela
# guarda de data ANTES de curar o ficheiro, e a edicao saia a dizer
# "Turbulence" ao lado da lista de ETFs do vector de crise FTQ, 35% em TLT,
# dentro do mesmo documento.
_MAPA_FTQ_N = dict(rules.REGIME_ETF_MAP["Critical_FTQ"])
def _ctx_com_estado(reg, sub, com_mapa=True):
    _pf_n = make_portfolio(issue=27, regime="Critical", subregime="Critical_FTQ")
    _cur_n = _pf_n["current"]
    if reg is _AUSENTE_N:
        _cur_n.pop("regime", None)
    else:
        _cur_n["regime"] = reg
    if sub is _AUSENTE_N:
        _cur_n.pop("critical_subregime", None)
    else:
        _cur_n["critical_subregime"] = sub
    _cur_n["active_etf_map"] = dict(_MAPA_FTQ_N) if com_mapa else {}
    return sn.build_context(make_data(), _pf_n, PREV, D, 27, AGORA)

_AUSENTE_N = object()
_c_ref_n = _ctx_com_estado("Critical", "Critical_FTQ")
for _reg_n, _sub_n in ((_AUSENTE_N, _AUSENTE_N), (None, None),
                       ("Critical_FTQ", _AUSENTE_N), ("critical", "FTQ"),
                       ("Crisis", _AUSENTE_N)):
    _c_n = _ctx_com_estado(_reg_n, _sub_n)
    eq(_c_n["regime_label"], _c_ref_n["regime_label"],
       f"com ({_reg_n!r}, {_sub_n!r}) no ficheiro, a edicao publica o MESMO "
       f"regime que com o estado bem escrito ({_c_n['regime_label']})")
    eq(_c_n["port_etfs"], _c_ref_n["port_etfs"],
       f"e os mesmos ETFs ({_reg_n!r}, {_sub_n!r})")
    true("Turbulence" not in _c_n["regime_label"],
         f"e nunca diz Turbulence ao lado dos ETFs de Critical "
         f"({_c_n['regime_label']} | {_c_n['port_etfs']})")
# E sem mapa nenhum que identifique o vector, cai no que o campo diz — e se o
# campo tambem nao diz nada, em Turbulence, que e o lado sem sub-regime.
_c_sem_n = _ctx_com_estado(_AUSENTE_N, _AUSENTE_N, com_mapa=False)
eq(_c_sem_n["regime_label"], "Turbulence",
   f"sem campo e sem mapa, a edicao diz Turbulence — que e o que se sabe "
   f"({_c_sem_n['regime_label']})")

# ── E a CAUSA tambem sai do que o produtor declara ────────────────────────
#
# O `fetch_data` pede a DGS10 DUAS vezes por corrida — o `latest_value` para o
# E/P e o `history_values` para a janela de 3 meses. Basta o SEGUNDO falhar para
# a janela nao ser medida, com a serie a publicar a horas: a ultima observacao e
# do proprio dia, a serie nao entra em `staleSeries`, e o pilar Premium pontuou
# sobre ela. A edicao acusava na mesma a FRED de ter parado a serie — palavra
# por palavra, sob pena de recusa da edicao.
for _mot_gat, _tem_gat, _nao_gat in (
        ("stale", "has stopped updating", "could not be retrieved"),
        ("unavailable", "could not be retrieved", "has stopped updating"),
        ("short-window", "not return enough observations", "has stopped updating")):
    _d_mot = make_data()
    _d_mot["stressGauge"]["triggers"]["tenY3m"] = {
        "series": "DGS10", "value": None, "decides": "subregime",
        "stale": True, "fired": None, "ndReason": _mot_gat}
    _av_mot = [a for a in sn.build_context(
        _d_mot, make_portfolio(issue=27, regime="Critical",
                               subregime="Critical_FTQ"),
        PREV, D, 27, AGORA)["avisos"] if "DGS10" in a]
    true(any(_tem_gat in a for a in _av_mot),
         f"motivo {_mot_gat}: o aviso diz {_tem_gat!r} ({_av_mot})")
    true(not any(_nao_gat in a for a in _av_mot),
         f"motivo {_mot_gat}: e NAO diz {_nao_gat!r} ({_av_mot})")
    true(not set(" ".join(_av_mot)) & set("&/"),
         f"motivo {_mot_gat}: e sem caracteres que o modelo reescreve ({_av_mot})")
# E o produtor declara mesmo o motivo, nos tres gatilhos.
import importlib.util as _iu_gbm
_sp_gbm = _iu_gbm.spec_from_file_location("gb_m", ROOT / "mrm_gauge_b.py")
_gb_t = _iu_gbm.module_from_spec(_sp_gbm)
_sp_gbm.loader.exec_module(_gb_t)
_gbf_m = _gb_t._fetch
try:
    _gb_t._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.62"}] if sid == "SAHMREALTIME" else [])
    _g_m = _gb_t.compute("k", None, None, hoje=D)
    eq(_g_m["triggers"]["tenY3m"]["ndReason"], "unavailable",
       "sem nenhum dos dois pontos do 10Y, a leitura nao se obteve")
    _g_m2 = _gb_t.compute("k", 4.15, None, hoje=D)
    eq(_g_m2["triggers"]["tenY3m"]["ndReason"], "short-window",
       "com um ponto so, a janela e curta")
    _g_m3 = _gb_t.compute("k", 4.15, 4.47, hoje=D)
    eq(_g_m3["triggers"]["tenY3m"]["ndReason"], None,
       "e medida, nao ha motivo nenhum a declarar")
    # E os dois gatilhos antigos declaram o SEU motivo, derivado do que
    # aconteceu — nao uma constante. A versao anterior cravava "stale" nos dois,
    # e acusava a FRED de ter parado uma serie em dois casos em que ela nao
    # parou nada: a falha de rede (o `_fetch` esgota as tentativas e o
    # `observacao_velha` diz "nao velha" porque nao ha data para medir) e a
    # janela sem observacao de comparacao. E a mesma acusacao que esta ronda
    # tirou do gatilho do 10Y.
    eq(_g_m3["triggers"]["sahmRealtime"]["ndReason"], None,
       "um gatilho AVALIADO nao declara motivo nenhum")
    # Falha de rede: a serie nao devolveu nada.
    _gb_t._fetch = lambda sid, limit, key, **kw: []
    _g_rede = _gb_t.compute("k", 4.15, 4.47, hoje=D)
    eq(_g_rede["triggers"]["sahmRealtime"]["fired"], None,
       "sem observacoes, o gatilho fica por avaliar")
    eq(_g_rede["triggers"]["sahmRealtime"]["stale"], False,
       "e nao e 'velha': nao ha data nenhuma para medir a idade")
    eq(_g_rede["triggers"]["sahmRealtime"]["ndReason"], "unavailable",
       f"o motivo e a indisponibilidade, nao a serie parada "
       f"({_g_rede['triggers']['sahmRealtime']['ndReason']})")
    # Serie parada: ha observacao, mas velha.
    _gb_t._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2024-01-01", "value": "0.10"}] if sid == "SAHMREALTIME" else
        [{"date": "2026-04-01", "value": "1.38"},
         {"date": "2025-04-01", "value": "1.44"}])
    _g_velha = _gb_t.compute("k", 4.15, 4.47, hoje=D)
    eq(_g_velha["triggers"]["sahmRealtime"]["ndReason"], "stale",
       f"com observacao velha, o motivo E a serie parada "
       f"({_g_velha['triggers']['sahmRealtime']['ndReason']})")
    # Janela sem observacao de comparacao: a serie publicou, falta o trimestre.
    _gb_t._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.10"}] if sid == "SAHMREALTIME" else
        [{"date": "2026-04-01", "value": "1.38"}])
    _g_janela = _gb_t.compute("k", 4.15, 4.47, hoje=D)
    eq(_g_janela["triggers"]["delinquencyAccel"]["ndReason"], "no-window",
       f"e com a serie a publicar mas sem a observacao de comparacao, di-lo "
       f"({_g_janela['triggers']['delinquencyAccel']['ndReason']})")
    # E o consumidor tem uma frase para cada um — nenhuma delas a acusar a FRED
    # de parar uma serie que publicou a horas.
    for _mot_n, _tem_n in (("unavailable", "could not be retrieved"),
                           ("no-window", "not the earlier observation")):
        _d_n = make_data()
        _d_n["stressGauge"]["triggers"]["sahmRealtime"].update(
            {"fired": None, "stale": False, "ndReason": _mot_n})
        _av_n = [a for a in sn.build_context(_d_n, make_portfolio(issue=27),
                                             PREV, D, 27, AGORA)["avisos"]
                 if "SAHMREALTIME" in a]
        true(_av_n, f"motivo {_mot_n}: o gatilho por avaliar vira aviso ({_av_n})")
        true(any(_tem_n in a for a in _av_n),
             f"motivo {_mot_n}: com a frase certa ({_av_n})")
        true(not any("has stopped updating" in a for a in _av_n),
             f"motivo {_mot_n}: e sem acusar a FRED ({_av_n})")
finally:
    _gb_t._fetch = _gbf_m

# ── E o que o gatilho DECIDIU sai do que a carteira fez ───────────────────
#
# "the sub-regime in force was retained" era escrito em TODAS as semanas em que
# a janela faltasse. A `subregime_from_gauge` tem TRES ramos: entrada fresca,
# retencao, medicao. Numa entrada fresca o motor nao retem nada — roda 100% da
# carteira para o vector defensivo — e numa semana calma a carteira nem sequer
# esta em Critical, portanto nao ha sub-regime nenhum para reter. O aviso ia ao
# prompt com ordem de o copiar palavra por palavra sob pena de recusa da edicao.
for _reg_10y, _sub_10y, _razao_10y, _tem, _nao_tem in (
        ("Critical", "Critical_FTQ", "hold", "retained", "entered Critical"),
        ("Critical", "Critical_Stress", "stress_on", "entered Critical", "retained"),
        # Uma TROCA de sub-regime tambem executa, e ai nada foi retido: o ramo
        # da retencao era o ramo por omissao e escrevia-se sobre a troca.
        ("Critical", "Critical_Stress",
         "critical_subregime_switch:Critical_FTQ->Critical_Stress",
         "changed this week", "retained"),
        ("Turbulence", None, "hold", "not in Critical", "retained"),
        ("Resilient", None, "resilient_off", "not in Critical", "retained")):
    _c_v = sn.build_context(_d_10y,
                            make_portfolio(issue=27, regime=_reg_10y,
                                           subregime=_sub_10y,
                                           reason=_razao_10y,
                                           triggered=(_razao_10y != "hold")),
                            PREV, D, 27, AGORA)
    _av_v = [a for a in _c_v["avisos"] if "DGS10" in a and "Gauge B trigger" in a]
    true(_av_v, f"{_reg_10y}/{_razao_10y}: o aviso sai na mesma ({_c_v['avisos']})")
    true(any(_tem in a for a in _av_v),
         f"{_reg_10y}/{_razao_10y}: e diz o que aconteceu — {_tem!r} ({_av_v})")
    true(not any(_nao_tem in a for a in _av_v),
         f"{_reg_10y}/{_razao_10y}: e NAO afirma {_nao_tem!r} ({_av_v})")
    true(not set(" ".join(_av_v)) & set("&/"),
         f"{_reg_10y}/{_razao_10y}: e sem caracteres que o modelo reescreve ({_av_v})")
# E o gatilho que DECIDE o regime continua a dize-lo: as duas frases nao podem
# colapsar numa so.
true(any("This week's regime was decided without it" in a for a in _av_gat),
     f"o gatilho do regime continua a dizer que o regime foi decidido sem ele "
     f"({_av_gat})")
# E o produtor declara mesmo o campo — se ele desaparecer, o consumidor volta
# em silencio a escrever a frase errada.
import importlib.util as _iu_gb_t
_sp_gb_t = _iu_gb_t.spec_from_file_location("gb_t", ROOT / "mrm_gauge_b.py")
_gb_t = _iu_gb_t.module_from_spec(_sp_gb_t)
_sp_gb_t.loader.exec_module(_gb_t)
_gbf_t = _gb_t._fetch
try:
    _gb_t._fetch = lambda sid, limit, key, **kw: (
        [{"date": "2026-08-01", "value": "0.62"}] if sid == "SAHMREALTIME" else [])
    _g_t = _gb_t.compute("k", None, None, hoje=D)
    _tr_t = _g_t["triggers"]
    eq(_tr_t["tenY3m"]["decides"], "subregime",
       "o produtor declara que a janela do 10Y decide o sub-regime")
    eq(_tr_t["sahmRealtime"]["decides"], "regime",
       "e que o gatilho de Sahm decide o regime")
    eq(_tr_t["delinquencyAccel"]["decides"], "regime",
       "e o da delinquencia tambem")
    eq(set(_tr_t) - {"sahmRealtime", "delinquencyAccel", "tenY3m"}, set(),
       f"e nao ha gatilhos por declarar ({sorted(_tr_t)})")
    true(all("decides" in _v_t for _v_t in _tr_t.values()),
         f"todos declaram o que decidem ({sorted(_tr_t)})")
finally:
    _gb_t._fetch = _gbf_t
true(any("renormalised" in a for a in _c_pa["avisos"]),
     "e a dizer que os pesos foram renormalizados")
true("stopped updating" in sn.build_prompt(_c_pa), "o modelo recebe a instrucao de o publicar")

# ── O score publicado tem de ser o score do motor ─────────────────────────
# O parser ja extraia o numero do HTML e a validacao deitava-o fora. Um score
# errado escrito pelo modelo chegava aos subscritores e ao site, e so era notado
# sete dias depois num log.warning do motor que ninguem le.
_c_score = sn.build_context(make_data(score=6.97), make_portfolio(issue=27), PREV, D, 27, AGORA)
eq(_c_score["score"], 7.0, "o contexto leva o score arredondado a uma casa")
eq(sn.validate_newsletter(edicao(27, score=6.97), _c_score), [],
   "uma edicao que publica o score do motor passa")
_mau_score = " | ".join(m for _t, m in
                        sn.validate_newsletter(edicao(27, score=4.2), _c_score))
true("score 4.2" in _mau_score and "motor calculou 7.0" in _mau_score,
     f"uma edicao que publica outro score e rejeitada ({_mau_score})")
eq(sorted({t for t, _ in sn.validate_newsletter(edicao(27, score=4.2), _c_score)}),
   [sn.FALHA_FORMA], "e e falha de FORMA: nao se publica um score errado")
# Arredondamento nao e erro.
eq(sn.validate_newsletter(edicao(27, score=7.04), _c_score), [],
   "uma diferenca de arredondamento nao e rejeitada")

# ── A verificacao "verbatim" nao pode falhar por tipografia ───────────────
# A semana com avisos e, por definicao, a semana degradada. Se a verificacao
# falhasse por um apostrofo escapado, era essa a semana que ficava sem edicao.
_av_teste = ("DATA QUALITY: this week's portfolio value uses the last known price "
             "for GLD, because the fresh quote failed.")
_ctx_av = {"issue_number": 27, "avisos": [_av_teste], "score": None}
for _nome, _forma in {
    "tal e qual":        _av_teste,
    "aspa curva":        _av_teste.replace("week's", "week\u2019s"),
    "&#8217;":           _av_teste.replace("week's", "week&#8217;s"),
    "&rsquo;":           _av_teste.replace("week's", "week&rsquo;s"),
    "travessao":         _av_teste.replace(", because", " \u2014 because"),
    "partido por tags":  _av_teste.replace("last known", "last <b>known</b>\n   "),
    "espaco duro":       _av_teste.replace(" for ", "&nbsp;for&nbsp;"),
}.items():
    _html = edicao(27, avisos=[]).replace("<div class=\"content\">",
                                          f"<div class=\"content\"><p>{_forma}</p>")
    _faltas = [m for t, m in sn.validate_newsletter(_html, _ctx_av)
               if "aviso de qualidade" in m]
    eq(_faltas, [], f"o aviso escrito com {_nome} conta como presente")
# E um aviso que NAO esta la continua a ser apanhado.
_faltas = [m for t, m in sn.validate_newsletter(edicao(27), _ctx_av)
           if "aviso de qualidade" in m]
true(_faltas, "e a ausencia real do aviso continua a ser apanhada")
# Nenhum aviso pode conter "&" ou "/": sao os caracteres que o modelo reescreve
# de varias maneiras, e um aviso que os use fica refem da tipografia dele — e
# estes avisos vao ao prompt com ordem de os copiar palavra por palavra sob pena
# de a edicao ser recusada.
#
# A verificacao era uma regex sobre o CODIGO-FONTE do produtor:
#   r'avisos\.append\(\s*\n?\s*f?"(DATA QUALITY[^"]*)"'
# Ela apanha o PRIMEIRO literal de cada `append` e mais nada. Metade dos avisos
# deste ficheiro sao construidos por concatenacao — um tronco mais uma
# `_consequencia` escolhida por ramo — e nenhum desses fragmentos era visto.
# Passou a haver uma barra num deles, e a regex ficou verde. Verifica-se agora o
# que o gerador PRODUZ: percorrem-se os estados que produzem avisos e
# inspecciona-se cada aviso inteiro.
import re as _re
_estados_aviso = []
for _paradas_t in ({}, {"T10Y2Y": 90}, {"ICSA": 90}, {"SP500": 90},
                   {"UNRATE": 90}, {"NCBEILQ027S_FBCELLQ027S_GDP": 90},
                   {"SP500": 90, "T10Y2Y": 90}):
    for _nd_t, _mot_t in (([], None), (["liquidity"], {"liquidity": "series"}),
                          (["premium"], {"premium": "stale-anchor"}),
                          (["cycle", "premium"], {"cycle": "series",
                                                  "premium": "stale-anchor"}),
                          # E o estado em que o produtor ABANDONA o composto:
                          # abaixo do peso minimo nao ha renormalizacao nenhuma.
                          (["cycle", "liquidity", "premium"],
                           {"cycle": "series", "liquidity": "series",
                            "premium": "stale-anchor"})):
        for _idade_t in (2.0, 31.0, 60.0):
            _estados_aviso.append((_paradas_t, _nd_t, _mot_t, _idade_t))
_vistos_aviso = set()
for _paradas_t, _nd_t, _mot_t, _idade_t in _estados_aviso:
    _d_t = make_data(nd=_nd_t, motivos=_mot_t, paradas=_paradas_t,
                     idade_horas=_idade_t, score_das_regras=True)
    # E com um gatilho do medidor parado, dos dois tipos.
    _d_t["stressGauge"]["triggers"]["sahmRealtime"].update(
        {"series": "SAHMREALTIME", "stale": True, "fired": None,
         "ndReason": "stale", "asOf": "2025-08-01"})
    _d_t["stressGauge"]["triggers"]["tenY3m"] = {
        "series": "DGS10", "value": None, "asOf": "2026-06-02",
        "decides": "subregime", "stale": True, "fired": None}
    for _razao_t2, _exec_t2 in (("hold", False), ("stress_on", True),
                                ("semestral_rebalance", True),
                                ("missing_prices_held", False),
                                ("no_allocation_available", False),
                                ("stale_allocation_held", False),
                                ("aborted_invalid_shares", False)):
        _c_t = sn.build_context(
            _d_t, make_portfolio(issue=27, reason=_razao_t2, triggered=_exec_t2),
            PREV, D, 27, AGORA)
        for _a_t in (_c_t.get("avisos") or []):
            _vistos_aviso.add(_a_t)
true(len(_vistos_aviso) >= 12,
     f"os estados varridos produzem avisos que cheguem ({len(_vistos_aviso)})")

# ── E a varredura verifica VERDADE, nao so tipografia ─────────────────────
#
# Ela nasceu para apanhar "&" e "/" — os caracteres que o modelo reescreve — e
# so isso verificava. Mas o mesmo varrimento tem os avisos todos na mao: pode
# perguntar-lhes se sao verdade. A primeira pergunta e a que faltava: nao ha
# renormalizacao nenhuma quando o `global_score` ABANDONA o composto (peso
# sobrevivente abaixo do minimo, tres pilares em n/d chegam). Tres avisos
# obrigatorios afirmavam "the remaining weights were renormalised" nessa
# semana — na mesma edicao em que o cartao do score sai SEM numero.
_nd_muitos = ["cycle", "liquidity", "premium"]
_d_sem = make_data(nd=_nd_muitos, score_das_regras=True,
                   motivos={p_: "series" for p_ in _nd_muitos},
                   paradas={"T10Y2Y": 90})
eq(_d_sem["globalResilienceScore"], None,
   f"tres pilares em n/d abandonam o composto "
   f"({_d_sem['globalResilienceScore']})")
_c_sem = sn.build_context(_d_sem, make_portfolio(issue=27), PREV, D, 27, AGORA)
_av_sem = _c_sem["avisos"]
true(_av_sem, "e a semana produz avisos")
true(not any("weights were renormalised" in a for a in _av_sem),
     f"sem composto, nenhum aviso afirma que os pesos renormalizaram "
     f"({[a for a in _av_sem if 'renormalis' in a]})")
true(any("no Resilience Score this week" in a for a in _av_sem),
     f"e diz-se o que aconteceu de facto ({_av_sem})")
# E COM composto, a frase de sempre — senao isto passava com um gerador que
# nunca fala de renormalizacao.
_d_com = make_data(nd=["liquidity"], score_das_regras=True,
                   motivos={"liquidity": "series"}, paradas={"T10Y2Y": 90})
true(_d_com["globalResilienceScore"] is not None,
     "com um pilar so em n/d ainda ha composto")
_av_com = sn.build_context(_d_com, make_portfolio(issue=27), PREV, D, 27,
                           AGORA)["avisos"]
true(any("weights were renormalised" in a for a in _av_com),
     f"e ai os pesos renormalizaram mesmo ({_av_com})")
true(not any("no Resilience Score this week" in a for a in _av_com),
     f"e nao se diz que nao ha score quando ha ({_av_com})")
# O mesmo para o ramo do E/P, que tem a sua propria copia da frase.
_d_ep_sem = make_data(nd=_nd_muitos, score_das_regras=True,
                      motivos={p_: ("stale-anchor" if p_ == "premium" else "series")
                               for p_ in _nd_muitos})
_av_ep = sn.build_context(_d_ep_sem, make_portfolio(issue=27), PREV, D, 27,
                          AGORA)["avisos"]
true(not any("weights were renormalised" in a for a in _av_ep),
     f"e o ramo do E-P tambem nao ({[a for a in _av_ep if 'renormalis' in a]})")
for _a_t in sorted(_vistos_aviso):
    true(not set(_a_t) & set("&/"),
         f"o aviso PRODUZIDO nao usa & nem / : {_a_t}")
    # E sem aspas curvas nem travessoes, pela mesma razao.
    true(not set(_a_t) & set("\u2018\u2019\u201c\u201d\u2014"),
         f"nem tipografia que o modelo reescreve: {_a_t}")

# ── O cartao do arquivo nao pode ficar verde com o medidor em n/d ─────────
# `if c["stress_active"]` tratava None como OFF e pintava o cartao com a cor do
# score, que pode ser verde. Uma semana em que o medidor nao pode ser avaliado
# nao e uma semana verde — e a mesma correccao que o regimeTone() do index.html
# levou, e que este cartao nao tinha levado.
import re as _r
def _cor_badge(**kw):
    """A cor da badge do REGIME no cartao — nao a do score, que e outra coisa."""
    _cc = sn.build_context(make_data(**kw), make_portfolio(issue=27), PREV, D, 27, AGORA)
    _card = sn.render_archive_card(_cc, "x.html")
    _m = _r.search(r'border:1px solid (#[0-9A-Fa-f]{6});[^"]*color:(#[0-9A-Fa-f]{6});', _card)
    return _m.group(1) if _m else None

eq(_cor_badge(stress=True, subregime="STRESS"), "#D73A49",
   "com o medidor ligado a badge e vermelha")
eq(_cor_badge(stress=None, score=3.0), "#8B96A3",
   "com o medidor em n/d a badge e neutra")
eq(_cor_badge(stress=False, score=3.0), "#34D058",
   "com o medidor desligado e o score baixo, ai sim, verde")

# ── O score lido do HTML tem ancora propria ──────────────────────────────
# Ler "a primeira etiqueta com N.N" era fragil por construcao: um decimal
# isolado antes do cartao do score passava a ser lido como o score. Enquanto
# isso so dava um log.warning era um incomodo; desde que a validacao compara o
# score publicado com o do motor, uma leitura errada custa a edicao da semana.
import newsletter_parse as _np2
_com_ruido = edicao(27, score=6.97).replace(
    '<div class="content">', '<div class="content"><div class="tile">4.35</div>')
eq(_np2.parse_allocation(_com_ruido)[1], 7.0,
   "com um decimal solto antes do cartao, o score lido continua a ser o do cartao")
eq(sn.validate_newsletter(_com_ruido, _c_score), [],
   "e a edicao passa a validacao")
# Sem a ancora, o recurso antigo ainda funciona (edicoes ja publicadas).
_sem_ancora = edicao(27, score=6.97).replace(' data-mrm-score="7.0"', '')
eq(_np2.parse_allocation(_sem_ancora)[1], 7.0,
   "e uma edicao antiga, sem a ancora, continua a ser lida")
# E o prompt pede mesmo a ancora ao modelo.
true("data-mrm-score" in sn.build_prompt(_c_score),
     "o prompt pede a ancora do score ao modelo")

# ── A tolerancia do score esta presa ao seu valor ────────────────────────
# Alargar a tolerancia para 1.0 nao fazia falhar nada: o numero que define a
# verificacao nao estava preso por teste nenhum.
eq(sn.SCORE_TOLERANCIA, 0.05, "a tolerancia e de meia casa decimal")

# ── O numero que o LEITOR ve tem de ser o do motor ───────────────────────
# A ancora `data-mrm-score` vai no esqueleto do prompt ja preenchida com o valor
# do motor: compara-la com o motor e compara-la consigo propria. Uma edicao com
# a ancora certa e um numero errado no cartao passava — e 4,2 e 7,0 estao em
# bandas de regime diferentes.
_bom = edicao(27, score=6.97)
eq(sn.validate_newsletter(_bom, _c_score), [], "ancora e cartao coerentes: passa")
_mentira = _bom.replace(">7.0<", ">4.2<")
_probs = [m for _t, m in sn.validate_newsletter(_mentira, _c_score)]
true(any("MOSTRA o score 4.2" in m for m in _probs),
     f"a ancora certa com o cartao errado e apanhada ({_probs})")
_ancora_ma = _bom.replace('data-mrm-score="7.0"', 'data-mrm-score="4.2"')
_probs2 = [m for _t, m in sn.validate_newsletter(_ancora_ma, _c_score)]
true(any("ancora do score diz 4.2" in m for m in _probs2),
     f"e o cartao certo com a ancora errada tambem ({_probs2})")
eq(sn._score_visivel(_bom), 7.0, "o score visivel le-se do cartao")
# E um cartao SEM numero devolve None, em vez de varrer o documento a procura de
# qualquer decimal. Numa semana n/d o cartao diz "N/A", e o recurso devolvia o
# primeiro numero que encontrasse no texto — o 10Y, um limiar, um peso — e
# apresentava-o como se fosse o score.
_nd_cartao = ('<html><body><div class="score-num" data-mrm-score="N/A">N/A</div>'
              '<p>The 10Y sits at 4.3% and the ERP at 1.6.</p></body></html>')
eq(sn._score_visivel(_nd_cartao), None,
   "um cartao sem numero devolve None, nao o primeiro decimal do documento")
eq(sn._score_visivel('<html><body><p>The 10Y sits at 4.3%.</p></body></html>'), 4.3,
   "e sem cartao nenhum o recurso continua a existir, para as edicoes antigas")
eq(sn._score_visivel(_mentira), 4.2, "com o ponto decimal intacto")
# ── O cartao do score tem de ser legivel pelos DOIS leitores ───────────────
#
# O `_score_visivel` le o conteudo da etiqueta; o parser do motor exige o
# decimal SOZINHO dentro dela. Metade das formas plausiveis de um modelo
# desenhar o cartao — "7.0 / 10", "7.0<span>/10</span>", "Score 7.0" — davam
# 7,0 num leitor e nada no outro: a edicao era publicada e enviada, e a partir
# da semana seguinte o portao que compara os dois leitores sobre o arquivo
# ficava vermelho para sempre. A validacao passa a recusa-las antes de sairem.
_c_cartao = {"issue_number": 30, "avisos": [], "score": 7.0}
_base_cartao = edicao(30, score=7.0)
_ANCORA_BOA = '<div class="score" data-mrm-score="7.0">7.0</div>'
true(_ANCORA_BOA in _base_cartao, "o fixture tem a ancora que o esqueleto pede")
# `motor_le` = o parser do motor consegue ler o score deste cartao sem a ancora.
# As formas em que nao consegue sao as que tem de ser recusadas ANTES de sair.
for _nome_c, _cartao, _motor_le in (
        ("decimal nu", '<div class="score-num">7.0</div>', True),
        ("dentro de negrito", '<div class="score-num"><b>7.0</b></div>', True),
        ("com a escala", '<div class="score-num">7.0 / 10</div>', False),
        ("escala em etiqueta", '<div class="score-num">7.0<span>/10</span></div>', False),
        ("espaco duro", '<div class="score-num">7.0&nbsp;/10</div>', False),
        ("com rotulo", '<div class="score-num">Score 7.0</div>', False)):
    _html_c = _base_cartao.replace(_ANCORA_BOA, _cartao)
    _probs_c = sn.validate_newsletter(_html_c, _c_cartao)
    if _motor_le:
        eq(_probs_c, [], f"cartao '{_nome_c}': o motor le-o, portanto passa")
    else:
        true(_probs_c,
             f"cartao '{_nome_c}' sem a ancora: a validacao recusa antes de publicar "
             f"(obtido {_probs_c})")
        true(any("ancora" in m for _t, m in _probs_c),
             f"e diz que e a ancora que falta ({_probs_c})")
    # E com a ancora ao lado, qualquer destas formas passa: o que se exige e que
    # o motor consiga ler, nao que o modelo escreva de uma maneira so.
    _com_ancora = _base_cartao.replace(
        _ANCORA_BOA, _cartao.replace('class="score-num"',
                                     'class="score-num" data-mrm-score="7.0"'))
    eq(sn.validate_newsletter(_com_ancora, _c_cartao), [],
       f"cartao '{_nome_c}' COM a ancora passa")

# ── Numa semana n/d nao pode aparecer numero nenhum no cartao ──────────────
# Toda a verificacao do score estava dentro do ramo numerico: na semana em que o
# motor recusou calcular — a da avaria, a que mais precisa de ser lida com
# cuidado — o modelo podia escrever o numero que quisesse no cartao de capa do
# produto, e a edicao saia. O motor nao age sobre esse numero; os subscritores
# leem-no.
_c_nd_score = {"issue_number": 30, "avisos": [], "score": "N/A"}
eq(sn.validate_newsletter(edicao(30, score="N/A"), _c_nd_score), [],
   "numa semana n/d, uma edicao que nao mostra numero nenhum passa")
# E o cartao tem de existir tambem nesta semana: sem ele, o varrimento do
# documento apanhava o "+0.0" da coluna WoW — obrigatoria pelo proprio prompt —
# e a queixa mandava o modelo tirar um numero que nao estava em cartao nenhum.
# Tres tentativas depois, a semana da avaria ficava sem newsletter.
_sem_cartao = edicao(30, score="N/A").replace(
    '<div class="score" data-mrm-score="N/A">N/A</div>',
    '<div class="score">Score unavailable this week</div>')
_probs_sc = sn.validate_newsletter(_sem_cartao, _c_nd_score)
true(any("nao tem o cartao do score" in m for _t, m in _probs_sc),
     f"numa semana n/d a edicao sem cartao e recusada, e a queixa aponta para o "
     f"cartao ({_probs_sc})")
true(not any("mostra 0.0" in m for _t, m in _probs_sc),
     f"e nao acusa um numero apanhado noutro sitio do documento ({_probs_sc})")

# E a prova sobre as edicoes REAIS, nao sobre o fixture: cada edicao do arquivo,
# reescrita como semana n/d (a ancora a dizer N/A, o cartao sem numero, o resto
# intacto), tem de passar. Enquanto a validacao leu a ancora pelo recurso do
# `parse_allocation` — que devolve o primeiro decimal nu de qualquer etiqueta do
# documento — dez das vinte e seis eram recusadas por um "6.97" que vinha da
# caixa de rebalanceamento, e a queixa era impossivel de satisfazer porque essa
# caixa e obrigatoria. O fixture nao via nada disto: escreve percentagens e WoW
# com sinal, e nenhum casa `<tag>N.N</tag>`.
import glob as _g_nd
def _como_semana_nd(html):
    """A edicao reescrita como o gerador a escreveria numa semana n/d, ou None.

    Devolve None quando a reescrita NAO pega — e isso e o ponto.

    Este bloco julga edicoes ja publicadas sob uma hipotese ("e se esta semana
    fosse n/d?"). Quem escreve o cartao do score e um modelo, e a validacao de
    publicacao aceita mais formas do que uma expressao regular sabe reescrever:
    `7.0<span>&nbsp;</span>`, a ancora dentro de um `<span>`, e por ai. Se uma
    reescrita falhada fosse tratada como uma edicao ma, uma edicao que o sistema
    gerou, validou, publicou e enviou fechava os dois jobs de sexta na semana
    seguinte, para sempre, sem recuperacao — por causa de um ficheiro commitado.
    E a familia de sempre, um nivel acima: o portao a derivar uma expectativa de
    estado que o sistema escreve, so que transformando-o primeiro.
    #
    # Uma edicao que nao se consegue reescrever nao e uma edicao ma: e um input
    # que nao serve para este teste. Salta-se, e ha um piso mais abaixo para o
    # teste nao se esvaziar sozinho.
    """
    # O conteudo do elemento ancorado fica em N/A, esteja ele aninhado ou nao.
    novo = _re.sub(r'(<(\w+)[^>]*\bdata-mrm-score=")[^"]*("[^>]*>).*?(</\2>)',
                   r'\1N/A\3N/A\4', html, count=1, flags=_re.S)
    if novo == html:
        novo = _re.sub(r'(<(\w+)[^>]*class="[^"]*score-num[^"]*"[^>]*)(>).*?(</\2>)',
                       r'\1 data-mrm-score="N/A"\3N/A\4', html, count=1, flags=_re.S)
    if novo == html:
        return None
    # A reescrita tem de ter PEGADO: a ancora em N/A e nenhum numero visivel no
    # cartao. Se nao pegou, o input nao serve — nao se acusa a edicao.
    _anc = _re.search(r'data-mrm-score="([^"]*)"', novo)
    if not _anc or _anc.group(1).strip().upper() not in ("N/A", "ND", "N-D"):
        return None
    if sn._score_visivel(novo) is not None:
        return None
    return novo

_recusadas_nd, _usadas_nd, _saltadas_nd = [], 0, []
for _f_nd in sorted(_g_nd.glob(str(ROOT / "MRM_Newsletter_Issue*_*.html"))):
    _h_nd = _como_semana_nd(Path(_f_nd).read_text(encoding="utf-8"))
    if _h_nd is None:
        _saltadas_nd.append(Path(_f_nd).name)
        continue
    _usadas_nd += 1
    # O numero da edicao sai do PROPRIO documento: as edicoes do arquivo
    # identificam-se la dentro, e o nome do ficheiro pode nao acompanhar uma
    # copia feita a mao. O que aqui se testa e o score, nao a identidade.
    _m_nd = _re.search(r"Issue #(\d+)", _h_nd)
    _n_nd = int(_m_nd.group(1)) if _m_nd else int(
        Path(_f_nd).name.split("Issue")[1].split("_")[0])
    _p_nd = [m for _t, m in sn.validate_newsletter(
        _h_nd, {"score": "N/A", "issue_number": _n_nd, "avisos": []})
        if _t == sn.FALHA_FORMA and "score" in m]
    if _p_nd:
        _recusadas_nd.append((Path(_f_nd).name, _p_nd[0]))
eq(_recusadas_nd, [],
   f"numa semana n/d, uma edicao real com o cartao certo passa a validacao "
   f"({_recusadas_nd[:2]})")
# O teste nao se pode esvaziar sozinho a saltar edicoes — mas o piso NAO pode
# ser uma contagem do arquivo, nem uma exigencia sobre as edicoes mais recentes.
# Ambos sao expectativas derivadas de estado que o sistema escreve, e e essa a
# familia de defeitos que este ficheiro existe para fechar: uma unica edicao que
# a reescrita nao apanhe — e quem escreve o cartao e um modelo — e uma das tres
# mais recentes, e o portao fica vermelho sobre um ficheiro ja commitado e
# ENVIADO, sem recuperacao, porque quem publicaria as tres edicoes seguintes e
# justamente o job que o portao trava.
#
# O piso passa a sair do PRODUTOR, que e deterministico: uma edicao escrita
# sobre o esqueleto que o gerador manda ao modelo tem de ser reescrivivel e tem
# de passar a validacao como semana n/d. Se a reescrita se partir, ou se o
# esqueleto mudar de forma, isto fica vermelho de imediato — sem depender de um
# unico ficheiro do arquivo.
_esqueleto_nd = _PROMPT_ESQUELETO
_m_corpo = _re.search(r"<!DOCTYPE html.*?</html>", _esqueleto_nd, _re.S | _re.I)
true(_m_corpo is not None, "o esqueleto do prompt traz uma edicao completa")
_ed_produtor = (_m_corpo.group(0)
                .replace("SCORE_HERE", "7.0")
                .replace("REGIME_HERE", "TURBULENCE")
                .replace("WOW_HERE", "+0.2 WoW"))
_ed_produtor = _re.sub(r"[A-Z_]+_HERE", "Analysis paragraph. " * 300, _ed_produtor)
_nd_produtor = _como_semana_nd(_ed_produtor)
true(_nd_produtor is not None,
     "uma edicao escrita sobre o esqueleto do PRODUTOR e reescrivivel como "
     "semana n/d — o piso sai de quem gera, nao do arquivo commitado")
_p_prod = [m for _t, m in sn.validate_newsletter(
    _nd_produtor, {"score": "N/A", "issue_number": 30, "avisos": []})
    if _t == sn.FALHA_FORMA and "score" in m]
eq(_p_prod, [],
   f"e passa a validacao da semana n/d ({_p_prod})")
# E o arquivo real continua a ser exercitado: se a reescrita deixasse de pegar
# em TODAS as edicoes publicadas, o laco acima ficava vazio e nao acusava nada.
true(_usadas_nd > 0,
     f"e o arquivo real continua a ser exercitado ({_usadas_nd} edicoes; "
     f"saltadas {_saltadas_nd})")

_probs_nd = sn.validate_newsletter(edicao(30, score=6.8), _c_nd_score)
# As DUAS verificacoes tem de acusar: o numero que o subscritor le no cartao, e
# a ancora que o motor le na semana seguinte. Exigir so uma deixava a outra
# livre — e foi assim que a semana n/d ficou sem verificacao nenhuma.
true(any("mostra 6.8 no cartao" in m for _t, m in _probs_nd),
     f"e uma que inventa um score no cartao e recusada ({_probs_nd})")
true(any("ancora data-mrm-score diz 6.8" in m for _t, m in _probs_nd),
     f"e a ancora inventada tambem ({_probs_nd})")

# ── A ancora vive NA etiqueta que mostra o score ──────────────────────────
#
# Sao dois leitores sobre a mesma coisa: o subscritor le o CONTEUDO do
# `score-num`, o motor le o ATRIBUTO `data-mrm-score` no sabado seguinte. A
# validacao comparava os dois numeros e deixava passar uma edicao em que eles
# estao em etiquetas DIFERENTES — e a partir dai podem divergir sem uma queixa.
# Concretamente: a reescrita que o portao faz para ensaiar uma semana n/d apaga
# o conteudo da etiqueta ancorada e deixa o `score-num` a mostrar o numero; a
# edicao, ja publicada e ENVIADA, passa a ser um input irreescrevivel e o portao
# fica vermelho sobre um ficheiro commitado — sem recuperacao, porque quem
# publicaria as edicoes seguintes e o job que o portao trava.
def _cartao(html_do_cartao, enchimento=12000):
    """Uma edicao valida em tudo excepto no cartao do score."""
    base = edicao(30, score=7.0, enchimento=enchimento)
    return base.replace('<div class="score" data-mrm-score="7.0">7.0</div>',
                        html_do_cartao)

_c_31 = dict(_c_nd_score)
_c_31["score"] = 7.0
_separados = _cartao('<div class="score-num">7.0</div>'
                     '<span data-mrm-score="7.0"></span>')
true(any("nao esta na etiqueta que mostra o score" in m
         for _t, m in sn.validate_newsletter(_separados, _c_31)),
     "a ancora numa etiqueta e o numero noutra e RECUSADA — os dois leitores "
     "tem de ler o mesmo pedaco de texto")
_juntos = _cartao('<div class="score-num" data-mrm-score="7.0">7.0</div>')
eq(sn.validate_newsletter(_juntos, _c_31), [],
   "e com a ancora NA etiqueta do cartao publica-se, que e como o esqueleto "
   "do prompt a escreve")
# E o arquivo publicado ANTES de a ancora existir nao pode ser recusado por
# isto: e estado commitado que ninguem pode alterar, e um portao que nao se
# pode satisfazer e uma sexta perdida para sempre.
eq(sn.ancora_fora_do_cartao('<div class="score-num">7.0</div>'), False,
   "uma edicao antiga, com cartao e sem ancora nenhuma, nao e acusada")
eq(sn.ancora_fora_do_cartao('<span data-mrm-score="7.0">7.0</span>'), False,
   "nem uma sem `score-num`, onde a etiqueta ancorada E o cartao")
# A propriedade fecha-se no PRODUTOR: o esqueleto que o gerador manda ao modelo
# tem de ja trazer a ancora na etiqueta do cartao. Se um dia as separar, o
# modelo obediente devolve uma edicao que a validacao recusa nas tres
# tentativas — a semana ficava sem newsletter por causa do proprio esqueleto.
_esq = _PROMPT_ESQUELETO
eq(sn.ancora_fora_do_cartao(_esq), False,
   "e o esqueleto do prompt escreve a ancora NA etiqueta do cartao")
_m_esq = sn._RE_CARTAO_SCORE.search(_esq)
true(_m_esq is not None and "data-mrm-score" in _m_esq.group(0),
     f"e o cartao do esqueleto traz mesmo a ancora ({_m_esq and _m_esq.group(0)})")

# E a queixa nova nao pode recusar uma edicao LEGITIMA: e FALHA_FORMA, portanto
# as tres tentativas falham e a semana fica sem newsletter. Quem escreve o
# cartao e um modelo, e estas sao as formas que ele escreve.
for _forma_ok, _porque_ok in (
    ('<div class="score-num" data-mrm-score="7.0">7.0</div>',
     "o esqueleto, a letra"),
    ('<div data-mrm-score="7.0" class="score-num">7.0</div>',
     "com o atributo antes da classe"),
    ("<div class='score-num' data-mrm-score='7.0'>7.0</div>",
     "com plicas em vez de aspas"),
    ('<div class="score-circle score-num big" data-mrm-score="7.0">7.0</div>',
     "com mais classes na mesma etiqueta"),
    ('<div class="score-num"><span data-mrm-score="7.0">7.0</span></div>',
     "com a ancora ANINHADA — e o mesmo pedaco de texto para os dois leitores"),
    ('<div class="score-number">decorativo</div>'
     '<div class="score-num" data-mrm-score="7.0">7.0</div>',
     "com um `score-number` decorativo antes: `score-num` e um TOKEN da classe, "
     "nao uma substring"),
    ('<div class="score-num">antigo</div>'
     '<div class="score-num" data-mrm-score="7.0">7.0</div>',
     "com dois cartoes e a ancora no segundo"),
):
    eq(sn.ancora_fora_do_cartao(_forma_ok), False,
       f"nao se acusa uma edicao legitima: {_porque_ok}")
    _p_ok = [m for _t, m in sn.validate_newsletter(_cartao(_forma_ok), _c_31)]
    eq(_p_ok, [], f"e publica-se: {_porque_ok} ({_p_ok})")
# O `_score_visivel` tem de ler o cartao CERTO nos mesmos casos — era ele que,
# com um `score-number` decorativo pelo meio, devolvia None e fazia a validacao
# queixar-se de que "a edicao nao mostra o score" sobre uma edicao que o mostra.
eq(sn._score_visivel('<div class="score-number">decorativo</div>'
                     '<div class="score-num" data-mrm-score="7.0">7.0</div>'), 7.0,
   "o score visivel sai do cartao, nao de um elemento com uma classe parecida")
# E o caso que so a fronteira de TOKEN apanha: o decoy com um NUMERO. Com
# `score-num` a casar por substring, `class="score-number"` a mostrar 4.2 era
# lido como o score, a validacao comparava 4,2 com o 7,0 do motor e recusava a
# edicao nas tres tentativas — 4,2 e 7,0 estao em bandas de regime diferentes,
# e a queixa era sobre um numero que ninguem publicou como score.
_decoy_num = ('<div class="score-numeric">4.2</div>'
              '<div class="score-num" data-mrm-score="7.0">7.0</div>')
eq(sn._score_visivel(_decoy_num), 7.0,
   "um elemento com uma classe PARECIDA e um numero nao passa por cartao do score")
eq(sn.validate_newsletter(_cartao(_decoy_num), _c_31), [],
   "e a edicao publica-se")
eq(sn._score_visivel('<div class="score-num"><span data-mrm-score="7.0">7.0</span></div>'),
   7.0, "e le-se atraves de uma etiqueta aninhada")
eq(sn._score_visivel('<div class="score-num" data-mrm-score="N/A">N/A</div>'), None,
   "e uma semana n/d continua a nao ter numero nenhum")

# E le-se correctamente nas edicoes reais ja publicadas.
import glob as _g
# O que o sistema GARANTE — e portanto o que se pode afirmar sobre o arquivo —
# e que os dois leitores estao a menos de `SCORE_TOLERANCIA` um do outro, e so
# nas edicoes em que o motor publicou um numero. A igualdade exacta nao e
# garantida por nada: a validacao aceita a ancora e o cartao a +/- 0,05, e numa
# semana em que o score vem em n/d o motor escreve "N/A" nos dois sitios e a
# validacao salta as comparacoes por completo. Nessa edicao os dois leitores
# caem nos seus recursos — que nunca coincidem — e o portao ficava vermelho
# para sempre a partir da sexta seguinte, sem carteira e sem newsletter, por
# causa de um ficheiro que ja esta no repositorio.
for _f in sorted(_g.glob(str(ROOT / "MRM_Newsletter_Issue*_*.html"))):
    _h = Path(_f).read_text(encoding="utf-8")
    _v = sn._score_visivel(_h)
    _a = _np2.parse_allocation(_h)[1]
    if _v is None:
        # Uma edicao de uma semana n/d nao publica score nenhum: o cartao diz
        # "N/A" e nao ha numero para os dois leitores concordarem. O que tem de
        # continuar verdade e que a edicao nao apresenta um numero qualquer do
        # texto como se fosse o score — e e por isso que `_score_visivel`
        # devolve None em vez de cair no recurso.
        true(_a is None or 0.0 <= _a <= 10.0,
             f"{Path(_f).name}: edicao sem score visivel nao inventa um "
             f"(motor leu {_a})")
        continue
    true(_v is not None and 0.0 <= _v <= 10.0,
         f"{Path(_f).name}: o score visivel e um numero da escala (obtido {_v})")
    # A folga afirmada e a que a validacao CONCEDE: ela compara cada leitor com
    # o motor a +/- SCORE_TOLERANCIA, portanto entre leitores a folga e o dobro.
    # Afirmar aqui a folga simples era exigir do arquivo mais do que o produtor
    # garante — e uma edicao publicada e valida trancava os dois portoes.
    true(_a is not None and abs(_v - _a) <= 2 * sn.SCORE_TOLERANCIA,
         f"{Path(_f).name}: o visivel e o que o motor le batem certo a menos de "
         f"{2 * sn.SCORE_TOLERANCIA} (visivel {_v}, motor {_a})")
true(sn.validate_newsletter(edicao(27, score=7.06), _c_score),
     "um score 0.1 acima do do motor e rejeitado")
eq(sn.validate_newsletter(edicao(27, score=7.04), _c_score), [],
   "e um que arredonda para o mesmo passa")

# ── O briefing ao dono nao publica "n/d%" ────────────────────────────────
# O `_pct` foi criado exactamente para isto e nao foi aplicado aqui, 200 linhas
# mais abaixo: o `%` e literal no f-string e cola-se ao "n/d".
_c_nd = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)
_c_nd["port_alpha"] = None
_c_nd["port_pnl"] = "n/d"
_tw = sn.build_tweet(_c_nd, "x.html")
true("n/d%" not in _tw, f"o tweet nao escreve 'n/d%' (obtido ...{_tw[-160:]})")
true("n/d" in _tw, "mas diz n/d")
_c_ok2 = sn.build_context(make_data(), make_portfolio(issue=27), PREV, D, 27, AGORA)
_c_ok2["port_alpha"] = -6.16
true("-6.16%" in sn.build_tweet(_c_ok2, "x.html"), "e com valor, publica-o com %")

# ── git_publish: rebase, retry, e o significado do `fatal` ───────────────
class _Res:
    def __init__(self, rc): self.returncode = rc
def _fake_run(chamadas, falhas_push):
    def run(cmd, **kw):
        chamadas.append(cmd)
        if cmd[:2] == ["git", "diff"]:
            return _Res(1)                     # ha alteracoes por commitar
        if cmd[-1] == "push" or cmd[:2] == ["git", "push"]:
            return _Res(1 if len(
                [c for c in chamadas if c[:2] == ["git", "push"]]) <= falhas_push else 0)
        return _Res(0)
    return run

_real_run, _real_sleep = sn.subprocess.run, sn.time.sleep
sn.time.sleep = lambda *_: None
try:
    _ch = []
    sn.subprocess.run = _fake_run(_ch, falhas_push=1)
    eq(sn.git_publish(["x.json"], "msg", tentativas=3), True,
       "um push recusado a primeira e tentado outra vez, e passa")
    true(sum(1 for c in _ch if c[:2] == ["git", "pull"]) >= 2,
         "e cada tentativa faz rebase antes do push")

    # Um rebase que fica a meio envenena as tentativas seguintes — e o modo de
    # falha que os tres workflows tratam explicitamente no shell. A funcao tem
    # de o limpar entre tentativas.
    true(any(c[:2] == ["git", "rebase"] and "--abort" in c for c in _ch),
         f"cada tentativa falhada limpa o rebase antes da seguinte ({_ch})")

    _ch = []
    sn.subprocess.run = _fake_run(_ch, falhas_push=99)
    eq(sn.git_publish(["x.json"], "msg", tentativas=3, fatal=False), False,
       "esgotadas as tentativas com fatal=False, devolve False")
    eq(sum(1 for c in _ch if c[:2] == ["git", "rebase"] and "--abort" in c), 3,
       "e cada uma das tres tentativas limpa o rebase")
    eq(sum(1 for c in _ch if c[:2] == ["git", "push"]), 3,
       "e tentou exactamente as vezes pedidas")

    _ch = []
    sn.subprocess.run = _fake_run(_ch, falhas_push=99)
    _rebentou = ""
    try:
        sn.git_publish(["x.json"], "msg", tentativas=2, fatal=True)
    except RuntimeError as e:
        _rebentou = str(e)
    true("nao foi possivel publicar" in _rebentou,
         f"e com fatal=True levanta ({_rebentou[:60]!r})")

    # Sem nada por commitar, nao ha commit nem push — e devolve True.
    _ch = []
    def _run_sem_alteracoes(cmd, **kw):
        _ch.append(cmd)
        return _Res(0)
    sn.subprocess.run = _run_sem_alteracoes
    eq(sn.git_publish(["x.json"], "msg"), True, "sem alteracoes, nada a publicar")
    true(not any(c[:2] == ["git", "push"] for c in _ch), "e nao ha push")
finally:
    sn.subprocess.run, sn.time.sleep = _real_run, _real_sleep

# ── Os marcadores do esqueleto tem de ser substituidos ───────────────────
# `SCORE_HERE`, `VERDICT_BOX_HERE`, `SECTOR_MATRIX_HERE` e companhia passavam
# como texto normal: uma edicao com dois deles por substituir tinha 15 mil
# caracteres, fechava as etiquetas todas, e era publicada e enviada com os
# marcadores em bruto no corpo.
_com_marcadores = edicao(27, score=6.97).replace(
    "<h2>CIO Verdict</h2>", "<h2>CIO Verdict</h2>VERDICT_BOX_HERE SECTOR_MATRIX_HERE")
_pm = [m for _t, m in sn.validate_newsletter(_com_marcadores, _c_score)]
true(any("marcadores do esqueleto" in m for m in _pm),
     f"uma edicao com marcadores por substituir e rejeitada ({_pm})")
true(any("VERDICT_BOX_HERE" in m and "SECTOR_MATRIX_HERE" in m for m in _pm),
     "e sao nomeados, para o modelo saber quais corrigir")
eq(sorted({t for t, m in sn.validate_newsletter(_com_marcadores, _c_score)
           if "marcadores" in m}), [sn.FALHA_FORMA],
   "e e falha de FORMA — nao se publica assim")
# Todos os marcadores do proprio esqueleto sao apanhados por esta regra.
import re as _re2
_prompt = sn.build_prompt(_c_score)
_marcadores = sorted(set(_re2.findall(r'\b[A-Z][A-Z0-9_]{3,}_HERE\b', _prompt)))
true(len(_marcadores) >= 5, f"o esqueleto tem marcadores ({_marcadores})")
for _mk in _marcadores:
    _h = edicao(27, score=6.97).replace("<h2>Gauge B</h2>", f"<h2>Gauge B</h2>{_mk}")
    true(any("marcadores do esqueleto" in m for _t, m in sn.validate_newsletter(_h, _c_score)),
         f"o marcador {_mk} e apanhado")
# E texto normal em maiusculas nao e confundido com um marcador.
_normal = edicao(27, score=6.97).replace("<h2>Gauge B</h2>",
                                         "<h2>Gauge B</h2><p>THE ERP IS HERE TO STAY</p>")
eq(sn.validate_newsletter(_normal, _c_score), [],
   "e maiusculas normais no texto nao sao confundidas com marcadores")

print(f"TODOS OS {ok} TESTES PASSARAM")
