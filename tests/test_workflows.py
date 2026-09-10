"""
Os workflows sao codigo, e as garantias operacionais tem de estar testadas.

Cada assercao aqui corresponde a uma falha que aconteceu ou podia acontecer sem
ninguem dar por ela:

  - O job da newsletter fazia checkout sem `ref`, portanto lia o portfolio.json
    do commit ANTERIOR ao push do job da carteira. Todas as edicoes publicadas
    desde a #5 reportaram o P&L da semana errada.
  - Nenhum job declarava `timeout-minutes`: um pedido pendurado corria ate ao
    limite de seis horas do GitHub.
  - `pip install yfinance` sem versao: o yfinance parte com regularidade, e a
    unica forma de dar por isso era a newsletter nao chegar.
  - Os pushes nao faziam rebase: um push recusado por concorrencia descartava a
    decisao da semana, que so existia no runner.
  - Nao havia alerta nenhum: se um job falhasse, o seguinte era saltado e a
    ausencia de noticias era indistinguivel de "correu bem".
  - Os testes corriam em `push` — depois do bot ja ter feito commit. Nunca
    foram um portao.

Sem rede.
"""
import json
import os
import re
import re as _re_rb
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# O fetch_data recusa importar sem chave — e ficheiro servido publicamente, nao
# pode ter chave embutida. Aqui so se lhe chama uma funcao pura.
os.environ.setdefault("FRED_API_KEY", "test-key-not-used")
WF = ROOT / ".github" / "workflows"
try:
    import yaml
except ImportError:                                   # pragma: no cover
    # NAO se sai verde. Este ficheiro e o unico que verifica que os workflows
    # correm mesmo a suite antes de mexer na carteira, com os tempos e as
    # dependencias certas — era o unico teste que se desarmava sozinho: tirar o
    # pyyaml do requirements.txt deixava a suite verde e desligava todo o
    # contrato dos workflows no runner, que instala do requirements num
    # interpretador limpo. Um portao que se cala quando lhe falta uma
    # dependencia nao e um portao.
    print("::error::pyyaml ausente — o contrato dos workflows NAO foi verificado. "
          "Instalar do requirements.txt.")
    sys.exit(1)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(c, what): eq(bool(c), True, what)

# `*.y*ml`, nao `*.yml`. O GitHub corre as duas extensoes, e todas as regras
# deste ficheiro — o portao, a ordem, os prazos, o alerta — olhavam so para uma
# delas. Um `deploy.yaml` com `run: python send_newsletter.py`, sem portao, sem
# prazo e sem alerta, era INVISIVEL a suite inteira: a linha abaixo parece
# cobrir "um workflow novo", mas so via os `.yml`.
_FICHEIROS_WF = sorted(WF.glob("*.y*ml"))
W = {f.stem: yaml.safe_load(f.read_text(encoding="utf-8")) for f in _FICHEIROS_WF}
# E a varredura tem de encontrar alguma coisa: um glob que nao case com nada
# deixa TODAS as regras deste ficheiro a iterar sobre um dicionario vazio, e a
# suite fica verde por nao olhar.
true(len(W) >= 3,
     f"a varredura dos workflows encontrou mesmo ficheiros ({sorted(W)}) — "
     f"zero aqui desligava todas as regras deste ficheiro de uma so vez")
eq(len(W), len(_FICHEIROS_WF),
   f"e dois workflows nao podem partilhar o mesmo nome sem extensao — o segundo "
   f"apagava o primeiro deste dicionario e ficava sem regras "
   f"({[f.name for f in _FICHEIROS_WF]})")
eq(sorted(W), ["friday-pipeline", "test", "update"], "os tres workflows existem")

# ── todos os jobs tem prazo ─────────────────────────────────────────────────
for nome, doc in W.items():
    for job, cfg in doc["jobs"].items():
        t = cfg.get("timeout-minutes")
        true(isinstance(t, int) and 0 < t <= 60,
             f"{nome}/{job} declara timeout-minutes razoavel (tem {t!r})")

# ── nenhum pip install sem ficheiro de versoes ─────────────────────────────
req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
true("yfinance" in req, "o requirements.txt lista o yfinance")
for linha in req.splitlines():
    linha = linha.split("#")[0].strip()
    if not linha:
        continue
    true(any(op in linha for op in ("==", "~=", ">=", "<")),
         f"a dependencia {linha!r} tem versao declarada")

for nome, doc in W.items():
    for job, cfg in doc["jobs"].items():
        for passo in cfg.get("steps", []):
            run = passo.get("run") or ""
            for l in run.splitlines():
                l = l.strip()
                if l.startswith("pip install") and "requirements.txt" not in l:
                    # pyflakes no workflow de testes e ferramenta, nao dependencia
                    true("pyflakes" in l, f"{nome}/{job}: `{l}` instala sem versoes fixadas")

# ── o job da newsletter le o main, nao o SHA do agendamento ────────────────
fp = W["friday-pipeline"]["jobs"]
for job in ("refresh-data", "update-portfolio", "send-newsletter"):
    passos = fp[job]["steps"]
    checkout = next((s for s in passos if str(s.get("uses", "")).startswith("actions/checkout")), None)
    true(checkout is not None, f"{job} faz checkout")
    eq((checkout.get("with") or {}).get("ref"), "main",
       f"{job} faz checkout de main — sem isto le o commit anterior ao push do job anterior")

# ── a ordem dos jobs e a que garante dados frescos ────────────────────────
eq(fp["update-portfolio"].get("needs"), "refresh-data",
   "a carteira so decide depois de os dados serem gerados nesta corrida")
eq(fp["send-newsletter"].get("needs"), "update-portfolio",
   "a newsletter so sai depois de a carteira decidir")

# ── os testes correm antes de a carteira ser tocada ───────────────────────
passos_cart = fp["update-portfolio"]["steps"]
i_testes = next((i for i, s in enumerate(passos_cart)
                 if "portao.sh" in (s.get("run") or "")), None)
# "update_portfolio.py" tambem aparece em "tests/test_update_portfolio.py":
# procura-se o comando que corre o motor, nao a substring.
i_corre = next((i for i, s in enumerate(passos_cart)
                if any(l.strip() == "python update_portfolio.py"
                       for l in (s.get("run") or "").splitlines())), None)
true(i_testes is not None, "o job da carteira corre testes")
true(i_corre is not None, "e corre o motor")
true(i_testes < i_corre, "e os testes vem ANTES de decidir a carteira")
# O portao tem de correr TODA a suite, nao uma lista escrita a mao — uma lista
# escrita a mao fica sempre para tras, e ficou: o test_gauge_b.py e o
# test_build_data.py, que cobrem o modulo que decide o regime e a frescura das
# series, nunca la entraram. Aceita-se o laco sobre `tests/test_*.py`, ou entao
# a mencao explicita de cada ficheiro que existe no disco.
PORTAO = ROOT / "tests" / "portao.sh"


def _e_portao(passo):
    """O passo E o portao. Um so predicado, usado por todas as regras.

    Havia dois: `"portao.sh" in run` numas regras e `"tests/test_" in run`
    noutras — a segunda era a forma de antes de o portao ser um script, e ja nao
    correspondia a passo nenhum. Duas escritas da mesma ideia divergem sempre, e
    a que diverge e a que ninguem esta a olhar.
    """
    # IGUALDADE, nao substring: `echo portao.sh` continha a substring, e o bloco
    # de shell do commit — que menciona o script dentro do laco de recuperacao —
    # tambem. Um passo E o portao quando corre o portao e mais nada.
    return (passo.get("run") or "").strip() == "bash tests/portao.sh"


# ── O que e um ACTO sobre o mundo real ────────────────────────────────────
#
# Correr o motor decide uma carteira de dinheiro real; correr o gerador ENVIA a
# edicao a subscritores reais; um `git push` publica em usmrm.net. Sao actos, e
# nenhum pode acontecer com a suite por correr.
#
# A definicao NAO e uma lista de nomes de comandos escrita a mao — uma lista
# escrita a mao fica sempre para tras, e foi assim que o portao ficou sem metade
# da suite. E uma propriedade da forma do comando: qualquer `python X.py` em que
# o X.py nao viva em `tests/`, mais qualquer `git push`. Um `send_sms.py` novo
# amanha e um acto no dia em que alguem o poe num workflow, sem ninguem ter de
# se lembrar de o acrescentar aqui.
_ACTO_PY = _re_rb.compile(r"^(?:python3?|py)\s+(?!-)(\S+\.py)\b")


def acto_da_linha(linha):
    """O acto que esta linha de shell executa, ou None."""
    _l = linha.strip()
    if not _l or _l.startswith("#"):
        return None
    if _re_rb.search(r"\bgit\s+push\b", _l):
        return "git push"
    _m = _ACTO_PY.match(_l)
    if _m and not _m.group(1).startswith("tests/"):
        return _m.group(1)
    return None


def actos_do_passo(passo):
    return [_a for _a in (acto_da_linha(_l)
                          for _l in (passo.get("run") or "").splitlines())
            if _a]


# ── A REGRA, com a polaridade ao contrario ────────────────────────────────
#
# `acto_da_linha` classifica ACTOS por um padrao sobre o texto de uma linha de
# shell — e um padrao sobre texto so ve o que quem o escreveu imaginou, que e
# exactamente a lista de literais que a ronda 46 tirou do portao, um nivel acima.
# `python -u send_newsletter.py` (o idioma canonico de CI, logs sem buffer) nao
# casava; `cd x && python motor.py` nao casava; um `uses:` local nao era sequer
# olhado; e como a isencao do laco universal usava o MESMO detector, um job que
# ele nao visse desaparecia de TODAS as regras. Tres mutacoes assim sobreviveram
# a suite inteira: na sexta, a edicao era gerada, ENVIADA e a carteira decidida
# com a suite por correr e o painel todo verde.
#
# A polaridade certa: nao se enumera o que AGE — enumera-se o pouco que pode
# correr ANTES do portao, e tudo o resto e acto por omissao. E o "pouco" e mesmo
# pouco: instalar dependencias (sem isso o portao nao corre) e preparar o
# runner. Um `python -u`, um `make`, um `./deploy.sh`, um `uses:` local ou um
# comando que ainda ninguem escreveu entram como acto no dia em que sao
# escritos, sem ninguem se lembrar de nada.
_USES_PREPARACAO = ("actions/checkout@", "actions/setup-python@",
                    "actions/setup-node@", "actions/cache@")
_INSTALACAO = _re_rb.compile(
    r"^(?:pip3?\s+install\b|npm\s+(?:ci|install)\b|python3?\s+-m\s+pip\s+install\b)")


def _pode_preceder_o_portao(passo):
    """Este passo pode correr ANTES da suite? So a preparacao do runner pode."""
    _u = (passo.get("uses") or "").strip()
    if _u:
        return any(_u.startswith(_p) for _p in _USES_PREPARACAO)
    _corrida = passo.get("run") or ""
    _linhas = [_l.strip() for _l in _corrida.splitlines()
               if _l.strip() and not _l.strip().startswith("#")]
    if not _linhas:
        return False
    # Um bloco de instalacao pode ter mais do que uma linha, mas TODAS tem de
    # ser instalacao: `pip install x && python motor.py` nao e instalacao.
    return all(_INSTALACAO.match(_l) and "&&" not in _l and ";" not in _l
               for _l in _linhas)


