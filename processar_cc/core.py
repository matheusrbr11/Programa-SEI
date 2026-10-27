"""Persistência do Crédito em Conta: tabela processos_credito_conta no hermes.db.

O repositório fica em ``analise_comum.repositorio``."""

from analise_comum.repositorio import Repositorio

from .config import TABELA_PROCESSOS

repositorio = Repositorio(TABELA_PROCESSOS, prefixo_indice="idx_proc_cc")

inicializar_tabela_processos = repositorio.inicializar
upsert_processo = repositorio.upsert
buscar_processo_por_status = repositorio.buscar_por_status
buscar_processo_por_numero = repositorio.buscar_por_numero
