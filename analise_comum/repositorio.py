"""Persistência — Repository Pattern, acesso à tabela ``processos`` no argos.db,
o banco próprio do programa. O hermes.db é só lido (``conectar_somente_leitura``).

Cada processo tem uma única modalidade ('cc' ou 'dj'). O processo só aparece
nos dois fluxos se alguém o colocou no marcador errado; ver ``upsert``."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote
import sqlite3

from .config import CAMINHO_ARGOS
from .log import log
from .pendencias import CATEGORIA_POR_STATUS

TABELA = "processos"
STATUS_IGNORADO = "ignorado"

# Colunas comuns às duas modalidades (além de id, processo, modalidade e status), na ordem da tabela.
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
    ("motivo_pendencia", "TEXT"),
)

# Colunas que só uma modalidade preenche; nas outras ficam nulas (ou 0, se flag)
COLUNAS_POR_MODALIDADE: dict[str, tuple[tuple[str, str], ...]] = {
    "cc": (),
    "dj": (  # dados do despacho e do Comprovante DJO
        ("valor_resgate", "REAL"),
        ("valor_30", "REAL"),
        ("caminho_comprovante_djo", "TEXT"),
        ("reu", "TEXT"),
        ("titulo_documento", "TEXT"),
        ("numero_documento", "TEXT"),
        ("tem_comprovante_djo", "INTEGER DEFAULT 0"),
    ),
}

COLUNAS_TABELA = COLUNAS_BASE + tuple(c for extras in COLUNAS_POR_MODALIDADE.values() for c in extras)

_TIPO_FLAG = "INTEGER DEFAULT 0"


class ConflitoModalidade(Exception):
    """O processo já está registrado, e ativo, na outra modalidade."""


def conectar_somente_leitura(caminho: Path) -> sqlite3.Connection:
    """Abre o banco com mode=ro: o próprio SQLite recusa qualquer escrita.
    A URI leva o caminho nativo escapado, que vale para disco local e para rede (\\\\servidor\\...)."""
    return sqlite3.connect(f"file:{quote(str(caminho), safe=':')}?mode=ro", uri=True)


def comandos_ddl() -> list[str]:
    """CREATE da tabela ``processos`` e do índice (usados também pela migração)."""
    colunas_ddl = ",\n".join(f"        {nome:<25} {tipo}" for nome, tipo in COLUNAS_TABELA)
    modalidades = ", ".join(f"'{m}'" for m in COLUNAS_POR_MODALIDADE)
    return [
        f"""CREATE TABLE IF NOT EXISTS {TABELA} (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        processo                  TEXT    NOT NULL UNIQUE,
        modalidade                TEXT    NOT NULL CHECK (modalidade IN ({modalidades})),
        status                    TEXT    NOT NULL DEFAULT 'pendente',
{colunas_ddl}
    )""",
        f"CREATE INDEX IF NOT EXISTS idx_processos_modalidade_status ON {TABELA}(modalidade, status)",
    ]


class Repositorio:
    """Registros de uma modalidade na tabela ``processos``. As leituras só
    enxergam a própria modalidade; a escrita só aceita as colunas dela
    (``nomes``; ``flags`` são as colunas 'tem_*' gravadas como 0 quando ausentes)."""

    def __init__(self, modalidade: str):
        if modalidade not in COLUNAS_POR_MODALIDADE:
            raise ValueError(f"Modalidade inválida: {modalidade!r}")
        self.modalidade = modalidade
        self.colunas = COLUNAS_BASE + COLUNAS_POR_MODALIDADE[modalidade]
        self.nomes = tuple(nome for nome, _ in self.colunas)
        self.flags = frozenset(nome for nome, tipo in self.colunas if tipo == _TIPO_FLAG)

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(CAMINHO_ARGOS)
        con.row_factory = sqlite3.Row
        return con

    def inicializar(self) -> None:
        """Garante que a tabela, o índice e as colunas existam (a tabela pode
        ter sido criada por uma versão anterior do programa)."""
        CAMINHO_ARGOS.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conectar()) as con:
            for comando in comandos_ddl():
                con.execute(comando)
            existentes = {r["name"] for r in con.execute(f"PRAGMA table_info({TABELA})")}
            for nome, tipo in COLUNAS_TABELA:
                if nome not in existentes:
                    try:
                        con.execute(f"ALTER TABLE {TABELA} ADD COLUMN {nome} {tipo}")
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e):  # outra máquina acabou de incluir
                            raise
            con.commit()

    def upsert(self, processo: str, status: str, **campos: Any) -> int:
        """Insere ou atualiza o registro do processo. Campos não informados (None)
        são preservados, não sobrescritos; a exceção é ``motivo_pendencia``, que
        descreve a situação atual e é apagado por qualquer gravação sem ele.

        Se o processo estiver registrado na outra modalidade: com status
        'ignorado', esta modalidade assume o registro (marcador corrigido);
        senão levanta ``ConflitoModalidade`` e nada é gravado."""
        desconhecidos = set(campos) - set(self.nomes)
        if desconhecidos:
            raise TypeError(f"Colunas inexistentes na modalidade {self.modalidade}: {', '.join(sorted(desconhecidos))}")

        with closing(self._conectar()) as con:
            row = con.execute(
                f"SELECT id, modalidade, status FROM {TABELA} WHERE processo = ?",
                (processo,),
            ).fetchone()

            if row and row["modalidade"] != self.modalidade:
                if row["status"] != STATUS_IGNORADO:
                    raise ConflitoModalidade(
                        f"{processo} já está registrado como '{row['modalidade']}' (status '{row['status']}')."
                    )
                log.info(f"{processo} estava ignorado como '{row['modalidade']}'; passa para '{self.modalidade}'.")
                con.execute(f"DELETE FROM {TABELA} WHERE id = ?", (row["id"],))  # recomeça sem os dados da outra modalidade
                row = None

            if row:
                reg_id = row["id"]
                atualizar = {"status": status, **campos}
                pares = [(k, v) for k, v in atualizar.items() if v is not None and k != "motivo_pendencia"]
                pares.append(("motivo_pendencia", campos.get("motivo_pendencia")))

                sql = f"UPDATE {TABELA} SET {', '.join(f'{k} = ?' for k, _ in pares)} WHERE id = ?"
                con.execute(sql, [v for _, v in pares] + [reg_id])
                con.commit()
                return reg_id

            # INSERT: grava todas as colunas da modalidade, com 0 nas flags não informadas
            valores = [
                (campos.get(nome) or 0) if nome in self.flags else campos.get(nome)
                for nome in self.nomes
            ]
            cur = con.execute(
                f"""INSERT INTO {TABELA}
                    (processo, modalidade, status, {', '.join(self.nomes)})
                    VALUES ({', '.join('?' * (len(self.nomes) + 3))})""",
                [processo, self.modalidade, status, *valores],
            )
            con.commit()  # o DELETE da troca de modalidade vai junto, na mesma transação
            return cur.lastrowid

    def registrar_motivo(self, processo: str, motivo: str) -> None:
        """Grava o motivo da pendência sem mudar o status do processo."""
        with closing(self._conectar()) as con:
            con.execute(
                f"UPDATE {TABELA} SET motivo_pendencia = ? WHERE processo = ? AND modalidade = ?",
                (motivo, processo, self.modalidade),
            )
            con.commit()

    def listar_pendencias(self) -> list[dict]:
        """Pendências abertas da modalidade: ``{"processo", "categoria", "motivo"}``."""
        marcas = ", ".join("?" * len(CATEGORIA_POR_STATUS))
        with closing(self._conectar()) as con:
            cur = con.execute(
                f"""SELECT processo, status, motivo_pendencia FROM {TABELA}
                    WHERE modalidade = ? AND (status IN ({marcas})
                        OR (status = 'dados_coletados' AND motivo_pendencia IS NOT NULL))
                    ORDER BY id""",
                (self.modalidade, *CATEGORIA_POR_STATUS),
            )
            return [
                {
                    "processo": r["processo"],
                    "categoria": CATEGORIA_POR_STATUS.get(r["status"], "finalizacao"),
                    "motivo": r["motivo_pendencia"],
                }
                for r in cur.fetchall()
            ]

    def modalidade_em_conflito(self, processo: str) -> str | None:
        """A outra modalidade em que o processo está registrado e ativo (status
        diferente de 'ignorado'), ou None. Serve para pular, antes da coleta,
        um processo colocado no marcador errado."""
        with closing(self._conectar()) as con:
            row = con.execute(
                f"SELECT modalidade FROM {TABELA} WHERE processo = ? AND modalidade != ? AND status != ?",
                (processo, self.modalidade, STATUS_IGNORADO),
            ).fetchone()
            return row["modalidade"] if row else None

    def buscar_por_status(self, status: str) -> list[dict]:
        """Busca os registros da modalidade com o status informado."""
        with closing(self._conectar()) as con:
            cur = con.execute(
                f"SELECT * FROM {TABELA} WHERE modalidade = ? AND status = ? ORDER BY id",
                (self.modalidade, status),
            )
            return [dict(r) for r in cur.fetchall()]

    def buscar_por_numero(self, processo: str) -> dict | None:
        """Busca o registro do processo, se ele for desta modalidade."""
        with closing(self._conectar()) as con:
            cur = con.execute(
                f"SELECT * FROM {TABELA} WHERE processo = ? AND modalidade = ?",
                (processo, self.modalidade),
            )
            row = cur.fetchone()
            return dict(row) if row else None
