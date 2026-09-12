"""
Testes do mrm_rules.py — as regras canónicas — e da sua unicidade.

O ponto destes testes não é só verificar cada função: é garantir que mais nenhum
módulo redefine as regras. Os testes de identidade abaixo falham se alguém voltar
a escrever uma segunda cópia do mapa de ETF ou do vector de pesos.

Sem rede: o yfinance é substituído por um stub antes do import.
"""
import sys, types, json
from datetime import date
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mrm_rules as rules
import update_portfolio as up

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1

def true(cond, what):
    eq(bool(cond), True, what)

# ── Unicidade: os outros módulos apontam para estes objectos, não para cópias ──
true(up.REGIME_ETF_MAP is rules.REGIME_ETF_MAP, "update_portfolio usa o mapa canonico")
true(up.CRITICAL_WEIGHTS is rules.CRITICAL_WEIGHTS, "update_portfolio usa os pesos canonicos")
true(up.BUCKETS is rules.BUCKETS, "update_portfolio usa os buckets canonicos")
true(up.classify_regime is rules.classify_regime, "classify_regime nao esta duplicado")
true(up.decide_rebalance is rules.decide_rebalance, "decide_rebalance nao esta duplicado")
true(up.effective_bucket_alloc is rules.effective_bucket_alloc, "effective_bucket_alloc nao esta duplicado")

# ── Coerência interna do mapa e dos pesos ─────────────────────────────────────
for key, etfs in rules.REGIME_ETF_MAP.items():
    eq(sorted(etfs), sorted(rules.BUCKETS), f"{key} cobre os 6 buckets")
for key, w in rules.CRITICAL_WEIGHTS.items():
    eq(round(sum(w.values()), 6), 100.0, f"{key} soma 100%")
    eq(sorted(w), sorted(rules.BUCKETS), f"{key} pesa os 6 buckets")
    true(key in rules.REGIME_ETF_MAP, f"{key} tem mapa de ETF")
eq(sorted(rules.ALLOCATION_BANDS), sorted(rules.BUCKETS), "ha uma banda por bucket")
for b, (lo, hi) in rules.ALLOCATION_BANDS.items():
    true(0 <= lo < hi <= 100, f"banda de {b} e um intervalo valido")

# ── classify_regime ───────────────────────────────────────────────────────────
eq(rules.classify_regime(9.5, False, "Turbulence"), "Turbulence", "score alto sozinho nao faz Critical")
eq(rules.classify_regime(6.97, True, "Turbulence"), "Critical", "medidor B faz Critical")
eq(rules.classify_regime(3.5, False, "Turbulence"), "Resilient", "score baixo com stress off")
eq(rules.classify_regime(6.97, None, "Critical"), "Critical", "n/d mantem o estado")

# ── score_band: rotula o medidor A, nao o regime da carteira ─────────────────
eq(rules.score_band(9.0), "Critical", "score 9 e Critical como rotulo")
eq(rules.classify_regime(9.0, False, "Turbulence"), "Turbulence", "mas a carteira fica em Turbulence")
eq(rules.score_band(4.0), "Resilient", "limiar Resilient inclusivo")
eq(rules.score_band(None), "nd", "sem score, n/d")

# ── sub-regime ────────────────────────────────────────────────────────────────
eq(rules.subregime_from_gauge("FTQ", False)[0], "Critical_Stress", "entrada fresca e defensiva")
eq(rules.subregime_from_gauge("FTQ", True)[0], "Critical_FTQ", "FTQ confirmado")
eq(rules.subregime_from_gauge(None, True)[0], "Critical_Stress", "sinal ausente e defensivo")

# ── decide_rebalance ──────────────────────────────────────────────────────────
D = rules.decide_rebalance
eq(D("Critical", "Turbulence", "Critical_Stress", None, False), "stress_on", "entrada")
eq(D("Turbulence", "Critical", None, "Critical_FTQ", False), "stress_off", "saida")
eq(D("Turbulence", "Turbulence", None, None, True), "semestral_rebalance", "semestral")
eq(D("Turbulence", "Turbulence", None, None, False), None, "sem evento")
eq(D("Critical", "Turbulence", "Critical_Stress", None, True), "stress_on", "stress ganha ao semestral")
eq(D("Turbulence", "Resilient", None, None, False), "resilient_off", "saida de Resilient e imediata")
eq(D("Resilient", "Turbulence", None, None, False), None, "entrada em Resilient exige confirmacao")
eq(D("Resilient", "Turbulence", None, None, False, "emergency_resilient_3.8"),
   "emergency_resilient_3.8", "entrada em Resilient com confirmacao")
eq(D("Critical", "Resilient", "Critical_Stress", None, False), "stress_on",
   "de Resilient para Critical continua a ser entrada em stress")

# ── validate_allocation ───────────────────────────────────────────────────────
good = {"US_EQUITIES": 20.0, "US_TREASURIES": 25.0, "IG_CREDIT": 15.0,
        "COMMODITIES": 12.0, "CASH": 20.0, "ALTERNATIVES": 8.0}
eq(rules.validate_allocation(good), (True, []), "alocacao tipica passa")
eq(rules.validate_allocation({})[0], False, "alocacao vazia reprova")
eq(rules.validate_allocation({**good, "US_EQUITIES": 80.0})[0], False, "80% em accoes reprova")
eq(rules.validate_allocation({**good, "US_TREASURIES": 0.0})[0], False, "0% em treasuries reprova")
half = {k: v / 2 for k, v in good.items()}
eq(rules.validate_allocation(half)[0], False, "total a 50% reprova")
eq(rules.validate_allocation({**good, "GOLD_BARS": 0.0})[0], False, "bucket desconhecido reprova")

# as 25 edições legíveis têm de continuar a passar as bandas
import glob, logging
logging.disable(logging.ERROR)
# O que se afirma NAO e uma contagem.
#
# Era `rejeitadas <= 2`, com exactamente 2 no arquivo — margem zero. E o sistema
# tem um caminho declarado que a excede: quando o modelo escreve uma tabela que
# o parser nao le, o `send_newsletter` publica a edicao na mesma, com a faixa
# "Allocation not validated", porque calar a semana inteira seria pagar por um
# problema um preco maior do que o problema. Na primeira vez que isso
# acontecesse, este ficheiro — que e portao dos dois jobs de sexta — ficava
# vermelho para sempre.
#
# A invariante e outra: uma edicao ilegivel ou e anterior ao esquema de seis
# buckets, ou DIZ ao leitor que a tabela nao foi validada. Nenhuma edicao pode
# ficar ilegivel em silencio.
#
# E esta invariante nao pode ser violada por uma corrida normal: o
# `generate_newsletter` levanta nas falhas de forma e, quando so a alocacao
# falha, publica COM a faixa — o que esta provado em test_newsletter.py, onde se
# verifica que a faixa aparece no HTML publicado. Uma edicao ilegivel e muda no
# arquivo so pode vir de uma edicao manual do ficheiro, e e isso que esta linha
# apanha.
PRIMEIRA_COM_SEIS_BUCKETS = 3
# O texto da faixa vem do PRODUTOR. Escrito a mao aqui, mudar a frase no
# send_newsletter deixava este teste a procurar a frase antiga: na primeira
# semana em que uma edicao saisse com a faixa nova e a tabela ilegivel, este
# ficheiro classificava-a como "ilegivel muda" e o portao ficava vermelho para
# sempre sobre um ficheiro ja commitado.
import importlib.util as _iu_faixa
_sp_faixa = _iu_faixa.spec_from_file_location(
    "sn_faixa", Path(__file__).resolve().parent.parent / "send_newsletter.py")