# O que um job de RELATORIO pode correr. Enumera-se o pouco que conta uma
# falha, nao o muito que age — a mesma inversao de polaridade que o portao
# levou, aplicada ao ramo irmao. A defesa de um job isento era `actos_do_passo`,
# e essa herda a cegueira do detector: `python -u send_newsletter.py` num job
# `if: failure()` — que corre precisamente quando a cadeia esta vermelha e o
# portao nunca correu — REENVIAVA a edicao a subscritores reais, com a suite
# inteira verde. Duas redes a taparem-se uma a outra, nenhuma armada sozinha.
_USES_RELATORIO = ("actions/github-script@",)
_RUN_RELATORIO = _re_rb.compile(r"^(?:echo|curl|printf|:)\b")


def _comandos_do_run(corpo):
    """As linhas de um `run:`, com as continuacoes `\\` ja juntas.

    Sem juntar, a segunda linha de um `curl` partido em tres nao comeca por
    `curl` e qualquer regra por linha a recusa — ou, pior, uma regra escrita ao
    contrario aceitava-a por nao a reconhecer.
    """
    _juntas, _acc = [], ""
    for _l in (corpo or "").splitlines():
        _s = _l.strip()
        if not _s or _s.startswith("#"):
            continue
        _acc += _s[:-1] + " " if _s.endswith("\\") else _s
        if not _s.endswith("\\"):
            _juntas.append(_acc)
            _acc = ""
    if _acc:
        _juntas.append(_acc)
    return _juntas


def _passo_e_de_relatorio(passo):
    _u = (passo.get("uses") or "").strip()
    if _u:
        return any(_u.startswith(_p) for _p in _USES_RELATORIO)
    _linhas = _comandos_do_run(passo.get("run"))
    if not _linhas:
        return False
    return all(_RUN_RELATORIO.match(_l) for _l in _linhas)


def _e_job_de_relatorio(job):
    """Um job que existe para CONTAR uma falha corre com a cadeia vermelha.

    E a unica isencao, e e estreita de proposito: `if: failure()` exacto (nao
    `always()`, que corre tambem no caminho de sucesso), `needs:` de alguem — um
    job de alerta que nao depende de nada nao esta a relatar coisa nenhuma — e
    todos os passos a serem passos de relatorio.
    """
    if str(job.get("if") or "").strip() != "failure()" or not job.get("needs"):
        return False
    return all(_passo_e_de_relatorio(_s) for _s in (job.get("steps") or []))


def so_a_instalacao_precede_o_portao(job, onde):
    """Todo o job tem portao, e antes dele so corre a preparacao do runner."""
    if _e_job_de_relatorio(job):
        # E um job de relatorio nao pode AGIR: a rede de seguranca do detector
        # de actos fica aqui, onde e um segundo par de olhos e nao a regra.
        for _s in (job.get("steps") or []):
            _a = actos_do_passo(_s)
            true(not _a,
                 f"o job de relatorio {onde} corre {_a} — um job que corre com a "
                 f"cadeia vermelha nao pode agir sobre o mundo real")
        return
    passos = job.get("steps") or []
    _i_portao = next((_i for _i, _s in enumerate(passos) if _e_portao(_s)), None)
    true(_i_portao is not None,
         f"o job {onde} nao corre o portao: todo o job que nao seja um relatorio "
         f"de falha (`if: failure()` com `needs:`) tem de correr a suite antes de "
         f"fazer o que quer que seja")
    for _i, _s in enumerate(passos[:_i_portao]):
        true(_pode_preceder_o_portao(_s),
             f"o passo {_i} de {onde} ({_s.get('name') or _s.get('uses') or (_s.get('run') or '')[:60]!r}) "
             f"corre ANTES do portao e nao e preparacao do runner — so instalar "
             f"dependencias e preparar o runner pode preceder a suite")


def portao_antes_dos_actos(job, onde):
    """Se o job AGE, o portao corre antes — em qualquer job de qualquer workflow.

    A ordem estava afirmada UMA vez, por nome, para o `update-portfolio`. O job
    que ENVIA tinha as duas regras "tem portao" e "tem o passo do envio", mas
    nunca "por esta ordem": trocar os dois passos do `send-newsletter` deixava a
    suite inteira verde, e nessa sexta a edicao era gerada, publicada e enviada
    a subscritores reais, e so entao a suite corria. E um job NOVO que corresse o
    motor sem portao nenhum passava, porque nenhuma regra falava dele.

    Aqui a regra e sobre JOBS, nao sobre nomes: para todo o job de todo o
    workflow, se algum passo corre um acto, tem de existir um passo do portao com
    indice estritamente inferior ao do PRIMEIRO acto.
    """
    passos = job.get("steps") or []
    _actos = [(_i, _a) for _i, _s in enumerate(passos) for _a in actos_do_passo(_s)]
    if not _actos:
        return                       # um job que nao age nao precisa de portao
    _i_acto, _qual = _actos[0]
    _i_portao = next((_i for _i, _s in enumerate(passos) if _e_portao(_s)), None)
    true(_i_portao is not None,
         f"o job {onde} corre {_qual!r} — um acto sobre o mundo real — e TEM de "
         f"correr o portao antes (actos: {[_a for _, _a in _actos]})")
    true(_i_portao < _i_acto,
         f"o job {onde} corre o portao no passo {_i_portao} e o primeiro acto "
         f"({_qual!r}) no passo {_i_acto}: o portao tem de vir ANTES, senao o "
         f"acto ja aconteceu quando a suite corre")


def portao_cobre_tudo(passos, onde):
    """O passo do portao INVOCA o script do portao, e mais nada.

    Isto era uma procura de texto: bastava a string `tests/test_*.py` aparecer
    em qualquer sitio do `run` para a regra se dar por satisfeita — um portao
    que ITERA sem correr, ou que menciona o glob num `echo` ao lado de um unico
    ficheiro, passava. E o bloco inteiro estava escrito duas vezes no YAML, mais
    uma terceira no `test.yml`: tres copias do mesmo laco, e a que diverge e a
    que ninguem esta a olhar.
    """
    corridas = [(s.get("run") or "").strip() for s in passos]
    true(any(_e_portao(_s) for _s in passos),
         f"o portao de {onde} e um passo que invoca o tests/portao.sh e mais "
         f"nada — qualquer coisa a volta dele e uma maneira de o desarmar "
         f"({corridas})")
    # E nenhum passo do job corre a suite POR FORA do script: uma segunda copia
    # do laco e uma segunda oportunidade de divergir.
    for _c in corridas:
        if "portao.sh" in _c:
            continue
        true("tests/test_" not in _c,
             f"o job {onde} nao tem uma segunda copia do laco da suite ({_c[:120]})")


def job_para_mesmo(job, onde):
    """E o job tambem: a mesma chave uma indentacao acima passava.

    `continue-on-error` ao nivel do JOB e aceite pelo GitHub e desarma o portao
    inteiro — e ainda por cima faz o `alert-on-failure`, que corre
    `if: failure()`, deixar de disparar: a sexta partida volta a ser silenciosa,
    que e a razao de existir daquele job.
    """
    true(not job.get("continue-on-error"),
         f"o job {onde} nao tem continue-on-error: uma suite vermelha tem de o "
         f"fazer falhar, e o alerta depende disso")
    # E a PROPRIEDADE, nao a lista: qualquer `if:` num job que depende do portao
    # quebra a cadeia do `needs:` de alguma maneira, e enumerar as quatro
    # funcoes deixava de fora tudo o resto —
    # `if: ${{ success() || needs.X.result == 'failure' }}`, por exemplo, nao
    # contem nenhuma delas e faz exactamente o mesmo.
    _cond_job = job.get("if")
    eq(_cond_job, None,
       f"o job {onde} nao tem `if:` nenhum — qualquer condicao quebra a cadeia "
       f"do needs, e a lista de funcoes proibidas nunca cobre todas as maneiras "
       f"de a escrever (if: {_cond_job!r})")


def portao_para_mesmo(passos, onde):
    """Correr a suite nao chega: uma suite vermelha tem de PARAR o job.

    A regra era uma lista de literais proibidos sobre o texto do YAML — a forma,
    nao a propriedade — e apanhava mal nos DOIS sentidos: deixava passar
    `set +o errexit`, `if python "$t"; then :; fi`, correr os testes em `&` com
    um `wait` que devolve 0, ou `||true` sem espaco; e recusava a forma CORRECTA
    de tornar a falha legivel no painel do Actions. Agora a propriedade e
    EXECUTADA: o script do portao corre, com um teste que falha injectado, e tem
    de sair vermelho. Isso vive no bloco de ensaio mais abaixo.

    Aqui fica so o que continua a ser sobre o YAML: o passo do portao nao pode
    ter `if:` (com um, na sexta agendada a suite nao corre de todo) e NENHUM
    passo do job pode ter `continue-on-error` nem `if:` — com eles o job acaba
    verde com o motor a falhar, e o `alert-on-failure`, que corre `if:
    failure()`, nao dispara: a sexta partida volta a ser silenciosa.
    """
    _viu_portao = False
    for _s in passos:
        corrida = _s.get("run") or ""
        if "portao.sh" in corrida:
            _viu_portao = True
        eq(_s.get("if"), None,
           f"o passo {_s.get('name')!r} de {onde} nao tem `if:` nenhum — "
           f"qualquer condicao e uma maneira de o saltar ou de o correr com a "
           f"cadeia vermelha (if: {_s.get('if')!r})")
        true(not _s.get("continue-on-error"),
             f"o passo {_s.get('name')!r} de {onde} nao tem continue-on-error: "
             f"com ele o job acaba verde e o alerta nao dispara")
    true(_viu_portao, f"o job {onde} TEM o passo do portao")


def nada_corre_depois_do_portao(passos, onde):
    """Nenhum passo a seguir ao portao pode ignorar uma suite vermelha.

    Verificava-se o `continue-on-error` e as formas `|| true`; ficava de fora o
    `if: always()`, que o GitHub aceita e que faz o passo correr na mesma. Com
    ele no passo que ENVIA, a edicao sai com a suite vermelha — o job acaba
    vermelho e o alerta abre, mas os subscritores ja receberam. A guarda tem de
    ser sobre o que corre DEPOIS, nao so sobre o portao.
    """
    depois = False
    for _s in passos:
        # O portao detectava-se por `"tests/test_" in run` — a forma de ANTES de
        # ele ser um script versionado. O passo real e `bash tests/portao.sh`,
        # que nao contem essa substring: a funcao percorria os passos todos com
        # `depois` sempre a False e NAO INSPECCIONAVA NADA. Estava verde por nao
        # olhar. O predicado tem de ser o mesmo que identifica o portao em todo
        # o resto deste ficheiro, e nao uma segunda escrita da mesma ideia.
        if _e_portao(_s):
            depois = True
            continue
        if not depois:
            continue
        _cond = str(_s.get("if") or "")
        for _sempre in ("always()", "failure()", "cancelled()", "!cancelled()"):
            true(_sempre not in _cond,
                 f"o passo {_s.get('name')!r} de {onde} corre DEPOIS do portao e "
                 f"nao pode ignorar uma suite vermelha (if: {_cond!r})")

