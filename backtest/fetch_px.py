"""Traz as series mensais de precos ajustados que o backtest carrega.

Os ficheiros em `backtest/data/px/` estao commitados de proposito: o backtest
tem de dar o mesmo resultado daqui a um ano sem depender de uma API. Este script
existe para se saber de onde vieram e para os poder refazer.

    python3 -m pip install -U yfinance     # a 0.2.x falha: "Expecting value"
    python3 backtest/fetch_px.py           # todos
    python3 backtest/fetch_px.py QQQ SHY   # so estes

Formato de saida, uma linha por ano (fecho AJUSTADO de fim de mes, dividendos
reinvestidos), com o ano parcial a terminar onde os dados terminam:

    2006 12 36.9703          # ano incompleto: <ano> <mes> <preco>
    2007 37.7499 37.1160 ... # ano completo: <ano> seguido de 12 precos
"""
import sys
from pathlib import Path

INICIO = "2006-12-01"
DESTINO = Path(__file__).resolve().parent / "data" / "px"
TICKERS = ["SPY", "IEF", "LQD", "DBC", "SHV", "VNQ", "TLT", "GLD",
           "QQQ", "SHY", "HYG", "IWO"]


def serie_mensal(ticker):
    """{'AAAA-MM': preco} do fecho ajustado de fim de mes."""
    import yfinance as yf
    df = yf.download(ticker, start=INICIO, interval="1mo",
                     auto_adjust=True, progress=False)
    if df is None or df.empty:
        raise SystemExit(f"{ticker}: o yfinance nao devolveu nada")
    fecho = df["Close"]
    if hasattr(fecho, "columns"):          # MultiIndex quando ha varios tickers
        fecho = fecho.iloc[:, 0]
    out = {}
    for data, valor in fecho.dropna().items():
        out[f"{data.year:04d}-{data.month:02d}"] = round(float(valor), 4)
    return out


def escreve(ticker, px):
    meses = sorted(px)
    linhas = []
    for ano in sorted({m[:4] for m in meses}):
        do_ano = [m for m in meses if m.startswith(ano)]
        # A linha do ano da os precos a partir de Janeiro, um por mes. So serve
        # se o ano comecar em Janeiro e nao tiver buracos — o primeiro ano da
        # serie comeca a meio, e esse vai na forma explicita.
        contiguo = do_ano == [f"{ano}-{i:02d}" for i in range(1, len(do_ano) + 1)]
        if contiguo:
            linhas.append(ano + " " + " ".join(f"{px[m]:.4f}" for m in do_ano))
        else:
            for m in do_ano:                # <ano> <mes> <preco>
                linhas.append(f"{ano} {m[5:]} {px[m]:.4f}")
    alvo = DESTINO / f"{ticker}.txt"
    alvo.write_text("\n".join(linhas) + "\n")
    print(f"  {ticker}: {len(meses)} meses, {meses[0]} a {meses[-1]} -> {alvo.name}")


if __name__ == "__main__":
    pedidos = [t.upper() for t in sys.argv[1:]] or TICKERS
    desconhecidos = [t for t in pedidos if t not in TICKERS]
    if desconhecidos:
        raise SystemExit(f"ticker fora da lista do backtest: {desconhecidos}")
    DESTINO.mkdir(parents=True, exist_ok=True)
    for t in pedidos:
        escreve(t, serie_mensal(t))