_sn_faixa = _iu_faixa.module_from_spec(_sp_faixa)
_sp_faixa.loader.exec_module(_sn_faixa)
_MARCA_FAIXA = "Allocation not validated"
true(_MARCA_FAIXA in _sn_faixa.AVISO_ALOCACAO_HTML,
     f"a faixa que o gerador publica contem a marca que este teste procura "
     f"({_MARCA_FAIXA!r})")
def _classifica_arquivo(le, onde=None):
    """Cada edicao do arquivo numa de tres gavetas, sem sobreposicao.

    `le(caminho)` diz se aquele leitor consegue tirar a alocacao do ficheiro.
    Devolve (legiveis, com_faixa, pre_esquema, mudas) — e a soma das quatro tem
    de dar o arquivo inteiro.

    `onde` existe para a classificacao poder ser exercitada sobre edicoes
    CONSTRUIDAS: hoje o arquivo real nao tem nenhuma edicao com a faixa, e sem
    isso a gaveta `com_faixa` nunca corre — quebra-la deixava a suite verde e
    a primeira semana em que o gerador publicasse pelo seu caminho declarado de
    degradacao punha o portao vermelho.
    """
    raiz = Path(onde) if onde else Path(__file__).resolve().parent.parent
    legiveis, com_faixa, pre_esquema, mudas = 0, [], [], []
    for _f in sorted(glob.glob(str(raiz / "MRM_Newsletter*.html"))):
        caminho = Path(_f)
        if le(caminho):
            legiveis += 1
            continue
        # A primeira edicao de todas nao tem numero no nome: e a pre-framework.
        numero = (int(caminho.name.split("Issue")[1].split("_")[0])
                  if "Issue" in caminho.name else 1)
        texto = caminho.read_text(encoding="utf-8", errors="replace")
        if numero < PRIMEIRA_COM_SEIS_BUCKETS:
            pre_esquema.append(caminho.name)
        elif _MARCA_FAIXA in texto:
            com_faixa.append(caminho.name)
        else:
            mudas.append(caminho.name)
    return legiveis, com_faixa, pre_esquema, mudas


lidas, _faixa_up, _pre_up, ilegiveis_mudas = _classifica_arquivo(
    lambda c: bool(up.parse_newsletter(c)[0]))
logging.disable(logging.NOTSET)
eq(ilegiveis_mudas, [],
   f"nenhuma edicao do arquivo e ilegivel em silencio: as anteriores a edicao "
   f"{PRIMEIRA_COM_SEIS_BUCKETS} sao pre-esquema (a 1 soma 80%, a 2 nao tem "
   f"alternativos) e as restantes tem de levar a faixa 'Allocation not "
   f"validated' (mudas: {ilegiveis_mudas})")
# NAO um piso com folga fixa. `lidas >= total - 3` parece relativo mas a folga e
# absoluta, e o proprio sistema gasta-a: o gerador tem um caminho DECLARADO em
# que, esgotadas as tentativas, publica e ENVIA a edicao com a faixa "Allocation
# not validated" — uma edicao que, por construcao, o parser nao le. Com duas das
# tres folgas ja gastas pelas edicoes pre-esquema, bastavam DUAS semanas assim
# ao longo da vida do sistema para o portao ficar vermelho sobre ficheiros ja
# commitados — e sem recuperacao, porque o arquivo so cresce e quem o faria
# crescer e o job que o portao trava.
#
# A propriedade nao tem folga nenhuma: cada edicao do arquivo ou se le, ou tem
# uma razao declarada para nao se ler. Isso nao envelhece.
_total_arq = len(glob.glob(str(Path(__file__).resolve().parent.parent
                               / "MRM_Newsletter*.html")))
eq(lidas + len(_faixa_up) + len(_pre_up), _total_arq,
   f"todas as edicoes do arquivo estao explicadas: {lidas} legiveis + "
   f"{len(_faixa_up)} com a faixa + {len(_pre_up)} pre-esquema = {_total_arq}")
true(lidas > 0, f"e o leitor do motor le mesmo o arquivo real ({lidas} edicoes)")

# E a gaveta da FAIXA e exercitada sobre edicoes CONSTRUIDAS, porque o arquivo
# real ainda nao tem nenhuma: sem isto, quebrar a isencao da faixa deixava a
# suite verde e a primeira semana em que o gerador publicasse pelo seu caminho
# declarado — a faixa "Allocation not validated" — punha o portao vermelho
# sobre um ficheiro ja commitado e ENVIADO, sem recuperacao.
import re as _re, shutil
import tempfile as _tf_fx
_dir_fx = Path(_tf_fx.mkdtemp())
_base_fx = sorted(glob.glob(str(Path(__file__).resolve().parent.parent
                                / "MRM_Newsletter_Issue26_*.html")))[0]
_html_fx = Path(_base_fx).read_text(encoding="utf-8", errors="replace")
# uma edicao legivel
(_dir_fx / "MRM_Newsletter_Issue30_11Sep2026.html").write_text(_html_fx, encoding="utf-8")
# uma ilegivel COM a faixa: e a que o gerador publica quando a tabela nao passa
_m_fx = _re.search(r"<table[^>]*>.*?Asset Class.*?</table>", _html_fx, _re.S | _re.I)
true(_m_fx is not None, "a edicao de base tem uma tabela de alocacao para duplicar")
_ilegivel_fx = _html_fx[:_m_fx.end()] + _html_fx[_m_fx.start():_m_fx.end()] + _html_fx[_m_fx.end():]
_ilegivel_fx = _sn_faixa.marcar_alocacao_invalida(_ilegivel_fx)
(_dir_fx / "MRM_Newsletter_Issue31_18Sep2026.html").write_text(_ilegivel_fx, encoding="utf-8")
_l_fx, _fx_fx, _pre_fx, _mudas_fx = _classifica_arquivo(
    lambda c: bool(up.parse_newsletter(c)[0]), onde=_dir_fx)
eq(_mudas_fx, [],
   f"uma edicao publicada COM a faixa nao e uma edicao muda ({_mudas_fx})")
eq(len(_fx_fx), 1,
   f"e cai na gaveta da faixa, que e a razao declarada para nao ser legivel "
   f"({_fx_fx})")
eq(_l_fx, 1, f"e a legivel continua a contar como legivel ({_l_fx})")
eq(_l_fx + len(_fx_fx) + len(_pre_fx), 2,
   "e as duas estao explicadas — que e a propriedade que o portao afirma")
shutil.rmtree(_dir_fx, ignore_errors=True)

# ── O parser tem de tolerar a FORMA que um LLM escreve ────────────────────
# Desde que o send_newsletter recusa publicar uma edicao que o parser nao leia,
# uma rejeicao por causa da forma deixou de ser "o rebalanceamento nao aconteceu"
# e passou a ser "nao houve newsletter nenhuma esta semana". Cada uma destas
# formas e HTML valido que o modelo produz, e cada uma delas era rejeitada.
import newsletter_parse as _np
_LINHAS = [("US Equities", 35), ("US Treasuries", 30), ("Investment-Grade Credit", 10),
           ("Commodities", 10), ("Cash", 10), ("Alternatives", 5)]

