"""Extratores de documentos (Strategy Pattern) e fábrica de orquestração."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import re

from .config import CONTA_PROCESSAR, PADRAO_CNJ, PADRAO_DATA_EXTENSO, PADRAO_CNPJ, CNPJ_ESTADO
from .services import consultar_conta_judicial
from .utils import (
    buscar_regex,
    normalizar_processo_judicial,
    converter_valor_moeda,
    converter_data_por_extenso,
    normalizar_cnpj,
)

log = logging.getLogger("jupiter.processarCC")


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class ExtratorBase(ABC):
    """
    Classe base para extratores de documentos.
    Cada especialização define seus títulos e a lógica de extração.
    """

    TITULOS: tuple[str, ...] = ()

    @classmethod
    def pode_extrair(cls, texto: str) -> bool:
        if not texto:
            return False
        return any(t in texto for t in cls.TITULOS)

    @classmethod
    @abstractmethod
    def extrair(cls, texto: str) -> list[dict]:
        """Retorna lista de dicts com {data_alvara, conta_judicial, cnpj}."""
        raise NotImplementedError

    @classmethod
    def _split_blocos(cls, texto: str) -> list[str]:
        """Divide o texto em blocos após cada ocorrência dos títulos."""
        if not cls.TITULOS:
            return []
        padrao = r"(?:" + "|".join(map(re.escape, cls.TITULOS)) + r")"
        return re.split(padrao, texto)[1:]


# ---------------------------------------------------------------------------
# Comprovante / Agendamento (BB) — lógica compartilhada
# ---------------------------------------------------------------------------
class ExtratorResgateBB(ExtratorBase):
    """Base comum: ambos os documentos do BB têm o mesmo layout de campos."""

    @classmethod
    def extrair(cls, texto: str) -> dict | None:
        blocos = cls._split_blocos(texto)
        ultimo_dado = None

        for bloco in blocos:
            dado = cls._extrair_bloco(bloco)
            if not dado:
                continue
            if dado.get("conta") == CONTA_PROCESSAR:
                return dado
            ultimo_dado = dado

        return ultimo_dado

    @classmethod
    def _extrair_bloco(cls, bloco: str) -> dict | None:
        """Processa um bloco de texto do BB e extrai dados financeiros."""
        processo_judicial = normalizar_processo_judicial(
            buscar_regex(bloco, r"Processo[\s\S]{0,80}?" + PADRAO_CNJ)
        )

        data_pagamento = buscar_regex(
            bloco,
            r"(?:Data|Dt\.?|Previs[aã]o)\s+(?:do|de)?\s*(?:Pagamento)[\s.:]*[\-]?\s*(?:\n\s*)?(\d{2}/\d{2}/\d{4})",
        )
        if not data_pagamento:
            data_pagamento = buscar_regex(
                bloco, r"(?:Data|Dt\.?)\s+Pagamento\s*[\-]?\s*(?:\n\s*)?(\d{2}/\d{2}/\d{4})"
            )

        ano = int(data_pagamento.split("/")[-1]) if data_pagamento else None

        valor_str = buscar_regex(
            bloco,
            r"Valor[\s\S]{0,80}?L[íi]q\.?[\s\S]{0,50}?(?:Pagamento|Resgate)[\s\S]{0,30}?([\d.,]+)",
        )
        if not valor_str:
            valor_str = buscar_regex(
                bloco,
                r"Valor[\s\S]{0,80}?L[íi]q(?:uido)?[\s\S]{0,50}?Resgate[\s\S]{0,30}?([\d.,]+)",
            )
        if not valor_str:
            valor_str = buscar_regex(
                bloco, r"Valor\s+Bruto\s+Resgate\s*:\s*R?\$?\s*([\d.,]+)"
            )
        valor_pesquisa = converter_valor_moeda(valor_str)

        conta_judicial = buscar_regex(
            bloco,
            r"Conta(?:\(s\)|s)?\s*(?:Judicial|Resgatada)(?:\(s\))?\s*[:\-]?[\s\S]{0,30}?(\d{10,})",
            flags=re.IGNORECASE,
        )

        conta_final = cls._identificar_conta_destino(bloco)

        return {
            "conta": conta_final,
            "processo_judicial": processo_judicial,
            "data_pagamento": data_pagamento,
            "ano": ano,
            "valor_pesquisa": valor_pesquisa,
            "conta_judicial": conta_judicial,
        }

    @classmethod
    def _identificar_conta_destino(cls, bloco: str) -> str | None:
        """Identifica se o bloco se refere à conta de processamento."""
        bloco_limpo = bloco.replace("-", "").replace(".", "")
        if "291632-0" in bloco or "2916320" in bloco_limpo:
            return CONTA_PROCESSAR

        contas = re.findall(
            r"(?:Conta|C/C)[\s\S]{0,20}?(\d{4,}[\.\-\s]*\d{1,2})\b",
            bloco,
            re.IGNORECASE,
        )
        return contas[0] if contas else None


class ExtratorComprovante(ExtratorResgateBB):
    """Extrai dados de Comprovante de Resgate já concluído no BB."""

    TITULOS = (
        "Comprovante de Resgate de Depósito Judicial",
        "Comprovante de Resgate Justiça Estadual",
        "Comprovante de Resgate Justiça Trabalhista",
    )


class ExtratorAgendamento(ExtratorResgateBB):
    """Extrai dados de Agendamento de Resgate no BB."""

    TITULOS = ("Agendamento de Resgate",)


# ---------------------------------------------------------------------------
# Documentos secundários (exigem consulta posterior no BB)
# ---------------------------------------------------------------------------
class ExtratorOficio(ExtratorBase):
    """Extrai dados de Ofícios do processo eletrônico."""

    TITULOS = ("Processo Eletrônico",)

    @classmethod
    def extrair(cls, texto: str) -> list[dict]:
        resultados = []
        for trecho in cls._split_blocos(texto):
            data_raw = buscar_regex(
                trecho, r"Rio de Janeiro,\s*(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})"
            )
            if not data_raw:
                data_raw = buscar_regex(trecho, PADRAO_DATA_EXTENSO)

            data_alvara = converter_data_por_extenso(data_raw)
            pares_contas = re.findall(
                r"contas?\s+judicia(?:l|is)\s+(\d{10,})(?:\s*(?:e|,)\s*(\d{10,}))?",
                trecho,
                re.IGNORECASE,
            )
            contas = [c for par in pares_contas for c in par if c]
            contas_unicas = list(dict.fromkeys(contas))
            cnpj = normalizar_cnpj(
                buscar_regex(trecho, r"CNPJ\s*(?:n[.\s]*[º°o]?)?\s*[:.-]?\s*" + PADRAO_CNPJ)
            )

            if data_alvara and contas_unicas and cnpj:
                for conta in contas_unicas:
                    resultados.append({
                        "data_alvara": data_alvara,
                        "conta_judicial": conta,
                        "cnpj": cnpj,
                    })
        return resultados


class ExtratorAlvaraEletronico(ExtratorBase):
    """Extrai dados de Alvarás Eletrônicos de Pagamento."""

    TITULOS = ("ALVARA ELETRONICO DE PAGAMENTO", "ALVARÁ ELETRÔNICO DE PAGAMENTO")

    @classmethod
    def extrair(cls, texto: str) -> list[dict]:
        resultados = []
        for trecho in cls._split_blocos(texto):
            data_raw = buscar_regex(trecho, r"[Cc]alculado\s+em[\s.]*:\s*(\d{2}\.\d{2}\.\d{4})")
            data_alvara = data_raw.replace(".", "/") if data_raw else None

            contas_raw = re.findall(
                r"Conta/Pcl\s+Resgatada[\s.]*:\s*(\d+)", trecho, re.IGNORECASE
            )
            contas_unicas = list(dict.fromkeys(contas_raw))

            cnpj = normalizar_cnpj(
                buscar_regex(trecho, r"(?:CPF/)?CNPJ\s+(?:Benefici[aá]rio|Autor)[\s.]*:\s*" + PADRAO_CNPJ)
            )
            if not cnpj:
                cnpj = normalizar_cnpj(buscar_regex(trecho, CNPJ_ESTADO))

            if data_alvara and cnpj and contas_unicas:
                for conta in contas_unicas:
                    resultados.append({
                        "data_alvara": data_alvara,
                        "conta_judicial": conta.strip(),
                        "cnpj": cnpj,
                    })
        return resultados


class ExtratorAlvaraLevantamento(ExtratorBase):
    """Extrai dados de Alvarás de Levantamento."""

    TITULOS = ("ALVARÁ DE LEVANTAMENTO DE QUANTIA", "ALVARÁ DE LEVANTAMENTO Nº")

    @classmethod
    def extrair(cls, texto: str) -> list[dict]:
        resultados = []
        for trecho in cls._split_blocos(texto):
            data_raw = buscar_regex(
                trecho, r"Data\s+do\s+Dep[óo]sito[\s.]*:\s*(\d{2}/\d{2}/\d{4})"
            )
            data_alvara = data_raw
            if not data_alvara:
                data_alvara = buscar_regex(
                    trecho, r"assinado eletronicamente por[\s\S]{0,120}?em\s+(\d{2}/\d{2}/\d{4})"
                )

            contas_raw = re.findall(
                r"Conta\s+Judicial[\s.]*:\s*(\d+)", trecho, re.IGNORECASE
            )
            if not contas_raw:
                contas_raw = re.findall(
                    r"Conta\s+Judicial\s+n[ºo°]\s*(\d+)", trecho, re.IGNORECASE
                )
            contas_unicas = list(dict.fromkeys(contas_raw))

            cnpj = normalizar_cnpj(
                buscar_regex(trecho, r"CNPJ\s+Benefici[aá]rio[\s.]*:\s*" + PADRAO_CNPJ)
            )
            if not cnpj:
                cnpj = normalizar_cnpj(
                    buscar_regex(trecho, r"Favorecido[\s\S]{0,80}?CNPJ\s*n?[ºo°]?\s*[:.-]?\s*" + PADRAO_CNPJ)
                )
            if not cnpj:
                cnpj = normalizar_cnpj(buscar_regex(trecho, CNPJ_ESTADO))

            if data_alvara and cnpj and contas_unicas:
                for conta in contas_unicas:
                    resultados.append({
                        "data_alvara": data_alvara,
                        "conta_judicial": conta.strip(),
                        "cnpj": cnpj,
                    })
        return resultados


class ExtratorMandado(ExtratorBase):
    """Extrai dados de Mandado de Pagamento."""

    TITULOS = ("MANDADO DE PAGAMENTO",)

    @classmethod
    def extrair(cls, texto: str) -> list[dict]:
        resultados = []
        for trecho in cls._split_blocos(texto):
            contas = re.findall(r"CONTA:\s*(\d+)", trecho, re.IGNORECASE)
            if not contas:
                contas = re.findall(
                    r"Conta[\s\S]{0,40}?judicial[\s.]*:\s*(\d{12,})", trecho, re.IGNORECASE
                )
            contas_unicas = list(dict.fromkeys(contas))

            data_raw = buscar_regex(
                trecho, r"Rio de Janeiro,\s*(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})"
            )
            if not data_raw:
                data_raw = buscar_regex(trecho, PADRAO_DATA_EXTENSO)
            data_alvara = converter_data_por_extenso(data_raw)

            cnpj = normalizar_cnpj(
                buscar_regex(trecho, r"(?:CPF/)?CNPJ[\s.]*:\s*" + PADRAO_CNPJ)
            )

            if data_alvara and contas_unicas and cnpj:
                for conta in contas_unicas:
                    resultados.append({
                        "data_alvara": data_alvara,
                        "conta_judicial": conta,
                        "cnpj": cnpj,
                    })
        return resultados


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
        return ExtratorComprovante.extrair(texto)
    if ExtratorAgendamento.pode_extrair(texto):
        return ExtratorAgendamento.extrair(texto)

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
