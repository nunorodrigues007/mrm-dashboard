"""
Teste de integração do fetch_data.py sem tocar na rede.

Substitui fetch_fred() e fetch_liquidity_percentile() por valores gravados
(os que o site servia a 2026-09-04) e verifica que o pipeline completo produz
o mesmo score e o mesmo estado do Medidor B.

Correr:  python tests/test_build_data.py
"""
import json, os, sys, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRED_API_KEY", "test-key-not-used")

import fetch_data, mrm_gauge_b

# ── valores gravados: os que o data.json servia a 2026-09-04 ───────────────────
OBS = {
    "T10Y2Y":  [{"date": "2026-09-03", "value": "0.43"}, {"date": "2026-08-03", "value": "0.22"}] * 10,
    "DGS10":   [{"date": "2026-09-02", "value": "4.79"}] * 40 + [{"date": "2026-06-02", "value": "4.47"}] * 40,
    "M2SL":    [{"date": "2026-07-01", "value": "22500.0"}] + [{"date": "2026-06-01", "value": "22400.0"}] * 11
               + [{"date": "2025-07-01", "value": "21345.0"}] * 3,
    "DRALACBN":[{"date": "2026-04-01", "value": "1.38"}, {"date": "2026-01-01", "value": "1.46"},
                {"date": "2025-10-01", "value": "1.50"}, {"date": "2025-07-01", "value": "1.44"},
                {"date": "2025-04-01", "value": "1.44"}, {"date": "2025-01-01", "value": "1.53"}],
    "TDSP":    [{"date": "2026-01-01", "value": "11.164138"}] * 5,
    "ICSA":    [{"date": "2026-08-29", "value": "206000"}, {"date": "2026-08-22", "value": "204000"},
                {"date": "2026-08-15", "value": "205000"}],
    "UNRATE":  [{"date": "2026-08-01", "value": "4.1"}, {"date": "2026-07-01", "value": "4.1"},
                {"date": "2026-06-01", "value": "4.2"}],
    "SAHMREALTIME": [{"date": "2026-08-01", "value": "-0.07"}, {"date": "2026-07-01", "value": "-0.03"},
                     {"date": "2026-06-01", "value": "0.07"}],
    # Mesmo nivel na data de referencia e hoje: E/P fica igual a referencia, para
    # que as ancoras acima (score 6.97, Premium 10.0) continuem a valer. O efeito
    # do movimento do preco e testado a parte, mais abaixo.
    "SP500":   [{"date": "2026-09-04", "value": "7670.00"},
                {"date": "2026-08-31", "value": "7670.00"},
                {"date": "2026-06-30", "value": "7460.00"}],
}

def fake_fetch_fred(series_id, limit=12, retries=3, backoff=5):
    return OBS.get(series_id, [])[:limit]

def fake_liquidity():
    # valores reproduzidos independentemente a partir das séries Z.1/GDP
    return 2.883, 99.3, "2026-01-01", {"totalEquitiesB": 91857.2, "gdpB": 31865.7,
                                       "historyPoints": 302, "historyStart": "1947-10-01"}

class FakeResp:
    def __init__(self, series_id): self.series_id = series_id
    def raise_for_status(self): pass
    def json(self):
        # A API do FRED com sort_order=desc devolve o mais recente primeiro.
        return {"observations": list(OBS.get(self.series_id, []))}

def fake_requests_get(url, params=None, timeout=None):
    return FakeResp((params or {}).get("series_id"))