def _tabela(cabecalho="Asset Class", celula_nome="<td>{n}</td>", pct="{p}%"):
    linhas = "".join(f"<tr>{celula_nome.format(n=n)}<td>{pct.format(p=p)}</td>"
                     f"<td>rationale</td><td>+0.0</td></tr>" for n, p in _LINHAS)
    return (f"<html><body><table><thead><tr><th>{cabecalho}</th><th>Allocation</th>"
            f"<th>Rationale</th><th>WoW</th></tr></thead><tbody>{linhas}</tbody>"
            f"</table></body></html>")

_FORMAS = {
    "simples":                     _tabela(),
    "<strong> dentro do <th>":     _tabela(cabecalho="<strong>Asset Class</strong>"),
    "espaco duro no cabecalho":    _tabela(cabecalho="Asset&nbsp;Class"),
    "<th scope=row> na linha":     _tabela(celula_nome='<th scope="row">{n}</th>'),
    "espaco duro na percentagem":  _tabela(pct="{p}&nbsp;%"),
    "nome em italico":             _tabela(celula_nome="<td><em>{n}</em></td>"),
    "entidade no nome":            _tabela(celula_nome="<td>{n}&nbsp;(broad)</td>"),
}
for _nome, _html in _FORMAS.items():
    _alloc, _sc, _notas = _np.parse_allocation(_html)
    eq(round(sum(_alloc.values()), 1) if _alloc else 0, 100.0,
       f"o parser le a tabela escrita com: {_nome}")

# E o rigor mantem-se onde deve: o conteudo continua a ser rejeitado.
_mau = _tabela().replace("<td>35%</td>", "<td>90%</td>")
_alloc_mau, _, _ = _np.parse_allocation(_mau)
eq(_alloc_mau, {}, "uma alocacao fora das bandas continua a ser rejeitada")

# ── A COLUNA certa, quando ha mais do que uma ─────────────────────────────
# A heuristica "primeira coluna que fale de percentagens" lia, em silencio, os
# pesos ANTIGOS numa tabela "Current Weight | New Target": a soma dava 100,
# todas as bandas passavam, e o motor executava a alocacao da semana passada
# como se fosse a desta. Aconteceu mesmo, nas edicoes 6 e 7 do arquivo.
_ROT = [("US Equities", 45, 30), ("US Treasuries", 20, 35),
        ("Investment-Grade Credit", 10, 10), ("Commodities", 10, 10),
        ("Cash", 10, 10), ("Alternatives", 5, 5)]

def _tabela_2col(cab_a, cab_b, celulas=lambda a, b: (f"{a}%", f"{b}%")):
    linhas = "".join("<tr><td>%s</td>%s</tr>" % (n, "".join(f"<td>{c}</td>" for c in celulas(a, b)))
                     for n, a, b in _ROT)
    return (f"<html><body><table><thead><tr><th>Asset Class</th><th>{cab_a}</th>"
            f"<th>{cab_b}</th></tr></thead><tbody>{linhas}</tbody></table></body></html>")

def _le(html):
    a, _s, n = _np.parse_allocation(html)
    return (a.get("US_EQUITIES") if a else None), [m for l, m in n if l == "error"]

eq(_le(_tabela_2col("Current Weight", "New Target"))[0], 30.0,
   "com 'Current Weight | New Target', le o alvo — nao o peso actual")
eq(_le(_tabela_2col("Current Weight", "Regime Target"))[0], 30.0,
   "e com 'Regime Target' tambem — foi assim que as edicoes 6 e 7 foram mal lidas")
eq(_le(_tabela_2col("Tactical Weight", "Benchmark"))[0], 45.0,
   "uma coluna 'Benchmark' nao e a carteira, mesmo somando 100")
# Uma banda ("10-20%") nao e uma alocacao: e o intervalo em que ela pode andar.
# A banda e construida de proposito para que o numero que a regex apanharia
# (o limite superior, o que vem colado ao %) some exactamente 100. Se as bandas
# contassem como alocacao, esta coluna seria valida, ganharia o desempate por
# "target", e o motor executaria limites de banda em vez de pesos.
eq(_le(_tabela_2col("Current Weight", "Target Range",
                    celulas=lambda a, b: (f"{a}%", f"{max(b-10,0)}\u2013{b}%")))[0], 45.0,
   "uma coluna de bandas nao e candidata; le-se a coluna de pesos (edicao 13)")
# Formas plausiveis que um LLM escreve para dizer "antes | depois". Rejeitar
# qualquer uma delas cala a semana — e numa semana semestral custa seis meses
# ate a oportunidade seguinte.
for _antes, _depois in (("Last Week", "This Week"), ("Previous", "Updated"),
                        ("Week Ago", "Week Ahead"), ("From", "To")):
    eq(_le(_tabela_2col(_antes, _depois))[0], 30.0,
       f"'{_antes} | {_depois}': le a coluna do depois")
# "Old Weight | Weight" nao tem nada que identifique a segunda como o destino:
# rejeita-se em vez de assumir que a coluna sem nome informativo e o alvo.
eq(_le(_tabela_2col("Old Weight", "Weight"))[0], None,
   "'Old Weight | Weight' e ambigua — nao se adivinha")

# Uma coluna de REFERENCIA ao lado da coluna da carteira: a carteira ganha, e
# nao ha ambiguidade. A lista de referencias tem de ser larga — o filtro do
# passado escolhia activamente a referencia quando a coluna da carteira se
# chamava "Current Weight", e 4 das 26 edicoes reais usam esse nome.
for _ref in ("Strategic Anchor", "Long-Run Average", "SAA Weight",
             "Peer Median", "Benchmark 60/40", "Policy Weight"):
    eq(_le(_tabela_2col("Current Weight", _ref))[0], 45.0,
       f"'Current Weight | {_ref}': executa a carteira, nao a referencia")

# ── O cabecalho le-se da LINHA, nao de todos os <th> da tabela ───────────
# Um `<tr><th colspan="3">Regime-Based Asset Allocation</th></tr>` antes do
# cabecalho — HTML valido e natural — fazia deslizar todos os nomes uma
# posicao. A coluna escolhida passava a chamar-se "asset class" e executava-se
# 45% onde a edicao publicava 30%; e o filtro do benchmark, a olhar para o nome
# errado, eliminava a coluna certa e ficava com a de referencia.
def _com_titulo(a, b):
    _l = "".join(f"<tr><td>{n}</td><td>{x}%</td><td>{y}%</td></tr>" for n, x, y in _ROT)
    return ('<html><body><table><thead>'
            '<tr><th colspan="3">Regime-Based Asset Allocation</th></tr>'
            f'<tr><th>Asset Class</th><th>{a}</th><th>{b}</th></tr></thead>'
            f'<tbody>{_l}</tbody></table></body></html>')

eq(_le(_com_titulo("Current Weight", "New Target"))[0], 30.0,
   "com um titulo em colspan antes do cabecalho, le-se a coluna certa")
eq(_le(_com_titulo("Benchmark 60/40", "Regime Target"))[0], 30.0,
   "e o filtro da referencia continua a olhar para o nome certo")
# Um cabecalho que nao case com as colunas nao e usado — e sem nomes, uma
# tabela ambigua e rejeitada, que e o comportamento seguro.
_desalinhado = _com_titulo("Current Weight", "New Target").replace(
    "<th>Asset Class</th><th>Current Weight</th><th>New Target</th>",
    "<th>Asset Class</th><th>Current Weight</th>")
