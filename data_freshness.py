"""
data_freshness.py — uma pergunta só, num sítio só: estes dados ainda valem?

Duas respostas vivem aqui, porque são a mesma pergunta feita a dois níveis: a
idade da observação mais recente de cada série do FRED, e a idade do próprio
data.json. A segunda estava dentro do update_portfolio.py, que importa o
yfinance ao nível do módulo — e por isso o send_newsletter.py, que publica os
mesmos números aos subscritores, não a podia usar sem arrastar o motor inteiro
para dentro do gerador da newsletter. Publicava sem verificar.

Existe por uma razão só. Uma série descontinuada, congelada ou renomeada no FRED
NÃO devolve erro: devolve HTTP 200 com a última observação que teve, semana após
semana, para sempre. Todo o sistema tratava esse número como leitura fresca — o
SAHMREALTIME parado em 0,20 dava `active: False` indefinidamente, um pilar com a
série morta continuava a pontuar, e a data de cada observação era publicada em
`asOf` e em `meta.fredSeriesDates` sem que ninguém a lesse.

Os prazos abaixo são deliberadamente largos: não medem atraso de publicação,
medem morte da série. Cada um é a cadência real da série mais o seu atraso
habitual de publicação, com folga — ultrapassá-lo não é a semana estar atrasada,
é a série ter deixado de ser actualizada. Um prazo apertado seria pior do que
não ter prazo nenhum: punha o sistema em n/d todas as semanas por causa do atraso
normal, e o n/d repetido acabaria por ser ignorado.
"""

from datetime import date, datetime

# série -> dias. Cadência (atraso habitual) → limite.
MAX_OBS_AGE_DAYS = {
    # Medidor B — os dois gatilhos que decidem o regime.
    "SAHMREALTIME": 120,   # mensal (~1 mês)   → 30-60 dias normais
    "DRALACBN":     330,   # trimestral (~5 m) → 120-210 dias normais
    # Medidor A — pilares e sentinelas.
    "T10Y2Y":        21,   # diária (dias úteis)
    "DGS10":         21,   # diária (dias úteis)
    "ICSA":          45,   # semanal (~1 semana)
    "M2SL":         120,   # mensal (~1 mês)
    "UNRATE":       120,   # mensal (~3 semanas)
    "TDSP":         330,   # trimestral (~5-6 meses)
    "SP500":         21,   # diária (dias úteis) — ancora o E/P do pilar Premium
    # Componentes do indicador de Buffett (Fed Z.1 + BEA), todas trimestrais com
    # publicação lenta.
    "NCBEILQ027S":  330,
    "FBCELLQ027S":  330,
    "GDP":          330,
}


def observacao_velha(series_id, obs_date, hoje=None):
    """(velha, idade_em_dias) para a observação mais recente de uma série.

    Uma data ilegível conta como velha: não se avalia nada sobre uma observação
    que não se sabe datar. Uma data no futuro também — não acontece numa série
    honesta, e a alternativa é confiar nela. Sem data devolve (False, None): esse
    é o caminho da série indisponível, que cada chamador já trata como n/d.
    Uma série sem limite declarado nunca é considerada velha, para que
    acrescentar uma série nova não a ponha em n/d por esquecimento."""
    if not obs_date:
        return False, None
    try:
        d = date.fromisoformat(str(obs_date))
    except (TypeError, ValueError):
        return True, None
    idade = ((hoje or date.today()) - d).days
    if idade < 0:
        return True, idade
    limite = MAX_OBS_AGE_DAYS.get(series_id)
    if limite is None:
        return False, idade
    return idade > limite, idade


# ── Idade do próprio data.json ───────────────────────────────────────────────
#
# Uma corrida do fetch_data que falhe deixa o ficheiro anterior no sítio. Sem
# esta verificação, o motor decidiria — e a newsletter publicaria — sobre as
# leituras da semana passada como se fossem desta.
DATA_WARN_AFTER_HOURS   = 30
DATA_REFUSE_AFTER_HOURS = 48


def data_age_hours(meta, now=None):
    """Idade do data.json em horas, ou None se não declarar quando foi gerado."""
    stamp = (meta or {}).get("generatedAt") or (meta or {}).get("lastUpdated")
    if not stamp or not isinstance(stamp, str):
        return None
    s = stamp.strip().replace("Z", "+00:00")
    try:
        gen = datetime.fromisoformat(s)
    except ValueError:
        return None
    if gen.tzinfo is not None:
        gen = gen.replace(tzinfo=None) - gen.utcoffset()   # normaliza para UTC ingénuo
    return ((now or datetime.utcnow()) - gen).total_seconds() / 3600.0


def limiar_declarado(freshness, chave, teto):
    """O limiar que o ficheiro declara, nunca mais frouxo do que o do leitor.

    Um data.json velho ou corrompido que declarasse `refuseAfterHours` enorme
    desligava a protecção que existe para o travar: o documento não pode ser
    juiz da sua própria validade. Pode ser mais apertado; nunca mais frouxo."""
    v = (freshness or {}).get(chave)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
        return teto
    return min(float(v), teto)


def series_paradas(meta):
    """(série, idade em dias) para as séries que o data.json declara paradas."""
    m = meta or {}
    paradas = dict(m.get("fredSeriesStale") or {})
    for nome in ((m.get("freshness") or {}).get("staleSeries") or []):
        paradas.setdefault(nome, None)
    return sorted(paradas.items())
