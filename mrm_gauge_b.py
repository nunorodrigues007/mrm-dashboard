"""
mrm_gauge_b.py — Medidor B (Stress) do US MRM
=============================================
O scorecard de cinco pilares (Medidor A) é um indicador AVANÇADO de fragilidade:
mede quanto há para correr mal, com horizonte de 6–18 meses. Numa crise três dos
seus pilares melhoram mecanicamente (curva desinverte, avaliações colapsam, ERP
abre), pelo que o composto não consegue sinalizar stress concorrente — em 2005-2026
nunca atingiu 8,0, nem sequer em 2008.

Este módulo é o Medidor B: rápido, baseado em VARIAÇÕES, horizonte 0–3 meses.
Não entra na média do Medidor A — sobrepõe-se a ele.

Gatilhos (fixados à partida, nenhum ajustado a resultados):
  B1  Regra de Sahm, série real-time do FRED (SAHMREALTIME) >= 0.50.
      Regra publicada por Claudia Sahm, desenhada para detecção de recessão em
      tempo real e robusta a revisões. Não é calibrada por mim.
  B2  Variação a 4 trimestres da delinquência bancária (DRALACBN) no decil 90
      da sua própria história desde 1987  ->  +0.81 pp.

Sub-regime quando activo, seguindo docs/critical_subregime.md:
  10Y a cair mais de 10 bp em 3 meses  ->  FTQ      (duração paga)
  caso contrário                       ->  STRESS   (duração não paga)

Comportamento histórico (1996-2026, dados real-time):
  dispara  2001-06→2002-11 · 2008-04→2010-11 · 2020-04→2021-03 · 2024-07→2024-09
  NÃO dispara em 2011, 2018 nem 2022 — separa recessões de bear markets.
  2024 é um falso positivo conhecido da regra de Sahm.
"""

import os
import time
import requests
from datetime import datetime, timedelta

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SAHM_TRIGGER = 0.50          # regra publicada
NPL_ACCEL_TRIGGER = 0.81     # decil 90 da variação 4T de DRALACBN desde 1987
TENY_FTQ_BP = -0.10          # queda mínima do 10Y em 3 meses para confirmar FTQ

# Os prazos de validade das observações e a função que os aplica vivem em
# data_freshness.py, partilhados com o fetch_data.py: a mesma pergunta — "esta
# série ainda está viva?" — não pode ter duas respostas em dois ficheiros.
from data_freshness import MAX_OBS_AGE_DAYS, observacao_velha  # noqa: F401


def _fetch(series_id, limit, api_key, retries=3, backoff=3):
    """Observações mais recentes de uma série FRED. Devolve [] em caso de falha.

    As tentativas têm espera crescente. Três tentativas seguidas sem pausa não
    servem contra o que faz falhar uma API — rate-limit ou indisponibilidade
    momentânea — e o custo de falhar aqui é o medidor ficar em n/d na semana."""
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": limit,
    }
    for attempt in range(retries):
        try:
            r = requests.get(FRED_BASE, params=params, timeout=30)
            r.raise_for_status()
            obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)]
            # Ordenar aqui em vez de confiar no sort_order da API: se a ordem vier
            # trocada, obs[0]-obs[4] passa a medir a variacao ao contrario e o
            # gatilho inverte-se sem dar erro.
            obs.sort(key=lambda o: o["date"], reverse=True)
            return obs
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [WARN] gauge_b: {series_id} indisponível após {retries} tentativas ({e})")
                return []
            espera = backoff * (attempt + 1)
            print(f"  [retry] gauge_b: {series_id} falhou ({e}); nova tentativa em {espera}s")
            time.sleep(espera)
    return []


# A janela da aceleracao da delinquencia: quatro trimestres, com folga para o
# calendario da publicacao. Fora dela nao ha comparacao que valha o limiar.
NPL_JANELA_DIAS = 365
NPL_JANELA_TOLERANCIA_DIAS = 45


