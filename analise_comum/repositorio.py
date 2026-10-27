"""Persistência — Repository Pattern, acesso exclusivo à tabela de processos
de cada fluxo no hermes.db. NÃO modifica outras tabelas do banco."""

from __future__ import annotations

from contextlib import closing
from typing import Any
import sqlite3

from .config import CAMINHO_HERMES

# Colunas comuns aos dois fluxos (além de id, processo e status), na ordem da tabela.
# Colunas "INTEGER DEFAULT 0" são flags: gravadas como 0 quando não informadas.
COLUNAS_BASE: tuple[tuple[str, str], ...] = (
    ("conta", "TEXT"),
    ("conta_judicial", "TEXT"),
    ("processo_judicial", "TEXT"),
    ("data_pagamento", "TEXT"),
    ("ano", "INTEGER"),
    ("valor_pesquisa", "REAL"),
    ("caminho_comprovante", "TEXT"),
    ("caminho_gr", "TEXT"),
    ("num_doc", "TEXT"),
    ("cnpj", "TEXT"),
    ("data_alvara", "TEXT"),
    ("tem_gr", "INTEGER DEFAULT 0"),
    ("tem_comprovante", "INTEGER DEFAULT 0"),
    ("tem_despacho_apos_gr", "INTEGER DEFAULT 0"),
    ("usuario_resposta", "TEXT"),
    ("data_hora_resposta", "TEXT"),
    ("tempo_resposta", "REAL"),
)

_TIPO_FLAG = "INTEGER DEFAULT 0"


class Repositorio:
    """Tabela de processos de um fluxo: ``tabela`` com as colunas base mais
    ``colunas_extras``. ``prefixo_indice`` mantém os nomes dos índices já
    criados no banco (ex.: 'idx_proc_cc')."""

    def __init__(self, tabela: str, prefixo_indice: str, colunas_extras: tuple[tuple[str, str], ...] = ()):
        self.tabela = tabela
        self.prefixo_indice = prefixo_indice
        self.colunas = COLUNAS_BASE + colunas_extras
        self._nomes = tuple(nome for nome, _ in self.colunas)
        self._flags = frozenset(nome for nome, tipo in self.colunas if tipo == _TIPO_FLAG)

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(CAMINHO_HERMES)
        con.row_factory = sqlite3.Row
        return con

    def inicializar(self) -> None:
        """Garante que a tabela e os índices existam."""
        colunas_ddl = ",\n".join(f"        {nome:<25} {tipo}" for nome, tipo in self.colunas)
        ddl = f"""
    CREATE TABLE IF NOT EXISTS {self.tabela} (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        processo                  TEXT    NOT NULL,
        status                    TEXT    NOT NULL DEFAULT 'pendente',
{colunas_ddl}
    );
    CREATE INDEX IF NOT EXISTS {self.prefixo_indice}_status   ON {self.tabela}(status);
    CREATE INDEX IF NOT EXISTS {self.prefixo_indice}_processo ON {self.tabela}(processo);
    """
        with closing(self._conectar()) as con:
            con.executescript(ddl)
            con.commit()

    def upsert(self, processo: str, status: str, **campos: Any) -> int:
        """Insere ou atualiza um registro na tabela. Campos não informados (None)
        são preservados, não sobrescritos."""
        desconhecidos = set(campos) - set(self._nomes)
        if desconhecidos:
            raise TypeError(f"Colunas inexistentes em {self.tabela}: {', '.join(sorted(desconhecidos))}")

        with closing(self._conectar()) as con:
            cur = con.execute(
                f"SELECT id FROM {self.tabela} WHERE processo = ?",
                (processo,),
            )
            row = cur.fetchone()

            if row:
                reg_id = row["id"]
                atualizar = {"status": status, **campos}
                pares = [(k, v) for k, v in atualizar.items() if v is not None]
                if not pares:
                    return reg_id

                sql = f"UPDATE {self.tabela} SET {', '.join(f'{k} = ?' for k, _ in pares)} WHERE id = ?"
                con.execute(sql, [v for _, v in pares] + [reg_id])
                con.commit()
                return reg_id

            # INSERT: grava todas as colunas, com 0 nas flags não informadas
            valores = [
                (campos.get(nome) or 0) if nome in self._flags else campos.get(nome)
                for nome in self._nomes
            ]
            con.execute(
                f"""INSERT INTO {self.tabela}
                    (processo, status, {', '.join(self._nomes)})
                    VALUES ({', '.join('?' * (len(self._nomes) + 2))})""",
                [processo, status, *valores],
            )
            con.commit()
            return con.execute("SELECT last_insert_rowid()").fetchone()[0]

    def buscar_por_status(self, status: str) -> list[dict]:
        """Busca todos os registros com o status informado."""
        with closing(self._conectar()) as con:
            cur = con.execute(
                f"SELECT * FROM {self.tabela} WHERE status = ? ORDER BY id", (status,)
            )
            return [dict(r) for r in cur.fetchall()]

    def buscar_por_numero(self, processo: str) -> dict | None:
        """Busca o registro do processo informado."""
        with closing(self._conectar()) as con:
            cur = con.execute(f"SELECT * FROM {self.tabela} WHERE processo = ?", (processo,))
            row = cur.fetchone()
            return dict(row) if row else None