# As regras acima sao exercitadas sobre workflows CONSTRUIDOS antes de serem
# aplicadas aos reais: nenhum job do repositorio tem hoje `continue-on-error`
# nem um `if:` — e uma regra que nunca ve um contra-exemplo pode ser desligada
# sem que nada fique vermelho. E foi assim que o `if:` ao nivel do job faltou.
def _recusa(fn, arg, onde, porque):
    _rebentou = False
    try:
        fn(arg, onde)
    except AssertionError:
        _rebentou = True
    true(_rebentou, f"a regra recusa {porque}")

_recusa(job_para_mesmo, {"if": "always()", "steps": []}, "ensaio",
        "um job com if: always()")
_recusa(job_para_mesmo, {"if": "failure()", "steps": []}, "ensaio",
        "um job com if: failure()")
_recusa(job_para_mesmo, {"if": "${{ !cancelled() }}", "steps": []}, "ensaio",
        "um job com if: !cancelled()")
_recusa(job_para_mesmo, {"continue-on-error": True, "steps": []}, "ensaio",
        "um job com continue-on-error")
job_para_mesmo({"steps": []}, "ensaio")            # e aceita um job normal
_recusa(portao_para_mesmo,
        [{"run": "bash tests/portao.sh", "continue-on-error": True}], "ensaio",
        "um portao com continue-on-error no passo")
_recusa(portao_cobre_tudo,
        [{"run": "bash tests/portao.sh || true"}], "ensaio",
        "um portao com qualquer coisa a volta da invocacao")
_recusa(portao_cobre_tudo,
        [{"run": "for t in tests/test_*.py; do echo \"$t\"; done"}], "ensaio",
        "um portao que menciona o glob mas nao invoca o script")
_recusa(portao_cobre_tudo,
        [{"run": "bash tests/portao.sh"},
         {"run": "python tests/test_audit.py"}], "ensaio",
        "uma segunda copia do laco da suite ao lado do script")
# Os ensaios desta regra eram construidos com um passo INVENTADO
# (`python tests/test_x.py`) que fazia as vezes do portao — e por isso
# continuaram verdes durante todo o tempo em que a regra, aplicada aos passos
# REAIS, nao inspeccionava nada. Um substituto no ensaio esconde o produtor.
# Agora o passo do portao dos ensaios e o mesmo texto que esta no YAML.
_PASSO_PORTAO = {"name": "portao", "run": "bash tests/portao.sh"}
_recusa(nada_corre_depois_do_portao,
        [dict(_PASSO_PORTAO),
         {"name": "envio", "if": "always()", "run": "python send_newsletter.py"}],
        "ensaio", "um passo com if: always() depois do portao")
# E a regra tem de VER o portao: um ensaio em que ela nao o encontrasse ficava
# verde por nao olhar, que foi exactamente o que aconteceu.
_recusa(nada_corre_depois_do_portao,
        [dict(_PASSO_PORTAO),
         {"name": "envio", "if": "success() || failure()",
          "run": "python send_newsletter.py"}],
        "ensaio", "um `if:` composto depois do portao")

# ── E as CINCO formas que a lista de literais nao via ─────────────────────
#
# Uma regra que enumera literais so apanha o que quem a escreveu ja tinha
# imaginado. Cada uma destas desarmava o portao com a suite inteira verde.
_recusa(portao_para_mesmo,
        [{"run": "bash tests/portao.sh",
          "if": "${{ github.event_name == 'workflow_dispatch' }}"}],
        "ensaio",
        "um portao com `if:` — na sexta agendada a suite nao corre de todo")
_recusa(portao_para_mesmo, [{"run": "echo sem portao nenhum"}], "ensaio",
        "um job SEM portao nenhum — a regra tem de exigir que ele exista")
_recusa(portao_para_mesmo,
        [{"run": "bash tests/portao.sh"},
         {"name": "motor", "run": "python update_portfolio.py",
          "continue-on-error": True}],
        "ensaio",
        "um passo do motor com continue-on-error — o job acaba verde e o "
        "alerta nao dispara")
# E o `if:` num passo DEPOIS do portao, em qualquer forma — nao so nas quatro
# funcoes que a lista antiga enumerava. `if: success() || github.event_name ==
# 'schedule'` tira o `success() &&` implicito e e verdadeiro na sexta agendada
# com o portao vermelho: a edicao e gerada e ENVIADA aos subscritores.
for _cond_ex in ("always()", "success() || github.event_name == 'schedule'",
                 "${{ success() || true }}", "${{ !success() || true }}",
                 "github.event_name == 'schedule'"):
    _recusa(portao_para_mesmo,
            [{"run": "bash tests/portao.sh"},
             {"name": "envio", "if": _cond_ex, "run": "python send_newsletter.py"}],
            "ensaio", f"um passo com if: {_cond_ex!r} depois do portao")
_recusa(job_para_mesmo,
        {"if": "${{ success() || needs.update-portfolio.result == 'failure' }}",
         "steps": []}, "ensaio",
        "um job cujo `if:` quebra a cadeia sem usar nenhuma das quatro funcoes")
# E aceita o portao real, escrito como deve ser.
portao_para_mesmo([{"run": "bash tests/portao.sh"}], "ensaio")
portao_cobre_tudo([{"run": "bash tests/portao.sh"}], "ensaio")

# ── E a propriedade EXECUTADA: uma suite vermelha PARA o portao ───────────
#
# Nenhum teste sobre o texto de um YAML consegue afirmar isto. Aqui o portao e
# um ficheiro versionado, e corre-se — com um teste que falha injectado — a
# exigir que ele saia vermelho. Foi a unica maneira de fechar as cinco formas
# que a lista de literais nao via: `set +o errexit`, `if python "$t"; then :;
# fi`, correr em `&` com um `wait` que devolve 0, `||true` sem espaco, e um
# `echo` do glob ao lado de um unico ficheiro.
import subprocess as _sp_p, tempfile as _tf_p, shutil as _sh_p
_tmp_p = Path(_tf_p.mkdtemp())
try:
    # Copia-se o repositorio inteiro? Nao: basta o script e uma pasta de testes
    # com um so ficheiro, para a corrida ser rapida. O que se mede e o CODIGO DE
    # SAIDA, nao o conteudo da suite.
    (_tmp_p / "tests").mkdir()
    _sh_p.copy(PORTAO, _tmp_p / "tests" / "portao.sh")
    # UM ficheiro por cada teste que existe mesmo no disco, com os nomes reais.
    # A cobertura do portao era afirmada por TEXTO (`"tests/test_*.py" in
    # portao.sh`), e um texto nao prova nada sobre o que corre: `tests/test_a*.py`
    # contem a string, itera, sai verde — e metade da suite deixa de correr no
    # dia em que a carteira e decidida. Aqui o portao corre com a lista real de
    # nomes e diz-se quais e que TOCOU; compara-se esse conjunto com o do disco.
    _reais = sorted(_p.name for _p in (ROOT / "tests").glob("test_*.py"))
    true(len(_reais) >= 8,
         f"o disco tem uma suite para cobrir (viu {len(_reais)} ficheiros)")
    for _n_t in _reais:
        (_tmp_p / "tests" / _n_t).write_text(
            "print('TODOS OS 1 TESTES PASSARAM')\n", encoding="utf-8")
    (_tmp_p / "tests" / "test_frontend.js").write_text(
        "console.log('TODOS OS 1 TESTES PASSARAM');\n", encoding="utf-8")
    _r_verde = _sp_p.run(["bash", str(_tmp_p / "tests" / "portao.sh")],
                         capture_output=True, text=True)
    eq(_r_verde.returncode, 0,
       f"com a suite verde, o portao abre (rc={_r_verde.returncode}, "
       f"{_r_verde.stdout[-200:]}{_r_verde.stderr[-200:]})")
    _tocados = sorted(_l.split("\u2500\u2500 ", 1)[1].strip()
                      for _l in _r_verde.stdout.splitlines()
                      if _l.startswith("\u2500\u2500 ")
                      and _l.split("\u2500\u2500 ", 1)[1].startswith("tests/"))
    eq(_tocados,
       sorted([f"tests/{_n_t}" for _n_t in _reais] + ["tests/test_frontend.js"]),
       "o portao CORREU todos os ficheiros de teste do disco, nem mais nem "
       "menos — e isto que a procura de texto pelo glob nunca chegou a afirmar")
    # E os ficheiros que ele nao tocou tem de ser nenhuns: um ficheiro novo em
    # `tests/` entra no portao sozinho, sem ninguem se lembrar de o la pôr.
    for _n_t in _reais:
        true(f"tests/{_n_t}" in _tocados,
             f"o portao corre o {_n_t} — nenhum teste fica de fora por omissao")
    # E agora um teste que FALHA. Nao um que rebente no import: um que corra e
    # saia com codigo 1, que e o que os testes deste repositorio fazem.
    (_tmp_p / "tests" / "test_zz_vermelho.py").write_text(
        "import sys\nprint('1 TESTE FALHOU')\nsys.exit(1)\n", encoding="utf-8")
    _r_vermelho = _sp_p.run(["bash", str(_tmp_p / "tests" / "portao.sh")],
                            capture_output=True, text=True)
    true(_r_vermelho.returncode != 0,
         f"com UM teste vermelho, o portao FECHA-SE (rc="
         f"{_r_vermelho.returncode}) — e isto que impede a carteira de ser "
         f"decidida e a newsletter de ser enviada")
    # E o teste vermelho no MEIO da lista nao pode ser tapado pelos que vem a
    # seguir: era exactamente o que um `set +e` fazia, com o codigo de saida a
    # passar a ser o do ultimo comando.
    (_tmp_p / "tests" / "test_aa_vermelho.py").write_text(
        "import sys\nprint('1 TESTE FALHOU')\nsys.exit(1)\n", encoding="utf-8")
    (_tmp_p / "tests" / "test_zz_vermelho.py").unlink()
    _r_meio = _sp_p.run(["bash", str(_tmp_p / "tests" / "portao.sh")],
                        capture_output=True, text=True)
    true(_r_meio.returncode != 0,
         f"e um teste vermelho no MEIO da lista tambem o fecha "
         f"(rc={_r_meio.returncode})")
    # E o JavaScript conta: era o ultimo comando do bloco, e sem `-e` era o
    # unico cujo codigo de saida chegava a contar.
    (_tmp_p / "tests" / "test_aa_vermelho.py").unlink()
    (_tmp_p / "tests" / "test_frontend.js").write_text(
        "console.log('1 TESTE FALHOU'); process.exit(1);\n", encoding="utf-8")
    _r_js = _sp_p.run(["bash", str(_tmp_p / "tests" / "portao.sh")],
                      capture_output=True, text=True)
    true(_r_js.returncode != 0,
         f"e o test_frontend.js vermelho fecha-o tambem (rc={_r_js.returncode})")
finally:
    _sh_p.rmtree(_tmp_p, ignore_errors=True)