_v_des, _e_des = _le(_desalinhado)
eq(_v_des, None, "um cabecalho que nao descreve as colunas faz rejeitar a tabela")
true(any("nao descreve esta tabela" in m for m in _e_des),
     f"e diz porque ({_e_des})")
# E uma coluna a MAIS nas linhas tambem: o desalinhamento e simetrico.
_a_mais = _com_titulo("Current Weight", "New Target").replace(
    "<td>30%</td></tr>", "<td>30%</td><td>-15pp</td></tr>")
eq(_le(_a_mais)[0], None, "e uma coluna a mais nas linhas tambem faz rejeitar")

# ── So UMA tabela pode ser a da alocacao ─────────────────────────────────
# O prompt di-lo ao modelo; o motor ficava com a primeira que encontrasse e
# nunca verificava se havia uma segunda.
_duas = ('<html><body><table><thead><tr><th>Asset Class</th><th>Strategic Weight</th>'
         '</tr></thead><tbody>'
         + "".join(f"<tr><td>{n}</td><td>{x}%</td></tr>" for n, x, _y in _ROT)
         + '</tbody></table>'
         + _tabela_2col("Current Weight", "New Target").split("<body>")[1])
_v2, _e2 = _le(_duas)
eq(_v2, None, "com duas tabelas 'Asset Class', nao se adivinha qual e")
true(any("2 tabelas" in m for m in _e2), f"e diz porque ({_e2})")

# Duas colunas com a MESMA alocacao nao sao ambiguas: e a mesma instrucao
# escrita duas vezes.
_ROT_IGUAL = [(n, b, b) for n, _a, b in _ROT]
_linhas_ig = "".join(f"<tr><td>{n}</td><td>{a}%</td><td>{b}%</td></tr>" for n, a, b in _ROT_IGUAL)
_html_ig = ("<html><body><table><thead><tr><th>Asset Class</th><th>Target Weight</th>"
            f"<th>Regime Target</th></tr></thead><tbody>{_linhas_ig}</tbody>"
            "</table></body></html>")
eq(_le(_html_ig)[0], 30.0, "duas colunas identicas nao sao ambiguas")

# Uma coluna de referencia NAO e a carteira — nem quando e a unica que soma 100.
# Sozinha, era aceite e executada como se fosse a alocacao.
for _ref in ("Benchmark 60/40", "Policy Weight", "Neutral Allocation"):
    _v, _err = _le(_tabela_2col(_ref, "Regime Target",
                                celulas=lambda a, b: (f"{a}%", f"{max(b-10,0)}\u2013{b}%")))
    eq(_v, None, f"'{_ref}' sozinha nao e aceite como a carteira")
    true(any("nao e a carteira" in m for m in _err), f"e diz porque ({_err})")

# E quando nao ha forma de saber, NAO se adivinha.
_v, _e = _le(_tabela_2col("Weight A", "Weight B"))
eq(_v, None, "duas colunas indistinguiveis: rejeita-se em vez de adivinhar")
true(any("mais do que uma coluna" in m for m in _e),
     f"e diz porque ({_e})")

# O arquivo real continua legivel, e as duas rejeicoes sao por CONTEUDO.
# A mesma decomposicao, com o leitor do parser directo. Pelas mesmas razoes: um
# piso com folga fixa fecha a sexta-feira no dia em que o proprio gerador gastar
# a folga pelo seu caminho declarado de degradacao.
_bons, _faixa_np, _pre_np, _mudas_np = _classifica_arquivo(
    lambda c: bool(_np.parse_allocation(c.read_text(encoding="utf-8"))[0]))
eq(_mudas_np, [],
   f"nenhuma edicao e ilegivel pelo parser sem uma razao declarada ({_mudas_np})")
_total_bons = len(glob.glob(str(Path(__file__).resolve().parent.parent
                                / "MRM_Newsletter*.html")))
eq(_bons + len(_faixa_np) + len(_pre_np), _total_bons,
   f"todas as edicoes reais estao explicadas: {_bons} legiveis + "
   f"{len(_faixa_np)} com a faixa + {len(_pre_np)} pre-esquema = {_total_bons}")
true(_bons > 0, f"e o parser le mesmo as edicoes reais ({_bons} edicoes)")

# ── Uma ancora que diz "N/A" NAO e uma ancora em falta ────────────────────
#
# O recurso do `parse_allocation` — o primeiro decimal nu de qualquer etiqueta —
# existe para as edicoes publicadas antes de a ancora existir. Com uma ancora
# presente mas nao numerica (a semana n/d escreve `data-mrm-score="N/A"`), ele
# corria na mesma e devolvia o "+0.0" da coluna WoW, o "6.97" da caixa de
# rebalanceamento, ou um numero qualquer de uma tabela de pilares — entregue ao
# motor como se fosse o score da semana. E fora da escala fechava o portao para
# sempre, sobre uma edicao ja publicada e ENVIADA.
_nd_ancora = ('<html><body><div class="score-num" data-mrm-score="N/A">N/A</div>'
              '<table><tr><td>63.4</td></tr></table>'
              '<div>WoW <span>+0.0</span></div></body></html>')
eq(_np.parse_allocation(_nd_ancora)[1], None,
   "com a ancora a dizer N/A, o motor le n/d — nao o primeiro decimal que "
   "encontrar no documento")
# E o recurso continua a servir para o que existe: as edicoes SEM ancora.
_sem_ancora = ('<html><body><div class="score-num">6.9</div>'
               '<table><tr><td>63.4</td></tr></table></body></html>')
eq(_np.parse_allocation(_sem_ancora)[1], 6.9,
   "e uma edicao antiga, sem ancora nenhuma, continua a ser lida pelo recurso")
# E a ancora numerica manda sobre tudo o resto.
_com_numero = ('<html><body><div class="score-num" data-mrm-score="7.2">7.2</div>'
               '<table><tr><td>63.4</td></tr></table></body></html>')
eq(_np.parse_allocation(_com_numero)[1], 7.2,
   "e com a ancora numerica e ela que decide")

# ── O vocabulario do PARSER tem de conhecer o do PROMPT ───────────────────
#
# O prompt passou a mostrar ao modelo duas ancoras de seis buckets — "Effective
# allocation now" e "Macro allocation on record (this is what resumes when Gauge
# B stands down)" — e a regra 8 manda-o explicar a diferenca na seccao da
# alocacao. Nenhuma dessas palavras existia no vocabulario do parser: uma tabela
# com as duas colunas era rejeitada por ambiguidade (semana sem newsletter, e
# numa semana semestral seis meses de espera) e, pior, "Regime Target | Macro
# Allocation" fazia-o escolher ACTIVAMENTE a coluna de crise — que o motor
# depois lia como se fosse a alocacao a retomar. Ensinar uma linguagem ao modelo
# e nao a ensinar ao leitor e o defeito de sempre, na sua forma mais directa.
_MACRO_ESPERADA = {"US_EQUITIES": 35.0, "US_TREASURIES": 30.0, "IG_CREDIT": 10.0,
                   "COMMODITIES": 10.0, "CASH": 10.0, "ALTERNATIVES": 5.0}
