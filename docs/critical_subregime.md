# Critical: FTQ vs Stress-without-relief

## Porque existem dois sub-regimes

O mesmo estado de crise pode significar duas coisas opostas para a duração.

Em 2008 e em 2020 o 10Y caiu depressa e sem ambiguidade: o TLT cobriu a queda das
ações e a carteira defensiva funcionou. Em 2022 não houve alívio de taxas nenhum —
o TLT perdeu cerca de 31% ao mesmo tempo que as ações caíam, e uma carteira
"defensiva" com duração longa teria sido pior do que não fazer nada.

Por isso Critical não é um estado só. Divide-se em dois, e qual deles está vivo é
decidido pela direção do 10Y, nunca assumido a partir do score.

| bucket | Critical_FTQ | Critical_Stress |
|---|---|---|
| US_EQUITIES | USMV 15% | USMV 15% |
| US_TREASURIES | TLT 35% | SHY 20% |
| IG_CREDIT | SGOV 15% | SGOV 20% |
| COMMODITIES | GLD 15% | GLD 15% |
| CASH | BIL 15% | BIL 25% |
| ALTERNATIVES | VNQ 5% | VNQ 5% |

## A regra

O 10Y tem de ter caído **pelo menos 10 bp em 3 meses** para confirmar FTQ. Qualquer
outra coisa — subida, lateral, entrada fresca em Critical, ou série indisponível —
cai no lado defensivo.

A porta é deliberadamente assimétrica: o TLT tem de ser reconquistado, não se perde
por dúvida. Custa pouca subida nos episódios reais de FTQ, que foram todos quedas
rápidas e inequívocas, e evita repetir 2022.

**Entrada fresca em Critical vai sempre para Critical_Stress**, mesmo que o 10Y já
esteja a cair. Só na semana seguinte, com o estado já estabelecido, é que o FTQ pode
ser confirmado.

## Fonte

O 10Y vem da FRED (`DGS10`), calculado no `mrm_gauge_b.py` e publicado no `data.json`
em `stressGauge.subregime`. O `update_portfolio.py` lê-o de lá.

Até Set 2026 o `update_portfolio.py` lia o 10Y do yfinance (`^TNX`) numa janela de 28
dias, enquanto o medidor B usava a FRED com janela de 3 meses: duas fontes e duas
janelas para a mesma medida, que podiam discordar em público. Ficou a da FRED, e a
janela ficou em 3 meses porque no backtest 2007-2026 a janela de 1 mês era pior —
2008 fechava a −4,1% em vez de +0,7%, e o sub-regime trocava 16 vezes em vez de 8.

## O que decide entrar em Critical

Não é o score. É o medidor B (`stressGauge.active`) — ver o cabeçalho do
`mrm_gauge_b.py`. O score de cinco pilares é um indicador avançado de fragilidade e
nunca atingiu 8,0 em 2005-2026, nem sequer em 2008, porque numa crise três dos seus
cinco pilares melhoram mecanicamente.

## Histórico das decisões

- **Jul 2026** — divisão de Critical em FTQ e Stress; porta assimétrica de 10 bp com
  entrada fresca sempre defensiva.
- **Set 2026** — Critical passa a ser decidido pelo medidor B em vez do score; o
  vetor de pesos de Critical passa a sobrepor-se às % da newsletter; a saída de
  Critical passa a ser imediata e a devolver as % da newsletter; fonte do 10Y
  unificada na FRED com janela de 3 meses.
