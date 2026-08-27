"""Integrações externas: Banco do Brasil, SIAFE, SharePoint e helpers de interação com o SEI."""

from __future__ import annotations

from selenium.webdriver.common.print_page_options import PrintOptions
from datetime import datetime, timedelta
from contextlib import closing
from num2words import num2words
from typing import Callable
from pathlib import Path
import logging
import sqlite3
import base64

from jupiter import Siafe, SEI, SharePoint
import automaweb

from .config import (
    URL_BB, URL_SIAFE, CAMINHO_DRIVER_EDGE, CAMINHO_HERMES, PASTA_GR,
    CONTA_PROCESSAR, NOME_PDF_PGE, NOME_TITULO_GR, NOME_TITULO_COMPROVANTE_BB,
    NOME_TITULO_COMPROVANTE_DJO, CAMINHO_COOKIES_SHAREPOINT, NOMES_MESES,
    PASTA_BASE_SHAREPOINT, PREFIXO_ARQUIVO_DIARIO, SITE_SHAREPOINT,
)
from .core import ErroSEI, ErroSIAFE, ErroBB, ErroLoginSiafe, ErroExtracao, ErroValidacao, ErroDownload
from .utils import (
    buscar_regex,
    extrair_texto_pdf,
    formatar_nome_arquivo,
    formatar_moeda,
    localizar_comprovante_em_disco,
    aguardar_novo_pdf,
    mover_gr_para_destino,
    mensagem_curta,
    navegador_perdido,
    _registrar_arquivo_pasta_gr,
)

log = logging.getLogger("jupiter.processarDJ")

# ---------------------------------------------------------------------------
# Banco do Brasil — download de comprovantes
# ---------------------------------------------------------------------------
XPATHS_MENSAGEM_ERRO_BB = (
    '//*[contains(@id,"mensagens")]',
    '//*[contains(@id,"mensagem")]',
    '//*[contains(@class,"mensagem-erro")]',
    '//*[contains(@class,"erro")]',
    '//*[contains(@class,"alert")]',
)

def _capturar_mensagem_erro_bb(nav) -> str | None:
    """Tenta ler a mensagem de validação exibida pelo BB (conta/CNPJ/data incorretos)."""
    for xpath in XPATHS_MENSAGEM_ERRO_BB:
        try:
            if nav.verifica_existe(xpath, timeout=1):
                texto = nav.obter_texto(xpath).strip()
                if texto:
                    return texto
        except Exception:
            continue
    return None


_cache_contabilizacoes: list[dict] | None = None


def _listar_contabilizacoes() -> list[dict]:
    """Lê a tabela contabilizacoes uma vez e cacheia em memória pelo tempo de vida do processo."""
    global _cache_contabilizacoes
    if _cache_contabilizacoes is None:
        if not CAMINHO_HERMES.exists():
            _cache_contabilizacoes = []
        else:
            try:
                with closing(sqlite3.connect(CAMINHO_HERMES)) as con:
                    con.row_factory = sqlite3.Row
                    cursor = con.execute(
                        "SELECT id, num_documento, valor, observacao, data FROM contabilizacoes"
                    )
                    _cache_contabilizacoes = [dict(r) for r in cursor.fetchall()]
            except sqlite3.Error as e:
                log.error(f"Erro de Banco de dados: {e}", exc_info=True)
                _cache_contabilizacoes = []
    return _cache_contabilizacoes


def _buscar_data_pagamento_por_conta(conta_judicial: str) -> str | None:
    """Busca em contabilizacoes (somente leitura) a 'Data do Pagamento' de um
    resgate já contabilizado para a conta judicial informada."""
    if not conta_judicial:
        return None

    row = next(
        (r for r in _listar_contabilizacoes() if conta_judicial in (r["observacao"] or "")),
        None,
    )
    if not row:
        return None

    data_pagamento = buscar_regex(
        row["observacao"],
        r"Data\s+do\s+Pagamento[\s.]*:?\s*(\d{2}/\d{2}/\d{4})",
    )
    return data_pagamento


