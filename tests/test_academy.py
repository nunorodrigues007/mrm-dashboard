"""
A Academia nao pode contradizer o data.json.

Este teste existe por causa de um defeito concreto. Os cinco capitulos de
pilares tinham uma caixa "Current Reading" onde o JavaScript injectava o valor
ao vivo no meio de uma frase escrita a mao no dia do lancamento. O resultado,
em Setembro de 2026, era o capitulo do Premium a dizer:

    "⚡ CURRENT READING — ALERT ZONE BREACHED
     The current ERP stands at -0.22% — within the Compressed range and
     approaching the Red Alert threshold of 0.80%. At this level, a 22bps
     further compression would trigger a Sentinel alert."

A etiqueta dizia que o limiar tinha sido rompido e a frase seguinte dizia que
estavamos a aproximar-nos dele. E o capitulo da Liquidity injectava o valor do
Buffett Indicator numa frase que nomeava "Market Cap / M2 Ratio", uma metrica
que o framework tinha deixado de usar.

O teste garante o que faltava: que nenhum numero, nome de metrica ou limiar dos
capitulos esta escrito no HTML. Se alguem voltar a escrever um a mao, falha.

Sem rede: le o index.html e o mrm_rules.py em disco.
"""
import re, sys, types
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mrm_rules as rules

HTML = (ROOT / "index.html").read_text(encoding="utf-8")
ok = 0

def true(cond, what):
    global ok
    assert cond, what
    ok += 1

def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1

# ── as cascas existem, uma por pilar ─────────────────────────────────────────
for pid in rules.PILLAR_ORDER:
    for shell in (f'id="bands-{pid}"', f'id="thead-{pid}"', f'id="note-{pid}"',
                  f'id="text-{pid}"', f'id="callout-label-{pid}"'):
        true(shell in HTML, f"falta a casca {shell} no capitulo de {pid}")

# ── e estao vazias: nenhuma banda escrita a mao ──────────────────────────────
for pid in rules.PILLAR_ORDER:
    m = re.search(r'<tbody id="bands-%s">(.*?)</tbody>' % pid, HTML, re.S)
    true(m and not m.group(1).strip(), f"o tbody de {pid} tem HTML escrito a mao")
    m = re.search(r'<p id="text-%s">(.*?)</p>' % pid, HTML, re.S)
    true(m and "Loading" in m.group(1),
         f"a caixa de {pid} tem prosa escrita a mao em vez do estado de carregamento")

# ── os spans que o codigo antigo preenchia desapareceram ─────────────────────
# Eram o mecanismo do defeito: valor ao vivo dentro de frase estatica.
for pid in rules.PILLAR_ORDER:
    true(f'id="value-{pid}"' not in HTML, f"o span value-{pid} devia ter desaparecido")
    true(f'id="score-{pid}"' not in HTML, f"o span score-{pid} devia ter desaparecido")

# ── as frases que contradiziam os dados nao voltaram ─────────────────────────
for frase in ("approaching the <strong>Red Alert threshold",
              "22bps further compression",
              "Market Cap / M2 Ratio currently stands",
              "has been trending higher for six consecutive quarters",
              "Bank NPL ratios currently stand at approximately"):
    true(frase not in HTML, f"a frase estatica voltou ao HTML: {frase!r}")

# ── a metrica obsoleta so aparece onde e explicitamente historica ────────────
for m in re.finditer(r"WILL5000PRFC", HTML):
    ctx = HTML[max(0, m.start() - 400):m.start()]
    true("used to measure" in ctx or "Until 2026" in ctx,
         "WILL5000PRFC aparece fora da nota historica")
true("Market Cap / M2" not in re.sub(r"<!--.*?-->", "", HTML.split("// ─")[0], flags=re.S),
     "Market Cap / M2 ainda aparece no corpo da pagina")

# ── o data.json e sempre lido fresco, nunca do cache do browser ─────────────
# Uma pagina aberta minutos depois de uma publicacao mostrava os numeros
# anteriores sem o dizer. Desde que a Academia le as bandas do data.json, um
# ficheiro velho o suficiente para nao as ter deixa os capitulos em branco.
true("fetch('data.json', { cache: 'no-store' })" in HTML,
     "o data.json tem de ser lido com cache: 'no-store'")
true(re.search(r"fetch\('data\.json'\)\s*[;)]", HTML) is None,
     "ficou um fetch do data.json sem instrucao de cache")

# ── sem bandas, o capitulo explica-se em vez de ficar a carregar ────────────
true("The scoring bands are published in data.json and this page could not read them" in HTML,
     "falta o estado degradado para um data.json sem bandas")

# ── o fallback de dados inventados foi removido ──────────────────────────────
true("getSampleData" not in HTML, "o fallback de dados inventados voltou")
true("globalResilienceScore: 6.4" not in HTML, "o score inventado voltou ao HTML")
true("showDataUnavailable" in HTML, "falta o estado de falha visivel")

# ── tudo o que o site desenha existe no as_dict() ────────────────────────────
d = rules.as_dict()
true("pillarScoring" in d and "pillarOrder" in d, "as_dict tem de publicar as bandas")
for pid in rules.PILLAR_ORDER:
    spec = d["pillarScoring"][pid]
    for field in ("roman", "name", "metric", "shortMetric", "fredSeries", "weight",
                  "scoredOn", "scoredOnLabel", "worseWhen", "edges", "coreQuestion",
                  "rangeHeader", "contextHeader", "unit", "digits", "bands"):
        true(field in spec, f"{pid}: o site le spec.{field}, que nao esta publicado")
    for b in spec["bands"]:
        for field in ("lo", "hi", "score", "label", "context", "reading"):
            true(field in b, f"{pid}: uma banda sem {field} deixaria uma celula vazia no site")

# ── e o inverso: o JS nao le nada que nao exista ─────────────────────────────
lidos = set(re.findall(r"\bspec\.([A-Za-z]+)", HTML))
publicados = set(d["pillarScoring"]["cycle"]) | {"bands"}
eq(sorted(lidos - publicados), [], "o index.html le campos que o as_dict nao publica")

print(f"TODOS OS {ok} TESTES PASSARAM")
