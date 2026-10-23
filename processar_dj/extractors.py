"""Ordem de prioridade dos extratores no Depósito Judicial.

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
    
    Ordem de prioridade: Alvara Eletronico -> Alvara Levantamento (exclusivos)
    -> Oficio -> Mandado -> Comprovante -> Agendamento (acumulam).
    """

    if not texto or not texto.strip():
        return None

    # 1. Alvará (Eletrônico ou de Levantamento) — prioridade exclusiva
    extratores_alvara = [ExtratorAlvaraEletronico, ExtratorAlvaraLevantamento]
    for extrator in extratores_alvara:
        if extrator.pode_extrair(texto):
            dados_alvara = extrator.extrair(texto)
            if dados_alvara:
                return consultar_conta_judicial(dados_alvara)

    # 2. Demais documentos: acumulam
    dados_bb: list[dict] = []
    extratores_secundarios = [
        ExtratorOficio,
        ExtratorMandado,
    ]
    for extrator in extratores_secundarios:
        if extrator.pode_extrair(texto):
            dados_bb.extend(extrator.extrair(texto))

    if ExtratorComprovante.pode_extrair(texto):
        dado_comprovante = ExtratorComprovante.extrair(texto, CONTAS_PROCESSAR)
        if dado_comprovante:
            dados_bb.append(dado_comprovante)
    if ExtratorAgendamento.pode_extrair(texto):
        dado_agendamento = ExtratorAgendamento.extrair(texto, CONTAS_PROCESSAR)
        if dado_agendamento:
            dados_bb.append(dado_agendamento)

    if not dados_bb:
        return None

    return consultar_conta_judicial(dados_bb)