def _tentar_resgate_bb(nav, conta_judicial: str, cnpj: str, data_alvara: str) -> str | None:
    """Uma tentativa de consulta de resgate no BB. Retorna mensagem de erro, se houver."""
    data_inicio = datetime.strptime(data_alvara, "%d/%m/%Y")
    data_fim = (data_inicio + timedelta(days=30)).strftime("%d/%m/%Y")

    nav.verifica_clicavel('//*[@id="formulario:tipoPessoa:1"]', timeout=5)
    nav.clicar('//*[@id="formulario:tipoPessoa:1"]')
    nav.digitar('//*[@id="formulario:contaJudicial"]', conta_judicial)
    nav.digitar('//*[@id="formulario:cnpjPessoaAux"]', cnpj)
    nav.digitar('//*[@id="formulario:dtPagamento"]', data_alvara)
    nav.digitar('//*[@id="formulario:dtPagamento2"]', data_fim)
    nav.clicar('//*[@id="formulario:btnContinuar"]')

    if not nav.verifica_existe('//*[@id="tblResgate"]/tbody/tr/td[1]/input', timeout=8):
        mensagem = _capturar_mensagem_erro_bb(nav)
        return mensagem or "dados invalidos"
    return None


def baixar_comprovante_bb(dados_oficio: dict) -> Path:
    """Localiza o comprovante na PASTA_GR ou navega no site do BB para obtê-lo."""
    data_alvara = dados_oficio.get("data_alvara")
    conta_judicial = dados_oficio.get("conta_judicial")
    cnpj = dados_oficio.get("cnpj")

    if not all([data_alvara, conta_judicial, cnpj]):
        raise ErroValidacao("Dados insuficientes para consulta BB.")

    caminho_em_disco = localizar_comprovante_em_disco(conta_judicial)
    if caminho_em_disco:
        log.info(f"Comprovante já disponível para conta judicial '{conta_judicial}'.")
        return Path(caminho_em_disco)

    try:
        datetime.strptime(data_alvara, "%d/%m/%Y")
    except (ValueError, TypeError) as e:
        raise ErroValidacao(f"Data inválida para consulta BB: {data_alvara} — {e}") from e

    nav = automaweb.Navegador()
    try:
        nav.abrir_driver_undetected(caminho_driver=CAMINHO_DRIVER_EDGE)
        nav.abrir_url(URL_BB)

        erro = _tentar_resgate_bb(nav, conta_judicial, cnpj, data_alvara)
        if erro:
            log.warning(f"BB não retornou resgate para conta {conta_judicial}: {erro}")

            data_fallback = _buscar_data_pagamento_por_conta(conta_judicial)
            if not data_fallback or data_fallback == data_alvara:
                raise ErroDownload(f"BB não retornou resgate para conta {conta_judicial}: {erro}")

            log.info(
                f"Tentando novamente para conta {conta_judicial} com a data {data_fallback}."
            )
            nav.abrir_url(URL_BB)
            erro = _tentar_resgate_bb(nav, conta_judicial, cnpj, data_fallback)
            if erro:
                raise ErroDownload(
                    f"BB não retornou resgate para conta {conta_judicial} mesmo com a data {data_fallback}"
                )

        nav.clicar('//*[@id="tblResgate"]/tbody/tr/td[1]/input')
        nav.clicar('//*[@id="formulario:btnContinuar"]')
        nav.clicar('//*[@id="tblFinalidade"]/tbody/tr[1]/td[1]/input')
        nav.clicar('//*[@id="formulario:btnContinuar"]')
        nav.clicar('//*[@id="formulario:botaoPdf"]')

        caminho_comprovante = PASTA_GR / f"{conta_judicial}.pdf"
        pdf_base64 = nav.driver.print_page(PrintOptions())
        with open(caminho_comprovante, "wb") as f:
            f.write(base64.b64decode(pdf_base64))
        _registrar_arquivo_pasta_gr(str(caminho_comprovante))

        return caminho_comprovante

    except ErroDownload:
        raise
    except Exception as e:
        raise ErroBB(f"Erro durante navegação no site do BB: {mensagem_curta(e)}") from e
    finally:
        try:
            nav.driver.quit()
        except Exception as e:
            log.warning(f"Erro ao fechar driver BB: {e}")


