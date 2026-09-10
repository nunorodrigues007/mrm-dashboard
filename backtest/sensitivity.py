"""Sensibilidade dos limiares — nao para escolher, para saber quao fragil e o resultado."""
import importlib.util, sys, io, contextlib
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # mrm_rules.py, seja de onde for chamado
spec=importlib.util.spec_from_file_location("fb", HERE / "final_backtest.py")
fb=importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(fb)
import mrm_rules as rules

def run(sahm_thr, npl_thr, ftq_bp):
    def gauge_b(m):
        s=fb.sahm.get(fb.madd(m,-1)); n=fb.npl_accel(m)
        if s is None and n is None: return None
        return (s is not None and s>=sahm_thr) or (n is not None and n>=npl_thr)
    def gsub(m):
        a,b=fb.mm.get(m), fb.mm.get(fb.madd(m,-3))
        if a is None or b is None: return None
        return "FTQ" if (a-b)<=ftq_bp else "STRESS"
    m0=fb.MONTHS[0]; regime, subregime="Turbulence", None
    # Sem corretagem, de proposito: esta tabela isola o efeito dos limiares. A
    # corretagem e identica em todas as variantes, por isso so acrescentaria uma
    # constante a todas as linhas.
    cap=fb.INITIAL_CAPITAL
    sh=fb.rebalance(cap, regime, subregime, m0); ser=[(m0,cap)]; low=0; n_on=0
    for m in fb.MONTHS[1:]:
        v=fb.value(sh,m); score=fb.S[m][fb.V2_SCORE_KEY]; stress=gauge_b(m)
        if stress: n_on+=1
        want=rules.classify_regime(score, stress, regime)
        want_sub=None
        if want=="Critical": want_sub,_=rules.subregime_from_gauge(gsub(m), regime=="Critical")
        low=low+1 if (score is not None and score<=rules.RESILIENT_MAX) else 0
        emerg=f"emergency_resilient_{score}" if low>=rules.CONSECUTIVE_WEEKS else None
        want=rules.confirm_regime(want, regime, emerg)
        if want!="Critical": want_sub=None
        reason=rules.decide_rebalance(want, regime, want_sub, subregime, fb.is_semestral(m), emerg)
        if reason:
            regime=want; subregime=want_sub if want=="Critical" else None
            sh=fb.rebalance(v, regime, subregime, m)
        ser.append((m,v))
    s=fb.stats(ser); d=dict(ser)
    y2008=(d['2008-12']/d['2007-12']-1)*100
    y2022=(d['2022-12']/d['2021-12']-1)*100
    return s, y2008, y2022, n_on

base=(0.50,0.81,-0.10)
print(f"{'Sahm':>6}{'ΔNPL':>7}{'FTQ bp':>8}{'CAGR':>8}{'Sortino':>9}{'MaxDD':>9}{'2008':>8}{'2022':>8}{'meses ON':>10}")
rows=[base,(0.40,0.81,-0.10),(0.60,0.81,-0.10),(0.50,0.60,-0.10),(0.50,1.00,-0.10),
      (0.50,0.81,-0.05),(0.50,0.81,-0.20),(0.50,99,-0.10),(99,0.81,-0.10)]
for a,b,c in rows:
    s,y08,y22,n=run(a,b,c)
    tag=" (so ΔNPL)" if a==99 else (" (so Sahm)" if b==99 else (" ← base" if (a,b,c)==base else ""))
    print(f"{a:>6}{b:>7}{c:>8}{s['cagr']*100:7.2f}%{s['sortino']:9.3f}{s['mdd']*100:8.1f}%{y08:7.1f}%{y22:7.1f}%{n:10d}{tag}")
