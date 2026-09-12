#!/usr/bin/env python3
"""
send_newsletter.py — MRM Weekly Newsletter
==========================================
Lê o data.json e o portfolio.json, pede o HTML ao modelo, grava, actualiza o
arquivo no index.html, faz commit e envia por Brevo.

Este ficheiro NÃO decide nada sobre a carteira. O job 1 do pipeline de sexta
(update_portfolio.py) corre primeiro, decide e escreve o portfolio.json; este
job relata o que aconteceu. Até Set 2026 havia aqui uma segunda cópia das regras
de rebalanceamento — escrita em função do score — e uma terceira cópia do mapa de
ETF sem a divisão FTQ/Stress. No dia em que o medidor B disparasse, a carteira
rodava para Critical e a newsletter dizia aos subscritores "No structural regime
change detected". As regras vivem agora todas em mrm_rules.py.

Estrutura: a derivação do contexto e a construção do prompt são funções puras,
para poderem ser testadas sem rede (tests/test_newsletter.py). Só o main() toca
em ficheiros, git, API do modelo e Brevo.
"""

import glob as _glob
import html as _htmllib
import hashlib, json, os, re, subprocess, requests, shutil, time
from datetime import datetime, date, timedelta

import mrm_rules as rules
# O mesmo parser que o motor usa no sabado seguinte para ler a alocacao.
# Uma edicao cuja tabela nao passe aqui nao chega a ser publicada.
from newsletter_parse import parse_allocation
# A mesma pergunta que o motor faz ao data.json antes de decidir. O gerador
# publicava os mesmos numeros aos subscritores sem nunca a fazer.
from data_freshness import (DATA_REFUSE_AFTER_HOURS, DATA_WARN_AFTER_HOURS,
                            data_age_hours, limiar_declarado, series_paradas)

START_DATE = date(2026, 3, 13)

# Tecto de geracao. A maior edicao escrita ate Set 2026 ronda os 7000 tokens; com
# o tecto a 8000 uma semana mais densa passava a fronteira sem que nada avisasse.
MAX_TOKENS = 16000
# As 26 edicoes publicadas andam pelos 18-20 mil caracteres. O limiar fica bem
# abaixo disso de proposito: o que ele apanha e o fragmento — uma resposta que
# nem sequer chega a ser uma edicao — e nao uma semana mais curta do que o
# habitual. Aperta-lo para perto do tamanho real transformaria uma variacao
# normal de comprimento numa semana sem newsletter.
MIN_NEWSLETTER_CHARS = 8000
# Quantas geracoes se tentam antes de desistir da semana. Cada nova tentativa
# leva a lista de problemas da anterior.
NEWSLETTER_TENTATIVAS = 3
# O score sai do motor com uma casa decimal; o modelo escreve-o no cartao. Uma
# diferenca maior do que meia casa nao e arredondamento, e um numero errado.
SCORE_TOLERANCIA = 0.05
# Tempo maximo gasto na FASE DE ENVIO. O job tem 60 minutos; a geracao pode
# comer 46 no pior caso; o que sobra e isto, com margem para o resto. Esgotado,
# para-se e regista-se quem falta — em vez de o GitHub matar o job a meio do
# envio, antes de a marca ficar gravada.
SEND_BUDGET_SECONDS = 8 * 60
BAND_COLOR = {"Resilient": "#34D058", "Turbulence": "#F98C4F",
              "Critical": "#D73A49", "nd": "#8B949E"}

REBALANCE_STYLE = {          # (fundo, borda, ícone) por classe de motivo
    "stress_on":                 ("#2d1515", "#D73A49", "\u26a0"),
    "critical_subregime_switch": ("#2d1515", "#D73A49", "\u26a0"),
    "stress_off":                ("#0a1f18", "#34D058", "\u27f2"),
    "semestral":                 ("#1a3a5c", "#388BFD", "\u27f3"),
    # A adopcao dos pesos do regime e uma rotacao executada, uma vez. Azul de
    # rotacao, como o semestral: nao e um alarme nem um alivio.
    "adopt_regime_weights":      ("#1a3a5c", "#388BFD", "\u27f3"),
    # Sair de Resilient e uma rotacao executada, nao um alivio: o score subiu
    # acima de 4,0. Azul de rotacao, nao o verde de "o stress acabou".
    "resilient_off":             ("#1a3a5c", "#388BFD", "\u27f3"),
    "emergency":                 ("#122008", "#34D058", "\u26a0"),
    "no_allocation":             ("#2d1f0a", "#F98C4F", "\u26a0"),
    # Uma semana em que o rebalanceamento FALHOU nao pode ser desenhada com a
    # caixa verde de sucesso: o texto foi acrescentado, o estilo nao, e o
    # default e verde.
    "missing_prices_held":       ("#2d1f0a", "#F98C4F", "\u26a0"),
    "aborted_invalid_shares":    ("#2d1f0a", "#F98C4F", "\u26a0"),
    "valuation_incomplete_held":  ("#2d1f0a", "#F98C4F", "\u26a0"),
    "valuation_not_credible_held": ("#2d1f0a", "#F98C4F", "\u26a0"),
    "no_allocation_available":    ("#2d1f0a", "#F98C4F", "\u26a0"),
    "stale_allocation_held":      ("#2d1f0a", "#F98C4F", "\u26a0"),
    "hold":                      ("#14181d", "#8B96A3", "\u25cf"),
}
# Neutro, nao verde: um motivo que este mapa nao conhece nao pode ser desenhado
# como se tivesse corrido bem.
REBALANCE_STYLE_DEFAULT = ("#14181d", "#8B96A3", "\u25cf")