def _um_ano_antes(obs):
    """A observacao mais proxima de um ano antes da mais recente, ou None.

    Pela DATA, nao pela posicao: com um trimestre em falta, a quinta entrada da
    lista deixa de ser ha um ano.
    """
    if not obs:
        return None
    try:
        recente = datetime.strptime(obs[0]["date"], "%Y-%m-%d").date()
    except Exception:
        return None
    alvo = recente - timedelta(days=NPL_JANELA_DIAS)
    melhor, melhor_dist = None, None
    for o in obs[1:]:
        try:
            d = datetime.strptime(o["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        dist = abs((d - alvo).days)
        if dist <= NPL_JANELA_TOLERANCIA_DIAS and (melhor_dist is None or dist < melhor_dist):
            melhor, melhor_dist = o, dist
    return melhor


def compute(api_key=None, dgs10_now=None, dgs10_3m_ago=None, hoje=None,
            dgs10_now_date=None, dgs10_3m_date=None):
    """
    Devolve o bloco 'stressGauge' para o data.json.

    Protocolo n/d: se uma série falhar, o gatilho correspondente fica None e é
    declarado no output. NUNCA é substituído por um valor neutro inventado.
    Se AMBOS falharem, 'active' fica None e o consumidor deve manter o estado
    anterior em vez de assumir OFF.
    """
    api_key = api_key or os.environ.get("FRED_API_KEY")
    if not api_key:
        raise RuntimeError("FRED_API_KEY não definida")

    # ── B1: regra de Sahm, vintage real-time ──
    sahm_obs = _fetch("SAHMREALTIME", 3, api_key)
    sahm_val = float(sahm_obs[0]["value"]) if sahm_obs else None
    sahm_date = sahm_obs[0]["date"] if sahm_obs else None
    b1 = (sahm_val >= SAHM_TRIGGER) if sahm_val is not None else None

    # ── B2: aceleração da delinquência a 4 trimestres ──
    npl_obs = _fetch("DRALACBN", 6, api_key)
    npl_accel = npl_date = None
    if npl_obs:
        # A observacao de comparacao escolhe-se pela DATA, nao pela posicao.
        #
        # `obs[0] - obs[4]` supunha que as seis observacoes pedidas chegavam
        # todas. Um so trimestre em falta — e a FRED publica um valor em falta
        # como "." — reduzia a lista a cinco, o `len >= 5` continuava a passar, e
        # `obs[4]` deixava de ser ha quatro trimestres: passava a ser ha cinco.
        # O limiar publicado e, por declaracao deste modulo e do `note` que vai
        # no data.json, "o decil 90 da variacao a 4 TRIMESTRES". Uma variacao a
        # cinco comparada com ele dispara o medidor, poe a carteira em Critical,
        # vende tudo para o vector defensivo — transaccao real, custo real — e
        # publica aos subscritores um numero que nenhuma janela de quatro
        # trimestres produz. E ao contrario: um disparo genuino pode ser apagado
        # pela mesma via, e ai a carteira fica exposta.
        #
        # Se nao houver observacao na janela, o gatilho fica em n/d — e o
        # protocolo n/d, que ja existe, mantem o estado anterior.
        # A data da observacao mais recente regista-se SEMPRE que haja
        # observacoes, mesmo que falte a de comparacao. Ficando presa dentro do
        # `if`, uma DRALACBN parada ha dois anos que tambem nao tenha
        # comparacao na janela publicava `stale: false` — e a edicao afirmava
        # "the series published" sobre uma serie que nao publica.
        npl_date = npl_obs[0]["date"]
        _base = _um_ano_antes(npl_obs)
        if _base is not None:
            npl_accel = round(float(npl_obs[0]["value"]) - float(_base["value"]), 2)
    b2 = (npl_accel >= NPL_ACCEL_TRIGGER) if npl_accel is not None else None

    # ── Idade das observações ──
    # Uma série parada devolve 200 e o mesmo número todas as semanas. Um gatilho
    # avaliado sobre uma observação velha demais passa a n/d — e o protocolo n/d
    # já existente faz o resto: com o outro gatilho quieto, o estado fica None e
    # o consumidor mantém o regime anterior em vez de declarar calma.
    sahm_velha, sahm_idade = observacao_velha("SAHMREALTIME", sahm_date, hoje)
    npl_velha,  npl_idade  = observacao_velha("DRALACBN", npl_date, hoje)
    if sahm_velha:
        b1 = None
    if npl_velha:
        b2 = None

    # ── estado ──
    # Protocolo n/d, aplicado a serio: so se pode declarar "calmo" quando os DOIS
    # gatilhos foram avaliados e nenhum disparou. Um gatilho que disparou basta
    # para declarar stress — um sinal presente nao precisa do outro para valer —
    # mas um gatilho em falta ao lado de um gatilho quieto NAO e calma: e falta
    # de informacao, e o estado anterior mantem-se.
    #
    # Antes, b1=None com b2=False dava active=False. Se o sistema estivesse em
    # Critical por causa da regra de Sahm e a serie falhasse, decide_rebalance
    # devolvia stress_off e a carteira liquidava a posicao defensiva por causa
    # de uma falha de rede.
    # PORQUE e que um gatilho ficou por avaliar — derivado, nao cravado.
    #
    # Escrito a mao como a constante "stale", ele acusava a FRED de ter parado
    # uma serie em dois casos em que ela nao parou nada: a falha de rede (o
    # `_fetch` esgota as tentativas e devolve `[]`, e o `observacao_velha` diz
    # "nao velha" porque nao ha data nenhuma para medir) e a janela sem
    # observacao de comparacao (a serie publicou, mas falta o trimestre a que
    # se compara). E a mesma acusacao que esta ronda existiu para tirar do
    # gatilho do 10Y.
    def _motivo_nd(disparo, velha, obs, valor):
        if disparo is not None:
            return None            # foi avaliado; nao ha motivo a declarar
        if velha:
            return "stale"
        if not obs:
            return "unavailable"
        if valor is None:
            return "no-window"     # publicou, mas sem observacao de comparacao
        return "unavailable"

    # O `basis` e o `ndReason` sao dois narradores do MESMO facto, e so um
    # deles conhecia os tres motivos. Quando falta a observacao de comparacao —
    # a serie publicou a horas, falta-lhe o trimestre a que se compara — o
    # `ndReason` dizia "no-window" e o `basis`, que e a frase que o site e o
    # prompt PUBLICAM, dizia "unavailable": a acusacao a FRED que as rondas 40 e
    # 41 existiram para tirar dos outros gatilhos, deixada intacta aqui.
    def _motivo(nome, motivo, idade):
        if motivo == "stale":
            quanto = "date unreadable" if idade is None else f"last observation is {idade} days old"
            return f"{nome} stale ({quanto})"
        if motivo == "no-window":
            return f"{nome} has no comparison observation in the window"
        return f"{nome} unavailable"

    _mot_sahm = _motivo_nd(b1, sahm_velha, sahm_obs, sahm_val)
    _mot_npl  = _motivo_nd(b2, npl_velha, npl_obs, npl_accel)

    if b1 is None and b2 is None:
        m = " and ".join([_motivo("Sahm", _mot_sahm, sahm_idade),
                          _motivo("ΔNPL", _mot_npl, npl_idade)])
        active, basis = None, f"n/d — {m}; retain previous state"
    elif (b1 is None or b2 is None) and not (b1 or b2):
        active = None
        missing = (_motivo("Sahm", _mot_sahm, sahm_idade) if b1 is None
                   else _motivo("ΔNPL", _mot_npl, npl_idade))
        basis = (f"n/d — {missing} and the other trigger is quiet; "
                 f"absence of a signal is not a signal. Retain previous state.")
    else:
        active = bool(b1) or bool(b2)
        fired = [n for n, f in (("Sahm", b1), ("ΔNPL", b2)) if f]
        nd = [n for n, f in (("Sahm", b1), ("ΔNPL", b2)) if f is None]
        basis = ("+".join(fired) if fired else "no trigger active")
        if nd:
            basis += f" (n/d: {', '.join(nd)})"

    # ── sub-regime ──
    #
    # O "default conservador" era uma ausencia disfarcada de medicao. A janela
    # de 3 meses do 10Y escolhe entre Critical_FTQ (TLT, 35% da carteira) e
    # Critical_Stress (SHY, 20%): sem ela, devolver "STRESS" faz o consumidor
    # ver uma leitura onde nao ha nenhuma, e uma carteira em FTQ vende o TLT por
    # causa de uma falha de rede — a transaccao que o proprio motor declara nao
    # poder acontecer quando o medidor inteiro fica sem dados. Aqui declara-se a
    # ausencia; quem decide o que fazer com ela e o consumidor, que sabe o que
    # a carteira detem. E o ramo `unavailable` do `subregime_from_gauge`, que
    # era codigo morto, passa a ser alcancavel.
    subregime = None
    teny_lido = dgs10_now is not None and dgs10_3m_ago is not None
    # A decisao usa o MESMO numero que se publica: a variacao em pontos base,
    # arredondada a uma casa. Comparar `4.40 - 4.50 <= -0.10` em vírgula
    # flutuante da -0.09999999999999964, isto e, FALSO — uma descida de
    # exactamente 10 bp, que o site promete a letra que confirma a fuga para a
    # qualidade ("a confirmed 10Y decline of -10bp or more"), punha 20% em SHY
    # em vez de 35% em TLT. E o gatilho publicava `value: -10.0` ao lado de
    # `fired: false`, no mesmo cartao.
    teny_bp = (round((dgs10_now - dgs10_3m_ago) * 100, 1) if teny_lido else None)
    teny_limiar_bp = round(TENY_FTQ_BP * 100, 1)
    teny_disparou = (None if not teny_lido else teny_bp <= teny_limiar_bp)
    if active and teny_lido:
        subregime = "FTQ" if teny_disparou else "STRESS"

    return {
        "active": active,
        "subregime": subregime,
        "basis": basis,
        "label": ("Stress OFF" if active is False else
                  ("Stress ON — Flight to Quality" if subregime == "FTQ" else
                   "Stress ON — No Relief" if subregime == "STRESS" else
                   "Stress ON — 10Y window n/d")
                  if active else "Stress n/d"),
        "triggers": {
            "sahmRealtime": {
                "series": "SAHMREALTIME", "value": sahm_val, "asOf": sahm_date,
                "decides": "regime",
                "threshold": SAHM_TRIGGER, "fired": b1,
                "ageDays": sahm_idade, "stale": sahm_velha,
                "ndReason": _mot_sahm,
                "maxAgeDays": MAX_OBS_AGE_DAYS["SAHMREALTIME"],
                "note": "Sahm rule, real-time vintage. Known false positive in 2024.",
            },
            "delinquencyAccel": {
                "series": "DRALACBN", "value": npl_accel, "asOf": npl_date,
                "decides": "regime",
                "threshold": NPL_ACCEL_TRIGGER, "fired": b2,
                "ageDays": npl_idade, "stale": npl_velha,
                "ndReason": _mot_npl,
                "maxAgeDays": MAX_OBS_AGE_DAYS["DRALACBN"],
                "note": "4-quarter change. Threshold = 90th percentile of its own history since 1987.",
            },
            # A janela de 3 meses do 10Y e o TERCEIRO input do medidor, e o
            # unico que nao era publicado. Nao decide `active` — decide qual dos
            # dois vectores de Critical se executa, e a diferenca entre eles sao
            # 35% da carteira em TLT contra 20% em SHY. Enquanto ficou por
            # declarar, uma falha de rede era indistinguivel de uma medicao: nem
            # o site, nem a newsletter, nem o motor tinham como saber que a
            # leitura faltou. Publica-se com a mesma forma dos outros dois, para
            # que os consumidores que ja sabem ler `stale`/`asOf` a apanhem sem
            # aprender nada de novo.
            "tenY3m": {
                "series": "DGS10", "value": teny_bp,
                "asOf": dgs10_now_date,
                "windowStart": dgs10_3m_date,
                # Qual das duas decisoes este gatilho toma, publicado em vez de
                # suposto. O consumidor avisava, para TODOS os gatilhos parados,
                # "This week's regime was decided without it" — falso para este,
                # que nao decide o regime nenhum: decide qual dos dois vectores
                # de Critical se executa. E o aviso vai ao prompt com ordem de o
                # copiar palavra por palavra sob pena de recusa da edicao.
                "decides": "subregime",
                # `fired` e a COMPARACAO que o `threshold` ao lado anuncia,
                # como nos outros dois gatilhos. Era `subregime == "FTQ"` — e o
                # `subregime` so e calculado quando o medidor esta ON, pelo que
                # numa semana calma a janela podia estar medida e MUITO abaixo
                # do limiar e publicar-se `fired: false` ao lado de "-20 bp,
                # fires at <= -10 bp". Uma contradicao dentro do mesmo cartao, e
                # com a mesma forma dos gatilhos que os consumidores ja sabem
                # ler. Se algum dia fizer falta dizer "isto foi aplicado", e um
                # campo diferente, nao este.
                "threshold": teny_limiar_bp,
                "fired": teny_disparou,
                "stale": not teny_lido,
                # PORQUE nao foi medida, declarado em vez de suposto. Nos outros
                # dois gatilhos `stale` significa "serie parada", porque vem do
                # `observacao_velha`; aqui significa so "nao foi medida", e a
                # causa mais provavel e um SEGUNDO pedido HTTP falhado (o
                # `fetch_data` pede a DGS10 duas vezes) ou uma janela curta, com
                # a serie a publicar a horas. Sem este campo, o consumidor
                # escrevia a causa dos outros e acusava a FRED de uma falha que
                # e de rede.
                "ndReason": (None if teny_lido else
                             ("short-window" if (dgs10_now is not None
                                                 or dgs10_3m_ago is not None)
                              else "unavailable")),
                "note": ("3-month change in the 10Y, in basis points. Decides "
                         "Flight-to-Quality against Stress-without-relief WITHIN "
                         "Critical; it does not decide Critical itself."),
            },
        },
        "computedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2, ensure_ascii=False))
