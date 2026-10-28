"""Depósito Judicial: o valor da GR vem da planilha de Resgate (gerada a partir
da planilha diária do SharePoint), que também vira o Comprovante DJO anexado
ao processo; despacho com os dados do documento de origem e links para os
documentos do processo."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile

from jupiter import SEI, SharePoint

from ..config import (
    PROJECT_BASE_PATH, PASTA_GR, SUFIXO_DESPACHO_USUARIO, TITULO_DESPACHO,
    NOME_TITULO_GR, NOME_TITULO_COMPROVANTE_BB, NOME_TITULO_COMPROVANTE_DJO,
)
from ..core import ErroDownload, ErroExtracao, ErroProcesso, ErroSEI, ErroValidacao
from ..extractors import (
    ExtratorComprovante,
    ExtratorAgendamento,
    ExtratorOficio,
    ExtratorAlvaraEletronico,
    ExtratorAlvaraLevantamento,
    ExtratorMandado,
)
from ..log import log
from ..services import valor_por_extenso
from ..utils import buscar_regex, formatar_moeda, mensagem_curta
from . import Modalidade
from .pdf import converter_planilha_para_pdf
from .planilha import gerar_planilha_resgate

CAMINHO_TEMPLATE_RESGATE = PROJECT_BASE_PATH / "planilhas" / "Resgate Modelo.xlsx"

CAMPOS_OBRIGATORIOS_DESPACHO = (
    "titulo_documento", "numero_documento", "processo_judicial", "reu",
    "data_pagamento", "conta_judicial", "valor_resgate", "valor_30", "num_doc",
)

# ---------------------------------------------------------------------------
# SharePoint — planilha diária de resgates
# ---------------------------------------------------------------------------
SITE_SHAREPOINT = "https://sefazrj.sharepoint.com/sites/SUBTES"
PASTA_BASE_SHAREPOINT = "/sites/SUBTES/Shared Documents/SUPGO/COOGO/Arquivos DJO Banco do Brasil"
PREFIXO_ARQUIVO_DIARIO = "RESGATES A FAVOR DO GOVER"
CAMINHO_COOKIES_SHAREPOINT = str(Path(tempfile.gettempdir()) / "cookies_SharePoint.json")

NOMES_MESES = {  # nome do mês nas pastas do SharePoint
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


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


# ---------------------------------------------------------------------------
# Modalidade
# ---------------------------------------------------------------------------
class DepositoJudicial(Modalidade):
    codigo = "dj"
    nome = "Depósito Judicial"
    nome_logger = "jupiter.processarDJ"

    marcador_filtro = "PGE - Deposito Judicial - Processar"
    marcador_concluido = "PGE - Deposito Judicial - Concluido"
    despacho_padrao = f"DJT_{SUFIXO_DESPACHO_USUARIO}"
    contas_processar = ("00000291921-4", "00000291632-0")
    tipos_nao_pge = (NOME_TITULO_GR, NOME_TITULO_COMPROVANTE_BB, NOME_TITULO_COMPROVANTE_DJO, "Despacho")

    campos_obrigatorios_extracao = {
        "conta": "Campo 'Conta' nao encontrado.",
        "processo_judicial": "Campo 'Processo Judicial' nao encontrado.",
        "ano": "Campo 'Ano' nao encontrado.",
        "conta_judicial": "Campo 'Conta(s) Resgatada(s)' nao encontrado.",
        "data_pagamento": "Campo 'Data do Pagamento' nao encontrado.",
    }
    campos_extras_dados = ("reu", "titulo_documento", "numero_documento", "valor_resgate", "valor_30")
    campos_complementaveis = ("reu", "titulo_documento", "numero_documento", "processo_judicial")
    campos_do_documento_origem = ("reu", "titulo_documento", "numero_documento")
    anexos_extras = ((NOME_TITULO_COMPROVANTE_DJO, "caminho_comprovante_djo", "tem_comprovante_djo"),)

    def extrair_dados_documento(self, texto: str) -> dict | None:
        """Ordem de prioridade: Alvara Eletronico -> Alvara Levantamento (exclusivos)
        -> Oficio -> Mandado -> Comprovante -> Agendamento (acumulam)."""
        if not texto or not texto.strip():
            return None

        # 1. Alvará (Eletrônico ou de Levantamento) — prioridade exclusiva
        extratores_alvara = [ExtratorAlvaraEletronico, ExtratorAlvaraLevantamento]
        for extrator in extratores_alvara:
            if extrator.pode_extrair(texto):
                dados_alvara = extrator.extrair(texto)
                if dados_alvara:
                    return self.consultar_conta_judicial(dados_alvara)

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
            dado_comprovante = ExtratorComprovante.extrair(texto, self.contas_processar)
            if dado_comprovante:
                dados_bb.append(dado_comprovante)
        if ExtratorAgendamento.pode_extrair(texto):
            dado_agendamento = ExtratorAgendamento.extrair(texto, self.contas_processar)
            if dado_agendamento:
                dados_bb.append(dado_agendamento)

        if not dados_bb:
            return None

        return self.consultar_conta_judicial(dados_bb)

    def preparar_documentos(self, dados: dict) -> dict:
        """Gera a planilha de Resgate (que define o valor da GR) e o Comprovante DJO em PDF."""
        conta_judicial = dados["conta_judicial"]
        data_pagamento = dados["data_pagamento"]
        contas_resgatadas = dados.get("contas_resgatadas") or [conta_judicial]
        caminho_planilha_resgate = PASTA_GR / f"DJT {conta_judicial}.xlsx"
        caminho_diario = baixar_planilha_diaria(data_pagamento, PASTA_GR)
        try:
            valor_pesquisa = gerar_planilha_resgate(
                caminho_template=CAMINHO_TEMPLATE_RESGATE,
                caminho_diario=caminho_diario,
                contas_resgatadas=contas_resgatadas,
                caminho_saida=caminho_planilha_resgate,
                data_pagamento=data_pagamento,
            )
            caminho_comprovante_djo = converter_planilha_para_pdf(caminho_planilha_resgate)
        except (ErroDownload, ValueError) as e:
            raise ErroExtracao(f"Erro ao gerar planilha de Resgate: {mensagem_curta(e)}") from e
        except ErroProcesso as e:
            raise ErroExtracao(f"Erro ao converter planilha de Resgate para PDF: {mensagem_curta(e)}") from e

        for caminho_xlsx in (caminho_planilha_resgate, caminho_diario):
            try:
                Path(caminho_xlsx).unlink(missing_ok=True)
            except OSError as e:
                log.warning(f"Não foi possível remover a planilha '{caminho_xlsx}': {e}")

        return {"valor_pesquisa": valor_pesquisa, "caminho_comprovante_djo": str(caminho_comprovante_djo)}

    def montar_despacho(self, registro_db: dict, estado: dict) -> dict:
        registro = {
            "num_doc": registro_db.get("num_doc"),
            "conta_judicial": registro_db.get("conta_judicial"),
            "processo_judicial": registro_db.get("processo_judicial"),
            "reu": registro_db.get("reu"),
            "titulo_documento": registro_db.get("titulo_documento"),
            "numero_documento": registro_db.get("numero_documento"),
            "valor_resgate": registro_db.get("valor_resgate"),
            "valor_30": registro_db.get("valor_30"),
            "data_pagamento": registro_db.get("data_pagamento") or "",
        }
        try:
            valor_resgate = float(registro["valor_resgate"])
            valor_30 = float(registro["valor_30"])
        except (ValueError, TypeError) as e:
            raise ErroValidacao("Valor inválido para formatação do despacho.") from e
        return {
            "registro": registro, "valor_resgate": valor_resgate, "valor_30": valor_30,
            "lista_nomes": estado["lista_nomes"],
        }

    def formatar_despacho(self, sei: SEI, despacho: dict) -> bool:
        """Despacho com valores por extenso e os index dos documentos do processo."""
        registro = despacho["registro"]
        valor_resgate, valor_30 = despacho["valor_resgate"], despacho["valor_30"]
        despacho_completo = all(registro.get(campo) for campo in CAMPOS_OBRIGATORIOS_DESPACHO)

        nome_despacho_encaminhamento = next(
            (n for n in despacho["lista_nomes"] if "Despacho de Encaminhamento de Processo" in n), None
        )
        index_despacho = buscar_regex(nome_despacho_encaminhamento, r"(\d+)") if nome_despacho_encaminhamento else None
        index_comprovante = sei.obter_numero_documento(NOME_TITULO_COMPROVANTE_BB)
        index_comprovante_djo = sei.obter_numero_documento(NOME_TITULO_COMPROVANTE_DJO)
        index_gr = sei.obter_numero_documento(NOME_TITULO_GR)

        mapa_texto = {
            "@tratamento_destinatario@ @cargo_destinatario@,": TITULO_DESPACHO,
            "[index_despacho]": index_despacho or "—",
            "[titulo_documento]": registro.get("titulo_documento") or "—",
            "[numero_documento]": registro.get("numero_documento") or "—",
            "[processo_judicial]": registro.get("processo_judicial") or "—",
            "[reu]": registro.get("reu") or "—",
            "[index_comprovante_DJO]": index_comprovante_djo or "—",
            "[data]": registro.get("data_pagamento") or "—",
            "[conta_judicial]": registro.get("conta_judicial") or "—",
            "[valor_resgate]": formatar_moeda(valor_resgate),
            "[valor_resgate_por_extenso]": valor_por_extenso(valor_resgate),
            "[index_comprovante]": index_comprovante or "—",
            "[valor_30]": formatar_moeda(valor_30),
            "[valor_30_por_extenso]": valor_por_extenso(valor_30),
            "[data_pagamento]": registro.get("data_pagamento") or "—",
            "[num_doc]": registro.get("num_doc") or "—",
            "[index_gr]": index_gr or "—",
        }
        # lista (não dict) porque [index_comprovante] aparece 2x no texto padrão do
        # despacho — cada entrada vira uma ocorrência distinta a ser transformada em link
        mapa_links = [
            (v, v) for v in (
                index_despacho, index_comprovante, index_comprovante, index_comprovante_djo, index_gr,
            ) if v
        ]

        if not sei.formatar_despacho(mapa_texto, mapa_links):
            raise ErroSEI("Formatação do despacho não confirmada pelo SEI.")
        return despacho_completo


MODALIDADE = DepositoJudicial()
