"""Monitor de passagens ID Jovem - Viaje Guanabara -> aviso no WhatsApp (CallMeBot)."""
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
IDA = [("João Pessoa", "Fortaleza"), ("Campina Grande", "Fortaleza"),
       ("Campina Grande", "Juazeiro do Norte")]
ROTAS = IDA + [(d, o) for o, d in IDA]  # ida e volta de cada rota

DIAS = int(os.getenv("DIAS", "60"))                  # quantos dias à frente pesquisar
PASSAGEIROS = os.getenv("PASSAGEIROS", "").strip()   # ex.: 3:1  (código do Jovem)
PALAVRA = re.compile(os.getenv("PALAVRA", r"id jovem"), re.I)
DEBUG = os.getenv("DEBUG") == "1"
TESTE = os.getenv("TESTE") == "1"
ESTADO = "estado.json"


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
            print("WhatsApp", tel[-4:], r.status_code, r.text[:80])
            algum = algum or r.status_code == 200
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
                    continue
                if DEBUG and i < 3:  # guarda amostras para calibrar
                    nome = f"debug/{o}-{d}-{data}".replace(" ", "_")
                    open(nome + ".txt", "w").write(texto)
                    page.screenshot(path=nome + ".png", full_page=True)
                if PALAVRA.search(texto):
                    achados[f"{o}>{d}>{data}"] = u
                    print("ACHEI:", o, d, data)
        nav.close()

    novos = {k: v for k, v in achados.items() if k not in antigo}
    if novos:
        linhas = []
        for k, v in list(novos.items())[:12]:
            o, d, data = k.split(">")
            ano, mes, dia = data.split("-")
            linhas.append(f"• {o} → {d} em {dia}/{mes}\n{v}")
        extra = f"\n(+{len(novos) - 12} outras datas)" if len(novos) > 12 else ""
        enviou = avisar("🚌 ID JOVEM DISPONÍVEL!", "\n\n".join(linhas) + extra,
                        link=list(novos.values())[0])
        if not enviou:
            return  # não grava o estado: avisa na próxima rodada
    else:
        print("Nada novo.")
    json.dump(sorted(achados), open(ESTADO, "w"))


if __name__ == "__main__":
    main()

