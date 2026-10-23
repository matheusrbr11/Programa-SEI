"""Logger do fluxo em execução, usado pelos módulos comuns."""

import logging

_nome_logger = "jupiter.processar"


def definir_logger_fluxo(nome_logger: str) -> None:
    """Define o logger do fluxo (ex.: 'jupiter.processarCC'). O nome vai para o
    log geral e é o que distingue ali o Crédito em Conta do Depósito Judicial;
    cada subprocesso roda um só fluxo."""
    global _nome_logger
    _nome_logger = nome_logger


class _LoggerDoFluxo:
    """Repassa as chamadas ao logger definido em ``definir_logger_fluxo``."""

    def __getattr__(self, atributo):
        return getattr(logging.getLogger(_nome_logger), atributo)


log = _LoggerDoFluxo()
