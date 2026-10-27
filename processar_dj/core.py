"""Persistência do Depósito Judicial: tabela processos_deposito_judicial no hermes.db.

O repositório fica em ``analise_comum.repositorio``."""

from analise_comum.repositorio import Repositorio

from .config import TABELA_PROCESSOS

# Colunas além das comuns: dados do despacho e do Comprovante DJO
COLUNAS_EXTRAS = (
    ("valor_resgate", "REAL"),
    ("valor_30", "REAL"),
    ("caminho_comprovante_djo", "TEXT"),
    ("reu", "TEXT"),
    ("titulo_documento", "TEXT"),
    ("numero_documento", "TEXT"),
    ("tem_comprovante_djo", "INTEGER DEFAULT 0"),
)

repositorio = Repositorio(TABELA_PROCESSOS, prefixo_indice="idx_proc_dj", colunas_extras=COLUNAS_EXTRAS)

inicializar_tabela_processos = repositorio.inicializar
upsert_processo = repositorio.upsert
buscar_processo_por_status = repositorio.buscar_por_status
buscar_processo_por_numero = repositorio.buscar_por_numero
