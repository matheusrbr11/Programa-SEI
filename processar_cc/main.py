"""Entry point do Crédito em Conta — compatibilidade com chamada via stdin."""

from analise_comum.main import executar

from .orchestrator import etapa1_coletar, etapa2_finalizar


def main() -> None:
    executar("jupiter.processarCC", etapa1_coletar, etapa2_finalizar)


if __name__ == "__main__":
    main()
