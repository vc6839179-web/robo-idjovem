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
SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

IDA = [("João Pessoa", "Fortaleza"), ("Campina Grande", "Fortaleza"),
       ("Campina Grande", "Juazeiro do Norte")]
ROTAS = IDA + [(d, o) for o, d in IDA]  # ida e volta de cada rota

DIAS = int(os.getenv("DIAS", "60"))                  # quantos dias à frente pesquisar
PASSAGEIROS = os.getenv("PASSAGEIROS", "").strip()   # ex.: 3:1  (código do Jovem)
CATEDRAL = os.getenv("CATEDRAL", "1") != "0"        # coloque CATEDRAL=0 para desligar a Catedral
DEBUG = os.getenv("DEBUG") == "1"
TESTE = os.getenv("TESTE") == "1"
RESUMO = os.getenv("RESUMO") == "1"   # manda TUDO que está disponível agora, mesmo o que já foi avisado
ESTADO = "estado.json"

# Dias da semana que interessam (segunda=0, terça=1, quarta=2, quinta=3, sexta=4, sábado=5, domingo=6)
DIAS_IDA = {3, 4, 5}       # ida (saindo de JP ou CG): quinta, sexta e sábado
DIAS_VOLTA_AVISO = {6, 0}  # volta (saindo de FOR ou JDN) que gera aviso normal: domingo e segunda
DIAS_VOLTA = {5, 6, 0}     # volta que o robô consulta: inclui sábado, usado só nas combinações ida+volta

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


def mkchave(o, d, data, tipo, emp="G"):
    """Chave de uma passagem encontrada. Guanabara (G) mantém o formato antigo; Catedral (C) leva um 5º campo."""
    return f"{o}>{d}>{data}>{tipo}" + ("" if emp == "G" else f">{emp}")


def partes(k):
    p = k.split(">")
    return p[0], p[1], p[2], p[3], (p[4] if len(p) > 4 else "G")