def main():
    fetch_data.fetch_fred = fake_fetch_fred
    fetch_data.fetch_liquidity_percentile = fake_liquidity
    mrm_gauge_b.requests.get = fake_requests_get

    tmp = tempfile.mkdtemp()
    real_dir = os.path.dirname(os.path.abspath(fetch_data.__file__))
    shutil.copy(os.path.join(real_dir, "score_history.json"), tmp)
    fetch_data.__file__ = os.path.join(tmp, "fetch_data.py")

    data = fetch_data.build_data()
    fails = []
    def check(cond, msg):
        print(("  OK   " if cond else "  FALHA ") + msg)
        if not cond: fails.append(msg)

    p = {x["id"]: x for x in data["pillars"]}
    print("\n── verificações ──")
    check(data["globalResilienceScore"] == 6.97, f"score composto = 6.97 (obtido {data['globalResilienceScore']})")
    check(p["cycle"]["score"] == 5.5,     f"pilar Cycle = 5.5 ({p['cycle']['score']})")
    check(p["liquidity"]["score"] == 9.5, f"pilar Liquidity = 9.5 ({p['liquidity']['score']})")
    check(p["premium"]["score"] == 10.0,  f"pilar Premium = 10.0 ({p['premium']['score']}) com E/P 3.84")
    check(p["solvency"]["score"] == 2.5,  f"pilar Solvency = 2.5 ({p['solvency']['score']})")
    check(p["debt"]["score"] == 5.5,      f"pilar Debt = 5.5 ({p['debt']['score']})")
    check(data["status"] == "Turbulence", f"status inalterado = Turbulence ({data['status']})")
    check(data["ndPillars"] == [],        f"nenhum pilar em n/d ({data['ndPillars']})")

    sg = data["stressGauge"]
    check(sg["active"] is False,          f"Medidor B desligado ({sg['active']})")
    check(sg["subregime"] is None,        f"sem sub-regime ({sg['subregime']})")
    check(sg["triggers"]["sahmRealtime"]["fired"] is False, "gatilho Sahm nao dispara (-0.07 < 0.50)")
    check(sg["triggers"]["delinquencyAccel"]["value"] == -0.06,
          f"aceleracao da delinquencia = -0.06 pp ({sg['triggers']['delinquencyAccel']['value']})")
    check(sg["triggers"]["delinquencyAccel"]["fired"] is False, "gatilho delinquencia nao dispara")

    hs = data["historicalScores"]
    check(len(hs) == 24, f"sparkline com 24 pontos reais ({len(hs)})")
    check(hs[0]["score"] != hs[-1]["score"], "sparkline deixou de ser plana por construcao")
    check(json.dumps(data) and True, "data.json serializavel")
    check(p["premium"].get("epEstimated") is True, "Premium declarado como estimativa")
    check(p["premium"].get("epAsOf") == "2026-08-31", "Premium com data do E/P")

    print("\n── E/P ancorado nos earnings ──")
    ep = p["premium"]
    anchor = ep.get("epAnchor", {})
    check(ep["epValue"] == fetch_data.SP500_EARNINGS_YIELD,
          f"indice ao nivel da referencia -> E/P igual a referencia ({ep['epValue']})")
    check(anchor.get("indexRef") == 7670.0 and anchor.get("indexNow") == 7670.0,
          f"o data.json declara os dois niveis do indice ({anchor.get('indexRef')}, {anchor.get('indexNow')})")
    check(anchor.get("earningsPerIndexUnit") is not None,
          "o data.json declara os earnings por unidade de indice")
    check("marked to the latest close" in anchor.get("basis", ""),
          f"a base do calculo e declarada ({anchor.get('basis')})")
    check(anchor.get("ageDays") is not None, "a idade da referencia e publicada")

    # o movimento do preco tem de mover o E/P — era isto que faltava ao pilar
    ref, ref_date = fetch_data.SP500_EARNINGS_YIELD, fetch_data.SP500_EARNINGS_YIELD_ASOF
    crash = [{"date": "2026-09-04", "value": "6136.00"}, {"date": ref_date, "value": "7670.00"}]
    rally = [{"date": "2026-09-04", "value": "9204.00"}, {"date": ref_date, "value": "7670.00"}]
    y_crash, _ = fetch_data.earnings_yield_now(ref, ref_date, crash)
    y_rally, _ = fetch_data.earnings_yield_now(ref, ref_date, rally)
    check(y_crash == 4.8, f"queda de 20% do indice -> E/P sobe de {ref} para {y_crash}")
    check(y_rally == 3.2, f"subida de 20% do indice -> E/P desce de {ref} para {y_rally}")
    check(fetch_data.score_premium(round(y_crash - 4.79, 2)) < fetch_data.score_premium(round(ref - 4.79, 2)),
          "o pilar Premium melhora numa queda do mercado, como deve")

    # sem serie do indice nao inventa: devolve a referencia e diz porque
    y_none, d_none = fetch_data.earnings_yield_now(ref, ref_date, [])
    check(y_none == ref and "unavailable" in d_none["basis"],
          "sem serie do indice devolve a referencia com o motivo declarado")
    y_old, d_old = fetch_data.earnings_yield_now(ref, "2000-01-01",
                                                 [{"date": "2026-09-04", "value": "7670.00"}])
    check(y_old == ref and "on or before" in d_old["basis"],
          "referencia anterior a serie disponivel cai no mesmo caminho seguro")

    # alarme duro: a referencia manual nao pode apodrecer em silencio
    age = fetch_data.days_since(fetch_data.SP500_EARNINGS_YIELD_ASOF)
    check(age is not None and age <= fetch_data.EP_MAX_AGE_DAYS,
          f"a referencia do E/P tem {age} dias (limite {fetch_data.EP_MAX_AGE_DAYS}) — "
          f"se falhar, actualizar SP500_EARNINGS_YIELD e SP500_EARNINGS_YIELD_ASOF no fetch_data.py")

    print("\n── regras canonicas exportadas para o front-end ──")
    import mrm_rules
    r = data.get("rules", {})
    check(r.get("etfMap", {}).get("Turbulence", {}).get("US_EQUITIES") == "SPY",
          "data.json leva o mapa de ETF canonico")
    check(r.get("criticalWeights", {}).get("Critical_FTQ", {}).get("US_TREASURIES") == 35.0,
          "data.json leva o vector de pesos de Critical")
    check(r.get("resilientMax") == mrm_rules.RESILIENT_MAX and r.get("criticalMin") == mrm_rules.CRITICAL_MIN,
          "limiares publicados = limiares do motor")
    check(r.get("regimeDecidedBy") == "gaugeB", "o JSON declara quem decide o regime")

    print("\n── deltas dos pilares ──")
    for pid in ("cycle", "liquidity", "premium", "solvency", "debt"):
        check(p[pid].get("metricValue") is not None, f"{pid} publica metricValue para comparacao futura")
    check(all(p[pid]["delta"] == "—" for pid in ("cycle", "premium", "solvency", "debt")),
          "sem publicacao anterior comparavel, os deltas sao travessao e nao numeros inventados")
    check("+0.02" not in json.dumps(data) and "\"+0.3\"" not in json.dumps(data),
          "os deltas escritos a mao desapareceram")

    real_prev = fetch_data.load_previous_metrics
    fetch_data.load_previous_metrics = lambda path="data_prev.json": (
        {"cycle": 0.22, "premium": -0.50, "solvency": 1.46, "debt": 11.0, "liquidity": 280.0},
        {"cycle": 5.0, "premium": 9.5, "solvency": 3.0, "debt": 5.5, "liquidity": 9.0},
        "2026-09-04T18:00:00Z")
    d3 = fetch_data.build_data()
    p3 = {x["id"]: x for x in d3["pillars"]}
    fetch_data.load_previous_metrics = real_prev
    check(p3["cycle"]["delta"] == "+0.21%", f"delta do Cycle real ({p3['cycle']['delta']})")
    check(p3["solvency"]["delta"] == "-0.08 pp", f"delta da Solvency real ({p3['solvency']['delta']})")
    # ERP de -0.95 contra -0.50 na publicacao anterior: o premio comprimiu-se mais
    check(p3["premium"]["delta"] == "-0.45%", f"delta do Premium real ({p3['premium']['delta']})")
    check(d3["meta"]["deltaBasis"] == "vs. 2026-09-04", f"base do delta declarada ({d3['meta']['deltaBasis']})")
    # a direccao segue o score, nao o sinal do numero: o ERP caiu (-0.45) e isso
    # e um agravamento, logo o pilar Premium subiu de 9.5 para 10.0 -> "worse"
    check(p3["premium"]["deltaDirection"] == "worse",
          f"Premium: ERP a cair e agravamento ({p3['premium']['deltaDirection']})")
    check(p3["solvency"]["deltaDirection"] == "better",
          f"Solvency: delinquencia a cair e melhoria ({p3['solvency']['deltaDirection']})")
    check(p3["debt"]["deltaDirection"] == "flat", f"Debt sem mudanca de score ({p3['debt']['deltaDirection']})")
    check(all(p[pid]["deltaDirection"] is None for pid in ("cycle", "premium")),
          "sem base de comparacao a direccao e neutra")

    print("\n── n/d: simular DGS10 indisponivel ──")
    OBS["DGS10"] = []
    d2 = fetch_data.build_data()
    p2 = {x["id"]: x for x in d2["pillars"]}
    check(p2["premium"]["score"] is None, "Premium fica n/d em vez de inventar 6.0")
    check(d2["ndPillars"] == ["premium"], f"n/d declarado no JSON ({d2['ndPillars']})")
    check(d2["globalResilienceScore"] == 5.97,
          f"composto renormalizado sobre 4 pilares = 5.97 (obtido {d2['globalResilienceScore']})")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'TODOS OS TESTES PASSARAM' if not fails else str(len(fails)) + ' TESTE(S) FALHARAM'}")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