# E o script corre TODOS os ficheiros que existem, nao uma lista escrita a mao.
_txt_portao = PORTAO.read_text(encoding="utf-8")
true("tests/test_*.py" in _txt_portao,
     "o portao itera sobre o glob, nao sobre uma lista escrita a mao")
true("set -euo pipefail" in _txt_portao,
     "e aborta a primeira falha, com as variaveis por definir a contarem como "
     "erro e sem engolir falhas no meio de um pipe")

portao_cobre_tudo(passos_cart, "update-portfolio")
portao_para_mesmo(passos_cart, "update-portfolio")
nada_corre_depois_do_portao(passos_cart, "update-portfolio")
job_para_mesmo(W["friday-pipeline"]["jobs"]["update-portfolio"], "update-portfolio")
# O `test_frontend.js` faz parte do portao — mas isso e agora uma propriedade
# do SCRIPT, nao do YAML, e verifica-se onde ele vive.
true("test_frontend.js" in PORTAO.read_text(encoding="utf-8"),
     "o portao inclui o test_frontend.js")

# E o job que ENVIA tambem: era o unico sem teste nenhum.
passos_news = W["friday-pipeline"]["jobs"]["send-newsletter"]["steps"]
portao_cobre_tudo(passos_news, "send-newsletter")
portao_para_mesmo(passos_news, "send-newsletter")
nada_corre_depois_do_portao(passos_news, "send-newsletter")
job_para_mesmo(W["friday-pipeline"]["jobs"]["send-newsletter"], "send-newsletter")
# ── E agora a mesma regra, sobre TODOS os jobs de TODOS os workflows ──────
#
# As regras acima eram invocadas por NOME, job a job, e um workflow inteiro
# ficava de fora: o `test.yml` e o `update.yml` nunca passaram por elas, e um
# `continue-on-error` no portao do `test.yml`, ou um `if: false` no fetch do
# `update.yml`, sobrevivia com a suite verde. Uma regra invocada por nome cobre
# os nomes que quem a escreveu se lembrou de escrever.
#
# A isencao tambem e DERIVADA, nao uma lista: um job pode ter `if:` (o
# `alert-on-failure` corre `if: failure()`, e e essa a sua razao de ser) desde
# que nao AJA e nao tenha portao. No dia em que um job de alerta ganhar um
# `python send_newsletter.py`, deixa de ser isento e a suite fica vermelha.
# A isencao ja NAO se calcula com o detector de actos: calculava-se, e herdava
# a cegueira dele — um job que o detector nao via saltava o laco INTEIRO.
# Agora e a mesma isencao estreita da regra principal: um job de relatorio.
for _nome_wf, _wf in W.items():
    for _nome_job, _job in (_wf.get("jobs") or {}).items():
        _onde = f"{_nome_wf}/{_nome_job}"
        # A regra principal: todo o job corre a suite antes de tudo o que nao
        # seja preparar o runner. Nao precisa de saber o que e um acto.
        so_a_instalacao_precede_o_portao(_job, _onde)
        # E a regra da ordem, que continua a valer como segundo par de olhos.
        portao_antes_dos_actos(_job, _onde)
        if _e_job_de_relatorio(_job):
            continue
        job_para_mesmo(_job, _onde)
        portao_para_mesmo(_job.get("steps") or [], _onde)
        nada_corre_depois_do_portao(_job.get("steps") or [], _onde)
        portao_cobre_tudo(_job.get("steps") or [], _onde)
        for _s in _job.get("steps", []):
            _sh = (_s.get("shell") or "").strip()
            true(_sh in ("", "bash", "python") or "-e" in _sh,
                 f"o passo {_s.get('name')!r} de {_onde} nao troca o shell por um "
                 f"que engole falhas ({_sh!r})")
            # A regra do `continue-on-error` e do `if:` valia so para os dois
            # jobs COM portao do pipeline de sexta. O `refresh-data` ficava de
            # fora — e e ele que publica o data.json: com `continue-on-error` no
            # fetch, ou `if: false` no commit, o job acaba VERDE sem ter
            # publicado nada, a carteira decide sobre o ficheiro da semana
            # passada, e o alerta nao dispara.
            true(not _s.get("continue-on-error"),
                 f"o passo {_s.get('name')!r} de {_onde} nao tem "
                 f"continue-on-error: com ele o job acaba verde sem ter feito o "
                 f"que existe para fazer, e o alerta nao dispara")
            # `is not None`, nao truthiness: `if: false` e carregado pelo YAML
            # como o booleano False, e `str(False or "")` colapsava para "" — a
            # condicao que SALTA o passo em todas as corridas era a unica que
            # passava.
            eq(_s.get("if"), None,
               f"o passo {_s.get('name')!r} de {_onde} nao tem `if:` nenhum — "
               f"qualquer condicao e uma maneira de o saltar em silencio "
               f"(if: {_s.get('if')!r})")

# ── E as regras NOVAS tem de ver contra-exemplos ──────────────────────────
#
# O principio esta escrito neste ficheiro: "uma regra que nunca ve um
# contra-exemplo pode ser desligada sem que nada fique vermelho. E foi assim que
# o `if:` ao nivel do job faltou." As regras que substituiram a deteccao de
# actos nasceram sem um unico ensaio de recusa: `_pode_preceder_o_portao ->
# return True`, `so_a_instalacao_precede_o_portao -> return`, a regra do `reset
# --hard` e `_e_job_de_relatorio -> bool(job.get("if"))` sobreviviam todas a
# suite inteira. A quarta e a pior: o docstring gaba-se de a isencao ser
# estreita, e a estreiteza nao estava verificada em lado nenhum.
_recusa(so_a_instalacao_precede_o_portao,
        {"steps": [{"uses": "actions/checkout@v4"},
                   {"run": "./deploy.sh"},
                   {"run": "bash tests/portao.sh"}]},
        "ensaio", "um passo que nao e preparacao antes do portao")
_recusa(so_a_instalacao_precede_o_portao,
        {"steps": [{"uses": "actions/checkout@v4"},
                   {"run": "pip install -r requirements.txt && python motor.py"},
                   {"run": "bash tests/portao.sh"}]},
        "ensaio", "uma linha que instala E age antes do portao")
_recusa(so_a_instalacao_precede_o_portao,
        {"steps": [{"uses": "./.github/actions/decidir"},
                   {"run": "bash tests/portao.sh"}]},
        "ensaio", "um `uses:` local antes do portao")
_recusa(so_a_instalacao_precede_o_portao,
        {"steps": [{"uses": "actions/checkout@v4"},
                   {"run": "python -u send_newsletter.py"}]},
        "ensaio", "um job sem portao nenhum (com o idioma que o detector de "
                  "actos nao via)")
_recusa(so_a_instalacao_precede_o_portao, {"steps": []}, "ensaio",
        "um job SEM passos — nem esse pode ser isento por omissao")
_recusa(so_a_instalacao_precede_o_portao, {}, "ensaio",
        "um job sem a chave `steps` de todo")
# E a isencao do relatorio nao pode alargar-se: cada uma das tres condicoes,
# sozinha, tem de a negar.
_ALERTA_REAL = W["friday-pipeline"]["jobs"]["alert-on-failure"]
true(_e_job_de_relatorio(_ALERTA_REAL),
     "o job de alerta REAL e reconhecido como relatorio — senao esta regra "
     "estaria verde por nunca isentar ninguem")
_recusa(so_a_instalacao_precede_o_portao,
        dict(_ALERTA_REAL, **{"if": "always()"}), "ensaio",
        "um job `if: always()` — corre tambem no caminho de sucesso")
_recusa(so_a_instalacao_precede_o_portao,
        {_k: _v for _k, _v in _ALERTA_REAL.items() if _k != "needs"}, "ensaio",
        "um job `if: failure()` SEM needs — nao esta a relatar coisa nenhuma")
_recusa(so_a_instalacao_precede_o_portao,
        dict(_ALERTA_REAL, steps=list(_ALERTA_REAL["steps"]) +
             [{"name": "reenviar", "run": "python -u send_newsletter.py"}]),
        "ensaio",
        "o job de alerta REAL com um passo que ENVIA — corre com a cadeia "
        "vermelha e o portao nunca correu")
_recusa(so_a_instalacao_precede_o_portao,
        dict(_ALERTA_REAL, steps=list(_ALERTA_REAL["steps"]) +
             [{"name": "publicar", "uses": "./.github/actions/publicar"}]),
        "ensaio", "o job de alerta REAL com um `uses:` local")
_recusa(so_a_instalacao_precede_o_portao,
        dict(_ALERTA_REAL, steps=list(_ALERTA_REAL["steps"]) +
             [{"name": "make", "run": "make send"}]),
        "ensaio", "o job de alerta REAL com um `make`")
# E aceita as formas correctas, para a regra nao ser um "recusa tudo".
so_a_instalacao_precede_o_portao(
    {"steps": [{"uses": "actions/checkout@v4"},
               {"uses": "actions/setup-python@v5"},
               {"run": "pip install -r requirements.txt"},
               {"run": "bash tests/portao.sh"},
               {"run": "python update_portfolio.py"}]}, "ensaio")
so_a_instalacao_precede_o_portao(_ALERTA_REAL, "ensaio")
# E o reconhecedor de passos de relatorio nos dois sentidos.
for _p_r, _quer_r in ((({"uses": "actions/github-script@v7"}), True),
                      (({"run": "echo ola"}), True),
                      (({"run": "curl -fsS -X POST url"}), True),
                      (({"run": "python x.py"}), False),
                      (({"run": "python -u x.py"}), False),
                      (({"run": "bash publica.sh"}), False),
                      (({"run": "make send"}), False),
                      (({"uses": "./.github/actions/x"}), False),
                      (({"run": "echo a\npython x.py"}), False),
                      (({}), False)):
    eq(_passo_e_de_relatorio(_p_r), _quer_r,
       f"um passo de relatorio: {_p_r} -> {_quer_r}")

# TODOS os jobs que guardam correm exactamente o MESMO portao: a assimetria
# entre eles era um sitio por onde ele se desarmava, e agora e uma so invocacao
# do mesmo ficheiro — nao ha como divergirem.
_invocacoes = sorted({(_s.get("run") or "").strip()
                      for _wf in W.values()
                      for _job in (_wf.get("jobs") or {}).values()
                      for _s in (_job.get("steps") or [])
                      if _e_portao(_s)})
eq(_invocacoes, ["bash tests/portao.sh"],
   "todos os jobs de todos os workflows correm o MESMO portao, escrito da "
   "mesma maneira")
# E os jobs que guardam sao TODOS os que agem — nenhum acto fica sem portao em
# nenhum workflow. Contado, para que a regra nao possa estar verde por nao ter
# visto job nenhum.
_com_actos = [f"{_n}/{_j}" for _n, _wf in W.items()
              for _j, _job in (_wf.get("jobs") or {}).items()
              if any(actos_do_passo(_s) for _s in (_job.get("steps") or []))]
true(len(_com_actos) >= 3,
     f"a regra da ordem viu mesmo os jobs que agem ({_com_actos})")

