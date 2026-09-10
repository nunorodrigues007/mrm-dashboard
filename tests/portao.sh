#!/usr/bin/env bash
#
# O PORTÃO: toda a suite, e uma falha PÁRA quem o chama.
#
# Isto era um bloco `run:` escrito dentro do YAML, e a regra que o guardava era
# um teste sobre o TEXTO desse bloco: uma lista de literais proibidos (`|| true`,
# `set +e`, ...). Uma lista só apanha o que quem a escreveu já tinha imaginado,
# e apanhava mal nos dois sentidos — deixava passar `set +o errexit`,
# `if python "$t"; then :; fi`, correr os testes em `&` com um `wait` que
# devolve 0, ou um `echo` do glob ao lado de um único ficheiro; e recusava a
# forma CORRECTA de tornar uma falha legível no painel do Actions.
#
# Um teste sobre texto nunca consegue afirmar "uma suite vermelha pára o job".
# Aqui o portão é um ficheiro versionado que a própria suite CORRE: o
# `tests/test_workflows.py` injecta um teste que falha e exige que este script
# saia com código diferente de zero. A propriedade passa a ser executada, não
# lida.
#
# `-e` para abortar à primeira falha, `-u` para uma variável por definir ser um
# erro, e `-o pipefail` para uma falha no meio de um pipe não ser engolida pelo
# último comando.
set -euo pipefail

cd "$(dirname "$0")/.."

falhou=0

for t in tests/test_*.py; do
  echo "── $t"
  # `|| falhou=1` NÃO engole a falha: acumula-a. Sem isto, o primeiro ficheiro
  # vermelho esconde os outros e cada correcção custa uma corrida inteira do
  # workflow para descobrir o próximo. A saída continua a ser vermelha no fim.
  python "$t" || falhou=1
done

echo "── tests/test_frontend.js"
node tests/test_frontend.js || falhou=1

if [ "$falhou" -ne 0 ]; then
  echo "::error::a suite falhou — o portao esta fechado"
  exit 1
fi

echo "── portao aberto: toda a suite verde"