def issue_number_for(today):
    return ((today - START_DATE).days // 7) + 1


def _fmt_trigger(t, unit=""):
    # `or "n/d"`, nao `.get(..., "n/d")`: o produtor ESCREVE a chave com `null`
    # — o valor e a data sao atribuidos na mesma linha, portanto sem valor nao
    # ha data — e um valor por omissao de chave ausente nunca chega a entrar. O
    # prompt entregava `as of None` ao modelo, na mesma edicao em que o aviso
    # obrigatorio diz, correctamente, que a leitura nao se obteve. E o mesmo
    # defeito que ja foi fechado no `Score: None/10`.
    v, thr, asof = t.get("value"), t.get("threshold"), t.get("asOf") or "n/d"
    v_s = "n/d" if v is None else f"{v:+.2f}{unit}"
    linha = f"{v_s} (fires at >= {thr}{unit}, as of {asof})"
    # Uma serie parada devolve o mesmo numero todas as semanas. Publicar o valor
    # sem dizer que a observacao e de ha meses e publica-lo como se fosse leitura
    # desta semana — que e exactamente o que a versao anterior fazia.
    if t.get("stale"):
        idade = t.get("ageDays")
        linha += (" — SERIES STALE: not evaluated this week"
                  + (f", last observation is {idade} days old" if idade is not None else ""))
    return linha


def build_context(data, portfolio_data, prev_data, today, issue_number, agora=None):
    """Tudo o que o prompt, o cartão do arquivo e o tweet precisam. Função pura:
    não lê ficheiros nem rede. `agora` existe para se poder fixar o relógio nos
    testes da idade do data.json."""
    global_score = data.get("globalResilienceScore")
    pillars   = {p["id"]: p for p in data.get("pillars", [])}
    sentinels = {s["id"]: s for s in data.get("sentinels", [])}
    prev_pillars = {p["id"]: p for p in (prev_data or {}).get("pillars", [])}
    prev_score   = (prev_data or {}).get("globalResilienceScore")

    def wow(pid, cur):
        prev = prev_pillars.get(pid, {}).get("score")
        if prev is None or cur is None:
            return "\u2014"
        d = round(float(cur) - float(prev), 1)
        return f"\u25b2 +{d}" if d > 0 else f"\u25bc {d}" if d < 0 else "\u2014 0.0"

    wow_score = ""
    if prev_score is not None and isinstance(global_score, (int, float)):
        d = round(float(global_score) - float(prev_score), 1)
        wow_score = f"\u25b2 +{d} WoW" if d > 0 else f"\u25bc {d} WoW" if d < 0 else "\u2014 0.0 WoW"

    band = rules.score_band(global_score if isinstance(global_score, (int, float)) else None)

    # ── Medidor B ────────────────────────────────────────────────────────────
    stress   = data.get("stressGauge") or {}
    active   = stress.get("active")
    sub      = stress.get("subregime")
    triggers = stress.get("triggers", {})
    if active is None:
        # "both triggers unavailable" deixou de ser verdade quando o protocolo
        # n/d se alargou: UM gatilho em falta ao lado de UM gatilho quieto
        # tambem da `active is None` — porque a ausencia ao lado do silencio nao
        # e calma — e nessa semana um dos dois FOI lido. O `basis` que o medidor
        # publica ja diz qual e qual; usa-se esse, em vez de uma segunda copia
        # escrita a mao que so acerta num dos casos.
        # O `basis` do produtor ja comeca por "n/d — "; prefixa-lo outra vez dava
        # "n/d — n/d — ..." ao modelo.
        _basis_g = str(stress.get("basis")
                       or "no usable reading this run; previous state retained")
        gauge_b_line = (_basis_g if _basis_g.lstrip().startswith("n/d")
                        else f"n/d \u2014 {_basis_g}")
    else:
        gauge_b_line = ("ON" if active else "OFF") + f" ({stress.get('basis', 'n/d')})"
        if active and sub:
            gauge_b_line += f" | sub-regime: {sub}"

    # ── Carteira: factos já executados ───────────────────────────────────────
    #
    # A carteira que se publica tem de ser a DESTA edição. O job 2 lia o
    # portfolio.json do commit anterior ao push do job 1, e todas as edições
    # publicadas desde a #5 reportaram o P&L da semana anterior — 22 de 22
    # verificáveis. A causa está no workflow (checkout sem `ref: main`), mas o
    # gerador não pode confiar em ficheiro nenhum: verifica, e recusa publicar
    # números que não são desta semana em vez de os publicar em silêncio.
    cur  = portfolio_data.get("current", {})
    history = portfolio_data.get("history") or []
    hist = history[-1] if history else {}

    if issue_number is not None:
        desta = next((h for h in history if h.get("issue") == issue_number), None)
        if desta is None:
            raise ValueError(
                f"O portfolio.json nao tem a edicao {issue_number}: a ultima e a "
                f"{hist.get('issue')}. O job da carteira nao correu, falhou, ou "
                f"este processo esta a ler um commit anterior ao dele. Nao se "
                f"publica a carteira da semana passada com o numero desta.")
        hist = desta
        # Os numeros publicados — valor, P&L, alpha, regime, sub-regime — vem
        # todos de `cur`, nao de `desta`. Por isso `cur` tem de ser desta
        # edicao, e um `cur` SEM o campo `issue` nao pode passar: era assim que
        # a versao anterior desta guarda deixava publicar a semana errada.
        if cur.get("issue") != issue_number:
            raise ValueError(
                f"O portfolio.json corrente e da edicao {cur.get('issue')!r}, nao da "
                f"{issue_number}. Os numeros publicados vem daqui, portanto nao se "
                f"publica a carteira da semana passada com o numero desta.")
        # E a data tem de bater certo: um numero de edicao pode estar bem e a
        # entrada ser de outra semana.
        if today is not None:
            d_hist = str(desta.get("date") or "")
            try:
                dias = abs((date.fromisoformat(d_hist) - today).days)
            except (ValueError, TypeError):
                # Sem data, ou com data ilegivel, nao ha como confirmar que a
                # entrada e desta semana. Saltar a verificacao em silencio era
                # deixar a porta aberta ao defeito que ela existe para fechar.
                raise ValueError(
                    f"A entrada da edicao {issue_number} tem data {d_hist!r}, que "
                    f"nao e legivel. Nao se publica uma semana que nao se consegue "
                    f"datar.")
            if dias > 7:
                raise ValueError(
                    f"A entrada da edicao {issue_number} tem data {d_hist}, a "
                    f"{dias} dias de {today}. O numero bate certo mas a semana nao.")
    # Pelo MESMO ponto unico que o motor usa. O gerador — que e quem escreve aos
    # subscritores — lia os dois campos crus, com o default "Turbulence" que a
    # ronda anterior tirou do motor precisamente por devolver um regime LEGIVEL
    # para uma chave ausente. Numa re-corrida (o Caso 2 e o Caso 3 do RUNBOOK) o
    # job da carteira sai pela guarda de data ANTES de curar o ficheiro, e a
    # edicao saia a dizer "Turbulence" ao lado da lista de ETFs do vector de
    # crise FTQ — 35% em TLT — dentro do mesmo documento.
    port_regime, port_subregime, _nota_pr = rules.normaliza_regime(
        cur.get("regime"), cur.get("critical_subregime"),
        cur.get("active_etf_map"))
    if _nota_pr:
        # Este ficheiro nao tem logger: escreve para o stdout do job, que e onde
        # o operador ja procura tudo o resto.
        print(f"  [WARN] estado ilegivel no portfolio.json: {_nota_pr}")
    port_regime = port_regime or "Turbulence"
    reason         = hist.get("rebalance_reason", "hold")

    # Uma semana decidida sobre dados recusados por idade, ou com precos de
    # recurso, publicava P&L e alocacao sem uma palavra. Quem le tem de saber.
    # Escritos em ingles, e em ASCII simples, por duas razoes praticas. A
    # newsletter e em ingles (regra 3 do prompt): pedir ao modelo que reproduza
    # "verbatim" um paragrafo em portugues era uma instrucao impossivel de
    # cumprir sem quebrar a outra. E sem acentos, aspas curvas ou "&" porque
    # estes avisos sao depois PROCURADOS no HTML gerado para confirmar que
    # chegaram la — um caractere que o modelo escape ("&amp;") faria falhar uma
    # verificacao por uma razao que nao interessa a ninguem.
    avisos = []
    if hist.get("data_refused"):
        # A consequencia sai da carteira, nao da suposicao — a mesma correccao
        # que o aviso do `ndPillars` levou, deixada intacta no aviso gemeo
        # trezentas linhas acima. O `data_refused` impede o SCORE e o MEDIDOR de
        # decidir; o rebalanceamento semestral e calendario e executa na mesma.
        # Na ultima sexta de Janeiro ou de Junho com o data.json recusado, a
        # edicao anunciava SEMESTRAL_REBALANCE, `rb_done: True`, e afirmava —
        # palavra por palavra, sob pena de recusa — que as posicoes se
        # mantiveram. Duas vezes por ano.
        # E a conclusao sai do MOTIVO, nao do booleano: sob `data_refused` o
        # semestral nao e o unico gatilho possivel, e escrever "decided by the
        # calendar" sobre uma troca de sub-regime seria a falsidade seguinte.
        _rz_dr = str(hist.get("rebalance_reason") or "")
        if not hist.get("rebalance_triggered", False):
            _fim_dr = ", so positions were held rather than decided."
        elif _rz_dr == "semestral_rebalance":
            _fim_dr = (", so neither the score nor Gauge B decided anything this "
                       "week. The rebalance was the scheduled semi-annual one.")
        else:
            _fim_dr = (f", so neither the score nor Gauge B decided anything this "
                       f"week. The rebalance this week was triggered by "
                       f"{_rz_dr or 'another rule'}.")
        avisos.append("DATA QUALITY: the gauge ran without fresh data this week. "
                      "The data file was stale or unavailable" + _fim_dr)
    congelados = hist.get("valuation_frozen") or []
    em_falta   = hist.get("valuation_missing") or []
    # O limiar `PRICE_FROZEN_AFTER_DAYS` distingue "aproximacao" de "ja nao e
    # credivel", e ate aqui essa distincao morria num log que ninguem le. Um
    # preco congelado ha 7 dias e um congelado ha 300 levavam a MESMA frase
    # morna. Sao dois avisos porque sao duas avarias diferentes.
    _sem_credito = [_c for _c in (hist.get("valuation_not_credible") or [])
                    if _c in congelados]
    _limite_cong = hist.get("price_frozen_after_days")
    _aproximados = [_c for _c in congelados if _c not in _sem_credito]
    if _aproximados:
        avisos.append(
            f"DATA QUALITY: this week's portfolio value uses the last known price "
            f"for {', '.join(_aproximados)}, because the fresh quote failed. The "
            f"published profit and loss is an approximation until the price "
            f"source is fixed.")
    if _sem_credito:
        # A DATA do ultimo preco, nao a idade em dias. A idade cresce sete dias
        # por semana enquanto a avaria durar, e este aviso tem de aparecer na
        # edicao PALAVRA POR PALAVRA sob pena de FALHA_FORMA: com a idade la
        # dentro era uma frase nova todas as sextas para o modelo copiar sem
        # falhar — e tres tentativas falhadas nao dao edicao nenhuma, ou seja,
        # nem carteira publicada nem newsletter, exactamente durante a avaria
        # que o aviso existe para contar. A data e a mesma frase semana apos
        # semana. E a mesma correccao que o aviso da serie parada e o da ancora
        # do E/P ja levaram, pela mesma razao.
        _datas_cong = hist.get("valuation_frozen_dates") or {}
        _det = ", ".join(
            f"{_c} (last priced {_datas_cong[_c]})" if _c in _datas_cong else _c
            for _c in _sem_credito)
        _lim_txt = (f" the {_limite_cong}-day limit" if _limite_cong is not None
                    else " the declared limit")
        avisos.append(
            f"DATA QUALITY: the price used for {_det} has been frozen for longer "
            f"than{_lim_txt}. This is no longer an approximation: the valuation "
            f"of that position, and therefore this week's profit and loss, is not "
            f"credible until the price source is fixed.")
    if em_falta:
        # Aviso diferente, porque a avaria e diferente: aqui nao se usou preco
        # nenhum — a posicao ficou de fora da conta.
        avisos.append(
            f"DATA QUALITY: {', '.join(em_falta)} could not be valued this week. "
            f"There is neither a fresh quote nor a last known price, so the "
            f"published portfolio value is INCOMPLETE and this week's profit and "
            f"loss figure was "
            f"suppressed rather than computed over a partial portfolio.")
    if hist.get("rebalance_reason") == "aborted_invalid_shares":
        avisos.append("DATA QUALITY: a rebalance was computed but failed the value "
                      "check before execution. Positions were held.")
    if hist.get("data_stale") and not congelados:
        avisos.append("DATA QUALITY: at least one price this week is a fallback "
                      "(previous close), because the fresh quote failed.")
    if reason == "missing_prices_held":
        avisos.append("DATA QUALITY: a rebalance was due but at least one "
                      "instrument had no price. Positions were held rather than "
                      "executing an incomplete allocation.")
    if reason == "no_allocation_available":
        avisos.append("DATA QUALITY: a rebalance was due but this week's "
                      "allocation failed validation. Positions were held.")
    # A idade do PROPRIO data.json. O motor recusa decidir sobre um ficheiro com
    # mais de 48 horas e mantem as posicoes; a newsletter publicava score,
    # pilares e medidor B do mesmo ficheiro sem uma unica verificacao. Numa
    # re-corrida dias depois, ou com o job do fetch a ter falhado, saia uma
    # edicao a apresentar as leituras da semana passada como se fossem desta.
    _meta   = data.get("meta") or {}
    _fresh  = _meta.get("freshness") or {}
    _idade  = data_age_hours(_meta, agora)
    _recusa = limiar_declarado(_fresh, "refuseAfterHours", DATA_REFUSE_AFTER_HOURS)
    _avisa  = limiar_declarado(_fresh, "warnAfterHours", DATA_WARN_AFTER_HOURS)
    if _idade is None:
        avisos.append("DATA QUALITY: the data file does not say when it was "
                      "generated, so the readings in this edition cannot be "
                      "confirmed as this week's.")
    elif _idade > _recusa:
        avisos.append(f"DATA QUALITY: the readings in this edition are "
                      f"{_idade:.0f} hours old, past the {_recusa:.0f}-hour limit. "
                      f"They are the last ones available, not this week's.")
    elif _idade > _avisa:
        avisos.append(f"DATA QUALITY: the readings in this edition are "
                      f"{_idade:.0f} hours old. The data refresh did not run on "
                      f"its usual schedule.")

    # Uma serie do medidor A que deixou de ser publicada. O pilar sai do
    # composto e o score RENORMALIZA sobre os restantes — ou seja, muda — e ate
    # aqui isso chegava aos subscritores sem uma palavra sobre porque mudou.
    # A DATA da ultima observacao, nao a idade em dias.
    #
    # A idade cresce sete dias por semana enquanto a serie estiver parada, e o
    # aviso tem de aparecer na edicao PALAVRA POR PALAVRA sob pena de
    # FALHA_FORMA: era uma frase nova todas as semanas para o modelo copiar sem
    # falhar, durante todas as semanas em que a avaria durasse. A data da ultima
    # observacao e a mesma frase semana apos semana — e a mesma correccao que o
    # aviso da ancora do E/P ja levou, pela mesma razao.
    # E a data LIDA, nao reconstruida. O `data.json` ja publica a data exacta em
    # `meta.fredSeriesDates`; subtrair a idade ao relogio do gerador da outra
    # coisa numa corrida que atravesse a meia-noite UTC ou numa re-corrida sobre
    # um data.json de ontem — e ai a "last observation" publicada e falsa. A
    # reconstrucao fica como recurso, para um data.json que nao traga as datas.
    _datas_fred = (_meta.get("fredSeriesDates") or {})
    # A CONSEQUENCIA sai do que o ficheiro declara, nao de uma suposicao.
    #
    # O aviso dizia sempre "o pilar saiu do composto e os pesos foram
    # renormalizados" — para TODAS as series paradas. Mas duas das series
    # vigiadas nao alimentam pilar nenhum: a ICSA e uma sentinela, e a SP500 so
    # marca o E/P a mercado (o pilar Premium continua a pontuar, com os earnings
    # da referencia). Com uma delas parada, a edicao publicava, no mesmo
    # documento, "5/5 Pillars Active" e uma caixa a dizer que um pilar tinha
    # saido. E o aviso vai ao prompt com a ordem de o copiar PALAVRA POR PALAVRA
    # sob pena de recusa: a falsidade era obrigatoria, e um modelo que se
    # recusasse a escreve-la tres vezes deixava a semana sem newsletter nenhuma.
    _nd = set(data.get("ndPillars") or [])
    # Quando falta composicao a MAIS, nao ha renormalizacao nenhuma: o
    # `global_score` abandona o composto assim que o peso sobrevivente cai
    # abaixo do minimo declarado, e devolve None. Tres avisos obrigatorios
    # afirmavam "the remaining weights were renormalised" nessa semana — na
    # mesma edicao em que o cartao do score sai SEM numero. Um so predicado,
    # lido do ficheiro, para as tres frases.
    _sem_composto = data.get("globalResilienceScore") is None
    _renorm = (" and the remaining weights were renormalised"
               if not _sem_composto else
               ". There is no Resilience Score this week: too much of the "
               "composite is missing to renormalise over what remains")
    _serie_do_pilar = {}
    for _pid, _spec in (rules.PILLAR_SCORING or {}).items():
        _partes = [x.strip() for x in
                   re.split(r"[+,]", str(_spec.get("fredSeries") or "")) if x.strip()]
        for _s_nome in _partes:
            _serie_do_pilar[_s_nome] = _pid
            _serie_do_pilar[_s_nome.replace(" ", "")] = _pid
        # E o nome COMPOSTO, que e o que o produtor publica quando o pilar vem
        # de mais do que uma serie: a Liquidez sai de tres, e o `fetch_data`
        # marca-a parada sob a chave "NCBEILQ027S_FBCELLQ027S_GDP" — que nao e
        # nenhuma das tres. Sem esta linha, a unica serie composta do sistema
        # caia no ramo "nao alimenta pilar nenhum" e a edicao publicava, no
        # mesmo documento, "4/5 Pillars Active (liquidity n/d)" e uma caixa a
        # dizer que o composto estava inalterado. Deriva-se do mesmo campo, nao
        # de uma constante nova que possa divergir dele.
        if len(_partes) > 1:
            _serie_do_pilar["_".join(_partes)] = _pid
            _serie_do_pilar["".join(_partes)] = _pid
    # E as series que entram num pilar sem lhe dar o nome. A SP500 e o preco do
    # E/P do Premium: parada, o pilar continua a pontuar, mas pontua sobre o
    # preco de outro dia — e o composto com ele. O ramo "nao alimenta pilar
    # nenhum" afirmava, palavra por palavra e sob pena de recusa, "so the
    # Resilience Score is unchanged".
    _serie_indirecta = {}
    for _pid, _sx in (getattr(rules, "PILLAR_EXTRA_SERIES", {}) or {}).items():
        for _s_nome in _sx:
            _serie_indirecta[_s_nome] = _pid
            _serie_indirecta[_s_nome.replace(" ", "")] = _pid
    # E a sentinela que cada serie alimenta, para o ramo "nao alimenta pilar
    # nenhum" poder dizer o que ACONTECEU em vez do que costumava acontecer:
    # desde que existe o quarto estado, uma sentinela sem leitura fica em n/d, e
    # nao "reported on the last reading available".
    _sentinela_da_serie = {}
    for _s_pub in (data.get("sentinels") or []):
        _s_fred = str(_s_pub.get("fredSeries") or "")
        if _s_fred:
            _sentinela_da_serie[_s_fred] = _s_pub
            _sentinela_da_serie[_s_fred.replace(" ", "")] = _s_pub
    _pilares_avisados = set()
    for _serie, _dias in series_paradas(_meta):
        _data_obs = _datas_fred.get(_serie)
        if not _data_obs and _dias is not None:
            _data_obs = (today - timedelta(days=int(_dias))).isoformat()
        _quando = f" (last observation: {str(_data_obs)[:10]})" if _data_obs else ""
        _pilar = _serie_do_pilar.get(_serie) or _serie_do_pilar.get(
            _serie.replace("_", "").replace(" ", ""))
        _pilar_ind = _serie_indirecta.get(_serie) or _serie_indirecta.get(
            _serie.replace("_", "").replace(" ", ""))
        if _pilar:
            _pilares_avisados.add(_pilar)
        if _pilar and _pilar in _nd:
            _consequencia = (f" The {_pilar} pillar was excluded from the "
                             f"Resilience Score this week{_renorm}.")
        elif _pilar:
            _consequencia = (f" The {_pilar} pillar still scored this week, on "
                             f"the last reading available.")
        elif _pilar_ind and _pilar_ind in _nd:
            _consequencia = (f" It is the price side of the {_pilar_ind} pillar's "
                             f"earnings yield. That pillar has no reading this "
                             f"week for a separate reason and was excluded from "
                             f"the Resilience Score.")
        elif _pilar_ind:
            # Nao e a serie NOMEADA do pilar, mas entra nele: dizer que o score
            # esta inalterado seria falso — esta construido sobre um preco parado.
            _consequencia = (f" It is the price side of the {_pilar_ind} pillar's "
                             f"earnings yield: that pillar still scored this week, "
                             f"but on a stale price, and the Resilience Score is "
                             f"built on that score."
                             + (f" Pillars with no reading this week: "
                                f"{', '.join(sorted(_nd))}, reported separately."
                                if _nd else ""))
        elif _nd:
            # Nao alimenta pilar nenhum — mas HA pilares fora do composto por
            # outra razao, e dizer "o score esta inalterado" nessa semana e
            # falso. O aviso obrigatorio palavra por palavra nao pode afirmar
            # sobre o composto uma coisa que o proprio ficheiro contradiz.
            _consequencia = (f" It feeds no pillar; the Resilience Score this "
                             f"week reflects the exclusion of "
                             f"{', '.join(sorted(_nd))}, reported separately.")
        else:
            _sent_da = (_sentinela_da_serie.get(_serie)
                        or _sentinela_da_serie.get(
                            _serie.replace("_", "").replace(" ", "")) or {})
            # A segunda metade da frase so se escreve quando se SABE qual e o
            # consumidor. A M2SL nao e pilar nem sentinela — alimenta o filtro 3
            # da Golden Rule — e caia aqui: a edicao afirmava, sob pena de
            # recusa, "what it feeds is reported on the last reading available"
            # no mesmo dia em que a pagina dizia "1 of the 3 entry filters could
            # not be evaluated this week". E a forma exacta do defeito
            # historico "icsa" vs "jobless": o mapa serie->consumidor
            # incompleto, e o ramo por omissao a AFIRMAR em vez de se calar.
            _tronco = (" It feeds no pillar, so the Resilience Score is "
                       "unchanged.")
            if not _sent_da:
                # Consumidor desconhecido: diz-se o que se sabe e mais nada.
                _consequencia = _tronco
            elif (_sent_da.get("status") == "nd"
                  or _sent_da.get("value") is None):
                _consequencia = (_tronco[:-1] + "; the sentinel it feeds has "
                                 "no reading this week.")
            else:
                _consequencia = (_tronco[:-1] + "; what it feeds is reported "
                                 "on the last reading available.")
        avisos.append(
            f"DATA QUALITY: the FRED series {_serie} has stopped updating"
            + _quando + "." + _consequencia)

    # ── Um pilar fora do composto avisa-se por SI, nao por tabela ───────────
    #
    # O aviso da exclusao existia so como apendice do aviso de "serie parada".
    # Mas um pilar entra em n/d por mais do que uma via, e a mais comum nem
    # sequer produz uma serie parada: se a FRED devolve a serie VAZIA — avaria,
    # serie descontinuada, chave recusada — nao ha observacao nenhuma para
    # marcar como velha, `staleSeries` fica vazio, e a edicao saia sem uma
    # unica linha obrigatoria a dizer que o score dessa semana foi calculado
    # sobre quatro pilares. O mesmo para o Premium com a ancora vencida, que
    # nao e serie da FRED nenhuma. E desde que um composto incompleto deixou de
    # decidir o regime, a consequencia ja nao e so de leitura: e que a carteira
    # fica onde esta ate a leitura voltar. Isso tem de ir escrito.
    _motivos_nd = data.get("ndReasons") or {}
    _frase_motivo = {
        "stale-anchor": ("its hand-set earnings reference is past its expiry "
                         "and no longer counts as a reading"),
        "series": "its underlying FRED series returned no usable reading",
    }
    for _pid_nd in sorted(_nd):
        if _pid_nd in _pilares_avisados:
            continue          # ja foi dito, com a serie parada que o causou
        _mot = _motivos_nd.get(_pid_nd)
        _porque = _frase_motivo.get(_mot, "no usable reading was available")
        # Sem "n-barra-d" e sem "e comercial": sao os caracteres que o modelo
        # reescreve de varias maneiras, e este aviso vai com ordem de o copiar
        # a letra sob pena de recusa da edicao.
        # A CONSEQUENCIA sai da carteira, nao de uma suposicao sobre o que o
        # n/d implica.
        #
        # "positions were held rather than rotated" e verdade sobre o SCORE — um
        # composto incompleto nao move o regime em nenhuma direccao — mas nao e
        # verdade sobre a semana: o medidor B decide Critical sozinho, sem
        # passar pelo score, e a ultima sexta de Janeiro ou de Junho rebalanceia
        # por calendario. Numa dessas semanas a edicao afirmava, palavra por
        # palavra e sob pena de recusa, que as posicoes se mantiveram — no mesmo
        # documento que anuncia "STRESS_ON" e a carteira toda trocada. Um modelo
        # que se recusasse a escrever a contradicao tres vezes deixava a semana
        # sem newsletter nenhuma. Fica so a parte que e sempre verdade, e a
        # outra so quando a carteira confirma que nao houve transaccao.
        _sem_transaccao = not hist.get("rebalance_triggered", False)
        avisos.append(
            f"DATA QUALITY: the {_pid_nd} pillar has no reading this week "
            f"because {_porque}. It was excluded from the Resilience Score"
            f"{_renorm}. While a pillar is "
            f"missing, the score does not move the regime in either direction"
            + (", so no rebalance was triggered this week." if _sem_transaccao
               else ". Any rebalance this week was decided by Gauge B or by the "
                    "calendar, not by the score."))

    # O medidor B e quem decide o regime. Se um dos seus dois gatilhos nao foi
    # avaliado porque a serie do FRED deixou de ser actualizada, quem le tem de
    # o saber: a decisao da semana foi tomada com meio medidor.
    for _tid, _t in (triggers or {}).items():
        # A condicao e o gatilho NAO TER SIDO AVALIADO, que e o que interessa a
        # quem le. `stale` e um dos motivos, nao a condicao: uma falha de rede
        # na SAHMREALTIME deixa `fired: None` com `stale: False` (o
        # `observacao_velha` devolve False quando nao ha data nenhuma), e a
        # semana em que o regime foi decidido com meio medidor saia sem uma
        # palavra — exactamente o contrario do que o comentario acima declara.
        if _t.get("fired") is None:
            # `_idade_gatilho`, nao `_idade`: este ciclo sombreava a idade do
            # data.json calculada acima. Sem efeito hoje, mas uma armadilha.
            # E aqui a data ja vem escrita pelo produtor (`asOf`): usa-se essa,
            # em vez da idade, pela mesma razao — a idade muda todas as semanas
            # e o aviso tem de ser copiado a letra.
            _quando_gatilho = _t.get("asOf")
            _idade_gatilho = _t.get("ageDays")
            if not _quando_gatilho and _idade_gatilho is not None:
                _quando_gatilho = (today - timedelta(days=int(_idade_gatilho))).isoformat()
            # O que este gatilho decide sai do que o produtor DECLARA, e o que
            # ACONTECEU sai da carteira. A janela de 3 meses do 10Y nao decide o
            # regime: decide qual dos dois vectores de Critical se executa — e a
            # diferenca entre eles e um terco da carteira. Dizer "the regime was
            # decided without it" era falso.
            #
            # Mas "the sub-regime in force was retained" tambem nao serve para
            # todas as semanas, e era escrito em todas: a `subregime_from_gauge`
            # tem TRES ramos — entrada fresca, retencao, medicao — e nas semanas
            # em que a carteira nem sequer esta em Critical nao ha sub-regime
            # nenhum para reter. Numa entrada fresca com a DGS10 em falta o
            # motor nao reteve nada: rodou 100% da carteira para o vector
            # defensivo, e a edicao afirmava o contrario, palavra por palavra e
            # sob pena de recusa.
            if _t.get("decides") == "subregime":
                _o_que = (". It decides which of the two defensive vectors is "
                          "held inside Critical, not the regime itself")
                if port_regime != "Critical":
                    _o_que += (", and the portfolio is not in Critical this "
                               "week, so nothing turned on it.")
                elif str(reason).startswith("critical_subregime_switch"):
                    _o_que += (". The sub-regime changed this week for another "
                               "reason, so nothing was held over from last "
                               "week: the change is reported with the "
                               "rebalance.")
                elif reason == "stress_on":
                    _o_que += (". The portfolio entered Critical this week, so "
                               "there was no sub-regime to retain: it went to "
                               "the defensive vector, which is where a fresh "
                               "entry goes until a 10Y decline is confirmed.")
                else:
                    _o_que += (", so the sub-regime in force was retained "
                               "rather than switched.")
            else:
                _o_que = ". This week's regime was decided without it."
            # A CAUSA tambem sai do que o produtor declara. Nos dois gatilhos
            # antigos `stale` vem do `observacao_velha` e significa mesmo "serie
            # parada"; na janela do 10Y significa "nao foi medida" — e a causa
            # mais provavel e um SEGUNDO pedido HTTP que falhou, ou uma janela
            # com menos de 55 pontos, com a serie a publicar normalmente. A
            # edicao acusava a FRED de ter parado uma serie cuja ultima
            # observacao e do proprio dia, e que o pilar Premium pontuou.
            _porque_gatilho = {
                "stale": "its FRED series has stopped updating",
                "unavailable": ("its reading could not be retrieved this run, "
                                "even though the series itself is publishing"),
                "short-window": ("the series did not return enough observations "
                                 "to measure the window"),
                "no-window": ("the series published, but not the earlier "
                              "observation the reading is measured against"),
            }.get(_t.get("ndReason"), "it could not be evaluated this week")
            avisos.append(
                f"DATA QUALITY: the Gauge B trigger {_t.get('series') or _tid} was NOT "
                f"evaluated this week because {_porque_gatilho}"
                + (f" (last observation: {_quando_gatilho})" if _quando_gatilho else "")
                + _o_que)
    # O ECO da semana passada, declarado ao leitor.
    #
    # Quando a tabela publicada em Critical era o vector de crise que o proprio
    # prompt mostrou, o motor descarta-a e retem a macro anterior — mas a edicao
    # ja tinha dito aos subscritores, por ordem da regra 8, que aquela tabela
    # Aqui vivia o aviso do "eco": a edicao anterior tinha repetido o vector de
    # crise em vez de publicar uma macro, o motor recusara-a, e os subscritores
    # tinham de saber que percentagens iam retomar. A pergunta morreu com o
    # `REGIME_WEIGHTS` — o que retoma a saida de Critical e o vector de
    # Turbulence, esteja a edicao anterior como estiver.

    # A ancora manual dos earnings, que alimenta o pilar Premium, tem prazo de
    # validade e nao se actualiza sozinha. Passado o prazo, o E/P publicado
    # continua a mexer-se com o preco do indice mas os earnings ficaram para
    # tras — e o pilar Premium, que entra no composto, fica a dizer uma coisa
    # que ja nao e verdade. Ate aqui a unica coisa que o assinalava era um
    # [WARN] no log de um workflow verde, que ninguem le.
    _ep_ancora = ((pillars.get("premium") or {}).get("epAnchor") or {})
    if _ep_ancora.get("stale"):
        # A frase leva a DATA da referencia, nao a idade em dias.
        #
        # Cada aviso tem de aparecer na edicao palavra por palavra, e a validacao
        # recusa a edicao se faltar. Um numero que muda todas as semanas ("102
        # dias", "109 dias") e uma frase nova todas as semanas para o modelo
        # copiar sem falhar — e isto passa a ser obrigatorio em TODAS as edicoes
        # ate alguem actualizar a constante. A data nao muda enquanto a
        # referencia nao mudar, e diz exactamente a mesma coisa.
        _ep_data = (pillars.get("premium") or {}).get("epAsOf") or "an earlier date"
        # E o que a frase afirma tem de ser verdade: sem a serie do indice nao
        # houve fecho nenhum para marcar, e o E-P publicado e a referencia crua.
        #
        # A condicao sai do CAMPO que o motor preenche, nao de uma frase.
        # A primeira versao procurava a expressao "marked to the latest index
        # close" no `basis` — uma frase que o fetch_data nunca escreve (escreve
        # "price marked to the latest close") e que so existia no fixture do
        # teste. O ramo verdadeiro nunca corria: a partir do dia em que a ancora
        # ficasse velha, TODAS as edicoes diziam aos subscritores que a serie do
        # indice estava indisponivel numa semana em que estava disponivel e o
        # E-P tinha sido marcado a mercado. Nao dava portao vermelho: dava uma
        # falsidade publicada, com a suite verde.
        _marcado = _ep_ancora.get("indexNow") is not None
        # A CONSEQUENCIA sai do estado, nao da idade. Passado o segundo prazo o
        # pilar sai mesmo do composto e os pesos renormalizam — e o aviso
        # continuava a dizer "may be stale", que e o que se diz quando ele ainda
        # esta la. O leitor via "4/5 Pillars Active", um score um ponto abaixo, e
        # uma caixa a dizer "pode estar desactualizado".
        if "premium" in _nd:
            _consequencia_ep = (
                f"The Premium pillar was excluded from the Resilience Score "
                f"this week{_renorm}. Past its expiry the reference no longer "
                f"counts as a measurement.")
        elif _marcado:
            _consequencia_ep = (
                "The earnings yield in this edition is that reference marked to "
                "the latest index close, so the Premium pillar may be stale.")
        else:
            _consequencia_ep = (
                "The earnings yield in this edition is that reference itself, "
                "because the index series was unavailable this week.")
        avisos.append(
            f"DATA QUALITY: the earnings reference behind the Premium pillar is "
            f"still the one from {_ep_data} and has not been refreshed since. "
            + _consequencia_ep)

    etf_map = cur.get("active_etf_map") or rules.REGIME_ETF_MAP[
        rules.resolve_etf_map_key(port_regime, port_subregime)]
    alloc = cur.get("bucket_allocation_pct", {})

    regime_label = rules.REGIME_LABELS.get(port_regime, port_regime)
    if port_regime == "Critical" and port_subregime:
        regime_label += f" \u00b7 {rules.SUBREGIME_LABELS.get(port_subregime, port_subregime)}"

    style = REBALANCE_STYLE_DEFAULT
    for key, val in REBALANCE_STYLE.items():
        if reason.startswith(key):
            style = val
            break

    # As sentinelas eram lidas por id fixo ("icsa"), mas o motor escreve "jobless",
    # "erp" e "unemployment": durante 26 edicoes a newsletter reportou
    # "ICSA: N/A Alert:False" e a taxa de desemprego nunca chegou ao modelo.
    # Passa a ser construida a partir do que la estiver.
    sentinel_line = " | ".join(
        f"{s.get('name', sid)}: {s.get('displayValue', s.get('value', 'n/d'))}"
        f" (threshold {s.get('thresholdDisplay', s.get('threshold', 'n/d'))}, "
        f"alert {bool(s.get('alert'))})"
        for sid, s in sentinels.items()
    ) or "n/d"

    nd = data.get("ndPillars") or []
    # O total sai das regras, nao do numero 5 escrito a mao. O sistema tem cinco
    # pilares hoje; no dia em que tiver seis, esta linha publicava "5/5 Pillars
    # Active" com um pilar em n/d — e o mesmo documento traria, ao lado, a caixa
    # a dizer que ele saiu do composto. O site ja deriva o seu contador de
    # `RULES.pillarOrder`; a newsletter era a ultima copia com o numero cravado.
    _total_pilares = len(rules.PILLAR_ORDER)
    pillars_live = f"{_total_pilares - len(nd)}/{_total_pilares} Pillars Active"
    if nd:
        pillars_live += f" ({', '.join(nd)} n/d)"

    nxt = rules.next_semestral_date(today)

    return {
        "avisos": avisos,
        "tem_avisos": bool(avisos),
        "today": today.strftime("%d %B %Y"),
        "today_file": today.strftime("%d%b%Y"),
        "issue_number": issue_number,
        "score": round(global_score, 1) if isinstance(global_score, (int, float)) else "N/A",
        "wow_score": wow_score,
        "score_band": band,
        "score_color": BAND_COLOR[band],
        "pillars_live": pillars_live,
        "pillars": pillars,
        "sentinels": sentinels,
        "sentinel_line": sentinel_line,
        "wow": wow,
        "stress_active": active,
        "gauge_b_line": gauge_b_line,
        "sahm_line": _fmt_trigger(triggers.get("sahmRealtime", {})),
        "npl_line": _fmt_trigger(triggers.get("delinquencyAccel", {}), " pp"),
        "regime_label": regime_label,
        "port_regime": port_regime,
        "port_subregime": port_subregime,
        "rb_reason": reason,
        "rb_alert": reason.upper(),
        "rb_status": rules.rebalance_copy(reason),
        "rb_done": bool(hist.get("rebalance_triggered", False)),
        "rb_color": style[0], "rb_border": style[1], "rb_icon": style[2],
        "port_etfs": " | ".join(etf_map.get(b, "?") for b in rules.BUCKETS),
        "alloc_line": (" | ".join(f"{b}: {alloc.get(b, 0):.0f}%" for b in rules.BUCKETS)
                       if alloc else "n/a"),
        # O mesmo vector em numeros, para a validacao comparar com a tabela que
        # o modelo escrever. A linha acima e para o prompt ler; esta e para o
        # motor verificar. Sao a mesma coisa de proposito: o que se manda
        # escrever e o que se exige de volta.
        "alloc_efectiva": dict(alloc or {}),
        # A alocacao MACRO em vigor — a que retoma quando o medidor B desligar.
        # Ja nao e uma memoria do que alguma edicao escreveu: e o vector de
        # Turbulence das regras, que e o que a carteira VAI executar. O prompt
        # mostra-o para a edicao o poder dizer aos subscritores; o motor nao o
        # le de volta de lado nenhum.
        "macro_line": " | ".join(
            f"{b}: {rules.REGIME_WEIGHTS['Turbulence'][b]:.0f}%"
            for b in rules.BUCKETS),
        "port_value": cur.get("portfolio_value", "N/A"),
        # None quando o motor suprimiu o P&L por a valorizacao estar incompleta.
        # Publicar "n/d" e o comportamento certo; publicar um numero calculado
        # sobre uma carteira parcial nao e.
        # `cur.get(k, "N/A")` era inalcancavel: uma chave ausente ja devolve
        # None e cai no ramo do n/d. O default so escondia a intencao.
        "port_pnl": ("n/d" if cur.get("portfolio_pnl_pct") is None
                     else cur.get("portfolio_pnl_pct")),
        "port_alpha": ("n/d" if cur.get("alpha_vs_benchmark_pct") is None
                       else cur.get("alpha_vs_benchmark_pct")),
        "next_sem": nxt.strftime("%d %B %Y") if nxt else "N/A",
    }


def build_prompt(c):
    """O prompt do modelo. Função pura: recebe o contexto e devolve texto."""
    # Um aviso de qualidade de dados nao pode ficar so no ficheiro: se a semana
    # foi decidida sem dados frescos, quem recebe a newsletter tem de o ler.
    _avisos = c.get("avisos") or []
    _avisos_bloco = ("\nDATA QUALITY — each line below MUST appear in the newsletter "
                     "word for word, inside a visible box near the portfolio "
                     "section. Do not paraphrase, translate or shorten them; the "
                     "edition is rejected before publication if any is missing:\n"
                     + "\n".join(f"  ! {a}" for a in _avisos)) if _avisos else ""
    """O prompt do modelo. Os nomes locais existem para que o texto abaixo se
    mantenha legível como texto."""
    today          = c["today"];         issue_number = c["issue_number"]
    score          = c["score"];         wow_score    = c["wow_score"]
    score_band     = c["score_band"];    score_color  = c["score_color"]
    _pillars_live  = c["pillars_live"];  wow          = c["wow"]
    pil            = c["pillars"]
    cycle, liquidity, premium = pil.get("cycle", {}), pil.get("liquidity", {}), pil.get("premium", {})
    solvency, debt            = pil.get("solvency", {}), pil.get("debt", {})
    sentinel_line             = c["sentinel_line"]
    _gauge_b_line  = c["gauge_b_line"];  _sahm_line   = c["sahm_line"]
    _npl_line      = c["npl_line"];      _regime_label = c["regime_label"]
    _rb_alert      = c["rb_alert"];      _rb_status   = c["rb_status"]
    _rb_done       = c["rb_done"];       _port_etfs   = c["port_etfs"]
    _alloc_line    = c["alloc_line"];    _next_sem    = c["next_sem"]
    _macro_line    = c.get("macro_line", "n/a")
    # As bandas saem do MOTOR, nao de uma copia escrita a mao no prompt.
    # Eram dois numeros iguais em dois sitios, e apertar uma banda no motor sem
    # tocar no prompt da um modelo obediente a escrever uma tabela que o motor
    # rejeita — semana mantida, e numa semana semestral seis meses de espera.
    # As bandas de alocacao iam no prompt como o envelope dentro do qual o modelo
    # podia escolher as percentagens. Deixou de haver escolha nenhuma para
    # enquadrar: a regra 8 da-lhe o vector exacto e a validacao rejeita a edicao
    # que dele se afaste mais de um ponto. Um envelope a volta de um numero fixo
    # so ensinaria o modelo que ha margem.
    # "n/d%" nao e nada: o % e literal no f-string e cola-se ao n/d.
    _pct = lambda v: "n/d" if v in (None, "n/d", "N/A") else f"{v}%"
    # `.get(k, "N/A")` nao apanha um valor NULO: a chave existe, e o que la esta
    # e None. Com um pilar em n/d, o prompt entregava ao modelo "Score: None/10"
    # e um modelo obediente escrevia-o na tabela que vai para os subscritores. O
    # `render_archive_card` e o `build_tweet` ja tinham a funcao certa; as cinco
    # linhas do prompt e que nao a usavam.
    _campo = lambda d_, k_: ("n/d" if d_.get(k_) is None else d_.get(k_))
    _port_value    = c["port_value"];    _port_pnl    = c["port_pnl"]
    _port_alpha    = c["port_alpha"]
    _rb_color      = c["rb_color"];      _rb_border   = c["rb_border"]
    _rb_icon       = c["rb_icon"]

    return f"""You are a Senior Risk Strategist and CIO. Generate a complete MRM Weekly Institutional Newsletter in HTML.

TODAY: {today} | ISSUE: #{issue_number}

THE SYSTEM HAS TWO GAUGES. They answer different questions and must never be conflated.

GAUGE A — GLOBAL RESILIENCE SCORE (leading fragility, 6-18 month horizon)
- Score: {score}/10 ({wow_score}) | Band: {score_band} | {_pillars_live}
- Cycle: {_campo(cycle, 'value')} | Score: {_campo(cycle, 'score')}/10 | WoW: {wow('cycle', cycle.get('score'))} | {_campo(cycle, 'status')}
- Liquidity: {_campo(liquidity, 'value')} | Score: {_campo(liquidity, 'score')}/10 | WoW: {wow('liquidity', liquidity.get('score'))} | {_campo(liquidity, 'status')}
- Premium (ERP): {_campo(premium, 'value')} | Score: {_campo(premium, 'score')}/10 | WoW: {wow('premium', premium.get('score'))} | {_campo(premium, 'status')}
- Solvency: {_campo(solvency, 'value')} | Score: {_campo(solvency, 'score')}/10 | WoW: {wow('solvency', solvency.get('score'))} | {_campo(solvency, 'status')}
- Debt: {_campo(debt, 'value')} | Score: {_campo(debt, 'score')}/10 | WoW: {wow('debt', debt.get('score'))} | {_campo(debt, 'status')}
- Sentinels — {sentinel_line}

What Gauge A is: a measure of how much there is to go wrong over the next 6-18
months. What it is not: a measure of whether anything is going wrong now. In a
downturn three of its five pillars mechanically improve — the curve steepens,
valuations compress, the risk premium widens — so the composite falls into a
crisis rather than rising. Backtested on 2005-2026 it never reached 8.0, not
even in 2008.

GAUGE B — CONCURRENT STRESS (0-3 month horizon). THIS DECIDES THE REGIME.
- State: {_gauge_b_line}
- Sahm rule, real-time vintage: {_sahm_line}
- Bank delinquency, 4-quarter change: {_npl_line}

Two published triggers, neither calibrated on this sample. Backtested on
1996-2026 real-time vintages it fires in 2001, 2008, 2020 and 2024, and stays
silent through the 2011, 2018 and 2022 bear markets. The 2024 firing is a known
false positive of the Sahm rule.

PORTFOLIO — WHAT ALREADY HAPPENED THIS WEEK (facts, not forecasts)
- Operative regime: {_regime_label}
- Rebalance outcome: {_rb_alert} — {_rb_status}
- Executed: {"yes" if _rb_done else "no transactions"}
- Active instruments: {_port_etfs}
- Effective allocation now: {_alloc_line}
- Macro allocation on record (this is what resumes when Gauge B stands down,
  and it is NOT the same thing as the effective allocation above): {_macro_line}
- Next scheduled semi-annual rebalance: {_next_sem}
- Portfolio Value: ${_port_value} | P&L: {_pct(_port_pnl)} | Alpha vs SPY: {_pct(_port_alpha)}
{_avisos_bloco}

RULES:
1. Institutional tone. Dry. Objective. Risk-focused.
2. Do not name individual tickers anywhere in the analysis, the sector matrix or
   the allocation table — write sectors, factors and asset classes. The single
   exception is the PORTFOLIO REBALANCE STATUS box, where the active instruments
   above are a fact of the portfolio and are listed as such.
3. English only.
4. Include the WoW column in all tables.
5. Deep Dive on the biggest WoW mover.
6. Return ONLY complete HTML. No markdown. No backticks.
7. NEVER explain the current market state, or the portfolio regime, with the
   Gauge A score. The regime above was decided by Gauge B. If the two disagree —
   a high score with Gauge B off, or a falling score with Gauge B on — say so
   plainly: it is the system working as designed, not a contradiction.
8. The allocation table REPORTS the allocation the engine has already executed.
   It is not an instruction and the engine does not read it back: the weights
   come from the regime, in mrm_rules.py, and nothing you write here can change
   a single dollar of the portfolio. What you can do is misreport it, which is
   why the numbers are given to you and checked after you write them:
   - the table must be exactly this, bucket by bucket: {_alloc_line}
   - exactly ONE table in the whole document may have "Asset Class" as its first
     header cell, and that is the allocation table;
   - it must have exactly ONE column of percentages, headed "Regime Target";
   - its rows must cover the six buckets and total 100;
   - an edition whose table does not match the executed allocation to within one
     percentage point is REJECTED before it is sent. Do not "improve" the
     numbers, round them differently, or reconcile them with your own analysis.
   - When the operative regime is Critical, this table is the fixed Critical
     vector the portfolio is holding now. Say, in the allocation section, that
     when Gauge B stands down the portfolio returns to the macro allocation
     shown above ({_macro_line}) — that vector is fixed in the rules too, so
     state it as a fact and not as a forecast or a recommendation.
   - Your analysis this week may of course DISAGREE with the allocation. Say so
     in prose, plainly, and say what you would do differently and why. What you
     may not do is write a table that shows something the portfolio is not
     holding.
9. After the CIO Verdict section, include a PORTFOLIO REBALANCE STATUS section
   using the facts above. Style it as a distinct box with background {_rb_color},
   border-left 4px solid {_rb_border}, icon {_rb_icon}. Show: operative regime,
   rebalance outcome and its explanation, active instruments, effective
   allocation, next semi-annual date, portfolio value / P&L / alpha.
10. Include a short GAUGE B section, before the CIO Verdict, reporting the two
   trigger values against their thresholds and what the state means for the
   portfolio. Two or three sentences, no speculation about when it might fire.

HTML STRUCTURE:
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
.score-band{{background:#161B22;padding:20px 28px;display:flex;align-items:center;gap:24px;border-bottom:3px solid {score_color};}}
.score-circle{{width:72px;height:72px;border-radius:50%;border:3px solid {score_color};display:flex;align-items:center;justify-content:center;flex-shrink:0;}}
.score-num{{font-family:'Courier New',monospace;font-size:26px;font-weight:700;color:{score_color};line-height:1;}}
.score-info .regime{{font-family:'Courier New',monospace;font-size:9px;color:{score_color};letter-spacing:0.16em;text-transform:uppercase;margin-bottom:4px;}}
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
<div class="score-band"><div class="score-circle"><div class="score-num" data-mrm-score="{score}">SCORE_HERE</div></div><div class="score-info"><div class="regime">● REGIME_HERE REGIME</div><div class="score-label">Global Resilience Score</div><div class="score-sub">Updated: {today} · FRED API Live · {_pillars_live} · WOW_HERE</div></div></div>
<div class="content">EXECUTIVE_SUMMARY_HERE PILLARS_TABLE_HERE DEEP_DIVE_HERE</div>
<hr class="page-divider"><div class="page-label">— Tactical Execution —</div>
<div class="content" style="padding-top:0;">EWS_TABLE_HERE SECTOR_MATRIX_HERE ALLOCATION_TABLE_HERE VERDICT_BOX_HERE PORTFOLIO_REBALANCE_STATUS_HERE</div>
<div class="footer"><div class="footer-logo">US<span>MRM</span> Intelligence Hub</div><div class="footer-links"><a href="https://usmrm.net">Live Terminal</a><a href="https://usmrm.net">Newsletter</a><a href="https://usmrm.net">BDCs</a><a href="mailto:usmrm@proton.me">Contact</a></div><div class="footer-disclaimer">This newsletter is produced for educational and personal analysis purposes only. It does not constitute financial advice.<br>All data sourced from FRED API · © 2026 US MRM Intelligence Hub · usmrm.net</div></div>
</div></body></html>

Replace ALL_CAPS placeholders with complete real HTML content. Return ONLY the final HTML."""


def render_archive_card(c, filename):
    """Cartão da edição no arquivo do index.html."""
    issue_number = c["issue_number"]; today = c["today"]
    regime = c["regime_label"];       score = c["score"]
    score_color = c["score_color"];   wow_score = c["wow_score"]
    p = c["pillars"]
    def _sc(pid):
        v = p.get(pid, {}).get("score")
        return "n/d" if v is None else v
    cycle_s, liquidity_s = _sc("cycle"), _sc("liquidity")
    premium_s, solvency_s, debt_s = _sc("premium"), _sc("solvency"), _sc("debt")
    # `if c["stress_active"]` tratava n/d (None) como OFF e pintava o cartao com
    # a cor do score — que pode ser verde. Uma semana em que o medidor nao pode
    # ser avaliado nao e uma semana verde. E a mesma correccao que o regimeTone()
    # do index.html levou; o cartao do arquivo tinha ficado de fora.
    badge_color = ("#D73A49" if c["stress_active"] else
                   "#8B96A3" if c["stress_active"] is None else score_color)
    return f"""
      <!-- ISSUE #{issue_number} -->
      <div style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px;">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid var(--border-subtle);">
          <div style="display:flex;align-items:center;gap:16px;">
            <div style="font-family:var(--mono);font-size:11px;font-weight:600;color:var(--text-muted);">ISSUE #{issue_number}</div>
            <div style="width:1px;height:16px;background:var(--border);"></div>
            <div style="font-family:var(--mono);font-size:11px;color:var(--text-muted);">{today}</div>
            <div style="padding:2px 8px;border-radius:4px;background:rgba(0,0,0,0.25);border:1px solid {badge_color};font-family:var(--mono);font-size:9px;font-weight:600;color:{badge_color};">{regime.upper()}</div>
          </div>
          <div style="display:flex;align-items:center;gap:20px;">
            <div style="text-align:right;"><div style="font-family:var(--mono);font-size:9px;color:var(--text-muted);text-transform:uppercase;">Score</div><div style="font-family:var(--mono);font-size:20px;font-weight:600;color:{score_color};">{score}</div></div>
            <div style="font-family:var(--mono);font-size:11px;color:{score_color};">{wow_score}</div>
          </div>
        </div>
        <div style="padding:16px 24px;display:grid;grid-template-columns:repeat(5,1fr);gap:12px;">
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Cycle</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{cycle_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Liquidity</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{liquidity_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Premium</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--red);">{premium_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Solvency</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--green);">{solvency_s}</div></div>
          <div style="text-align:center;"><div style="font-family:var(--mono);font-size:8px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;">Debt</div><div style="font-family:var(--mono);font-size:14px;font-weight:600;color:var(--orange);">{debt_s}</div></div>
        </div>
        <div style="padding:0 24px 20px;display:flex;gap:12px;">
          <a href="/{filename}" target="_blank" style="padding:8px 16px;background:var(--blue-dim);border:1px solid rgba(56,139,253,0.3);border-radius:6px;color:var(--blue);font-size:12px;font-weight:500;text-decoration:none;">Read Full Issue →</a>
          <a href="https://twitter.com/intent/tweet?text=🧊+MRM+Weekly+Signal+Issue+%23{issue_number}+%7C+Score+{score}%2F10+%7C+{regime}%0Ausmrm.net%0A%23MacroInvesting+%23ERP+%23Finance" target="_blank" style="padding:8px 16px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;color:var(--text-secondary);font-size:12px;text-decoration:none;">𝕏 Share</a>
        </div>
      </div>"""


def build_tweet(c, filename):
    issue_number = c["issue_number"]; score = c["score"]; wow_score = c["wow_score"]
    regime = c["regime_label"]; p = c["pillars"]
    g = "ON" if c["stress_active"] else "OFF" if c["stress_active"] is False else "n/d"
    def _sc(pid):
        v = p.get(pid, {}).get("score")
        return "n/d" if v is None else v
    return f"""\U0001f9ca MRM Weekly Signal \u2014 Issue #{issue_number}

Score {score}/10 {wow_score} | {regime}
Gauge B (concurrent stress): {g}

Pillars:
\u00b7 Cycle: {_sc('cycle')}/10
\u00b7 Liquidity: {_sc('liquidity')}/10
\u00b7 Premium (ERP): {_sc('premium')}/10
\u00b7 Solvency: {_sc('solvency')}/10
\u00b7 Debt: {_sc('debt')}/10

Portfolio: ${c["port_value"]} | Alpha vs SPY: {"n/d" if c["port_alpha"] in (None, "n/d", "N/A") else str(c["port_alpha"]) + "%"}
Rebalance: {c["rb_alert"]}

Read \u2192 usmrm.net/{filename}

#MacroInvesting #ERP #Finance #WeekendReading"""


# ─────────────────────────────────────────────────────────────────────────────
# I/O, rede e efeitos — só a partir daqui
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: {what} ({path}) — {e}")
        return {}


def call_model(prompt, api_key, retries=3, timeout=300):
    """Chamada ao modelo, com prazo e tentativas.

    Sem `timeout`, um pedido pendurado corria ate ao limite de seis horas do
    GitHub Actions — e como nenhum job declarava `timeout-minutes`, ninguem
    dava por isso ate ao fim do dia. Erros transitorios (5xx, 429) merecem nova
    tentativa; um 4xx de pedido invalido nao, porque a segunda tentativa daria
    o mesmo."""
    ultimo = None
    for tentativa in range(retries):
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-opus-4-6", "max_tokens": MAX_TOKENS,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                break
            ultimo = f"{r.status_code} — {r.text[:300]}"
            if r.status_code < 500 and r.status_code != 429:
                raise RuntimeError(f"Claude API error: {ultimo}")
        if tentativa == retries - 1:
            raise RuntimeError(f"Claude API falhou apos {retries} tentativas: {ultimo}")
        espera = 10 * (tentativa + 1)
        print(f"  [retry] modelo indisponivel ({ultimo}); nova tentativa em {espera}s")
        time.sleep(espera)

    # A resposta chegou com 200 — isso diz que a API respondeu, nao que a
    # newsletter esta inteira. Uma geracao que bate no tecto de tokens devolve
    # 200 com `stop_reason: "max_tokens"` e o HTML cortado a meio de uma frase.
    # Ate aqui esse texto era gravado, publicado no site e enviado a todos os
    # subscritores, e era ele que o motor lia na semana seguinte para decidir a
    # carteira. O tecto de 8000 tokens estava a ~1000 da maior edicao ja escrita.
    payload = r.json()
    parado = payload.get("stop_reason")
    if parado == "max_tokens":
        raise RuntimeError(
            f"O modelo esgotou o limite de {MAX_TOKENS} tokens: a newsletter veio "
            f"truncada. Nao e publicada nem enviada.")
    blocos = payload.get("content") or []
    textos = [b.get("text", "") for b in blocos if isinstance(b, dict) and b.get("type") == "text"]
    if not textos:
        raise RuntimeError(f"O modelo respondeu sem texto (stop_reason={parado!r}).")

    html = "".join(textos).strip()
    if html.startswith("```"):
        html = html.split("```html")[-1].split("```")[0].strip()
    return html


def _normalizar(txt):
    """Texto reduzido ao que um leitor reconheceria como a mesma frase.

    A lista de entidades escrita a mao nao chegava. Quatro dos oito avisos tem um
    apostrofo ("this week's ..."), e o modelo escreve-o de tres maneiras —
    `\u2019`, `&#8217;`, `&rsquo;` — das quais so a primeira estava contemplada.
    A consequencia era desproporcionada: a semana com avisos e, por definicao, a
    semana degradada, e era essa que ficava com maior probabilidade de nao
    produzir edicao nenhuma. Agora desfazem-se TODAS as entidades com o
    html.unescape e dobra-se a pontuacao equivalente."""
    txt = _htmllib.unescape(txt or "")
    # Toda a pontuacao vira espaco. Nao se esta a comparar tipografia, esta-se a
    # perguntar "esta frase aparece nesta pagina?": aspas curvas, travessoes,
    # espacos duros e a diferenca entre "P/L" e "P&L" nao mudam a resposta, e
    # deixar qualquer uma delas fazer falhar a verificacao seria calar a edicao
    # por uma razao que nao interessa a ninguem.
    txt = re.sub(r"[^0-9a-z]+", " ", txt.lower())
    return txt.strip()


def _texto_visivel(html):
    """O HTML sem etiquetas, normalizado. E o que um leitor ve, e e sobre isso
    que se procura um aviso: procurar a frase no HTML cru falharia por uma
    quebra de linha dentro de um <p> ou por uma entidade."""
    txt = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return _normalizar(txt)


# Uma edicao pode falhar de duas maneiras muito diferentes, e trata-las da
# mesma forma foi o erro da primeira versao desta validacao.
#
#  FORMA   — veio truncada, nao e HTML, nao se identifica, faltam os avisos de
#            qualidade de dados. Nao ha edicao nenhuma para publicar.
#  ALOCACAO— o documento esta inteiro, mas a tabela de alocacao nao passa no
#            parser do motor (percentagens fora das bandas, total errado,
#            classes que nao mapeiam). Isto ja acontecia antes desta validacao
#            existir, e a consequencia era o motor manter as posicoes na semana
#            seguinte. Recusar publicar por causa disto seria trocar "o
#            rebalanceamento nao aconteceu" por "nao houve newsletter nenhuma" —
#            um preco mais alto do que o problema.
FALHA_FORMA = "forma"
FALHA_ALOCACAO = "alocacao"


# `score-num` como TOKEN da classe. Como substring, `class="score-number"` num
# elemento decorativo anterior ao cartao casava primeiro, e os dois leitores do
# score — o do subscritor e o do motor — passavam a olhar para o sitio errado.
_RE_CARTAO_SCORE = re.compile(
    r'<(\w+)[^>]*\bclass="[^"]*(?<![-\w])score-num(?![-\w])[^"]*"[^>]*>', re.I)


def _conteudo_do_cartao(html, m):
    """O texto dentro da etiqueta que `m` abriu, ate ao primeiro fecho."""
    fim = html.find("</", m.end())
    return html[m.end():fim] if fim != -1 else html[m.end():]


def _score_visivel(html):
    """O score tal como o leitor o ve: o numero dentro do cartao do score.

    Le-se do CONTEUDO da etiqueta, nao do atributo — o atributo e o que a
    maquina le, e sao coisas que podem divergir. Sem o cartao, procura-se o
    primeiro numero com uma casa decimal entre 0 e 10 no texto visivel, que e
    onde o score aparece na abertura de todas as edicoes."""
    # `score-num` como TOKEN da classe, e nao como substring: `class="score-number"`
    # ou `class="score-num-sm"` noutro sitio do documento casava primeiro e o
    # cartao verdadeiro nem chegava a ser lido — a validacao queixava-se de que
    # "a edicao nao mostra o score" sobre uma edicao que o mostra, e a queixa era
    # impossivel de satisfazer nas tres tentativas: semana sem newsletter.
    #
    # E TODOS os cartoes, nao so o primeiro: o numero e o do cartao que o tem.
    cartoes = [_conteudo_do_cartao(html, m) for m in _RE_CARTAO_SCORE.finditer(html)]
    if not cartoes:
        m = re.search(r'<[^>]*data-mrm-score[^>]*>(.*?)</', html, re.I | re.S)
        cartoes = [m.group(1)] if m else []
        if not cartoes:
            cartoes = None
    if cartoes is not None:
        for corpo in cartoes:
            # Sem `_normalizar` aqui: ela reduz tudo a alfanumericos e come o ponto
            # decimal, transformando "4.2" em "4 2" e o score em 4,0.
            cru = _htmllib.unescape(re.sub(r"<[^>]+>", " ", corpo))
            n = re.search(r'(-?\d+(?:\.\d+)?)', cru)
            if n:
                return float(n.group(1))
        # O cartao existe e NAO tem numero: e uma semana n/d, em que o motor nao
        # publica score nenhum. Devolver None e a resposta certa. Cair no recurso
        # aqui seria varrer o documento a procura de qualquer decimal e devolver
        # o primeiro que aparecesse — um numero que ninguem publicou como score,
        # apresentado como se fosse ele.
        return None
    # Sem cartao nenhum — as edicoes anteriores a existencia do cartao — procura-se
    # o primeiro numero com uma casa decimal entre 0 e 10 no texto visivel, que e
    # onde o score aparece na abertura dessas edicoes.
    visivel = _htmllib.unescape(re.sub(r"<[^>]+>", " ", html))
    for n in re.finditer(r'(?<![\d.])(\d{1,2}\.\d)(?![\d])', visivel):
        v = float(n.group(1))
        if 0.0 <= v <= 10.0:
            return v
    return None


def ancora_fora_do_cartao(html):
    """A ancora existe mas NAO esta na etiqueta que mostra o score.

    Sao dois leitores sobre a mesma coisa: o `_score_visivel` le o CONTEUDO da
    etiqueta com `score-num` — e o que o subscritor ve —, e o
    `parse_allocation` le o ATRIBUTO `data-mrm-score` — e o que o motor le no
    sabado seguinte para decidir a carteira. Se o atributo estiver numa etiqueta
    e o numero noutra, a validacao ve dois numeros iguais e deixa passar:

        <div class="score-num">7.0</div><span data-mrm-score="7.0"></span>

    E depois divergem. A reescrita que o portao faz para ensaiar uma semana n/d
    apaga o conteudo da etiqueta ANCORADA e deixa o `score-num` a mostrar 7.0:
    a edicao — ja publicada e ENVIADA — passa a ser um input que o portao nao
    consegue transformar, e como e uma das mais recentes o portao fica vermelho
    sobre um ficheiro commitado. Sexta sem carteira e sem newsletter, e sem
    recuperacao, porque quem publicaria as edicoes seguintes e o job travado.
    Pior ainda no caminho normal: um dia os dois numeros divergem de verdade e
    os subscritores leem um score que nao e o que a carteira executa.

    Sem ancora nenhuma nao ha queixa: as 26 edicoes publicadas antes de a ancora
    existir tem `score-num` e mais nada, e uma verificacao sobre estado
    commitado que ninguem pode alterar e um portao que nao se pode satisfazer.
    Sem `score-num` tambem nao: ai a etiqueta ancorada E o cartao, e os dois
    leitores caem na mesma.
    """
    if not re.search(r"data-mrm-score", html, re.I):
        return False
    cartoes = list(_RE_CARTAO_SCORE.finditer(html))
    if not cartoes:
        return False
    for m in cartoes:
        # Na propria etiqueta...
        if "data-mrm-score" in m.group(0).lower():
            return False
        # ...ou DENTRO dela: o `_score_visivel` le o conteudo do cartao, portanto
        # `<div class="score-num"><span data-mrm-score="7.0">7.0</span></div>` e
        # o mesmo pedaco de texto para os dois leitores. Exigir a ancora no
        # atributo da etiqueta de fora recusaria uma edicao correcta, e a queixa
        # e FALHA_FORMA: as tres tentativas falhavam e a semana ficava sem
        # newsletter.
        if "data-mrm-score" in _conteudo_do_cartao(html, m).lower():
            return False
    return True


def validate_newsletter(html, c):
    """Devolve a lista de razoes para NAO publicar esta edicao. Vazia = publicavel.

    Funcao pura. O que se verifica aqui nao e estilo — e o contrato com quem le a
    seguir: os subscritores, o site, e sobretudo o motor da carteira, que no
    sabado seguinte extrai desta pagina a alocacao que vai executar. Ate esta
    versao nao se verificava nada: o que o modelo devolvesse era gravado,
    commitado e enviado."""
    problemas = []
    texto = (html or "").strip()
    if not texto:
        return [(FALHA_FORMA, "o modelo devolveu texto vazio")]

    baixo = texto.lower()
    if len(texto) < MIN_NEWSLETTER_CHARS:
        problemas.append((FALHA_FORMA, f"a edicao tem {len(texto)} caracteres; uma edicao "
                                       f"completa tem pelo menos {MIN_NEWSLETTER_CHARS}"))
    if "<html" not in baixo:
        problemas.append((FALHA_FORMA, "a resposta nao e um documento HTML (falta <html>)"))
    if not baixo.endswith("</html>"):
        problemas.append((FALHA_FORMA, "a resposta nao termina em </html>: veio cortada"))
    if "</body>" not in baixo:
        problemas.append((FALHA_FORMA, "falta a etiqueta de fecho </body>"))
    # <div> e <table> nao sao etiquetas vazias e o modelo escreve-as sempre aos
    # pares. Um desequilibrio e um documento incompleto, mesmo que por acaso
    # traga um </html> no fim.
    for tag in ("div", "table"):
        abre  = len(re.findall(rf"<{tag}\b", baixo))
        fecha = len(re.findall(rf"</{tag}\s*>", baixo))
        if abre != fecha:
            problemas.append((FALHA_FORMA, f"<{tag}> abre {abre} vezes e fecha {fecha}: HTML incompleto"))

    # Os marcadores do esqueleto do prompt. `SCORE_HERE`, `VERDICT_BOX_HERE`,
    # `SECTOR_MATRIX_HERE` e companhia passavam como texto normal: uma edicao com
    # dois deles por substituir tinha 15 mil caracteres, fechava as etiquetas
    # todas, e era publicada e enviada com os marcadores em bruto no corpo.
    sobraram = sorted(set(re.findall(r'\b[A-Z][A-Z0-9_]{3,}_HERE\b', texto)))
    if sobraram:
        problemas.append((FALHA_FORMA,
                          "a edicao tem marcadores do esqueleto por substituir: "
                          + ", ".join(sobraram)))

    n = c.get("issue_number")
    if n is not None and f"#{n}" not in texto:
        problemas.append((FALHA_FORMA, f"a edicao nao se identifica como #{n}"))

    # A verificacao que importa mais: a tabela de alocacao tem de ser legivel
    # pelo MESMO parser que o motor usa. Uma tabela que nao passe aqui e uma
    # semana em que o rebalanceamento nao aconteceria, em silencio.
    alloc, score_html, notas = parse_allocation(texto)

    # O score que o modelo escreveu tem de ser o score do motor.
    #
    # O parser ja o extraia e a validacao deitava-o fora. Um numero errado — e o
    # modelo escreve numeros errados — chegava aos subscritores e ao site, e so
    # era notado sete dias depois, como um log.warning do motor que ninguem le.
    # Verificam-se OS DOIS numeros, e por boas razoes diferentes.
    #
    # A ancora `data-mrm-score` vai no esqueleto do prompt ja preenchida com o
    # valor do motor: compara-la com o motor e compara-la consigo propria. E uma
    # tautologia — e foi exactamente o que a versao anterior desta verificacao
    # passou a fazer quando a ancora foi introduzida. O que os subscritores leem
    # e o numero VISIVEL, e e esse que tem de bater certo: uma edicao com
    # `data-mrm-score="7.0"` e um "4.2" no cartao passava, e 4,2 e 7,0 estao em
    # bandas de regime diferentes.
    #
    # A ancora continua a ser verificada, mas por outra razao: e ela que o motor
    # le na semana seguinte. Se o modelo lhe mexer, o motor passa a ler um numero
    # que ninguem publicou.
    esperado = c.get("score")
    visto = _score_visivel(texto)
    # Antes dos dois ramos, porque vale nos dois: o numero que o subscritor le e
    # o numero que o motor le tem de ser o MESMO pedaco de texto.
    if ancora_fora_do_cartao(texto):
        problemas.append((FALHA_FORMA,
                          "a ancora data-mrm-score nao esta na etiqueta que "
                          "mostra o score: o subscritor le o conteudo do "
                          "score-num e o motor le o atributo, e assim podem "
                          "divergir. A ancora vai NA etiqueta do cartao, como "
                          "o esqueleto do prompt a escreve"))
    if isinstance(esperado, (int, float)):
        # A ancora TEM de existir e ser legivel.
        #
        # Enquanto a sua ausencia foi tolerada em silencio, o modelo podia
        # escrever o cartao de meia duzia de maneiras plausiveis — "7.0 / 10",
        # "7.0<span>/10</span>", "Score 7.0" — em que o `_score_visivel` le 7,0 e
        # o parser do motor nao le nada. A edicao era publicada e enviada, e a
        # partir da semana seguinte o portao que compara os dois leitores sobre o
        # arquivo ficava vermelho para sempre, por causa de um ficheiro ja
        # commitado: sexta sem carteira e sem newsletter, sem recuperacao. E a
        # ancora nao e decorativa — e ela que o motor le na semana seguinte.
        if score_html is None:
            problemas.append((FALHA_FORMA,
                              f"o motor calculou {esperado} mas o parser nao "
                              f"consegue ler o score na edicao: falta a ancora "
                              f"data-mrm-score, ou o cartao tem mais do que o "
                              f"numero"))
        elif abs(score_html - float(esperado)) > SCORE_TOLERANCIA:
            problemas.append((FALHA_FORMA,
                              f"a ancora do score diz {score_html} mas o motor "
                              f"calculou {esperado}"))
        if visto is None:
            problemas.append((FALHA_FORMA,
                              f"a edicao nao mostra o score {esperado} em lado nenhum"))
        elif abs(visto - float(esperado)) > SCORE_TOLERANCIA:
            problemas.append((FALHA_FORMA,
                              f"a edicao MOSTRA o score {visto} mas o motor "
                              f"calculou {esperado}"))
    else:
        # Semana n/d: o motor NAO calculou score nenhum. Ate aqui toda a
        # verificacao estava dentro do ramo numerico, portanto nesta semana — a
        # da avaria, a que mais precisa de ser lida com cuidado — o modelo podia
        # escrever o numero que lhe apetecesse no cartao de capa do produto e
        # nada o impedia. Nao ha numero certo para comparar: o que se exige e
        # que nao apareca numero nenhum onde o score se le.
        # O cartao tem de existir tambem nesta semana. Sem esta exigencia, uma
        # edicao sem cartao caia no varrimento do documento, que apanhava o
        # "+0.0" da coluna WoW da tabela de alocacao — uma coluna que o proprio
        # prompt OBRIGA a incluir. O modelo recebia, nas tres tentativas, a
        # instrucao de tirar um numero obrigatorio que nao estava em cartao
        # nenhum, e a semana ficava sem newsletter: a semana da avaria.
        if not re.search(r'class="[^"]*score-num|data-mrm-score', texto, re.I):
            problemas.append((FALHA_FORMA,
                              "o motor nao calculou score esta semana (n/d) e a "
                              "edicao nao tem o cartao do score: tem de o ter, com "
                              "a ancora data-mrm-score=\"N/A\" e sem numero"))
        elif visto is not None:
            problemas.append((FALHA_FORMA,
                              f"o motor nao calculou score esta semana (n/d) mas a "
                              f"edicao mostra {visto} no cartao do score"))
        # A ancora le-se COMO ANCORA, nao pelo `parse_allocation`.
        #
        # O `parse_allocation` so casa a ancora quando ela tem um numero; numa
        # semana n/d ela diz "N/A" e ele cai no recurso, que devolve o primeiro
        # decimal nu de qualquer etiqueta do documento — o "6.97" da caixa de
        # rebalanceamento, que o proprio prompt obriga a incluir. A queixa
        # resultante era falsa E impossivel de satisfazer: as tres tentativas
        # recebiam a mesma mensagem e a semana da avaria ficava sem newsletter.
        # Passando as 26 edicoes reais do arquivo pela validacao como se fossem
        # semana n/d, dez eram recusadas assim.
        _ancora_crua = re.search(r'data-mrm-score="([^"]*)"', texto, re.I)
        if _ancora_crua:
            try:
                _n_ancora = float(_ancora_crua.group(1).strip())
            except ValueError:
                _n_ancora = None
            if _n_ancora is not None:
                problemas.append((FALHA_FORMA,
                                  f"o motor nao calculou score esta semana (n/d) mas a "
                                  f"ancora data-mrm-score diz {_n_ancora}"))

    if not alloc:
        erros = [m for nivel, m in notas if nivel == "error"] or ["motivo nao registado"]
        problemas.append((FALHA_ALOCACAO, "a tabela de alocacao nao passa no parser do motor: "
                                          + "; ".join(erros)))
    else:
        # A tabela ja nao INSTRUI a carteira — o vector vem do regime — mas
        # continua a ser o que os subscritores leem como sendo a carteira. Uma
        # edicao que publique percentagens diferentes das que o motor executou
        # nao move um dolar e mente a toda a gente, o que e pior de ler e melhor
        # de apanhar: aqui compara-se numero a numero, antes de publicar.
        #
        # A tolerancia e do `allocation_matches_rules` e existe porque a tabela e
        # escrita para uma pessoa: 41,45% aparece como 41%.
        _bate, _desvios = rules.allocation_matches(alloc, c.get("alloc_efectiva"))
        if not _bate:
            problemas.append((FALHA_ALOCACAO,
                              "a tabela publicada nao e a alocacao que a carteira "
                              "tem: " + "; ".join(_desvios)))

    # Os avisos de qualidade de dados nao sao decorativos: sao a diferenca entre
    # publicar um P&L calculado sobre precos velhos e dize-lo a quem o le.
    visivel = _texto_visivel(texto)
    for aviso in (c.get("avisos") or []):
        if _normalizar(aviso) not in visivel:
            problemas.append((FALHA_FORMA, f"falta o aviso de qualidade de dados: {aviso[:80]}..."))

    return problemas


AVISO_ALOCACAO_HTML = (
    '<div style="background:#2d1f0a;border-left:4px solid #F98C4F;padding:14px 16px;'
    'margin:0 0 16px 0;border-radius:0 6px 6px 0;font-family:Arial,sans-serif;">'
    '<div style="font-family:\'Courier New\',monospace;font-size:9px;font-weight:600;'
    'letter-spacing:0.16em;text-transform:uppercase;color:#F98C4F;margin-bottom:6px;">'
    '&#9888; Allocation not validated</div>'
    '<p style="font-size:12.5px;color:#2D3139;line-height:1.7;margin:0;">'
    'The allocation table in this edition did not pass the portfolio engine&#39;s '
    'validation. The percentages below are published as written, but the engine '
    'will NOT execute them. A scheduled semi-annual rebalance is cancelled outright '
    'while this is the most recent readable edition, and positions are held. If a '
    'regime change forces a rebalance before then, the engine uses the last '
    'allocation that did validate — not this one. Treat the allocation section of '
    'this edition as commentary, not as the portfolio&#39;s instruction.</p></div>')


def marcar_alocacao_invalida(html):
    """Insere a faixa de aviso logo a seguir ao <body>.

    A alternativa era nao publicar. Uma tabela que o motor rejeita ja acontecia
    antes desta validacao existir, e a consequencia era o motor manter as
    posicoes — em silencio. Publicar com o aviso a dizer isso e melhor do que
    nao publicar, e muito melhor do que publicar em silencio."""
    if AVISO_ALOCACAO_HTML in html:
        return html
    m = re.search(r"<body[^>]*>", html, re.IGNORECASE)
    if not m:
        return AVISO_ALOCACAO_HTML + html
    return html[:m.end()] + AVISO_ALOCACAO_HTML + html[m.end():]


def generate_newsletter(c, api_key, tentativas=NEWSLETTER_TENTATIVAS, gerar=None):
    """Gera a edicao e so a devolve depois de passar em validate_newsletter.

    Uma geracao falhada nao e fatal a primeira: o modelo recebe a lista de
    problemas e tenta de novo.

    Esgotadas as tentativas, a decisao depende do TIPO de problema. Um documento
    truncado ou sem os avisos de qualidade nao se publica — nao ha ali edicao
    nenhuma. Uma tabela de alocacao que o motor rejeitaria publica-se, com uma
    faixa a dizer exactamente isso: e o que ja acontecia antes desta validacao
    existir, e calar a semana inteira por causa disso seria pagar por um
    problema um preco maior do que o problema."""
    gerar = gerar or call_model
    prompt_base = build_prompt(c)
    ultimo, ultimo_html = None, None
    for tentativa in range(1, tentativas + 1):
        prompt = prompt_base
        if ultimo:
            prompt += ("\n\nA versao anterior foi REJEITADA antes de ser publicada, "
                       "por estes motivos. Corrige-os e devolve o documento completo:\n"
                       + "\n".join(f"  - {m}" for _t, m in ultimo))
        html = gerar(prompt, api_key)
        problemas = validate_newsletter(html, c)
        if not problemas:
            if tentativa > 1:
                print(f"  [modelo] edicao aceite a tentativa {tentativa}")
            return html
        print(f"  [modelo] tentativa {tentativa}/{tentativas} rejeitada:")
        for _t, msg in problemas:
            print(f"    - [{_t}] {msg}")
        ultimo, ultimo_html = problemas, html

    forma = [m for t, m in (ultimo or []) if t == FALHA_FORMA]
    if forma:
        raise RuntimeError(
            f"A newsletter nao passou a validacao em {tentativas} tentativas e "
            "NAO foi publicada nem enviada. Problemas de forma: " + "; ".join(forma))
    print("  [modelo] a forma esta correcta; so a tabela de alocacao nao passa. "
          "Publica-se com a faixa de aviso — o motor mantem as posicoes.")
    return marcar_alocacao_invalida(ultimo_html)


def update_archive(index_path, card_html, issue_number):
    with open(index_path, "r", encoding="utf-8") as f:
        index = f.read()
    marker = "<!-- NEWSLETTER_ARCHIVE_START -->"
    if marker not in index:
        print("Warning: archive marker not found in index.html")
        return False
    # Remove um cartão existente da mesma edição, para que uma re-corrida
    # correctiva substitua em vez de duplicar.
    # O `\Z` como fronteira era uma armadilha viva: se o cartao a substituir
    # fosse o ULTIMO do documento, esta substituicao apagava tudo o que vinha a
    # seguir — hoje, 270 linhas incluindo todo o JavaScript da pagina. Nunca
    # disparou porque as edicoes sao inseridas da mais recente para a mais
    # antiga, mas nao e disso que uma pagina deve depender. A fronteira passa a
    # ser o proximo cartao ou o fim da zona do arquivo.
    fim_arquivo = "<!-- NEWSLETTER_ARCHIVE_END -->"
    index = re.sub(rf'\s*<!-- ISSUE #{issue_number} -->.*?'
                   rf'(?=<!-- ISSUE #|{re.escape(fim_arquivo)})',
                   '', index, count=1, flags=re.S)
    index = index.replace(marker, marker + "\n\n      " + card_html)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index)
    return True


