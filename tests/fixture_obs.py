"""Observacoes da FRED datadas RELATIVAMENTE a semana que se esta a simular.

Existe por uma razao especifica, e a razao vale mais do que o codigo.

Os testes que servem de portao aos dois jobs de sexta-feira precisam de series
economicas de mentira. A forma obvia — copiar as datas reais de uma semana que
correu bem — tem um defeito que so aparece meses depois: o `data_freshness`
tem prazos de validade, e uma observacao mensal de Agosto de 2026 caduca 120
dias depois. Nesse dia o medidor deixa de avaliar o gatilho, o teste falha, e
como e portao dos jobs, o que se ve nao e um teste vermelho na segunda-feira: e
uma sexta sem carteira e sem newsletter.

Por isso cada serie e datada pela sua propria cadencia a contar da sexta
simulada — diarias em dias, semanais em semanas, mensais em meses, trimestrais
em trimestres — e a sexta simulada, por sua vez, sai do portfolio.json em vez
de sair do calendario. Assim o fixture acompanha o repositorio e nunca
envelhece.
"""
from datetime import date, timedelta

import fetch_data


def mes_menos(d, n):
    """O primeiro dia do mes, n meses antes de d."""
    ano, mes = d.year, d.month - n
    while mes <= 0:
        ano, mes = ano - 1, mes + 12
    return date(ano, mes, 1)