def consultar_conta_judicial(lista_dados: list[dict]) -> dict | None:
    """
    Para cada conjunto de dados (conta judicial, CNPJ, data), tenta obter
    o comprovante do BB (do disco ou baixando) e extrair seus dados.
    """
    from .extractors import ExtratorComprovante, ExtratorAgendamento

    extratores_resgate_bb = (ExtratorComprovante, ExtratorAgendamento)

    ultimo_dado_valido = None

    for i, dados_oficio in enumerate(lista_dados):
        conta_judicial = dados_oficio.get("conta_judicial")
        if not conta_judicial:
            continue

        try:
            caminho_comprovante = baixar_comprovante_bb(dados_oficio)
        except (ErroBB, ErroDownload, ErroValidacao) as e:
            if navegador_perdido(e):
                raise
            log.warning(f"Erro ao obter comprovante para conta {conta_judicial}: {mensagem_curta(e)}")
            continue

        texto_comprovante = extrair_texto_pdf(str(caminho_comprovante))
        extrator = next(
            (e for e in extratores_resgate_bb if e.pode_extrair(texto_comprovante)), None
        )
        if not texto_comprovante.strip() or extrator is None:
            log.warning(f"Comprovante para {conta_judicial} não contém o título esperado.")
            continue

        dados = extrator.extrair(texto_comprovante)
        if not dados:
            log.warning(f"Não foi possível extrair dados do comprovante de {conta_judicial}")
            continue

        dados["caminho_comprovante"] = str(caminho_comprovante)
        dados["conta_judicial"] = conta_judicial
        dados["cnpj"] = dados_oficio.get("cnpj")
        dados["data_alvara"] = dados_oficio.get("data_alvara")
        dados["reu"] = dados_oficio.get("reu")
        dados["titulo_documento"] = dados_oficio.get("titulo_documento")
        dados["numero_documento"] = dados_oficio.get("numero_documento")

        if dados.get("conta") == CONTA_PROCESSAR:
            return dados

        ultimo_dado_valido = dados

    return ultimo_dado_valido


# ---------------------------------------------------------------------------
# SIAFE — download de Guias de Recolhimento
# ---------------------------------------------------------------------------
def buscar_gr_no_banco(num_judicial: str, valor_pesquisa: float, data_pagamento: str) -> dict | None:
    """Consulta a tabela contabilizacoes (somente leitura, cacheada em memoria) no hermes.db."""
    candidatas = [
        row for row in _listar_contabilizacoes()
        if row["num_documento"] is not None and num_judicial in (row["observacao"] or "")
    ]
    if len(candidatas) <= 1:
        return candidatas[0] if candidatas else None

    for row in candidatas:
        if row["valor"] == valor_pesquisa and row["data"] == data_pagamento:
            return row
    return None


def abrir_sessao_siafe(
    siafe: Siafe,
    versao_siafe: int,
    siafe_usuario: str,
    siafe_senha: str,
    ano_doc: int | str,
) -> None:
    """Navega e autentica uma sessão do SIAFE para a combinação (versao_siafe, ano_doc)."""
    url = URL_SIAFE.get(versao_siafe)
    if url is None:
        raise ErroSIAFE(f"Versão SIAFE inválida: {versao_siafe}")

    siafe.abrir_url(url)

    if not siafe.logar_siafe(versao_siafe, siafe_usuario, siafe_senha, ano_doc):
        raise ErroLoginSiafe("Erro no login SIAFE.")


