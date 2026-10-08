"""Monitor de passagens ID Jovem - Viaje Guanabara -> aviso no ntfy."""
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
    """Manda o aviso pelo ntfy."""
    return ntfy(titulo, texto, link, prioridade)


def url(o, d, data):
    return (f"{BASE}/{CIDADES[o]}/{CIDADES[d]}/?departure_date={data}"
            f"&passengers={PASSAGEIROS}")


def mkchave(o, d, data, tipo):
    """Chave de uma passagem encontrada: origem>destino>data>tipo (tipo = 100 ou 50)."""
    return f"{o}>{d}>{data}>{tipo}"


def partes(k):
    p = k.split(">")
    return p[0], p[1], p[2], p[3]


VALORES = {}  # chave da passagem -> menor preço encontrado (aparece no aviso)


def valor(o, d, data, tipo):
    """' R$ 98' ou ' R$ 84,50' (vazio se o preço não foi guardado)."""
    v = VALORES.get(mkchave(o, d, data, tipo))
    if v is None:
        return ""
    return " R$ " + f"{v:.2f}".replace(".", ",").removesuffix(",00")


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


def rotulo_data(data, usados=None, extra=""):
    d = dt.date.fromisoformat(data)
    txt = f"{SEMANA[d.weekday()]} {d.day:02d}/{d.month:02d}{extra}"
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
        o, d, data, t = partes(k)
        if t == tipo:
            grupos.setdefault((o, d), []).append(data)
    usados, linhas = set(), []
    for o, d in ROTAS:
        if (o, d) in grupos:
            sentido = "IDA" if (o, d) in IDA else "VOLTA"
            datas = ", ".join(rotulo_data(x, usados, valor(o, d, x, tipo)) for x in sorted(grupos[(o, d)]))
            linhas.append(f"{sentido} {SIGLAS[o]} → {SIGLAS[d]}: {datas}")
    r = rodape(usados)
    return "\n".join(linhas) + ("\n\n" + r if r else "")


# (dia da semana da IDA, dias até a VOLTA): quinta -> domingo (3, 3), sexta -> domingo (4, 2)
# e quinta -> sábado (3, 2). Para outras combinações, acrescente pares, ex.: (5, 1) = sábado -> domingo.
# Obs.: a volta precisa estar em DIAS_VOLTA (acima) para ser consultada.
COMBOS = [(3, 3), (4, 2), (3, 2)]


def avisavel(k):
    """Aviso normal só para os dias de interesse (volta de sábado serve apenas às combinações)."""
    o, d, data, _ = partes(k)
    dia = dt.date.fromisoformat(data).weekday()
    return dia in (DIAS_IDA if (o, d) in IDA else DIAS_VOLTA_AVISO)


def melhor(achados, o, d, data):
    """Tipo da melhor passagem nessa rota/data ('100' antes de '50'), ou None."""
    for t in ("100", "50"):
        if mkchave(o, d, data, t) in achados:
            return t
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
                    rota = f"{o}>{d}" if x == o else f"{o}>{d}>{x}"  # mesma rota mantém a chave antiga
                    pares[f"par|{rota}|{data}|{volta}|{a}+{b}"] = (o, d, data, volta, a, b, x)
    return pares


def montar_pares(pares):
    grupos = {"100": [], "misto": [], "50": []}
    usados = set()
    for k, (o, d, ida, volta, a, b, x) in sorted(pares.items(), key=lambda kv: (kv[1][2], kv[0])):
        if x == o:
            linha = (f"{SIGLAS[o]} ⇄ {SIGLAS[d]}: ida {rotulo_data(ida, usados, valor(o, d, ida, a))} → "
                     f"volta {rotulo_data(volta, usados, valor(d, x, volta, b))}")
        else:  # volta por outra cidade de origem
            linha = (f"IDA {SIGLAS[o]} → {SIGLAS[d]} {rotulo_data(ida, usados, valor(o, d, ida, a))} + "
                     f"VOLTA {SIGLAS[d]} → {SIGLAS[x]} {rotulo_data(volta, usados, valor(d, x, volta, b))}")
        if a == b:
            grupos[a].append(linha)
        else:
            grupos["misto"].append(linha + f" (ida {a}% · volta {b}%)")
    blocos = []
    if grupos["100"]:
        blocos.append("🏆 IDA E VOLTA 100% GRÁTIS\n" + "\n".join(grupos["100"]))
    if grupos["misto"]:
        blocos.append("🥇 UMA GRÁTIS + OUTRA COM 50%\n" + "\n".join(grupos["misto"]))
    if grupos["50"]:
        blocos.append("🥈 IDA E VOLTA COM 50%\n" + "\n".join(grupos["50"]))
    r = rodape(usados)
    return "\n\n".join(blocos) + ("\n\n" + r if r else "")


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
                    VALORES[mkchave(o, d, data, tipo)] = preco
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

    novos = {k: v for k, v in achados.items() if avisavel(k) and (RESUMO or k not in antigo)}
    enviados = set()
    # destaque: ida (quinta) + volta (domingo) disponíveis -> vai ANTES dos avisos normais, que continuam
    pares = achar_pares(achados)
    novos_pares = pares if RESUMO else {k: v for k, v in pares.items() if k not in antigo}
    enviados_pares = set()
    if novos_pares:
        prio = 5 if any("100" in (v[4], v[5]) for v in novos_pares.values()) else 4
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