def obs_base(sexta):
    """Series frescas para a semana de `sexta`, cada uma na sua cadencia."""
    o = {}
    # diarias
    o["T10Y2Y"] = [{"date": (sexta - timedelta(days=i)).isoformat(), "value": "0.43"}
                   for i in range(0, 20)]
    o["DGS10"] = ([{"date": (sexta - timedelta(days=i)).isoformat(), "value": "4.79"}
                   for i in range(0, 40)] +
                  [{"date": (sexta - timedelta(days=92 + i)).isoformat(), "value": "4.47"}
                   for i in range(0, 40)])
    # O indice precisa de um fecho na data de referencia do E/P, que e uma
    # constante do fetch_data — e de um fecho fresco.
    # O indice precisa de um fecho fresco e de outro na data de referencia do
    # E-P. A referencia so entra se for ANTERIOR a semana simulada: numa semana
    # ensaiada mais antiga do que a ancora, um fecho datado do futuro passava a
    # ser a observacao mais recente da serie e o data_freshness declarava o
    # SP500 parado — um aviso inventado pelo proprio fixture.
    o["SP500"] = [{"date": sexta.isoformat(), "value": "7670.00"}]
    if fetch_data.SP500_EARNINGS_YIELD_ASOF < sexta.isoformat():
        o["SP500"].append({"date": fetch_data.SP500_EARNINGS_YIELD_ASOF,
                           "value": "7670.00"})
    # semanal
    o["ICSA"] = [{"date": (sexta - timedelta(days=7 * i + 2)).isoformat(),
                  "value": v} for i, v in enumerate(("206000", "204000", "205000"))]
    # mensais, publicadas com cerca de um mes de atraso
    o["M2SL"] = ([{"date": mes_menos(sexta, 1).isoformat(), "value": "22500.0"}] +
                 [{"date": mes_menos(sexta, i).isoformat(), "value": "22400.0"}
                  for i in range(2, 13)] +
                 [{"date": mes_menos(sexta, 13).isoformat(), "value": "21345.0"}])
    o["UNRATE"] = [{"date": mes_menos(sexta, i).isoformat(), "value": v}
                   for i, v in enumerate(("4.1", "4.1", "4.2"), start=1)]
    o["SAHMREALTIME"] = [{"date": mes_menos(sexta, i).isoformat(), "value": v}
                         for i, v in enumerate(("-0.07", "-0.03", "0.07"), start=1)]
    # trimestrais, publicadas com cerca de cinco meses de atraso
    _t0 = mes_menos(sexta, 5)
    _t0 = date(_t0.year, ((_t0.month - 1) // 3) * 3 + 1, 1)

    def _tri(n):
        ano, mes = _t0.year, _t0.month - 3 * n
        while mes <= 0:
            ano, mes = ano - 1, mes + 12
        return date(ano, mes, 1).isoformat()

    o["DRALACBN"] = [{"date": _tri(i), "value": v} for i, v in
                     enumerate(("1.38", "1.46", "1.50", "1.44", "1.44", "1.53"))]
    o["TDSP"] = [{"date": _tri(i), "value": "11.164138"} for i in range(5)]
    return o


def sahm(sexta, *valores):
    """A serie de Sahm com os valores dados, do mais recente para o mais antigo."""
    return [{"date": mes_menos(sexta, i).isoformat(), "value": str(v)}
            for i, v in enumerate(valores, start=1)]


def dgs10(sexta, agora, ha_tres_meses):
    """O 10Y com um nivel hoje e outro ha tres meses — o sinal FTQ/Stress."""
    return ([{"date": (sexta - timedelta(days=i)).isoformat(), "value": str(agora)}
             for i in range(0, 40)] +
            [{"date": (sexta - timedelta(days=92 + i)).isoformat(), "value": str(ha_tres_meses)}
             for i in range(0, 40)])


def npl(sexta, *valores):
    """A delinquencia trimestral, do mais recente para o mais antigo."""
    _t0 = mes_menos(sexta, 5)
    _t0 = date(_t0.year, ((_t0.month - 1) // 3) * 3 + 1, 1)
    saida = []
    for i, v in enumerate(valores):
        ano, mes = _t0.year, _t0.month - 3 * i
        while mes <= 0:
            ano, mes = ano - 1, mes + 12
        saida.append({"date": date(ano, mes, 1).isoformat(), "value": str(v)})
    return saida


# A unica data do fixture que NAO acompanha a semana simulada, e porque: o
# `earnings_yield_now` precisa de um fecho do indice na data de referencia do
# E-P, que e uma constante escrita a mao no fetch_data. Nao e um sinal de
# frescura — e a ancora contra a qual o preco de hoje e marcado.
DATAS_FIXAS_ESPERADAS = {("SP500", fetch_data.SP500_EARNINGS_YIELD_ASOF)}


def verifica_frescura(sexta, eq, anos=1):
    """Afirma que o fixture esta dentro do prazo hoje E daqui a `anos` anos, e
    que nenhuma das suas datas esta presa ao calendario.

    Sem isto, o defeito que este modulo existe para evitar volta a entrar em
    silencio: basta alguem acrescentar uma serie com uma data fixa. A primeira
    versao desta funcao so olhava para a observacao mais recente de cada serie —
    e ja havia uma data fixa em `obs[1]` do proprio modulo.
    """
    import data_freshness as df
    futuro = sexta + timedelta(days=364 * anos)
    for horizonte in (sexta, futuro):
        for serie, obs in obs_base(horizonte).items():
            velha, idade = df.observacao_velha(serie, obs[0]["date"], horizonte)
            eq(velha, False,
               f"a observacao mais recente de {serie} ({obs[0]['date']}, {idade} dias) "
               f"esta dentro do prazo na semana de {horizonte}")
    # Nenhuma data fica para tras quando a semana anda — excepto as declaradas.
    # A comparacao e feita a dez anos de distancia de proposito: as series
    # longas (M2SL tem 14 pontos mensais, as trimestrais cinco) sobrepoem-se
    # naturalmente a um ano, e uma sobreposicao legitima nao e uma data presa.
    agora, depois = obs_base(sexta), obs_base(sexta + timedelta(days=3653))
    presas = {(serie, o["date"]) for serie, obs in agora.items() for o in obs}
    presas &= {(serie, o["date"]) for serie, obs in depois.items() for o in obs}
    esperadas = {d for d in DATAS_FIXAS_ESPERADAS if d[1] < sexta.isoformat()}
    eq(presas, esperadas,
       f"as unicas datas do fixture presas ao calendario sao as declaradas "
       f"(encontradas {sorted(presas)}, esperadas {sorted(esperadas)})")


def date_from_name(nome):
    """A data que o nome de uma edicao carrega (`..._04Sep2026.html`)."""
    from datetime import datetime
    return datetime.strptime(nome.split("_")[-1].replace(".html", ""), "%d%b%Y").date()


def semana_ensaiada(root, rules, ordinaria=True):
    """A sexta a ensaiar e a sua edicao, a partir do portfolio.json do repo.

    Devolve `(sexta, anterior, issue)`. Com `ordinaria=True` salta as semanas de
    rebalanceamento semestral: as asserçoes dos testes que a usam descrevem uma
    semana em que so os medidores decidem, e numa semana semestral o motor
    rebalanceia por calendario — a afirmacao "sem gatilho nao ha
    rebalanceamento" deixaria de ser verdade duas vezes por ano, sem ninguem
    ter mexido em nada. As semanas semestrais tem os seus proprios testes.
    """
    import json
    pf = json.loads((root / "portfolio.json").read_text(encoding="utf-8"))
    # A sexta sai do NUMERO da edicao, nao da data gravada: `current.date` e o
    # ultimo dia de NEGOCIACAO da semana, e numa sexta de feriado — o Natal de
    # 2026, a Sexta-feira Santa de todos os anos — o motor grava a quinta.
    sexta = date(2026, 3, 13) + timedelta(weeks=pf["current"]["issue"])
    saltadas = 0
    if ordinaria:
        while rules.is_semestral_rebalance_week(sexta):
            sexta += timedelta(days=7)
            saltadas += 1
    issue = ((sexta - date(2026, 3, 13)).days // 7) + 1
    anterior = sexta - timedelta(days=7)
    # O contrato desta funcao, verificado em vez de prometido: a semana ensaiada
    # e a que o pipeline vai mesmo correr a seguir (uma semana a frente da
    # ultima fechada, ou duas quando se salta a semestral), `anterior` e mesmo a
    # semana anterior, e o numero da edicao sai da mesma regra em ambas. Sem
    # isto, qualquer das tres podia deslizar sem que teste nenhum o dissesse.
    assert sexta.weekday() == 4, f"a semana ensaiada tem de ser uma sexta: {sexta}"
    assert (sexta - anterior).days == 7, "anterior tem de ser a semana antes"
    assert issue - 1 == ((anterior - date(2026, 3, 13)).days // 7) + 1, \
        "o numero da edicao anterior tem de sair da mesma regra"
    assert issue - pf["current"]["issue"] == 1 + saltadas, (
        f"a semana ensaiada ({issue}) tem de ser a seguinte a ultima fechada "
        f"({pf['current']['issue']}), mais as semestrais saltadas ({saltadas})")
    return sexta, anterior, issue


def poe_edicao_anterior(root, tmp, issue, dia):
    """Deixa em `tmp` a edicao N-1, para o motor ter uma alocacao para ler.

    Usa a edicao publicada quando ela existe — e o caso normal, e assim o
    parser corre sobre HTML verdadeiro. Quando a semana ensaiada nao e a
    imediatamente a seguir a ultima publicada, essa edicao nao existe: aí
    escreve-se o fixture, em vez de o teste passar a depender de um ficheiro
    que o repositorio pode nao ter.
    """
    import shutil
    import newsletter_parse
    # Pela DATA, nao pela cadeia: "04Sep2026" ordena antes de "29Aug2026".
    def _quando(caminho):
        try:
            return date_from_name(caminho.name)
        except Exception:
            return date.min

    # A MAIS RECENTE, nao a mais antiga: com duas edicoes para o mesmo numero —
    # marca perdida mais re-corrida — a antiga e a superseded, e o ensaio corria
    # sobre ela. O comentario dizia "pela data" e o indice dizia o contrario.
    publicadas = sorted(root.glob(f"MRM_Newsletter_Issue{issue}_*.html"),
                        key=_quando, reverse=True)
    # So serve uma edicao que o parser do motor consiga mesmo ler. As primeiras
    # edicoes do arquivo sao anteriores ao esquema de seis buckets — a edicao 2
    # nao tem ALTERNATIVES — e servi-las como "a edicao N-1" faria o ensaio
    # afirmar coisas sobre uma alocacao que o motor recusa.
    if publicadas and newsletter_parse.parse_allocation(
            publicadas[0].read_text(encoding="utf-8"))[0]:
        shutil.copy(publicadas[0], tmp)
        return publicadas[0].name
    from fixture_newsletter import edicao
    nome = f"MRM_Newsletter_Issue{issue}_{dia.strftime('%d%b%Y')}.html"
    (tmp / nome).write_text(edicao(issue), encoding="utf-8")
    return nome
