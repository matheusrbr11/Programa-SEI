"""Ambiente do Programa SEI (Produção/Homologação) e os caminhos que dependem dele.

Lido pela interface (main.py) e pelos módulos processar_cc e processar_dj.
"""

from pathlib import Path

# Mesma numeração de versaoSei/versaoSiafe da jupiter: 1=Produção, 3=Homologação.
VERSAO_SEI = 1
HOMOLOGACAO = VERSAO_SEI == 3
NOME_AMBIENTE = "Homologação" if HOMOLOGACAO else "Produção"

# SIAFE-Rio 2 acompanha o ambiente do SEI; o SIAFE-Rio 1 (GRs de 2016 a 2023)
# não tem homologação, então segue na versão 4 (só consulta e download de GR).
VERSAO_SIAFE_RIO_2 = 3 if HOMOLOGACAO else 1
VERSAO_SIAFE_RIO_1 = 4

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_BASE_PATH = Path(__file__).resolve().parent

# Na produção o programa roda de "Programas da SUPCONC/Programa SEI" (ver exe.py), e as
# pastas compartilhadas (logs, GRs PRJ, Programa Hermes) ficam ao lado dele.
PASTA_SUPCONC = PROJECT_BASE_PATH.parent

# Em homologação, tudo fica numa pasta local, para não misturar com a produção.
# Copie o hermes.db da produção para homologacao/base de dados/ antes de rodar.
if HOMOLOGACAO:
    PASTA_HOMOLOGACAO = PROJECT_BASE_PATH / "homologacao"
    PASTA_LOG_GERAL = str(PASTA_HOMOLOGACAO / "logs")
    PASTA_GR = PASTA_HOMOLOGACAO / "GRs PRJ"
    CAMINHO_HERMES = PASTA_HOMOLOGACAO / "base de dados" / "hermes.db"
    PASTA_GR.mkdir(parents=True, exist_ok=True)
else:
    PASTA_LOG_GERAL = str(PASTA_SUPCONC / "logs" / "Programa SEI")
    PASTA_GR = PASTA_SUPCONC / "GRs PRJ"
    CAMINHO_HERMES = PASTA_SUPCONC / "Programa Hermes" / "base de dados" / "hermes.db"