# ── E a regra da ordem recusa, sobre os passos REAIS ──────────────────────
#
# Um ensaio construido a mao afirma o que quem o escreveu imaginou. Estes
# ensaios sao o YAML de producao MUTADO: se a regra estiver verde por nao olhar,
# a mutacao passa e o ensaio da o alarme.
_reais_news = W["friday-pipeline"]["jobs"]["send-newsletter"]["steps"]
_i_p_news = next(_i for _i, _s in enumerate(_reais_news) if _e_portao(_s))
_i_a_news = next(_i for _i, _s in enumerate(_reais_news) if actos_do_passo(_s))
# 1) os dois passos reais trocados — a edicao e ENVIADA e so depois a suite corre
_trocado = list(_reais_news)
_trocado[_i_p_news], _trocado[_i_a_news] = _trocado[_i_a_news], _trocado[_i_p_news]
_recusa(portao_antes_dos_actos, {"steps": _trocado}, "ensaio",
        "o job REAL do envio com o portao depois do envio")
# 2) o portao real apagado — um job que age sem portao nenhum
_recusa(portao_antes_dos_actos,
        {"steps": [_s for _i, _s in enumerate(_reais_news) if _i != _i_p_news]},
        "ensaio", "o job REAL do envio sem portao nenhum")
# 3) o mesmo para o job que decide a carteira
_reais_cart = W["friday-pipeline"]["jobs"]["update-portfolio"]["steps"]
_i_p_cart = next(_i for _i, _s in enumerate(_reais_cart) if _e_portao(_s))
_recusa(portao_antes_dos_actos,
        {"steps": [_s for _i, _s in enumerate(_reais_cart) if _i != _i_p_cart]},
        "ensaio", "o job REAL da carteira sem portao nenhum")
# 4) um job NOVO que corre o motor sem portao — o caso que nenhuma regra por
#    nome podia ver, porque o nome ainda nao existia quando ela foi escrita
_recusa(portao_antes_dos_actos,
        {"steps": [{"name": "motor", "run": "python update_portfolio.py"}]},
        "ensaio", "um job novo que corre o motor sem portao")
_recusa(portao_antes_dos_actos,
        {"steps": [{"name": "envio", "run": "python send_newsletter.py"}]},
        "ensaio", "um job novo que ENVIA sem portao")
_recusa(portao_antes_dos_actos,
        {"steps": [{"name": "dados", "run": "python fetch_data.py"}]},
        "ensaio", "um job novo que publica dados sem portao")
_recusa(portao_antes_dos_actos,
        {"steps": [{"name": "publicar", "run": "git add x\ngit push"}]},
        "ensaio", "um job novo que faz push sem portao")
# 5) o portao no MEIO: depois do primeiro acto e antes do segundo nao chega
_recusa(portao_antes_dos_actos,
        {"steps": [{"run": "python fetch_data.py"},
                   {"run": "bash tests/portao.sh"},
                   {"run": "git push"}]},
        "ensaio", "um portao entre dois actos — o primeiro ja aconteceu")
# 6) e um job de alerta que ganha um acto deixa de ser isento
_recusa(portao_antes_dos_actos,
        {"if": "failure()",
         "steps": [{"run": "python send_newsletter.py"}]},
        "ensaio", "um job de alerta que passou a ENVIAR")
# E aceita as formas correctas: portao antes do acto, e um job que nao age.
portao_antes_dos_actos({"steps": [{"run": "bash tests/portao.sh"},
                                  {"run": "python update_portfolio.py"}]},
                       "ensaio")
portao_antes_dos_actos({"if": "failure()", "steps": [{"uses": "actions/github-script@v7"}]},
                       "ensaio")
# E o detector de actos nao pode ser cego nem histerico: verifica-se nos dois
# sentidos, porque uma regra que nao ve acto nenhum esta verde por nao olhar.
eq(acto_da_linha("python update_portfolio.py"), "update_portfolio.py", "o motor e um acto")
eq(acto_da_linha("  python3 send_newsletter.py  "), "send_newsletter.py", "python3 tambem")
eq(acto_da_linha("git push && exit 0"), "git push", "um push e um acto")
eq(acto_da_linha("            git push"), "git push", "mesmo indentado dentro de um laco")
eq(acto_da_linha("python tests/test_audit.py"), None, "correr um teste nao e um acto")
eq(acto_da_linha("bash tests/portao.sh"), None, "o portao nao e um acto")
eq(acto_da_linha("python -m pyflakes *.py tests/*.py"), None, "o lint nao e um acto")
eq(acto_da_linha("pip install -r requirements.txt"), None, "instalar nao e um acto")
eq(acto_da_linha("# git push -- comentario"), None, "um comentario nao e um acto")
eq(acto_da_linha("./actionlint"), None, "o actionlint nao e um acto")
eq(acto_da_linha("echo 'python update_portfolio.py'"), None,
   "uma mencao dentro de um echo nao e a invocacao")

# ── os pushes fazem rebase e tentam outra vez ─────────────────────────────
for nome, job in (("friday-pipeline", "refresh-data"),
                  ("friday-pipeline", "update-portfolio"),
                  ("update", "update-data")):
    runs = " ".join(s.get("run") or "" for s in W[nome]["jobs"][job]["steps"])
    if "git push" in runs:
        true("pull --rebase" in runs, f"{nome}/{job} faz rebase antes do push")
        true("for i in" in runs, f"{nome}/{job} tenta o push mais do que uma vez")

# ── uma falha nao pode ser silenciosa ─────────────────────────────────────
for nome in ("friday-pipeline", "update"):
    jobs = W[nome]["jobs"]
    alerta = jobs.get("alert-on-failure")
    true(alerta is not None, f"{nome} tem job de alerta")
    eq(str(alerta.get("if")).strip(), "failure()", f"{nome}: o alerta dispara em falha")
    true(W[nome].get("permissions", {}).get("issues") == "write",
         f"{nome} tem permissao para abrir o issue do alerta")
    corpo = " ".join(str(s) for s in alerta["steps"])
    true("issues.create" in corpo, f"{nome}: o alerta abre mesmo um issue")
deps = set(W["friday-pipeline"]["jobs"]["alert-on-failure"]["needs"])
eq(deps, {"refresh-data", "update-portfolio", "send-newsletter"},
   "o alerta cobre os tres jobs, nao so o ultimo")

# ── contextos de expressao validos nos `if:` ──────────────────────────────
# O contexto `secrets` NAO e permitido num `if`, e o GitHub rejeita o ficheiro
# inteiro quando o encontra: a corrida agendada nem comeca, e o proprio job de
# alerta vai dentro do ficheiro invalido. O actionlint apanha, mas corre num
# passo de CI que descarrega da rede e nao faz parte do portao de sexta. Esta
# verificacao e local e nao precisa de rede.
CONTEXTOS_PROIBIDOS_EM_IF = ("secrets.",)

def todos_os_ifs(doc):
    for job, cfg in doc["jobs"].items():
        if "if" in cfg:
            yield f"{job} (job)", str(cfg["if"])
        for i, passo in enumerate(cfg.get("steps", [])):
            if "if" in passo:
                yield f"{job}/passo {i} ({passo.get('name', '?')})", str(passo["if"])

for nome, doc in W.items():
    for onde, expr in todos_os_ifs(doc):
        for proibido in CONTEXTOS_PROIBIDOS_EM_IF:
            true(proibido not in expr,
                 f"{nome}/{onde}: `if:` usa o contexto {proibido!r}, que o GitHub "
                 f"rejeita — o ficheiro inteiro fica invalido (expr: {expr!r})")

# um `if` que le uma variavel de ambiente exige que ela esteja declarada no job
for nome, doc in W.items():
    for job, cfg in doc["jobs"].items():
        env_job = set((cfg.get("env") or {}).keys())
        for i, passo in enumerate(cfg.get("steps", [])):
            expr = str(passo.get("if", ""))
            for var in re.findall(r"env\.([A-Za-z_][A-Za-z0-9_]*)", expr):
                true(var in env_job,
                     f"{nome}/{job}/passo {i}: o `if` le env.{var}, que tem de estar "
                     f"declarado ao nivel do JOB (o env do proprio passo nao e visivel ai)")

# ── duas corridas nao se atropelam ────────────────────────────────────────
for nome in ("friday-pipeline", "update"):
    true(W[nome].get("concurrency"), f"{nome} declara concurrency")
    eq(W[nome]["concurrency"].get("cancel-in-progress"), False,
       f"{nome}: uma corrida a decidir a carteira nao pode ser cancelada a meio")

# ── os segredos tem de estar ao alcance de TODOS os passos que precisam deles ─
#
# A FRED_API_KEY estava declarada so no passo do fetch. O passo do commit, no
# mesmo job, tem um laco de recuperacao de conflito que volta a correr o
# fetch_data.py — e esse levanta RuntimeError no import quando a chave falta.
# O laco de retry, construido exactamente para o dia em que os dois workflows
# colidissem no data.json, nunca podia funcionar. Nenhum teste dava por isso
# porque nenhum olhava para o AMBITO dos segredos, so para a sua presenca.
# O que cada comando le do ambiente, verificado contra o proprio codigo mais
# abaixo — nao escrito de cor.
COMANDOS_QUE_PRECISAM = {
    "python fetch_data.py":       ("FRED_API_KEY",),
    "python send_newsletter.py":  ("ANTHROPIC_API_KEY", "BREVO_API_KEY"),
}

for nome, wf in W.items():
    for job_id, job in (wf.get("jobs") or {}).items():
        herdado = set(wf.get("env") or {}) | set(job.get("env") or {})
        for passo in (job.get("steps") or []):
            corpo = passo.get("run") or ""
            visiveis = herdado | set(passo.get("env") or {})
            for comando, chaves in COMANDOS_QUE_PRECISAM.items():
                if comando in corpo:
                    for chave in chaves:
                        true(chave in visiveis,
                             f"{nome}/{job_id}, passo {passo.get('name', '(sem nome)')!r}: "
                             f"corre `{comando}` e ve a {chave}")

# E a lista acima nao pode ser escrita de cor: confirma-se contra o codigo que
# esses ficheiros leem mesmo essas variaveis.
for _fich, _chaves in COMANDOS_QUE_PRECISAM.items():
    _nome_fich = _fich.split()[-1]
    _fonte = (ROOT / _nome_fich).read_text(encoding="utf-8")
    for _ch in _chaves:
        true(_ch in _fonte,
             f"{_nome_fich} le mesmo a {_ch} — a lista acima descreve o codigo")

# ── o tempo declarado tem de chegar para o trabalho declarado ─────────────
# O send_newsletter faz ate NEWSLETTER_TENTATIVAS geracoes, cada uma com ate 3
# chamadas ao modelo de 300 s. O pior caso batia nos 46 minutos contra um
# timeout de 30: o GitHub matava o job antes da ultima tentativa, e o que se
# perdia era a mensagem que diz porque e que a edicao foi recusada.
import importlib.util as _ilu
sys.path.insert(0, str(ROOT))
_sp = _ilu.spec_from_file_location("sn_wf", ROOT / "send_newsletter.py")
_sn = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_sn)
_pior_caso_min = (_sn.NEWSLETTER_TENTATIVAS * 3 * 300 + 3 * 30) / 60.0
_timeout = fp["send-newsletter"].get("timeout-minutes")
true(isinstance(_timeout, int) and _timeout >= _pior_caso_min,
     f"o timeout do job da newsletter ({_timeout} min) cobre o pior caso "
     f"declarado no codigo ({_pior_caso_min:.0f} min)")

