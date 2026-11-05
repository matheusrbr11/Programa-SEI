"""Pendências: processos que precisam da ação de alguém, com o motivo.

Uma pendência é ``{"processo", "categoria", "motivo"}``. O subprocesso envia as
da execução à interface numa linha ``__PENDENCIAS__:[...]`` do stdout, e a tela
VER PENDÊNCIAS lê as que continuam abertas no banco."""

from __future__ import annotations

PREFIXO_PENDENCIAS = "__PENDENCIAS__:"

# categoria -> título exibido, na ordem de exibição
CATEGORIAS = {
    "despacho": "Revisar despacho no SEI",
    "despacho_incompleto": "Despacho incompleto: complete antes de assinar",
    "gr": "GR pendente no SIAFE",
    "coleta": "Erro na coleta de dados",
    "finalizacao": "Erro ao responder o processo",
    "marcador": "Conferir marcador",
}

# status que, sozinhos, deixam o processo pendente no banco. Um processo em
# 'dados_coletados' só é pendência se tiver motivo: a Etapa 2 falhou nele.
CATEGORIA_POR_STATUS = {
    "revisar_despacho": "despacho",
    "aguardando_gr": "gr",
    "erro_coleta": "coleta",
}


def linhas_pendencias(pendencias: list[dict], limite: int | None = None) -> list[str]:
    """Texto das pendências, agrupado por categoria. Com ``limite``, mostra só
    essa quantidade de processos e informa quantos ficaram de fora."""
    linhas: list[str] = []
    exibidos = 0
    for categoria, titulo in CATEGORIAS.items():
        do_grupo = [p for p in pendencias if p["categoria"] == categoria]
        if not do_grupo or (limite is not None and exibidos >= limite):
            continue
        if linhas:
            linhas.append("")
        linhas.append(f"{titulo} ({len(do_grupo)})")
        for pendencia in do_grupo:
            if limite is not None and exibidos >= limite:
                break
            motivo = pendencia.get("motivo")
            linhas.append(f"- {pendencia['processo']}: {motivo}" if motivo else f"- {pendencia['processo']}")
            exibidos += 1
    if limite is not None and len(pendencias) > exibidos:
        linhas += ["", f"... e mais {len(pendencias) - exibidos} processo(s)."]
    return linhas
