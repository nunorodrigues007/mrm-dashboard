"""
O scores.json do backtest tem de ser verificavel, nao um artefacto de confianca.

Este ficheiro era um JSON commitado sem gerador no repositorio, e a afirmacao
"nao ha hindsight na reconstrucao" nao era testavel por ninguem. Nao e possivel
regerar os inputs em bruto sem rede — mas e possivel, e obrigatorio, provar que
tudo o que deles deriva sai das REGRAS DE PRODUCAO e nao de outra coisa.

O que fica preso aqui:
  1. Os cinco scores de pilar de cada mes saem de rules.score_pillar() aplicado
     aos valores em bruto do proprio ficheiro.
  2. O composto sai de rules.global_score() aplicado a esses pilares.
  3. O rotulo de regime sai de rules.score_band() aplicado ao composto.
  4. O ERP e mesmo E/P menos o 10Y.
  5. Os atrasos de publicacao declarados sao reais: a delinquencia de cada mes
     e uma observacao trimestral com pelo menos cinco meses.
  6. A serie e mensal, continua, sem buracos nem duplicados.

Se alguem mexer numa banda de scoring, isto falha — que e o mesmo mecanismo que
protege os numeros publicados no site.

Sem rede.
"""
import csv, json, sys, types
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mrm_rules as rules

SC = json.load(open(ROOT / "backtest" / "data" / "scores.json"))
ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def close(got, want, tol, what):
    global ok
    assert abs(got - want) <= tol, f"{what}: esperado {want} +/- {tol}, obtido {got}"
    ok += 1
def true(c, what): eq(bool(c), True, what)

eq(len(SC), 260, "260 meses de 2005-01 a 2026-08")

# ── 6. serie mensal, continua, ordenada ─────────────────────────────────────
meses = [r["month"] for r in SC]
eq(len(set(meses)), len(meses), "sem meses duplicados")
eq(meses, sorted(meses), "por ordem cronologica")
def prox(m):
    y, mo = int(m[:4]), int(m[5:7]) + 1
    return f"{y + (mo - 1) // 12:04d}-{(mo - 1) % 12 + 1:02d}"
for a, b in zip(meses, meses[1:]):
    assert prox(a) == b, f"buraco na serie entre {a} e {b}"
ok += 1

# ── 1-3. os derivados saem das regras de producao ───────────────────────────
CAMPO = {"cycle": "spread", "liquidity": "buffett_pct",
         "solvency": "npl", "debt": "dsr"}

def recomputa(r, sufixo):
    """Pilares e composto recalculados dos valores em bruto, com as regras."""
    erp = r[f"erp_{sufixo}"]
    p = {}
    for pid in rules.PILLAR_ORDER:
        bruto = erp if pid == "premium" else r[CAMPO[pid]]
        p[pid] = rules.score_pillar(pid, bruto)
    score, _ = rules.global_score(p)
    return p, score

divergencias = []
for r in SC:
    for sufixo in ("real", "frozen"):
        pilares, score = recomputa(r, sufixo)
        guardados = r[f"pillars_{sufixo}"]
        for pid, valor in pilares.items():
            if valor is None:
                # um pilar em n/d nao pode aparecer guardado com numero
                if guardados.get(pid) is not None:
                    divergencias.append(f"{r['month']}/{sufixo}/{pid}: n/d mas guardado {guardados[pid]}")
                continue
            if abs(guardados.get(pid, -999) - valor) > 1e-9:
                divergencias.append(f"{r['month']}/{sufixo}/{pid}: guardado {guardados.get(pid)}, regras dao {valor}")
        if score is not None and abs(r[f"score_{sufixo}"] - score) > 0.005:
            divergencias.append(f"{r['month']}/{sufixo}: composto guardado {r[f'score_{sufixo}']}, regras dao {score}")
        rotulo = rules.score_band(r[f"score_{sufixo}"])
        if r[f"regime_{sufixo}"] != rotulo:
            divergencias.append(f"{r['month']}/{sufixo}: regime guardado {r[f'regime_{sufixo}']}, regras dao {rotulo}")

assert not divergencias, ("o scores.json nao sai das regras de producao:\n  "
                          + "\n  ".join(divergencias[:15])
                          + (f"\n  ... e mais {len(divergencias)-15}" if len(divergencias) > 15 else ""))
ok += 1
print(f"  {len(SC)*2} meses-versao recalculados pilar a pilar a partir dos valores em bruto")

