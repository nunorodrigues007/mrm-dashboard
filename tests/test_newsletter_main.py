"""
Smoke test do send_newsletter.main() — o caminho completo do job 2 do pipeline.

Os testes do prompt cobrem funcoes puras; este cobre a corrida inteira: gravar a
newsletter, inserir o cartao no arquivo do index.html, copiar o data_prev.json,
preparar o commit e enviar. Rede, git e o modelo sao substituidos.

Cenario escolhido: a semana em que o medidor B dispara — a que mais importa que
funcione, e a que a versao anterior contava ao contrario.
"""
import contextlib, importlib.util, io, json, os, shutil, sys, tempfile, types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("sn", ROOT / "send_newsletter.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)
import mrm_rules as rules

# O `git_publish` verdadeiro, guardado ANTES de qualquer teste lhe pôr um duplo
# por cima: ha uma verificacao mais abaixo que precisa de correr a funcao a
# serio para ler o que ela escreve no log.
_GIT_PUBLISH_REAL = sn.git_publish
# Capturados ANTES de qualquer substituicao: os testes deste ficheiro trocam
# `sn.brevo_send` por dezenas de duplos, e uma sonda que queira exercitar o
# CAMINHO REAL tem de guardar a referencia aqui em cima.
_BREVO_SEND_REAL = sn.brevo_send
_SEND_TO_EACH_REAL = sn.send_to_each

ok = 0
def true(cond, what):
    global ok
    assert cond, what
    ok += 1

# ── o relogio ────────────────────────────────────────────────────────────────
#
# Este teste copia o portfolio.json COMMITADO e chama o sn.main(). O main()
# calcula a edicao a partir de `datetime.utcnow()`, e o build_context recusa —
# de proposito — publicar uma carteira que nao seja a dessa edicao.
#
# Com o relogio real, isso torna o teste um calendario: de segunda a quinta o
# portfolio commitado e o da edicao corrente e passa; a partir das 00:00 UTC de
# SEXTA o numero incrementa, o portfolio ainda e o da semana anterior, e o teste
# FALHA. E como esta suite e o portao dos dois jobs de sexta, isso nao daria um
# teste vermelho: daria uma semana sem carteira e sem newsletter, todas as
# semanas, a partir das 22:00 UTC de sexta — que e precisamente a hora a que o
# pipeline corre.
#
# O relogio passa a ser o da carteira que se esta a usar — mas a SEXTA dessa
# carteira, nao a data que ela tem gravada.
#
# `current.date` e o ultimo dia de NEGOCIACAO da semana. Dez vezes ate 2032 a
# bolsa esta fechada a sexta — o Natal de 2026, o Ano Novo de 2027, a
# Sexta-feira Santa todos os anos — e nessas semanas o motor data a carteira de
# QUINTA. Como o numero da edicao conta semanas desde 13 de Marco de 2026, uma
# quinta cai na semana anterior: `issue_number_for(current.date)` dava uma
# edicao a menos do que a que o portfolio.json tem, e o `build_context` — que
# existe precisamente para recusar publicar a carteira da semana errada —
# levantava.
#
# A consequencia nao era uma semana perdida: era o fim. O job 1 escreve a data
# de quinta, o job 2 morre no portao, e na sexta seguinte o job 1 ja nem chega a
# decidir, porque o portao corre antes. O portfolio.json ficava congelado na
# quinta e o pipeline nao se recuperava sozinho.
#
# A sexta sai do NUMERO da edicao, que e o que nunca depende do calendario.
_PF_HOJE = json.load(open(ROOT / "portfolio.json"))["current"]
HOJE = sn.START_DATE + timedelta(weeks=_PF_HOJE["issue"] - 1)
assert HOJE.weekday() == 4, f"a sexta da edicao {_PF_HOJE['issue']} e {HOJE}"

class _DataHora(datetime):
    @classmethod
    def utcnow(cls):
        return datetime(HOJE.year, HOJE.month, HOJE.day, 22, 5)

sn.datetime = _DataHora
ISSUE_HOJE = sn.issue_number_for(HOJE)
assert ISSUE_HOJE == _PF_HOJE["issue"], (
    f"a sexta escolhida ({HOJE}) tem de ser a da edicao que o portfolio.json "
    f"tem ({_PF_HOJE['issue']}), nao a edicao {ISSUE_HOJE}")
CARIMBO = datetime(HOJE.year, HOJE.month, HOJE.day, 18, 0).strftime("%Y-%m-%dT%H:%M:%SZ")


def prepara(tmp):
    """Copia os ficheiros de estado para `tmp`, com o carimbo do data.json no
    relogio simulado.

    Sem isto, a idade do data.json era medida contra a data da carteira: os dois
    ficheiros sao escritos na mesma corrida e enquanto avancarem juntos passa,
    mas basta uma sexta em que so um deles seja commitado para o gerador
    acrescentar o aviso de qualidade de dados, o fixture do modelo nao o levar,
    e a validacao reprovar — com este ficheiro a ser portao do job da
    newsletter. A idade do data.json tem os seus proprios testes; aqui o
    assunto e o gerador.
    """
    for f in ("data.json", "portfolio.json", "index.html"):
        shutil.copy(ROOT / f, tmp)
    _d = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    _d.setdefault("meta", {})["generatedAt"] = CARIMBO
    for nome in ("data.json", "data_prev.json"):
        (tmp / nome).write_text(json.dumps(_d), encoding="utf-8")
    return tmp

tmp = prepara(Path(tempfile.mkdtemp()))

d = json.load(open(tmp / "data.json"))
d["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                    "label": "Stress ON — No Relief",
                    "triggers": {"sahmRealtime": {"value": 0.62, "threshold": 0.5, "asOf": "2026-08-01"},
                                 "delinquencyAccel": {"value": 0.9, "threshold": 0.81, "asOf": "2026-04-01"}}}
d["rules"] = rules.as_dict()
json.dump(d, open(tmp / "data.json", "w"))

pf = json.load(open(tmp / "portfolio.json"))
pf["current"].update({"regime": "Critical", "critical_subregime": "Critical_Stress",
                      "active_etf_map": dict(rules.REGIME_ETF_MAP["Critical_Stress"]),
                      "bucket_allocation_pct": dict(rules.CRITICAL_WEIGHTS["Critical_Stress"])})
pf["history"][-1].update({"rebalance_reason": "stress_on", "rebalance_triggered": True})
json.dump(pf, open(tmp / "portfolio.json", "w"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_newsletter import edicao, edicao_do_prompt

sent, prompts = [], []
def fake_model(prompt, key):
    prompts.append(prompt)
    # Uma edicao plausivel, nao um fragmento: o send_newsletter passou a validar
    # o que o modelo devolve antes de gravar, publicar ou enviar. E o score e os
    # avisos saem do PROMPT, como sairiam de um modelo obediente: com eles
    # fixos, este portao dependia de o data.json commitado dizer 6,97 e de nao
    # haver nada a avisar.
    return edicao_do_prompt(prompt, ISSUE_HOJE)
sn.call_model = fake_model
def _publica_ok(files, msg, **kw):
    sent.append(("git", list(files), msg))
    return True
sn.git_publish = _publica_ok
sn.brevo_subscribers = lambda key: ["a@example.com", "b@example.com"]
# `to` e agora um endereco unico, nao uma lista: brevo_send envia um a um.
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    sent.append(("mail", to, subject)), (True, types.SimpleNamespace(status_code=200)))[1]

os.environ["ANTHROPIC_API_KEY"] = "not-used"
os.environ["BREVO_API_KEY"] = "not-used"
cwd = os.getcwd()
os.chdir(tmp)
try:
    sn.main()
finally:
    os.chdir(cwd)

files = sorted(p.name for p in tmp.glob("MRM_Newsletter_Issue*.html"))
idx = (tmp / "index.html").read_text(encoding="utf-8")
mails = [m for m in sent if m[0] == "mail"]
gits = [g for g in sent if g[0] == "git"]

true(len(files) == 1, "gravou exactamente uma newsletter")
true(files[0] in idx, "inseriu o cartao da edicao no arquivo do index.html")
true("ISSUE #" in idx, "o cartao tem o numero da edicao")
true(json.load(open(tmp / "data_prev.json")).get("stressGauge", {}).get("active") is True,
     "data_prev.json ficou com o estado desta semana para o WoW da proxima")
# Dois commits, por ordem: primeiro a edicao e o arquivo, depois — so depois de
# o envio ter tido sucesso — a rotacao do data_prev. Rodar antes do envio fazia
# com que uma re-corrida encontrasse prev == data e publicasse todos os "vs.
# semana anterior" a zero.
true(len(gits) == 3, f"tres commits: publicacao, marca de envio e rotacao (obtido {len(gits)})")
true(set(gits[0][1]) == {files[0], "index.html"},
     f"o primeiro leva a edicao e o arquivo (obtido {gits[0][1]})")
# A marca vai SOZINHA e a seguir ao envio: e o unico ficheiro cujo push tem de
# sobreviver ao runner, porque e ele que impede o reenvio na proxima corrida.
true(gits[1][1] == ["sent_issues.json"],
     f"o segundo publica a marca de envio, sozinha (obtido {gits[1][1]})")
true(set(gits[2][1]) == {"data_prev.json"},
     f"o terceiro roda o data_prev (obtido {gits[2][1]})")
# A marca e escrita ANTES de qualquer coisa pos-envio, e recusa a re-corrida.
marca = json.load(open(tmp / "sent_issues.json"))
true(any(e["issue"] == ISSUE_HOJE for e in marca["sent"]),
     f"a edicao enviada fica registada em sent_issues.json ({marca})")
true(marca["sent"][-1]["recipients"] == 2, "com o numero de destinatarios servidos")

# Re-corrida: nao gera, nao publica, nao envia.
antes = len(sent)
os.chdir(tmp)
try:
    sn.main()
finally:
    os.chdir(cwd)
true(len(sent) == antes, f"a re-corrida nao envia nem publica nada (novos: {sent[antes:]})")
true(len(prompts) == 1, "e nem sequer pede uma edicao nova ao modelo")
i_envio = next(i for i, s in enumerate(sent) if s[0] == "mail")
i_rot   = next(i for i, s in enumerate(sent) if s[0] == "git" and "data_prev.json" in s[1])
i_marca = next(i for i, s in enumerate(sent) if s[0] == "git" and "sent_issues.json" in s[1])
true(i_envio < i_marca < i_rot,
     "a marca e publicada depois do envio e ANTES da rotacao do data_prev")
true(i_envio < i_rot, "a rotacao acontece DEPOIS do envio, nao antes")
# Uma mensagem POR PESSOA, mais o briefing do dono. A versao anterior punha
# todos os subscritores num unico `to:`, e cada um recebia no cabecalho o
# endereco de todos os outros — exposicao de dados pessoais, nao estilo.
true(len(mails) == 3, f"duas newsletters (uma por subscritor) e o briefing (obtido {len(mails)})")
true("Critical · No Relief" in mails[0][2], "o assunto leva o regime operativo, nao o do score")

def destinatarios(m):
    to = m[1]
    return list(to) if isinstance(to, (list, tuple)) else [to]

for m in mails:
    true(len(destinatarios(m)) == 1,
         f"nenhuma mensagem leva mais do que um destinatario (levava {destinatarios(m)})")
enviados_para = sorted(destinatarios(m)[0] for m in mails[:2])
true(enviados_para == ["a@example.com", "b@example.com"],
     f"cada subscritor recebeu a sua (obtido {enviados_para})")
true(destinatarios(mails[2])[0] == "usmrm@proton.me", "briefing do dono")

# e a funcao recusa explicitamente uma lista com varios
recusou = False
try:
    sn.brevo_send.__wrapped__ if False else None
    import send_newsletter as _sn_real
    _sn_real.brevo_send("k", {}, ["a@x.com", "b@x.com"], "s", "h")
except ValueError as e:
    recusou = "um destinatario de cada vez" in str(e)
except Exception:
    pass
true(recusou, "brevo_send recusa uma lista com varios destinatarios")

p = prompts[0]
true("Operative regime: Critical · No Relief" in p, "o modelo recebeu o regime real")
true("Rebalance outcome: STRESS_ON" in p, "o modelo recebeu o motivo real")
true("USMV | SHY | SGOV | GLD | BIL | VNQ" in p, "o modelo recebeu os instrumentos reais")
true("No structural regime change detected" not in p, "a frase legada nao voltou")

shutil.rmtree(tmp, ignore_errors=True)

# ── Cenario 2: o modelo devolve uma edicao truncada ──────────────────────────
# Antes desta versao isto passava sem uma palavra: o fragmento era gravado,
# commitado para o site e enviado a todos os subscritores — e era ele que o
# motor lia no sabado seguinte para decidir a carteira. Agora a semana falha em
# alto e bom som e nada sai.
tmp2 = prepara(Path(tempfile.mkdtemp()))
idx_antes = (tmp2 / "index.html").read_text(encoding="utf-8")

sent2, tentativas = [], []
def modelo_truncado(prompt, key):
    tentativas.append(prompt)
    return edicao_do_prompt(prompt, ISSUE_HOJE, fechar=False)
sn.call_model = modelo_truncado
def _publica_ok2(files, msg, **kw):
    sent2.append(("git", list(files), msg))
    return True
sn.git_publish = _publica_ok2
sn.brevo_subscribers = lambda key: ["a@example.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    sent2.append(("mail", to, subject)), (True, types.SimpleNamespace(status_code=200)))[1]

os.chdir(tmp2)
recusou2 = ""
try:
    sn.main()
except RuntimeError as e:
    recusou2 = str(e)
finally:
    os.chdir(cwd)

true("NAO foi publicada nem enviada" in recusou2,
     f"main() recusa a edicao truncada (obtido {recusou2[:120]!r})")
true(len(tentativas) == sn.NEWSLETTER_TENTATIVAS,
     f"tentou {sn.NEWSLETTER_TENTATIVAS} vezes antes de desistir (obtido {len(tentativas)})")
true("REJEITADA" in tentativas[1],
     "a segunda tentativa leva os problemas da primeira no prompt")
true(not list(tmp2.glob("MRM_Newsletter_Issue*.html")),
     "nao gravou nenhum ficheiro de edicao")
true((tmp2 / "index.html").read_text(encoding="utf-8") == idx_antes,
     "nao mexeu no arquivo do index.html")
true(sent2 == [], f"nao fez commit nem enviou nada (obtido {sent2})")
shutil.rmtree(tmp2, ignore_errors=True)

# ── Cenario 3: o push DEPOIS do envio falha ──────────────────────────────
# Os subscritores ja receberam. Uma excepcao a partir daqui punha o job
# vermelho, o operador re-corria o pipeline, e a segunda passagem gerava uma
# edicao NOVA e enviava a lista toda outra vez. O publish pos-envio tem de ser
# nao-fatal, e a marca tem de ficar gravada na mesma.
tmp3 = prepara(Path(tempfile.mkdtemp()))

sent3 = []
sn.call_model = lambda prompt, key: edicao_do_prompt(prompt, ISSUE_HOJE)
def publish_que_falha(files, msg, **kw):
    sent3.append(("git", list(files), msg))
    # O publish pos-envio TEM de vir com fatal=False. Se vier fatal, esta
    # simulacao rebenta — que e exactamente o que acontecia em producao.
    if "data_prev.json" in files and kw.get("fatal", True):
        raise RuntimeError("push recusado (simulado)")
    return False   # nada foi publicado
sn.git_publish = publish_que_falha
sn.brevo_subscribers = lambda key: ["a@example.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    sent3.append(("mail", to, subject)), (True, types.SimpleNamespace(status_code=200)))[1]

os.chdir(tmp3)
rebentou3 = None
try:
    sn.main()
except BaseException as e:      # SystemExit nao e Exception
    rebentou3 = e
finally:
    os.chdir(cwd)

# main() NAO rebenta a meio: envia tudo, escreve a marca, faz o briefing — e so
# no fim sai com erro, para o job ficar vermelho e alguem olhar. Um job verde
# com a marca por publicar era o caminho directo para o reenvio da semana
# seguinte.
true(isinstance(rebentou3, SystemExit),
     f"main() sai com erro quando a marca nao fica publicada (obtido {rebentou3!r})")
true("marca de envio NAO foi publicada" in str(rebentou3),
     f"e a mensagem diz exactamente o que aconteceu ({rebentou3})")
true("reenvia a toda a gente" in str(rebentou3),
     "e o que acontece se se voltar a correr sem corrigir")
true((tmp3 / "sent_issues.json").exists(),
     "e a marca de envio fica gravada em disco, apesar do push falhado")
# A marca vai sozinha e primeiro: e o unico ficheiro cujo push tem de sobreviver.
_gits3 = [g for g in sent3 if g[0] == "git"]
_i_marca = next(i for i, g in enumerate(_gits3) if "sent_issues.json" in g[1])
_i_prev  = next(i for i, g in enumerate(_gits3) if "data_prev.json" in g[1])
true(_gits3[_i_marca][1] == ["sent_issues.json"],
     f"a marca e publicada sozinha ({_gits3[_i_marca][1]})")
true(_i_marca < _i_prev, "e antes do data_prev")
true(len([m for m in sent3 if m[0] == "mail"]) == 2,
     f"uma newsletter e o briefing sairam ({sent3})")
# E a re-corrida a seguir NAO reenvia.
antes3 = len(sent3)
os.chdir(tmp3)
try:
    sn.main()
finally:
    os.chdir(cwd)
true(len(sent3) == antes3,
     f"a re-corrida depois do push falhado nao reenvia (novos: {sent3[antes3:]})")
shutil.rmtree(tmp3, ignore_errors=True)

# ── update_archive: substituir o cartao MAIS ANTIGO nao come o ficheiro ───
# A fronteira da substituicao era `\Z` — o fim do documento. Se o cartao a
# substituir fosse o ultimo, a expressao apagava tudo o que vinha a seguir:
# hoje, 270 linhas incluindo todo o JavaScript da pagina. Nunca disparou porque
# as edicoes sao inseridas da mais recente para a mais antiga, mas nao e disso
# que uma pagina deve depender.
tmp4 = Path(tempfile.mkdtemp())
shutil.copy(ROOT / "index.html", tmp4)
_antes = (tmp4 / "index.html").read_text(encoding="utf-8")
sn.update_archive(str(tmp4 / "index.html"), "<!-- ISSUE #1 --><div>substituido</div>", 1)
_dep = (tmp4 / "index.html").read_text(encoding="utf-8")
true("VIEW: PORTFOLIO" in _dep,
     "substituir o cartao mais antigo nao apaga o resto da pagina")
true(_dep.count("</script>") == _antes.count("</script>"),
     f"o JavaScript da pagina fica intacto "
     f"({_dep.count('</script>')} vs {_antes.count('</script>')})")
true(_dep.count("<!-- ISSUE #1 -->") == 1, "e o cartao e substituido, nao duplicado")
true("substituido" in _dep, "com o conteudo novo")
true(len(_antes) - len(_dep) < 4000,
     f"e so o cartao antigo desapareceu, nao um terco do ficheiro "
     f"(perdeu {len(_antes) - len(_dep)} caracteres)")
shutil.rmtree(tmp4, ignore_errors=True)

# ── Cenario 4: o registo de envios existe mas esta ilegivel ───────────────
# Um push interrompido deixa o ficheiro truncado; um merge mal resolvido deixa
# marcadores de conflito. "Ilegivel" nao quer dizer "nao foi enviada" — quer
# dizer que nao se sabe, e engolir a excepcao fazia o guarda contra o reenvio
# desaparecer em silencio (e o mark_sent a seguir apagava o historico todo).
for _corrompido in ('{"sent": [{"issue": 26, "recipie',
                    '<<<<<<< HEAD\n{"sent": []}\n=======\n',
                    '{"sent": "nao e uma lista"}',
                    'nao e json de todo'):
    tmp5 = prepara(Path(tempfile.mkdtemp()))
    (tmp5 / "sent_issues.json").write_text(_corrompido, encoding="utf-8")
    sent5 = []
    sn.call_model = lambda prompt, key: edicao_do_prompt(prompt, ISSUE_HOJE)
    sn.git_publish = lambda files, msg, **kw: (sent5.append(("git", list(files), msg)), True)[1]
    sn.brevo_subscribers = lambda key: ["a@example.com"]
    sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
        sent5.append(("mail", to, subject)), (True, types.SimpleNamespace(status_code=200)))[1]
    os.chdir(tmp5)
    erro5 = None
    try:
        sn.main()
    except BaseException as e:
        erro5 = e
    finally:
        os.chdir(cwd)
    true(isinstance(erro5, sn.MarcaIlegivel),
         f"um registo ilegivel ({_corrompido[:24]!r}) para o envio (obtido {erro5!r})")
    true(sent5 == [], f"e nada e publicado nem enviado (obtido {sent5})")
    true((tmp5 / "sent_issues.json").read_text(encoding="utf-8") == _corrompido,
         "e o ficheiro nao e reescrito por cima — o historico nao se perde")
    shutil.rmtree(tmp5, ignore_errors=True)

# Um registo VALIDO mas sem esta edicao segue normalmente; e a ausencia do
# ficheiro tambem (primeira corrida de sempre).
tmp6 = prepara(Path(tempfile.mkdtemp()))
(tmp6 / "sent_issues.json").write_text('{"sent": [{"issue": 1, "recipients": 3}]}', encoding="utf-8")
sent6 = []
sn.git_publish = lambda files, msg, **kw: (sent6.append(("git", list(files), msg)), True)[1]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    sent6.append(("mail", to, subject)), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp6)
try:
    sn.main()
finally:
    os.chdir(cwd)
true(any(m[0] == "mail" for m in sent6), "um registo valido de outra edicao nao trava esta")
_reg6 = json.load(open(tmp6 / "sent_issues.json"))
true([e["issue"] for e in _reg6["sent"]] == [1, ISSUE_HOJE],
     f"e a entrada anterior e preservada ({_reg6})")
shutil.rmtree(tmp6, ignore_errors=True)

# ── Cenario 5: a re-corrida REAL — runner limpo, so o que esta no git ────
# Os cenarios anteriores re-corriam no mesmo tmpdir, logo viam o ficheiro local.
# Em GitHub Actions cada corrida faz checkout limpo de `main`: o que ficou so no
# disco do runner anterior nao existe. E por isso que a marca tem de ser
# PUBLICADA, e nao apenas escrita — e este teste modela isso, copiando para o
# runner novo apenas o que o "repositorio" recebeu.
def runner(ficheiros_no_repo, falhar_push_da_marca=False):
    """Uma corrida num directorio novo, com so o que esta 'no repositorio'."""
    d = Path(tempfile.mkdtemp())
    for nome, conteudo in ficheiros_no_repo.items():
        (d / nome).write_text(conteudo, encoding="utf-8")
    reg, envios = dict(ficheiros_no_repo), []
    def _pub(files, msg, **kw):
        if falhar_push_da_marca and "sent_issues.json" in files:
            return False                      # o push nao chegou ao repositorio
        for f in files:                       # o que passa fica "no repositorio"
            if (d / f).exists():
                reg[f] = (d / f).read_text(encoding="utf-8")
        return True
    sn.call_model = lambda prompt, key: edicao_do_prompt(prompt, ISSUE_HOJE)
    sn.git_publish = _pub
    sn.brevo_subscribers = lambda key: ["a@example.com", "b@example.com"]
    sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
        envios.append(to), (True, types.SimpleNamespace(status_code=200)))[1]
    os.chdir(d)
    erro = None
    try:
        sn.main()
    except BaseException as e:
        erro = e
    finally:
        os.chdir(cwd)
    shutil.rmtree(d, ignore_errors=True)
    return reg, envios, erro

# O data.json entra com o carimbo no relogio simulado, pela mesma razao que o
# `prepara` acima: a idade do data.json tem os seus proprios testes, e aqui o
# assunto e a marca de envio.
_d_repo = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
_d_repo.setdefault("meta", {})["generatedAt"] = CARIMBO
_repo = {"data.json": json.dumps(_d_repo),
         "portfolio.json": (ROOT / "portfolio.json").read_text(encoding="utf-8"),
         "index.html": (ROOT / "index.html").read_text(encoding="utf-8")}
_repo["data_prev.json"] = _repo["data.json"]

# Corrida 1 com o push da marca a funcionar; corrida 2 num runner novo.
_repo1, _envios1, _erro1 = runner(dict(_repo))
true(len(_envios1) == 3, f"a primeira corrida envia (obtido {_envios1})")
true(_erro1 is None, f"e termina limpa (obtido {_erro1!r})")
true("sent_issues.json" in _repo1, "a marca chegou ao repositorio")
_repo2, _envios2, _erro2 = runner(_repo1)
true(_envios2 == [],
     f"o runner seguinte, com checkout limpo, NAO reenvia (obtido {_envios2})")

# E agora o caso que importa: o push da marca falha. O job tem de ficar
# vermelho — se ficasse verde, ninguem saberia, e a re-corrida reenviava.
_repo3, _envios3, _erro3 = runner(dict(_repo), falhar_push_da_marca=True)
true(len(_envios3) == 3, "a edicao e enviada na mesma")
true(isinstance(_erro3, SystemExit),
     f"mas o job termina em ERRO, para o alerta disparar (obtido {_erro3!r})")
true("sent_issues.json" not in _repo3,
     "e a marca nao chegou ao repositorio — que e exactamente o que se esta a sinalizar")

# ── Cenario 6: entrega PARCIAL — retoma-se, nao se repete ────────────────
# Com 400 servidos e 100 falhados, a marca dizia so "enviada" e a unica saida
# documentada (apagar a entrada) reenviava aos 400. Agora fica registado a quem
# chegou, e a corrida seguinte serve apenas os que faltam — com A MESMA edicao,
# nao com uma nova, porque quem a escreve e um modelo.
_LISTA = ["a@x.com", "b@x.com", "c@x.com", "d@x.com"]
_falham = {"c@x.com", "d@x.com"}

def corre_parcial(d, falham, geracoes):
    envios = []
    def _send(key, sender, to, subject, html, **kw):
        if to in falham:
            return (False, types.SimpleNamespace(status_code=500))
        envios.append((to, html))
        return (True, types.SimpleNamespace(status_code=200))
    def _gera(prompt, key):
        geracoes.append(1)
        return edicao_do_prompt(prompt, ISSUE_HOJE,
                                enchimento=12000 + 100 * len(geracoes))   # texto diferente a cada vez
    sn.call_model = _gera
    sn.git_publish = lambda files, msg, **kw: True
    sn.brevo_subscribers = lambda key: list(_LISTA)
    sn.brevo_send = _send
    os.chdir(d)
    erro = None
    try:
        sn.main()
    except BaseException as e:
        erro = e
    finally:
        os.chdir(cwd)
    return envios, erro

tmp7 = prepara(Path(tempfile.mkdtemp()))
_ger = []
_env1, _err1 = corre_parcial(tmp7, _falham, _ger)
_dest1 = [e[0] for e in _env1 if e[0] in _LISTA]
true(sorted(_dest1) == ["a@x.com", "b@x.com"],
     f"a primeira passagem serve os dois que aceitam ({_dest1})")
_reg7 = json.load(open(tmp7 / "sent_issues.json"))["sent"][-1]
true(_reg7["complete"] is False, f"e a edicao fica marcada como INCOMPLETA ({_reg7})")
# O registo guarda DIGESTS, nunca enderecos: o sent_issues.json e commitado e
# empurrado para main, e a raiz do ramo e servida em usmrm.net — gravar aqui a
# lista publicava-a no site e deixava-a no historico do git para sempre.
true(sorted(_reg7["served"]) == sn.marcas(["a@x.com", "b@x.com"]),
     f"com a marca de quem recebeu ({_reg7['served']})")
true(not any("@" in x for x in _reg7["served"]),
     f"e nenhum endereco vai para o ficheiro publicado ({_reg7['served']})")
true(sorted(_reg7["failed"]) == sn.marcas(["c@x.com", "d@x.com"]), "e de quem falhou")
true(not any("@" in x for x in _reg7.get("failed", []) + _reg7.get("pending", [])),
     "e tambem nao vao enderecos nas listas de falhados e por servir")

# Segunda passagem, com a Brevo ja boa: so os que faltam, e a MESMA edicao.
_env2, _err2 = corre_parcial(tmp7, set(), _ger)
_dest2 = [e[0] for e in _env2 if e[0] in _LISTA]
true(sorted(_dest2) == ["c@x.com", "d@x.com"],
     f"a segunda passagem serve so quem faltava ({_dest2})")
true(len(_ger) == 1, f"e NAO gera uma edicao nova ({len(_ger)} geracoes)")
_html1 = next(h for t, h in _env1 if t == "a@x.com")
_html2 = next(h for t, h in _env2 if t == "c@x.com")
true(_html1 == _html2, "quem recebeu depois recebeu exactamente a mesma edicao")
_reg7b = json.load(open(tmp7 / "sent_issues.json"))["sent"][-1]
true(_reg7b["complete"] is True, f"e agora a edicao fica completa ({_reg7b})")
true(sorted(_reg7b["served"]) == sn.marcas(_LISTA), "com os quatro servidos")

# Terceira passagem: completa, nao faz nada.
_env3, _err3 = corre_parcial(tmp7, set(), _ger)
true([e[0] for e in _env3 if e[0] in _LISTA] == [],
     "a terceira passagem nao envia a ninguem")
shutil.rmtree(tmp7, ignore_errors=True)

# ── O orcamento de tempo do envio ────────────────────────────────────────
# Cada destinatario falhado custa ate 210 s. Sem orcamento, o GitHub matava o
# job A MEIO do envio — depois de centenas de mensagens saidas e ANTES da marca
# ser gravada — e a re-corrida reenviava a quem ja tinha recebido.
_t = {"agora": 0.0}
def _relogio():
    return _t["agora"]
def _send_lento(key, sender, to, subject, html, **kw):
    _t["agora"] += 100.0
    return (True, types.SimpleNamespace(status_code=200))
sn.brevo_send = _send_lento
_marcas = []
_env, _fal, _rest = sn.send_to_each(
    "k", {"name": "x", "email": "x@x.com"}, [f"{i}@x.com" for i in range(10)],
    "assunto", "<html></html>", orcamento_s=350,
    marcar=lambda e, f, r: _marcas.append((len(e), len(r))), relogio=_relogio)
true(len(_env) == 4, f"para quando o orcamento acaba (enviou {len(_env)} de 10)")
true(len(_rest) == 6, f"e diz quem falta ({len(_rest)})")
true(_marcas and _marcas[-1] == (len(_env), len(_rest)),
     f"e a marca final regista o estado real ({_marcas[-1] if _marcas else None})")
# Sem orcamento apertado, envia a todos.
_t["agora"] = 0.0
_env, _fal, _rest = sn.send_to_each(
    "k", {"name": "x", "email": "x@x.com"}, [f"{i}@x.com" for i in range(10)],
    "assunto", "<html></html>", orcamento_s=100000, relogio=_relogio)
true(len(_env) == 10 and _rest == [], "com tempo, envia a todos")

# ── O job morto A MEIO do envio deixa rasto ──────────────────────────────
# E o cenario que o orcamento tenta evitar e que a marca incremental tem de
# sobreviver: se o processo desaparecer no meio da lista, o que ja saiu tem de
# estar registado, senao a corrida seguinte reenvia a quem ja recebeu.
tmp8 = Path(tempfile.mkdtemp())
_alvos = [f"{i}@x.com" for i in range(6)]
_gravados = []
def _marca_em_disco(env, fal, rest):
    (tmp8 / "marca.json").write_text(json.dumps({"served": sn.marcas(env),
                                                 "pending": sn.marcas(rest)}))
    _gravados.append(len(env))
def _send_que_morre(key, sender, to, subject, html, **kw):
    if to == "3@x.com":
        raise KeyboardInterrupt("o runner foi morto")
    return (True, types.SimpleNamespace(status_code=200))
sn.brevo_send = _send_que_morre
morreu = None
try:
    sn.send_to_each("k", {"name": "x", "email": "x@x.com"}, _alvos, "s", "<html></html>",
                    marcar=_marca_em_disco)
except BaseException as e:
    morreu = e
true(isinstance(morreu, KeyboardInterrupt), "o envio foi mesmo interrompido a meio")
_m8 = json.loads((tmp8 / "marca.json").read_text())
true(_m8["served"] == sn.marcas(["0@x.com", "1@x.com", "2@x.com"]),
     f"e ficou gravado a quem ja tinha chegado ({_m8['served']})")
true(sn.marca_destinatario("3@x.com") in _m8["pending"]
     and sn.marca_destinatario("5@x.com") in _m8["pending"],
     f"e quem falta ({_m8['pending']})")

# ── E o LOG da corrida tambem nao leva um unico endereco ──────────────────
#
# Toda a limpeza do sent_issues.json existe porque a raiz de `main` e servida
# em usmrm.net e o historico do git e para sempre. Mas o envio imprimia o
# endereco em claro a cada falha — uma linha por tentativa e uma no fim —, e o
# log da corrida do GitHub Actions e igualmente publico num repositorio publico
# (que e o que Pages com dominio proprio implica). O RUNBOOK manda o operador
# ABRIR esse log. Numa avaria da Brevo — rate-limit, 5xx: exactamente o caso
# que as tentativas existem para tratar — saia a lista inteira.
_lista_log = ["ana@exemplo.pt", "bruno@exemplo.pt", "carlos@exemplo.pt"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    False, types.SimpleNamespace(status_code=500))
_saida_log = io.StringIO()
with contextlib.redirect_stdout(_saida_log):
    sn.send_to_each("k", {"name": "x", "email": "x@x.com"}, _lista_log,
                    "s", "<html></html>", orcamento_s=100000)
_texto_log = _saida_log.getvalue()
true("@" not in _texto_log,
     f"nenhum endereco sai no log da corrida, nem numa avaria da Brevo "
     f"({_texto_log[:300]})")
for _nome_log in ("ana", "bruno", "carlos", "exemplo"):
    true(_nome_log not in _texto_log,
         f"nem o nome de {_nome_log} ({_texto_log[:200]})")
true(sn.marca_destinatario("ana@exemplo.pt") in _texto_log,
     f"e o log continua a identificar QUEM falhou, pela marca ({_texto_log[:300]})")
# E a tentativa intermedia do `brevo_send` — a que corre antes do ultimo erro —
# tambem nao. E ela que sai uma vez por retry.
_t_retry = {"n": 0}
def _post_falha(url, headers=None, json=None, timeout=None):
    _t_retry["n"] += 1
    return types.SimpleNamespace(status_code=500, text="erro")
_guardado_post = sn.requests.post
sn.requests.post = _post_falha
_guardado_sleep = sn.time.sleep
sn.time.sleep = lambda s_: None
_saida_retry = io.StringIO()
try:
    with contextlib.redirect_stdout(_saida_retry):
        _BREVO_SEND_REAL("k", {"name": "x", "email": "x@x.com"}, "ana@exemplo.pt",
                         "s", "<html></html>")
finally:
    sn.requests.post = _guardado_post
    sn.time.sleep = _guardado_sleep
true("@" not in _saida_retry.getvalue(),
     f"nem nas tentativas intermedias ({_saida_retry.getvalue()[:200]})")
true(_t_retry["n"] > 1, f"e as tentativas correram mesmo ({_t_retry['n']})")
shutil.rmtree(tmp8, ignore_errors=True)

# ── Retoma que atravessa a meia-noite, e retoma sem o ficheiro ───────────
# O nome do ficheiro inclui a DATA, e o numero da edicao nao muda de sexta para
# sabado. Uma entrega parcial e, por definicao, o caso lento — e a retoma
# atravessa a meia-noite UTC com facilidade. A versao anterior nao encontrava o
# ficheiro, gerava uma edicao NOVA, e os `if not ja` a seguir impediam que fosse
# arquivada ou publicada: um terco da lista com uma analise, dois tercos com
# outra, e a ligacao do e-mail a apontar para uma pagina que nunca existiu.
tmp9 = prepara(Path(tempfile.mkdtemp()))
_n9 = ISSUE_HOJE
_html9 = edicao(_n9)
# a edicao ficou publicada com o nome de SEXTA
(tmp9 / f"MRM_Newsletter_Issue{_n9}_04Sep2026.html").write_text(_html9, encoding="utf-8")
json.dump({"sent": [{"issue": _n9, "recipients": 1, "complete": False,
                     "served": sn.marcas(["a@x.com"]),
                     "pending": sn.marcas(["b@x.com"])}]},
          open(tmp9 / "sent_issues.json", "w"))
_ger9, _env9 = [], []
sn.call_model = lambda p_, k_: (_ger9.append(1), edicao_do_prompt(p_, _n9, enchimento=99000))[1]
sn.git_publish = lambda files, msg, **kw: True
sn.brevo_subscribers = lambda key: ["a@x.com", "b@x.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    _env9.append((to, html)), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp9)
try:
    sn.main()
finally:
    os.chdir(cwd)
true(_ger9 == [], f"a retoma com outro nome de ficheiro NAO gera edicao nova ({_ger9})")
_b9 = next(h for t, h in _env9 if t == "b@x.com")
true(_b9 == _html9, "e quem faltava recebe exactamente a edicao ja publicada")
true([t for t, _ in _env9 if t == "a@x.com"] == [], "sem reenviar a quem ja tinha")
shutil.rmtree(tmp9, ignore_errors=True)

# ── Duas edicoes para o mesmo numero: a retoma manda a MAIS RECENTE ────────
#
# Acontece quando a marca se perde e o job corre outra vez: ficam dois ficheiros
# com o mesmo numero e datas diferentes. Ordenar por cadeia dava "04Sep2026"
# antes de "29Aug2026" — a mais antiga — e a retoma mandava a quem faltava uma
# edicao que os outros nunca viram.
tmp9c = prepara(Path(tempfile.mkdtemp()))
_velha = edicao(_n9, enchimento=12000)
_nova = edicao(_n9, enchimento=13000)
(tmp9c / f"MRM_Newsletter_Issue{_n9}_29Aug2026.html").write_text(_velha, encoding="utf-8")
(tmp9c / f"MRM_Newsletter_Issue{_n9}_04Sep2026.html").write_text(_nova, encoding="utf-8")
json.dump({"sent": [{"issue": _n9, "recipients": 1, "complete": False,
                     "served": sn.marcas(["a@x.com"]),
                     "pending": sn.marcas(["b@x.com"])}]},
          open(tmp9c / "sent_issues.json", "w"))
_env9c = []
sn.call_model = lambda p_, k_: edicao_do_prompt(p_, _n9)
sn.git_publish = lambda files, msg, **kw: True
sn.brevo_subscribers = lambda key: ["a@x.com", "b@x.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    _env9c.append((to, html)), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp9c)
try:
    sn.main()
finally:
    os.chdir(cwd)
_b9c = next(h for t, h in _env9c if t == "b@x.com")
true(_b9c == _nova,
     "com duas edicoes para o mesmo numero, a retoma manda a mais RECENTE")
shutil.rmtree(tmp9c, ignore_errors=True)

# ── A marca publicada com enderecos limpa-se sozinha, sem travar nada ─────
#
# O RUNBOOK manda o operador escrever esta entrada a mao numa recuperacao, as
# 22:41 de uma sexta: e dai que entram enderecos em claro. A correccao tem de
# ser automatica na corrida seguinte — e NAO pode ser um portao. Uma verificacao
# de privacidade sobre estado que uma pessoa edita, posta a travar a suite,
# travaria o job da carteira (que nada tem a ver com subscritores) e, com ele, a
# propria limpeza: o sistema deixava de se curar por causa da guarda.
tmp14 = prepara(Path(tempfile.mkdtemp()))
json.dump({"sent": [{"issue": ISSUE_HOJE - 3, "recipients": 2, "complete": True,
                     "served": ["ana@exemplo.pt", "bruno@exemplo.pt"]}]},
          open(tmp14 / "sent_issues.json", "w"))
_pub14, _env14 = [], []
sn.call_model = lambda p_, k_: edicao_do_prompt(p_, ISSUE_HOJE)
sn.git_publish = lambda files, msg, **kw: (_pub14.append((list(files), msg)), True)[1]
sn.brevo_subscribers = lambda key: ["c@x.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    _env14.append(to), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp14)
_erro14 = None
try:
    sn.main()
except BaseException as e:
    _erro14 = e
finally:
    os.chdir(cwd)
true(_erro14 is None, f"a marca com enderecos NAO trava a corrida ({_erro14!r})")
_texto14 = (tmp14 / "sent_issues.json").read_text(encoding="utf-8")
true("@" not in _texto14,
     f"e o ficheiro fica limpo, sozinho ({_texto14[:200]})")
true(any("sent_issues.json" in f and "sanitise" in m for f, m in _pub14),
     f"e a limpeza e publicada, para o ficheiro servido no site deixar de a ter "
     f"({[m for _, m in _pub14]})")
true("c@x.com" in _env14, "e a edicao da semana sai na mesma")
shutil.rmtree(tmp14, ignore_errors=True)

# E a limpeza nao pode ser FATAL: se o push nao passar — as 22:00 de sexta ha
# tres bots a empurrar para `main` — a corrida segue e tenta outra vez para a
# semana. Uma limpeza de privacidade a travar o envio e o defeito que ela veio
# corrigir, ao contrario.
tmp15 = prepara(Path(tempfile.mkdtemp()))
json.dump({"sent": [{"issue": ISSUE_HOJE - 3, "recipients": 1, "complete": True,
                     "served": ["ana@exemplo.pt"]}]},
          open(tmp15 / "sent_issues.json", "w"))
_avisos15, _env15 = [], []
sn.call_model = lambda p_, k_: edicao_do_prompt(p_, ISSUE_HOJE)
sn.git_publish = (lambda files, msg, **kw:
                  (_avisos15.append((list(files), kw.get("fatal", True), kw.get("aviso"))),
                   "sent_issues.json" not in files or "sanitise" not in msg)[1])
sn.brevo_subscribers = lambda key: ["c@x.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    _env15.append(to), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp15)
_erro15 = None
try:
    sn.main()
except BaseException as e:
    _erro15 = e
finally:
    os.chdir(cwd)
_lim15 = [a_ for a_ in _avisos15 if "sanitise" not in str(a_[2] or "") or True]
_chamada_limpeza = next((a_ for a_ in _avisos15 if a_[0] == ["sent_issues.json"]), None)
true(_chamada_limpeza is not None, f"a limpeza tentou publicar ({_avisos15})")
true(_chamada_limpeza[1] is False,
     "e a publicacao da limpeza NAO e fatal — nao pode travar a corrida")
# O aviso e verificado na MENSAGEM que o operador le, nao no argumento: com
# `aviso=None`, `str(None or "")` e "" e a asserçao passava enquanto o
# `git_publish` imprimia o aviso por omissao — "A newsletter JA FOI ENVIADA;
# nao voltar a correr o envio" — a quem ainda nao enviou nada, e a semana
# perdia-se. E preciso correr o `git_publish` a serio e ler o que ele escreve.
true(_chamada_limpeza[2],
     f"a limpeza traz o seu proprio aviso ({_chamada_limpeza[2]!r})")
_saida_gp = io.StringIO()
_run_ant = sn.subprocess.run
try:
    # `git diff --staged --quiet` tem de devolver 1 (ha mudancas), senao o
    # git_publish sai cedo e nao chega ao push.
    def _run_falso(args, **kw):
        if args[:2] == ["git", "push"]:
            return types.SimpleNamespace(returncode=1)
        if args[:3] == ["git", "diff", "--staged"]:
            return types.SimpleNamespace(returncode=1)
        return types.SimpleNamespace(returncode=0)
    sn.subprocess.run = _run_falso
    _sleep_ant, sn.time.sleep = sn.time.sleep, lambda *_: None
    with contextlib.redirect_stdout(_saida_gp):
        _GIT_PUBLISH_REAL(["sent_issues.json"], "chore: sanitise",
                          tentativas=1, fatal=False, aviso=_chamada_limpeza[2])
finally:
    sn.subprocess.run = _run_ant
    sn.time.sleep = _sleep_ant
_texto_gp = _saida_gp.getvalue()
true("JA FOI ENVIADA" not in _texto_gp,
     f"e o que o operador LE nao afirma um envio que nao houve ({_texto_gp!r})")
true("re-correr normalmente" in _texto_gp,
     f"e diz-lhe o que fazer ({_texto_gp!r})")
true("c@x.com" in _env15,
     f"e com o push da limpeza recusado a edicao sai na mesma ({_env15})")
shutil.rmtree(tmp15, ignore_errors=True)

# E as entradas que nao se percebem sao PRESERVADAS, nao apagadas: a unica prova
# de que uma edicao saiu nao pode desaparecer numa limpeza de privacidade.
_dir16 = Path(tempfile.mkdtemp())
(_dir16 / "sent_issues.json").write_text(json.dumps({"sent": [
    {"issue": "25", "recipients": 3, "served": ["ana@exemplo.pt"], "complete": True},
    {"issue": 24.0, "recipients": 2, "complete": True},
]}), encoding="utf-8")
sn.sanear_marca(str(_dir16 / "sent_issues.json"))
_reg16 = json.loads((_dir16 / "sent_issues.json").read_text(encoding="utf-8"))
true(len(_reg16["sent"]) == 2,
     f"as entradas com o numero noutra forma sao preservadas ({_reg16['sent']})")
true("@" not in json.dumps(_reg16),
     f"e mesmo assim saem sem enderecos ({json.dumps(_reg16)[:160]})")
shutil.rmtree(_dir16, ignore_errors=True)

# ── Retoma sobre uma marca HERDADA, com enderecos em claro ────────────────
#
# O escritor normaliza; o leitor tem de normalizar tambem. Uma marca escrita
# antes de os enderecos passarem a digests — ou a entrada que o RUNBOOK manda
# escrever a mao — traz enderecos em claro: se a leitura os comparasse com
# digests, nenhum batia certo e a retoma reenviava a TODA a gente, incluindo a
# quem ja tinha recebido. E o unico caso em que uma pessoa recebe duas vezes.
tmp9b = prepara(Path(tempfile.mkdtemp()))
_html9b = edicao(_n9)
(tmp9b / f"MRM_Newsletter_Issue{_n9}_04Sep2026.html").write_text(_html9b, encoding="utf-8")
json.dump({"sent": [{"issue": _n9, "recipients": 1, "complete": False,
                     "served": ["a@x.com"],          # HERDADA, em claro
                     "pending": ["b@x.com"]}]},
          open(tmp9b / "sent_issues.json", "w"))
_env9b = []
sn.call_model = lambda p_, k_: edicao_do_prompt(p_, _n9)
sn.git_publish = lambda files, msg, **kw: True
sn.brevo_subscribers = lambda key: ["a@x.com", "b@x.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    _env9b.append(to), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp9b)
try:
    sn.main()
finally:
    os.chdir(cwd)
true("a@x.com" not in _env9b,
     f"com a marca herdada em claro, quem ja tinha recebido NAO recebe outra vez "
     f"({_env9b})")
true("b@x.com" in _env9b, f"e quem faltava recebe ({_env9b})")
# E o ficheiro que fica no disco ja nao tem enderecos: a marca herdada e limpa
# na primeira vez que se lhe toca.
_texto9b = (tmp9b / "sent_issues.json").read_text(encoding="utf-8")
true("@x.com" not in _texto9b,
     f"e a marca herdada sai limpa do ficheiro publicado ({_texto9b[:200]})")
shutil.rmtree(tmp9b, ignore_errors=True)

# E se o ficheiro nao estiver no checkout de todo, PARA — nao gera outro.
tmp10 = prepara(Path(tempfile.mkdtemp()))
json.dump({"sent": [{"issue": _n9, "recipients": 1, "complete": False,
                     "served": sn.marcas(["a@x.com"]),
                     "pending": sn.marcas(["b@x.com"])}]},
          open(tmp10 / "sent_issues.json", "w"))
_ger10, _env10 = [], []
sn.call_model = lambda p_, k_: (_ger10.append(1), edicao_do_prompt(p_, _n9))[1]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    _env10.append(to), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp10)
erro10 = None
try:
    sn.main()
except BaseException as e:
    erro10 = e
finally:
    os.chdir(cwd)
true(isinstance(erro10, RuntimeError), f"sem o ficheiro publicado, a retoma para ({erro10!r})")
true("NAO se gera outra edicao" in str(erro10), f"e diz porque ({erro10})")
true(_ger10 == [] and _env10 == [], "e nao gera nem envia nada")
shutil.rmtree(tmp10, ignore_errors=True)

# ── Uma entrega INCOMPLETA nao termina verde ─────────────────────────────
# Bastava um envio com sucesso: o job ficava verde, o alert-on-failure nao
# corria, e ninguem sabia que faltavam pessoas por servir. O `complete: false`
# era carta morta.
tmp11 = prepara(Path(tempfile.mkdtemp()))
_lista11 = [f"{i}@x.com" for i in range(10)]
sn.call_model = lambda p_, k_: edicao_do_prompt(p_, ISSUE_HOJE)
sn.git_publish = lambda files, msg, **kw: True
sn.brevo_subscribers = lambda key: list(_lista11)
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    (True, types.SimpleNamespace(status_code=200)) if to in ("0@x.com", "usmrm@proton.me")
    else (False, types.SimpleNamespace(status_code=500)))
os.chdir(tmp11)
erro11 = None
try:
    sn.main()
except BaseException as e:
    erro11 = e
finally:
    os.chdir(cwd)
true(isinstance(erro11, SystemExit),
     f"com 1 servido e 9 falhados, o job termina em ERRO (obtido {erro11!r})")
true("INCOMPLETA" in str(erro11), f"e di-lo ({erro11})")
true("9 falhados" in str(erro11), "com a contagem")
_r11 = json.load(open(tmp11 / "sent_issues.json"))["sent"][-1]
true(_r11["complete"] is False, "e a marca fica incompleta, para a retoma saber")
shutil.rmtree(tmp11, ignore_errors=True)

# ── E o ramo do data_prev, isolado ───────────────────────────────────────
# O teste do cenario 3 fazia o git_publish falhar para TUDO, portanto nao
# distinguia qual dos dois ramos causava o SystemExit: substituir
# `_prev_publicado` por True constante mantinha a suite verde. A consequencia
# deste ramo e propria — uma edicao inteira com todos os "vs. semana anterior"
# a zero — e merece o seu proprio caso.
tmp12 = prepara(Path(tempfile.mkdtemp()))
sn.call_model = lambda p_, k_: edicao_do_prompt(p_, ISSUE_HOJE)
sn.brevo_subscribers = lambda key: ["a@x.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    True, types.SimpleNamespace(status_code=200))
# so o push do data_prev falha; o da marca passa
sn.git_publish = lambda files, msg, **kw: "data_prev.json" not in files
os.chdir(tmp12)
erro12 = None
try:
    sn.main()
except BaseException as e:
    erro12 = e
finally:
    os.chdir(cwd)
true(isinstance(erro12, SystemExit),
     f"o data_prev por rodar tambem poe o job vermelho (obtido {erro12!r})")
true("data_prev.json NAO rodou" in str(erro12), f"e a mensagem nomeia-o ({erro12})")
true("marca de envio" not in str(erro12),
     "e NAO acusa a marca, que foi publicada — os dois ramos sao distintos")
true("vs. semana anterior" in str(erro12),
     "e diz qual e a consequencia se ficar assim")
shutil.rmtree(tmp12, ignore_errors=True)

# ── E a rotacao do FICHEIRO acontece depois do envio, nao so o push ──────
# A ordem estava afirmada sobre os `git_publish`. Mas o que mata o WoW e a
# COPIA local: mover `shutil.copy(data.json, data_prev.json)` para antes do
# envio deixava a suite inteiramente verde, e numa recuperacao a mao — que e
# o que o RUNBOOK manda fazer, na mesma arvore de trabalho — a corrida seguinte
# encontrava `prev == data` e publicava uma edicao a dizer aos subscritores que
# NADA mudou na semana. A propriedade e sobre o ficheiro em disco: enquanto o
# envio nao tiver acontecido, o data_prev.json ainda e o da semana anterior.
tmp13b = prepara(Path(tempfile.mkdtemp()))
# O data_prev fica com uma marca reconhecivel da semana ANTERIOR.
_prev13b = json.loads((tmp13b / "data_prev.json").read_text(encoding="utf-8"))
_prev13b["globalResilienceScore"] = 1.23
(tmp13b / "data_prev.json").write_text(json.dumps(_prev13b), encoding="utf-8")
_estado13b = {}
sn.call_model = lambda p_, k_: edicao_do_prompt(p_, ISSUE_HOJE)
sn.git_publish = lambda files, msg, **kw: True
sn.brevo_subscribers = lambda key: ["a@x.com"]
def _envio13b(key, sender, to, subject, html, **kw):
    # No momento em que o primeiro envio acontece, o data_prev AINDA nao pode
    # ter sido rodado: se ja tiver, uma falha a partir daqui perde o WoW.
    _estado13b.setdefault("prev_no_envio", json.loads(
        (tmp13b / "data_prev.json").read_text(encoding="utf-8")))
    raise RuntimeError("a rede caiu a meio do envio")
sn.brevo_send = _envio13b
os.chdir(tmp13b)
try:
    sn.main()
except BaseException:
    pass
finally:
    os.chdir(cwd)
true("prev_no_envio" in _estado13b, "o envio foi mesmo tentado")
true(_estado13b["prev_no_envio"].get("globalResilienceScore") == 1.23,
     f"no momento do envio o data_prev.json ainda e o da semana ANTERIOR — a "
     f"rotacao do ficheiro acontece DEPOIS, senao uma falha a meio do envio "
     f"publica na semana seguinte todos os 'vs. semana anterior' a zero "
     f"(obtido {_estado13b['prev_no_envio'].get('globalResilienceScore')})")
_prev_depois13b = json.loads((tmp13b / "data_prev.json").read_text(encoding="utf-8"))
true(_prev_depois13b.get("globalResilienceScore") == 1.23,
     f"e com o envio falhado ele NAO e rodado: a re-corrida na mesma arvore de "
     f"trabalho ainda tem com que comparar "
     f"(obtido {_prev_depois13b.get('globalResilienceScore')})")
shutil.rmtree(tmp13b, ignore_errors=True)

# ── A semana em que o score se mexe e uma serie da FRED para ────────────────
#
# Este e o caso que o resto do ficheiro nao cobria: enquanto o fixture do modelo
# escrevia um score constante de 6,97 e nenhum aviso, o teste so passava na
# semana em que o data.json commitado dissesse 6,97 e nao houvesse nada a
# avisar. O score publicado ja andou entre 6,5 e 7,0, e uma serie da FRED parar
# e precisamente o que o data_freshness existe para apanhar — quando isso
# acontecesse, a validacao recusaria a edicao tres vezes e este ficheiro, que e
# portao dos dois jobs de sexta, ficava vermelho. Nao um teste vermelho na
# segunda-feira: uma semana sem carteira e sem newsletter, na semana em que os
# avisos existem para ser lidos.
tmp13 = prepara(Path(tempfile.mkdtemp()))
_d13 = json.loads((tmp13 / "data.json").read_text())
_d13["globalResilienceScore"] = 4.2                     # outra banda de regime
_d13.setdefault("meta", {}).setdefault("fredSeriesStale", {})["SAHMREALTIME"] = 400
# A derivacao tem de PEGAR: se nenhum pilar se chamar "premium" — e os ids dos
# pilares ja mudaram neste sistema — o ciclo nao faz nada e a asserçao la abaixo
# falha com uma mensagem que nao nomeia a causa, fechando os dois jobs de sexta
# por uma razao que ninguem consegue ler.
_tocou13 = 0
for _p13 in _d13.get("pillars", []):
    if _p13.get("id") == "premium":
        _p13.setdefault("epAnchor", {}).update({"stale": True, "ageDays": 260})
        _tocou13 += 1
true(_tocou13 == 1,
     f"o data.json tem um pilar 'premium' para envelhecer a ancora "
     f"(ids: {[p.get('id') for p in _d13.get('pillars', [])]})")
(tmp13 / "data.json").write_text(json.dumps(_d13))
_env13, _pub13, _prompts13 = [], [], []
def _modelo13(prompt, key):
    _prompts13.append(prompt)
    return edicao_do_prompt(prompt, ISSUE_HOJE)
sn.call_model = _modelo13
sn.git_publish = lambda files, msg, **kw: (_pub13.append(list(files)), True)[1]
sn.brevo_subscribers = lambda key: ["a@x.com", "b@x.com"]
sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
    _env13.append((to, html)), (True, types.SimpleNamespace(status_code=200)))[1]
os.chdir(tmp13)
erro13 = None
try:
    sn.main()
except BaseException as e:
    erro13 = e
finally:
    os.chdir(cwd)
true(erro13 is None, f"a semana com score diferente e avisos publica na mesma ({erro13!r})")
true(len(_env13) == 3,
     f"e chega aos dois subscritores e ao briefing do dono (obtido {len(_env13)})")
true(len(_prompts13) == 1,
     f"e o modelo nao precisa de tentativas extra (obtido {len(_prompts13)})")
_html13 = _env13[0][1]
true("4.2" in _html13, "a edicao MOSTRA o score que o motor calculou, nao um fixo")
true("SAHMREALTIME" in _html13 and "stopped updating" in _html13,
     "e leva o aviso da serie parada, palavra por palavra")
true("earnings reference" in _html13,
     "e o aviso da ancora dos earnings tambem")
shutil.rmtree(tmp13, ignore_errors=True)

# ── 14. A marca perdida: a Brevo e a segunda testemunha ───────────────────
#
# O caso mais perigoso do RUNBOOK, e o unico que ainda exigia uma pessoa: a
# edicao sai e o push do `sent_issues.json` falha. O runner e destruido com o
# registo dentro e a corrida seguinte, sem marca nenhuma, reenviava a TODA a
# gente — uma segunda edicao, com texto diferente, porque quem a escreve e um
# modelo.
def _prepara_perdida(dir_):
    """Uma semana em que a edicao JA esta publicada e a marca nao existe."""
    _t = prepara(dir_)
    _c = build_context_qualquer(_t)
    (_t / f"MRM_Newsletter_Issue{ISSUE_HOJE}_{_c}.html").write_text(
        edicao(ISSUE_HOJE), encoding="utf-8")
    (_t / "sent_issues.json").write_text(json.dumps({"sent": []}))
    return _t

def build_context_qualquer(_t):
    # O nome do ficheiro so precisa de casar com o glob `Issue{N}_*.html`.
    return "11Sep2026"

def _corre(tmp_, *, brevo_vistos=None, brevo_erro=None, subs=("a@x.com", "b@x.com")):
    _env, _pub, _consultas = [], [], []
    sn.call_model = lambda prompt, key: edicao_do_prompt(prompt, ISSUE_HOJE)
    sn.git_publish = lambda files, msg, **kw: (_pub.append(list(files)), True)[1]
    sn.brevo_subscribers = lambda key: list(subs)
    sn.brevo_send = lambda key, sender, to, subject, html, **kw: (
        _env.append((to, kw.get("tags"))), (True, types.SimpleNamespace(status_code=200)))[1]
    def _consulta(key, issue, **kw):
        _consultas.append(issue)
        return (set(brevo_vistos or ()), brevo_erro)
    sn.brevo_destinatarios_da_edicao = _consulta
    erro = None
    os.chdir(tmp_)
    try:
        sn.main()
    except BaseException as e:
        erro = e
    finally:
        os.chdir(cwd)
    return _env, _pub, _consultas, erro

# (a) A Brevo diz que um dos dois ja recebeu: serve-se so o outro.
_t14 = _prepara_perdida(Path(tempfile.mkdtemp()))
_env14, _pub14, _cons14, _erro14 = _corre(_t14, brevo_vistos={"a@x.com"})
true(_erro14 is None, f"a corrida termina sem excepcao ({_erro14!r})")
true(_cons14 == [ISSUE_HOJE],
     f"perguntou-se a Brevo por esta edicao, uma vez ({_cons14})")
_destinos14 = [e for e, _tags in _env14]
true("a@x.com" not in _destinos14,
     f"quem a Brevo diz ter recebido NAO recebe outra vez ({_destinos14})")
true("b@x.com" in _destinos14, f"e quem faltava recebe ({_destinos14})")
shutil.rmtree(_t14, ignore_errors=True)

# (b) A Brevo nao responde: nao se envia nada. Duplicar custa mais do que esperar.
_t14b = _prepara_perdida(Path(tempfile.mkdtemp()))
_env14b, _pub14b, _cons14b, _erro14b = _corre(_t14b, brevo_erro="HTTP 503")
true(isinstance(_erro14b, RuntimeError),
     f"sem resposta da Brevo e sem marca, a corrida PARA ({_erro14b!r})")
true("RUNBOOK" in str(_erro14b), "e manda ler o RUNBOOK")
true(_env14b == [], f"e nao saiu uma unica mensagem ({_env14b})")
shutil.rmtree(_t14b, ignore_errors=True)

# (c) Sem edicao publicada nao ha desconfianca nenhuma: uma semana normal nao
#     passa a depender da API de estatisticas da Brevo para poder enviar.
_t14c = prepara(Path(tempfile.mkdtemp()))
(_t14c / "sent_issues.json").write_text(json.dumps({"sent": []}))
_env14c, _pub14c, _cons14c, _erro14c = _corre(_t14c, brevo_erro="HTTP 503")
true(_erro14c is None, f"a semana normal corre na mesma ({_erro14c!r})")
true(_cons14c == [], f"e nao se pergunta nada a Brevo ({_cons14c})")
true(len([e for e, _ in _env14c if e in ("a@x.com", "b@x.com")]) == 2,
     f"e os dois subscritores recebem ({_env14c})")

# (d) E cada mensagem vai etiquetada com a edicao — e essa etiqueta que torna a
#     pergunta da alinea (a) possivel na semana seguinte.
_tags14 = [t for e, t in _env14c if e in ("a@x.com", "b@x.com")]
true(all(t == [sn.etiqueta_edicao(ISSUE_HOJE)] for t in _tags14),
     f"cada envio leva a etiqueta da edicao ({_tags14})")
shutil.rmtree(_t14c, ignore_errors=True)


print(f"TODOS OS {ok} TESTES PASSARAM")
