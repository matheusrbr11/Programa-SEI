"""Constantes e configurações de negócio do Crédito em Conta.

O que é comum aos dois fluxos fica em ``analise_comum.config``."""

from analise_comum.config import SUFIXO_DESPACHO_USUARIO

# ---------------------------------------------------------------------------
# Contas
# ---------------------------------------------------------------------------
CONTAS_PROCESSAR = ("00000291632-0",)

# ---------------------------------------------------------------------------
# Marcadores SEI
# ---------------------------------------------------------------------------
MARCADOR_FILTRO = "PGE - Credito em Conta - Processar"
MARCADOR_CONCLUIDO = "PGE - Credito em Conta - Concluido"

# ---------------------------------------------------------------------------
# Documentos / Títulos
# ---------------------------------------------------------------------------
DESPACHO_PADRAO = f"DPJ_{SUFIXO_DESPACHO_USUARIO}"
CAMPOS_OBRIGATORIOS_DESPACHO = ("valor", "data", "num_documento")

# ---------------------------------------------------------------------------
# SEI
# ---------------------------------------------------------------------------
TIPOS_NAO_PGE = ("Guia de Recolhimento", "Comprovante de Resgate", "Despacho")

# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
MODALIDADE = "cc"  # coluna modalidade da tabela processos
