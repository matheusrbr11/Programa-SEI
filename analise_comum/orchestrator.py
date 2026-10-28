"""Casos de uso das Etapas 1 e 2, e orquestração pública em lote, para qualquer
modalidade. O que muda entre o Crédito em Conta e o Depósito Judicial fica em
``analise_comum.modalidades``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, NoReturn, Optional
import time

from jupiter import SEI, Siafe

from .config import (
    ORGAO_SEI_PADRAO, UNIDADE_SEI, NIVEL_ACESSO_SEI, HIPOTESE_LEGAL, NOME_TITULO_GR,
    NOME_TITULO_COMPROVANTE_BB, BLOCO_ASSINATURA, SAIR_SIAFE, VERSAO_SEI,
    VERSAO_SIAFE_RIO_2, VERSAO_SIAFE_RIO_1,
)
from .core import (
    ErroProcesso, ErroExtracao, ErroValidacao, ErroDownload, ErroSEI,
    ErroLoginSEI, ErroLoginSiafe,
)
from .log import log
from .modalidades import Modalidade
from .repositorio import Repositorio
from .services import (
    mapear_estado_documentos, buscar_gr_no_banco, baixar_gr_no_siafe,
    baixar_gr_siafe_por_valor, abrir_sessao_siafe,
)
from .utils import (
    localizar_gr_em_disco, mensagem_curta, navegador_perdido,
    sessao_siafe_viva, sessao_sei_viva,
)

# despacho incluído sem formatação confirmada: fora das Etapas 1 e 2 até revisão manual
STATUS_REVISAR_DESPACHO = "revisar_despacho"

# versaoSiafe da jupiter -> nome exibido no pedido de senha
NOMES_SIAFE = {1: "SIAFE-Rio 2", 2: "SIAFE-Rio 2 (beta)", 3: "SIAFE-Rio 2 (homologação)", 4: "SIAFE-Rio 1"}


def nome_siafe(versao_siafe: int) -> str:
    return NOMES_SIAFE.get(versao_siafe, f"SIAFE (versão {versao_siafe})")

# (versao, nome, erro da tentativa anterior) -> (usuario, senha), ou None se o login foi cancelado
PedirCredenciaisSiafe = Callable[[int, str, Optional[str]], Optional[tuple[str, str]]]


def determinar_versao_siafe(ano: int) -> tuple[int, str]:
    """Retorna (versao_siafe, nome_amigavel) com base no ano e no ambiente."""
    if ano >= 2024:
        return VERSAO_SIAFE_RIO_2, "SIAFE-Rio 2"
    if 2016 <= ano <= 2023:
        return VERSAO_SIAFE_RIO_1, "SIAFE-Rio 1"
    raise ErroValidacao(f"Ano {ano} inválido. O ano deve ser 2016 ou superior.")


def _logar_erro_lote(processo: str, i: int, total: int, e: Exception, acao: str, sei=None) -> bool:
    """Loga o erro de um item do lote. Retorna True somente se o navegador de
    fato não responder mais (checagem via ``sessao_sei_viva``)."""
    msg = mensagem_curta(e)

    navegador_morto = navegador_perdido(e) and (sei is None or not sessao_sei_viva(sei))
    if navegador_morto:
        log.error(f"[{i}/{total}] {processo} navegador encerrado, interrompendo lote: {msg}", exc_info=True)
        return True

    if isinstance(e, ErroProcesso):
        log.warning(f"[{i}/{total}] {processo} erro na {acao}: {msg}", exc_info=True)
    else:
        log.error(f"[{i}/{total}] {processo} erro INESPERADO na {acao}: {msg}", exc_info=True)
    return False


class Fluxo:
    """Etapas 1 e 2 de uma modalidade, gravando na tabela ``processos`` só os
    registros dela."""

    def __init__(self, modalidade: Modalidade):
        self.modalidade = modalidade
        self.repo = Repositorio(modalidade.codigo)

    # -----------------------------------------------------------------------
    # Etapa 1 — Coleta de dados do processo
    # -----------------------------------------------------------------------
    def validar_dados_extraidos(self, dados: dict) -> None:
        """Validação de negócio dos dados extraídos."""
        for campo, mensagem in self.modalidade.campos_obrigatorios_extracao.items():
            if not dados.get(campo):
                raise ErroValidacao(mensagem)

    def _flags(self, estado: dict) -> dict:
        """Flags 'tem_*' do estado da árvore que a modalidade grava."""
        return {flag: int(estado[flag]) for flag in self.repo.flags if flag in estado}

    def _candidatos_pge(self, lista_nomes: list[str]) -> list[str]:
        return [n for n in lista_nomes if not any(tipo in n for tipo in self.modalidade.tipos_nao_pge)]

    def coletar_dados_processo(self, sei: SEI, processo: str) -> dict:
        """
        ETAPA 1 (sub-fase A) — Coleta os dados do processo a partir do SEI (e do
        BB, quando necessário para resolver a conta) e prepara os documentos da
        modalidade (no DJ, a planilha de Resgate, que define o valor da GR). Não
        acessa o SIAFE: quando a GR não é encontrada localmente (banco de
        contabilizacoes ou disco), o retorno vem com status "aguardando_gr" e os
        dados necessários para a consulta no SIAFE, que é feita depois, em lote,
        na sub-fase B (ver ``_baixar_gr_pendentes_em_lote``).
        """
        modalidade = self.modalidade
        log.info(f"[ETAPA 1] Coletando dados: {processo}")

        # 1. Mapear documentos
        estado = mapear_estado_documentos(sei, processo)

        if not estado["acessivel"]:
            log.info(f"Processo {processo} não está mais acessível na unidade. Ignorando.")
            return {"processo": processo, "status": "ignorado"}

        lista_nomes = estado["lista_nomes"]
        flags = self._flags(estado)

        if estado["tem_gr"] and estado["tem_despacho_apos_gr"]:
            log.info(f"Processo {processo} já está completo no SEI. Nada a coletar.")
            return {"processo": processo, "status": "concluido", **flags}

        # 2. Extrair dados dos anexos
        try:
            if estado["tem_comprovante"]:
                nome_comprovante = next(n for n in lista_nomes if NOME_TITULO_COMPROVANTE_BB in n)
                dados = modalidade.extrair_dados_comprovante_do_processo(sei, nome_comprovante)
                if not dados:
                    raise ErroExtracao("Não foi possível extrair dados do Comprovante já anexado.")

                # o comprovante não traz os dados do documento de origem (réu, título...)
                candidatos_pge = self._candidatos_pge(lista_nomes) if modalidade.campos_do_documento_origem else []
                if candidatos_pge:
                    dados_pge = modalidade.encontrar_dados_em_anexos(sei, candidatos_pge)
                    if dados_pge:
                        for campo in modalidade.campos_do_documento_origem:
                            dados.setdefault(campo, dados_pge.get(campo))
            else:
                candidatos = self._candidatos_pge(lista_nomes)
                if not candidatos:
                    raise ErroExtracao("Nenhum documento PGE encontrado no processo.")
                dados = modalidade.encontrar_dados_em_anexos(sei, candidatos)
                if not dados:
                    raise ErroExtracao("Informações necessárias não encontradas em nenhum anexo.")
        except (ErroExtracao, ErroProcesso):
            raise
        except Exception as e:
            raise ErroExtracao(f"Erro inesperado ao processar anexo: {e}")

        # 3. Validar dados extraídos
        self.validar_dados_extraidos(dados)

        # 4. Validar conta de processamento
        if dados["conta"] not in modalidade.contas_processar:
            return {"processo": processo, "status": "ignorado", "conta": dados["conta"], **flags}

        dados_coletados = {
            "conta": dados["conta"],
            "conta_judicial": dados.get("conta_judicial"),
            "processo_judicial": dados["processo_judicial"],
            "data_pagamento": dados["data_pagamento"],
            "ano": dados["ano"],
            "cnpj": dados.get("cnpj"),
            "data_alvara": dados.get("data_alvara"),
            "caminho_comprovante": dados.get("caminho_comprovante"),
            **{campo: dados.get(campo) for campo in modalidade.campos_extras_dados},
        }

        # 5. Documentos da modalidade (traz o valor da GR)
        dados_coletados.update(modalidade.preparar_documentos(dados))
        ano = dados_coletados["ano"]
        valor_pesquisa = dados_coletados["valor_pesquisa"]

        # 6. GR — só o que dá pra resolver sem o SIAFE (banco de contabilizacoes/disco)
        num_doc = None
        caminho_gr = None

        if not estado["tem_gr"]:
            determinar_versao_siafe(ano)

            if ano >= 2025:
                registro_gr = buscar_gr_no_banco(dados_coletados["processo_judicial"], valor_pesquisa, dados_coletados["data_pagamento"])
                if registro_gr:
                    num_doc = registro_gr["num_documento"]

            caminho_gr = localizar_gr_em_disco(num_documento=num_doc)

            if caminho_gr:
                log.info("GR ja disponivel na pasta.")
                if not num_doc:
                    num_doc = Path(caminho_gr).name.split(" - ")[0].strip()
            else:
                # Nao encontrada localmente: fica pendente para download em lote no SIAFE
                return {
                    "processo": processo, "status": "aguardando_gr",
                    **dados_coletados, "num_doc": num_doc, **flags,
                }

        # 7. Retorno
        return {
            "processo": processo, "status": "dados_coletados",
            **dados_coletados, "caminho_gr": caminho_gr, "num_doc": num_doc, **flags,
        }

    # -----------------------------------------------------------------------
    # Etapa 2 — Finalização do processo no SEI
    # -----------------------------------------------------------------------
    def _retirar_para_revisao_despacho(self, processo: str, erro: Exception) -> NoReturn:
        """O despacho já foi incluído no processo, mas a formatação não foi
        confirmada. Grava o status 'revisar_despacho', que tira o processo das
        Etapas 1 e 2: sem isso, a próxima execução veria o despacho na árvore e
        seguiria para o bloco de assinatura com o texto do modelo.

        Depois de revisar o despacho no SEI, conclua o processo manualmente ou
        volte o status para 'dados_coletados' para o programa finalizá-lo."""
        self.repo.upsert(processo=processo, status=STATUS_REVISAR_DESPACHO)
        raise ErroSEI(
            f"Despacho incluído, mas a formatação não foi confirmada ({mensagem_curta(erro)}). "
            f"Processo retirado do fluxo automático: revise o despacho no SEI."
        ) from erro

    def _anexar(self, sei: SEI, titulo: str, caminho: str) -> None:
        try:
            confirmado = sei.incluir_anexo(titulo, caminho, NIVEL_ACESSO_SEI, HIPOTESE_LEGAL)
        except Exception as e:
            raise ErroSEI(f"Erro ao anexar {titulo}: {e}")
        if not confirmado:
            raise ErroSEI(f"Anexo '{titulo}' não confirmado pelo SEI.")

    def finalizar_processo(
        self, sei: SEI, registro_db: dict, processos_no_marcador: set[str] | None = None,
        marcador_filtro: str | None = None,
    ) -> tuple[bool, bool]:
        """
        ETAPA 2 — Finaliza um processo no SEI:
          1. Anexa o Comprovante de Resgate (se necessário).
          2. Anexa os documentos gerados pela modalidade (DJ: Comprovante DJO).
          3. Anexa a GR.
          4. Inclui o despacho formatado.
          5. Adiciona ao bloco de assinatura.
          6. Altera o marcador para 'Concluido'.

        Retorna (finalizado, despacho_completo):
          - finalizado = False (sem finalizar) quando o processo não está mais
            acessível na unidade, OU quando ``processos_no_marcador`` é
            informado e o processo não consta mais nele (foi retirado do
            marcador de filtro entre a Etapa 1 e a Etapa 2); em ambos os casos
            já marca o status como 'ignorado'.
          - despacho_completo = False quando o despacho foi incluído (nesta
            execução ou em uma anterior) com algum dado obrigatório faltando
            no banco, exigindo complementação manual.
        """
        modalidade = self.modalidade
        marcador_filtro = marcador_filtro or modalidade.marcador_filtro
        processo = registro_db["processo"]
        log.info(f"[ETAPA 2] Respondendo: {processo}")

        if processos_no_marcador is not None and processo not in processos_no_marcador:
            log.info(f"Processo {processo} não está mais no marcador de filtro. Ignorando.")
            self.repo.upsert(processo=processo, status="ignorado")
            return False, True

        estado_inicial = mapear_estado_documentos(sei, processo)
        if not estado_inicial["acessivel"]:
            log.info(f"Processo {processo} não está mais acessível na unidade. Ignorando.")
            self.repo.upsert(processo=processo, status="ignorado")
            return False, True

        caminho_comprovante = registro_db.get("caminho_comprovante")
        caminho_gr = registro_db.get("caminho_gr")
        num_doc = registro_db.get("num_doc")
        despacho_completo = True

        # 1. Anexar Comprovante de Resgate
        if caminho_comprovante and not estado_inicial["tem_comprovante"]:
            conta_judicial = registro_db.get("conta_judicial")
            if conta_judicial and str(conta_judicial) in Path(caminho_comprovante).name:
                self._anexar(sei, NOME_TITULO_COMPROVANTE_BB, caminho_comprovante)
                self.repo.upsert(processo=processo, status="dados_coletados", tem_comprovante=1)
            else:
                raise ErroSEI(f"{NOME_TITULO_COMPROVANTE_BB} não encontrado ou nome incompatível.")

        # 2. Anexar documentos gerados pela modalidade
        for titulo, coluna_caminho, coluna_flag in modalidade.anexos_extras:
            caminho = registro_db.get(coluna_caminho)
            if caminho and not estado_inicial.get(coluna_flag):
                if not Path(caminho).exists():
                    raise ErroSEI(f"{titulo} não encontrado: {caminho}")
                self._anexar(sei, titulo, str(caminho))
                self.repo.upsert(processo=processo, status="dados_coletados", **{coluna_flag: 1})

        # 3. Anexar GR
        if caminho_gr and not estado_inicial["tem_gr"]:
            caminho_gr_path = Path(caminho_gr).resolve()
            if not caminho_gr_path.exists():
                raise ErroSEI(f"Arquivo da GR não encontrado na pasta: {caminho_gr_path}")

            nome_arquivo_gr = caminho_gr_path.name
            if num_doc and num_doc in nome_arquivo_gr:
                self._anexar(sei, NOME_TITULO_GR, str(caminho_gr_path))
                self.repo.upsert(processo=processo, status="dados_coletados", tem_gr=1)
            else:
                raise ErroSEI(
                    f"Número do documento '{num_doc}' não encontrado no nome do arquivo '{nome_arquivo_gr}'."
                )

        # 4. Incluir despacho
        if not estado_inicial["tem_despacho_apos_gr"]:
            despacho = modalidade.montar_despacho(registro_db, estado_inicial)  # valida antes de criar no SEI

            try:
                modalidade.antes_de_incluir_despacho(sei, despacho)
                if not sei.incluir_despacho(modalidade.despacho_padrao, NIVEL_ACESSO_SEI, HIPOTESE_LEGAL):
                    raise ErroSEI("Despacho não confirmado pelo SEI.")
            except ErroSEI:
                raise
            except Exception as e:
                raise ErroSEI(f"Erro ao inserir despacho: {e}")

            # o despacho já está no processo: daqui em diante, uma falha não pode deixá-lo seguir para assinatura
            try:
                despacho_completo = modalidade.formatar_despacho(sei, despacho)
            except Exception as e:
                self._retirar_para_revisao_despacho(processo, e)
            if not despacho_completo:
                log.warning(f"Processo {processo}: despacho incluído com dados incompletos.")
            self.repo.upsert(processo=processo, status="dados_coletados", tem_despacho_apos_gr=1)

        # 5. Bloco de assinatura
        try:
            incluido = sei.incluir_processo_bloco(BLOCO_ASSINATURA)
        except Exception as e:
            raise ErroSEI(f"Erro ao incluir processo no bloco de assinatura: {e}")
        if not incluido:
            raise ErroSEI(f"Bloco de assinatura '{BLOCO_ASSINATURA}' não encontrado no SEI.")

        # 6. Alterar marcador (adiciona o novo antes de remover o de filtro: nunca fica sem marcador)
        marcador_concluido = modalidade.marcador_concluido
        try:
            trocado = sei.trocar_marcador(processo, de=marcador_filtro, para=marcador_concluido)
        except Exception as e:
            raise ErroSEI(f"Erro ao alterar marcador para '{marcador_concluido}': {e}")
        if not trocado:
            raise ErroSEI(f"Troca de marcador para '{marcador_concluido}' não confirmada pelo SEI.")

        log.info(f"Processo {processo} concluido com sucesso.")
        return True, despacho_completo

    # -----------------------------------------------------------------------
    # Orquestração pública em lote
    # -----------------------------------------------------------------------
    def _persistir_resultado_coleta(self, processo: str, payload: dict, estatisticas: dict) -> None:
        """Grava resultado da coleta e atualiza estatísticas do lote."""
        status = payload.get("status")
        campos = {k: v for k, v in payload.items() if k in self.repo.nomes}
        campos.update({flag: payload.get(flag, 0) for flag in self.repo.flags})
        self.repo.upsert(processo=processo, status=status, **campos)

        if status == "ignorado":
            estatisticas["ignorados"] += 1
        elif status == "concluido":
            estatisticas["ja_prontos"] += 1
            log.info(f"{processo} já está completo no SEI.")
        elif status == "aguardando_gr":
            estatisticas["aguardando_gr"] += 1
            log.info(f"{processo} dados coletados; GR pendente.")
        else:
            estatisticas["coletados"] += 1
            log.info(f"{processo} dados coletados e salvos.")

    def _registrar_erro_coleta(self, processo: str, erro: Exception, estatisticas: dict) -> None:
        """Marca o processo como 'erro_coleta' e atualiza estatisticas do lote."""
        try:
            self.repo.upsert(processo=processo, status="erro_coleta")
        except Exception as e:
            log.error(f"{processo} erro ao gravar status de erro: {e}", exc_info=True)
        estatisticas["erros"] += 1
        estatisticas["erros_detalhe"].append({"processo": processo, "erro": str(erro)})

    # -----------------------------------------------------------------------
    # Etapa 1 (sub-fase B) — Download de GR em lote, agrupado por sessão SIAFE
    # -----------------------------------------------------------------------
    @staticmethod
    def _agrupar_pendentes_gr(pendentes: list[dict]) -> dict[tuple[int, str], list[dict]]:
        """Agrupa os registros 'aguardando_gr' por (versao_siafe, ano_doc)."""
        grupos: dict[tuple[int, str], list[dict]] = {}
        for reg in pendentes:
            ano = reg["ano"]
            versao_siafe, _ = determinar_versao_siafe(ano)
            num_doc = reg.get("num_doc")
            ano_doc = num_doc[:4] if num_doc else str(ano)

            grupos.setdefault((versao_siafe, ano_doc), []).append(reg)

        for chave, registros in grupos.items():
            grupos[chave] = sorted(registros, key=lambda r: r.get("num_doc") is None)
        return grupos

    @staticmethod
    def _logar_siafe(siafe: Siafe, versao_siafe: int, ano_doc: str, pedir_credenciais: PedirCredenciaisSiafe) -> bool:
        """Entra no SIAFE com as credenciais da versão, pedindo de novo enquanto
        o login for recusado. False se o usuário cancelar o pedido."""
        nome = nome_siafe(versao_siafe)
        erro = None
        while True:
            credenciais = pedir_credenciais(versao_siafe, nome, erro)
            if credenciais is None:
                return False
            try:
                abrir_sessao_siafe(siafe, versao_siafe, *credenciais, ano_doc)
                return True
            except ErroLoginSiafe:
                log.warning(f"[ETAPA 1] Login recusado no {nome}; pedindo usuário e senha de novo.")
                erro = "Login recusado pelo SIAFE. Confira usuário e senha."

    def _baixar_gr_pendentes_em_lote(self, pedir_credenciais: PedirCredenciaisSiafe, estatisticas: dict) -> dict | None:
        """
        Resolve todos os processos 'aguardando_gr' abrindo uma sessão do SIAFE
        por grupo (versao_siafe, ano_doc). As credenciais de cada versão são
        pedidas só aqui, quando há GR a buscar naquela versão.

        Retorna um dict de falha se o lote deve ser interrompido
        (login cancelado ou navegador perdido), ou None se terminou
        com sucesso ou erros pontuais por processo, que nao interrompem.
        """
        pendentes = self.repo.buscar_por_status("aguardando_gr")
        if not pendentes:
            return None

        grupos = self._agrupar_pendentes_gr(pendentes)
        log.info(f"[ETAPA 1] {len(pendentes)} GR(s) pendente(s) em {len(grupos)} sessão(oes) SIAFE")

        # pede as credenciais de cada versão antes de abrir o navegador, que ficaria
        # na frente da tela de login (uma senha recusada é pedida de novo depois, no login)
        for versao_siafe in dict.fromkeys(versao for versao, _ in grupos):
            if pedir_credenciais(versao_siafe, nome_siafe(versao_siafe), None) is None:
                log.warning(f"[ETAPA 1] Login no SIAFE cancelado; {estatisticas['aguardando_gr']} GR(s) continuam pendentes.")
                return {"sucesso": False, "motivo": "login_siafe_cancelado", "estatisticas": estatisticas}

        siafe = Siafe()
        total = estatisticas["total"]
        progresso_base = total - len(pendentes)
        try:
            siafe.abrir_driver(tempo_wait=20)

            for idx_grupo, ((versao_siafe, ano_doc), registros) in enumerate(grupos.items()):
                log.info(
                    f"[ETAPA 1] Sessão SIAFE versao={versao_siafe} ano={ano_doc} "
                    f"({len(registros)} GR(s))"
                )
                try:
                    if idx_grupo > 0:
                        siafe.clicar(SAIR_SIAFE)
                    if not self._logar_siafe(siafe, versao_siafe, ano_doc, pedir_credenciais):
                        log.warning(f"[ETAPA 1] Login no SIAFE cancelado; {estatisticas['aguardando_gr']} GR(s) continuam pendentes.")
                        return {"sucesso": False, "motivo": "login_siafe_cancelado", "estatisticas": estatisticas}
                except Exception as e:
                    log.error(f"[ETAPA 1] Erro ao abrir sessao SIAFE do grupo: {mensagem_curta(e)}", exc_info=True)
                    return {"sucesso": False, "motivo": "navegador_perdido", "estatisticas": estatisticas}

                for idx_reg, reg in enumerate(registros):
                    processo = reg["processo"]
                    num_doc_salvo = reg.get("num_doc")
                    primeira_consulta = idx_reg == 0
                    progresso_base += 1

                    if not sessao_siafe_viva(siafe):
                        log.error(f"[ETAPA 1] Navegador SIAFE perdido, interrompendo lote antes de {processo}.")
                        print(f"__PROGRESSO__:{progresso_base}:{total}", flush=True)
                        return {
                            "sucesso": False, "motivo": "navegador_perdido",
                            "estatisticas": estatisticas,
                        }

                    try:
                        if num_doc_salvo:
                            registro_gr = {"num_documento": num_doc_salvo, "valor": reg["valor_pesquisa"]}
                            caminho_gr = baixar_gr_no_siafe(
                                siafe, registro_gr, primeira_consulta=primeira_consulta,
                            )
                        else:
                            caminho_gr = baixar_gr_siafe_por_valor(
                                siafe, reg["valor_pesquisa"], versao_siafe,
                                data_pagamento=reg.get("data_pagamento"),
                                primeira_consulta=primeira_consulta,
                            )
                        num_doc = num_doc_salvo

                        if not caminho_gr:
                            raise ErroDownload(f"GR nao disponivel no SIAFE para {processo}.")

                        if not num_doc:
                            num_doc = Path(caminho_gr).name.split(" - ")[0].strip()

                        self.repo.upsert(
                            processo=processo, status="dados_coletados",
                            caminho_gr=caminho_gr, num_doc=num_doc,
                        )
                        estatisticas["coletados"] += 1
                        estatisticas["aguardando_gr"] -= 1
                        log.info(f"{processo} GR obtida no SIAFE.")

                    except Exception as e:
                        navegador_morto = navegador_perdido(e) or not sessao_siafe_viva(siafe)
                        _logar_erro_lote(processo, progresso_base, total, e, "download de GR")
                        if navegador_morto:
                            return {
                                "sucesso": False, "motivo": "navegador_perdido",
                                "estatisticas": estatisticas,
                            }

                        estatisticas["erros"] += 1
                        estatisticas["erros_detalhe"].append({"processo": processo, "erro": str(e)})
                    finally:
                        print(f"__PROGRESSO__:{progresso_base}:{total}", flush=True)

        except ErroLoginSiafe:
            return {"sucesso": False, "motivo": "falha_login_siafe", "estatisticas": estatisticas}
        finally:
            try:
                siafe.fechar_driver()
            except Exception as e:
                log.debug(f"[ETAPA 1] Driver SIAFE ja indisponivel ao fechar: {e}")

        return None

    def etapa1_coletar(
        self,
        sei_user: str,
        sei_pass: str,
        pedir_credenciais_siafe: PedirCredenciaisSiafe,
        orgao_sei: str = ORGAO_SEI_PADRAO,
        marcador_filtro: str | None = None,
    ) -> dict:
        """
        Orquestra a ETAPA 1 em lote, em duas sub-fases:
          A. Varre o SEI: mapeia documentos, extrai dados (consultando o BB
             quando necessario) e prepara os documentos da modalidade para cada
             processo do marcador. GRs não encontradas localmente ficam com
             status 'aguardando_gr'.
          B. Baixa as GRs pendentes em lote no SIAFE, pedindo as credenciais
             de cada versão do SIAFE só quando houver GR a buscar nela.
        """
        marcador_filtro = marcador_filtro or self.modalidade.marcador_filtro
        self.repo.inicializar()
        sei = SEI()
        estatisticas = {
            "total": 0, "coletados": 0, "erros": 0,
            "ignorados": 0, "ja_prontos": 0, "aguardando_gr": 0, "erros_detalhe": [],
        }

        try:
            sei.abrir_driver(tempo_wait=20)
            log.info("[ETAPA 1] Iniciando navegador")
            log.info("[ETAPA 1] Autenticando no SEI")
            if not sei.logar_sei(VERSAO_SEI, sei_user, sei_pass, orgao_sei, unidade=UNIDADE_SEI):
                raise ErroLoginSEI(f"Falha no login SEI ou na troca para a unidade {UNIDADE_SEI}.")

            processos_debug = self.repo.buscar_por_status("debug")
            em_modo_debug = bool(processos_debug)

            if em_modo_debug:
                lista_processos = [reg["processo"] for reg in processos_debug]
                log.info(
                    f"[ETAPA 1] Modo DEBUG ativo: processando apenas "
                    f"{len(lista_processos)} processo(s) marcado(s) como 'debug', "
                    f"ignorando o marcador de filtro."
                )
            else:
                log.info("[ETAPA 1] Coletando processos no marcador")
                processos = sei.visualizar_processos_por_marcador(marcador_filtro)
                lista_processos = [p["processo"] for p in processos] # a visualização já é só do marcador

            if not lista_processos:
                log.warning(f"Nenhum processo encontrado com o marcador '{marcador_filtro}'")
                return {"sucesso": True, "motivo": "vazio", "estatisticas": estatisticas}

            total = len(lista_processos)
            estatisticas["total"] = total
            log.info(f"{total} processo(s) mapeado(s) para coleta")

            for i, processo in enumerate(lista_processos, start=1):
                if not em_modo_debug:
                    outra_modalidade = self.repo.modalidade_em_conflito(processo)
                    if outra_modalidade:
                        log.warning(f"[{i}/{total}] {processo} já está registrado como '{outra_modalidade}'; confira o marcador. Pulando.")
                        estatisticas["ignorados"] += 1
                        print(f"__PROGRESSO__:{i}:{total}", flush=True)
                        continue
                    existente = self.repo.buscar_por_numero(processo)
                    status_existente = existente.get("status") if existente else None
                    if status_existente in ("dados_coletados", "concluido"):
                        log.info(f"[{i}/{total}] {processo} ja possui dados coletados/concluido. Pulando.")
                        estatisticas["ja_prontos"] += 1
                        print(f"__PROGRESSO__:{i}:{total}", flush=True)
                        continue
                    if status_existente == STATUS_REVISAR_DESPACHO:
                        log.warning(f"[{i}/{total}] {processo} aguardando revisão manual do despacho. Pulando.")
                        estatisticas["ignorados"] += 1
                        print(f"__PROGRESSO__:{i}:{total}", flush=True)
                        continue
                    if status_existente == "aguardando_gr":
                        log.info(f"[{i}/{total}] {processo} ja aguardando GR de execucao anterior. Pulando remapeamento.")
                        estatisticas["aguardando_gr"] += 1
                        print(f"__PROGRESSO__:{i}:{total}", flush=True)
                        continue

                navegador_com_perda = False
                try:
                    payload = self.coletar_dados_processo(sei, processo)
                    self._persistir_resultado_coleta(processo, payload, estatisticas)
                except Exception as e:
                    navegador_com_perda = _logar_erro_lote(processo, i, total, e, "coleta", sei=sei)
                    self._registrar_erro_coleta(processo, e, estatisticas)
                finally:
                    if em_modo_debug:
                        resultado_real = self.repo.buscar_por_numero(processo)
                        status_real = resultado_real.get("status") if resultado_real else "?"
                        log.info(f"[DEBUG] {processo}: resultado real da coleta = '{status_real}' (status no banco mantido como 'debug').")
                        self.repo.upsert(processo=processo, status="debug")
                    print(f"__PROGRESSO__:{i}:{total}", flush=True)

                if navegador_com_perda:
                    return {
                        "sucesso": False, "motivo": "navegador_perdido",
                        "estatisticas": estatisticas,
                    }

        except ErroLoginSEI:
            return {"sucesso": False, "motivo": "falha_login_sei", "estatisticas": estatisticas}
        except Exception as e:
            log.error(f"[ETAPA 1] Erro critico: {mensagem_curta(e)}", exc_info=True)
            return {"sucesso": False, "motivo": "erro_critico", "estatisticas": estatisticas, "erro": str(e)}
        finally:
            try:
                sei.fechar_driver()
            except Exception as e:
                log.debug(f"[ETAPA 1] Driver ja indisponivel ao fechar: {e}")
        falha_siafe = self._baixar_gr_pendentes_em_lote(pedir_credenciais_siafe, estatisticas)
        if falha_siafe:
            return falha_siafe

        return {"sucesso": True, "estatisticas": estatisticas}

    def etapa2_finalizar(
        self,
        sei_user: str,
        sei_pass: str,
        orgao_sei: str = ORGAO_SEI_PADRAO,
        marcador_filtro: str | None = None,
    ) -> dict:
        """
        Orquestra a ETAPA 2 em lote:
          1. Autentica no SEI.
          2. Busca todos os processos com status 'dados_coletados'.
          3. Para cada um, chama finalizar_processo() — pulando quem não
             estiver mais no marcador de filtro (pode ter sido retirado dele
             manualmente entre a Etapa 1 e a Etapa 2).
        """
        marcador_filtro = marcador_filtro or self.modalidade.marcador_filtro
        self.repo.inicializar()
        sei = SEI()
        estatisticas = {
            "total": 0, "concluidos": 0, "ignorados": 0, "erros": 0,
            "erros_detalhe": [], "despachos_parciais": [],
        }

        try:
            sei.abrir_driver(tempo_wait=20)
            log.info("[ETAPA 2] Iniciando navegador")
            log.info("[ETAPA 2] Autenticando no SEI")
            if not sei.logar_sei(VERSAO_SEI, sei_user, sei_pass, orgao_sei, unidade=UNIDADE_SEI):
                raise ErroLoginSEI(f"Falha no login SEI ou na troca para a unidade {UNIDADE_SEI}.")

            processos_debug = self.repo.buscar_por_status("debug")
            em_modo_debug = bool(processos_debug)

            if em_modo_debug:
                pendentes = processos_debug
                log.info(
                    f"[ETAPA 2] Modo DEBUG ativo: processando apenas "
                    f"{len(pendentes)} processo(s) marcado(s) como 'debug', "
                    f"ignorando status 'dados_coletados' e o marcador de filtro."
                )
            else:
                pendentes = self.repo.buscar_por_status("dados_coletados")

            total = len(pendentes)
            estatisticas["total"] = total

            if not pendentes:
                log.info("[ETAPA 2] Nenhum processo pendente de finalizacao.")
                return {"sucesso": True, "motivo": "vazio", "estatisticas": estatisticas}

            processos_no_marcador = None
            if not em_modo_debug:
                log.info("[ETAPA 2] Verificando marcador de filtro dos processos pendentes")
                processos_marcador = sei.visualizar_processos_por_marcador(marcador_filtro)
                processos_no_marcador = {p["processo"] for p in processos_marcador} # a visualização já é só do marcador

            log.info(f"[ETAPA 2] {total} processo(s) pendente(s) de finalizacao")

            for i, reg in enumerate(pendentes, start=1):
                processo = reg["processo"]
                navegador_com_perda = False
                inicio = time.monotonic()
                try:
                    finalizado, despacho_completo = self.finalizar_processo(sei, reg, processos_no_marcador, marcador_filtro)
                    if finalizado:
                        self.repo.upsert(
                            processo=processo, status="concluido",
                            usuario_resposta=sei_user,
                            data_hora_resposta=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            tempo_resposta=round(time.monotonic() - inicio, 2),
                        )
                        estatisticas["concluidos"] += 1
                        log.info(f"[{i}/{total}] {processo} finalizado com sucesso.")
                        if not despacho_completo:
                            estatisticas["despachos_parciais"].append(processo)
                            print(f"__DESPACHO_PARCIAL__:{processo}", flush=True)
                    else:
                        estatisticas["ignorados"] += 1
                        log.info(f"[{i}/{total}] {processo} ignorado (não acessível ou fora do marcador).")
                except Exception as e:
                    estatisticas["erros"] += 1
                    estatisticas["erros_detalhe"].append({"processo": processo, "erro": str(e)})
                    navegador_com_perda = _logar_erro_lote(processo, i, total, e, "finalizacao", sei=sei)
                finally:
                    if em_modo_debug:
                        resultado_real = self.repo.buscar_por_numero(processo)
                        status_real = resultado_real.get("status") if resultado_real else "?"
                        log.info(f"[DEBUG] {processo}: resultado real da finalizacao = '{status_real}' (status no banco mantido como 'debug').")
                        self.repo.upsert(processo=processo, status="debug")
                    print(f"__PROGRESSO__:{i}:{total}", flush=True)

                if navegador_com_perda:
                    return {
                        "sucesso": False, "motivo": "navegador_perdido",
                        "estatisticas": estatisticas,
                    }

        except ErroLoginSEI:
            return {"sucesso": False, "motivo": "falha_login_sei", "estatisticas": estatisticas}
        except Exception as e:
            log.error(f"[ETAPA 2] Erro critico: {mensagem_curta(e)}", exc_info=True)
            return {"sucesso": False, "motivo": "erro_critico", "estatisticas": estatisticas, "erro": str(e)}
        finally:
            try:
                sei.fechar_driver()
            except Exception as e:
                log.debug(f"[ETAPA 2] Driver ja indisponivel ao fechar: {e}")

        return {"sucesso": True, "estatisticas": estatisticas}
