"""Monitor de passagens ID Jovem - Viaje Guanabara -> aviso no ntfy e WhatsApp (CallMeBot)."""
import os, re, json, datetime as dt
import requests
from playwright.sync_api import sync_playwright

BASE = "https://viajeguanabara.com.br/onibus"
CIDADES = {
    "João Pessoa": "joao_pessoa-pb",
    "Campina Grande": "campina_grande-pb",
    "Fortaleza": "fortaleza-ce",
    "Juazeiro do Norte": "juazeiro_do_norte-ce",
}
SIGLAS = {"João Pessoa": "JP", "Campina Grande": "CG",
          "Fortaleza": "FOR", "Juazeiro do Norte": "JDN"}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]

IDA = [("João Pessoa", "Fortaleza"), ("Campina Grande", "Fortaleza"),
       ("Campina Grande", "Juazeiro do Norte")]
ROTAS = IDA + [(d, o) for o, d in IDA]  # ida e volta de cada rota

DIAS = int(os.getenv("DIAS", "60"))                  # quantos dias à frente pesquisar
PASSAGEIROS = os.getenv("PASSAGEIROS", "").strip()   # ex.: 3:1  (código do Jovem)
PALAVRA = re.compile(os.getenv("PALAVRA", r"id jovem"), re.I)
DEBUG = os.getenv("DEBUG") == "1"
TESTE = os.getenv("TESTE") == "1"
ESTADO = "estado.json"
MAX_LINHAS = 20


def destinos_whatsapp():
    """Lê CALLMEBOT_DESTINOS ("telefone:chave,telefone:chave") ou o par antigo."""
    lista = []
    for item in os.getenv("CALLMEBOT_DESTINOS", "").split(","):
        if ":" in item:
            tel, chave = item.strip().split(":", 1)
            lista.append((tel.strip(), chave.strip()))
    tel, chave = os.getenv("CALLMEBOT_PHONE", "").strip(), os.getenv("CALLMEBOT_APIKEY", "").strip()
    if tel and chave and (tel, chave) not in lista:
        lista.append((tel, chave))
    return lista


def whatsapp(texto):
    lista = destinos_whatsapp()
    if not lista:
        print("WhatsApp ainda não configurado. Mensagem seria:\n" + texto)
        return False
    algum = False
    for tel, chave in lista:
        try:
            r = requests.get("https://api.callmebot.com/whatsapp.php", timeout=30,
                             params={"phone": tel, "apikey": chave, "text": texto})
            resposta = re.sub(r"<[^>]+>", " ", r.text)
            print("WhatsApp", tel[-4:], r.status_code, resposta[:400])
            ok = r.status_code in (200, 203) and "invalid" not in resposta.lower()
            algum = algum or ok
        except Exception as e:
            print("WhatsApp erro", tel[-4:], e)
    return algum


def ntfy(titulo, texto, link=None):
    topico = os.getenv("NTFY_TOPIC", "").strip()
    if not topico:
        print("ntfy ainda não configurado.")
        return False
    corpo = {"topic": topico, "title": titulo, "message": texto,
             "priority": 5, "tags": ["bus"]}
    if link:
        corpo["click"] = link
    try:
        r = requests.post("https://ntfy.sh", json=corpo, timeout=30)
        print("ntfy:", r.status_code, r.text[:120])
        return r.status_code == 200
    except Exception as e:
        print("ntfy erro:", e)
        return False


def avisar(titulo, texto, link=None):
    """Manda por ntfy e WhatsApp; vale se pelo menos um funcionar."""
    a = ntfy(titulo, texto, link)
    b = whatsapp(f"{titulo}\n\n{texto}")
    return a or b


def url(o, d, data):
    return (f"{BASE}/{CIDADES[o]}/{CIDADES[d]}/?departure_date={data}"
            f"&passengers={PASSAGEIROS}")


def linha_aviso(o, d, data):
    ano, mes, dia = data.split("-")
    return (f"Passagem disponível com ID Jovem no dia {dia} de "
            f"{MESES[int(mes) - 1]} na rota {SIGLAS[o]} → {SIGLAS[d]}")


def main():
    if TESTE:
        avisar("✅ Teste do robô ID Jovem", "Se você recebeu isto, o aviso está funcionando!")
        return
    if not PASSAGEIROS:
        raise SystemExit("Defina a variável PASSAGEIROS (veja o passo a passo).")

    try:
        antigo = set(json.load(open(ESTADO)))
    except Exception:
        antigo = set()
    achados, hoje = {}, dt.date.today()
    os.makedirs("debug", exist_ok=True)
    resumo = []  # só usado no debug

    with sync_playwright() as p:
        nav = p.chromium.launch()
        page = nav.new_context(locale="pt-BR", viewport={"width": 1280, "height": 900}).new_page()
        for o, d in ROTAS:
            for i in range(DIAS):
                data = (hoje + dt.timedelta(days=i)).isoformat()
                u = url(o, d, data)
                try:
                    page.goto(u, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(2500)
                    texto = page.inner_text("body")
                except Exception as e:
                    print("Erro", o, d, data, e)
                    resumo.append(f"{SIGLAS[o]}>{SIGLAS[d]} {data}: ERRO")
                    continue
                if DEBUG and i < 3:  # guarda amostras (hoje, amanhã e depois) para calibrar
                    nome = f"debug/{o}-{d}-{data}".replace(" ", "_")
                    open(nome + ".txt", "w").write(texto)
                    page.screenshot(path=nome + ".png", full_page=True)
                m = PALAVRA.search(texto)
                if m:
                    achados[f"{o}>{d}>{data}"] = u
                    print("ACHEI:", o, d, data)
                    if DEBUG:
                        trecho = texto[max(0, m.start() - 80):m.end() + 80].replace("\n", " | ")
                        print("   trecho:", trecho)
                if DEBUG:
                    resumo.append(f"{SIGLAS[o]}>{SIGLAS[d]} {data}: "
                                  f"{'ACHEI' if m else 'nada'} ({len(texto)} caracteres)")
        nav.close()

    if DEBUG:
        print("\n===== RESUMO DO DEBUG =====")
        print("\n".join(resumo))
        print("===========================\n")

    novos = {k: v for k, v in achados.items() if k not in antigo}
    if novos:
        linhas = []
        for k in list(novos)[:MAX_LINHAS]:
            o, d, data = k.split(">")
            linhas.append("• " + linha_aviso(o, d, data))
        extra = f"\n(+{len(novos) - MAX_LINHAS} outras datas)" if len(novos) > MAX_LINHAS else ""
        enviou = avisar("🚌 ID JOVEM DISPONÍVEL!", "\n".join(linhas) + extra,
                        link=list(novos.values())[0])
        if not enviou:
            return  # não grava o estado: avisa na próxima rodada
    else:
        print("Nada novo.")
    json.dump(sorted(achados), open(ESTADO, "w"))


if __name__ == "__main__":
    main()