# ── E o job dos DADOS, pela mesma aritmetica ──────────────────────────────
#
# A regra do prazo existia so para o job do envio. O `refresh-data` ganhou um
# portao e o laco de recuperacao ganhou outro — e o pior caso triplicou sem que
# o prazo mudasse: 1 portao + 1 fetch, mais 3 voltas de (portao + fetch + 10 s).
# Com a FRED degradada, cada fetch faz `retries` tentativas por serie com
# `timeout` e backoff. Com 15 minutos o GitHub matava o job a meio: sem
# data.json publicado, a carteira e a newsletter saltadas, a semana perdida.
_sp_fd = _ilu.spec_from_file_location("fd_wf", ROOT / "fetch_data.py")
_fd = _ilu.module_from_spec(_sp_fd); _sp_fd.loader.exec_module(_fd)
import inspect as _insp
_par_fd = _insp.signature(_fd.fetch_fred).parameters
_tent_fd = _par_fd["retries"].default
_back_fd = _par_fd["backoff"].default
# Uma corrida difícil do fetch: as series todas, cada uma com as tentativas e o
# backoff declarados. O numero de series le-se do proprio modulo, nao de cor.
# O numero de series LE-SE do codigo — os identificadores passados ao
# `fetch_fred` mais o mapa da liquidez — e nao esta escrito de cor aqui: uma
# serie nova acrescentada amanha entra nesta conta sozinha.
_fonte_fd = (ROOT / "fetch_data.py").read_text(encoding="utf-8")
_ids_fd = set(_re_rb.findall(
    r"fetch_fred(?:_full_history)?\(\s*[\"']([A-Z0-9]+)[\"']", _fonte_fd))
_series_fd = len(_ids_fd) + len(getattr(_fd, "LIQUIDITY_SERIES", {}) or {})
true(_series_fd >= 5,
     f"a leitura das series do fetch_data encontrou mesmo alguma coisa "
     f"({_series_fd}: {sorted(_ids_fd)}) — zero aqui punha esta regra a medir "
     f"um pior caso de zero minutos")
_um_fetch_s = _series_fd * (_tent_fd * 15 + _back_fd * (1 + 2))
_portao_s = 60           # a suite corre em ~25 s; 60 s de folga para instalar nada
_pior_dados_min = ((_um_fetch_s + _portao_s)
                   + 3 * (_um_fetch_s + _portao_s + 10)) / 60.0
for _job_dados, _wf_dados in (("refresh-data", "friday-pipeline"),
                              ("update-data", "update")):
    _t_dados = W[_wf_dados]["jobs"][_job_dados].get("timeout-minutes")
    true(isinstance(_t_dados, int) and _t_dados >= _pior_dados_min,
         f"o timeout de {_wf_dados}/{_job_dados} ({_t_dados} min) cobre o pior "
         f"caso declarado — {_series_fd} series x {_tent_fd} tentativas, mais "
         f"as 3 voltas do laco de recuperacao com o portao a correr em cada uma "
         f"({_pior_dados_min:.0f} min)")

# ── tudo o que os workflows precisam tem de estar seguido pelo git ────────
# Os modulos partilhados e os testes novos ficaram untracked. `git add -u` — que
# so leva ficheiros ja seguidos — matava os tres jobs: o fetch_data, o
# update_portfolio e o send_newsletter importam o data_freshness e o
# newsletter_parse ao nivel do modulo, e o portao corre `tests/test_*.py`.
import subprocess as _proc
_seguidos = set(_proc.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                        text=True).stdout.split())
# E se estamos DENTRO de um repositorio, a lista tem de vir: um `git ls-files`
# que falhe — git ausente, indice corrompido — devolvia vazio e o bloco inteiro
# saltava em silencio. Uma verificacao que se desliga sozinha quando a
# ferramenta falha nao e uma verificacao; e no runner do GitHub, que faz
# checkout, o caso "fora de um repositorio" nao existe.
true((not (ROOT / ".git").exists()) or bool(_seguidos),
     "dentro de um repositorio, o `git ls-files` tem de devolver ficheiros — "
     "vazio aqui significa que a verificacao seguinte se desligou sozinha")
if _seguidos:                      # fora de um repositorio git, salta
    _precisos = [p.name for p in ROOT.glob("*.py")] + \
                [f"tests/{p.name}" for p in (ROOT / "tests").glob("*.py")] + \
                ["tests/test_frontend.js", "requirements.txt"]
    _faltam = [f for f in _precisos if f not in _seguidos]
    true(not _faltam,
         f"todos os ficheiros que os workflows importam ou correm estao no git "
         f"(faltam: {_faltam})")

# O workflow de CI tem o mesmo dever que os portoes: correr a suite toda. A
# lista escrita a mao esta completa hoje por acaso, e foi exactamente assim que
# os portoes ficaram sem o test_gauge_b.py no dia em que ele foi escrito.
portao_cobre_tudo(W["test"]["jobs"]["test"]["steps"], "test.yml")

# ── o alerta tem de dizer o que fazer, e o runbook tem de existir ────────
# O corpo do issue dizia "a newsletter pode nao ter saido" e aconselhava
# re-correr — e no modo de falha mais perigoso (marca por publicar) a newsletter
# SAIU e re-correr reenvia a toda a gente. A instrucao certa vivia so num
# print() perdido no log.
_alerta = " ".join(str(p.get("with", {}).get("script", ""))
                   for p in fp["alert-on-failure"]["steps"])
true("RUNBOOK" in _alerta, "o alerta remete para o runbook")
true("INCOMPLETA" in _alerta, "e distingue a entrega incompleta")
true("sent_issues.json" in _alerta, "e nomeia a marca de envio")
true("reenvia" in _alerta or "duas vezes" in _alerta,
     "e diz qual e o risco de re-correr as cegas")

_runbook = ROOT / "RUNBOOK.md"
true(_runbook.exists(), "o RUNBOOK.md existe")

# ── O RUNBOOK tem de conhecer o unico input posto a mao ───────────────────
#
# `SP500_EARNINGS_YIELD` e a unica coisa neste sistema que so um humano
# actualiza — e actualiza-la MEXE no composto, que decide a carteira. Passado o
# prazo, o pilar Premium sai do composto; ao repor a referencia, volta. O
# procedimento nao pode ficar so no comentario de um ficheiro Python: se o
# operador nao souber que existe, a referencia apodrece e o sistema decide sobre
# quatro pilares em vez de cinco, semana apos semana.
_txt_rb_ep = _runbook.read_text(encoding="utf-8")
for _termo_rb in ("SP500_EARNINGS_YIELD", "EP_STALE_AFTER_DAYS",
                  "EP_ND_AFTER_DAYS"):
    true(_termo_rb in _txt_rb_ep,
         f"o RUNBOOK nomeia {_termo_rb} — e o unico input posto a mao")
# A consequencia que o RUNBOOK anuncia tem de ser a do codigo, nao uma
# terceira historia. Com um pilar em n/d o composto vai a None, e entao
# `classify_regime` nao muda o regime em nenhuma direccao: nao entra em
# Resilient (nao ha como confirmar) nem sai dele (nao ha como desmentir). A
# unica excepcao e sair de Critical, que e o medidor B a decidir, nao o score.
# Aqui verifica-se a propriedade no codigo E que a seccao da ancora a conta ao
# operador — se alguem mexer numa das duas, esta assercao cai.
import importlib.util as _iu_rg
_sp_rg = _iu_rg.spec_from_file_location("rules_rb", ROOT / "mrm_rules.py")
_rules_rb = _iu_rg.module_from_spec(_sp_rg)
_sp_rg.loader.exec_module(_rules_rb)
for _prev_rb in ("Resilient", "Turbulence"):
    eq(_rules_rb.classify_regime(None, False, _prev_rb), _prev_rb,
       f"sem composto completo o regime {_prev_rb} mantem-se — nao se entra "
       f"em Resilient nem se sai dele")
eq(_rules_rb.classify_regime(None, False, "Critical"), "Turbulence",
   "a unica saida sem score e a de Critical, que o medidor B decidiu")
_sec_rb = _txt_rb_ep.split("## Manuten", 1)[-1].split("\n---", 1)[0]
true("n/d" in _sec_rb and "Resilient" in _sec_rb,
     "e a seccao da ancora diz ao operador o que acontece ao Resilient "
     "enquanto houver um pilar em n/d")
# ── E a segunda dependencia da DGS10, que so um humano descobre lendo ─────
#
# A DGS10 alimenta o E/P do pilar Premium E a janela de 3 meses que escolhe,
# dentro de Critical, entre 35% em TLT e 20% em SHY. A segunda ficou anos sem
# estar declarada em lado nenhum: nem no RUNBOOK, nem como gatilho publicado. Um
# operador a ver "DGS10 stopped updating" nao tinha como saber que a carteira
# tinha um sub-regime congelado por causa disso.
true("tenY3m" in _txt_rb_ep,
     "o RUNBOOK nomeia o gatilho da janela do 10Y")
true("DGS10" in _txt_rb_ep and "Critical_FTQ" in _txt_rb_ep,
     "e diz que serie o alimenta e o que ela decide")
# E o que ele promete e o que o codigo faz, medido no codigo.
_sp_rg2 = _iu_rg.spec_from_file_location("rules_rb2", ROOT / "mrm_rules.py")
_rules_rb2 = _iu_rg.module_from_spec(_sp_rg2)
_sp_rg2.loader.exec_module(_rules_rb2)
eq(_rules_rb2.subregime_from_gauge(None, True, "Critical_FTQ")[0], "Critical_FTQ",
   "sem leitura, quem esta em FTQ mantem-se em FTQ")
eq(_rules_rb2.subregime_from_gauge(None, False)[0], "Critical_Stress",
   "e a entrada fresca continua a ir para o lado defensivo")

# E os prazos que o RUNBOOK anuncia sao os do codigo — nao uma terceira copia.
import importlib.util as _iu_ep
_sp_ep = _iu_ep.spec_from_file_location("fd_rb", ROOT / "fetch_data.py")
_fd_rb = _iu_ep.module_from_spec(_sp_ep)
import os as _os_ep
_os_ep.environ.setdefault("FRED_API_KEY", "x")
_sp_ep.loader.exec_module(_fd_rb)
for _nome_ep, _val_ep in (("EP_STALE_AFTER_DAYS", _fd_rb.EP_STALE_AFTER_DAYS),
                          ("EP_ND_AFTER_DAYS", _fd_rb.EP_ND_AFTER_DAYS)):
    true(f"{_val_ep} dias" in _txt_rb_ep,
         f"e o prazo que o RUNBOOK diz para {_nome_ep} e o do codigo "
         f"({_val_ep})")

# E o pyyaml esta declarado onde o runner o vai buscar: sem ele, este ficheiro
# nao corre, e sem este ficheiro ninguem verifica os workflows.
_reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
true(_re_rb.search(r"(?im)^\s*(pyyaml|PyYAML)\b", _reqs),
     f"o pyyaml esta no requirements.txt — e o que faz este contrato correr no "
     f"runner ({_reqs.strip()!r})")

