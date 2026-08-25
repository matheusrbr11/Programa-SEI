"""Casos de uso das Etapas 1 e 2, e orquestração pública em lote."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging
import time

from jupiter import SEI, Siafe

from .config import (
    ORGAO_SEI_PADRAO, MARCADOR_FILTRO, CONTA_PROCESSAR, TIPOS_NAO_PGE,
    NIVEL_ACESSO_SEI, HIPOTESE_LEGAL, DESPACHO_PADRAO, TITULO_DESPACHO,
    BLOCO_ASSINATURA, MARCADOR_CONCLUIDO,
)
from .core import (
    inicializar_tabela_processos, upsert_processo,
    buscar_processo_por_numero, buscar_processo_por_status,
    ErroProcesso, ErroExtracao, ErroValidacao, ErroDownload, ErroSEI,
    ErroLoginSEI, ErroLoginSiafe,
)
from .services import (
    mapear_estado_documentos, encontrar_dados_em_anexos, extrair_dados_comprovante_do_processo,
    buscar_gr_no_banco, baixar_gr_no_siafe, baixar_gr_siafe_por_valor,
    abrir_sessao_siafe, formatar_despacho_inserido,
)
from .utils import localizar_gr_em_disco, mensagem_curta, navegador_perdido, sessao_siafe_viva

log = logging.getLogger("jupiter.processarCC")


# ---------------------------------------------------------------------------
# Etapa 1 — Coleta de dados do processo
# ---------------------------------------------------------------------------
def validar_dados_extraidos(dados: dict) -> None:
    """Validação de negócio dos dados extraídos."""
    campos_obrigatorios = {
        "conta": "Campo 'Conta' nao encontrado.",
        "processo_judicial": "Campo 'Processo Judicial' nao encontrado.",
        "ano": "Campo 'Ano' nao encontrado.",
        "valor_pesquisa": "Campo 'Valor' nao encontrado.",
    }
    for campo, mensagem in campos_obrigatorios.items():
        if not dados.get(campo):
            raise ErroValidacao(mensagem)


def determinar_versao_siafe(ano: int) -> tuple[int, str]:
    """Retorna (versao_siafe, nome_amigavel) com base no ano."""
    if ano >= 2024:
        return 1, "SIAFE-Rio 2"
    if 2016 <= ano <= 2023:
        return 4, "SIAFE-Rio 1"
    raise ErroValidacao(f"Ano {ano} inválido. O ano deve ser 2016 ou superior.")


def coletar_dados_processo(sei: SEI, processo: str) -> dict:
    """
    ETAPA 1 (sub-fase A) — Coleta os dados do processo a partir do SEI (e do
    BB, quando necessário para resolver a conta). Não acessa o SIAFE: quando
    a GR não é encontrada localmente (banco de contabilizacoes ou disco), o
    retorno vem com status "aguardando_gr" e os dados necessários para a
    consulta no SIAFE, que é feita depois, em lote, na sub-fase B
    (ver ``_baixar_gr_pendentes_em_lote``).
    """
    log.info(f"[ETAPA 1] Coletando dados: {processo}")

    # 1. Mapear documentos
    estado = mapear_estado_documentos(sei, processo)
    lista_nomes = estado["lista_nomes"]

    if estado["tem_gr"] and estado["tem_despacho_apos_gr"]:
        log.info(f"Processo {processo} já está completo no SEI. Nada a coletar.")
        return {
            "processo": processo,
            "status": "concluido",
            "tem_gr": int(estado["tem_gr"]),
            "tem_comprovante": int(estado["tem_comprovante"]),
            "tem_despacho_apos_gr": int(estado["tem_despacho_apos_gr"]),
        }

    # 2. Extrair dados dos anexos
    try:
        if estado["tem_comprovante"]:
            nome_comprovante = next(n for n in lista_nomes if "Comprovante de Resgate" in n)
            dados = extrair_dados_comprovante_do_processo(sei, nome_comprovante)
            if not dados:
                raise ErroExtracao("Não foi possível extrair dados do Comprovante já anexado.")
        else:
            candidatos = [n for n in lista_nomes if not any(tipo in n for tipo in TIPOS_NAO_PGE)]
            if not candidatos:
                raise ErroExtracao("Nenhum documento PGE encontrado no processo.")
            dados = encontrar_dados_em_anexos(sei, candidatos)
            if not dados:
                raise ErroExtracao("Informações necessárias não encontradas em nenhum anexo.")
    except (ErroExtracao, ErroProcesso):
        raise
    except Exception as e:
        raise ErroExtracao(f"Erro inesperado ao processar anexo: {e}")

    # 3. Validar dados extraídos
    validar_dados_extraidos(dados)

    conta = dados["conta"]
    conta_judicial = dados.get("conta_judicial")
    processo_judicial = dados["processo_judicial"]
    data_pagamento = dados["data_pagamento"]
    ano = dados["ano"]
    valor_pesquisa = dados["valor_pesquisa"]

    # 4. Validar conta de processamento
    if conta != CONTA_PROCESSAR:
        return {
            "processo": processo,
            "status": "ignorado",
            "conta": conta,
            "tem_gr": int(estado["tem_gr"]),
            "tem_comprovante": int(estado["tem_comprovante"]),
            "tem_despacho_apos_gr": int(estado["tem_despacho_apos_gr"]),
        }

    # 5. Comprovante
    caminho_comprovante = dados.get("caminho_comprovante")

    # 6. GR — só o que dá pra resolver sem o SIAFE (banco de contabilizacoes/disco)
    registro_gr = None
    num_doc = None
    caminho_gr = None

    if not estado["tem_gr"]:
        determinar_versao_siafe(ano)

        if ano >= 2025:
            registro_gr = buscar_gr_no_banco(processo_judicial, valor_pesquisa, data_pagamento)
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
                "processo": processo,
                "status": "aguardando_gr",
                "conta": conta,
                "conta_judicial": conta_judicial,
                "processo_judicial": processo_judicial,
                "data_pagamento": data_pagamento,
                "ano": ano,
                "valor_pesquisa": valor_pesquisa,
                "cnpj": dados.get("cnpj"),
                "data_alvara": dados.get("data_alvara"),
                "caminho_comprovante": caminho_comprovante,
                "num_doc": num_doc,
                "tem_gr": int(estado["tem_gr"]),
                "tem_comprovante": int(estado["tem_comprovante"]),
                "tem_despacho_apos_gr": int(estado["tem_despacho_apos_gr"]),
            }

    # 7. Retorno
    return {
        "processo": processo,
        "status": "dados_coletados",
        "conta": conta,
        "conta_judicial": conta_judicial,
        "processo_judicial": processo_judicial,
        "data_pagamento": data_pagamento,
        "ano": ano,
        "valor_pesquisa": valor_pesquisa,
        "cnpj": dados.get("cnpj"),
        "data_alvara": dados.get("data_alvara"),
        "caminho_comprovante": caminho_comprovante,
        "caminho_gr": caminho_gr,
        "num_doc": num_doc,
        "tem_gr": int(estado["tem_gr"]),
        "tem_comprovante": int(estado["tem_comprovante"]),
        "tem_despacho_apos_gr": int(estado["tem_despacho_apos_gr"]),
    }


# ---------------------------------------------------------------------------
# Etapa 2 — Finalização do processo no SEI
# ---------------------------------------------------------------------------
def finalizar_processo(sei: SEI, registro_db: dict) -> None:
    """
    ETAPA 2 — Finaliza um processo no SEI:
      1. Anexa o comprovante de Resgate (se necessário).
      2. Anexa a GR.
      3. Inclui o despacho formatado.
      4. Adiciona ao bloco de assinatura.
      5. Altera o marcador para 'Concluido'.
    """
    processo = registro_db["processo"]
    log.info(f"[ETAPA 2] Respondendo: {processo}")
    sei.pesquisar_processo(processo)

    tem_gr = bool(registro_db.get("tem_gr", 0))
    tem_comprovante = bool(registro_db.get("tem_comprovante", 0))
    tem_despacho_apos_gr = bool(registro_db.get("tem_despacho_apos_gr", 0))

    caminho_comprovante = registro_db.get("caminho_comprovante")
    caminho_gr = registro_db.get("caminho_gr")
    num_doc = registro_db.get("num_doc")
    valor_pesquisa = registro_db.get("valor_pesquisa")
    data_pagamento = registro_db.get("data_pagamento")

    # 1. Anexar comprovante de Resgate
    if caminho_comprovante and not tem_comprovante:
        conta_judicial = registro_db.get("conta_judicial")
        if conta_judicial and str(conta_judicial) in Path(caminho_comprovante).name:
            try:
                confirmado = sei.incluir_anexo(
                    "Comprovante de Resgate", caminho_comprovante,
                    NIVEL_ACESSO_SEI, HIPOTESE_LEGAL,
                )
            except Exception as e:
                raise ErroSEI(f"Erro ao anexar comprovante: {e}")
            if not confirmado:
                raise ErroSEI("Comprovante não confirmado pelo SEI.")
            upsert_processo(processo=processo, status="dados_coletados", tem_comprovante=1)
        else:
            raise ErroSEI("Comprovante não encontrado ou nome incompatível.")

    # 2. Anexar GR
    if caminho_gr and not tem_gr:
        caminho_gr_path = Path(caminho_gr).resolve()
        if not caminho_gr_path.exists():
            raise ErroSEI(f"Arquivo da GR não encontrado na pasta: {caminho_gr_path}")

        nome_arquivo_gr = caminho_gr_path.name
        if num_doc and num_doc in nome_arquivo_gr:
            try:
                confirmado = sei.incluir_anexo(
                    "Guia de Recolhimento", str(caminho_gr_path),
                    NIVEL_ACESSO_SEI, HIPOTESE_LEGAL,
                )
            except Exception as e:
                raise ErroSEI(f"Erro ao anexar GR: {e}")
            if not confirmado:
                raise ErroSEI("GR não confirmada pelo SEI.")
            upsert_processo(processo=processo, status="dados_coletados", tem_gr=1)
        else:
            raise ErroSEI(
                f"Número do documento '{num_doc}' não encontrado no nome do arquivo '{nome_arquivo_gr}'."
            )

    # 3. Incluir despacho
    if not tem_despacho_apos_gr:
        try:
            index_doc = sei.copiar_informacoes_documento("Guia de Recolhimento")
            if not sei.incluir_despacho(DESPACHO_PADRAO, NIVEL_ACESSO_SEI, HIPOTESE_LEGAL):
                raise ErroSEI("Despacho não confirmado pelo SEI.")

            registro_despacho = {
                "num_documento": num_doc or "—",
                "valor": valor_pesquisa,
                "data": data_pagamento or "",
            }
            formatar_despacho_inserido(sei, registro_despacho, TITULO_DESPACHO, index_doc)
        except (ErroSEI, ErroValidacao):
            raise
        except Exception as e:
            raise ErroSEI(f"Erro ao formatar/inserir despacho: {e}")
        upsert_processo(processo=processo, status="dados_coletados", tem_despacho_apos_gr=1)

    # 4. Bloco de assinatura
    try:
        sei.incluir_processo_bloco(BLOCO_ASSINATURA)
    except Exception as e:
        raise ErroSEI(f"Erro ao incluir processo no bloco de assinatura: {e}")

    # 5. Alterar marcador
    try:
        sei.remover_marcador(processo)
        sei.adicionar_marcador(MARCADOR_CONCLUIDO, processo, flag_removido=True)
    except Exception as e:
        raise ErroSEI(f"Erro ao alterar marcador para '{MARCADOR_CONCLUIDO}': {e}")

    log.info(f"Processo {processo} concluido com sucesso.")


# ---------------------------------------------------------------------------
# Orquestração pública em lote
# ---------------------------------------------------------------------------
def _logar_erro_lote(processo: str, i: int, total: int, e: Exception, acao: str) -> bool:
    """Loga o erro de um item do lote (aviso para ErroProcesso, erro para
    exceções inesperadas). Retorna True se o erro indicar que o
    navegador/sessão morreu."""
    msg = mensagem_curta(e)

    if navegador_perdido(e):
        log.error(f"[{i}/{total}] {processo} navegador encerrado, interrompendo lote: {msg}", exc_info=True)
        return True

    if isinstance(e, ErroProcesso):
        log.warning(f"[{i}/{total}] {processo} erro na {acao}: {msg}", exc_info=True)
    else:
        log.error(f"[{i}/{total}] {processo} erro INESPERADO na {acao}: {msg}", exc_info=True)
    return False


def _persistir_resultado_coleta(processo: str, payload: dict, estatisticas: dict) -> None:
    """Grava resultado da coleta e atualiza estatísticas do lote."""
    status = payload.get("status")

    if status == "ignorado":
        upsert_processo(
            processo=processo, status="ignorado", conta=payload.get("conta"),
            tem_gr=payload.get("tem_gr", 0), tem_comprovante=payload.get("tem_comprovante", 0),
            tem_despacho_apos_gr=payload.get("tem_despacho_apos_gr", 0),
        )
        estatisticas["ignorados"] += 1
        return

    if status == "concluido":
        upsert_processo(
            processo=processo, status="concluido",
            tem_gr=payload.get("tem_gr", 0), tem_comprovante=payload.get("tem_comprovante", 0),
            tem_despacho_apos_gr=payload.get("tem_despacho_apos_gr", 0),
        )
        estatisticas["ja_prontos"] += 1
        log.info(f"{processo} já está completo no SEI.")
        return

    upsert_processo(
        processo=processo, status=status,
        conta=payload.get("conta"),
        conta_judicial=payload.get("conta_judicial"),
        processo_judicial=payload.get("processo_judicial"),
        data_pagamento=payload.get("data_pagamento"),
        ano=payload.get("ano"),
        valor_pesquisa=payload.get("valor_pesquisa"),
        cnpj=payload.get("cnpj"),
        data_alvara=payload.get("data_alvara"),
        caminho_comprovante=payload.get("caminho_comprovante"),
        caminho_gr=payload.get("caminho_gr"),
        num_doc=payload.get("num_doc"),
        tem_gr=payload.get("tem_gr", 0),
        tem_comprovante=payload.get("tem_comprovante", 0),
        tem_despacho_apos_gr=payload.get("tem_despacho_apos_gr", 0),
    )
    if status == "aguardando_gr":
        estatisticas["aguardando_gr"] += 1
        log.info(f"{processo} dados coletados; GR pendente.")
    else:
        estatisticas["coletados"] += 1
        log.info(f"{processo} dados coletados e salvos.")


def _registrar_erro_coleta(processo: str, erro: Exception, estatisticas: dict) -> None:
    """Marca o processo como 'erro_coleta' e atualiza estatisticas do lote."""
    try:
        upsert_processo(processo=processo, status="erro_coleta")
    except Exception as e:
        log.error(f"{processo} erro ao gravar status de erro: {e}", exc_info=True)
    estatisticas["erros"] += 1
    estatisticas["erros_detalhe"].append({"processo": processo, "erro": str(erro)})


# ---------------------------------------------------------------------------
# Etapa 1 (sub-fase B) — Download de GR em lote, agrupado por sessão SIAFE
# ---------------------------------------------------------------------------
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


def _baixar_gr_pendentes_em_lote(siafe_usuario: str, siafe_senha: str, estatisticas: dict) -> dict | None:
    """
    Resolve todos os processos 'aguardando_gr' abrindo uma sessão do SIAFE
    por grupo (versao_siafe, ano_doc).

    Retorna um dict de falha se o lote deve ser interrompido 
    (falha de login ou navegador perdido), ou None se terminou 
    com sucesso ou erros pontuais por processo, que nao interrompem.
    """
    pendentes = buscar_processo_por_status("aguardando_gr")
    if not pendentes:
        return None

    grupos = _agrupar_pendentes_gr(pendentes)
    log.info(f"[ETAPA 1] {len(pendentes)} GR(s) pendente(s) em {len(grupos)} sessão(oes) SIAFE")

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
                abrir_sessao_siafe(siafe, versao_siafe, siafe_usuario, siafe_senha, ano_doc)
                
            except ErroLoginSiafe:
                log.error("[ETAPA 1] Falha de login no SIAFE, interrompendo lote.")
                return {"sucesso": False, "motivo": "falha_login_siafe", "estatisticas": estatisticas}
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

                    upsert_processo(
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
    sei_user: str,
    sei_pass: str,
    siafe_user: str,
    siafe_pass: str,
    orgao_sei: str = ORGAO_SEI_PADRAO,
    marcador_filtro: str = MARCADOR_FILTRO,
) -> dict:
    """
    Orquestra a ETAPA 1 em lote, em duas sub-fases:
      A. Varre o SEI: mapeia documentos, extrai dados (consultando o BB
         quando necessario) para cada processo do marcador. GRs nao
         encontradas localmente ficam com status 'aguardando_gr'.
      B. Baixa as GRs pendente em lote no SIAFE.
    """
    inicializar_tabela_processos()
    sei = SEI()
    estatisticas = {
        "total": 0, "coletados": 0, "erros": 0,
        "ignorados": 0, "ja_prontos": 0, "aguardando_gr": 0, "erros_detalhe": [],
    }

    try:
        sei.abrir_driver(tempo_wait=20)
        log.info("[ETAPA 1] Iniciando navegador")
        log.info("[ETAPA 1] Autenticando no SEI")
        if not sei.logar_sei(sei_user, sei_pass, orgao_sei):
            raise ErroLoginSEI("Falha no login SEI.")

        log.info("[ETAPA 1] Coletando processos no marcador")
        processos = sei.visualizar_processos_por_marcador(marcador_filtro)
        lista_processos = sei.filtrar_processos_por_marcador(processos, marcador_filtro)

        if not lista_processos:
            log.warning(f"Nenhum processo encontrado com o marcador '{marcador_filtro}'")
            return {"sucesso": True, "motivo": "vazio", "estatisticas": estatisticas}

        total = len(lista_processos)
        estatisticas["total"] = total
        log.info(f"{total} processo(s) mapeado(s) para coleta")

        for i, processo in enumerate(lista_processos, start=1):
            existente = buscar_processo_por_numero(processo)
            status_existente = existente.get("status") if existente else None
            if status_existente in ("dados_coletados", "concluido"):
                log.info(f"[{i}/{total}] {processo} ja possui dados coletados/concluido. Pulando.")
                estatisticas["ja_prontos"] += 1
                print(f"__PROGRESSO__:{i}:{total}", flush=True)
                continue
            if status_existente == "aguardando_gr":
                log.info(f"[{i}/{total}] {processo} ja aguardando GR de execucao anterior. Pulando remapeamento.")
                estatisticas["aguardando_gr"] += 1
                print(f"__PROGRESSO__:{i}:{total}", flush=True)
                continue

            navegador_perdido = False
            try:
                payload = coletar_dados_processo(sei, processo)
                _persistir_resultado_coleta(processo, payload, estatisticas)
            except Exception as e:
                navegador_perdido = _logar_erro_lote(processo, i, total, e, "coleta")
                _registrar_erro_coleta(processo, e, estatisticas)
            finally:
                print(f"__PROGRESSO__:{i}:{total}", flush=True)

            if navegador_perdido:
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
    falha_siafe = _baixar_gr_pendentes_em_lote(siafe_user, siafe_pass, estatisticas)
    if falha_siafe:
        return falha_siafe

    return {"sucesso": True, "estatisticas": estatisticas}


def etapa2_finalizar(
    sei_user: str,
    sei_pass: str,
    orgao_sei: str = ORGAO_SEI_PADRAO,
) -> dict:
    """
    Orquestra a ETAPA 2 em lote:
      1. Autentica no SEI.
      2. Busca todos os processos com status 'dados_coletados'.
      3. Para cada um, chama finalizar_processo().
    """
    inicializar_tabela_processos()
    sei = SEI()
    estatisticas = {"total": 0, "concluidos": 0, "erros": 0, "erros_detalhe": []}

    try:
        sei.abrir_driver(tempo_wait=20)
        log.info("[ETAPA 2] Iniciando navegador")
        log.info("[ETAPA 2] Autenticando no SEI")
        if not sei.logar_sei(sei_user, sei_pass, orgao_sei):
            raise ErroLoginSEI("Falha no login SEI.")

        pendentes = buscar_processo_por_status("dados_coletados")
        total = len(pendentes)
        estatisticas["total"] = total

        if not pendentes:
            log.info("[ETAPA 2] Nenhum processo pendente de finalizacao.")
            return {"sucesso": True, "motivo": "vazio", "estatisticas": estatisticas}

        log.info(f"[ETAPA 2] {total} processo(s) pendente(s) de finalizacao")

        for i, reg in enumerate(pendentes, start=1):
            processo = reg["processo"]
            navegador_perdido = False
            inicio = time.monotonic()
            try:
                finalizar_processo(sei, reg)
                upsert_processo(
                    processo=processo, status="concluido",
                    usuario_resposta=sei_user,
                    data_hora_resposta=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    tempo_resposta=round(time.monotonic() - inicio, 2),
                )
                estatisticas["concluidos"] += 1
                log.info(f"[{i}/{total}] {processo} finalizado com sucesso.")
            except Exception as e:
                estatisticas["erros"] += 1
                estatisticas["erros_detalhe"].append({"processo": processo, "erro": str(e)})
                navegador_perdido = _logar_erro_lote(processo, i, total, e, "finalizacao")
            finally:
                print(f"__PROGRESSO__:{i}:{total}", flush=True)

            if navegador_perdido:
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
