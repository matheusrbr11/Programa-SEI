"""Constantes, URLs, regex globais e configurações de negócio do Depósito Judicial."""

from pathlib import Path
import tempfile

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_BASE_PATH = Path(__file__).resolve().parent.parent
PASTA_LOG_GERAL = r"\\cifs-zone1\tesouro\Programas da SUPCONC\logs\Programa SEI"
PASTA_GR = Path(r"//cifs-zone1/tesouro/Programas da SUPCONC/GRs PRJ")
CAMINHO_HERMES = Path(r"\\cifs-zone1\tesouro\Programas da SUPCONC\Programa Hermes\base de dados\hermes.db")
CAMINHO_DRIVER_EDGE = str(PROJECT_BASE_PATH / "driver" / "msedgedriver.exe")
CAMINHO_TEMPLATE_RESGATE = PROJECT_BASE_PATH / "Resgate Modelo.xlsx"

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
URL_SIAFE2 = "https://siafe2.fazenda.rj.gov.br/Siafe/faces/login.jsp"
URL_SIAFE1 = "https://www5.fazenda.rj.gov.br/SiafeRio/faces/login.jsp"
URL_BB = "https://www63.bb.com.br/portalbb/djo/id/resgate/dadosResgate,802,4647,500828,0,1,1.bbx"
URL_SIAFE = {1: URL_SIAFE2, 4: URL_SIAFE1}

# ---------------------------------------------------------------------------
# Contas
# ---------------------------------------------------------------------------
CONTA_PROCESSAR = "00000291921-4"

# CNPJ do Estado (fallback quando o documento não traz o CNPJ explicitamente)
CNPJ_ESTADO = r"(42\.?498\.?675/0001-?52)"

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
NOME_PDF_PGE = "Documento"
NOME_TITULO_GR = "Guia de Recolhimento"
NOME_TITULO_COMPROVANTE_BB = "Comprovante de Resgate"
NOME_TITULO_COMPROVANTE_DJO = "Comprovante DJO"
TITULO_DESPACHO = "À SUBAFIN,"
DESPACHO_PADRAO = "DJT"
BLOCO_ASSINATURA = "1240785 - Assinatura de despachos da COOCCB"
ORGAO_SEI_PADRAO = "SEFAZ"

# ---------------------------------------------------------------------------
# SEI
# ---------------------------------------------------------------------------
NIVEL_ACESSO_SEI = "restrito"
HIPOTESE_LEGAL = "Controle Interno (Art. 26, § 3º, da Lei nº 10.180/2001)"
TIPOS_NAO_PGE = ("Guia de Recolhimento", "Comprovante de Resgate", "Comprovante DJO", "Despacho")

# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
TABELA_PROCESSOS = "processos_deposito_judicial"

# ---------------------------------------------------------------------------
# Meses (data por extenso)
# ---------------------------------------------------------------------------
MESES = {
    "janeiro": "01", "fevereiro": "02", "março": "03", "abril": "04",
    "maio": "05", "junho": "06", "julho": "07", "agosto": "08",
    "setembro": "09", "outubro": "10", "novembro": "11", "dezembro": "12",
}
NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
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