def _consultar_e_baixar_gr(siafe: Siafe, valor: float, acao_consulta: Callable[[Siafe], str | None]) -> str | None:
    """Executa uma consulta (ja logada) e move o PDF baixado para PASTA_GR."""
    pasta_downloads = Path.home() / "Downloads"
    arquivos_antes = set(pasta_downloads.glob("*.pdf"))

    num_doc = acao_consulta(siafe)
    if not num_doc:
        log.warning("Consulta SIAFE não retornou número de documento.")
        return None

    arquivo_baixado = aguardar_novo_pdf(pasta_downloads, arquivos_antes)
    if not arquivo_baixado:
        return None

    return mover_gr_para_destino(arquivo_baixado, num_doc, valor)


def baixar_gr_no_siafe(siafe: Siafe, registro: dict, *, primeira_consulta: bool = True) -> str | None:
    """Download de GR quando já se conhece o num_documento (a partir de 2025).

    Espera uma sessão ``siafe`` já aberta e autenticada (ver ``abrir_sessao_siafe``)
    para a versão/ano corretos.
    """
    try:
        num_doc = registro["num_documento"]

        def consulta_por_registro(siafe: Siafe) -> str | None:
            return num_doc if siafe.consultar_GR_numDoc(num_doc, primeira_consulta=primeira_consulta) else None

        return _consultar_e_baixar_gr(siafe, registro["valor"], consulta_por_registro)
    except Exception as e:
        raise ErroSIAFE(f"Erro no download SIAFE: {mensagem_curta(e)}") from e


def baixar_gr_siafe_por_valor(
    siafe: Siafe,
    valor_pesquisa: float,
    versao_siafe: int,
    *,
    primeira_consulta: bool = True,
) -> str | None:
    """Download de GR via consulta por valor (anos 2016-2024), tentando o
    valor exato e, se não achar, uma tentativa a mais com +R$ 0,01.

    Espera uma sessão ``siafe`` já aberta e autenticada (ver ``abrir_sessao_siafe``)
    para a versão/ano corretos.
    """
    try:
        def consulta(siafe: Siafe, *, valor: float, primeira: bool) -> str | None:
            return siafe.consultar_GR_valor(valor, versao_siafe, primeira_consulta=primeira)

        resultado = _consultar_e_baixar_gr(
            siafe, valor_pesquisa, lambda s: consulta(s, valor=valor_pesquisa, primeira=primeira_consulta),
        )
        if resultado:
            return resultado

        valor_mais_um_centavo = round(valor_pesquisa + 0.01, 2)
        resultado = _consultar_e_baixar_gr(
            siafe, valor_mais_um_centavo, lambda s: consulta(s, valor=valor_mais_um_centavo, primeira=False),
        )
        if resultado:
            log.warning(
                f"GR encontrada com +R$ 0,01 (calculado: R$ {valor_pesquisa:,.2f}, "
                f"encontrado: R$ {valor_mais_um_centavo:,.2f})."
            )
        return resultado
    except Exception as e:
        raise ErroSIAFE(f"Erro no download SIAFE: {mensagem_curta(e)}") from e


# ---------------------------------------------------------------------------
# SEI — helpers reutilizáveis de interação
# ---------------------------------------------------------------------------
def baixar_e_extrair_texto(sei: SEI, nome_doc: str) -> str:
    """Baixa documento do SEI, renomeia, extrai texto do PDF."""
    caminho_definitivo = Path.cwd() / "Documento.pdf"
    try:
        if not sei.baixar_documento(nome_doc):
            raise ErroSEI(f"[{nome_doc}] Download falhou.")

        caminho_baixado = Path.cwd() / f"{formatar_nome_arquivo(nome_doc)}.pdf"
        if not caminho_baixado.exists():
            raise ErroSEI(f"[{nome_doc}] Arquivo não localizado após download.")

        if caminho_baixado.resolve() != caminho_definitivo.resolve():
            if caminho_definitivo.exists():
                caminho_definitivo.unlink()
            caminho_baixado.rename(caminho_definitivo)

        texto_pdf = extrair_texto_pdf(str(caminho_definitivo))
        if not texto_pdf.strip():
            raise ErroExtracao(f"[{nome_doc}] PDF sem texto extraível.")

        return texto_pdf
    except (ErroSEI, ErroExtracao):
        raise
    except Exception as e:
        raise ErroSEI(f"[{nome_doc}] Erro inesperado: {mensagem_curta(e)}") from e