_CRISE_COL = [15, 20, 20, 15, 25, 5]
_MACRO_COL = [35, 30, 10, 10, 10, 5]
_NOMES_COL = ["US Equities", "US Treasuries", "Investment-Grade Credit",
              "Commodities", "Cash", "Alternatives"]

def _tabela_duas_colunas(cab_crise, cab_macro):
    _l = "".join(
        f"<tr><td>{n}</td><td>{a}%</td><td>{b}%</td><td>Rationale</td></tr>"
        for n, a, b in zip(_NOMES_COL, _CRISE_COL, _MACRO_COL))
    return ('<html><body><div class="score-num" data-mrm-score="7.0">7.0</div>'
            f'<table><thead><tr><th>Asset Class</th><th>{cab_crise}</th>'
            f'<th>{cab_macro}</th><th>Rationale</th></tr></thead>'
            f'<tbody>{_l}</tbody></table></body></html>')

for _c1, _c2 in (("Effective Allocation", "Macro Allocation"),
                 ("Effective Allocation Now", "Macro Allocation on Record"),
                 ("Current Weight", "Macro Allocation"),
                 ("Active Now", "Resumes When Gauge B Stands Down"),
                 ("Crisis Vector (This Week)", "Macro Weight")):
    _a_col, _, _n_col = _np.parse_allocation(_tabela_duas_colunas(_c1, _c2))
    eq(_a_col, _MACRO_ESPERADA,
       f"com '{_c1}' ao lado de '{_c2}', o motor le a MACRO e nao o vector de "
       f"crise ({_a_col})")

# E o que nao consegue desempatar e RECUSADO, nunca adivinhado — a coluna errada
# aqui e uma transaccao de dinheiro real que ninguem pediu.
for _c1, _c2 in (("Regime Target", "Macro Allocation"),
                 ("This Week", "Macro Allocation")):
    _a_amb, _, _n_amb = _np.parse_allocation(_tabela_duas_colunas(_c1, _c2))
    eq(_a_amb, {},
       f"com '{_c1}' ao lado de '{_c2}' — duas colunas igualmente de destino — "
       f"o parser RECUSA em vez de escolher a de crise ({_a_amb})")
    true(any("mais do que uma coluna" in m for _t, m in _n_amb),
         f"e diz porque ({[m for _t, m in _n_amb][-2:]})")

# E o produtor fecha a ambiguidade na origem: a regra 8 manda UMA so coluna de
# percentagens, com o nome que o parser sabe ler.
_prompt_regra8 = (Path(__file__).resolve().parent.parent
                  / "send_newsletter.py").read_text(encoding="utf-8")
true("exactly ONE column of percentages" in _prompt_regra8,
     "a regra 8 exige uma so coluna de percentagens na tabela de alocacao")
true('headed "Regime Target"' in _prompt_regra8,
     "e diz como se chama essa coluna — uma so, em todos os regimes")
# Set 2026: a tabela deixou de ser uma instrucao. A regra 8 da o vector exacto e
# diz ao modelo, por palavras, que nada do que ele escreva move a carteira — e
# que uma tabela que nao bata certo faz a edicao ser recusada antes de sair.
true("the engine does not read it back" in _prompt_regra8,
     "a regra 8 diz ao modelo que a tabela nao e lida de volta pelo motor")
true("{_alloc_line}" in _prompt_regra8,
     "e da-lhe o vector exacto que tem de reproduzir")
true("REJECTED before it is sent" in _prompt_regra8,
     "e avisa que a edicao e recusada se a tabela nao bater certo")

# ── Uma coluna SOLITARIA nao e uma referencia so por levar um adjectivo ───
#
# A lista de referencias contem `strategic`, `policy`, `long-run`, `anchor`,
# `model`, `reference`, `neutral` — que e exactamente como a industria chama
# aquilo que a regra 8 pede ao modelo ("Strategic Asset Allocation", "Policy
# Portfolio"). Aplicada a uma coluna solitaria, rejeitava uma edicao
# perfeitamente conforme por causa do adjectivo: faixa de aviso e semana sem
# rebalanceamento — e numa semana semestral, seis meses de espera.
def _tabela_uma_coluna(cabecalho):
    _l = "".join(
        f"<tr><td>{n}</td><td>{v}%</td><td>Rationale</td><td>+0.0</td></tr>"
        for n, v in zip(_NOMES_COL, _MACRO_COL))
    return ('<html><body><div class="score-num" data-mrm-score="7.0">7.0</div>'
            f'<table><thead><tr><th>Asset Class</th><th>{cabecalho}</th>'
            f'<th>Rationale</th><th>WoW</th></tr></thead>'
            f'<tbody>{_l}</tbody></table></body></html>')

for _cab in ("Macro Allocation", "Strategic Macro Allocation",
             "Neutral Macro Allocation", "Long-Run Macro Allocation",
             "Macro Anchor", "Macro Allocation Model", "Regime Target"):
    _a_uma, _, _ = _np.parse_allocation(_tabela_uma_coluna(_cab))
    eq(_a_uma, _MACRO_ESPERADA,
       f"'{_cab}' e a carteira com um adjectivo, nao uma referencia ({_a_uma})")
# E o que continua a NAO ser a carteira continua a ser recusado: uma referencia
# que nao fala de destino nenhum.
# E uma REFERENCIA continua a ser uma referencia mesmo com a palavra "target"
# no nome: "Benchmark Target" e "Index Target" sao 60/40, nao a carteira, e
# aceita-los seria negociar dinheiro real sobre uma referencia. A excepcao e o
# vocabulario que a regra 8 ensina ao modelo, nao qualquer palavra de destino.
for _cab in ("Benchmark 60/40", "Peer Median", "Strategic Anchor",
             "Policy Weight", "Long-Run Average",
             "Benchmark Target", "Index Target", "Policy Target",
             "Strategic Target", "Neutral Target"):
    _a_ref, _, _n_ref = _np.parse_allocation(_tabela_uma_coluna(_cab))
    eq(_a_ref, {}, f"'{_cab}' continua a nao ser a carteira ({_a_ref})")
    true(any("nao e a carteira" in m for _t, m in _n_ref),
         f"e diz porque ({[m for _t, m in _n_ref][-2:]})")

# ── As duas listas de buckets sao a MESMA ────────────────────────────────
#
# Um bucket sem banda declarada era um KeyError dentro do `validate_allocation`,
# no meio do job da carteira, com a causa a tres ficheiros de distancia — e do
# outro lado o prompt filtrava-o em silencio, portanto o modelo nem sequer
# recebia a banda. Um lado a tolerar e o outro a rebentar esconde a causa.
eq(sorted(rules.BUCKETS), sorted(rules.ALLOCATION_BANDS),
   f"BUCKETS e ALLOCATION_BANDS cobrem os mesmos buckets "
   f"({sorted(set(rules.BUCKETS) ^ set(rules.ALLOCATION_BANDS))})")
_erro_bandas = None
try:
    rules.validate_allocation({b: 100.0 / len(rules.BUCKETS) for b in rules.BUCKETS})
except Exception as _e_bandas:                                   # noqa: BLE001
    _erro_bandas = _e_bandas
eq(_erro_bandas, None,
   f"e uma alocacao sobre todos os buckets nao levanta ({_erro_bandas!r})")

