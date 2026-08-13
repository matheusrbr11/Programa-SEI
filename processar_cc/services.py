"""Integrações externas: Banco do Brasil, SIAFE e helpers de interação com o SEI."""

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

from jupiter import Siafe, SEI
import automaweb

from .config import (
    URL_BB, URL_SIAFE, CAMINHO_DRIVER_EDGE, CAMINHO_HERMES, PASTA_GR,
    CONTA_PROCESSAR, NOME_PDF_PGE,
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

log = logging.getLogger("jupiter.processarCC")

# ---------------------------------------------------------------------------
# Banco do Brasil — download de comprovantes
# ---------------------------------------------------------------------------
# Candidatos genéricos de onde o portal do BB (JSF) costuma renderizar
# mensagens de validação. Sem acesso à página ao vivo não dá pra cravar o
# seletor exato, então tenta vários e usa o primeiro texto não vazio.
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
    """Le a tabela contabilizacoes uma vez e cacheia em memoria pelo tempo de
    vida do processo. É somente leitura para este programa (quem escreve é o
    modulo PRJ do Hermes) — no pior caso, uma contabilizacao adicionada pelo
    Hermes durante o lote fica fora do cache, e o programa apenas baixa a GR
    pelo SIAFE em vez de reaproveitar, sem causar nenhuma acao incorreta."""
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
                log.error(f"Erro SQLite ao carregar contabilizacoes: {e}", exc_info=True)
                _cache_contabilizacoes = []
    return _cache_contabilizacoes


def _buscar_data_pagamento_por_conta(conta_judicial: str) -> str | None:
    """
    Busca em contabilizacoes (somente leitura) a 'Data do Pagamento' de um
    resgate já contabilizado para a conta judicial informada.

    Usado como fallback quando a data extraída do documento do processo
    (ex.: 'Data do Depósito' do Alvará de Levantamento) está desatualizada
    e o BB não retorna o resgate com ela.
    """
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
    if data_pagamento:
        log.info(
            f"Data de pagamento para conta {conta_judicial} localizada em "
            f"contabilizacoes: {data_pagamento}."
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
        return mensagem or "dados invalidos (conta judicial, CNPJ ou data)"
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
        log.info(f"Comprovante ja disponivel para conta judicial '{conta_judicial}'.")
        return Path(caminho_em_disco)

    try:
        datetime.strptime(data_alvara, "%d/%m/%Y")
    except (ValueError, TypeError) as e:
        raise ErroValidacao(f"Data invalida para consulta BB: {data_alvara} — {e}") from e

    nav = automaweb.Navegador()
    try:
        nav.abrir_driver_undetected(caminho_driver=CAMINHO_DRIVER_EDGE)
        nav.abrir_url(URL_BB)

        erro = _tentar_resgate_bb(nav, conta_judicial, cnpj, data_alvara)
        if erro:
            log.warning(f"BB nao retornou resgate para conta {conta_judicial}: {erro}")

            data_fallback = _buscar_data_pagamento_por_conta(conta_judicial)
            if not data_fallback or data_fallback == data_alvara:
                raise ErroDownload(f"BB nao retornou resgate para conta {conta_judicial}: {erro}")

            log.info(
                f"Tentando novamente para conta {conta_judicial} com a data de "
                f"contabilizacoes: {data_fallback}."
            )
            nav.abrir_url(URL_BB)
            erro = _tentar_resgate_bb(nav, conta_judicial, cnpj, data_fallback)
            if erro:
                raise ErroDownload(
                    f"BB nao retornou resgate para conta {conta_judicial} mesmo com "
                    f"a data de contabilizacoes: {erro}"
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
        raise ErroBB(f"Erro durante navegacao no site do BB: {mensagem_curta(e)}") from e
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

    log.debug(f"consultar_conta_judicial: {len(lista_dados)} conjunto(s) de dados extraido(s)")
    ultimo_dado_valido = None

    for i, dados_oficio in enumerate(lista_dados):
        conta_judicial = dados_oficio.get("conta_judicial")
        if not conta_judicial:
            continue

        log.debug(
            f"  [{i}] data_alvara={dados_oficio.get('data_alvara')}, "
            f"conta={conta_judicial}, cnpj={dados_oficio.get('cnpj')}"
        )

        try:
            caminho_comprovante = baixar_comprovante_bb(dados_oficio)
        except (ErroBB, ErroDownload, ErroValidacao) as e:
            if navegador_perdido(e):
                raise
            log.warning(f"Falha ao obter comprovante para conta {conta_judicial}: {mensagem_curta(e)}")
            continue

        texto_comprovante = extrair_texto_pdf(str(caminho_comprovante))
        extrator = next(
            (e for e in extratores_resgate_bb if e.pode_extrair(texto_comprovante)), None
        )
        if not texto_comprovante.strip() or extrator is None:
            log.warning(f"Comprovante para {conta_judicial} nao contem titulo esperado.")
            continue

        dados = extrator.extrair(texto_comprovante)
        if not dados:
            log.warning(f"Nao foi possivel extrair dados do comprovante de {conta_judicial}")
            continue

        dados["caminho_comprovante"] = str(caminho_comprovante)
        dados["conta_judicial"] = conta_judicial
        dados["cnpj"] = dados_oficio.get("cnpj")
        dados["data_alvara"] = dados_oficio.get("data_alvara")

        if dados.get("conta") == CONTA_PROCESSAR:
            return dados

        ultimo_dado_valido = dados

    return ultimo_dado_valido


# ---------------------------------------------------------------------------
# SIAFE — download de Guias de Recolhimento
# ---------------------------------------------------------------------------
def buscar_gr_no_banco(num_judicial: str) -> dict | None:
    """Consulta a tabela contabilizacoes (somente leitura, cacheada em memoria) no hermes.db."""
    for row in _listar_contabilizacoes():
        if row["num_documento"] is not None and num_judicial in (row["observacao"] or ""):
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
        raise ErroSIAFE(f"Versao SIAFE invalida: {versao_siafe}")

    siafe.abrir_url(url)

    if not siafe.logar_siafe(versao_siafe, siafe_usuario, siafe_senha, ano_doc):
        raise ErroLoginSiafe("Falha no login SIAFE.")


def _consultar_e_baixar_gr(siafe: Siafe, valor: float, acao_consulta: Callable[[Siafe], str | None]) -> str | None:
    """Executa uma consulta (ja logada) e move o PDF baixado para PASTA_GR."""
    pasta_downloads = Path.home() / "Downloads"
    arquivos_antes = set(pasta_downloads.glob("*.pdf"))

    num_doc = acao_consulta(siafe)
    if not num_doc:
        log.warning("Consulta SIAFE nao retornou numero de documento.")
        return None

    arquivo_baixado = aguardar_novo_pdf(pasta_downloads, arquivos_antes)
    if not arquivo_baixado:
        return None

    return mover_gr_para_destino(arquivo_baixado, num_doc, valor)


def baixar_gr_no_siafe(siafe: Siafe, registro: dict, *, primeira_consulta: bool = True) -> str | None:
    """Download de GR quando já se conhece o num_documento (a partir de 2025).

    Espera uma sessão ``siafe`` ja aberta e autenticada (ver ``abrir_sessao_siafe``)
    para a versao/ano corretos. ``primeira_consulta`` deve ser False a partir da
    2ª consulta dentro do mesmo grupo (versao_siafe, ano) — o painel de filtro
    do SIAFE fica aberto entre consultas na mesma sessão.
    """
    try:
        num_doc = registro["num_documento"]

        def consulta_por_registro(siafe: Siafe) -> str | None:
            return num_doc if siafe.consultar_GR_numDoc(num_doc, primeira_consulta=primeira_consulta) else None

        return _consultar_e_baixar_gr(siafe, registro["valor"], consulta_por_registro)
    except Exception as e:
        raise ErroSIAFE(f"Erro inesperado no download SIAFE: {mensagem_curta(e)}") from e


def baixar_gr_siafe_por_valor(
    siafe: Siafe,
    valor_pesquisa: float,
    versao_siafe: int,
    data_pagamento: str | None = None,
    *,
    primeira_consulta: bool = True,
) -> str | None:
    """Download de GR via consulta por valor (anos 2016-2024).

    Espera uma sessão ``siafe`` ja aberta e autenticada (ver ``abrir_sessao_siafe``)
    para a versao/ano corretos. ``primeira_consulta`` deve ser False a partir da
    2ª consulta dentro do mesmo grupo (versao_siafe, ano) — ver nota em
    ``baixar_gr_no_siafe``.
    """
    try:
        def consulta_por_valor(siafe: Siafe) -> str | None:
            return siafe.consultar_GR_valor(
                valor_pesquisa, versao_siafe, data_pagamento=data_pagamento,
                primeira_consulta=primeira_consulta,
            )

        return _consultar_e_baixar_gr(siafe, valor_pesquisa, consulta_por_valor)
    except Exception as e:
        raise ErroSIAFE(f"Erro inesperado no download SIAFE: {mensagem_curta(e)}") from e


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
            raise ErroSEI(f"[{nome_doc}] Arquivo nao localizado apos download.")

        if caminho_baixado.resolve() != caminho_definitivo.resolve():
            if caminho_definitivo.exists():
                caminho_definitivo.unlink()
            caminho_baixado.rename(caminho_definitivo)

        texto_pdf = extrair_texto_pdf(str(caminho_definitivo))
        if not texto_pdf.strip():
            raise ErroExtracao(f"[{nome_doc}] PDF sem texto extraivel.")

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
            log.info(f"  [{nome_doc}] Dados encontrados (Conta de Processamento).")
            return dados

        log.warning(f"  [{nome_doc}] Conta encontrada ({dados.get('conta')}) sera ignorada.")
        dado_alternativo = dados

    return dado_alternativo


def extrair_dados_comprovante_do_processo(sei: SEI, nome_comprovante: str) -> dict | None:
    """Quando o comprovante já existe no processo, extrai diretamente."""
    from .extractors import ExtratorComprovante

    log.info(f"Comprovante ja presente no processo ('{nome_comprovante}'). Extraindo...")
    texto_pdf = baixar_e_extrair_texto(sei, nome_comprovante)
    return ExtratorComprovante.extrair(texto_pdf)


def formatar_despacho_inserido(sei: SEI, registro: dict, titulo: str, index_doc: str) -> None:
    """Formata o despacho no SEI com valor por extenso."""
    try:
        valor_float = float(registro["valor"])
    except (ValueError, TypeError) as e:
        raise ErroValidacao("Valor invalido para formatacao do despacho.") from e

    valor_formatado = formatar_moeda(valor_float)
    try:
        valor_por_extenso = num2words(valor_float, lang="pt_BR", to="currency")
        valor_por_extenso = valor_por_extenso.replace("catorze", "quatorze")
    except Exception:
        valor_por_extenso = "valor por extenso nao calculado"

    sei.formatar_despacho(
        titulo=titulo,
        valor=valor_formatado,
        valor_por_extenso=valor_por_extenso,
        data=registro.get("data", ""),
        num_documento=registro.get("num_documento", "—"),
        index_doc=index_doc,
    )


def mapear_estado_documentos(sei: SEI, processo: str) -> dict:
    """Mapeia a árvore de documentos do SEI e retorna flags de estado."""
    try:
        sei.pesquisar_processo(processo)
        documentos_arvore = sei.analisar_documentos()
        lista_nomes = list(documentos_arvore.keys())

        tem_gr = any("Guia de Recolhimento" in nome for nome in lista_nomes)
        tem_comprovante = any("Comprovante de Resgate" in nome for nome in lista_nomes)

        idx_gr = next(
            (i for i, n in enumerate(lista_nomes) if "Guia de Recolhimento" in n), None
        )
        tem_despacho_apos_gr = idx_gr is not None and any(
            "Despacho" in n for n in lista_nomes[idx_gr + 1 :]
        )

        return {
            "lista_nomes": lista_nomes,
            "tem_gr": tem_gr,
            "tem_comprovante": tem_comprovante,
            "tem_despacho_apos_gr": tem_despacho_apos_gr,
        }
    except Exception as e:
        raise ErroSEI(f"Erro ao mapear documentos da arvore: {e}")
