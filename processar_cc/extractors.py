"""Ordem de prioridade dos extratores no Crédito em Conta.

Os extratores ficam em ``processar_comum.extractors``."""

from __future__ import annotations

from processar_comum.extractors import (
    ExtratorComprovante,
    ExtratorAgendamento,
    ExtratorOficio,
    ExtratorAlvaraEletronico,
    ExtratorAlvaraLevantamento,
    ExtratorMandado,
)

from .config import CONTAS_PROCESSAR
from .services import consultar_conta_judicial


# ---------------------------------------------------------------------------
# Fábrica / Orquestrador de extração
# ---------------------------------------------------------------------------
def extrair_dados_documento(texto: str) -> dict | None:
    """
    Orquestra a extração de dados de qualquer documento suportado.
    Ordem de prioridade: Comprovante -> Agendamento -> Oficio -> Alvara -> Mandado.
    """

    if not texto or not texto.strip():
        return None

    # 1. Comprovante / Agendamento (documento principal)
    if ExtratorComprovante.pode_extrair(texto):
        return ExtratorComprovante.extrair(texto, CONTAS_PROCESSAR)
    if ExtratorAgendamento.pode_extrair(texto):
        return ExtratorAgendamento.extrair(texto, CONTAS_PROCESSAR)

    # 2. Documentos que exigem consulta posterior no BB
    dados_bb: list[dict] = []
    extratores_secundarios = [
        ExtratorOficio,
        ExtratorAlvaraEletronico,
        ExtratorAlvaraLevantamento,
        ExtratorMandado,
    ]
    for extrator in extratores_secundarios:
        if extrator.pode_extrair(texto):
            dados_bb.extend(extrator.extrair(texto))

    if not dados_bb:
        return None

    return consultar_conta_judicial(dados_bb)
