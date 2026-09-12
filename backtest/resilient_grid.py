"""Grelha do ramo Resilient: limiar x confirmacao de N leituras consecutivas.

Nao serve para escolher o limiar — serve para saber o que acontece se ele subir.
A conclusao esta no README: nenhuma variante melhora o Sharpe e todas pioram a
quebra maxima, porque o mapa Resilient carrega 75% em activos de risco e o score
mais baixo e o mercado mais complacente.

    python3 backtest/resilient_grid.py
"""
import io, contextlib, importlib.util, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # mrm_rules.py, seja de onde for chamado
spec = importlib.util.spec_from_file_location("fb", HERE / "final_backtest.py")
bt = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(bt)
import mrm_rules as rules

MESES = bt.MONTHS

def corre(limiar, histerese):
    """(stats, ledger, meses em Resilient, episódios, log)."""
    rules.RESILIENT_MAX = limiar
    orig = rules.classify_regime
    estado = {"acima": 0}
    def wrap(score, stress_active=None, previous_regime="Turbulence"):
        want = orig(score, stress_active, previous_regime)
        if histerese and previous_regime == "Resilient" and want == "Turbulence":
            estado["acima"] = estado["acima"] + 1 if (score is not None and score > limiar) else 0
            if estado["acima"] < histerese:
                return "Resilient"
        else:
            estado["acima"] = 0
        return want
    rules.classify_regime = wrap
    try:
        ser, log, led = bt.run_v2(model="open_close")
    finally:
        rules.classify_regime = orig
        estado["acima"] = 0

    # meses em Resilient, lidos das transições
    mr, ep, dentro, i0 = 0, 0, False, None
    for m, txt in log:
        destino = txt.split("->")[-1].split("[")[0].strip()
        entra = destino.startswith("Resilient")
        if entra and not dentro:
            dentro, i0, ep = True, MESES.index(m), ep + 1
        elif dentro and not entra:
            mr += MESES.index(m) - i0; dentro = False
    if dentro:
        mr += len(MESES) - i0
    return bt.stats(ser), led, mr, ep, log

print(f"{'limiar':>7} {'hist':>5} {'CAGR':>7} {'Vol':>6} {'Sharpe':>7} {'Sortino':>8} "
      f"{'MaxDD':>7} {'(mes)':>9} {'rebal':>6} {'mesesR':>7} {'epis':>5} {'Final':>10}")
resultados = {}
for lim in (4.0, 4.4, 4.6, 5.0, 5.5):
    for hist in (0, 2, 3):
        if lim == 4.0 and hist: continue
        s, led, mr, ep, log = corre(lim, hist)
        resultados[(lim, hist)] = (s, mr, ep, log, led)
        print(f"{lim:>7.1f} {hist or '-':>5} {s['cagr']*100:>6.2f}% {s['vol']*100:>5.1f}% "
              f"{s['sharpe']:>7.3f} {s['sortino']:>8.3f} {s['mdd']*100:>6.1f}% {s['mdd_m']:>9} "
              f"{led['n_rebal']:>6} {mr:>7} {ep:>5} {s['final']:>10,.0f}")

print("\n── entradas em Resilient, por limiar (sem histerese) ──")
for lim in (4.4, 4.6, 5.0, 5.5):
    log = resultados[(lim, 0)][3]
    ent = [m for m, t in log if t.split("->")[-1].split("[")[0].strip().startswith("Resilient")]
    print(f"  {lim}: {len(ent)} entradas -> {ent}")