def pascoa(ano):
    """Data da Páscoa (algoritmo gregoriano) - base para Carnaval, Sexta Santa e Corpus Christi."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e, f = b // 4, b % 4, (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(ano, mes, dia)


def feriados(ano):
    p, dia = pascoa(ano), dt.timedelta
    return {
        dt.date(ano, 1, 1): "Ano Novo",
        p - dia(days=48): "Carnaval",
        p - dia(days=47): "Carnaval",
        p - dia(days=2): "Sexta-feira Santa",
        dt.date(ano, 4, 21): "Tiradentes",
        dt.date(ano, 5, 1): "Dia do Trabalho",
        p + dia(days=60): "Corpus Christi",
        dt.date(ano, 9, 7): "Independência",
        dt.date(ano, 10, 12): "Nossa Senhora Aparecida",
        dt.date(ano, 11, 2): "Finados",
        dt.date(ano, 11, 15): "Proclamação da República",
        dt.date(ano, 11, 20): "Consciência Negra",
        dt.date(ano, 12, 25): "Natal",
    }


def feriadao(data):
    """Se a data (AAAA-MM-DD) cai num feriadão, devolve (nome, data_do_feriado); senão None.
    Feriadão = feriado em segunda, terça, quinta ou sexta (com "enforcado" nos dias de ponte).
    Vale do 2º dia antes do início da folga até o último dia da folga."""
    d = dt.date.fromisoformat(data)
    um = dt.timedelta(days=1)
    fer = {}
    for ano in (d.year - 1, d.year, d.year + 1):
        fer.update(feriados(ano))
    for h in sorted(fer):
        if h.weekday() not in (0, 1, 3, 4):
            continue
        ponte = {h - um} if h.weekday() == 1 else {h + um} if h.weekday() == 3 else set()

        def folga(x):
            return x.weekday() >= 5 or x in fer or x in ponte
        ini = fim = h
        while folga(ini - um):
            ini -= um
        while folga(fim + um):
            fim += um
        if ini - 2 * um <= d <= fim:
            return fer[h], h
    return None


def rotulo_data(data, usados=None):
    d = dt.date.fromisoformat(data)
    txt = f"{SEMANA[d.weekday()]} {d.day:02d}/{d.month:02d}"
    f = feriadao(data)
    if f:
        txt += " 🏖️"
        if usados is not None:
            usados.add(f)
    return txt


def rodape(usados):
    return "\n".join(f"🏖️ Feriadão de {n} ({SEMANA[h.weekday()]} {h.day:02d}/{h.month:02d})"
                     for n, h in sorted(usados, key=lambda x: x[1]))


def montar(novos, tipo):
    """Texto com as datas novas de um tipo ('100' ou '50'): uma linha por rota,
    indicando ida ou volta e o dia da semana de cada data. 🏖️ marca feriadão."""
    grupos = {}
    for k in novos:
        o, d, data, t, emp = partes(k)
        if t == tipo:
            grupos.setdefault((o, d, emp), []).append(data)
    usados, linhas = set(), []
    for o, d in ROTAS:
        for emp in ("G", "C"):
            if (o, d, emp) in grupos:
                sentido = "IDA" if (o, d) in IDA else "VOLTA"
                empresa = " (Catedral)" if emp == "C" else ""
                datas = ", ".join(rotulo_data(x, usados) for x in sorted(grupos[(o, d, emp)]))
                linhas.append(f"{sentido} {SIGLAS[o]} → {SIGLAS[d]}{empresa}: {datas}")
    r = rodape(usados)
    return "\n".join(linhas) + ("\n\n" + r if r else "")


# (dia da semana da IDA, dias até a VOLTA): quinta -> domingo (3, 3), sexta -> domingo (4, 2)
# e quinta -> sábado (3, 2). Para outras combinações, acrescente pares, ex.: (5, 1) = sábado -> domingo.
# Obs.: a volta precisa estar em DIAS_VOLTA (acima) para ser consultada.
COMBOS = [(3, 3), (4, 2), (3, 2)]


def avisavel(k):
    """Aviso normal só para os dias de interesse (volta de sábado serve apenas às combinações)."""
    o, d, data, _, _ = partes(k)
    return dt.date.fromisoformat(data).weekday() in (DIAS_IDA if (o, d) in IDA else DIAS_VOLTA_AVISO)


def melhor(achados, o, d, data):
    """(tipo, empresa) da melhor passagem nessa rota/data: 100% antes de 50%; Guanabara antes da Catedral."""
    for t in ("100", "50"):
        for emp in ("G", "C"):
            if mkchave(o, d, data, t, emp) in achados:
                return t, emp
    return None


def achar_pares(achados):
    """Ida numa rota + volta na rota inversa, ambas disponíveis (100% ou 50%)."""
    pares = {}
    for o, d in IDA:
        # a volta sai de d para qualquer cidade que também vá até d (ex.: ida CG→FOR, volta FOR→JP ou FOR→CG)
        destinos_volta = [x for x, d2 in IDA if d2 == d]
        datas = {partes(k)[2] for k in achados if k.startswith(f"{o}>{d}>")}
        for data in sorted(datas):
            dia = dt.date.fromisoformat(data)
            a = melhor(achados, o, d, data)
            if not a:
                continue
            for dia_ida, depois in COMBOS:
                if dia.weekday() != dia_ida:
                    continue
                volta = (dia + dt.timedelta(days=depois)).isoformat()
                for x in destinos_volta:
                    b = melhor(achados, d, x, volta)
                    if not b:
                        continue
                    sa, sb = a[0] + ("" if a[1] == "G" else a[1]), b[0] + ("" if b[1] == "G" else b[1])
                    rota = f"{o}>{d}" if x == o else f"{o}>{d}>{x}"  # mesma rota mantém a chave antiga
                    pares[f"par|{rota}|{data}|{volta}|{sa}+{sb}"] = (o, d, data, volta, a, b, x)
    return pares


def montar_pares(pares):
    grupos = {"100": [], "misto": [], "50": []}
    usados = set()
    for k, (o, d, ida, volta, (a, ea), (b, eb), x) in sorted(pares.items(), key=lambda kv: (kv[1][2], kv[0])):
        ci = ' (Catedral)' if ea == 'C' else ''
        cv = ' (Catedral)' if eb == 'C' else ''
        if x == o:
            linha = (f"{SIGLAS[o]} ⇄ {SIGLAS[d]}: ida {rotulo_data(ida, usados)}{ci} → "
                     f"volta {rotulo_data(volta, usados)}{cv}")
        else:  # volta por outra cidade de origem
            linha = (f"IDA {SIGLAS[o]} → {SIGLAS[d]} {rotulo_data(ida, usados)}{ci} + "
                     f"VOLTA {SIGLAS[d]} → {SIGLAS[x]} {rotulo_data(volta, usados)}{cv}")
        if a == b:
            grupos[a].append(linha)
        else:
            grupos["misto"].append(linha + f" (ida {a}% · volta {b}%)")
    blocos = []
    if grupos["100"]:
        blocos.append("🏆 IDA E VOLTA 100% GRÁTIS\n" + "\n".join(grupos["100"]))
    if grupos["misto"]:
        blocos.append("🥇 UMA PERNA GRÁTIS + OUTRA COM 50%\n" + "\n".join(grupos["misto"]))
    if grupos["50"]:
        blocos.append("🥈 IDA E VOLTA COM 50%\n" + "\n".join(grupos["50"]))
    r = rodape(usados)
    return "\n\n".join(blocos) + ("\n\n" + r if r else "")


# ---------------------------------------------------------------- Catedral (ClickBus)
CAT_BASE = "https://catedral.clickbus.com.br/onibus"
CAT_SLUGS = {"João Pessoa": "joao-pessoa-pb", "Fortaleza": "fortaleza-ce-todos"}  # "-todos" = todos os terminais
CAT_ROTAS = [("João Pessoa", "Fortaleza"), ("Fortaleza", "João Pessoa")]  # a Catedral só opera esta rota
SEM_GRATUIDADE = re.compile(r"não há gratuidades", re.I)
# Quando a data pedida não tem viagem, o site mostra as viagens da data MAIS PRÓXIMA com esta frase.
# Sem este filtro, o robô atribuiria a viagem de outro dia à data consultada.
SEM_VIAGEM_NO_DIA = re.compile(r"essa linha não tem viagens", re.I)
# pop-up "Passagens com benefícios": "Id Jovem (50%)  2 disponíveis, 0 ocupado"
BENEFICIO = re.compile(r"id\s*jovem\s*\((100|50)\s*%\)\s*(\d+)\s*dispon", re.I)
MAX_VIAGENS = 12  # máximo de horários clicados por dia


def cat_url(o, d, data):
    return f"{CAT_BASE}/{CAT_SLUGS[o]}/{CAT_SLUGS[d]}?departureDate={data}&gratuity=true"


def cat_tipos(texto):
    """{'100': vagas, '50': vagas} lido do pop-up; só entra o que tem pelo menos 1 vaga."""
    tipos = {}
    for t, n in BENEFICIO.findall(texto):
        if int(n) > 0:
            tipos[t] = max(int(n), tipos.get(t, 0))
    return tipos


def cat_abrir(page, u):
    page.goto(u, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(1500)


CAT_SELETORES = ("button:has-text('R$')", "[role=button]:has-text('R$')", "a:has-text('R$')",
                 "[class*=fare]:has-text('R$')", "[class*=price]:has-text('R$')", "text=/R\\$\\s*\\d/")


def cat_contagens(page):
    """Quantos elementos cada seletor encontra (serve para calibrar)."""
    out = {}
    for sel in CAT_SELETORES:
        try:
            out[sel] = page.locator(sel).count()
        except Exception:
            out[sel] = -1
    return out


def cat_abrir_popup(page, u, j, recarregar):
    """Clica na j-ésima tarifa (testando os seletores na ordem) até abrir o pop-up de benefícios.
    Devolve o texto da página com o pop-up, ou None."""
    for sel in CAT_SELETORES:
        if recarregar:
            cat_abrir(page, u)
        recarregar = True  # nas tentativas seguintes, recarrega a lista antes de clicar
        loc = page.locator(sel)
        if loc.count() <= j:
            continue
        try:
            loc.nth(j).click(timeout=6000)
            page.wait_for_selector("text=/Passagens com benef/i", timeout=6000)
        except Exception:
            continue
        page.wait_for_timeout(500)
        return page.inner_text("body")
    return None


def varrer_catedral(nav, hoje, resumo):
    """Devolve ({chave: url}, páginas). A busca já vem filtrada (gratuity=true); para cada
    viagem clica na tarifa e lê o pop-up de benefícios, procurando Id Jovem com vagas."""
    achados, paginas = {}, 0
    page = nav.new_context(locale="pt-BR", viewport={"width": 1280, "height": 900}).new_page()
    page.route("**/*", lambda r: r.abort()
               if r.request.resource_type in ("image", "media", "font") else r.continue_())
    if DEBUG:  # guarda respostas JSON que falem de Jovem/gratuidade: pode haver um jeito mais rápido de ler
        cont = [0]

        def captura(resp):
            try:
                if cont[0] < 15 and "json" in (resp.headers.get("content-type") or ""):
                    corpo = resp.text()
                    if re.search(r"jovem|gratuity|benefit", corpo, re.I):
                        cont[0] += 1
                        open(f"debug/catedral-json-{cont[0]}.txt", "w").write(resp.url + "\n\n" + corpo[:200000])
            except Exception:
                pass
        page.on("response", captura)

    amostras = 0  # no debug, guarda prints só de dias que têm resultado (até 4)
    mapa = {}     # rota -> datas em que a Catedral mostra viagens com gratuidade (para entender o padrão)
    for o, d in CAT_ROTAS:
        for i in range(max(DIAS, 60) if DEBUG else DIAS):
            dia = hoje + dt.timedelta(days=i)
            if not DEBUG and dia.weekday() not in (DIAS_IDA if (o, d) in IDA else DIAS_VOLTA):
                continue
            data, u = dia.isoformat(), None
            u = cat_url(o, d, data)
            rota = f"CATEDRAL {SIGLAS[o]}>{SIGLAS[d]} {data}: "
            nome = f"debug/catedral-{SIGLAS[o]}-{SIGLAS[d]}-{data}"
            try:
                cat_abrir(page, u)
                paginas += 1
                texto = page.inner_text("body")
                if SEM_GRATUIDADE.search(texto):
                    if DEBUG and i == 0:  # uma amostra de "sem gratuidade"
                        open(nome + ".txt", "w").write(texto)
                    resumo.append(rota + "nada (sem gratuidades)")
                    continue
                if SEM_VIAGEM_NO_DIA.search(texto):
                    resumo.append(rota + "linha sem viagens nesse dia (o site mostra outra data: ignorado)")
                    continue
                mapa.setdefault((o, d), []).append(data)
                amostra = DEBUG and amostras < 4
                contagens = cat_contagens(page)
                if amostra:
                    amostras += 1
                    open(nome + ".txt", "w").write(texto)
                    open(nome + ".html", "w").write(page.content()[:600000])
                    page.screenshot(path=nome + ".png", full_page=True)
                    open(nome + "-seletores.txt", "w").write(json.dumps(contagens, indent=1))
                n = min(max(contagens.values()), MAX_VIAGENS) if contagens else 0
                tipos, abriu = {}, 0
                for j in range(n):
                    popup = cat_abrir_popup(page, u, j, recarregar=bool(j))
                    if j:
                        paginas += 1
                    if popup is None:
                        continue  # esta viagem não abriu pop-up de benefícios
                    abriu += 1
                    if amostra:
                        open(f"{nome}-popup{j}.txt", "w").write(popup)
                        open(f"{nome}-popup{j}.html", "w").write(page.content()[:600000])
                        page.screenshot(path=f"{nome}-popup{j}.png")
                    for t, v in cat_tipos(popup).items():
                        tipos[t] = max(v, tipos.get(t, 0))
                for t, v in tipos.items():
                    achados[mkchave(o, d, data, t, "C")] = u
                    print(f"ACHEI (Catedral): {o} > {d} {data} ID Jovem {t}% ({v} vagas)")
                if tipos:
                    det = ", ".join(f"{t}% ({v} vagas)" for t, v in sorted(tipos.items(), key=lambda x: -int(x[0])))
                    resumo.append(rota + f"ACHEI ({det})")
                elif n == 0:
                    resumo.append(rota + f"SEM botões de tarifa reconhecidos {contagens}")
                elif abriu == 0:
                    resumo.append(rota + f"{n} viagem(ns), mas o pop-up de benefícios NÃO abriu {contagens}")
                else:
                    resumo.append(rota + f"{abriu} pop-up(s) lido(s), sem Id Jovem com vaga")
            except Exception as e:
                print("Erro Catedral", o, d, data, str(e)[:150])
                resumo.append(rota + f"ERRO ({str(e)[:120]})")
    if DEBUG:  # mapa: em que dias a Catedral oferece alguma gratuidade
        for (o, d), datas in mapa.items():
            por_dia = {}
            for x in datas:
                dd = dt.date.fromisoformat(x)
                por_dia[SEMANA[dd.weekday()]] = por_dia.get(SEMANA[dd.weekday()], 0) + 1
            lista = ", ".join(f"{SEMANA[dt.date.fromisoformat(x).weekday()]} {x[8:]}/{x[5:7]}" for x in datas)
            resumo.append(f"MAPA CATEDRAL {SIGLAS[o]}>{SIGLAS[d]}: {len(datas)} dia(s) com gratuidade "
                          f"por dia da semana {por_dia} -> {lista or 'nenhum'}")
        if not mapa:
            resumo.append("MAPA CATEDRAL: nenhum dia com gratuidade nos dias consultados")
    page.context.close()
    return achados, paginas


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
        if CATEDRAL:  # etapa isolada: se falhar, a Guanabara segue normal
            try:
                ach_c, pag_c = varrer_catedral(nav, hoje, resumo)
                achados.update(ach_c)
                paginas += pag_c
            except Exception as e:
                print("Catedral falhou (a Guanabara segue normal):", e)
        nav.close()
    seg = time.time() - inicio
    print(f"Tempo: {seg:.0f}s para {paginas} páginas ({seg / max(paginas, 1):.1f}s por página)")

    if DEBUG:
        print("\n===== RESUMO DO DEBUG =====")
        print("\n".join(resumo))
        print("===========================\n")

    novos = {k: v for k, v in achados.items() if avisavel(k) and (RESUMO or k not in antigo)}
    enviados = set()
    # destaque: ida (quinta) + volta (domingo) disponíveis -> vai ANTES dos avisos normais, que continuam
    pares = achar_pares(achados)
    novos_pares = pares if RESUMO else {k: v for k, v in pares.items() if k not in antigo}
    enviados_pares = set()
    if novos_pares:
        prio = 5 if any("100" in (v[4][0], v[5][0]) for v in novos_pares.values()) else 4
        titulo = ("📋 RESUMO DO DIA: " if RESUMO else "") + "⭐ IDA E VOLTA DISPONÍVEL"
        if avisar(titulo, montar_pares(novos_pares), link=None, prioridade=prio):
            enviados_pares.update(novos_pares)
    # 100% primeiro (aviso forte); depois os 50% (aviso normal)
    for tipo, titulo, prio in (("100", "🎉 ID JOVEM 100% GRÁTIS", 5),
                               ("50", "🚌 ID Jovem 50% de desconto", 3)):
        chaves = sorted((k for k in novos if partes(k)[3] == tipo),
                        key=lambda k: (partes(k)[2], k))
        if not chaves:
            continue
        texto = montar(novos, tipo)
        if RESUMO:
            titulo = "📋 RESUMO DO DIA: " + titulo.split(" ", 1)[1]
        if avisar(titulo, texto, link=None, prioridade=prio):
            enviados.update(chaves)
    if not novos and not novos_pares:
        print("Nada novo." if not RESUMO else "Resumo: nenhuma passagem disponível, nada enviado.")
    # guarda o que já foi avisado; o que falhou no envio fica de fora e é avisado na próxima rodada
    estado = {k for k in achados if k in antigo or k in enviados}
    estado |= {k for k in pares if k in antigo or k in enviados_pares}
    json.dump(sorted(estado), open(ESTADO, "w"))


if __name__ == "__main__":
    main()
