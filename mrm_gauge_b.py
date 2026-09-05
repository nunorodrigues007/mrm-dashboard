"""
mrm_gauge_b.py — Medidor B (Stress) do US MRM
=============================================
O scorecard de cinco pilares (Medidor A) é um indicador AVANÇADO de fragilidade:
mede quanto há para correr mal, com horizonte de 6–18 meses. Numa crise três dos
seus pilares melhoram mecanicamente (curva desinverte, avaliações colapsam, ERP
abre), pelo que o composto não consegue sinalizar stress concorrente — em 2005-2026
nunca atingiu 8,0, nem sequer em 2008.

Este módulo é o Medidor B: rápido, baseado em VARIAÇÕES, horizonte 0–3 meses.
Não entra na média do Medidor A — sobrepõe-se a ele.

Gatilhos (fixados à partida, nenhum ajustado a resultados):
  B1  Regra de Sahm, série real-time do FRED (SAHMREALTIME) >= 0.50.
      Regra publicada por Claudia Sahm, desenhada para detecção de recessão em
      tempo real e robusta a revisões. Não é calibrada por mim.
  B2  Variação a 4 trimestres da delinquência bancária (DRALACBN) no decil 90
      da sua própria história desde 1987  ->  +0.81 pp.

Sub-regime quando activo, seguindo docs/critical_subregime.md:
  10Y a cair mais de 10 bp em 3 meses  ->  FTQ      (duração paga)
  caso contrário                       ->  STRESS   (duração não paga)

Comportamento histórico (1996-2026, dados real-time):
  dispara  2001-06→2002-11 · 2008-04→2010-11 · 2020-04→2021-03 · 2024-07→2024-09
  NÃO dispara em 2011, 2018 nem 2022 — separa recessões de bear markets.
  2024 é um falso positivo conhecido da regra de Sahm.
"""

import os
import requests
from datetime import datetime

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

SAHM_TRIGGER = 0.50          # regra publicada
NPL_ACCEL_TRIGGER = 0.81     # decil 90 da variação 4T de DRALACBN desde 1987
TENY_FTQ_BP = -0.10          # queda mínima do 10Y em 3 meses para confirmar FTQ


def _fetch(series_id, limit, api_key, retries=3):
    """Observações mais recentes de uma série FRED. Devolve [] em caso de falha."""
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": limit,
    }
    for attempt in range(retries):
        try:
            r = requests.get(FRED_BASE, params=params, timeout=30)
            r.raise_for_status()
            obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)]
            # Ordenar aqui em vez de confiar no sort_order da API: se a ordem vier
            # trocada, obs[0]-obs[4] passa a medir a variacao ao contrario e o
            # gatilho inverte-se sem dar erro.
            obs.sort(key=lambda o: o["date"], reverse=True)
            return obs
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [WARN] gauge_b: {series_id} indisponível ({e})")
                return []
    return []


def compute(api_key=None, dgs10_now=None, dgs10_3m_ago=None):
    """
    Devolve o bloco 'stressGauge' para o data.json.

    Protocolo n/d: se uma série falhar, o gatilho correspondente fica None e é
    declarado no output. NUNCA é substituído por um valor neutro inventado.
    Se AMBOS falharem, 'active' fica None e o consumidor deve manter o estado
    anterior em vez de assumir OFF.
    """
    api_key = api_key or os.environ.get("FRED_API_KEY")
    if not api_key:
        raise RuntimeError("FRED_API_KEY não definida")

    # ── B1: regra de Sahm, vintage real-time ──
    sahm_obs = _fetch("SAHMREALTIME", 3, api_key)
    sahm_val = float(sahm_obs[0]["value"]) if sahm_obs else None
    sahm_date = sahm_obs[0]["date"] if sahm_obs else None
    b1 = (sahm_val >= SAHM_TRIGGER) if sahm_val is not None else None

    # ── B2: aceleração da delinquência a 4 trimestres ──
    npl_obs = _fetch("DRALACBN", 6, api_key)
    npl_accel = npl_date = None
    if len(npl_obs) >= 5:
        npl_accel = round(float(npl_obs[0]["value"]) - float(npl_obs[4]["value"]), 2)
        npl_date = npl_obs[0]["date"]
    b2 = (npl_accel >= NPL_ACCEL_TRIGGER) if npl_accel is not None else None

    # ── estado ──
    if b1 is None and b2 is None:
        active, basis = None, "n/d — both series unavailable; retain previous state"
    else:
        active = bool(b1) or bool(b2)
        fired = [n for n, f in (("Sahm", b1), ("ΔNPL", b2)) if f]
        nd = [n for n, f in (("Sahm", b1), ("ΔNPL", b2)) if f is None]
        basis = ("+".join(fired) if fired else "no trigger active")
        if nd:
            basis += f" (n/d: {', '.join(nd)})"

    # ── sub-regime ──
    subregime = None
    if active:
        if dgs10_now is None or dgs10_3m_ago is None:
            subregime = "STRESS"          # default conservador, como no update_portfolio.py
        else:
            subregime = "FTQ" if (dgs10_now - dgs10_3m_ago) <= TENY_FTQ_BP else "STRESS"

    return {
        "active": active,
        "subregime": subregime,
        "basis": basis,
        "label": ("Stress OFF" if active is False else
                  f"Stress ON — {'Flight to Quality' if subregime == 'FTQ' else 'No Relief'}"
                  if active else "Stress n/d"),
        "triggers": {
            "sahmRealtime": {
                "series": "SAHMREALTIME", "value": sahm_val, "asOf": sahm_date,
                "threshold": SAHM_TRIGGER, "fired": b1,
                "note": "Sahm rule, real-time vintage. Known false positive in 2024.",
            },
            "delinquencyAccel": {
                "series": "DRALACBN", "value": npl_accel, "asOf": npl_date,
                "threshold": NPL_ACCEL_TRIGGER, "fired": b2,
                "note": "4-quarter change. Threshold = 90th percentile of its own history since 1987.",
            },
        },
        "computedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2, ensure_ascii=False))
