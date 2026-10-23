"""Persistência do fluxo: tabela de processos no hermes.db.

Exceções e setup de ambiente ficam em ``processar_comum.core``."""

from __future__ import annotations

from contextlib import closing
from typing import Any
import sqlite3

from processar_comum.config import CAMINHO_HERMES

from .config import TABELA_PROCESSOS


# ---------------------------------------------------------------------------
# Persistência — Repository Pattern, acesso exclusivo à tabela
# processos_deposito_judicial. NÃO modifica outras tabelas do banco hermes.db.
# ---------------------------------------------------------------------------
def _conectar_db() -> sqlite3.Connection:
    con = sqlite3.connect(CAMINHO_HERMES)
    con.row_factory = sqlite3.Row
    return con


def inicializar_tabela_processos() -> None:
    """Garante que a tabela e os índices existam."""
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {TABELA_PROCESSOS} (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        processo                  TEXT    NOT NULL,
        status                    TEXT    NOT NULL DEFAULT 'pendente',
        conta                     TEXT,
        conta_judicial            TEXT,
        processo_judicial         TEXT,
        data_pagamento            TEXT,
        ano                       INTEGER,
        valor_pesquisa            REAL,
        valor_resgate             REAL,
        valor_30                  REAL,
        num_doc                   TEXT,
        caminho_comprovante       TEXT,
        caminho_comprovante_djo  TEXT,
        caminho_gr                TEXT,
        cnpj                      TEXT,
        data_alvara               TEXT,
        reu                       TEXT,
        titulo_documento          TEXT,
        numero_documento          TEXT,
        tem_comprovante           INTEGER DEFAULT 0,
        tem_comprovante_djo       INTEGER DEFAULT 0,
        tem_gr                    INTEGER DEFAULT 0,
        tem_despacho_apos_gr      INTEGER DEFAULT 0,
        usuario_resposta          TEXT,
        data_hora_resposta        TEXT,
        tempo_resposta            REAL
    );
    CREATE INDEX IF NOT EXISTS idx_proc_dj_status   ON {TABELA_PROCESSOS}(status);
    CREATE INDEX IF NOT EXISTS idx_proc_dj_processo ON {TABELA_PROCESSOS}(processo);
    """
    with closing(_conectar_db()) as con:
        con.executescript(ddl)
        con.commit()


def upsert_processo(
    processo: str,
    status: str,
    conta: str | None = None,
    conta_judicial: str | None = None,
    processo_judicial: str | None = None,
    data_pagamento: str | None = None,
    ano: int | None = None,
    valor_pesquisa: float | None = None,
    valor_resgate: float | None = None,
    valor_30: float | None = None,
    num_doc: str | None = None,
    caminho_comprovante: str | None = None,
    caminho_comprovante_djo: str | None = None,
    caminho_gr: str | None = None,
    cnpj: str | None = None,
    data_alvara: str | None = None,
    reu: str | None = None,
    titulo_documento: str | None = None,
    numero_documento: str | None = None,
    tem_comprovante: int | None = None,
    tem_comprovante_djo: int | None = None,
    tem_gr: int | None = None,
    tem_despacho_apos_gr: int | None = None,
    usuario_resposta: str | None = None,
    data_hora_resposta: str | None = None,
    tempo_resposta: float | None = None,
) -> int:
    """Insere ou atualiza um registro na tabela. Campos não informados (None)
    são preservados, não sobrescritos."""
    with closing(_conectar_db()) as con:
        cur = con.execute(
            f"SELECT id FROM {TABELA_PROCESSOS} WHERE processo = ?",
            (processo,),
        )
        row = cur.fetchone()

        if row:
            reg_id = row["id"]
            campos: list[str] = []
            valores: list[Any] = []

            locais = {
                "status": status,
                "conta": conta,
                "conta_judicial": conta_judicial,
                "processo_judicial": processo_judicial,
                "data_pagamento": data_pagamento,
                "ano": ano,
                "valor_pesquisa": valor_pesquisa,
                "valor_resgate": valor_resgate,
                "valor_30": valor_30,
                "num_doc": num_doc,
                "caminho_comprovante": caminho_comprovante,
                "caminho_comprovante_djo": caminho_comprovante_djo,
                "caminho_gr": caminho_gr,
                "cnpj": cnpj,
                "data_alvara": data_alvara,
                "reu": reu,
                "titulo_documento": titulo_documento,
                "numero_documento": numero_documento,
                "tem_comprovante": tem_comprovante,
                "tem_comprovante_djo": tem_comprovante_djo,
                "tem_gr": tem_gr,
                "tem_despacho_apos_gr": tem_despacho_apos_gr,
                "usuario_resposta": usuario_resposta,
                "data_hora_resposta": data_hora_resposta,
                "tempo_resposta": tempo_resposta,
            }
            for k, v in locais.items():
                if v is not None:
                    campos.append(f"{k} = ?")
                    valores.append(v)

            if not campos:
                return reg_id

            valores.append(reg_id)
            sql = f"UPDATE {TABELA_PROCESSOS} SET {', '.join(campos)} WHERE id = ?"
            con.execute(sql, valores)
            con.commit()
            return reg_id

        # INSERT
        con.execute(
            f"""INSERT INTO {TABELA_PROCESSOS}
                (processo, status, conta, conta_judicial, processo_judicial,
                 data_pagamento, ano, valor_pesquisa, valor_resgate, valor_30,
                 num_doc, caminho_comprovante, caminho_comprovante_djo, caminho_gr,
                 cnpj, data_alvara, reu, titulo_documento, numero_documento,
                 tem_comprovante, tem_comprovante_djo, tem_gr, tem_despacho_apos_gr,
                 usuario_resposta, data_hora_resposta, tempo_resposta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (processo, status, conta, conta_judicial, processo_judicial,
             data_pagamento, ano, valor_pesquisa, valor_resgate, valor_30,
             num_doc, caminho_comprovante, caminho_comprovante_djo, caminho_gr,
             cnpj, data_alvara, reu, titulo_documento, numero_documento,
             tem_comprovante or 0, tem_comprovante_djo or 0, tem_gr or 0, tem_despacho_apos_gr or 0,
             usuario_resposta, data_hora_resposta, tempo_resposta),
        )
        con.commit()
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def buscar_processo_por_status(status: str) -> list[dict]:
    """Busca todos os registros com o status informado."""
    with closing(_conectar_db()) as con:
        cur = con.execute(
            f"SELECT * FROM {TABELA_PROCESSOS} WHERE status = ? ORDER BY id", (status,)
        )
        return [dict(r) for r in cur.fetchall()]


def buscar_processo_por_numero(processo: str) -> dict | None:
    """Busca o registro do processo informado."""
    with closing(_conectar_db()) as con:
        cur = con.execute(f"SELECT * FROM {TABELA_PROCESSOS} WHERE processo = ?", (processo,))
        row = cur.fetchone()
        return dict(row) if row else None