def encontrar_dados_em_anexos(sei: SEI, candidatos: list[str]) -> dict | None:
    """Itera pelos candidatos até encontrar dados do comprovante."""
    from .extractors import extrair_dados_documento

    prioritarios = [n for n in candidatos if NOME_PDF_PGE in n]
    restantes = [n for n in candidatos if NOME_PDF_PGE not in n]
    dado_alternativo = None

    for nome_doc in prioritarios + restantes:
        log.info(f"Buscando dados em: '{nome_doc}'...")
        try:
            texto_pdf = baixar_e_extrair_texto(sei, nome_doc)
        except (ErroSEI, ErroExtracao) as e:
            if navegador_perdido(e):
                raise
            log.warning(f"  [{nome_doc}] Pulando ({mensagem_curta(e)}).")
            continue

        dados = extrair_dados_documento(texto_pdf)
        if not dados:
            log.warning(f"  [{nome_doc}] Nenhum dado reconhecido.")
            continue

        if dados.get("conta") == CONTA_PROCESSAR:
            log.info(f"  [{nome_doc}] Dados encontrados.")
            return dados

        dado_alternativo = dados

    return dado_alternativo


def extrair_dados_comprovante_do_processo(sei: SEI, nome_comprovante: str) -> dict | None:
    """Quando o comprovante já existe no processo, extrai diretamente."""
    from .extractors import ExtratorComprovante

    log.info(f"Comprovante ja presente no processo ('{nome_comprovante}'). Extraindo...")
    texto_pdf = baixar_e_extrair_texto(sei, nome_comprovante)
    return ExtratorComprovante.extrair(texto_pdf)


def _valor_por_extenso(valor_float: float) -> str:
    try:
        valor_por_extenso = num2words(valor_float, lang="pt_BR", to="currency")
        return valor_por_extenso.replace("catorze", "quatorze")
    except Exception:
        return "valor por extenso não calculado"


def formatar_despacho_dj_inserido(sei: SEI, registro: dict, titulo: str, lista_nomes: list[str]) -> None:
    """Formata o despacho de Depósito Judicial no SEI, com valores por extenso
    e os links dos documentos (despacho inicial, comprovantes e GR) do processo."""
    try:
        valor_resgate_float = float(registro["valor_resgate"])
        valor_30_float = float(registro["valor_30"])
    except (ValueError, TypeError, KeyError) as e:
        raise ErroValidacao("Valor inválido para formatação do despacho.") from e

    index_despacho = buscar_regex(lista_nomes[0], r"(\d+)") if lista_nomes else None
    index_comprovante = sei.copiar_informacoes_documento(NOME_TITULO_COMPROVANTE_BB)
    index_comprovante_djo = sei.copiar_informacoes_documento(NOME_TITULO_COMPROVANTE_DJO)
    index_gr = sei.copiar_informacoes_documento(NOME_TITULO_GR)

    mapa_texto = {
        "@tratamento_destinatario@ @cargo_destinatario@,": titulo,
        "[index_despacho]": index_despacho or "—",
        "[titulo_documento]": registro.get("titulo_documento") or "—",
        "[numero_documento]": registro.get("numero_documento") or "—",
        "[processo_judicial]": registro.get("processo_judicial") or "—",
        "[reu]": registro.get("reu") or "—",
        "[index_comprovante_DJO]": index_comprovante_djo or "—",
        "[data]": registro.get("data_pagamento") or "",
        "[conta_judicial]": registro.get("conta_judicial") or "—",
        "[valor_resgate]": formatar_moeda(valor_resgate_float),
        "[valor_resgate_por_extenso]": _valor_por_extenso(valor_resgate_float),
        "[index_comprovante]": index_comprovante or "—",
        "[valor_30]": formatar_moeda(valor_30_float),
        "[valor_30_por_extenso]": _valor_por_extenso(valor_30_float),
        "[data_pagamento]": registro.get("data_pagamento") or "",
        "[num_doc]": registro.get("num_doc") or "—",
        "[index_gr]": index_gr or "—",
    }
    mapa_links = {
        v: v for v in (index_despacho, index_comprovante, index_comprovante_djo, index_gr) if v
    }

    sei.formatar_despacho(mapa_texto, mapa_links)


