"""Modalidades de processo: o que muda entre o Crédito em Conta e o Depósito
Judicial. O fluxo (``analise_comum.orchestrator``) é um só e chama a
modalidade nos pontos em que elas diferem.

Para uma modalidade nova: crie um módulo aqui com uma subclasse de
``Modalidade``, registre-a em ``MODALIDADES`` e inclua as colunas próprias em
``analise_comum.repositorio.COLUNAS_POR_MODALIDADE``."""

from __future__ import annotations

from importlib import import_module

from jupiter import SEI

from .. import services as comum


class Modalidade:
    """Configuração e pontos de variação de uma modalidade. Os métodos com
    implementação aqui são o comportamento padrão; as subclasses sobrescrevem
    o que for diferente."""

    codigo: str                 # coluna 'modalidade' da tabela processos
    nome: str                   # nome para logs e mensagens
    nome_logger: str            # logger do subprocesso (distingue os fluxos no log geral)

    marcador_filtro: str
    marcador_concluido: str
    despacho_padrao: str        # nome do texto padrão do despacho no SEI
    contas_processar: tuple[str, ...]
    tipos_nao_pge: tuple[str, ...]  # documentos da árvore que não são da PGE

    # campo extraído -> mensagem de erro, quando ausente
    campos_obrigatorios_extracao: dict[str, str]
    # campos dos dados extraídos, além dos comuns, que vão para o banco
    campos_extras_dados: tuple[str, ...] = ()
    # campos procurados nos demais anexos quando o documento principal não os traz
    campos_complementaveis: tuple[str, ...] = ()
    # campos do documento da PGE trazidos mesmo quando o comprovante já está anexado
    campos_do_documento_origem: tuple[str, ...] = ()
    # documentos gerados na coleta e anexados na Etapa 2: (título no SEI, coluna do caminho, coluna da flag)
    anexos_extras: tuple[tuple[str, str, str], ...] = ()

    # --- Etapa 1: extração ---------------------------------------------------
    def extrair_dados_documento(self, texto: str) -> dict | None:
        """Extrai os dados de um documento da PGE, na ordem de extratores da modalidade."""
        raise NotImplementedError

    def encontrar_dados_em_anexos(self, sei: SEI, candidatos: list[str]) -> dict | None:
        return comum.encontrar_dados_em_anexos(
            sei, candidatos, self.extrair_dados_documento, self.contas_processar, self.campos_complementaveis,
        )

    def extrair_dados_comprovante_do_processo(self, sei: SEI, nome_comprovante: str) -> dict | None:
        return comum.extrair_dados_comprovante_do_processo(sei, nome_comprovante, self.contas_processar)

    def consultar_conta_judicial(self, lista_dados: list[dict]) -> dict | None:
        return comum.consultar_conta_judicial(lista_dados, self.contas_processar)

    def preparar_documentos(self, dados: dict) -> dict:
        """Depois da extração e antes da GR: gera o que a modalidade precisar e
        devolve campos para o banco. Deve trazer 'valor_pesquisa' (o valor da GR)."""
        return {"valor_pesquisa": dados["valor_pesquisa"]}

    # --- Etapa 2: despacho ---------------------------------------------------
    def montar_despacho(self, registro_db: dict, estado: dict) -> dict:
        """Dados do despacho a partir do registro. Valida antes de o despacho
        ser criado no SEI (levanta ``ErroValidacao``)."""
        raise NotImplementedError

    def antes_de_incluir_despacho(self, sei: SEI, despacho: dict) -> None:
        """Leituras no SEI que precisam ser feitas antes de o despacho existir."""

    def formatar_despacho(self, sei: SEI, despacho: dict) -> bool:
        """Formata o despacho já incluído. Retorna False se faltou algum campo
        obrigatório; levanta ``ErroSEI`` se o SEI não confirmar a formatação."""
        raise NotImplementedError


MODALIDADES = {"cc": ".cc", "dj": ".dj"}  # código -> módulo com ``MODALIDADE``


def obter_modalidade(codigo: str) -> Modalidade:
    """Carrega só o módulo da modalidade pedida (o DJ depende de Excel e SharePoint)."""
    if codigo not in MODALIDADES:
        raise ValueError(f"Modalidade inválida: {codigo!r}")
    return import_module(MODALIDADES[codigo], __name__).MODALIDADE
