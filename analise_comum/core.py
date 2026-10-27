"""Núcleo comum: exceções e setup de ambiente do processo filho."""

from __future__ import annotations

import undetected_chromedriver as uc
from jupiter import configurar_log
import logging
import base64
import sys
import traceback

from .config import PASTA_LOG_GERAL, PROJECT_BASE_PATH
from .log import definir_logger_fluxo


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

# Erros de autenticação:
class ErroLoginSEI(Exception):
    """Falha de autenticacao no SEI."""
    pass


class ErroLoginSiafe(Exception):
    """Falha de autenticacao no SIAFE."""
    pass


# Erros do Processo:
class ErroProcesso(Exception):
    """Erro esperado durante o processamento de um processo específico."""
    pass


# Erros de Serviço:
class ErroSEI(ErroProcesso):
    """Erro ao interagir com o SEI (anexar, despachar, bloco, marcador)."""
    pass


class ErroSIAFE(ErroProcesso):
    """Erro ao interagir com o SIAFE (fora de falha de login)."""
    pass


class ErroBB(ErroProcesso):
    """Erro ao interagir com o site do Banco do Brasil."""
    pass


# Erros de Negócio:
class ErroExtracao(ErroProcesso):
    """Falha ao extrair dados de um documento/anexo."""
    pass


class ErroDownload(ErroProcesso):
    """Documento nao encontrado/disponivel para download (BB, SharePoint ou SIAFE)."""
    pass


class ErroDadosNaoLocalizadosBB(ErroDownload):
    """BB respondeu 'Dados não localizados' mesmo após os retries de data e
    CNPJ (ver ``baixar_comprovante_bb``) — indica CNPJ e/ou data realmente
    incorretos no ofício/alvará, não instabilidade do site."""
    pass


class ErroValidacao(ErroProcesso):
    """Dados extraídos não passaram na validação de negócio."""
    pass


# ---------------------------------------------------------------------------
# Setup de ambiente do processo filho
# ---------------------------------------------------------------------------
_configurado = False


class FormatterSemTraceback(logging.Formatter):
    """Formatter que omite traceback para saída no console."""

    def format(self, record: logging.LogRecord) -> str:
        mensagem = record.getMessage().strip()
        primeira_linha = mensagem.splitlines()[0] if mensagem else ""
        return f"{record.levelname}: {primeira_linha}"


PREFIXO_DETALHE_ERRO = "__ERRO_DETALHE__:"


class DetalheErroHandler(logging.Handler):
    """Emite, para cada erro, uma linha extra com o detalhe completo do erro."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            partes = [record.getMessage().strip()]
            if record.exc_info:
                partes.append("".join(traceback.format_exception(*record.exc_info)).strip())
            texto_completo = "\n".join(p for p in partes if p)
            if not texto_completo:
                return
            codificado = base64.b64encode(texto_completo.encode("utf-8")).decode("ascii")
            print(f"{PREFIXO_DETALHE_ERRO}{codificado}", flush=True)
        except Exception:
            pass  # nunca deixa o proprio logging quebrar o processo


def configurar_ambiente(nome_logger: str) -> None:
    """Aplica patches do selenium, define o logger do fluxo usado pelos módulos
    comuns e liga o log dos loggers 'jupiter' e 'automaweb' no stdout."""
    global _configurado
    if _configurado:
        return

    definir_logger_fluxo(nome_logger)

    uc.Chrome.__del__ = lambda self: None  # evita erro no __del__ do uc
    sys.stdout.reconfigure(encoding="utf-8")

    configurar_log("Programa Argos", PASTA_LOG_GERAL, PROJECT_BASE_PATH / "logs")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(FormatterSemTraceback())

    detalhe_handler = DetalheErroHandler()
    detalhe_handler.setLevel(logging.ERROR)

    for nome_logger_raiz in ("jupiter", "automaweb"):
        logging.getLogger(nome_logger_raiz).addHandler(stdout_handler)
        logging.getLogger(nome_logger_raiz).addHandler(detalhe_handler)

    _configurado = True
