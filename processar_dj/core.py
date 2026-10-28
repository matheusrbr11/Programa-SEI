"""Persistência do Depósito Judicial: registros com modalidade 'dj' na tabela processos do hermes.db.

O repositório fica em ``analise_comum.repositorio``; as colunas próprias do DJ
(dados do despacho e do Comprovante DJO) estão em ``COLUNAS_POR_MODALIDADE``."""

from analise_comum.repositorio import Repositorio

from .config import MODALIDADE

repositorio = Repositorio(MODALIDADE)

inicializar_tabela_processos = repositorio.inicializar
upsert_processo = repositorio.upsert
buscar_processo_por_status = repositorio.buscar_por_status
buscar_processo_por_numero = repositorio.buscar_por_numero
modalidade_em_conflito = repositorio.modalidade_em_conflito