# ── calendário ────────────────────────────────────────────────────────────────
eq(rules.is_semestral_rebalance_week(date(2026, 1, 30)), True, "ultima sexta de Janeiro 2026")
eq(rules.is_semestral_rebalance_week(date(2026, 1, 23)), False, "penultima sexta nao")
eq(rules.is_semestral_rebalance_week(date(2026, 6, 26)), True, "ultima sexta de Junho 2026")
eq(rules.is_semestral_rebalance_week(date(2026, 9, 25)), False, "Setembro nao e semestral")
nxt = rules.next_semestral_date(date(2026, 9, 8))
eq((nxt.month, nxt.weekday()), (1, 4), "proxima semestral e uma sexta de Janeiro")
true(rules.is_semestral_rebalance_week(nxt), "e de facto uma semana semestral")

# ── rebalance_copy cobre todos os motivos que o motor produz ─────────────────
for reason in ("stress_on", "stress_off", "resilient_off", "semestral_rebalance", "hold",
               "no_allocation_available", "missing_prices_held", "aborted_invalid_shares",
               "critical_subregime_switch:Critical_Stress->Critical_FTQ",
               "emergency_resilient_3.8"):
    txt = rules.rebalance_copy(reason)
    true(txt and txt != reason, f"ha texto publicavel para {reason}")
eq(rules.rebalance_copy(None), rules.REBALANCE_COPY["hold"], "sem motivo = hold")

# ── as_dict é serializável e completo ─────────────────────────────────────────
d = rules.as_dict()
json.dumps(d)  # levanta se nao for serializavel
for field in ("resilientMax", "criticalMin", "regimeLabels", "etfMap",
              "criticalWeights", "allocationBands", "gaugeACaption", "gaugeBCaption"):
    true(field in d, f"as_dict inclui {field}")
eq(d["etfMap"]["Turbulence"]["US_EQUITIES"], "SPY", "as_dict leva o mapa real")
d["etfMap"]["Turbulence"]["US_EQUITIES"] = "XXX"
eq(rules.REGIME_ETF_MAP["Turbulence"]["US_EQUITIES"], "SPY", "as_dict devolve copias, nao os originais")

# ── Nomes de classe que o modelo escreve e o mapa tem de conhecer ────────
# Desde que uma linha nao reconhecida rejeita a tabela inteira, cada nome em
# falta e uma semana sem rebalanceamento. E as palavras-chave sao testadas por
# SUBSTRING, o que e uma armadilha: "rates" apanhava "IG CorpoRATES" e mandava
# credito para treasuries.
for _nome, _esperado in (("IG Corporates", "IG_CREDIT"),
                         ("Corporate Bonds", "IG_CREDIT"),
                         ("IG Bonds", "IG_CREDIT"),
                         ("Investment-Grade Credit", "IG_CREDIT"),
                         ("UST 7-10y", "US_TREASURIES"),
                         ("Intermediate Treasuries", "US_TREASURIES"),
                         ("Managed Futures", "ALTERNATIVES"),
                         ("Short-Duration Sovereigns", "CASH"),
                         ("US Large-Cap Equity", "US_EQUITIES"),
                         ("Commodity Complex", "COMMODITIES")):
    eq(_np.map_asset_class(_nome)[0], _esperado,
       f"'{_nome}' mapeia para {_esperado}")

# ── As FRONTEIRAS das bandas do score, derivadas das constantes ───────────
#
# `>=` trocado por `>` sobrevivia: um score de exactamente 8,0 mudava de banda
# publicada sem um teste vermelho. E a banda e o que colore o numero na capa do
# produto e o que a Academia promete.
for _lim_b, _dentro, _fora in ((rules.RESILIENT_MAX, "Resilient", "Turbulence"),
                               (rules.CRITICAL_MIN, "Critical", "Turbulence")):
    _e = 0.01
    if _lim_b == rules.RESILIENT_MAX:
        eq(rules.score_band(_lim_b - _e), _dentro,
           f"abaixo de {_lim_b} e {_dentro}")
        eq(rules.score_band(_lim_b), _dentro,
           f"e o limite EXACTO {_lim_b} tambem e {_dentro} — a regra publicada "
           f"e 'score <= {_lim_b}'")
        eq(rules.score_band(_lim_b + _e), _fora,
           f"e acima ja e {_fora}")
    else:
        eq(rules.score_band(_lim_b + _e), _dentro,
           f"acima de {_lim_b} e {_dentro}")
        eq(rules.score_band(_lim_b), _dentro,
           f"e o limite EXACTO {_lim_b} tambem e {_dentro} — a regra publicada "
           f"e 'score >= {_lim_b}'")
        eq(rules.score_band(_lim_b - _e), _fora,
           f"e abaixo ja e {_fora}")
# ── A tolerancia do total: a fronteira DECLARADA, exercitada dos dois lados ─
#
# `ALLOCATION_TOTAL_TOLERANCE = 5.0` e um numero que o codigo publica na propria
# mensagem de recusa ("outside 100 +/- 5"), mas nenhum caso de teste chegava
# perto dele: todas as alocacoes somavam 100 ou entao 60. Trocar o 5.0 por 25.0
# nao punha um unico teste vermelho — e uma edicao que somasse 120% passava a
# ser aceite e executada com dinheiro real, com um bucket a levar um quinto a
# mais do que o texto que o subscritor leu.
#
# Os numeros aqui sao LITERAIS de proposito. Derivar a fronteira da constante
# fazia o teste mover-se com a mutacao — a expectativa lida do estado que o
# sistema reescreve — e nada ficava vermelho.
def _aloc(us_eq):
    return {"US_EQUITIES": us_eq, "US_TREASURIES": 30.0, "IG_CREDIT": 10.0,
            "COMMODITIES": 5.0, "CASH": 10.0, "ALTERNATIVES": 5.0}   # base: 60


eq(rules.ALLOCATION_TOTAL_TOLERANCE, 5.0,
   "a tolerancia declarada do total e 5 pontos percentuais — muda-la e uma "
   "decisao, nao um acidente")
_ok_105, _pr_105 = rules.validate_allocation(_aloc(45.0))          # 105.0
true(_ok_105, f"um total de exactamente 105% esta DENTRO de 100 +/- 5 ({_pr_105})")
_ok_106, _pr_106 = rules.validate_allocation(_aloc(45.1))          # 105.1
true(not _ok_106, "um total de 105,1% ja esta FORA e a alocacao e rejeitada")
true(any("outside 100" in _x for _x in _pr_106),
     f"e a razao dada e o total, nao outra coisa ({_pr_106})")
_ok_95, _pr_95 = rules.validate_allocation(
    dict(_aloc(40.0), CASH=5.0))                                   # 95.0
true(_ok_95, f"e um total de exactamente 95% tambem esta dentro ({_pr_95})")
_ok_94, _pr_94 = rules.validate_allocation(
    dict(_aloc(39.9), CASH=5.0))                                   # 94.9
true(not _ok_94, "e 94,9% ja esta fora")
true(any("outside 100" in _x for _x in _pr_94),
     f"pelo total ({_pr_94})")
