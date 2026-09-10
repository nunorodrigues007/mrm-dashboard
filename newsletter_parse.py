"""
newsletter_parse.py — leitura da tabela de alocacao a partir do HTML da newsletter.

Existe por uma razao concreta: quem ESCREVE a newsletter e quem a LE na semana
seguinte tem de usar o mesmo parser. Ate aqui o `update_portfolio.py` era o unico
a saber ler a tabela, e o `send_newsletter.py` publicava e enviava sem nunca
verificar se o que o modelo escreveu era sequer legivel. Uma edicao truncada, ou
com a tabela com outro formato, passava: os subscritores recebiam-na, ficava no
site, e so na sabado seguinte — ao decidir a carteira — e que se descobria que a
alocacao nao dava para ler. Nessa altura o motor mantinha as posicoes em silencio.

Modulo sem dependencias de rede nem de ficheiros: recebe texto, devolve dados.
"""

import html as _html
import re

import mrm_rules as rules


def _e_linha_de_total(nome):
    """Uma linha de soma ("Total", "Total Portfolio", "Sum"), que nao e uma
    classe de activo e nao deve contar como peso nem como linha perdida."""
    n = (nome or "").strip().lower().rstrip(":")
    return n in ("total", "sum", "total portfolio", "total allocation",
                 "portfolio total", "grand total")


def _celulas(row_html):
    """As celulas de uma linha, por ordem, sejam <td> ou <th>.

    Um `<th scope="row">US Equities</th>` e uma linha de dados com o nome numa
    celula de cabecalho — HTML valido, e que o LLM escreve."""
    return [_texto_de_celula(m.group(2)) for m in
            re.finditer(r'<(td|th)[^>]*>(.*?)</\1>', row_html, re.IGNORECASE | re.DOTALL)]


def _e_cabecalho(row_html, cells):
    """Uma linha de cabecalho: so celulas <th>, e sem PERCENTAGENS.

    O criterio e a percentagem e nao "tem digitos": um cabecalho legitimo pode
    trazer numeros no nome — "Benchmark 60/40", "US Treasuries (7-10y)" — e
    classifica-lo como linha de dados por causa disso fazia perder os nomes das
    colunas justamente na tabela em que eles decidem qual e a alocacao."""
    if not cells:
        return False
    if re.search(r'<td\b', row_html, re.IGNORECASE):
        return False
    return not any(re.search(r'\d\s*%', c) for c in cells[1:])


def _texto_de_celula(bruto):
    """O texto de uma celula, sem etiquetas e sem entidades.

    Quem escreve a tabela e um LLM: `<th><strong>Asset Class</strong></th>`,
    `Asset&nbsp;Class` e `35&nbsp;%` sao formas que ele produz e que o parser
    anterior rejeitava — e desde que o send_newsletter passou a recusar publicar
    uma edicao ilegivel, essa rejeicao deixou de ser "o rebalanceamento nao
    aconteceu" e passou a ser "nao houve newsletter nenhuma esta semana". A
    tolerancia tem de estar na FORMA; o rigor fica onde deve, na validacao das
    bandas e do total."""
    txt = re.sub(r"<[^>]+>", " ", bruto or "")
    txt = _html.unescape(txt)
    # O espaco duro sobrevive ao unescape como U+00A0 e nao e apanhado por \s
    # em modo nao-unicode nalgumas formas; normaliza-se explicitamente.
    txt = txt.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", txt).strip()