def mapear_estado_documentos(sei: SEI, processo: str) -> dict:
    """Mapeia a árvore de documentos do SEI e retorna flags de estado."""
    try:
        sei.pesquisar_processo(processo)
        documentos_arvore = sei.analisar_documentos()
        lista_nomes = list(documentos_arvore.keys())

        tem_gr = any(NOME_TITULO_GR in nome for nome in lista_nomes)
        tem_comprovante = any(NOME_TITULO_COMPROVANTE_BB in nome for nome in lista_nomes)
        tem_comprovante_djo = any(NOME_TITULO_COMPROVANTE_DJO in nome for nome in lista_nomes)

        idx_gr = next(
            (i for i, n in enumerate(lista_nomes) if NOME_TITULO_GR in n), None
        )
        tem_despacho_apos_gr = idx_gr is not None and any(
            "Despacho" in n for n in lista_nomes[idx_gr + 1 :]
        )

        return {
            "lista_nomes": lista_nomes,
            "tem_gr": tem_gr,
            "tem_comprovante": tem_comprovante,
            "tem_comprovante_djo": tem_comprovante_djo,
            "tem_despacho_apos_gr": tem_despacho_apos_gr,
        }
    except Exception as e:
        raise ErroSEI(f"Erro ao mapear documentos da árvore: {e}")


# ---------------------------------------------------------------------------
# SharePoint — planilha diária de resgates
# ---------------------------------------------------------------------------
def _nome_arquivo_diario(data: datetime) -> str:
    """Monta o nome do arquivo da planilha diária para a data informada."""
    return f"{PREFIXO_ARQUIVO_DIARIO} {data.strftime('%d_%m_%Y')}.xlsx"


def _caminho_sharepoint_diario(data: datetime) -> str:
    """.../Arquivos DJO Banco do Brasil/[Ano]/[MM - Mês]/Resgates a Favor do Governo/<arquivo>"""
    pasta_mes = f"{data.month:02d} - {NOMES_MESES[data.month]}"
    return (
        f"{PASTA_BASE_SHAREPOINT}/{data.year}/{pasta_mes}"
        f"/Resgates a Favor do Governo/{_nome_arquivo_diario(data)}"
    )


def baixar_planilha_diaria(data_pagamento: str, pasta_destino: Path) -> Path:
    """Localiza e baixa, do SharePoint, a planilha diária correspondente
    à Data do Pagamento do Comprovante de Resgate (formato DD/MM/AAAA).
    """
    data = datetime.strptime(data_pagamento, "%d/%m/%Y")
    nome_arquivo = _nome_arquivo_diario(data)
    caminho_sharepoint = _caminho_sharepoint_diario(data)

    sp = SharePoint(SITE_SHAREPOINT, caminho_cookie=CAMINHO_COOKIES_SHAREPOINT)

    if not sp.existe_arquivo(caminho_sharepoint):
        raise ErroDownload(
            f"Planilha diária não encontrada no SharePoint para {data_pagamento} "
            f"(esperado: '{nome_arquivo}')."
        )

    sp.download_arquivo(caminho_sharepoint, pasta_local=str(pasta_destino))

    caminho_local = pasta_destino / nome_arquivo
    if not caminho_local.exists():
        raise ErroDownload(f"Download de '{nome_arquivo}' não gerou o arquivo esperado em {pasta_destino}.")

    log.info(f"Planilha diária baixada: {nome_arquivo}")
    return caminho_local