# ── A privacidade da marca NAO se verifica aqui ───────────────────────────
#
# Houve aqui uma asserçao a exigir que o `sent_issues.json` commitado nao
# tivesse um unico "@". A intencao estava certa e o sitio estava errado: este
# ficheiro e portao dos DOIS jobs de sexta, e o job da carteira nao tem relacao
# nenhuma com enderecos de subscritores. Um endereco escrito a mao numa
# recuperacao — que e o que o RUNBOOK manda fazer — fechava os dois jobs
# indefinidamente e, pior, impedia a limpeza automatica de correr, porque ela
# vive dentro do job travado: o sistema deixava de se curar por causa da guarda.
#
# A garantia passou para onde tem efeito: o `send_newsletter.sanear_marca()`
# limpa o ficheiro e publica-o limpo no inicio de cada corrida, e isso esta
# afirmado em test_newsletter_main.py. O que aqui fica e a parte que e mesmo
# deste ficheiro: o RUNBOOK nao pode voltar a mandar escrever enderecos.

# ── E o RUNBOOK nao pode voltar a mandar escrever enderecos ────────────────
#
# O sent_issues.json e commitado para main e a raiz do ramo e servida em
# usmrm.net. O RUNBOOK manda o operador edita-lo a mao em dois dos quatro casos:
# se voltar a dizer-lhe para colar a lista de destinatarios, a exposicao volta
# pela porta do procedimento, sem uma linha de codigo mudar.
_texto_rb = _runbook.read_text(encoding="utf-8")
_bloco_marca = _texto_rb[_texto_rb.find("sent_issues.json"):]
true("Nunca escrever endereços de e-mail neste ficheiro" in _texto_rb,
     "o RUNBOOK diz, a letra, para nunca escrever enderecos no sent_issues.json")
true("<marcas>" in _texto_rb or "marca_destinatario" in _texto_rb,
     "e diz como obter a marca em vez do endereco")
_exemplos = _re_rb.findall(r'"served":\s*\[([^\]]*)\]', _texto_rb)
for _ex in _exemplos:
    true("@" not in _ex,
         f"e nenhum exemplo do RUNBOOK mostra um endereco no campo served ({_ex})")
_texto_rb = _runbook.read_text(encoding="utf-8")
for _t in ("sent_issues.json", "complete", "MarcaIlegivel", "data_prev.json",
           "force_rebalance", "git add -u"):
    true(_t in _texto_rb, f"o runbook cobre {_t}")
# E o runbook tem de estar seguido pelo git, como tudo o resto.
if _seguidos:
    true("RUNBOOK.md" in _seguidos, "e esta commitado")

# ── o laco de recuperacao de conflito CORRE mesmo ────────────────────────
#
# Ate aqui verificava-se que o laco existia e que a chave estava no ambito
# certo — nunca que ele funciona. E shell dentro de YAML: nenhum teste o
# executava, e o defeito que o tornava inutil (a FRED_API_KEY declarada so no
# passo do fetch, quando e o passo do COMMIT que volta a correr o
# fetch_data.py) viveu ali sem ser visto.
#
# Este teste extrai o `run:` do proprio workflow — nao uma copia — e corre-o
# num repositorio git de brincar, contra um conflito real: outro cliente
# empurrou uma alteracao ao MESMO ficheiro. So se substitui o `sleep`, para o
# teste nao demorar 30 segundos.
#
# O que este bloco NAO verifica e o ambito do segredo no YAML: aqui a chave vem
# do ambiente do processo, para se poder correr o laco com e sem ela. Quem
# verifica o ambito e o bloco COMANDOS_QUE_PRECISAM, mais abaixo.
import os
import shutil as _sh
import subprocess as _proc2
import tempfile as _tmp

def _passo_do_commit(wf, job):
    for p in W[wf]["jobs"][job]["steps"]:
        corpo = p.get("run") or ""
        if "git add data.json" in corpo and "for i in" in corpo:
            return corpo
    return None

def _monta_repo(d, com_chave, portao_do_remoto=0):
    """Um remoto, um cliente que ja empurrou, e o runner com a sua versao.

    `portao_do_remoto` e o codigo de saida do `tests/portao.sh` que existe em
    `origin/main`. Depois do `git reset --hard origin/main` o laco corre o
    portao DESSA base — que nao e a base sobre a qual a suite correu no passo
    anterior — e e por isso que ele existe aqui: com 1, a base nova esta
    vermelha e o laco tem de morrer sem publicar.
    """
    env = dict(os.environ, GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@b.c",
               GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@b.c")
    if com_chave:
        env["FRED_API_KEY"] = "x"
    else:
        env.pop("FRED_API_KEY", None)
    def g(*args, cwd=None):
        return _proc2.run(args, cwd=cwd or d, env=env, capture_output=True, text=True)

    semente = d / "semente"
    semente.mkdir()
    g("git", "init", "-q", cwd=semente); g("git", "checkout", "-q", "-b", "main", cwd=semente)
    (semente / "data.json").write_text('{"v":0}')
    # O fetch_data de brincar rebenta no import sem a chave, como o verdadeiro.
    (semente / "fetch_data.py").write_text(
        'import json, os\n'
        'if not os.environ.get("FRED_API_KEY"):\n'
        '    raise RuntimeError("FRED_API_KEY nao definida")\n'
        'json.dump({"v": 7}, open("data.json", "w"))\n')
    # O portao existe no repositorio, como no verdadeiro: o laco corre-o depois
    # do `reset --hard` e sem ele o passo morre com "No such file or directory".
    (semente / "tests").mkdir()
    (semente / "tests" / "portao.sh").write_text("#!/bin/sh\nexit 0\n")
    g("git", "add", "-A", cwd=semente); g("git", "commit", "-qm", "inicial", cwd=semente)
    g("git", "clone", "-q", "--bare", str(semente), str(d / "remoto.git"))
    g("git", "clone", "-q", str(d / "remoto.git"), str(d / "outro"))
    g("git", "clone", "-q", str(d / "remoto.git"), str(d / "runner"))
    outro = d / "outro"
    (outro / "data.json").write_text('{"v":99}')
    if portao_do_remoto:
        (outro / "tests" / "portao.sh").write_text(
            f"#!/bin/sh\nexit {portao_do_remoto}\n")
    g("git", "commit", "-qam", "outro", cwd=outro)
    g("git", "push", "-q", "origin", "main", cwd=outro)
    runner = d / "runner"
    (runner / "data.json").write_text('{"v":1}')
    return runner, env

def _corre_laco(corpo, com_chave, push_sempre_recusado=False,
               portao_do_remoto=0, devolve_remoto=False):
    d = Path(_tmp.mkdtemp())
    try:
        runner, env = _monta_repo(d, com_chave, portao_do_remoto)
        if push_sempre_recusado:
            # Um `git` de brincar no PATH que recusa todos os pushes e delega o
            # resto no verdadeiro. E como se outro cliente empurrasse sempre
            # primeiro — o caso em que as tres tentativas se esgotam.
            _bin = d / "bin"; _bin.mkdir()
            # `shutil.which`, e nao o binario `which`: o Debian ja o retirou do
            # debianutils e o Ubuntu segue-lhe o rasto. Um FileNotFoundError
            # aqui nao falhava so esta asserçao — matava o ficheiro inteiro, que
            # e portao dos dois jobs de sexta.
            _git_real = _sh.which("git") or "/usr/bin/git"
            (_bin / "git").write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do [ "$a" = "push" ] && exit 1; done\n'
                f'exec {_git_real} "$@"\n')
            (_bin / "git").chmod(0o755)
            env = dict(env, PATH=f"{_bin}:{env.get('PATH', '')}")
        # `python` pode nao existir; e o sleep de 10s nao serve num teste.
        guiao = corpo.replace("sleep 10", "sleep 0").replace("python fetch_data.py",
                                                             sys.executable + " fetch_data.py")
        # `bash -e`, que e o que o GitHub usa por omissao num `run:`. A
        # diferenca nao e cosmetica: sem o `-e`, um `python fetch_data.py` que
        # rebente e ENGOLIDO, o laco segue, o `git push` diz "everything
        # up-to-date" e o passo termina VERDE com a semana por publicar.
        r = _proc2.run(["bash", "-e", "-c", guiao], cwd=runner, env=env,
                       capture_output=True, text=True, timeout=120)
        remoto = _proc2.run(["git", "show", "main:data.json"], cwd=d / "remoto.git",
                            capture_output=True, text=True).stdout
        return r.returncode, remoto, (r.stdout + r.stderr)
    finally:
        _sh.rmtree(d, ignore_errors=True)

# ── Dentro de um bloco de shell, um `reset --hard` reabre o portao ────────
#
# A regra da ordem e sobre os INDICES dos passos, e um bloco de shell tem uma
# ordem propria por dentro. O laco de recuperacao faz `git reset --hard
# origin/main` e a partir dai a arvore e o codigo que estiver no remoto NESSE
# instante — que nao e o codigo sobre o qual a suite correu. Correr um acto
# depois disso, mesmo que a publicacao so venha a seguir, e correr codigo por
# testar. O portao tem de vir entre o `reset` e o primeiro acto, nao depois.
def portao_apos_reset(passo, onde):
    """Um `reset --hard` reabre o portao para tudo o que volte a correr.

    O bloco NAO e uma linha recta: e um `for i in 1 2 3`. Um acto escrito ANTES
    do `reset --hard` executa-se DEPOIS dele na volta seguinte, sobre uma base
    que esta corrida nunca testou — e no job da carteira era precisamente o
    `git push` que estava nessa posicao. Ler so as linhas seguintes deixava esse
    caso invisivel por construcao: a MESMA execucao, dada a volta do laco,
    ficava verde.
    """
    _linhas_b = (passo.get("run") or "").splitlines()
    _i_reset = next((_i for _i, _l in enumerate(_linhas_b)
                     if "reset --hard" in _l and not _l.strip().startswith("#")),
                    None)
    if _i_reset is None:
        return
    _i_laco = next((_i for _i, _l in enumerate(_linhas_b[:_i_reset])
                    if _re_rb.match(r"^\s*(for|while)\b", _l)), None)
    if _i_laco is None:
        # Sem laco, a leitura recta serve: o que corre depois e o que esta
        # escrito depois.
        _regiao = _linhas_b[_i_reset + 1:]
        _exige = any(acto_da_linha(_l) for _l in _regiao)
    else:
        # Com laco, TUDO o que esta no corpo volta a correr depois do reset.
        _i_done = next((_i for _i, _l in enumerate(_linhas_b)
                        if _i > _i_reset and _re_rb.match(r"^\s*done\b", _l)),
                       len(_linhas_b))
        _regiao = _linhas_b[_i_reset + 1:_i_done]
        _exige = any(acto_da_linha(_l) for _l in _linhas_b[_i_laco + 1:_i_done])
    if not _exige:
        return
    _i_portao_b = next((_i for _i, _l in enumerate(_regiao)
                        if _l.strip() == "bash tests/portao.sh"), None)
    true(_i_portao_b is not None,
         f"{onde}, passo {passo.get('name')!r}: depois de um `reset --hard` "
         f"volta a correr-se um acto sobre a base nova — o portao tem de correr "
         f"entre os dois, senao a garantia 'este job so publica com a suite "
         f"verde' fica presa a ordem dos passos do YAML e este caminho passa "
         f"por baixo dela")
    _i_acto_reg = next((_i for _i, _l in enumerate(_regiao)
                        if acto_da_linha(_l)), None)
    true(_i_acto_reg is None or (_i_portao_b is not None
                                 and _i_portao_b < _i_acto_reg),
         f"{onde}, passo {passo.get('name')!r}: e o portao vem ANTES do primeiro "
         f"acto depois do reset (portao {_i_portao_b}, acto {_i_acto_reg})")