# ── E a TABELA de bandas, limite a limite ─────────────────────────────────
#
# A ronda anterior armou a fronteira do TOTAL e deixou a tabela ao lado, na
# mesma funcao, intacta: onze dos doze limites, mais a propria comparacao
# `lo <= pct <= hi`, sobreviviam a mutacao com a suite inteira verde. O unico
# caso acima de uma banda em toda a suite era `US_EQUITIES: 70` — uma linha da
# tabela viva, as outras cinco por armar. E o comentario que declara estas
# bandas da o exemplo literal `0% em treasuries`, que era precisamente a
# mutacao que passava.
#
# `validate_allocation` e o que separa a tabela que um modelo escreve semana a
# semana do rebalanceamento que a EXECUTA. Alargar uma banda e uma alteracao de
# politica de investimento; sem isto, a suite tratava-a como cosmetica.
#
# A tabela e fixada com LITERAIS: um laco que lesse `ALLOCATION_BANDS` para
# construir a expectativa move-se com a mutacao e nao afirma nada.
eq(rules.ALLOCATION_BANDS, {
    "US_EQUITIES":   (5.0, 60.0),
    "US_TREASURIES": (10.0, 50.0),
    "IG_CREDIT":     (0.0, 35.0),
    "COMMODITIES":   (0.0, 25.0),
    "CASH":          (0.0, 40.0),
    "ALTERNATIVES":  (0.0, 20.0),
}, "as bandas declaradas por bucket — alarga-las e uma decisao de politica de "
   "investimento, nao um refactor")


def _com(bucket, pct):
    """Uma alocacao com `bucket` em `pct` e o resto a fechar 100 dentro das bandas.

    O complemento vai para os buckets com mais folga, e nunca para o proprio
    bucket em prova: senao a recusa podia vir do vizinho e o teste nao falava do
    limite que diz estar a provar.
    """
    _base = {"US_EQUITIES": 20.0, "US_TREASURIES": 20.0, "IG_CREDIT": 10.0,
             "COMMODITIES": 5.0, "CASH": 10.0, "ALTERNATIVES": 5.0}   # 70
    _a = dict(_base, **{bucket: pct})
    _falta = 100.0 - sum(_a.values())
    # Distribui-se pela folga de cada bucket que nao esta em prova.
    for _b in ("CASH", "IG_CREDIT", "US_TREASURIES", "US_EQUITIES",
               "COMMODITIES", "ALTERNATIVES"):
        if _b == bucket or abs(_falta) < 1e-9:
            continue
        _lo, _hi = rules.ALLOCATION_BANDS[_b]
        _novo = min(_hi, max(_lo, _a[_b] + _falta))
        _falta -= (_novo - _a[_b])
        _a[_b] = round(_novo, 4)
    return _a, abs(_falta) < 1e-9


# Cada limite, exactamente em cima (aceite) e um decimo acima/abaixo (recusado).
# Os pares vem da tabela literal acima, nao de `ALLOCATION_BANDS`.
for _b_n, (_lo_n, _hi_n) in (("US_EQUITIES", (5.0, 60.0)),
                             ("US_TREASURIES", (10.0, 50.0)),
                             ("IG_CREDIT", (0.0, 35.0)),
                             ("COMMODITIES", (0.0, 25.0)),
                             ("CASH", (0.0, 40.0)),
                             ("ALTERNATIVES", (0.0, 20.0))):
    for _pct_n, _quer_n in ((_lo_n, True), (_hi_n, True),
                            (_lo_n - 0.1, False), (_hi_n + 0.1, False)):
        if _pct_n < 0:
            continue                      # abaixo de zero nao e uma alocacao
        _a_n, _fecha_n = _com(_b_n, _pct_n)
        true(_fecha_n,
             f"o caso de prova de {_b_n}={_pct_n} fecha mesmo os 100% ({_a_n})")
        _ok_n, _pr_n = rules.validate_allocation(_a_n)
        eq(_ok_n, _quer_n,
           f"{_b_n} a {_pct_n}% (banda {_lo_n}-{_hi_n}): "
           f"{'aceite' if _quer_n else 'recusado'} ({_pr_n}, {_a_n})")
        if not _quer_n:
            true(any(_b_n in _x for _x in _pr_n),
                 f"e a razao dada nomeia o {_b_n}, nao outro bucket ({_pr_n})")

# E a banda por bucket decide por si: um total perfeito com um bucket fora da
# banda nao passa — senao esta prova so falaria do total.
_ok_b, _pr_b = rules.validate_allocation(
    {"US_EQUITIES": 70.0, "US_TREASURIES": 10.0, "IG_CREDIT": 10.0,
     "COMMODITIES": 5.0, "CASH": 5.0, "ALTERNATIVES": 0.0})         # 100, US_EQ > 60
true(not _ok_b, f"um total certo com um bucket fora da banda e rejeitado ({_pr_b})")

# E o mesmo para o regime, que e o que decide a carteira.
eq(rules.classify_regime(rules.RESILIENT_MAX, False, "Turbulence"), "Resilient",
   f"um score de exactamente {rules.RESILIENT_MAX} sinaliza Resilient")
eq(rules.classify_regime(rules.RESILIENT_MAX + 0.01, False, "Turbulence"),
   "Turbulence", "e um pouco acima ja nao")