ASSET_CLASS_BUCKET_MAP = {
    "US Equities": "US_EQUITIES", "US Equities (Broad)": "US_EQUITIES",
    "Domestic Equity": "US_EQUITIES", "International Developed": "US_EQUITIES",
    "US Large-Cap Equities": "US_EQUITIES", "Large-Cap Equity": "US_EQUITIES",
    "US Large-Cap Equity": "US_EQUITIES",
    "US Treasuries": "US_TREASURIES", "US Treasuries (7": "US_TREASURIES",
    "Sovereign": "US_TREASURIES", "Intermediate Treasuries": "US_TREASURIES",
    "Investment-Grade Credit": "IG_CREDIT", "Investment Grade Credit": "IG_CREDIT",
    "Investment-Grade Fixed": "IG_CREDIT",
    "Commodities": "COMMODITIES", "Real Assets": "COMMODITIES",
    "Commodities Broad Basket": "COMMODITIES",
    "Cash": "CASH", "Cash & Equivalents": "CASH", "Cash / Ultra-Short Bills": "CASH",
    "Short-Duration Bills": "CASH", "Short Duration Bills": "CASH",
    # "Short-Duration Sovereigns" contem "Sovereign" e caia em US_TREASURIES, a
    # somar-se a linha das treasuries intermedias: 45% em duracao, 0% em cash,
    # numa semana em que a newsletter dizia 25% e 20%. Passava despercebido
    # porque 45% ainda cabe na banda 10–50 das treasuries.
    "Short-Duration Sovereigns": "CASH", "Short Duration Sovereigns": "CASH",
    "Short-Duration Sovereign": "CASH", "Short-Term Sovereigns": "CASH",
    "Ultra-Short Sovereigns": "CASH",
    "Alternatives / Real": "ALTERNATIVES", "Alternatives / Hedge": "ALTERNATIVES",
    "Alternatives": "ALTERNATIVES", "Real Estate": "ALTERNATIVES", "REITs": "ALTERNATIVES",
}

# Palavras-chave, tentadas por ordem, so quando o mapa explicito acima falha.
# Quem escreve a newsletter e um LLM e inventa nomes novos todas as semanas — em 26
# edicoes apareceram "Broad Equities", "Intermediate Govt Bonds", "Short-Term Bills",
# "Fixed Income (Duration)", "Credit (IG/HY)". Cada nome nao reconhecido tirava a sua
# linha do total, o total falhava a validacao dos 100% e o rebalanceamento dessa semana
# nao acontecia, em silencio. A ordem importa: dinheiro antes de duracao (senao
# "Short-Duration / T-Bills" ia parar a treasuries) e credito antes de accoes.
BUCKET_KEYWORDS = [
    ("CASH",          ("t-bill", "tbill", "bills", "cash", "money market",
                       "short-term", "short term", "short-duration", "short duration", "ultra-short")),
    # "rates" NAO entra nesta lista: as palavras-chave sao testadas por
    # substring, e "IG CorpoRATES" contem "rates" — mandava credito para
    # treasuries. As que entram foram escolhidas por nao colidirem.
    ("US_TREASURIES", ("treasur", "govt bond", "govt. bond", "government bond", "sovereign",
                       "duration", "ust ")),
    # "IG Corporates", "Corporate Bonds" e "IG Bonds" sao nomes que o modelo
    # escreve. Desde que uma linha nao reconhecida rejeita a tabela inteira,
    # cada nome em falta e uma semana sem rebalanceamento.
    ("IG_CREDIT",     ("investment grade", "investment-grade", "ig credit", "credit",
                       "corporat", "ig bond", "ig corp")),
    ("US_EQUITIES",   ("equit", "stocks", "large-cap", "large cap", "small-cap", "small cap")),
    ("COMMODITIES",   ("commodit", "real asset", "gold", "energy")),
    ("ALTERNATIVES",  ("reit", "real estate", "alternative", "hedge", "infrastructure",
                       "managed futures")),
]


def map_asset_class(asset_class):
    """(bucket, como) para um nome de classe de activo. `como` e 'exacto', a palavra-chave
    que apanhou, ou None se nao houver correspondencia — nesse caso a linha e ignorada e
    o total nao fecha em 100%, que e o sinal de que a alocacao nao e de confianca."""
    name = asset_class.lower()
    # Chave mais longa primeiro: o mapa e testado por substring, por isso
    # "Sovereign" apanharia "Short-Duration Sovereigns" antes da entrada
    # especifica. O nome mais especifico tem de ganhar ao mais generico.
    for key, bucket in sorted(ASSET_CLASS_BUCKET_MAP.items(), key=lambda kv: -len(kv[0])):
        if key.lower() in name:
            return bucket, "exacto"
    for bucket, words in BUCKET_KEYWORDS:
        for w in words:
            if w in name:
                return bucket, w
    return None, None


