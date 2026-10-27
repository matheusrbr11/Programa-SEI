"""Constantes, URLs e regex comuns ao Crédito em Conta e ao Depósito Judicial."""

from pathlib import Path
import os

from ambiente import (  # noqa: F401 — reexportados para os módulos dos fluxos
    VERSAO_SEI, VERSAO_SIAFE_RIO_2, VERSAO_SIAFE_RIO_1,
    PASTA_LOG_GERAL, PASTA_GR, CAMINHO_HERMES,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_BASE_PATH = Path(__file__).resolve().parent.parent
CAMINHO_DRIVER_EDGE = str(PROJECT_BASE_PATH / "driver" / "msedgedriver.exe")

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
URL_SIAFE2 = "https://siafe2.fazenda.rj.gov.br/Siafe/faces/login.jsp"
URL_SIAFE2_HML = "https://hml2.fazenda.rj.gov.br/Siafe/faces/login.jsp"
URL_SIAFE1 = "https://www5.fazenda.rj.gov.br/SiafeRio/faces/login.jsp"
URL_BB = "https://www63.bb.com.br/portalbb/djo/id/resgate/dadosResgate,802,4647,500828,0,1,1.bbx"
URL_SIAFE = {1: URL_SIAFE2, 3: URL_SIAFE2_HML, 4: URL_SIAFE1}
SAIR_SIAFE = '//*[@id="pt1:pt_rhcl1:pt_np1:pt_cni2"]'

# ---------------------------------------------------------------------------
# CNPJ e UG
# ---------------------------------------------------------------------------
CNPJ_PRINCIPAL = "42.498.675/0001-52"
CNPJ_ALTERNATIVO = "42.498.600/0001-71"

CODIGO_UG_SIAFE = "999900"

# ---------------------------------------------------------------------------
# Documentos / Títulos
# ---------------------------------------------------------------------------
NOME_PDF_PGE = "Documento"
TITULO_DESPACHO = "À SUBAFIN,"

# Modelo de despacho salvo no SEI é individual por usuário; 'bdeza' usa o
# modelo com sufixo B, qualquer outro usuário usa o modelo com sufixo M.
SUFIXO_DESPACHO_USUARIO = "B" if os.getlogin().lower() == "bdeza" else "M"
BLOCO_ASSINATURA = "1437591 - BLOCO PARA ASSINATURAS DOS DESPACHOS DA COOCB - SUPCONFI"
ORGAO_SEI_PADRAO = "SEFAZ"
# o SEI abre na última unidade usada; sem fixar, o marcador pode vir vazio e o lote "terminar" sem nada
UNIDADE_SEI = "SEFAZ/COOCB"

# ---------------------------------------------------------------------------
# SEI
# ---------------------------------------------------------------------------
NIVEL_ACESSO_SEI = "restrito"
HIPOTESE_LEGAL = "Controle Interno (Art. 26, § 3º, da Lei nº 10.180/2001)"

# ---------------------------------------------------------------------------
# Meses (data por extenso)
# ---------------------------------------------------------------------------
MESES = {
    "janeiro": "01", "fevereiro": "02", "março": "03", "abril": "04",
    "maio": "05", "junho": "06", "julho": "07", "agosto": "08",
    "setembro": "09", "outubro": "10", "novembro": "11", "dezembro": "12",
}

# ---------------------------------------------------------------------------
# Regex reutilizáveis
# ---------------------------------------------------------------------------
PADRAO_CNJ = (
    r"(\d{7}[\.\-\s]?\d{2}[\.\-\s]?\d{4}[\.\-\s]?\d[\.\-\s]?\d{2}[\.\-\s]?\d{4}"
    r"|\d{4}[\.\-\s]?\d{3}[\.\-\s]?\d{6}[\.\-\s]?\d"
    r"|\d{14,20})"
)
PADRAO_DATA_EXTENSO = (
    r"(?:^|\n)\s*[A-Za-zÀ-ÿ\s]{3,30}?\,\s*(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})"
)
PADRAO_CNPJ = r"(\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[\/\s]?\d{4}[\-\s]{0,3}\d{2})"

CNPJ_ESTADO = r"(42\.?498\.?675/0001-?52)"