def git_publish(files, message, tentativas=3, fatal=True, aviso=None):
    """Commit e push, com rebase e nova tentativa.

    `fatal=False` para o que corre DEPOIS do envio: a essa altura os
    subscritores ja receberam, e uma excepcao aqui punha o job vermelho. O
    operador re-corria o pipeline, e a re-corrida gerava uma edicao nova (texto
    diferente, porque quem a escreve e um modelo), publicava-a por cima e
    enviava a lista toda outra vez. Um push que falha depois do envio e um
    problema de sincronizacao, nao uma razao para reenviar a semana."""
    subprocess.run(["git", "config", "user.email", "action@github.com"], check=True)
    subprocess.run(["git", "config", "user.name", "MRM Newsletter Bot"], check=True)
    subprocess.run(["git", "add", *files], check=True)
    if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
        return True
    subprocess.run(["git", "commit", "-m", message], check=True)
    # O mesmo laco que os outros jobs ganharam: um push recusado por
    # concorrencia nao pode descartar o trabalho da semana a primeira.
    for tentativa in range(1, tentativas + 1):
        r1 = subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"])
        if r1.returncode == 0 and subprocess.run(["git", "push"]).returncode == 0:
            print("Pushed to GitHub")
            return True
        subprocess.run(["git", "rebase", "--abort"], stderr=subprocess.DEVNULL)
        print(f"  [retry] push recusado (tentativa {tentativa}/{tentativas})")
        time.sleep(5 * tentativa)
    if fatal:
        raise RuntimeError(f"nao foi possivel publicar {files} apos {tentativas} tentativas")
    # O aviso por omissao e o de DEPOIS do envio. Quem chama antes tem de
    # trazer o seu: dizer "a newsletter ja foi enviada, nao re-correr" a quem
    # ainda nao enviou nada faz perder a semana.
    print(aviso or (f"AVISO: {files} nao foi publicado. A newsletter JA FOI "
                    f"ENVIADA; nao voltar a correr o envio — corrigir o "
                    f"repositorio a mao."))
    return False


