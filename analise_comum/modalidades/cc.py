"""Crédito em Conta: valor da GR vem do comprovante; despacho com valor, data,
número do documento no SIAFE e link para a GR."""

from __future__ import annotations

from jupiter import SEI

from ..config import SUFIXO_DESPACHO_USUARIO, TITULO_DESPACHO, NOME_TITULO_GR, NOME_TITULO_COMPROVANTE_BB
from ..core import ErroSEI, ErroValidacao
from ..extractors import (
    ExtratorComprovante,
    ExtratorAgendamento,
    ExtratorOficio,
    ExtratorAlvaraEletronico,
    ExtratorAlvaraLevantamento,
    ExtratorMandado,
)
from ..services import valor_por_extenso
from ..utils import formatar_moeda
from . import Modalidade

CAMPOS_OBRIGATORIOS_DESPACHO = ("valor", "data", "num_documento")


class CreditoEmConta(Modalidade):
    codigo = "cc"
    nome = "Crédito em Conta"
    nome_logger = "jupiter.processarCC"

    marcador_filtro = "PGE - Credito em Conta - Processar"
    marcador_concluido = "PGE - Credito em Conta - Concluido"
    despacho_padrao = f"DPJ_{SUFIXO_DESPACHO_USUARIO}"
    contas_processar = ("00000291632-0",)
    tipos_nao_pge = (NOME_TITULO_GR, NOME_TITULO_COMPROVANTE_BB, "Despacho")

    campos_obrigatorios_extracao = {
        "conta": "Campo 'Conta' nao encontrado.",
        "processo_judicial": "Campo 'Processo Judicial' nao encontrado.",
        "ano": "Campo 'Ano' nao encontrado.",
        "valor_pesquisa": "Campo 'Valor' nao encontrado.",
    }

    def extrair_dados_documento(self, texto: str) -> dict | None:
        """Ordem de prioridade: Comprovante -> Agendamento -> Oficio -> Alvara -> Mandado."""
        if not texto or not texto.strip():
            return None

        # 1. Comprovante / Agendamento (documento principal)
        if ExtratorComprovante.pode_extrair(texto):
            return ExtratorComprovante.extrair(texto, self.contas_processar)
        if ExtratorAgendamento.pode_extrair(texto):
            return ExtratorAgendamento.extrair(texto, self.contas_processar)

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

        return self.consultar_conta_judicial(dados_bb)

    def montar_despacho(self, registro_db: dict, estado: dict) -> dict:
        registro = {
            "num_documento": registro_db.get("num_doc"),
            "valor": registro_db.get("valor_pesquisa"),
            "data": registro_db.get("data_pagamento"),
        }
        try:
            valor = float(registro["valor"])
        except (ValueError, TypeError) as e:
            raise ErroValidacao("Valor inválido para formatação do despacho.") from e
        return {"registro": registro, "valor": valor}

    def antes_de_incluir_despacho(self, sei: SEI, despacho: dict) -> None:
        despacho["index_gr"] = sei.obter_numero_documento(NOME_TITULO_GR)

    def formatar_despacho(self, sei: SEI, despacho: dict) -> bool:
        registro, valor, index_gr = despacho["registro"], despacho["valor"], despacho["index_gr"]
        despacho_completo = all(registro.get(campo) for campo in CAMPOS_OBRIGATORIOS_DESPACHO)

        mapa_texto = {
            "@tratamento_destinatario@ @cargo_destinatario@,": TITULO_DESPACHO,
            "[valor]": formatar_moeda(valor),
            "[valor_ext]": valor_por_extenso(valor),
            "[data]": registro.get("data") or "—",
            "[num_documento]": registro.get("num_documento") or "—",
            "[index_documento_gr]": index_gr or "—",
        }
        mapa_links = {index_gr: index_gr} if index_gr else {}

        if not sei.formatar_despacho(mapa_texto, mapa_links):
            raise ErroSEI("Formatação do despacho não confirmada pelo SEI.")
        return despacho_completo


MODALIDADE = CreditoEmConta()