for _nome_wf, _wf_doc in W.items():
    for _job_id, _job_doc in (_wf_doc.get("jobs") or {}).items():
        for _p in (_job_doc.get("steps") or []):
            portao_apos_reset(_p, f"{_nome_wf}/{_job_id}")

# E a regra ve mesmo o LACO, nao so as linhas seguintes. Este e o corpo REAL do
# job da carteira como estava antes desta correcçao: o `git push` esta escrito
# ANTES do reset, e por isso a leitura recta nao via acto nenhum depois dele —
# mas na volta seguinte o push corre sobre a base por testar.
_recusa(portao_apos_reset,
        {"name": "ensaio", "run": "\n".join([
            "for i in 1 2 3; do",
            "  if git pull --rebase --autostash origin main; then",
            "    git push && exit 0",
            "  else",
            "    git reset --hard origin/main",
            "    cp /tmp/decidido.json portfolio.json",
            "    git add portfolio.json",
            "  fi",
            "  sleep 10",
            "done"])},
        "ensaio", "um laco cujo `git push` volta a correr depois do reset sem "
                  "portao — a forma exacta que a leitura recta nao via")
_recusa(portao_apos_reset,
        {"name": "ensaio", "run": "\n".join([
            "git reset --hard origin/main",
            "python fetch_data.py"])},
        "ensaio", "um reset seguido de um acto, sem laco e sem portao")
_recusa(portao_apos_reset,
        {"name": "ensaio", "run": "\n".join([
            "git reset --hard origin/main",
            "python fetch_data.py",
            "bash tests/portao.sh"])},
        "ensaio", "o portao DEPOIS do acto — o acto ja correu")
_recusa(portao_apos_reset,
        {"name": "ensaio", "run": "\n".join([
            "while true; do",
            "  git push",
            "  git reset --hard origin/main",
            "done"])},
        "ensaio", "o mesmo com `while` em vez de `for`")
# E aceita as formas correctas.
portao_apos_reset({"name": "ensaio", "run": "\n".join([
    "for i in 1 2 3; do",
    "  git push && exit 0",
    "  git reset --hard origin/main",
    "  bash tests/portao.sh",
    "  python fetch_data.py",
    "done"])}, "ensaio")
portao_apos_reset({"name": "ensaio", "run": "git reset --hard origin/main\necho feito"},
                  "ensaio")
portao_apos_reset({"name": "ensaio", "run": "echo sem reset nenhum"}, "ensaio")

# E o `-e` tem de continuar a valer: um `shell:` proprio que o retire poe o
# passo a terminar verde com a semana por publicar.
for _nome_wf, _wf_doc in W.items():
    for _job_id, _job_doc in (_wf_doc.get("jobs") or {}).items():
        _sh_default = ((_wf_doc.get("defaults") or {}).get("run") or {}).get("shell")
        _sh_job = ((_job_doc.get("defaults") or {}).get("run") or {}).get("shell")
        for _p in (_job_doc.get("steps") or []):
            _shell = _p.get("shell") or _sh_job or _sh_default
            if _shell is None:
                continue
            true("-e" in _shell or _shell in ("bash", "sh"),
                 f"{_nome_wf}/{_job_id}: o passo {_p.get('name')!r} declara "
                 f"shell {_shell!r} — tem de abortar ao primeiro erro")

for _wf, _job in (("update", "update-data"), ("friday-pipeline", "refresh-data")):
    _corpo = _passo_do_commit(_wf, _job)
    true(_corpo is not None, f"{_wf}/{_job} tem o passo do commit com o laco")
    if _corpo is None:
        continue
    # Com a chave visivel — como esta agora, ao nivel do job — o conflito e
    # recuperado e a semana e publicada.
    _rc, _remoto, _saida = _corre_laco(_corpo, com_chave=True)
    eq(_rc, 0, f"{_wf}: com a FRED_API_KEY visivel, o laco recupera do conflito "
               f"(saida: {_saida[-300:]})")
    true('"v": 7' in _remoto or '"v":7' in _remoto,
         f"{_wf}: e o ficheiro regenerado fica no remoto (obtido {_remoto!r})")

    # ── E se a BASE NOVA estiver vermelha, nao se publica ────────────────
    #
    # A garantia "este job so publica com a suite verde" estava amarrada a
    # ORDEM DOS PASSOS do YAML — mecanismo, nao propriedade. O laco faz
    # `git reset --hard origin/main` e a partir dai a arvore e o codigo que
    # estiver no remoto NESSE instante, que nao e o codigo sobre o qual a
    # suite correu. Este e o caminho construido para o dia em que os dois
    # workflows colidem no data.json, ou seja, o dia em que ele e usado.
    _rc_v, _remoto_v, _saida_v = _corre_laco(_corpo, com_chave=True,
                                             portao_do_remoto=1)
    true(_rc_v != 0,
         f"{_wf}: com a suite VERMELHA na base para onde o laco salta, o passo "
         f"morre (rc={_rc_v}, {_saida_v[-300:]})")
    true('"v": 7' not in _remoto_v and '"v":7' not in _remoto_v,
         f"{_wf}: e NADA e publicado sobre codigo por testar (remoto: "
         f"{_remoto_v!r})")

    # Tres tentativas falhadas tem de FALHAR o passo. Apagar o `exit 1` final
    # deixava o passo verde com o data.json por publicar — a falha silenciosa
    # que esta suite existe para impedir, e que nenhum cenario cobria porque o
    # unico construido era o do conflito recuperavel.
    _rc3, _, _saida3 = _corre_laco(_corpo, com_chave=True, push_sempre_recusado=True)
    true(_rc3 != 0,
         f"{_wf}: tres tentativas falhadas fazem o passo FALHAR, nao terminar "
         f"verde (saida: {_saida3[-200:]})")
    true("::error" in _saida3,
         f"{_wf}: e a falha e anunciada ao GitHub com ::error")

    # Sem a chave — o defeito de A1 — o laco morre no primeiro conflito e a
    # semana fica por publicar. E o cenario que este teste existe para impedir
    # que volte.
    _rc2, _remoto2, _ = _corre_laco(_corpo, com_chave=False)
    true(_rc2 != 0, f"{_wf}: sem a chave o laco NAO consegue recuperar, e o passo "
                    f"FALHA em vez de terminar verde (e por isso e que ela tem de "
                    f"estar ao nivel do job)")
    true('"v":99' in _remoto2,
         f"{_wf}: e o remoto fica com a versao do outro cliente, sem a desta corrida")

# ── O build_data le e escreve pelo MESMO sitio ──────────────────────────────
#
# Quatro auditorias seguidas encontraram a mesma familia: um teste-portao a
# derivar uma expectativa de estado que o proprio sistema reescreve. A forma
# mais dificil de ver nao aparecia no teste nenhum: o `fetch_data` escrevia o
# data.json pelo `__file__` mas lia a publicacao anterior por caminho RELATIVO
# ao directorio corrente, e por isso um teste que redirigisse o `__file__` para
# um temporario — como a suite faz — continuava a ler o data_prev.json do
# repositorio. O job da newsletter roda esse ficheiro em todas as sextas bem
# sucedidas: o portao partia-se na segunda sexta publicada, e nao havia um
# `ROOT / "data_prev.json"` para procurar.
#
# A primeira tentativa de fechar isto foi uma regra sobre o texto dos testes —
# "quem corre o motor tem de mudar de directorio ou declarar a publicacao
# anterior" — verificada por presenca de substring no ficheiro. Nao servia:
# bastava a declaracao existir noutro teste do mesmo ficheiro, ou o modulo ser
# importado com outro nome, para a regra dar verde sobre o defeito. Uma regra
# assim e uma quarta copia da memoria, nao uma verificacao.
#
# O que se verifica agora e a PROPRIEDADE, no produtor: com o `__file__` do
# fetch_data apontado para um directorio vazio, a publicacao anterior tem de vir
# vazia — venha de onde vier o processo. Fechada aqui, a assimetria deixa de
# poder reaparecer em qualquer teste.
import fetch_data as _fd_sim

_dir_vazio = Path(tempfile.mkdtemp())
_guardado_file = _fd_sim.__file__
_cwd_antes = os.getcwd()
try:
    _fd_sim.__file__ = str(_dir_vazio / "fetch_data.py")
    os.chdir(ROOT)                      # o directorio corrente TEM data_prev.json
    _m, _s, _stamp = _fd_sim.load_previous_metrics()
    eq((_m, _s, _stamp), ({}, {}, None),
       "com o fetch_data apontado para um directorio vazio, a publicacao "
       "anterior vem vazia mesmo com o directorio corrente a ser o repositorio "
       "— o build_data le e escreve pelo mesmo sitio")
    # E ao lado do data.json, le mesmo: a simetria nao pode ter sido fechada
    # transformando a leitura em codigo morto.
    (_dir_vazio / "data_prev.json").write_text(json.dumps(
        {"pillars": [{"id": "cycle", "metricValue": 0.42, "score": 5.0}],
         "meta": {"lastUpdated": "2026-09-04T18:00:00Z"}}), encoding="utf-8")
    _m2, _s2, _stamp2 = _fd_sim.load_previous_metrics()
    eq((_m2, _s2, _stamp2), ({"cycle": 0.42}, {"cycle": 5.0}, "2026-09-04T18:00:00Z"),
       "e le a publicacao que estiver ao lado do data.json que vai escrever")
finally:
    os.chdir(_cwd_antes)
    _fd_sim.__file__ = _guardado_file
    shutil.rmtree(_dir_vazio, ignore_errors=True)

# E a propriedade vale para TODOS os ficheiros do repositorio que o fetch_data
# toca, nao so para a publicacao anterior: o data.json que escreve e o
# score_history.json que le. A assimetria fechada num sitio volta pelo seguinte
# ficheiro que la entre.
_fonte_fd = (ROOT / "fetch_data.py").read_text(encoding="utf-8")
for _ficheiro in ("data.json", "data_prev.json", "score_history.json"):
    _usos = [l.strip() for l in _fonte_fd.splitlines()
             if f'"{_ficheiro}"' in l and "#" not in l.split(f'"{_ficheiro}"')[0]]
    true(_usos, f"o fetch_data nomeia o {_ficheiro}")
    for _l in _usos:
        true("os.path.join" in _l or "os.path.dirname" in _l or "path=None" in _l
             or "shutil.copy" in _l,
             f"o fetch_data resolve o {_ficheiro} pelo seu proprio directorio, "
             f"nao pelo directorio corrente (linha: {_l!r})")

print(f"TODOS OS {ok} TESTES PASSARAM")
