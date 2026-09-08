"""
Smoke test do send_newsletter.main() — o caminho completo do job 2 do pipeline.

Os testes do prompt cobrem funcoes puras; este cobre a corrida inteira: gravar a
newsletter, inserir o cartao no arquivo do index.html, copiar o data_prev.json,
preparar o commit e enviar. Rede, git e o modelo sao substituidos.

Cenario escolhido: a semana em que o medidor B dispara — a que mais importa que
funcione, e a que a versao anterior contava ao contrario.
"""
import importlib.util, json, os, shutil, sys, tempfile, types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("sn", ROOT / "send_newsletter.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)
import mrm_rules as rules

ok = 0
def true(cond, what):
    global ok
    assert cond, what
    ok += 1

tmp = Path(tempfile.mkdtemp())
for f in ("data.json", "portfolio.json", "index.html"):
    shutil.copy(ROOT / f, tmp)
shutil.copy(ROOT / "data.json", tmp / "data_prev.json")

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

sent, prompts = [], []
def fake_model(prompt, key):
    prompts.append(prompt)
    return "<html><body>NEWSLETTER TESTE</body></html>"
sn.call_model = fake_model
sn.git_publish = lambda files, msg: sent.append(("git", list(files), msg))
sn.brevo_subscribers = lambda key: ["a@example.com", "b@example.com"]
sn.brevo_send = lambda key, sender, to, subject, html: (
    sent.append(("mail", list(to), subject)), (True, types.SimpleNamespace(status_code=200)))[1]

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
true(len(gits) == 1 and set(gits[0][1]) == {files[0], "index.html", "data_prev.json"},
     "preparou o commit com os tres ficheiros certos")
true(len(mails) == 2, "enviou a newsletter e o briefing do dono")
true("Critical · No Relief" in mails[0][2], "o assunto leva o regime operativo, nao o do score")
true(mails[0][1] == ["a@example.com", "b@example.com"], "enviou para os subscritores")
true(mails[1][1] == ["usmrm@proton.me"], "briefing do dono")

p = prompts[0]
true("Operative regime: Critical · No Relief" in p, "o modelo recebeu o regime real")
true("Rebalance outcome: STRESS_ON" in p, "o modelo recebeu o motivo real")
true("USMV | SHY | SGOV | GLD | BIL | VNQ" in p, "o modelo recebeu os instrumentos reais")
true("No structural regime change detected" not in p, "a frase legada nao voltou")

shutil.rmtree(tmp, ignore_errors=True)
print(f"TODOS OS {ok} TESTES PASSARAM")
