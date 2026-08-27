"""Núcleo: exceções, dataclasses de domínio, setup de ambiente e persistência."""

from __future__ import annotations

from dataclasses import dataclass, field
import undetected_chromedriver as uc
from contextlib import closing
from jupiter import configurar_log
from typing import Any
import urllib3
import logging
import sqlite3
import base64
import sys
import traceback

from .config import CAMINHO_HERMES, PASTA_LOG_GERAL, PROJECT_BASE_PATH, TABELA_PROCESSOS

log = logging.getLogger("jupiter.processarDJ")


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

# Erros de autenticação:
class ErroLoginSEI(Exception):
    """Falha de autenticacao no SEI."""
    pass


class ErroLoginSiafe(Exception):
    """Falha de autenticacao no SIAFE."""
    pass


# Erros do Processo:
class ErroProcesso(Exception):
    """Erro esperado durante o processamento de um processo específico."""
    pass


# Erros de Serviço:
class ErroSEI(ErroProcesso):
    """Erro ao interagir com o SEI (anexar, despachar, bloco, marcador)."""
    pass


class ErroSIAFE(ErroProcesso):
    """Erro ao interagir com o SIAFE (fora de falha de login)."""
    pass


class ErroBB(ErroProcesso):
    """Erro ao interagir com o site do Banco do Brasil."""
    pass


# Erros de Negócio:
class ErroExtracao(ErroProcesso):
    """Falha ao extrair dados de um documento/anexo."""
    pass


class ErroDownload(ErroProcesso):
    """Documento nao encontrado/disponivel para download (BB, SharePoint ou SIAFE)."""
    pass


class ErroValidacao(ErroProcesso):
    """Dados extraídos não passaram na validação de negócio."""
    pass


# ---------------------------------------------------------------------------
# Dataclasses de domínio
# ---------------------------------------------------------------------------
@dataclass
class DadosComprovante:
    """Dados extraídos de um Comprovante de Resgate ou Agendamento BB."""
    conta: str | None = None
    processo_judicial: str | None = None
    data_pagamento: str | None = None
    ano: int | None = None
    valor_pesquisa: float | None = None
    conta_judicial: str | None = None
    caminho_comprovante: str | None = None
    cnpj: str | None = None
    data_alvara: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conta": self.conta,
            "processo_judicial": self.processo_judicial,
            "data_pagamento": self.data_pagamento,
            "ano": self.ano,
            "valor_pesquisa": self.valor_pesquisa,
            "conta_judicial": self.conta_judicial,
            "caminho_comprovante": self.caminho_comprovante,
            "cnpj": self.cnpj,
            "data_alvara": self.data_alvara,
        }


@dataclass
class DadosOficio:
    """Dados extraídos de Ofícios, Alvarás ou Mandados (para consulta BB)."""
    data_alvara: str | None = None
    conta_judicial: str | None = None
    cnpj: str | None = None


@dataclass
class EstadoDocumentos:
    """Estado da árvore de documentos de um processo no SEI."""
    lista_nomes: list[str] = field(default_factory=list)
    tem_gr: bool = False
    tem_comprovante: bool = False
    tem_despacho_apos_gr: bool = False


@dataclass
class PayloadColeta:
    """Retorno da Etapa 1 com todos os campos necessários."""
    processo: str
    status: str
    conta: str | None = None
    conta_judicial: str | None = None
    processo_judicial: str | None = None
    data_pagamento: str | None = None
    ano: int | None = None
    valor_pesquisa: float | None = None
    cnpj: str | None = None
    data_alvara: str | None = None
    caminho_comprovante: str | None = None
    caminho_comprovante_djo: str | None = None
    caminho_gr: str | None = None
    num_doc: str | None = None
    tem_gr: int = 0
    tem_comprovante: int = 0
    tem_comprovante_djo: int = 0
    tem_despacho_apos_gr: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "processo": self.processo,
            "status": self.status,
            "conta": self.conta,
            "conta_judicial": self.conta_judicial,
            "processo_judicial": self.processo_judicial,
            "data_pagamento": self.data_pagamento,
            "ano": self.ano,
            "valor_pesquisa": self.valor_pesquisa,
            "cnpj": self.cnpj,
            "data_alvara": self.data_alvara,
            "caminho_comprovante": self.caminho_comprovante,
            "caminho_comprovante_djo": self.caminho_comprovante_djo,
            "caminho_gr": self.caminho_gr,
            "num_doc": self.num_doc,
            "tem_gr": self.tem_gr,
            "tem_comprovante": self.tem_comprovante,
            "tem_comprovante_djo": self.tem_comprovante_djo,
            "tem_despacho_apos_gr": self.tem_despacho_apos_gr,
        }


# ---------------------------------------------------------------------------
# Setup de ambiente do processo filho
# ---------------------------------------------------------------------------
_configurado = False


class FormatterSemTraceback(logging.Formatter):
    """Formatter que omite traceback para saída no console."""

    def format(self, record: logging.LogRecord) -> str:
        mensagem = record.getMessage().strip()
        primeira_linha = mensagem.splitlines()[0] if mensagem else ""
        return f"{record.levelname}: {primeira_linha}"


PREFIXO_DETALHE_ERRO = "__ERRO_DETALHE__:"


class DetalheErroHandler(logging.Handler):
    """Emite, para cada erro, uma linha extra com o detalhe completo do erro."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            partes = [record.getMessage().strip()]
            if record.exc_info:
                partes.append("".join(traceback.format_exception(*record.exc_info)).strip())
            texto_completo = "\n".join(p for p in partes if p)
            if not texto_completo:
                return
            codificado = base64.b64encode(texto_completo.encode("utf-8")).decode("ascii")
            print(f"{PREFIXO_DETALHE_ERRO}{codificado}", flush=True)
        except Exception:
            pass  # nunca deixa o proprio logging quebrar o processo


def configurar_ambiente() -> None:
    """Aplica patches do selenium e liga o log dos loggers 'jupiter' e 'automaweb' no stdout."""
    global _configurado
    if _configurado:
        return

    uc.Chrome.__del__ = lambda self: None  # evita erro no __del__ do uc
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    sys.stdout.reconfigure(encoding="utf-8")

    configurar_log("Programa SEI", PASTA_LOG_GERAL, PROJECT_BASE_PATH / "logs")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(FormatterSemTraceback())

    detalhe_handler = DetalheErroHandler()
    detalhe_handler.setLevel(logging.ERROR)

    for nome_logger in ("jupiter", "automaweb"):
        logging.getLogger(nome_logger).addHandler(stdout_handler)
        logging.getLogger(nome_logger).addHandler(detalhe_handler)

    _configurado = True


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