# Marca de que a edicao ja saiu. Sem ela, qualquer falha DEPOIS do envio punha o
# job vermelho e a re-corrida reenviava a lista toda, com um texto diferente.
SENT_MARKER = "sent_issues.json"


# O normalizador do numero de edicao vive nas REGRAS, nao aqui: o
# `portfolio.json` tem cinco leitores do mesmo campo e um `sort` que levanta
# TypeError com uma string, o que mata o job da carteira TODAS as sextas, para
# sempre — o unico caminho que faria o ficheiro avancar e o que esta travado.
numero_de_edicao = rules.numero_de_edicao


class MarcaIlegivel(Exception):
    """O registo de envios existe mas nao se consegue ler."""


def already_sent(issue_number, path=SENT_MARKER):
    """A entrada desta edicao no registo, ou None se ainda nao foi enviada.

    Ausente e ilegivel sao coisas diferentes e nao podem ter a mesma resposta.
    Ausente e a primeira corrida: segue-se. Ilegivel — ficheiro truncado por um
    push interrompido, marcadores de conflito de merge — nao quer dizer "nao foi
    enviada": quer dizer que nao se sabe. Engolir a excepcao fazia o guarda
    contra o reenvio desaparecer em silencio, e o `mark_sent` a seguir apagava o
    historico todo por cima."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            registo = json.load(f)
    except Exception as e:
        raise MarcaIlegivel(
            f"{path} existe mas nao se consegue ler ({e}). Nao e possivel saber "
            f"se a edicao ja foi enviada, e por isso NAO se envia. Corrigir o "
            f"ficheiro a mao antes de voltar a correr.") from e
    if not isinstance(registo, dict) or not isinstance(registo.get("sent", []), list):
        raise MarcaIlegivel(f"{path} nao tem a forma esperada. NAO se envia.")
    for e in registo["sent"]:
        if isinstance(e, dict) and numero_de_edicao(e.get("issue")) == issue_number:
            return e
    return None


def marca_destinatario(email):
    """A marca de um destinatario no registo publicado — nunca o endereco.

    O `sent_issues.json` e commitado e empurrado para `main`, e a raiz do ramo e
    servida em usmrm.net: gravar aqui os enderecos publicava a lista de
    subscritores no site e deixava-a no historico do git para sempre. O proprio
    `brevo_send` existe para nao pôr a lista no cabecalho de cada mensagem —
    "uma exposicao de dados pessoais, nao um detalhe de estilo" — e este ficheiro
    fazia pior.

    A retoma de uma entrega parcial so precisa de saber SE um endereco ja foi
    servido, nao qual e: um digest chega. Nao e anonimato perfeito — quem ja
    tenha um endereco pode testa-lo — mas deixa de haver lista para ler.
    """
    return hashlib.sha256(("mrm:" + email.strip().lower()).encode("utf-8")).hexdigest()[:16]


_MARCA_RE = re.compile(r"^[0-9a-f]{16}$")


def marcas(emails):
    """Marca cada endereco, e deixa passar o que ja e uma marca.

    A retoma junta o que veio do ficheiro (ja marcado) com o que foi servido
    nesta passagem (enderecos). Sem esta idempotencia, a segunda passagem
    marcava outra vez o que ja estava marcado e a comparacao deixava de bater —
    quem ja tinha recebido recebia de novo."""
    saida = set()
    for e in (emails or []):
        e = str(e).strip()
        if _MARCA_RE.match(e):
            saida.add(e)
            continue
        # O MESMO reconhecedor que limpa o resto do ficheiro. Isto digeria a
        # LINHA INTEIRA, e o `_sem_enderecos` extrai o endereco de dentro dela:
        # `Ana Silva <ana@exemplo.pt>` — a forma que o RUNBOOK convida a
        # escrever numa recuperacao — dava aqui uma marca e ali outra. A retoma
        # compara os dois conjuntos, nao batiam, e a Ana recebia a edicao uma
        # SEGUNDA VEZ. Duas escritas da mesma ideia divergem sempre.
        _achados = _EMAIL_RE.findall(e) if "@" in e else []
        if len(_achados) == 1 and _achados[0] != e:
            print("AVISO: uma entrada do registo trazia texto a volta do "
                  "endereco; marcou-se o endereco, nao a linha.")
        saida.add(marca_destinatario(_achados[0] if len(_achados) == 1 else e))
    return sorted(saida)


# Um endereco e uma corrida de caracteres que NAO sao delimitadores, dos dois
# lados de um `@`, com o dominio a acabar num ponto e num sufixo. NAO e um
# alfabeto escrito a mao: `[A-Za-z0-9._%+-]` deixava passar `jose@exemplo.pt`
# inteiro — o caractere antes do `@` nao esta na classe, portanto NAO HAVIA
# MATCH DE TODO e o endereco era commitado em claro para `main` e servido em
# usmrm.net, semana apos semana. O mesmo para qualquer dominio acentuado. Um
# alfabeto escrito a mao fica sempre para tras de alguma lingua; a propriedade
# "nao e espaco nem pontuacao de delimitacao" nao fica.
# A parte local segue o `atext` da RFC 5322 — uma ESPECIFICACAO, nao uma lista
# de opinioes — mais os caracteres nao-ASCII da RFC 6531 (SMTPUTF8). A tentativa
# anterior dizia estar a fugir do alfabeto escrito a mao e escrevia outro: uma
# lista de delimitadores que incluia o apostrofo, que e `atext` legal e comum em
# nomes portugueses e irlandeses. `ana'silva@exemplo.pt` era publicado como
# `ana'<marca>` — o endereco partido ao meio, com a primeira parte EM CLARO. E
# do lado do dominio a mesma lista comia o URL inteiro em
# `https://usmrm.net/x?u=ana@exemplo.pt`.
_ATEXT = r"A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-\u0080-\U0010FFFF"
_DTEXT = r"A-Za-z0-9.\-\u0080-\U0010FFFF"
# A parte local tambem pode vir CITADA (`"ana silva"@exemplo.pt`), que e a
# segunda forma que a RFC 5322 define — e a que o reconhecedor anterior partia
# ao meio: o `"` nao pertence ao `atext`, portanto a rede de saida marcava
# `silva"@exemplo.pt` e deixava `"ana` publicado em claro. A alternativa citada
# vem PRIMEIRO, para ganhar a corrida ao ramo nao-citado.
_DOM = "[" + _DTEXT + "]+\\.[" + _DTEXT.replace(".", "") + "]{2,}"
_PADRAO_EMAIL = '"[^"\\n]*"@' + _DOM + "|[" + _ATEXT + "]+@" + _DOM
_EMAIL_RE = re.compile(_PADRAO_EMAIL)

# `Ana Silva <ana@exemplo.pt>` — a forma que o RUNBOOK convida o operador a
# escrever numa recuperacao. Substituir so o endereco deixava o NOME em claro
# num ficheiro commitado para `main` e servido em usmrm.net; um nome proprio ao
# lado de "servido" e um dado pessoal tanto como o endereco. O nome desaparece
# com ele, e a marca continua a ser a do ENDERECO — a mesma que o `marcas()`
# grava —, para a retoma de uma entrega parcial continuar a bater.
_ENDER_ANGULAR = re.compile(
    r"[^\n,;<>]{0,80}<\s*(" + _PADRAO_EMAIL + r")\s*>")


def _sem_enderecos_no_texto(valor):
    """Um texto com todos os enderecos substituidos pelas suas marcas."""
    # A forma `Nome <endereco>` PRIMEIRO: com o endereco ja substituido, o nome
    # deixava de ter por onde ser reconhecido.
    _v = _ENDER_ANGULAR.sub(lambda m: marca_destinatario(m.group(1)), valor)
    return _EMAIL_RE.sub(lambda m: marca_destinatario(m.group(0)), _v)


# E uma verificacao de SAIDA, nao de entrada: o que se garante e "nao ha
# enderecos no ficheiro", e essa propriedade tem de ser verificada no resultado,
# nao na imaginacao de quem escreveu a classe de caracteres. Qualquer `@` que
# sobreviva ladeado por caracteres nao-brancos e um endereco que escapou.
_ARROBA_VIVA = re.compile(r"\S@\S")


def _sem_enderecos(valor):
    """O mesmo valor, com qualquer endereco substituido pela sua marca.

    Percorre a estrutura toda em vez de tres nomes de campo. A versao anterior
    limpava `served`, `failed` e `pending` — e o RUNBOOK manda o operador editar
    este ficheiro a mao as 22:41 de uma sexta, onde `recipients` e o nome que
    convida a receber a lista. Um endereco em `recipients`, numa `nota`, ou numa
    chave que ainda nao existe, era commitado para `main` e servido em
    usmrm.net, semana apos semana. O que se garante nao e "estes tres campos
    estao limpos": e "nao ha enderecos no ficheiro".
    """
    if isinstance(valor, dict):
        # As CHAVES tambem. A versao anterior percorria a estrutura toda mas so
        # os valores, e o RUNBOOK manda o operador reconstruir este ficheiro a
        # mao — `{"entregas": {"ana@exemplo.pt": "ok"}}` e uma forma natural de
        # o escrever. Ficava commitado para main e servido em usmrm.net, e
        # republicado em cada `mark_sent`. A propriedade e "nao ha enderecos no
        # ficheiro", e uma chave e parte do ficheiro.
        saida = {}
        for k, v in valor.items():
            nk = _sem_enderecos(k) if isinstance(k, str) else k
            nv = _sem_enderecos(v)
            # Duas chaves DIFERENTES podem colapsar numa so: a marca normaliza
            # maiusculas e espacos, portanto "Ana@Exemplo.pt" e "ana@exemplo.pt"
            # dao o mesmo digest e a segunda apagava a primeira em silencio. Uma
            # limpeza de privacidade nao pode ser um apagador de registos — e a
            # mesma razao por que as entradas de forma desconhecida sao
            # preservadas em vez de filtradas.
            if isinstance(nk, str) and nk in saida and saida[nk] != nv:
                _n = 2
                while f"{nk}-{_n}" in saida:
                    _n += 1
                print(f"AVISO: duas chaves do registo davam a mesma marca "
                      f"({nk}); a segunda foi preservada como {nk}-{_n} em vez "
                      f"de apagar a primeira.")
                nk = f"{nk}-{_n}"
            saida[nk] = nv
        return saida
    if isinstance(valor, (list, tuple)):
        return [_sem_enderecos(v) for v in valor]
    if isinstance(valor, str):
        _limpo = _sem_enderecos_no_texto(valor)
        # A REDE DE SAIDA. O que se garante e "nao ha enderecos no ficheiro", e
        # isso tem de ser verificado no RESULTADO. Duas rondas seguidas
        # escreveram um padrao a que faltava uma forma real de endereco — a
        # primeira um alfabeto ASCII, a segunda uma lista de delimitadores com o
        # apostrofo la dentro — e nos dois casos o que se publicou foi o
        # endereco partido ao meio com a primeira parte em claro. Um `@` que
        # sobreviva ladeado por caracteres nao-brancos e um endereco que
        # escapou, seja qual for o padrao que o deixou passar: o token inteiro
        # leva marca.
        #
        # E nao levanta. Esta limpeza corre dentro do job que decide a carteira,
        # e uma guarda de privacidade sobre estado que uma pessoa edita a mao
        # nao pode travar a semana — se travasse, a propria limpeza deixava de
        # correr e o sistema deixava de se curar por causa da guarda.
        if _ARROBA_VIVA.search(_limpo):
            # O VALOR INTEIRO, nao o token delimitado por espacos. Marcar
            # `\S+@\S+` deixava a parte CITADA de fora: `"ana silva"@x.pt`
            # publicava `"ana` em claro — o endereco partido ao meio com a
            # primeira parte visivel, que e a falha que esta rede veio acabar e
            # que se repetiu tres rondas seguidas, agora com um aviso por cima.
            # Um valor que contem um endereco que o reconhecedor nao viu
            # perde-se inteiro: a privacidade nao se negoceia contra a
            # legibilidade de uma nota, e o aviso diz o que corrigir a mao.
            print("AVISO: um endereco no registo nao casou com o reconhecedor. "
                  "O CAMPO INTEIRO foi substituido pela sua marca, que pode nao "
                  "bater com a de quem foi servido — corrigir a mao.")
            return marca_destinatario(valor)
        return _limpo
    return valor


def _entrada_limpa(entrada):
    """Uma entrada do registo sem um unico endereco, onde quer que ele esteja."""
    limpa = dict(entrada)
    for campo in ("served", "failed", "pending"):
        if campo in limpa:
            limpa[campo] = marcas(limpa[campo] or [])
    return _sem_enderecos(limpa)


def sanear_marca(path=SENT_MARKER):
    """Limpa o registo publicado, e diz se o mudou.

    O ficheiro e commitado para `main` e a raiz do ramo e servida em usmrm.net.
    O `mark_sent` ja escreve so marcas, mas o RUNBOOK manda o operador editar
    este ficheiro a mao numa recuperacao, as 22:41 de uma sexta — e dai entram
    enderecos em claro. Isso e uma exposicao de dados pessoais e tem de ser
    corrigida na corrida seguinte, sozinha.

    O que NAO pode ser e um portao: uma verificacao de privacidade sobre estado
    que uma pessoa edita nao pode travar o job que decide a carteira. Se travar,
    a limpeza — que vive dentro do job travado — nunca corre, e o sistema deixa
    de se curar precisamente por causa da guarda. Por isso isto limpa e segue.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            registo = json.load(f)
    except FileNotFoundError:
        return False
    except Exception:
        raise MarcaIlegivel(
            f"{path} existe mas nao se consegue ler. NAO se prossegue: uma "
            f"marca ilegivel tratada como ausente reenvia a edicao a lista toda.")
    # So se limpa o que se percebe. Um ficheiro com outra forma — truncado, com
    # marcadores de conflito, com `sent` a nao ser uma lista — nao e limpo aqui:
    # o `already_sent` a seguir tem de o rejeitar, porque "nao se sabe se ja foi
    # enviada" nao pode virar "esta vazio, envia-se".
    if not isinstance(registo, dict) or not isinstance(registo.get("sent", []), list):
        return False
    antes = json.dumps(registo, sort_keys=True)
    # Limpa-se o que se percebe e PRESERVA-SE o resto. Filtrar aqui as entradas
    # com outra forma — `"issue": "27"`, que e o que sai de uma reconstrucao a
    # mao, ou `27.0` — apagava a unica prova de que uma edicao saiu, e publicava
    # a supressao. Uma limpeza de privacidade nao pode ser um apagador de
    # registos: o `already_sent` honra `"issue": 27.0`, e esta funcao corre
    # antes dele.
    # O numero da edicao e normalizado aqui: `"27"` de uma reconstrucao a mao
    # era preservado e ficava INERTE, porque o `already_sent` compara com `==`.
    for _e in (registo.get("sent") or []):
        if isinstance(_e, dict):
            _n = numero_de_edicao(_e.get("issue"))
            if _n is not None and _e.get("issue") != _n:
                print(f"AVISO: {path} tinha o numero da edicao como "
                      f"{_e.get('issue')!r}; normalizado para {_n}. Sem isto a "
                      f"marca era inerte e a edicao saia uma segunda vez.")
                _e["issue"] = _n
    _preservadas = [e for e in (registo.get("sent") or [])
                    if not isinstance(e, dict)
                    or numero_de_edicao(e.get("issue")) is None]
    if _preservadas:
        print(f"AVISO: {len(_preservadas)} entrada(s) de {path} sem numero de "
              f"edicao legivel. Sao PRESERVADAS tal e qual — corrigir a mao.")
    # Tambem as entradas que nao sao dicionarios: uma linha solta escrita a mao
    # ("#23 -> ana@exemplo.pt") e preservada como registo, mas sem o endereco.
    registo["sent"] = [_entrada_limpa(e) if isinstance(e, dict) else _sem_enderecos(e)
                       for e in (registo.get("sent") or [])]
    # E o ficheiro INTEIRO, nao so a lista `sent`. Um endereco numa chave ou num
    # valor de topo — `{"fila@exemplo.pt": "por servir"}`, que e o que sai de
    # uma reconstrucao a mao as 22:41 — sobrevivia a limpeza e ficava commitado
    # em `main`, servido em usmrm.net. A propriedade e sobre o FICHEIRO.
    registo = _sem_enderecos(registo)
    if json.dumps(registo, sort_keys=True) == antes:
        return False
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registo, f, indent=2)
    os.replace(tmp, path)
    return True


