"""Monitor de passagens ID Jovem - Viaje Guanabara -> aviso no ntfy e WhatsApp (CallMeBot)."""
import os, re, json, time, datetime as dt
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
DEBUG = os.getenv("DEBUG") == "1"
TESTE = os.getenv("TESTE") == "1"
ESTADO = "estado.json"
MAX_LINHAS = 20

# Dias da semana que interessam (segunda=0, terça=1, quarta=2, quinta=3, sexta=4, sábado=5, domingo=6)
DIAS_IDA = {2, 3, 4, 5}    # ida (saindo de JP ou CG): quarta, quinta, sexta e sábado
DIAS_VOLTA = {6, 0}        # volta (saindo de FOR ou JDN): domingo e segunda

# O texto "ID Jovem" NÃO aparece na página. Com "1 jovem" na busca, a passagem vem com a
# etiqueta "Desconto", o preço cheio e, em seguida, o preço com ~50% de desconto.
# Ex. no texto da página: "R$ 187.99" ... "R$\n98\n,00"
PRECOS = re.compile(r"R\$\s*(\d+\.\d{2})\s+R\$\s*(\d+)\s*,\s*(\d{2})")


def brl(v):
    return f"{v:.2f}".replace(".", ",")


GRATIS_ATE = 12.0  # ID Jovem 100% (gratuita) só paga a taxa: preço final abaixo de R$ 12


def id_jovem(texto):
    """Devolve {"100": menor_preço, "50": menor_preço} com os tipos de ID Jovem da página.
    "nenhum resultado encontrado" = sem passagem (ou vagas esgotadas) -> {}."""
    if re.search(r"nenhum resultado", texto, re.I):
        return {}
    tipos = {}
    for a, b, c in PRECOS.findall(texto):
        cheio, final = float(a), float(f"{b}.{c}")
        if final < GRATIS_ATE:
            tipo = "100"
        elif final <= cheio * 0.6:
            tipo = "50"
        else:
            continue
        tipos[tipo] = min(final, tipos.get(tipo, final))
    return tipos


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
            # esconde telefone e chave: os logs de repositório público são visíveis a todos
            resposta = resposta.replace(tel, "***").replace(tel.lstrip("+"), "***")
            resposta = resposta.replace(chave, "***")
            print("WhatsApp", "***", r.status_code, resposta[:400])
            ok = r.status_code in (200, 203) and "invalid" not in resposta.lower()
            algum = algum or ok
        except Exception as e:
            print("WhatsApp erro", "***", str(e).replace(chave, "***"))
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


def linha_aviso(o, d, data, tipo):
    ano, mes, dia = data.split("-")
    desc = "100% grátis" if tipo == "100" else "50% de desconto"
    return (f"Passagem disponível com ID Jovem ({desc}) no dia {dia} de "
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
    inicio, paginas = time.time(), 0

    with sync_playwright() as p:
        nav = p.chromium.launch()
        page = nav.new_context(locale="pt-BR", viewport={"width": 1280, "height": 900}).new_page()
        # não baixa imagens, vídeos e fontes: só o texto importa e a página carrega bem mais rápido
        page.route("**/*", lambda r: r.abort()
                   if r.request.resource_type in ("image", "media", "font") else r.continue_())
        for o, d in ROTAS:
            for i in range(DIAS):
                dia = hoje + dt.timedelta(days=i)
                # no debug olha todos os dias (para testar as 6 rotas); no normal só os dias que interessam
                if not DEBUG and dia.weekday() not in (DIAS_IDA if (o, d) in IDA else DIAS_VOLTA):
                    continue
                data = dia.isoformat()
                u = url(o, d, data)
                paginas += 1
                t_pag = time.time()
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=45000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass  # seguiu carregando: lê o que já está na tela
                    page.wait_for_timeout(1500)
                    texto = page.inner_text("body")
                except Exception as e:
                    print(f"[{paginas}] ERRO {SIGLAS[o]}>{SIGLAS[d]} {data} ({time.time() - t_pag:.1f}s): {str(e)[:150]}", flush=True)
                    resumo.append(f"{SIGLAS[o]}>{SIGLAS[d]} {data}: ERRO ({str(e)[:120]})")
                    continue
                print(f"[{paginas}] {SIGLAS[o]}>{SIGLAS[d]} {data} ({time.time() - t_pag:.1f}s)", flush=True)
                if DEBUG and i < 3:  # guarda amostras (hoje, amanhã e depois) para calibrar
                    nome = f"debug/{o}-{d}-{data}".replace(" ", "_")
                    open(nome + ".txt", "w").write(texto)
                    page.screenshot(path=nome + ".png", full_page=True)
                tipos = id_jovem(texto)
                for tipo, preco in tipos.items():
                    achados[f"{o}>{d}>{data}>{tipo}"] = u
                    print(f"ACHEI: {o} > {d} {data} ID Jovem {tipo}% a partir de R$ {brl(preco)}")
                if DEBUG:
                    rota = f"{SIGLAS[o]}>{SIGLAS[d]} {data}: "
                    if tipos:
                        det = ", ".join(f"{t}% a partir de R$ {brl(p)}"
                                        for t, p in sorted(tipos.items(), key=lambda x: -int(x[0])))
                        resumo.append(rota + f"ACHEI ({det})")
                    elif re.search(r"nenhum resultado", texto, re.I):
                        resumo.append(rota + "nada (nenhum resultado encontrado)")
                    else:
                        resumo.append(rota + "TEM VIAGENS, mas sem preço de ID Jovem reconhecido")
        nav.close()
    seg = time.time() - inicio
    print(f"Tempo: {seg:.0f}s para {paginas} páginas ({seg / max(paginas, 1):.1f}s por página)")

    if DEBUG:
        print("\n===== RESUMO DO DEBUG =====")
        print("\n".join(resumo))
        print("===========================\n")

    novos = {k: v for k, v in achados.items() if k not in antigo}
    if novos:
        linhas = []
        ordem = sorted(novos, key=lambda k: (k.split(">")[2], k))  # por data
        for k in ordem[:MAX_LINHAS]:
            o, d, data, tipo = k.split(">")
            linhas.append("• " + linha_aviso(o, d, data, tipo))
        extra = f"\n(+{len(novos) - MAX_LINHAS} outras)" if len(novos) > MAX_LINHAS else ""
        titulo = ("🎉 ID JOVEM 100% GRÁTIS DISPONÍVEL!" if any(k.endswith(">100") for k in novos)
                  else "🚌 ID JOVEM DISPONÍVEL!")
        enviou = avisar(titulo, "\n".join(linhas) + extra, link=novos[ordem[0]])
        if not enviou:
            return  # não grava o estado: avisa na próxima rodada
    else:
        print("Nada novo.")
    json.dump(sorted(achados), open(ESTADO, "w"))


if __name__ == "__main__":
    main()
