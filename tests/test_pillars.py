"""
Testes das bandas de scoring dos cinco pilares.

Estas bandas eram funcoes escritas a mao no fetch_data.py, e as tabelas que a
Academia publicava eram outro texto, escrito a mao no HTML, que envelheceu ate
contradizer o codigo. Agora ha um sitio so — o PILLAR_SCORING do mrm_rules.py —
e o site desenha as tabelas a partir dele.

Este ficheiro prende duas coisas:

1. **Equivalencia.** As funcoes score_* originais estao aqui copiadas tal como
   estavam antes da unificacao. Um varrimento exaustivo, incluindo exactamente
   em cima de cada limiar, obriga as bandas a reproduzir o comportamento que ja
   estava publicado. Se alguem quiser mudar um limiar, muda-o de proposito e
   actualiza tambem estas funcoes de referencia — nao por acidente.

2. **Coerencia estrutural.** As bandas nao podem ter buracos nem sobreposicoes,
   o score tem de ser monotono na direccao do risco, e cada banda tem de trazer
   o texto que o site publica. Uma banda sem texto seria uma celula vazia numa
   tabela do site.

Sem rede.
"""
import sys, types
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mrm_rules as rules

# ── as funcoes ORIGINAIS, copiadas do fetch_data.py antes desta alteracao ────
def old_cycle(s):
    if s is None: return None
    if s < -0.75: return 9.5
    if s < -0.50: return 8.5
    if s < -0.25: return 7.5
    if s < 0.00:  return 6.5
    if s < 0.50:  return 5.5
    if s < 0.75:  return 4.5
    if s < 1.25:  return 3.5
    if s < 2.00:  return 2.5
    return 1.5

def old_liquidity(p):
    if p is None: return None
    if p > 95: return 9.5
    if p > 90: return 8.5
    if p > 80: return 7.5
    if p > 65: return 6.5
    if p > 50: return 5.5
    if p > 35: return 4.0
    if p > 20: return 3.0
    return 1.5

def old_premium(e):
    if e is None: return None
    if e < 0.00: return 10.0
    if e < 0.50: return 9.0
    if e < 0.80: return 8.0
    if e < 1.20: return 7.0
    if e < 2.00: return 5.5
    if e < 3.00: return 4.0
    if e < 4.00: return 2.5
    return 1.5

def old_solvency(n):
    if n is None: return None
    if n > 5.00: return 9.5
    if n > 4.00: return 8.0
    if n > 3.00: return 6.5
    if n > 2.50: return 5.5
    if n > 2.00: return 4.5
    if n > 1.50: return 3.5
    if n > 1.00: return 2.5
    return 1.5

def old_debt(d):
    if d is None: return None
    if d > 13.00: return 9.5
    if d > 12.50: return 8.5
    if d > 12.00: return 7.5
    if d > 11.50: return 6.5
    if d > 11.00: return 5.5
    if d > 10.50: return 4.5
    if d > 10.00: return 3.5
    return 2.0

def old_global(scores):
    """A conta de referencia, escrita a mao, contra a qual global_score e comparada.

    O chao de evidencia faz parte da regra e nao e um detalhe de implementacao:
    com dois pilares vivos o composto e um numero construido sobre 45% da
    evidencia, e duas leituras seguidas <= 4,0 desse composto rodavam a carteira
    inteira para o mapa Resilient. Todo o resto do sistema mantem posicoes quando
    os dados degradam; este era o unico sitio onde a degradacao produzia a accao
    maxima, e na direccao risk-on."""
    weights = {"cycle":0.20,"liquidity":0.20,"premium":0.25,"solvency":0.15,"debt":0.20}
    nd = sorted(k for k in weights if scores.get(k) is None)
    ok = {k: w for k, w in weights.items() if scores.get(k) is not None}
    if not ok: return None, nd
    total = sum(ok.values())
    if total < 0.50 - 1e-9:          # menos de metade do peso: nao ha composto
        return None, nd
    return round(sum(scores[k]*w/total for k,w in ok.items()), 2), nd

def old_status(score):
    if score is None: return "nd"
    if score <= 4.0: return "stable"
    if score <= 6.0: return "caution"
    if score <= 7.5: return "warning"
    return "critical"

OLD = {"cycle": old_cycle, "liquidity": old_liquidity, "premium": old_premium,
       "solvency": old_solvency, "debt": old_debt}
RANGE = {"cycle": (-400, 400), "liquidity": (0, 10000), "premium": (-300, 800),
         "solvency": (0, 800), "debt": (800, 1500)}