# ── 4. o ERP e E/P menos o 10Y ──────────────────────────────────────────────
for r in SC:
    if r["ep_real"] is not None and r["y10"] is not None:
        close(r["erp_real"], round(r["ep_real"] - r["y10"], 2), 0.02,
              f"{r['month']}: ERP real = E/P - 10Y")
        ok -= 1   # nao inflacionar a contagem com 260 asserçoes iguais
ok += 1

# ── 5. os atrasos de publicacao sao reais ───────────────────────────────────
# A verificacao certa nao e procurar o valor na serie (valores de delinquencia
# repetem-se, e uma coincidencia daria um atraso falso). E: para cada mes M, o
# valor usado tem de ser o da ULTIMA observacao trimestral cuja data mais o
# atraso declarado ja tenha passado em M.
LAG_NPL_MESES = 5
q = {row["date"]: float(row["dralacbn"])
     for row in csv.DictReader(open(ROOT / "backtest" / "data" / "fred_quarterly.csv"))
     if row["dralacbn"]}
qk = sorted(q)

def meses_entre(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])

def esperado_npl(mes):
    """O que um observador nesse mes poderia mesmo saber."""
    visiveis = [d for d in qk if meses_entre(d[:7], mes) >= LAG_NPL_MESES]
    return q[visiveis[-1]] if visiveis else None

erradas, conferidas = [], 0
for r in SC:
    if r["npl"] is None:
        continue
    esperado = esperado_npl(r["month"])
    if esperado is None:
        continue
    conferidas += 1
    if abs(r["npl"] - esperado) > 1e-9:
        erradas.append(f"{r['month']}: usa {r['npl']}, so podia saber {esperado}")

assert not erradas, ("ha meses a usar delinquencia que ainda nao estava publicada:\n  "
                     + "\n  ".join(erradas[:10]))
ok += 1
true(conferidas > 200, f"a delinquencia foi conferida na maioria dos meses ({conferidas})")
# (Nao ha aqui uma assercao sobre o "lag minimo observado": identificar a
# observacao usada por VALOR e ambiguo — valores de delinquencia repetem-se — e
# qualquer forma de o fazer enviesa o resultado. A verificacao que vale e a de
# `erradas` acima, que compara com o que era publicamente conhecido, e a prova
# de mutacao abaixo, que confirma que ela apanha um dado adiantado.)

# ── a verificacao acima tem de conseguir falhar ─────────────────────────────
# Um teste que nao pode falhar nao e um teste. Aqui muta-se mesmo o dado: uma
# copia do ficheiro passa a usar, num mes, a delinquencia de um trimestre que so
# seria publicado meses depois. A verificacao tem de o apanhar.
def verifica(registos):
    """A mesma verificacao, aplicada a um conjunto qualquer de registos."""
    maus = []
    for r in registos:
        if r["npl"] is None:
            continue
        esp = esperado_npl(r["month"])
        if esp is not None and abs(r["npl"] - esp) > 1e-9:
            maus.append(r["month"])
    return maus

eq(verifica(SC), [], "os dados reais passam a verificacao")

import copy as _copy
mutado = _copy.deepcopy(SC)
alvo = next(r for r in mutado
            if r["month"] >= "2010-01" and r["npl"] is not None
            and any(0 <= meses_entre(d[:7], r["month"]) < LAG_NPL_MESES
                    and abs(q[d] - esperado_npl(r["month"])) > 1e-9 for d in qk))
adiantado = next(q[d] for d in qk
                 if 0 <= meses_entre(d[:7], alvo["month"]) < LAG_NPL_MESES
                 and abs(q[d] - esperado_npl(alvo["month"])) > 1e-9)
antes = alvo["npl"]
alvo["npl"] = adiantado
true(verifica(mutado) == [alvo["month"]],
     f"a verificacao apanha um mes a usar dados adiantados "
     f"({alvo['month']}: {antes} -> {adiantado})")
alvo["npl"] = antes
eq(verifica(mutado), [], "e volta a passar quando o dado e reposto")

# ── nenhum mes ve o futuro no 10Y ───────────────────────────────────────────
mm = {row["month"]: float(row["y10"])
      for row in csv.DictReader(open(ROOT / "backtest" / "data" / "monthly.csv"))}
iguais = sum(1 for r in SC if r["month"] in mm and abs(mm[r["month"]] - r["y10"]) < 1e-9)
true(iguais > 250, f"o 10Y de cada mes e o do proprio mes, nao o de outro ({iguais}/260)")

print(f"TODOS OS {ok} TESTES PASSARAM")
