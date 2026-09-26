#!/usr/bin/env python3
"""
smoke_precos.py — as cotacoes chegam ao runner? Sem tocar em nada.

Porque existe. A avaria de Setembro de 2026 so era observavel na sexta-feira a
noite, depois de a carteira ja ter sido publicada com precos velhos. Descobrir
que o runner nao fala com a Yahoo NAO tem de custar uma semana de espera nem uma
edicao errada: isto corre a proci, a pedido, e responde a unica pergunta que
interessa — que fonte serve cada instrumento a partir DESTE runner.

Nao escreve ficheiros, nao faz commit, nao envia nada. Le o portfolio.json so
para saber que instrumentos estao detidos.
"""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import update_portfolio as up


def main():
    chave = (os.environ.get("ALPHAVANTAGE_API_KEY") or "").strip()
    print("=" * 66)
    print("TESTE DE FUMO ÀS COTAÇÕES — não escreve nada, não envia nada")
    print("=" * 66)
    # Nunca o VALOR, so se existe e o tamanho: um segredo impresso num log de um
    # repositorio publico deixa de ser um segredo.
    print(f"ALPHAVANTAGE_API_KEY configurada: "
          f"{'SIM (' + str(len(chave)) + ' caracteres)' if chave else 'NAO'}")
    if not chave:
        print("  -> sem ela a cascata corre com a Yahoo sozinha, que e a fonte "
              "que falhou. Criar o segredo em Settings > Secrets and variables "
              "> Actions.")
    print(f"Fontes declaradas, por ordem: "
          f"{', '.join(n for n, _ in up.FONTES_DE_PRECO)}")

    alvo = up.adjust_for_market_holiday(up.get_last_friday())
    print(f"Sexta-feira alvo: {alvo}")

    try:
        cur = json.loads((ROOT / "portfolio.json").read_text(encoding="utf-8"))["current"]
        tickers = sorted(set(cur.get("shares") or {}) | {"SPY"})
    except Exception as e:
        print(f"  (portfolio.json ilegivel: {e} — a usar o universo de Turbulence)")
        tickers = ["BIL", "IEF", "LQD", "PDBC", "SPY", "VNQ"]
    print(f"Instrumentos: {', '.join(tickers)}")
    print("-" * 66)

    prices, price_dates, stale = up.fetch_prices(tickers, alvo)

    print("-" * 66)
    largura = max(len(t) for t in tickers)
    sem_preco, de_recurso = [], []
    for t in tickers:
        p = prices.get(t)
        if p is None:
            sem_preco.append(t)
            print(f"  {t:<{largura}}  SEM COTAÇÃO — nenhuma fonte respondeu")
            continue
        fonte = up.ULTIMAS_FONTES.get(t, "?")
        if fonte != "yahoo":
            de_recurso.append(t)
        print(f"  {t:<{largura}}  {p:>10.4f}  de {price_dates[t]}  via {fonte}")

    print("=" * 66)
    if sem_preco:
        print(f"VERMELHO: {', '.join(sem_preco)} sem cotação nenhuma. Se isto "
              f"acontecesse na sexta, a carteira seria publicada ao último preço "
              f"conhecido e o alerta abriria um issue.")
        return 1
    if de_recurso:
        print(f"AMARELO: tudo valorizado, mas {', '.join(de_recurso)} veio da "
              f"fonte de recurso — a Yahoo continua em baixo neste runner. O "
              f"sistema publicaria CORRECTO na mesma. É a cascata a trabalhar.")
        return 0
    print("VERDE: os seis vieram da Yahoo. O acesso do runner está restabelecido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
