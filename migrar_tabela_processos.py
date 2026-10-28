"""Migração única: junta processos_credito_conta e processos_deposito_judicial
na tabela ``processos`` (coluna ``modalidade``) do hermes.db.

Pode rodar depois que o programa já criou ``processos``, desde que ela ainda
esteja vazia. Uso (com o Hermes e o Programa-SEI fechados):
    python migrar_tabela_processos.py [caminho do hermes.db]            # só mostra o que faria
    python migrar_tabela_processos.py [caminho do hermes.db] --aplicar  # faz backup e migra

Sem caminho, usa o CAMINHO_HERMES do ambiente configurado em ambiente.py.
As tabelas antigas viram *_old e, no lugar delas, ficam views só de leitura
com o mesmo nome: quem só consulta continua funcionando, e uma versão antiga
do programa que tente gravar nelas falha em vez de gravar no lugar errado."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import sys

from analise_comum.repositorio import (
    COLUNAS_BASE, COLUNAS_POR_MODALIDADE, STATUS_IGNORADO, TABELA, comandos_ddl,
)

TABELAS_ANTIGAS = {"cc": "processos_credito_conta", "dj": "processos_deposito_judicial"}


def _colunas(con: sqlite3.Connection, modalidade: str) -> list[str]:
    """Colunas de dados da tabela antiga, na ordem física dela (a view repete essa ordem).
    Levanta SystemExit se houver coluna que a tabela nova não tem: ela seria perdida."""
    tabela = TABELAS_ANTIGAS[modalidade]
    existentes = [r[1] for r in con.execute(f"PRAGMA table_info({tabela})") if r[1] not in ("id", "processo", "status")]
    conhecidas = {nome for nome, _ in COLUNAS_BASE + COLUNAS_POR_MODALIDADE[modalidade]}
    sobrando = [c for c in existentes if c not in conhecidas]
    if sobrando:
        sys.exit(f"{tabela} tem coluna(s) sem lugar na tabela nova: {', '.join(sobrando)}")
    return existentes


def _existe(con: sqlite3.Connection, nome: str, tipo: str = "table") -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (tipo, nome)).fetchone() is not None


def planejar(con: sqlite3.Connection) -> dict[str, set[str]]:
    """Processos de cada modalidade que NÃO entram na tabela nova (duplicados).
    Levanta SystemExit se a migração não puder ser feita."""
    for tabela in TABELAS_ANTIGAS.values():
        if _existe(con, tabela, "view"):
            sys.exit(f"'{tabela}' já é uma view: a migração já foi feita.")
        if not _existe(con, tabela):
            sys.exit(f"Tabela '{tabela}' não encontrada.")
    # o programa cria 'processos' vazia na primeira execução; só junta numa tabela ainda sem registros
    if _existe(con, TABELA) and con.execute(f"SELECT count(*) FROM {TABELA}").fetchone()[0]:
        sys.exit(f"A tabela '{TABELA}' já tem registros; a migração só junta os dados antigos numa tabela vazia.")
    for modalidade in TABELAS_ANTIGAS:
        _colunas(con, modalidade)

    duplicados = con.execute(
        f"""SELECT c.processo, c.status, d.status FROM {TABELAS_ANTIGAS['cc']} c
            JOIN {TABELAS_ANTIGAS['dj']} d ON c.processo = d.processo"""
    ).fetchall()
    descartar = {"cc": set(), "dj": set()}
    em_conflito = []
    for processo, status_cc, status_dj in duplicados:
        if status_cc == STATUS_IGNORADO:  # inclui os dois ignorados: fica o DJ, e o fluxo certo assume quando passar por ele
            descartar["cc"].add(processo)
        elif status_dj == STATUS_IGNORADO:
            descartar["dj"].add(processo)
        else:
            em_conflito.append((processo, status_cc, status_dj))
        print(f"  Nas duas tabelas: {processo} (cc: {status_cc}, dj: {status_dj})")

    if em_conflito:
        linhas = "\n".join(f"  {p}: cc '{c}', dj '{d}'" for p, c, d in em_conflito)
        sys.exit(f"Processos ativos nas duas modalidades; decida qual registro fica e rode de novo:\n{linhas}")
    return descartar


def migrar(con: sqlite3.Connection, descartar: dict[str, set[str]]) -> None:
    """Executa a migração numa transação só (tudo ou nada)."""
    con.execute("BEGIN IMMEDIATE")
    try:
        for comando in comandos_ddl():
            con.execute(comando)

        for modalidade, tabela in TABELAS_ANTIGAS.items():
            colunas = ", ".join(_colunas(con, modalidade))
            fora = sorted(descartar[modalidade])
            filtro = f"WHERE processo NOT IN ({', '.join('?' * len(fora))})" if fora else ""
            con.execute(
                f"""INSERT INTO {TABELA} (processo, modalidade, status, {colunas})
                    SELECT processo, ?, status, {colunas} FROM {tabela} {filtro} ORDER BY id""",
                [modalidade, *fora],
            )
            esperado = con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0] - len(fora)
            migrado = con.execute(f"SELECT count(*) FROM {TABELA} WHERE modalidade = ?", (modalidade,)).fetchone()[0]
            if migrado != esperado:
                raise RuntimeError(f"{tabela}: esperado {esperado} registro(s), migrado(s) {migrado}.")
            print(f"  {tabela} -> {TABELA} (modalidade '{modalidade}'): {migrado} registro(s)")

            con.execute(f"ALTER TABLE {tabela} RENAME TO {tabela}_old")
            con.execute(
                f"""CREATE VIEW {tabela} AS
                    SELECT id, processo, status, {colunas} FROM {TABELA} WHERE modalidade = '{modalidade}'"""
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def main() -> None:
    argumentos = [a for a in sys.argv[1:] if a != "--aplicar"]
    aplicar = "--aplicar" in sys.argv[1:]
    if argumentos:
        caminho = Path(argumentos[0])
    else:
        from ambiente import CAMINHO_HERMES, NOME_AMBIENTE
        caminho = CAMINHO_HERMES
        print(f"Ambiente: {NOME_AMBIENTE}")
    if not caminho.exists():
        sys.exit(f"Banco não encontrado: {caminho}")
    print(f"Banco: {caminho}")

    with closing(sqlite3.connect(caminho, isolation_level=None)) as con:
        descartar = planejar(con)
        if not aplicar:
            print("Nada foi alterado. Rode com --aplicar para migrar.")
            return

        backup = caminho.with_name(f"{caminho.stem}.antes_unificacao_{datetime.now():%Y%m%d_%H%M%S}{caminho.suffix}")
        with closing(sqlite3.connect(backup)) as destino:
            con.backup(destino)
        print(f"Backup: {backup}")

        migrar(con, descartar)
    print("Migração concluída.")


if __name__ == "__main__":
    main()
