"""Constantes, URLs, regex globais e configurações de negócio."""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_BASE_PATH = Path(__file__).resolve().parent.parent
PASTA_LOG_GERAL = r"\\cifs-zone1\tesouro\Programas da SUPCONC\logs\Programa SEI"
PASTA_GR = Path(r"//cifs-zone1/tesouro/Programas da SUPCONC/GRs PRJ")
# Banco de dados permanece o do Programa Hermes (recurso compartilhado em rede):
# o Módulo SEI usa a tabela própria "processos_credito_conta" e também lê a
# tabela "contabilizacoes" (GRs já contabilizadas pelo PRJ) nesse mesmo arquivo.
# TODO: apontando para hermes_testes.db (local) enquanto em testes — reverter para o caminho de rede antes de ir para produção.
CAMINHO_HERMES = PROJECT_BASE_PATH / "hermes_testes.db"
# CAMINHO_HERMES = Path(r"\\cifs-zone1\tesouro\Programas da SUPCONC\Programa Hermes\base de dados\hermes.db")
CAMINHO_DRIVER_EDGE = str(PROJECT_BASE_PATH / "driver" / "msedgedriver.exe")

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
CONTA_PROCESSAR = "00000291632-0"

# ---------------------------------------------------------------------------
# Marcadores SEI
# ---------------------------------------------------------------------------
MARCADOR_FILTRO = "PGE - Credito em Conta - Processar"
MARCADOR_CONCLUIDO = "PGE - Credito em Conta - Concluido"

# ---------------------------------------------------------------------------
# Documentos / Títulos
# ---------------------------------------------------------------------------
NOME_PDF_PGE = "Documento"
TITULO_DESPACHO = "À SUBAFIN,"
DESPACHO_PADRAO = "DPJ"
BLOCO_ASSINATURA = "1240785 - Assinatura de despachos da COOCCB"
ORGAO_SEI_PADRAO = "SEFAZ"

# ---------------------------------------------------------------------------
# SEI
# ---------------------------------------------------------------------------
NIVEL_ACESSO_SEI = "restrito"
HIPOTESE_LEGAL = "Controle Interno (Art. 26, § 3º, da Lei nº 10.180/2001)"
TIPOS_NAO_PGE = ("Guia de Recolhimento", "Comprovante de Resgate", "Despacho")

# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
TABELA_PROCESSOS = "processos_credito_conta"

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