def parse_allocation(content):
    """Le o score e a tabela de alocacao de uma newsletter em HTML.

    Devolve (bucket_alloc, mrm_score, notas). `notas` e uma lista de
    (nivel, mensagem) com nivel em {"info", "warning", "error"} — quem chama
    decide se as regista, se as ignora ou se recusa publicar por causa delas.
    Uma alocacao reprovada devolve {} — nao e corrigida, e rejeitada.

    Uses a TR-based approach: finds the allocation table, then extracts cells
    row-by-row. This avoids cross-cell regex matching bugs that occurred when
    using a single regex with re.DOTALL.
    """
    notas = []

    # ── Extract MRM Score ─────────────────────────────────────────────────────
    # A marca explicita primeiro. Ler "a primeira etiqueta do documento que
    # contenha N.N" e fragil por construcao: qualquer decimal isolado antes do
    # cartao do score — um valor do 10Y, um P&L — passa a ser lido como o score.
    # Enquanto isso so produzia um log.warning era um incomodo; desde que a
    # validacao compara o score publicado com o do motor, uma leitura errada
    # custa a edicao da semana. O prompt passa a pedir `data-mrm-score`, e a
    # leitura antiga fica como recurso para as edicoes ja publicadas.
    mrm_score = None
    marca = re.search(r'data-mrm-score="\s*(-?\d+(?:\.\d+)?)\s*"', content)
    # O recurso so corre quando NAO HA ancora nenhuma — as edicoes publicadas
    # antes de a ancora existir. Com uma ancora presente mas nao numerica (a
    # semana n/d escreve `data-mrm-score="N/A"`), o recurso varria o documento e
    # devolvia o primeiro decimal nu de qualquer etiqueta: o "+0.0" da coluna
    # WoW, o "6.97" da caixa de rebalanceamento, ou um "63.4" de uma tabela de
    # pilares. Um numero que ninguem publicou como score, entregue ao motor como
    # se fosse ele — e, fora da escala, fechava o portao para sempre sobre uma
    # edicao ja commitada. Uma ancora que diz "N/A" nao e uma ancora em falta: e
    # a declaracao de que nao ha score, e o protocolo n/d manda respeita-la.
    _tem_ancora = re.search(r'data-mrm-score="', content, re.I) is not None
    score_match = marca or (
        None if _tem_ancora
        else re.search(r'<[^>]*>\s*(\d+\.\d+)\s*</[^>]*>', content))
    if score_match:
        try:
            mrm_score = float(score_match.group(1))
        except ValueError:
            pass
    notas.append(("info", f"MRM Score parsed: {mrm_score}"))

    # ── Extract Allocation Table (TR-based) ───────────────────────────────────
    # Step 1: isolate the allocation table.
    # A ancora era o titulo "Regime-Based Asset Allocation", mas quem escreve a
    # newsletter e um LLM e o titulo varia: das 26 edicoes ate Set 2026 so 6 o usam
    # (a de 4 Set diz "Regime-Based Allocation Framework"). Nas outras a alocacao
    # nao era lida e o rebalanceamento ficava silenciosamente por fazer — o semestral
    # de Junho so passou porque calhou uma semana com o titulo certo.
    # A ancora passa a ser a propria tabela: aquela cujo cabecalho tem "Asset Class",
    # presente nas 26 edicoes. O titulo fica como recurso, se algum dia a tabela mudar.
    def _linha_de_cabecalho(table_html):
        """A LINHA de cabecalho da tabela de alocacao, se existir.

        Devolve os nomes das colunas por posicao, ou None se esta tabela nao for
        a da alocacao. E preciso ser a linha, e nao "todos os <th> da tabela":
        um `<tr><th colspan="3">Regime-Based Asset Allocation</th></tr>` antes do
        cabecalho — HTML valido e natural para quem escreve — fazia deslizar
        todos os nomes uma posicao. Os filtros que decidem qual e a coluna da
        alocacao passavam entao a olhar para o nome errado: numa tabela
        "Asset Class | Current Weight | New Target" a coluna escolhida chamava-se
        "asset class" e executava-se 45% onde a edicao publicava 30%; e numa
        "Asset Class | Benchmark | Regime Target" o filtro do benchmark eliminava
        a coluna certa e ficava com a de referencia."""
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.IGNORECASE | re.DOTALL):
            cells = _celulas(row)
            if not _e_cabecalho(row, cells):
                continue
            primeira = cells[0].lower()
            if primeira == "asset class" or primeira.startswith("asset class"):
                return [c.lower() for c in cells]
        return None

    alloc_section, headers = None, None
    candidatas = []
    for table_html in re.findall(r'<table[^>]*>.*?</table>', content, re.IGNORECASE | re.DOTALL):
        cabecalhos = _linha_de_cabecalho(table_html)
        if cabecalhos is not None:
            candidatas.append((table_html, cabecalhos))

    # O prompt diz ao modelo que SO UMA tabela do documento pode ter "Asset
    # Class" como primeira celula de cabecalho. A versao anterior ficava com a
    # primeira que encontrasse e nunca verificava se havia uma segunda —
    # adivinhar aqui e o mesmo erro que adivinhar a coluna.
    if len(candidatas) > 1:
        notas.append(("error",
                      f"o documento tem {len(candidatas)} tabelas com 'Asset Class' "
                      f"no cabecalho; so uma pode ser a alocacao a executar"))
        return {}, mrm_score, notas
    if candidatas:
        alloc_section, headers = candidatas[0]

    if alloc_section is None:
        fallback = re.search(r'Regime-Based\s+(?:\w+\s+)?Allocation.*?</table>',
                             content, re.IGNORECASE | re.DOTALL)
        if fallback:
            alloc_section = fallback.group(0)
            for row in re.findall(r'<tr[^>]*>(.*?)</tr>', alloc_section,
                                  re.IGNORECASE | re.DOTALL):
                cells = _celulas(row)
                if _e_cabecalho(row, cells):
                    headers = [c.lower() for c in cells]
                    break

    if alloc_section is None:
        notas.append(("error", "Allocation table not found in newsletter HTML"))
        return {}, mrm_score, notas

    notas.append(("info", f"Allocation table found ({len(alloc_section)} chars)"))

    # ── Qual e a coluna da alocacao? ──────────────────────────────────────────
    #
    # A heuristica anterior escolhia "a primeira coluna cujo cabecalho fale de
    # percentagens". Ha uma forma de tabela — natural para quem escreve sobre uma
    # rotacao — em que ela le o numero errado EM SILENCIO:
    #
    #   Asset Class | Current Weight | New Target | Change
    #   US Equities |            45% |       30% |   -15pp
    #
    # Escolhia "Current Weight". A soma dava 100, todas as bandas passavam, e o
    # motor executava os pesos ANTIGOS — 15 pontos de accoes a mais do que a
    # edicao publicou — sem um aviso, sem um log, sem a faixa.
    #
    # Palavras-chave nao resolvem isto: a edicao 13 tem "Current Weight" (a
    # alocacao a executar) ao lado de "Target Range" (uma banda, "10-20%"), e ai
    # a coluna "current" e a certa. O que distingue as colunas nao e o nome, e o
    # que la esta: uma coluna de alocacao soma 100 sobre os seis buckets. Usa-se
    # portanto o proprio `validate_allocation` como criterio, coluna a coluna.
    #
    # Exactamente uma coluna valida -> e essa. Nenhuma -> rejeita-se, com o
    # motivo da que chegou mais perto. Mais do que uma -> a tabela e
    # genuinamente ambigua e NAO se adivinha: rejeita-se, o motor mantem as
    # posicoes e a edicao sai com a faixa de aviso.
    headers = headers or []

    linhas = []
    for row_html in re.findall(r'<tr[^>]*>(.*?)</tr>', alloc_section, re.IGNORECASE | re.DOTALL):
        cells = _celulas(row_html)
        if len(cells) < 2 or _e_cabecalho(row_html, cells):
            continue
        if not any(re.search(r'\d', c) for c in cells[1:]):
            continue
        if cells[0]:
            linhas.append(cells)

    if not linhas:
        notas.append(("error", "a tabela de alocacao nao tem linhas de dados legiveis"))
        return {}, mrm_score, notas

    def _percentagem(celula):
        """A percentagem de uma celula, ou None quando a celula nao E uma.

        Uma celula com DOIS numeros nao e uma alocacao. Ha duas formas disso, e
        so uma estava coberta:

          "10-20%"          uma banda: o intervalo em que o peso pode andar.
          "45% -> 30%"      uma transicao: o peso antigo e o novo na mesma
                            celula, que o modelo escreve com uma seta, um traco
                            ou um "to". Aqui lia-se 45 — os pesos da SEMANA
                            PASSADA — a coluna somava 100, passava em todas as
                            bandas, e o motor executava-os sem uma nota, sem um
                            aviso e sem a faixa. O subscritor lia "-> 30%" e a
                            carteira ficava com 45%.

        O criterio passa a ser o que o resto deste ficheiro ja escolhe em todo o
        lado: perante ambiguidade, nao se adivinha. Duas percentagens numa
        celula devolvem None, a coluna deixa de somar 100 e a tabela e
        rejeitada — com a faixa de aviso e as posicoes mantidas."""
        percentagens = re.findall(r'(\d+(?:\.\d+)?)\s*%', celula)
        if len(percentagens) > 1:
            return None
        # Uma banda escreve-se com os dois numeros e um so % no fim.
        if re.search(r'\d\s*%?\s*(?:[-\u2010-\u2015\u2192>]+|to\b)\s*\d', celula):
            return None
        return float(percentagens[0]) if percentagens else None

    n_col = max(len(c) for c in linhas)
    # Um cabecalho com menos (ou mais) celulas do que as linhas de dados nao
    # descreve estas colunas: os nomes ficariam desalinhados, e sao eles que
    # decidem qual e a alocacao a executar. Sem nomes de confianca, o desempate
    # por nome nao corre — o que faz uma tabela ambigua ser rejeitada, que e o
    # comportamento seguro.
    if headers and len(headers) != n_col:
        notas.append(("error",
                      f"o cabecalho tem {len(headers)} colunas e as linhas de dados "
                      f"tem {n_col}: o cabecalho nao descreve esta tabela, e sao os "
                      f"nomes que decidem qual e a coluna a executar"))
        return {}, mrm_score, notas
    nomes = headers
    tentativas = []
    for col in range(1, n_col):
        alloc_col, nao_mapeadas = {}, []
        for cells in linhas:
            if col >= len(cells):
                continue
            pct = _percentagem(cells[col])
            if pct is None:
                continue
            bucket, _como = map_asset_class(cells[0])
            if bucket:
                alloc_col[bucket] = alloc_col.get(bucket, 0.0) + pct
            elif _e_linha_de_total(cells[0]):
                # Uma linha "Total 100%" nao e uma classe de activo: e a soma.
                pass
            else:
                nao_mapeadas.append((cells[0], pct))
        if not alloc_col:
            continue
        # Uma linha cujo nome nao mapeia para bucket nenhum e um BURACO na
        # alocacao, nao um detalhe. O peso dela desaparecia com um simples
        # `warning`, o resto passava nos 100 +/- 5, e o motor executava uma
        # carteira com menos capital do que tinha — a diferenca evaporava-se e
        # aparecia na semana seguinte como uma perda de desempenho. Aconteceu
        # com dados reais: a edicao de 14 Mar perdeu "Defensive Healthcare" e
        # "Consumer Staples", 20 pontos, e so nao passou porque foram 20 e nao 5.
        valida, problemas = rules.validate_allocation(alloc_col)
        if nao_mapeadas:
            valida = False
            problemas = problemas + [
                "linha nao reconhecida: '{}' ({}%) — o peso dela nao entra em "
                "bucket nenhum e desapareceria da carteira".format(nome, pct_n)
                for nome, pct_n in nao_mapeadas]
        tentativas.append({"col": col, "alloc": alloc_col, "valida": valida,
                           "problemas": problemas, "nao_mapeadas": nao_mapeadas,
                           "nome": nomes[col] if col < len(nomes) else f"coluna {col}"})

    validas = [t for t in tentativas if t["valida"]]

    # Quando mais do que uma coluna soma 100 sobre os seis buckets, ai sim o
    # NOME desempata — mas so ai, e nesta ordem. Primeiro caem as colunas que
    # nao sao a carteira de todo (um "Benchmark" ao lado de um "Tactical
    # Weight"). Depois, entre as que restam, ganha a que fala do FUTURO: numa
    # tabela "Current Weight | Regime Target", o que o motor tem de executar e o
    # alvo, e ler o peso actual era executar em silencio a alocacao da semana
    # passada — o que aconteceu, verificavelmente, nas edicoes 6 e 7.
    # Tudo o que e uma referencia e nao a carteira. A lista era curta demais:
    # "Strategic Anchor", "Long-Run Average", "SAA Weight", "Peer Median" e
    # "Model" passavam todas, e o filtro do passado — que existe para escolher a
    # coluna do futuro — escolhia-as activamente quando a coluna da carteira se
    # chamava "Current Weight". Quatro das 26 edicoes reais usam esse nome.
    NAO_E_A_CARTEIRA = (r'benchmark|index|neutral|policy|passive|spy\b|reference|'
                        r'strategic|saa\b|long[- ]run|average|median|peer|model|anchor')
    # `macro` e `resume` entraram com o vocabulario que o PROPRIO prompt passou
    # a ensinar ao modelo: em Critical, a linha "Macro allocation on record
    # (this is what resumes when Gauge B stands down)" e a tabela e a macro, nao
    # a alocacao efectiva. Sem estas palavras, "Effective Allocation | Macro
    # Allocation" era rejeitada por ambiguidade (semana sem newsletter, e numa
    # semana semestral seis meses de espera) e "Regime Target | Macro
    # Allocation" escolhia ACTIVAMENTE a coluna de crise — que o motor depois
    # lia como se fosse a alocacao a retomar.
    ALVO             = (r'target|new\b|proposed|forward|recommend|regime|this week|'
                        r'ahead|updated|macro|resume|to\b')
    # E do lado do passado entra o vocabulario da alocacao EFECTIVA: em Critical
    # ela e o vector fixo de crise, que o motor ja conhece e nao precisa de ler.
    PASSADO          = (r'current|previous|prior|last|actual|existing|old|held|'
                        r'week ago|effective|in force|active now|crisis|'
                        r'critical vector|from\b')

    # Incondicional, e nao so quando ha empate. Uma coluna "Benchmark 60/40" ao
    # lado de uma coluna de bandas e a UNICA que soma 100: sozinha, era aceite e
    # executada como se fosse a carteira. O que a exclui nao e ser a segunda —
    # e nao ser a carteira.
    # ... mas uma coluna que ALEM DISSO fala de destino nao e uma referencia: e
    # a carteira com um adjectivo. "Strategic Macro Allocation", "Policy Weight
    # (Macro)", "Long-Run Macro Allocation" sao exactamente como a industria
    # chama aquilo que a regra 8 pede, e a lista de referencias contem
    # `strategic`, `policy`, `long-run`, `anchor`, `model`. Uma edicao
    # perfeitamente conforme ficava com a faixa e a semana sem rebalanceamento
    # por causa do adjectivo. O que exclui uma coluna e nao ser a carteira, e um
    # "Benchmark 60/40" continua a nao ser: ele nao casa com o vocabulario de
    # destino.
    # A excepcao e o vocabulario que a regra 8 ENSINA ao modelo, nao o `ALVO`
    # inteiro. Com o `ALVO` (que inclui `target`, `new`, `to`), um "Benchmark
    # Target" ou um "Index Target" passavam a ser aceites e EXECUTADOS: uma
    # referencia 60/40 negociada como se fosse a carteira. O que se quer e nao
    # rejeitar a carteira por causa de um adjectivo — "Strategic Macro
    # Allocation" e a carteira; "Benchmark Target" nao e.
    CARTEIRA_APESAR_DO_ADJECTIVO = r'macro|regime target|resume'
    nao_carteira = [t for t in validas
                    if re.search(NAO_E_A_CARTEIRA, t["nome"])
                    and not re.search(CARTEIRA_APESAR_DO_ADJECTIVO, t["nome"])]
    validas = [t for t in validas if t not in nao_carteira]
    if not validas and nao_carteira:
        notas.append(("error",
                      "a unica coluna que soma 100 e "
                      + str([t["nome"] for t in nao_carteira])
                      + ", que nao e a carteira — e uma referencia"))
        return {}, mrm_score, notas

    # Duas colunas que dao a MESMA alocacao nao sao ambiguas: e a mesma
    # instrucao escrita duas vezes ("Target Weight | Regime Target").
    if len(validas) > 1 and all(t["alloc"] == validas[0]["alloc"] for t in validas):
        validas = validas[:1]

    # As colunas que falam do passado saem — e nao so as que impedem o desempate.
    # Sem este passo simetrico, "Last Week | This Week", "Previous | Updated" e
    # "From | To" eram todas rejeitadas por ambiguidade, e uma rejeicao numa
    # semana semestral custa seis meses de espera pela oportunidade seguinte.
    if len(validas) > 1:
        # A coluna que sobrevive ao filtro do passado tem de casar POSITIVAMENTE
        # com o vocabulario de destino. Sem isso, bastava a outra coluna
        # chamar-se "current" para esta ganhar por omissao, fosse ela o que
        # fosse — e o que se ganhava em "Last Week | This Week" perdia-se em
        # "Current Weight | <qualquer coisa>".
        futuro = [t for t in validas
                  if not re.search(PASSADO, t["nome"]) and re.search(ALVO, t["nome"])]
        if len(futuro) == 1:
            validas = futuro
    if len(validas) > 1:
        alvo = [t for t in validas
                if re.search(ALVO, t["nome"]) and not re.search(PASSADO, t["nome"])]
        if len(alvo) == 1:
            validas = alvo
    if len(validas) > 1:
        notas.append(("error",
                      "a tabela tem mais do que uma coluna que podia ser a alocacao "
                      + str([t["nome"] for t in validas])
                      + " — nao ha forma de saber qual e a que o motor deve executar"))
        return {}, mrm_score, notas
    if not validas:
        if not tentativas:
            notas.append(("error", "nenhuma coluna da tabela traz percentagens legiveis"))
            return {}, mrm_score, notas
        # A que chegou mais perto de 100 e a que melhor explica o que correu mal.
        melhor = min(tentativas, key=lambda t: abs(sum(t["alloc"].values()) - 100))
        for nome, pct_n in melhor["nao_mapeadas"]:
            notas.append(("error", f"  Unmatched asset class: '{nome}' ({pct_n}%) "
                                   f"— a alocacao ficaria incompleta"))
        for prob in melhor["problemas"]:
            notas.append(("error", f"Allocation rejected — {prob}"))
        return {}, mrm_score, notas

    escolhida = validas[0]
    bucket_alloc = escolhida["alloc"]
    notas.append(("info", f"Percentage column: {escolhida['col']} ('{escolhida['nome']}')"))
    for nome, pct_n in escolhida["nao_mapeadas"]:
        notas.append(("warning", f"  Unmatched asset class: '{nome}' ({pct_n}%)"))

    ok, problems = rules.validate_allocation(bucket_alloc)
    if ok:
        notas.append(("info", f"Allocation parsed OK: {bucket_alloc} (total={sum(bucket_alloc.values()):.1f}%)"))
        return bucket_alloc, mrm_score, notas
    for prob in problems:
        notas.append(("error", f"Allocation rejected — {prob}"))
    return {}, mrm_score, notas
