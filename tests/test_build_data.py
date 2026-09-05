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