def mark_sent(issue_number, quantos, path=SENT_MARKER, servidos=None,
              falhados=None, restantes=None):
    """Grava a marca ANTES de qualquer outra coisa pos-envio. A ordem importa:
    o efeito irreversivel ja aconteceu, e a proxima linha de codigo a falhar nao
    pode apagar o facto de ele ter acontecido."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            registo = json.load(f)
    except Exception:
        registo = {}
    registo.setdefault("sent", [])
    # As entradas herdadas passam pelo mesmo crivo que a que se escreve agora.
    #
    # Duas razoes, as duas com consequencia real. Primeira: `mark_sent` so
    # sanitizava a SUA entrada, e as outras iam intactas para o ficheiro que e
    # commitado e servido em usmrm.net — uma entrada antiga, ou uma que o
    # operador escreveu a mao seguindo o RUNBOOK, republicava os enderecos todas
    # as semanas. Segunda: uma entrada malformada — uma string, um None, um
    # dicionario sem `issue` — fazia o `mark_sent` rebentar com AttributeError
    # ou KeyError, e ele e chamado DEPOIS do primeiro envio: saia uma mensagem, o
    # job morria sem gravar a marca, e cada re-corrida reenviava. O
    # `already_sent` ja tolerava estas formas; os dois lados passam a concordar.
    # E o numero e NORMALIZADO aqui, nao so aceite. O filtro aceitava
    # `"issue": "25"` (a forma que sai de uma reconstrucao a mao, que o RUNBOOK
    # manda fazer) mas preservava a string, e o `sorted` mais abaixo comparava-a
    # com o int desta edicao: TypeError DEPOIS de as primeiras mensagens ja
    # terem saido, antes de a marca ser gravada. O job morria, e a re-corrida —
    # que nao encontrava marca nenhuma — gerava uma edicao nova e reenviava a
    # lista toda. O `sanear_marca` tapava isto por acidente, e o guarda contra o
    # reenvio nao pode depender de um remedio que pode nao chegar a gravar.
    # As de forma desconhecida sao PRESERVADAS, nao filtradas.
    #
    # O filtro descartava tudo o que nao fosse um dicionario com um `issue`
    # legivel — exactamente as entradas que o `sanear_marca` preserva de
    # proposito, e pela mesma razao: uma linha escrita a mao numa recuperacao
    # ("#23 -> servido a toda a gente") e a unica prova de que aquela edicao
    # saiu. O `mark_sent` corre em todas as semanas bem sucedidas, portanto
    # apagava-a do ficheiro commitado, em silencio e para sempre. Limpas de
    # enderecos, sim; apagadas, nao.
    _legiveis, _guardadas = [], []
    for e in registo["sent"]:
        _n = numero_de_edicao(e.get("issue")) if isinstance(e, dict) else None
        if _n is None:
            _guardadas.append(_entrada_limpa(e) if isinstance(e, dict)
                              else _sem_enderecos(e))
        elif _n != issue_number:
            _legiveis.append(dict(_entrada_limpa(e), issue=_n))
    if _guardadas:
        print(f"AVISO: {len(_guardadas)} entrada(s) de {path} sem numero de "
              f"edicao legivel. Sao PRESERVADAS tal e qual (sem enderecos) — "
              f"corrigir a mao.")
    registo["sent"] = _legiveis
    entrada = {"issue": issue_number, "recipients": quantos,
               "sentAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    # Quem ja recebeu, quem falhou e quem falta. Sem isto, uma entrega parcial
    # (400 servidos, 100 falhados) era irrecuperavel: a unica saida documentada
    # — apagar a entrada — reenviava aos 400.
    # Digests, nunca enderecos — ver `marca_destinatario`.
    if servidos is not None:
        entrada["served"] = marcas(servidos)
    if falhados:
        entrada["failed"] = marcas(falhados)
    if restantes:
        entrada["pending"] = marcas(restantes)
    entrada["complete"] = not (falhados or restantes)
    registo["sent"].append(entrada)
    # `e["issue"]` e sempre um int aqui: a normalizacao acontece no filtro
    # acima, e e la que esta afirmada. Repeti-la neste `key` seria codigo que
    # nenhum teste pode tornar vermelho — e um ramo que ninguem exercita nao e
    # defesa, e um sitio onde um erro se pode esconder.
    # `e["issue"]` e sempre um int aqui: a normalizacao acontece no laco acima,
    # e e la que esta afirmada. As preservadas ficam FORA da ordenacao — nao tem
    # numero por que ordenar — e voltam a cabeca, onde nada as trunca.
    registo["sent"] = _guardadas + sorted(
        registo["sent"], key=lambda e: e["issue"])[-60:]
    # E o ficheiro INTEIRO passa pelo crivo, nao so a lista `sent`: uma chave ou
    # um valor de topo escritos a mao numa recuperacao ficavam commitados em
    # `main` e servidos em usmrm.net, republicados em cada semana bem sucedida.
    registo = _sem_enderecos(registo)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registo, f, indent=2)
    os.replace(tmp, path)
    return registo


OWNER_FALLBACK = "usmrm@proton.me"


def brevo_subscribers(api_key, page_size=500, max_pages=20):
    """Lista de subscritores, paginada.

    Duas coisas estavam erradas. Nao havia paginacao: acima de mil contactos os
    restantes desapareciam sem aviso. E um erro da API devolvia silenciosamente
    a lista com o email do dono, o que o log apresentava como "Sending to 1
    subscribers" — indistinguivel de nao haver subscritores. Um erro passa a
    levantar excepcao; uma lista genuinamente vazia continua a cair no dono."""
    emails, offset = [], 0
    for _ in range(max_pages):
        # Tentativas com espera: um 5xx transitorio da Brevo matava o envio
        # semanal inteiro assim que a funcao passou a levantar excepcao. Falhar
        # alto e certo; falhar a primeira e desnecessario.
        r = None
        for tentativa in range(3):
            try:
                r = requests.get("https://api.brevo.com/v3/contacts",
                                 headers={"api-key": api_key, "accept": "application/json"},
                                 params={"limit": page_size, "offset": offset},
                                 timeout=30)
            except requests.RequestException as e:
                r, motivo = None, f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    break
                motivo = f"HTTP {r.status_code}"
                if r.status_code < 500 and r.status_code != 429:
                    break        # 401/403: tentar outra vez da o mesmo
            if tentativa < 2:
                print(f"  [retry] Brevo falhou ({motivo}); nova tentativa em {10*(tentativa+1)}s")
                time.sleep(10 * (tentativa + 1))
        if r is None or r.status_code != 200:
            raise RuntimeError(
                f"Brevo indisponivel ao listar contactos (offset {offset}): "
                f"{'sem resposta' if r is None else r.status_code}. Nao se envia "
                f"a ninguem sobre uma lista que nao se conseguiu ler.")
        contacts = r.json().get("contacts", [])
        emails += [c["email"] for c in contacts
                   if c.get("emailBlacklisted") is False and c.get("email")]
        if len(contacts) < page_size:
            break
        offset += page_size
    else:
        raise RuntimeError(f"Brevo: mais de {max_pages * page_size} contactos — "
                           f"paginacao interrompida por seguranca.")
    if not emails:
        print(f"  [WARN] Brevo devolveu zero subscritores activos — a enviar so para {OWNER_FALLBACK}.")
        return [OWNER_FALLBACK]
    return emails


def etiqueta_edicao(issue_number):
    """A etiqueta com que cada mensagem desta edicao vai marcada na Brevo.

    Existe para uma coisa so, e e a mais perigosa do RUNBOOK: a edicao sai e a
    marca nao chega a ser commitada. O runner e destruido com o registo dentro, e
    a corrida seguinte — sem nada no `sent_issues.json` — gera uma edicao nova e
    REENVIA A TODA A GENTE. A unica prova de quem recebeu vivia num ficheiro que
    podia nao ter sido publicado; passa a viver tambem do lado de quem entregou,
    que e a Brevo, e que nao depende de um `git push` ter corrido bem."""
    return f"mrm-issue-{int(issue_number)}"


def brevo_destinatarios_da_edicao(api_key, issue_number, dias=30, timeout=30,
                                  limite=5000):
    """Quem a Brevo diz ter recebido esta edicao. Devolve (conjunto, erro).

    Um erro NAO e um conjunto vazio, e a diferenca e dinheiro: "a Brevo diz que
    ninguem recebeu" autoriza enviar; "nao consegui perguntar a Brevo" nao
    autoriza nada. Quem chama tem de distinguir os dois — devolver `set()` nos
    dois casos era construir a duplicacao que isto existe para impedir."""
    eventos = ("delivered", "requests", "opened", "clicks")
    encontrados, erro = set(), None
    for evento in eventos:
        try:
            r = requests.get("https://api.brevo.com/v3/smtp/statistics/events",
                             headers={"api-key": api_key, "accept": "application/json"},
                             params={"tags": etiqueta_edicao(issue_number),
                                     "days": dias, "limit": limite, "event": evento},
                             timeout=timeout)
        except requests.RequestException as e:
            erro = f"{type(e).__name__}: {e}"
            continue
        if r.status_code == 404:
            # A Brevo devolve 404 quando nao ha eventos nenhuns para o filtro.
            # Isso E uma resposta: nao ha registo de envio.
            continue
        if r.status_code != 200:
            erro = f"HTTP {r.status_code}"
            continue
        for ev in (r.json() or {}).get("events") or []:
            if ev.get("email"):
                encontrados.add(ev["email"].strip().lower())
    if encontrados:
        return encontrados, None
    return encontrados, erro


def brevo_send(api_key, sender, to, subject, html, retries=3, timeout=60,
               tags=None):
    """Envia para UM destinatario. Com prazo e tentativas.

    Nao tem `to` plural de proposito. A versao anterior punha toda a lista de
    subscritores num unico `to:`, o que significa que cada subscritor recebia no
    cabecalho o endereco de todos os outros — uma exposicao de dados pessoais,
    nao um detalhe de estilo. Quem envia para varios usa send_to_each(), que
    faz uma mensagem por pessoa.

    E a chamada que entrega de facto a newsletter era a unica sem `timeout` nem
    retry: um 5xx transitorio perdia a semana depois de tudo ja estar publicado
    no site."""
    if isinstance(to, (list, tuple, set)):
        if len(to) != 1:
            raise ValueError(
                f"brevo_send envia para um destinatario de cada vez (recebeu {len(to)}). "
                f"Usa send_to_each() — um `to` com varios endereços expoe a lista "
                f"de subscritores a toda a gente.")
        to = next(iter(to))

    r = None
    for tentativa in range(retries):
        try:
            r = requests.post("https://api.brevo.com/v3/smtp/email",
                              headers={"api-key": api_key, "content-type": "application/json",
                                       "accept": "application/json"},
                              json={"sender": sender, "to": [{"email": to}],
                                    "subject": subject, "htmlContent": html,
                                    # As etiquetas sao o que torna o envio
                                    # consultavel depois. Sem elas, o registo do
                                    # lado da Brevo existe mas nao e pesquisavel
                                    # por edicao, e a reconstrucao da marca
                                    # perdida nao tem por onde pegar.
                                    **({"tags": list(tags)} if tags else {})},
                              timeout=timeout)
        except requests.RequestException as e:
            r, motivo = None, f"{type(e).__name__}: {e}"
        else:
            if r.status_code in (200, 201, 202):
                return True, r
            motivo = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                return False, r        # pedido invalido: repetir da o mesmo
        if tentativa < retries - 1:
            espera = 10 * (tentativa + 1)
            # A MARCA, nunca o endereco. Toda a limpeza do sent_issues.json
            # existe porque a raiz de `main` e servida em usmrm.net e o
            # historico do git e para sempre — e este `print` ia para o log da
            # corrida, que o RUNBOOK manda o operador ABRIR e ler, e que numa
            # avaria da Brevo (rate-limit, 5xx: exactamente o caso que estas
            # tentativas existem para tratar) levava a lista inteira. Um log nao
            # e um ficheiro, mas e igualmente publico.
            print(f"  [retry] envio para {marca_destinatario(to)} falhou "
                  f"({motivo}); nova tentativa em {espera}s")
            time.sleep(espera)
    return False, r


def send_to_each(api_key, sender, recipients, subject, html, issue_number=None,
                 orcamento_s=SEND_BUDGET_SECONDS, marcar=None, relogio=None):
    """Uma mensagem por pessoa. Devolve (enviados, falhados, restantes).

    Duas coisas que nao existiam e que a fase de envio precisa de ter:

    ORCAMENTO. Cada destinatario falhado custa ate 210 s entre tentativas e
    esperas. Com uma lista grande e uma Brevo com problemas, o job batia no
    limite do GitHub NO MEIO do envio — depois de centenas de mensagens saidas e
    ANTES da marca ser gravada. A re-corrida reenviava a quem ja tinha recebido.
    Esgotado o orcamento, para-se de propria vontade, com a lista do que falta.

    MARCA INCREMENTAL. `marcar(enviados_ate_agora)` e chamado ao longo do
    caminho, nao so no fim: uma entrega parcial deixa registado a quem ja
    chegou, e o que sobra pode ser servido depois sem duplicar."""
    relogio = relogio or time.monotonic
    inicio = relogio()
    enviados, falhados, restantes = [], [], list(recipients)
    for e in list(recipients):
        if orcamento_s and (relogio() - inicio) > orcamento_s:
            print(f"  [ORCAMENTO] {orcamento_s:.0f}s de envio esgotados com "
                  f"{len(restantes)} por servir. Para-se aqui em vez de o job ser "
                  f"morto a meio.")
            break
        ok, r = brevo_send(api_key, sender, e, subject, html,
                           tags=([etiqueta_edicao(issue_number)]
                                 if issue_number is not None else None))
        (enviados if ok else falhados).append(e)
        restantes.remove(e)
        if not ok:
            # Idem: a marca, nunca o endereco — e aqui sai uma linha POR
            # destinatario falhado, portanto uma avaria da Brevo publicava a
            # lista toda no log da corrida.
            print(f"  [ERRO] envio para {marca_destinatario(e)} falhou "
                  f"({'sem resposta' if r is None else r.status_code})")
        # A CADA destinatario, nao de 25 em 25: o que se esta a proteger e o job
        # ser morto a meio, e nesse caso o que interessa e que o ultimo estado
        # gravado seja o real. Escrever um JSON pequeno custa uma fraccao do que
        # custa a chamada HTTP que acabou de acontecer.
        if marcar:
            marcar(enviados, falhados, restantes)
    if marcar:
        marcar(enviados, falhados, restantes)
    return enviados, falhados, restantes


def main():
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]
    brevo_key     = os.environ["BREVO_API_KEY"]

    agora = datetime.utcnow()
    today = agora.date()
    issue_number = issue_number_for(today)

    data      = _load_json("data.json", "data.json is required")
    portfolio = _load_json("portfolio.json", "portfolio.json not found — portfolio section will be thin")
    prev      = _load_json("data_prev.json", "no previous week — WoW skipped")

    # A edicao ja saiu? Entao NAO se gera outra.
    #
    # O envio e o unico efeito irreversivel deste ficheiro, e ate aqui nao
    # deixava rasto nenhum: nem no portfolio.json, nem em ficheiro de estado, nem
    # no arquivo do index.html (o cartao e removido e re-inserido de proposito).
    # Qualquer falha DEPOIS do envio — a rotacao do data_prev, um push recusado —
    # punha o job vermelho, o operador re-corria o pipeline, e a segunda passagem
    # gerava uma edicao NOVA (texto diferente, porque quem a escreve e um
    # modelo), publicava-a por cima e enviava a lista toda outra vez.
    # Antes de tudo: se a marca publicada tiver enderecos em claro — de uma
    # edicao manual, ou herdada — limpa-se e publica-se limpa. Nao trava a
    # corrida: a limpeza e o remedio, nao a condicao.
    if sanear_marca():
        print(f"{SENT_MARKER} tinha destinatarios por marcar; foi limpo.")
        # `fatal=False` de proposito: a limpeza e o remedio, nao a condicao. Se
        # o push nao passar — as 22:00 de sexta ha tres bots a empurrar para
        # `main` — a corrida segue e tenta outra vez para a semana.
        #
        # E o aviso e o DESTE chamador: o aviso por omissao do `git_publish` diz
        # "a newsletter JA FOI ENVIADA; nao voltar a correr o envio", o que aqui
        # seria falso — isto corre no primeiro passo, antes de se gerar seja o
        # que for — e mandava o operador nao re-correr uma semana que nao chegou
        # a sair.
        git_publish([SENT_MARKER],
                    "chore: sanitise sent_issues.json (recipients are digests)",
                    fatal=False,
                    aviso=(f"AVISO: a limpeza do {SENT_MARKER} nao foi publicada. "
                           f"Nada foi enviado ainda nesta corrida; se ela falhar, "
                           f"re-correr normalmente."))

    ja = already_sent(issue_number)

    # ── a marca perdida: perguntar a quem entregou ──────────────────────────
    #
    # O caso mais perigoso do RUNBOOK, e o unico que ainda exigia uma pessoa: a
    # edicao saiu e o `sent_issues.json` ficou no runner, que ja foi destruido.
    # Sem marca, tudo aqui em baixo conclui "esta semana ainda nao saiu" e
    # reenvia a TODA a gente — a edicao chega duas vezes, e a segunda e um texto
    # diferente, porque quem a escreve e um modelo.
    #
    # O ficheiro nao e a unica prova de que uma mensagem foi entregue: a Brevo
    # tambem sabe. Cada envio vai etiquetado com a edicao, e aqui pergunta-se.
    # Pergunta-se a Brevo SO quando ha motivo para desconfiar, e ha um sinal
    # exacto: a edicao desta semana ja esta publicada no repositorio. O
    # `git_publish` da edicao corre ANTES do envio e e fatal — se a pagina nao
    # foi publicada, nada foi enviado, e uma semana normal (a primeira passagem)
    # nao tem ficheiro nenhum. Sem esta porta, todas as semanas passavam a
    # depender da API de estatisticas da Brevo para poder enviar, e uma avaria
    # dela transformava-se numa semana sem newsletter — trocar uma duplicacao
    # rara por uma falha semanal nao e um bom negocio.
    _ja_publicada = bool(_glob.glob(f"MRM_Newsletter_Issue{issue_number}_*.html"))
    if not ja and _ja_publicada:
        print(f"A edicao #{issue_number} esta publicada no repositorio mas nao "
              f"tem marca no {SENT_MARKER}. A perguntar a Brevo se ja foi "
              f"entregue a alguem antes de enviar seja o que for.")
        _vistos, _erro_brevo = brevo_destinatarios_da_edicao(brevo_key, issue_number)
        if _erro_brevo and not _vistos:
            # NAO se envia as cegas. Nao saber se a edicao ja saiu e o estado em
            # que reenviar custa mais do que esperar: o ficheiro pode estar
            # perdido, e a alternativa a parar e duplicar.
            raise RuntimeError(
                f"Nao ha marca de envio para a edicao #{issue_number} no "
                f"{SENT_MARKER} e NAO foi possivel perguntar a Brevo quem ja a "
                f"recebeu ({_erro_brevo}). Parar e o comportamento certo: se a "
                f"edicao tiver saido e o push da marca tiver falhado, enviar "
                f"agora duplica-a para toda a lista. Ver o RUNBOOK.md, caso 1a.")
        if _vistos:
            print(f"AVISO: o {SENT_MARKER} nao tem marca da edicao "
                  f"#{issue_number}, mas a Brevo regista {len(_vistos)} "
                  f"destinatario(s) ja servido(s). A marca perdeu-se — "
                  f"reconstroi-se a partir da Brevo e serve-se apenas quem "
                  f"falta. Ninguem recebe duas vezes.")
            ja = {"issue": issue_number,
                  "recipients": len(_vistos),
                  "sentAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "served": marcas(sorted(_vistos)),
                  "failed": [], "pending": [],
                  # `complete: False` de proposito: o que a Brevo sabe e quem
                  # recebeu, nao quem faltava. Se a lista nao tiver crescido, a
                  # retoma nao encontra ninguem por servir e termina sem enviar
                  # nada — que e o resultado certo.
                  "complete": False,
                  "recovered_from": "brevo"}
            # E escreve-se JA no ficheiro, antes de qualquer envio. Duas razoes:
            # o `ja` e relido mais abaixo (depois de publicar) e um objecto so
            # em memoria evaporava-se ai, voltando a reenviar a toda a gente; e
            # se esta corrida morrer a meio, a seguinte encontra a marca em vez
            # de repetir a reconstrucao — que so funciona enquanto a Brevo
            # responder e os eventos nao expirarem.
            mark_sent(issue_number, len(_vistos),
                      servidos=sorted(_vistos), restantes=["por-apurar"])
            _m = already_sent(issue_number)
            if _m:
                ja = dict(_m, recovered_from="brevo")

    if ja and ja.get("complete", True):
        print(f"A edicao #{issue_number} ja foi enviada a {ja.get('recipients')} "
              f"destinatarios em {ja.get('sentAt')}. Nada a fazer.")
        print("Se for mesmo preciso reenviar, apagar a entrada de "
              f"{SENT_MARKER} deliberadamente.")
        return
    if ja:
        # Entrega parcial: retoma-se onde ficou, sem reenviar a quem ja recebeu.
        print(f"A edicao #{issue_number} foi enviada a "
              f"{len(ja.get('served') or [])} destinatarios e ficou incompleta "
              f"({len(ja.get('failed') or [])} falhados, "
              f"{len(ja.get('pending') or [])} por servir). Retoma-se.")

    # O relogio passa explicitamente: sem ele, a idade do data.json era medida
    # contra o relogio real mesmo quando o resto da corrida esta a ser simulado,
    # e os testes que fixam a semana falhavam 30 horas depois do carimbo do
    # data.json commitado — um portao que se fecha sozinho.
    c = build_context(data, portfolio, prev, today, issue_number, agora=agora)
    print(f"Issue #{issue_number} — {c['today']} | score {c['score']} | "
          f"regime {c['regime_label']} | gauge B {c['gauge_b_line']} | rebalance {c['rb_reason']}")

    filename = f"MRM_Newsletter_Issue{issue_number}_{c['today_file']}.html"

    if ja:
        # Retoma. Quem falta tem de receber A MESMA edicao que os outros
        # receberam — nao uma edicao nova: quem a escreve e um modelo, e gerar
        # outra vez daria texto diferente. Um terco da lista ficaria com uma
        # analise que os outros dois tercos nunca viram, e a ligacao no e-mail
        # apontaria para uma pagina que nunca existiu.
        #
        # A edicao procura-se pelo NUMERO, com glob, e nao pelo nome que esta
        # corrida construiria: o nome inclui a data, e uma retoma que atravesse a
        # meia-noite UTC — precisamente o caso lento, que e o que produz entregas
        # parciais — geraria outro nome e nao encontraria o ficheiro.
        # Ordenadas pela DATA, nao pela cadeia: "04Sep2026" ordena antes de
        # "29Aug2026", e com dois ficheiros para a mesma edicao — marca perdida
        # mais re-corrida, numa semana que atravesse a fronteira do mes —
        # escolhia-se o mais antigo dos dois e reenviava-se a edicao errada.
        def _quando(nome):
            try:
                return datetime.strptime(
                    nome.split("_")[-1].replace(".html", ""), "%d%b%Y")
            except Exception:
                return datetime.min

        candidatos = sorted(_glob.glob(f"MRM_Newsletter_Issue{issue_number}_*.html"),
                            key=_quando)
        if not candidatos:
            raise RuntimeError(
                f"Retoma da edicao #{issue_number}: a edicao ja foi enviada a "
                f"{len(ja.get('served') or [])} pessoas, mas o ficheiro publicado "
                f"nao esta neste checkout. NAO se gera outra edicao — os que "
                f"faltam receberiam um texto diferente do dos primeiros. "
                f"Repor o ficheiro a partir do repositorio e correr outra vez.")
        filename = candidatos[-1]
        with open(filename, "r", encoding="utf-8") as f:
            html_content = f.read()
        print(f"Retoma: reutiliza-se a edicao ja publicada ({filename}, "
              f"{len(html_content)} chars) em vez de gerar outra.")
    else:
        html_content = generate_newsletter(c, anthropic_key)
        print(f"HTML generated ({len(html_content)} chars)")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Saved: {filename}")

    if not ja and update_archive("index.html", render_archive_card(c, filename), issue_number):
        print("index.html updated")

    # O data_prev.json roda DEPOIS do envio, nao antes.
    #
    # Era copiado aqui, antes de publicar e de enviar. Se qualquer coisa
    # falhasse a partir daqui — e desde que brevo_subscribers passou a levantar
    # excepcao, falha mais vezes — a re-corrida encontrava prev == data e todos
    # os "vs. semana anterior" saiam a zero. Os subscritores recebiam uma edicao
    # a dizer que nada tinha mudado na semana.
    if not ja:
        git_publish([filename, "index.html"],
                    f"Auto: Newsletter Issue #{issue_number} — {c['today']}")

    subscribers = brevo_subscribers(brevo_key)
    # Quem ja recebeu esta edicao numa passagem anterior nao volta a receber. O
    # registo guarda a lista, precisamente para que uma entrega parcial possa ser
    # terminada em vez de repetida.
    ja = already_sent(issue_number) or {}
    # `marcas()` na LEITURA tambem: uma marca herdada — ou a entrada que o
    # RUNBOOK manda escrever a mao — podia trazer enderecos em claro, e entao
    # nenhum digest batia certo e a retoma reenviava a toda a gente, incluindo a
    # quem ja tinha recebido. O escritor normalizava e o leitor nao.
    servidos_antes = set(marcas(ja.get("served") or []))
    por_servir = [e for e in subscribers if marca_destinatario(e) not in servidos_antes]
    if servidos_antes:
        print(f"{len(servidos_antes)} subscritores ja receberam a #{issue_number} "
              f"numa passagem anterior; faltam {len(por_servir)}.")
    print(f"Sending to {len(por_servir)} subscribers (uma mensagem por pessoa)...")

    # A marca e escrita AO LONGO do envio, nao so no fim: se o job for
    # interrompido a meio, fica registado a quem ja chegou.
    def _marcar(env, fal, rest):
        # `servidos_antes` ja sao digests; `env` sao enderecos desta passagem.
        mark_sent(issue_number, len(servidos_antes) + len(env),
                  servidos=sorted(servidos_antes | set(marcas(env))),
                  falhados=fal, restantes=rest)

    enviados, falhados, restantes = send_to_each(
        brevo_key,
        {"name": "US MRM Intelligence Hub", "email": "noreply@usmrm.net"},
        por_servir,
        f"MRM Weekly Signal — Issue #{issue_number} | {c['today']} | "
        f"Score {c['score']}/10 · {c['regime_label']}",
        html_content,
        issue_number=issue_number, marcar=_marcar)
    print(f"Issue #{issue_number}: {len(enviados)} enviados, {len(falhados)} falhados, "
          f"{len(restantes)} por servir")
    if not enviados and not servidos_antes:
        print("Nenhum envio teve sucesso.")
        raise SystemExit(1)

    # A PRIMEIRA coisa depois do envio: registar que ele aconteceu. Tudo o que
    # vem a seguir pode falhar sem que a semana seja reenviada.
    _marcar(enviados, falhados, restantes)

    # A marca so serve para alguma coisa se sobreviver a este runner.
    #
    # Em GitHub Actions cada corrida faz checkout limpo de `main`: um ficheiro
    # que fique so no disco do runner e destruido com ele. Se o push da marca
    # falhasse, a re-corrida seguinte nao a via, gerava uma edicao nova e
    # reenviava a lista toda — precisamente o que o `sent_issues.json` existe
    # para impedir. Por isso a marca vai sozinha, primeiro, com mais tentativas
    # do que o resto. A falha nao levanta aqui — levanta no fim, depois de o
    # briefing ao dono sair, com a lista completa do que ficou por publicar; o
    # efeito e o mesmo (job vermelho, alerta, humano a olhar) com uma mensagem
    # melhor do que a excepcao crua daria.
    if not git_publish([SENT_MARKER],
                       f"chore: mark issue #{issue_number} as sent",
                       tentativas=5, fatal=False):
        print("ERRO: a marca de envio nao foi publicada. A edicao #%d JA FOI "
              "ENVIADA a %d subscritores." % (issue_number, len(enviados)))
        print("NAO voltar a correr o envio antes de commitar o "
              f"{SENT_MARKER} a mao — uma re-corrida reenviaria a toda a gente.")
        _marca_publicada = False
    else:
        _marca_publicada = True

    # Enviada com sucesso: so agora esta semana passa a ser "a anterior".
    shutil.copy("data.json", "data_prev.json")
    _prev_publicado = git_publish(
        ["data_prev.json"],
        f"chore: rotate data_prev after issue #{issue_number}", fatal=False)
    if not _prev_publicado:
        print("AVISO: o data_prev.json nao rodou no repositorio. A edicao da "
              "proxima semana sairia com todos os 'vs. semana anterior' a zero "
              "se isto nao for corrigido.")

    tweet_text = build_tweet(c, filename)
    tweet_html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#F2F4F7;padding:24px;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;">
  <div style="font-family:Courier New,monospace;font-size:9px;font-weight:600;letter-spacing:0.16em;text-transform:uppercase;color:#8B949E;margin-bottom:12px;">SATURDAY TWEET — Copy &amp; Paste on Twitter/X</div>
  <div style="background:#F8F9FB;border:1px solid #E5E8EC;border-radius:8px;padding:16px;font-family:Courier New,monospace;font-size:13px;color:#1A1D20;line-height:1.8;white-space:pre-wrap;">{tweet_text}</div>
  <div style="margin-top:16px;padding:12px;background:#EBF5FF;border-radius:8px;font-size:12px;color:#586068;">
    Newsletter Issue #{issue_number}: {len(enviados)} enviado(s), {len(falhados)} falhado(s).<br>
    Live at: <a href="https://usmrm.net/{filename}">usmrm.net/{filename}</a><br><br>
    Gauge B: {c['gauge_b_line']}<br>
    Portfolio: {c['regime_label']} — {c['rb_reason']}
  </div>
</div>
</body></html>"""
    ok, r = brevo_send(brevo_key, {"name": "MRM System", "email": "noreply@usmrm.net"},
                       OWNER_FALLBACK,
                       f"MRM Issue #{issue_number} sent — Saturday tweet ready", tweet_html)
    # `brevo_send` devolve (False, None) quando a rede falha nas tres tentativas.
    # Isto corre DEPOIS de os subscritores ja terem recebido e de o data_prev ja
    # ter rodado: um AttributeError aqui punha o job vermelho, e a re-corrida
    # reenviava a toda a gente com todos os WoW a zero. O briefing ao dono e uma
    # conveniencia, nao pode derrubar a semana.
    if ok:
        print("Owner briefing sent")
    else:
        print(f"Owner briefing error: {'sem resposta' if r is None else r.status_code} "
              f"(a newsletter ja foi enviada; isto nao afecta os subscritores)")

    # O envio correu; o que falta e estado que TEM de ficar no repositorio. Sair
    # a zero aqui punha o job verde, o alert-on-failure nao disparava, e ninguem
    # sabia que a proxima corrida ia reenviar a toda a gente.
    problemas_finais = []
    if not _marca_publicada:
        problemas_finais.append("a marca de envio NAO foi publicada no repositorio "
                                "— commita-la a mao antes de voltar a correr, ou a "
                                "proxima corrida reenvia a toda a gente")
    if not _prev_publicado:
        problemas_finais.append("o data_prev.json NAO rodou — sem isso a edicao da "
                                "proxima semana sai com todos os 'vs. semana "
                                "anterior' a zero")
    # Uma entrega INCOMPLETA tambem nao pode terminar verde. Bastava um envio com
    # sucesso para o job ficar verde: o alert-on-failure nao corria, nao se abria
    # issue nenhum, e ninguem ficava a saber que faltavam pessoas por servir. O
    # `complete: false` era carta morta — nada o lia, e a retoma so acontece se
    # alguem disparar o workflow para a mesma semana.
    if falhados or restantes:
        problemas_finais.append(
            f"a edicao #{issue_number} ficou INCOMPLETA: {len(falhados)} falhados, "
            f"{len(restantes)} por servir. Voltar a correr este job serve apenas "
            f"os que faltam — a edicao publicada e reutilizada e ninguem recebe "
            f"duas vezes")
    if problemas_finais:
        raise SystemExit("A edicao foi enviada, mas: " + "; ".join(problemas_finais))


if __name__ == "__main__":
    main()