STEP  = {"cycle": 100.0, "liquidity": 100.0, "premium": 100.0, "solvency": 100.0, "debt": 100.0}

fails = 0; n = 0
for pid, fn in OLD.items():
    lo, hi = RANGE[pid]; d = STEP[pid]
    for i in range(lo, hi + 1):
        x = i / d
        a, b = fn(x), rules.score_pillar(pid, x)
        n += 1
        if a != b:
            fails += 1
            if fails <= 10: print(f"DIVERGE {pid} x={x}: antigo={a} novo={b}")
    # e exactamente em cima de cada limiar declarado
    for band in rules.PILLAR_SCORING[pid]["bands"]:
        for edge in (band["lo"], band["hi"]):
            if edge is None: continue
            for x in (edge, edge - 1e-9, edge + 1e-9):
                a, b = fn(x), rules.score_pillar(pid, x)
                n += 1
                if a != b:
                    fails += 1
                    print(f"DIVERGE-LIMIAR {pid} x={x!r}: antigo={a} novo={b}")
    assert rules.score_pillar(pid, None) is None, f"{pid}: None tem de dar n/d"
    n += 1

# ── o chao de evidencia do composto ─────────────────────────────────────────
# A fronteira cai limpa entre dois e tres pilares: os dois mais pesados somam
# 0,45 e os tres mais leves somam 0,55.
import itertools as _it
_CHEIO = {"cycle": 5.5, "liquidity": 9.5, "premium": 10.0, "solvency": 2.5, "debt": 5.5}
for _k in range(6):
    for _falta in _it.combinations(_CHEIO, _k):
        _sc = {p: (None if p in _falta else v) for p, v in _CHEIO.items()}
        _vivos = 5 - _k
        _tem = rules.global_score(_sc)[0] is not None
        assert _tem == (_vivos >= 3), (
            f"com {_vivos} pilares vivos ({sorted(set(_CHEIO) - set(_falta))}) "
            f"o composto {'devia' if _vivos >= 3 else 'nao devia'} existir")
        n += 1
assert rules.MIN_PILLAR_WEIGHT == 0.50, "o chao e metade do peso total"
n += 1

# ── global_score e pillar_status ────────────────────────────────────────────
import random
random.seed(7)
for _ in range(20000):
    sc = {pid: (None if random.random() < 0.2 else round(random.uniform(1, 10), 1))
          for pid in OLD}
    a, b = old_global(sc), rules.global_score(sc)
    n += 1
    if a != b:
        fails += 1; print(f"DIVERGE global {sc}: {a} != {b}")
for i in range(0, 1101):
    s = i / 100
    n += 1
    if old_status(s) != rules.pillar_status(s):
        fails += 1; print(f"DIVERGE status {s}")
assert rules.pillar_status(None) == "nd"; n += 1

# ── coerencia estrutural das bandas ─────────────────────────────────────────
for pid, spec in rules.PILLAR_SCORING.items():
    bands = spec["bands"]
    assert bands[0]["lo"] is None, f"{pid}: primeira banda tem de ser aberta em baixo"
    assert bands[-1]["hi"] is None, f"{pid}: ultima banda tem de ser aberta em cima"
    for prev, cur in zip(bands, bands[1:]):
        assert prev["hi"] == cur["lo"], f"{pid}: buraco entre {prev['hi']} e {cur['lo']}"
    scores = [b["score"] for b in bands]
    mono = scores == sorted(scores, reverse=True) if spec["worseWhen"] == "lower" \
           else scores == sorted(scores)
    assert mono, f"{pid}: o score tem de ser monotono na direccao do risco"
    for b in bands:
        assert 1.0 <= b["score"] <= 10.0
        assert b["label"] and b["context"] and b["reading"], f"{pid}: banda sem texto"
    assert spec["edges"] in ("hi_exclusive", "lo_exclusive")
    assert spec["worseWhen"] in ("lower", "higher")
    n += 1
assert round(sum(rules.PILLAR_WEIGHTS.values()), 6) == 1.0, "os pesos tem de somar 1"
n += 1

# as_dict continua serializavel e leva as bandas
import json
d = rules.as_dict()
json.dumps(d)
assert d["pillarScoring"]["premium"]["bands"][0]["score"] == 10.0
d["pillarScoring"]["premium"]["bands"][0]["score"] = 0
assert rules.PILLAR_SCORING["premium"]["bands"][0]["score"] == 10.0, "as_dict tem de devolver copias"
n += 2

assert fails == 0, f"{fails} divergencias entre as bandas e o scoring publicado"
print(f"TODOS OS {n} TESTES PASSARAM")