# ── A PRECEDENCIA de decide_rebalance, celula a celula ────────────────────
#
# Esta funcao tem seis argumentos e decide se a carteira roda, e com que
# etiqueta. Estava verificada por casos avulsos: a guarda da emergencia —
# `emergency_reason and regime == "Resilient" and was_regime != "Resilient"` —
# tinha as DUAS metades desarmadas, e cada metade tem um comentario de seis
# linhas a descrever o defeito real que existe para impedir.
#
#   - sem `was_regime != "Resilient"`: ja dentro de Resilient com o score baixo
#     a persistir, o motivo voltava a ser `emergency_resilient_*` TODAS as
#     sextas — rebalanceamento semanal, com custo de transaccao real, e a
#     newsletter a publicar "the portfolio rotated to the Resilient map" numa
#     carteira que ja la estava, contra a regra declarada de nao haver
#     rebalanceamento tactico semanal.
#   - com `regime != "Critical"` em vez de `== "Resilient"`: alcancavel hoje —
#     basta o medidor B ficar todo em n/d (falha da FRED) com a carteira em
#     Turbulence, e o motor rebalanceava o mapa de TURBULENCE publicando o texto
#     do Resilient, que e falso, e outra vez todas as semanas.
#
# Nenhuma asserçao avulsa arma uma precedencia. O que se fixa aqui e a TABELA
# INTEIRA — todos os regimes x regimes anteriores x sub-regimes x semestral x
# emergencia — com a saida escrita a mao em cada celula. Uma alteracao de
# precedencia passa a ser uma alteracao visivel desta tabela.
_PRECEDENCIA = [
    # (regime, was_regime, sub, was_sub, semestral, emergency) -> motivo
    ('Resilient', 'Resilient', None, None, False, None, None),
    ('Resilient', 'Resilient', None, None, False, 'emergency_resilient_x', None),
    ('Resilient', 'Resilient', None, None, True, None, 'semestral_rebalance'),
    ('Resilient', 'Resilient', None, None, True, 'emergency_resilient_x', 'semestral_rebalance'),
    ('Resilient', 'Turbulence', None, None, False, None, None),
    ('Resilient', 'Turbulence', None, None, False, 'emergency_resilient_x', 'emergency_resilient_x'),
    ('Resilient', 'Turbulence', None, None, True, None, 'semestral_rebalance'),
    ('Resilient', 'Turbulence', None, None, True, 'emergency_resilient_x', 'emergency_resilient_x'),
    ('Resilient', 'Critical', None, 'Critical_FTQ', False, None, 'stress_off_to_resilient'),
    ('Resilient', 'Critical', None, 'Critical_FTQ', False, 'emergency_resilient_x', 'stress_off_to_resilient'),
    ('Resilient', 'Critical', None, 'Critical_FTQ', True, None, 'stress_off_to_resilient'),
    ('Resilient', 'Critical', None, 'Critical_FTQ', True, 'emergency_resilient_x', 'stress_off_to_resilient'),
    ('Resilient', 'Critical', None, 'Critical_Stress', False, None, 'stress_off_to_resilient'),
    ('Resilient', 'Critical', None, 'Critical_Stress', False, 'emergency_resilient_x', 'stress_off_to_resilient'),
    ('Resilient', 'Critical', None, 'Critical_Stress', True, None, 'stress_off_to_resilient'),
    ('Resilient', 'Critical', None, 'Critical_Stress', True, 'emergency_resilient_x', 'stress_off_to_resilient'),
    ('Turbulence', 'Resilient', None, None, False, None, 'resilient_off'),
    ('Turbulence', 'Resilient', None, None, False, 'emergency_resilient_x', 'resilient_off'),
    ('Turbulence', 'Resilient', None, None, True, None, 'resilient_off'),
    ('Turbulence', 'Resilient', None, None, True, 'emergency_resilient_x', 'resilient_off'),
    ('Turbulence', 'Turbulence', None, None, False, None, None),
    ('Turbulence', 'Turbulence', None, None, False, 'emergency_resilient_x', None),
    ('Turbulence', 'Turbulence', None, None, True, None, 'semestral_rebalance'),
    ('Turbulence', 'Turbulence', None, None, True, 'emergency_resilient_x', 'semestral_rebalance'),
    ('Turbulence', 'Critical', None, 'Critical_FTQ', False, None, 'stress_off'),
    ('Turbulence', 'Critical', None, 'Critical_FTQ', False, 'emergency_resilient_x', 'stress_off'),
    ('Turbulence', 'Critical', None, 'Critical_FTQ', True, None, 'stress_off'),
    ('Turbulence', 'Critical', None, 'Critical_FTQ', True, 'emergency_resilient_x', 'stress_off'),
    ('Turbulence', 'Critical', None, 'Critical_Stress', False, None, 'stress_off'),
    ('Turbulence', 'Critical', None, 'Critical_Stress', False, 'emergency_resilient_x', 'stress_off'),
    ('Turbulence', 'Critical', None, 'Critical_Stress', True, None, 'stress_off'),
    ('Turbulence', 'Critical', None, 'Critical_Stress', True, 'emergency_resilient_x', 'stress_off'),
    ('Critical', 'Resilient', 'Critical_FTQ', None, False, None, 'stress_on'),
    ('Critical', 'Resilient', 'Critical_FTQ', None, False, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Resilient', 'Critical_FTQ', None, True, None, 'stress_on'),
    ('Critical', 'Resilient', 'Critical_FTQ', None, True, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Resilient', 'Critical_Stress', None, False, None, 'stress_on'),
    ('Critical', 'Resilient', 'Critical_Stress', None, False, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Resilient', 'Critical_Stress', None, True, None, 'stress_on'),
    ('Critical', 'Resilient', 'Critical_Stress', None, True, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_FTQ', None, False, None, 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_FTQ', None, False, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_FTQ', None, True, None, 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_FTQ', None, True, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_Stress', None, False, None, 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_Stress', None, False, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_Stress', None, True, None, 'stress_on'),
    ('Critical', 'Turbulence', 'Critical_Stress', None, True, 'emergency_resilient_x', 'stress_on'),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_FTQ', False, None, None),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_FTQ', False, 'emergency_resilient_x', None),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_FTQ', True, None, 'semestral_rebalance'),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_FTQ', True, 'emergency_resilient_x', 'semestral_rebalance'),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_Stress', False, None, 'critical_subregime_switch:Critical_Stress->Critical_FTQ'),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_Stress', False, 'emergency_resilient_x', 'critical_subregime_switch:Critical_Stress->Critical_FTQ'),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_Stress', True, None, 'critical_subregime_switch:Critical_Stress->Critical_FTQ'),
    ('Critical', 'Critical', 'Critical_FTQ', 'Critical_Stress', True, 'emergency_resilient_x', 'critical_subregime_switch:Critical_Stress->Critical_FTQ'),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_FTQ', False, None, 'critical_subregime_switch:Critical_FTQ->Critical_Stress'),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_FTQ', False, 'emergency_resilient_x', 'critical_subregime_switch:Critical_FTQ->Critical_Stress'),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_FTQ', True, None, 'critical_subregime_switch:Critical_FTQ->Critical_Stress'),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_FTQ', True, 'emergency_resilient_x', 'critical_subregime_switch:Critical_FTQ->Critical_Stress'),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_Stress', False, None, None),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_Stress', False, 'emergency_resilient_x', None),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_Stress', True, None, 'semestral_rebalance'),
    ('Critical', 'Critical', 'Critical_Stress', 'Critical_Stress', True, 'emergency_resilient_x', 'semestral_rebalance'),
]
for _reg_p, _was_p, _sub_p, _wsub_p, _sem_p, _emg_p, _quer_p in _PRECEDENCIA:
    eq(rules.decide_rebalance(_reg_p, _was_p, _sub_p, _wsub_p, _sem_p, _emg_p),
       _quer_p,
       f"decide_rebalance({_reg_p}, was={_was_p}, sub={_sub_p}, "
       f"was_sub={_wsub_p}, semestral={_sem_p}, emerg={_emg_p})")
# E a tabela cobre mesmo o produto todo: uma tabela a que faltassem linhas
# estaria verde por nao ter olhado para as celulas que faltam.
eq(len(_PRECEDENCIA), 64,
   "a tabela cobre os 3x3 regimes com os sub-regimes reais, semestral e "
   "emergencia — 64 celulas")
eq(len({_c[:6] for _c in _PRECEDENCIA}), 64, "e nenhuma celula esta repetida")
# As duas celulas que a guarda da emergencia existe para negar, nomeadas:
eq(rules.decide_rebalance("Resilient", "Resilient", None, None, False,
                          "emergency_resilient_x"), None,
   "ja dentro de Resilient, a emergencia NAO volta a rodar a carteira todas as "
   "semanas")
eq(rules.decide_rebalance("Turbulence", "Turbulence", None, None, False,
                          "emergency_resilient_x"), None,
   "e em Turbulence a emergencia nao roda nada — o texto que ela publica fala "
   "do mapa de Resilient, que nao e onde a carteira esta")
eq(rules.decide_rebalance("Critical", "Critical", "Critical_Stress",
                          "Critical_Stress", False, "emergency_resilient_x"), None,
   "e dentro de Critical o score baixo e esperado: tres dos cinco pilares "
   "melhoram mecanicamente numa crise")
# E o sub-regime de Critical nunca e None: o produtor devolve sempre um dos dois.
for _wcl in (True, False):
    for _gs in (None, "FTQ", "Critical_FTQ", "desconhecido"):
        _s_pr, _ = rules.subregime_from_gauge(_gs, _wcl, "Critical_Stress")
        true(_s_pr in rules.CRITICAL_WEIGHTS,
             f"subregime_from_gauge devolve sempre um sub-regime real "
             f"({_gs!r}, was_critical={_wcl}) -> {_s_pr!r}")

print(f"TODOS OS {ok} TESTES PASSARAM")
