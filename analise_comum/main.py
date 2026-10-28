"""Entry point comum — credenciais trocadas com a interface via stdin/stdout.

A interface manda, na primeira linha do stdin, um JSON com sei_user/sei_pass.
A senha do SIAFE só é pedida quando o fluxo vai entrar nele, uma vez por versão
(SIAFE-Rio 2 e SIAFE-Rio 1 podem ter senhas diferentes): o subprocesso escreve
``__PEDIR_SIAFE__:{"versao", "nome", "erro"}`` no stdout e lê, na próxima linha
do stdin, ``{"siafe_user", "siafe_pass"}`` ou ``null`` (login cancelado)."""

from __future__ import annotations
from typing import Callable, Optional
import json
import sys

from .core import configurar_ambiente
from .log import log

_CODIGOS_SAIDA_FALHA = {
    "falha_login_sei": 2,
    "falha_login_siafe": 3,
    "login_siafe_cancelado": 1,  # GRs ficam pendentes para a próxima execução
    "navegador_perdido": 4,
    "erro_critico": 5,
}


def _codigo_saida_falha(resultado: dict) -> int:
    """Traduz o motivo de falha em código de saída. Motivos nao mapeados
    caem no 5 (erro critico)."""
    return _CODIGOS_SAIDA_FALHA.get(resultado.get("motivo"), 5)


def _sair_com_estatisticas(resultado: dict) -> None:
    """Encerra com 0 (sucesso) ou 1 (sucesso parcial) conforme as estatísticas."""
    estatisticas = resultado["estatisticas"]
    erros = estatisticas.get("erros", 0)
    if erros:
        log.warning(f"Processamento concluido com {erros} erro(s) em itens do lote.")
        sys.exit(1)

    despachos_parciais = estatisticas.get("despachos_parciais") or []
    if despachos_parciais:
        log.warning(
            f"Despacho incluido com dados incompletos em {len(despachos_parciais)} "
            f"processo(s): {', '.join(despachos_parciais)}."
        )

    sys.exit(0)


PREFIXO_PEDIR_SIAFE = "__PEDIR_SIAFE__:"

# (versao, nome para exibir, erro da tentativa anterior) -> (usuario, senha) ou None se cancelado
PedirCredenciaisSiafe = Callable[[int, str, Optional[str]], Optional[tuple[str, str]]]


def _pedir_credenciais_siafe_via_stdin() -> PedirCredenciaisSiafe:
    """Pede à interface as credenciais de cada versão do SIAFE, guardando-as
    pelo resto da execução; com ``erro`` (login recusado), pede de novo."""
    guardadas: dict[int, tuple[str, str]] = {}

    def pedir(versao: int, nome: str, erro: Optional[str] = None) -> Optional[tuple[str, str]]:
        if erro:
            guardadas.pop(versao, None)
        if versao not in guardadas:
            print(PREFIXO_PEDIR_SIAFE + json.dumps({"versao": versao, "nome": nome, "erro": erro}), flush=True)
            resposta = json.loads(sys.stdin.readline() or "null")
            if not resposta:
                return None
            guardadas[versao] = (resposta["siafe_user"], resposta["siafe_pass"])
        return guardadas[versao]

    return pedir


def executar(
    nome_logger: str,
    etapa1_coletar: Callable[[str, str, PedirCredenciaisSiafe], dict],
    etapa2_finalizar: Callable[[str, str], dict],
) -> None:
    """Lê as credenciais do SEI via stdin e executa a etapa do fluxo indicada em sys.argv[1].

    etapa1 — coleta de dados (SEI; o SIAFE é pedido à interface quando for usado).
    etapa2 — finalização/resposta dos processos (só SEI).
    """
    configurar_ambiente(nome_logger)

    etapa = sys.argv[1] if len(sys.argv) > 1 else "etapa1"

    try:
        credenciais = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Erro critico ao ler credenciais de entrada: {e}", exc_info=True)
        sys.exit(5)

    sei_user = credenciais.get("sei_user")
    sei_pass = credenciais.get("sei_pass")
    if not all([sei_user, sei_pass]):
        log.error("Credenciais incompletas recebidas via stdin.")
        sys.exit(5)

    if etapa == "etapa1":
        r1 = etapa1_coletar(sei_user, sei_pass, _pedir_credenciais_siafe_via_stdin())
        if not r1["sucesso"]:
            if r1.get("motivo") == "login_siafe_cancelado":
                log.warning("Login no SIAFE cancelado: as GRs pendentes ficam para a próxima execução.")
            else:
                log.error("Erro ao processar processos SEI.")
            sys.exit(_codigo_saida_falha(r1))
        _sair_com_estatisticas(r1)

    elif etapa == "etapa2":

        r2 = etapa2_finalizar(sei_user, sei_pass)
        if not r2["sucesso"]:
            log.error("Erro ao responder processos SEI.")
            sys.exit(_codigo_saida_falha(r2))
        _sair_com_estatisticas(r2)


def executar_modalidade(codigo: str) -> None:
    """Entry point do subprocesso de uma modalidade ('cc' ou 'dj')."""
    from .modalidades import obter_modalidade
    from .orchestrator import Fluxo

    modalidade = obter_modalidade(codigo)
    fluxo = Fluxo(modalidade)
    executar(modalidade.nome_logger, fluxo.etapa1_coletar, fluxo.etapa2_finalizar)
