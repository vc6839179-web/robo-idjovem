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
LEGENDA = "JP = João Pessoa · CG = Campina Grande · FOR = Fortaleza · JDN = Juazeiro do Norte"
SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

IDA = [("João Pessoa", "Fortaleza"), ("Campina Grande", "Fortaleza"),
       ("Campina Grande", "Juazeiro do Norte")]
ROTAS = IDA + [(d, o) for o, d in IDA]  # ida e volta de cada rota

DIAS = int(os.getenv("DIAS", "60"))                  # quantos dias à frente pesquisar
PASSAGEIROS = os.getenv("PASSAGEIROS", "").strip()   # ex.: 3:1  (código do Jovem)
DEBUG = os.getenv("DEBUG") == "1"
TESTE = os.getenv("TESTE") == "1"
ESTADO = "estado.json"

# Dias da semana que interessam (segunda=0, terça=1, quarta=2, quinta=3, sexta=4, sábado=5, domingo=6)
DIAS_IDA = {3, 4, 5}    # ida (saindo de JP ou CG):  quinta, sexta e sábado
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


def partir(texto, limite):
    """Divide o texto em partes de até `limite` caracteres, sempre quebrando entre linhas."""
    partes, atual = [], ""
    for linha in texto.split("\n"):
        while len(linha) > limite:  # linha gigante: corta (não deve acontecer)
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(linha[:limite])
            linha = linha[limite:]
        if atual and len(atual) + 1 + len(linha) > limite:
            partes.append(atual)
            atual = linha
        else:
            atual = f"{atual}\n{linha}" if atual else linha
    if atual:
        partes.append(atual)
    return partes


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
    partes = partir(texto, 1000)  # mensagens longas vão em várias partes, na ordem
    algum = False
    for tel, chave in lista:
        for n, parte in enumerate(partes, 1):
            if len(partes) > 1:
                parte = f"({n}/{len(partes)})\n{parte}"
            try:
                r = requests.get("https://api.callmebot.com/whatsapp.php", timeout=30,
                                 params={"phone": tel, "apikey": chave, "text": parte})
                resposta = re.sub(r"<[^>]+>", " ", r.text)
                # esconde telefone e chave: os logs de repositório público são visíveis a todos
                resposta = resposta.replace(tel, "***").replace(tel.lstrip("+"), "***")
                resposta = resposta.replace(chave, "***")
                print("WhatsApp", "***", f"parte {n}/{len(partes)}", r.status_code, resposta[:200])
                ok = r.status_code in (200, 203) and "invalid" not in resposta.lower()
                algum = algum or ok
            except Exception as e:
                print("WhatsApp erro", "***", str(e).replace(chave, "***"))
            if n < len(partes):
                time.sleep(3)  # dá tempo de uma parte chegar antes da outra
    return algum


def ntfy(titulo, texto, link=None, prioridade=5):
    topico = os.getenv("NTFY_TOPIC", "").strip()
    if not topico:
        print("ntfy ainda não configurado.")
        return False
    partes = partir(texto, 3500)  # o ntfy aceita ~4 mil caracteres por mensagem
    algum = False
    for n, parte in enumerate(partes, 1):
        t = titulo if len(partes) == 1 else f"{titulo} ({n}/{len(partes)})"
        corpo = {"topic": topico, "title": t, "message": parte,
                 "priority": prioridade, "tags": ["bus"]}
        if link:
            corpo["click"] = link
        try:
            r = requests.post("https://ntfy.sh", json=corpo, timeout=30)
            print("ntfy:", r.status_code, r.text[:120])
            algum = algum or r.status_code == 200
        except Exception as e:
            print("ntfy erro:", e)
    return algum


def avisar(titulo, texto, link=None, prioridade=5):
    """Manda por ntfy e WhatsApp; vale se pelo menos um funcionar."""
    a = ntfy(titulo, texto, link, prioridade)
    b = whatsapp(f"{titulo}\n\n{texto}")
    return a or b


def url(o, d, data):
    return (f"{BASE}/{CIDADES[o]}/{CIDADES[d]}/?departure_date={data}"
            f"&passengers={PASSAGEIROS}")


def rotulo_data(data):
    d = dt.date.fromisoformat(data)
    return f"{SEMANA[d.weekday()]} {d.day:02d}/{d.month:02d}"


def montar(novos, tipo):
    """Texto com as datas novas de um tipo ('100' ou '50'): uma linha por rota,
    indicando ida ou volta e o dia da semana de cada data."""
    grupos = {}
    for k in novos:
        o, d, data, t = k.split(">")
        if t == tipo:
            grupos.setdefault((o, d), []).append(data)
    linhas = []
    for o, d in ROTAS:
        if (o, d) in grupos:
            sentido = "IDA" if (o, d) in IDA else "VOLTA"
            datas = ", ".join(rotulo_data(x) for x in sorted(grupos[(o, d)]))
            linhas.append(f"{sentido} {SIGLAS[o]} → {SIGLAS[d]}: {datas}")
    return "\n".join(linhas)


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
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=45000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass  # seguiu carregando: lê o que já está na tela
                    page.wait_for_timeout(1500)
                    texto = page.inner_text("body")
                except Exception as e:
                    print("Erro", o, d, data, e)
                    resumo.append(f"{SIGLAS[o]}>{SIGLAS[d]} {data}: ERRO ({str(e)[:120]})")
                    continue
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
    enviados = set()
    # 100% primeiro (aviso forte); depois os 50% (aviso normal)
    for tipo, titulo, prio in (("100", "🎉 ID JOVEM 100% GRÁTIS", 5),
                               ("50", "🚌 ID Jovem 50% de desconto", 3)):
        chaves = sorted((k for k in novos if k.endswith(">" + tipo)),
                        key=lambda k: (k.split(">")[2], k))
        if not chaves:
            continue
        texto = montar(novos, tipo) + "\n\n" + LEGENDA
        if avisar(titulo, texto, link=None, prioridade=prio):
            enviados.update(chaves)
    if not novos:
        print("Nada novo.")
    # guarda o que já foi avisado; o que falhou no envio fica de fora e é avisado na próxima rodada
    json.dump(sorted(k for k in achados if k in antigo or k in enviados), open(ESTADO, "w"))


if __name__ == "__main__":
    main()
