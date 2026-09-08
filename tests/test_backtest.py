"""
Regressão do backtest — os números publicados têm de continuar a sair do código.

O backtest importa mrm_rules.py, por isso qualquer alteração às regras muda estes
valores. É isso que se pretende: se alguém mexer nos pesos, nos limiares ou na
lógica de rebalanceamento, este teste falha e obriga a republicar os números em
vez de os deixar desactualizados no site e na Academy.

Sem rede: lê apenas os ficheiros em backtest/data/.
"""
import importlib.util, io, contextlib, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("fb", ROOT / "backtest" / "final_backtest.py")
fb = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(fb)

ok = 0
def close(got, want, tol, what):
    global ok
    assert abs(got - want) <= tol, f"{what}: esperado {want} ± {tol}, obtido {got:.4f}"
    ok += 1
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1

v2, log = fb.run_v2()
v1, _ = fb.run_v1()
s2, s1 = fb.stats(v2), fb.stats(v1)
d2, d1 = dict(v2), dict(v1)

eq(len(fb.MONTHS), 235, "periodo de 235 meses (2007-02 a 2026-08)")

# ── os numeros publicados na Academia e no relatorio ────────────────────────
close(s1["cagr"] * 100, 6.93, 0.05, "v1 CAGR")
close(s1["mdd"] * 100, -23.9, 0.1, "v1 quebra maxima")
close(s2["cagr"] * 100, 6.60, 0.05, "v2 CAGR")
close(s2["mdd"] * 100, -16.4, 0.1, "v2 quebra maxima")
close(s2["sortino"], 0.911, 0.01, "v2 Sortino")
close(s2["sharpe"], 0.681, 0.01, "v2 Sharpe")
close((s1["cagr"] - s2["cagr"]) * 100, 0.33, 0.05, "custo do seguro em pp de CAGR")

y = lambda d, a, b: (d[b] / d[a] - 1) * 100
close(y(d1, "2007-12", "2008-12"), -13.8, 0.2, "2008 no sistema anterior")
close(y(d2, "2007-12", "2008-12"), 1.5, 0.2, "2008 no sistema actual")
close(y(d2, "2021-12", "2022-12"), -12.8, 0.2, "2022 — o medidor B fica calado de proposito")

# ── comportamento do medidor, nao so o retorno ──────────────────────────────
fired = [m for m in fb.MONTHS if fb.gauge_b(m)]
eq(len(fired), 48, "48 meses com o medidor B ligado em 235")
for ano in ("2008", "2020", "2024"):
    assert any(m.startswith(ano) for m in fired), f"o medidor tinha de disparar em {ano}"
    ok += 1
for ano in ("2011", "2018", "2022"):
    assert not any(m.startswith(ano) for m in fired), f"o medidor nao pode disparar em {ano}"
    ok += 1
assert any(m.startswith("2008-0") and m <= "2008-05" for m in fired), "2008: disparo antes de Lehman"
ok += 1

# ── entradas e saidas ────────────────────────────────────────────────────────
reasons = [t.split("[")[1].rstrip("]") for _, t in log]
eq(reasons.count("stress_on"), 3, "tres entradas em stress (2008, 2020, 2024)")
eq(reasons.count("stress_off"), 3, "tres saidas de stress")
eq(reasons.count("resilient_off"), 1, "uma saida de Resilient — a que faltava ao motor")
assert all(r.startswith("critical_subregime_switch") or r in
           ("stress_on", "stress_off", "resilient_off", "semestral_rebalance")
           or r.startswith("emergency_resilient") for r in reasons), "motivos conhecidos"
ok += 1

# a primeira entrada em Critical e sempre defensiva
first_on = next(t for _, t in log if "stress_on" in t)
assert "Critical_Stress" in first_on, f"entrada fresca defensiva ({first_on})"
ok += 1

print(f"TODOS OS {ok} TESTES PASSARAM")
