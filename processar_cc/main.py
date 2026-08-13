"""Entry point — compatibilidade com chamada via stdin."""

from __future__ import annotations
import logging
import json
import sys

from .core import configurar_ambiente
from .orchestrator import etapa1_coletar, etapa2_finalizar

log = logging.getLogger("jupiter.processarCC")

_CODIGOS_SAIDA_FALHA = {
    "falha_login_sei": 2,
    "falha_login_siafe": 3,
    "navegador_perdido": 4,
    "erro_critico": 5,
}


def _codigo_saida_falha(resultado: dict) -> int:
    """Traduz o motivo de falha em código de saída. Motivos nao mapeados
    caem no 5 (erro critico)."""
    return _CODIGOS_SAIDA_FALHA.get(resultado.get("motivo"), 5)


def _sair_com_estatisticas(resultado: dict) -> None:
    """Encerra com 0 (sucesso) ou 1 (sucesso parcial) conforme as estatísticas."""
    erros = resultado["estatisticas"].get("erros", 0)
    if erros:
        log.warning(f"Processamento concluido com {erros} erro(s) em itens do lote.")
        sys.exit(1)
    sys.exit(0)


def main() -> None:
    """Lê JSON de credenciais via stdin e executa a etapa indicada em sys.argv[1].

    etapa1 — coleta de dados (precisa de sei_user/sei_pass/siafe_user/siafe_pass).
    etapa2 — finalização/resposta dos processos (precisa apenas de sei_user/sei_pass).
    """
    configurar_ambiente()

    etapa = sys.argv[1] if len(sys.argv) > 1 else "etapa1"

    try:
        dados_input = sys.stdin.read()
        credenciais = json.loads(dados_input)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Erro critico ao ler credenciais de entrada: {e}", exc_info=True)
        sys.exit(5)

    sei_user = credenciais.get("sei_user")
    sei_pass = credenciais.get("sei_pass")

    if etapa == "etapa1":
        siafe_user = credenciais.get("siafe_user")
        siafe_pass = credenciais.get("siafe_pass")
        if not all([sei_user, sei_pass, siafe_user, siafe_pass]):
            log.error("Credenciais incompletas recebidas via stdin.")
            sys.exit(5)

        r1 = etapa1_coletar(sei_user, sei_pass, siafe_user, siafe_pass)
        if not r1["sucesso"]:
            log.error("Erro ao processar processos SEI.")
            sys.exit(_codigo_saida_falha(r1))
        _sair_com_estatisticas(r1)

    elif etapa == "etapa2":
        if not all([sei_user, sei_pass]):
            log.error("Credenciais incompletas recebidas via stdin.")
            sys.exit(5)

        r2 = etapa2_finalizar(sei_user, sei_pass)
        if not r2["sucesso"]:
            log.error("Erro ao responder processos SEI.")
            sys.exit(_codigo_saida_falha(r2))
        _sair_com_estatisticas(r2)
        

if __name__ == "__main__":
    main()
