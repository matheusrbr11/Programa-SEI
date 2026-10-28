"""Constantes e configurações de negócio do Depósito Judicial.

O que é comum aos dois fluxos fica em ``analise_comum.config``."""

from pathlib import Path
import tempfile

from analise_comum.config import PROJECT_BASE_PATH, SUFIXO_DESPACHO_USUARIO

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CAMINHO_TEMPLATE_RESGATE = PROJECT_BASE_PATH / "planilhas" / "Resgate Modelo.xlsx"

# ---------------------------------------------------------------------------
# Contas
# ---------------------------------------------------------------------------
CONTAS_PROCESSAR = ("00000291921-4", "00000291632-0")

# ---------------------------------------------------------------------------
# SharePoint
# ---------------------------------------------------------------------------
SITE_SHAREPOINT = "https://sefazrj.sharepoint.com/sites/SUBTES"
PASTA_BASE_SHAREPOINT = "/sites/SUBTES/Shared Documents/SUPGO/COOGO/Arquivos DJO Banco do Brasil"
PREFIXO_ARQUIVO_DIARIO = "RESGATES A FAVOR DO GOVER"
CAMINHO_COOKIES_SHAREPOINT = str(Path(tempfile.gettempdir()) / "cookies_SharePoint.json")

# ---------------------------------------------------------------------------
# Marcadores SEI
# ---------------------------------------------------------------------------
MARCADOR_FILTRO = "PGE - Deposito Judicial - Processar"
MARCADOR_CONCLUIDO = "PGE - Deposito Judicial - Concluido"

# ---------------------------------------------------------------------------
# Documentos / Títulos
# ---------------------------------------------------------------------------
NOME_TITULO_GR = "Guia de Recolhimento"
NOME_TITULO_COMPROVANTE_BB = "Comprovante de Resgate"
NOME_TITULO_COMPROVANTE_DJO = "Comprovante DJO - Movimento Diário"

DESPACHO_PADRAO = f"DJT_{SUFIXO_DESPACHO_USUARIO}"
CAMPOS_OBRIGATORIOS_DESPACHO = (
    "titulo_documento", "numero_documento", "processo_judicial", "reu",
    "data_pagamento", "conta_judicial", "valor_resgate", "valor_30", "num_doc",
)
CAMPOS_COMPLEMENTAVEIS = ("reu", "titulo_documento", "numero_documento", "processo_judicial")

# ---------------------------------------------------------------------------
# SEI
# ---------------------------------------------------------------------------
TIPOS_NAO_PGE = ("Guia de Recolhimento", "Comprovante de Resgate", "Comprovante DJO - Movimento Diário", "Despacho")

# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
MODALIDADE = "dj"  # coluna modalidade da tabela processos

# ---------------------------------------------------------------------------
# Meses (nome do mês, para as pastas do SharePoint)
# ---------------------------------------------------------------------------
NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}
