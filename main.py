from selenium.common.exceptions import NoSuchElementException, SessionNotCreatedException, InvalidSessionIdException
from pathlib import Path
import pandas as pd
import subprocess
import threading
import traceback
import logging
import base64
import queue
import json
import sys
import os

from eop_ui import AppConfig, BaseApp
from jupiter import configurar_log, Siafe, GraphAPI
from ambiente import PASTA_LOG_GERAL

logger = logging.getLogger("jupiter.SEI")

# módulos do menu principal: código -> (nome exibido, script do subprocesso)
MODULOS = {
    "cc": ("Crédito em Conta", "run_processar_cc.py"),
    "dj": ("Depósito Judicial", "run_processar_dj.py"),
}

# linhas que o subprocesso escreve no stdout para a interface (o resto é log)
PREFIXO_BARRA = "__PROGRESSO__:"
PREFIXO_DETALHE_ERRO = "__ERRO_DETALHE__:"
PREFIXO_DESPACHO_PARCIAL = "__DESPACHO_PARCIAL__:"
PREFIXO_PEDIR_SIAFE = "__PEDIR_SIAFE__:"  # o subprocesso vai entrar no SIAFE e precisa das credenciais

# campos do cfg trocados pela tela de login do siafe e restaurados depois
CAMPOS_CFG_LOGIN = ("login_subtitle", "login_user_label", "user_digits_only", "user_exact_length", "user_max_length")


class ColetorErros(logging.Handler):
    """Acumula os registros de ERRO da sessão (do app e da biblioteca) para o resumo enviado ao fechar."""

    def __init__(self, destino: list, level=logging.ERROR):
        super().__init__(level=level)
        self._destino = destino  # lista onde os erros vão sendo guardados

    def emit(self, record):
        # chamado pelo logging a cada erro; guarda os dados num dict pra montar o resumo depois
        try:
            # se tiver exceção, formata o traceback completo em texto
            tb = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else ""
            self._destino.append({
                "timestamp": pd.Timestamp.fromtimestamp(record.created).strftime("%d/%m/%Y %H:%M:%S"),
                "origem": record.name,
                "mensagem": record.getMessage(),
                "traceback": tb,
            })
        except Exception:
            self.handleError(record)


class SeiApp(BaseApp):


### CONFIGURAÇÕES DO PROGRAMA

    def __init__(self):
        cfg = AppConfig(
            app_name="Programa SEI",
            app_version="1.6.0",
            login_subtitle="⚠️ Faça Login com os dados do SEI. ⚠️",
            login_user_label="Usuário do SEI:",
            image_filename="img/SEI.png",
            logo_size=(360, 125),
        )
        super().__init__(cfg)

        self.siafeVersao = 1  # 1 = Prod | 2 = Beta | 3 = Homologação

        self.siafe = Siafe()             # controla o navegador/sessão do siafe (login da etapa 1)
        self.stop_event = False          # vira True quando o usuário cancela a rotina
        self.retorno_automatico = True   # se True, volta sozinho pra tela anterior ao terminar
        self.operacao_em_andamento = False  # True enquanto a thread do subprocesso (CC ou DJ) roda

        self.sei_usuario = ""            # credenciais do sei, capturadas no login inicial
        self.sei_senha = ""

        # credenciais do siafe por versão (SIAFE-Rio 2 e SIAFE-Rio 1 podem ter senhas diferentes),
        # pedidas só quando a etapa 1 vai entrar no siafe e guardadas até fechar o programa
        self.credenciais_siafe: dict[int, tuple[str, str]] = {}
        self._ultimo_cpf_siafe = ""      # preenche o usuário no próximo pedido de senha
        self._fila_siafe = None          # resposta da tela de login do siafe em aberto
        self._codigo_em_execucao = None  # módulo da operação em andamento ("cc" ou "dj")

        self.graph = None                # GraphAPI; definido em __main__ após configurar_log
        self.dev_emails = []             # destinatários do e-mail de erro
        self.teams_webhook = None        # webhook do Teams (opcional)
        self.erros_acumulados = []       # falhas críticas da sessão; resumo enviado ao fechar

        # já abre direto na tela de login do SEI; ao logar, vai pro menu principal
        self.show_sei_login_frame()

    def _enviar_resumo_erros(self):
        """Envia, uma única vez ao fechar, um resumo dos erros da sessão por e-mail e/ou Teams."""
        # sem graph configurado ou sem erros, não há o que enviar
        if not self.graph or not self.erros_acumulados:
            return

        total = len(self.erros_acumulados)
        # monta o corpo da mensagem linha a linha
        linhas = [
            "Resumo de erros — Programa SEI",
            f"Usuário: {os.getlogin()}",
            f"Total de erros na sessão: {total}",
        ]
        for i, err in enumerate(self.erros_acumulados, 1):
            linhas.append("")
            linhas.append(f"[{i}] {err['timestamp']} — {err['origem']}")
            linhas.append(err["mensagem"])
            if err["traceback"]:
                linhas.append(err["traceback"].strip())
        mensagem = "\n".join(linhas)
        titulo = f"[SEI] {total} erro(s) na execução"

        try:
            # manda por e-mail e/ou teams, conforme o que estiver configurado
            if self.dev_emails:
                self.graph.enviar_email(titulo=titulo, mensagem=mensagem, destinatarios=self.dev_emails)
            if self.teams_webhook:
                self.graph.enviar_mensagem(self.teams_webhook, f"{titulo}\n\n{mensagem}", self.dev_emails)
        except Exception:
            # se erro no envio, não deixa isso derrubar o fechamento do app
            logger.error("Erro ao enviar o resumo de erros aos desenvolvedores", exc_info=True)

    def report_callback_exception(self, exc, val, tb):
        """Erros não tratados na interface (Tkinter) — logados e capturados para o resumo."""
        logger.error("Erro não tratado na interface", exc_info=(exc, val, tb))


# EVENTOS DE NAVEGAÇÃO

    def cancelar_e_voltar(self, tela_retorno_fn):

        # botão de cancelar: sinaliza parada e volta pra tela indicada
        self.stop_event = True
        self.retorno_automatico = False  # a navegação já é feita aqui; a thread não deve redirecionar depois
        self._liberar_pedido_siafe()
        try:
            if self.siafe.driver:
                self.siafe.fechar_driver()
        except Exception:
            pass
        self.after(0, tela_retorno_fn)

    def limpar_recursos(self):

        # chamado ao fechar o app: garante que o navegador não fique aberto
        self.stop_event = True
        self._liberar_pedido_siafe()
        try:
            if hasattr(self, 'siafe') and self.siafe.driver:
                self.siafe.fechar_driver()
        except Exception:
            pass


# TELAS VISUAIS

    def processar_login_sei(self, usuario_digitado, senha_digitada):

        # guarda o login do sei e segue pro menu principal
        self.sei_usuario = usuario_digitado
        self.sei_senha = senha_digitada
        self.show_main_frame()

    def show_sei_login_frame(self):

        # wrapper do componente generico de login (eop_ui.BaseApp) pro SEI
        self.show_login_frame(on_success=self.processar_login_sei)

    def show_main_frame(self):

        # menu principal: um botão pra cada módulo do programa
        self.clear_frame()
        self.create_menu()
        self.add_back_button(self.show_sei_login_frame)
        self._add_logo()
        self.make_header_label("Programa SEI", pady=(20, 5))
        self.make_subtitle_label("Menu Principal", pady=(0, 20))

        frame_modulos = self.make_section_frame()
        for codigo, (nome, _) in MODULOS.items():
            self.make_primary_button(
                frame_modulos, text=nome, command=lambda c=codigo: self.show_modulo_frame(c)
            ).pack(pady=10)
        self._add_footer()

    def show_modulo_frame(self, codigo):

        # tela do módulo: as duas etapas do fluxo, cada uma com seu botão.
        # o login do siafe não é pedido aqui: só quando a etapa 1 for de fato entrar no siafe
        nome, _ = MODULOS[codigo]
        self.clear_frame()
        self.create_menu()
        self.add_back_button(self.show_main_frame)
        self._add_logo()
        self.make_header_label(nome, pady=(20, 5))
        self.make_subtitle_label("Instrução de Processos", pady=(0, 20))

        frame_etapas = self.make_section_frame()
        self.make_primary_button(
            frame_etapas, text="PROCESSAR PROCESSOS",
            command=lambda: self.iniciar_operacao_thread(codigo, "etapa1")
        ).pack(pady=10)
        self.make_primary_button(
            frame_etapas, text="RESPONDER PROCESSOS",
            command=lambda: self.iniciar_operacao_thread(codigo, "etapa2")
        ).pack(pady=10)
        self._add_footer()


### LOGIN DO SIAFE (pedido pelo subprocesso durante a etapa 1)

    def _credenciais_siafe_para(self, pedido):

        # chamado na thread de execução quando o subprocesso pede o login de uma versão do siafe.
        # devolve {"siafe_user", "siafe_pass"} ou None (cancelado); bloqueia até o usuário responder
        versao = pedido["versao"]
        if pedido.get("erro"):
            self.credenciais_siafe.pop(versao, None)  # a senha guardada foi recusada

        if versao not in self.credenciais_siafe:
            fila = queue.Queue()
            self._fila_siafe = fila
            self.after(0, lambda: self.show_siafe_login_frame(pedido, fila))
            resposta = fila.get()
            self._fila_siafe = None
            if resposta is None or self.stop_event:
                return None
            self.credenciais_siafe[versao] = resposta
            self._ultimo_cpf_siafe = resposta[0]

        usuario, senha = self.credenciais_siafe[versao]
        return {"siafe_user": usuario, "siafe_pass": senha}

    def show_siafe_login_frame(self, pedido, fila):

        # troca a tela de execução pela tela de login padrão, com as regras do siafe (cpf de 11 dígitos);
        # ao entrar ou voltar, restaura o cfg do sei e a tela de execução com o log
        nome = pedido["nome"]
        log_salvo = self._log_box.get("0.0", "end-1c")
        progresso_salvo = self._progress_bar.get()
        cfg_salvo = {campo: getattr(self.cfg, campo) for campo in CAMPOS_CFG_LOGIN}

        self.cfg.login_subtitle = (
            f"⚠️ Login recusado. Confira os dados do {nome}. ⚠️" if pedido.get("erro")
            else f"⚠️ Faça Login com os dados do {nome}. ⚠️"
        )
        self.cfg.login_user_label = "Usuário (CPF):"
        self.cfg.user_digits_only = True
        self.cfg.user_exact_length = True
        self.cfg.user_max_length = 11

        def responder(resposta):
            for campo, valor in cfg_salvo.items():
                setattr(self.cfg, campo, valor)
            self.show_execution_frame(
                on_cancel=lambda: self.cancelar_e_voltar(lambda: self.show_modulo_frame(self._codigo_em_execucao)),
                initial_msg=log_salvo,
            )
            self.update_progress(progresso_salvo)
            if resposta is not None:
                # devolve a frente pro navegador do siafe, que segue trabalhando
                self.after(200, self.lower)
            fila.put(resposta)

        self.show_login_frame(on_success=lambda usuario, senha: responder((usuario, senha)))
        self.add_back_button(lambda: responder(None))  # voltar = cancelar o login; as GRs ficam pendentes

        # cpf da última versão já preenchido: o cursor vai direto pra senha
        if self._ultimo_cpf_siafe:
            self._entry_user.insert(0, self._ultimo_cpf_siafe)
            self._check_login_fields()
            self._entry_pass.focus_set()
        self._trazer_para_frente()

    def _trazer_para_frente(self):

        # o navegador do sei/siafe costuma estar na frente: traz a janela do programa por cima dele
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.after(500, lambda: self.attributes("-topmost", False))

    def _liberar_pedido_siafe(self):

        # cancelamento/fechamento com o login do siafe aberto: destrava a thread que espera a resposta
        fila = self._fila_siafe
        if fila is not None:
            fila.put(None)


### BACKEND: EXECUÇÃO DOS MÓDULOS

    @staticmethod
    def _matar_arvore_processo(process: subprocess.Popen):
        if os.name == 'nt':
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, check=False,
            )
            subprocess.run(
                ["taskkill", "/IM", "msedge.exe", "/T", "/F"],
                capture_output=True, check=False,
            )
        else:
            process.terminate()

    def iniciar_operacao_thread(self, codigo, etapa):

        # bloqueia inicio de uma segunda operacao (CC ou DJ) enquanto a
        # anterior ainda esta rodando/sendo cancelada em background
        if self.operacao_em_andamento:
            logger.warning("Operação já em andamento; nova solicitação ignorada.")
            return

        # prepara a tela de execução e roda a etapa escolhida numa thread
        self.operacao_em_andamento = True
        self._codigo_em_execucao = codigo
        self.show_execution_frame(on_cancel=lambda: self.cancelar_e_voltar(lambda: self.show_modulo_frame(codigo)))
        self.stop_event = False
        self.retorno_automatico = True
        threading.Thread(target=self.execucao_thread, args=(codigo, etapa), daemon=True).start()

    def execucao_thread(self, codigo, etapa):
        nome, script = MODULOS[codigo]
        process = None
        processos_despacho_parcial = []
        try:
            script_path = str(self.cfg.base_path / script)

            # força saída sem buffer e em utf-8 pra ler o progresso em tempo real
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            cmd = [sys.executable, "-u", script_path, etapa]

            # roda o script como processo filho, capturando entrada e saída
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # passa as credenciais do sei pro script filho na primeira linha do stdin (json).
            # o stdin fica aberto: o filho pede por ele o login do siafe, se e quando precisar
            process.stdin.write(json.dumps({"sei_user": self.sei_usuario, "sei_pass": self.sei_senha}) + "\n")
            process.stdin.flush()

            # lê a saída do script linha a linha enquanto ele roda
            for line in process.stdout:
                # se o usuário cancelou, mata o processo e sai do loop
                if self.stop_event:
                    logger.warning(">>> Rotina interrompida pelo operador <<<")
                    self._matar_arvore_processo(process)
                    break

                linha_limpa = line.strip()
                if not linha_limpa:
                    continue

                # linhas com esse prefixo carregam o andamento pra barra de progresso
                if linha_limpa.startswith(PREFIXO_BARRA):
                    try:
                        atual, total = map(int, linha_limpa[len(PREFIXO_BARRA):].split(":"))
                        valor_barra = atual / total if total > 0 else 1
                        self.update_progress(valor_barra)
                    except ValueError:
                        pass
                elif linha_limpa.startswith(PREFIXO_PEDIR_SIAFE):
                    # o filho fica esperando a resposta no stdin: credenciais ou null (cancelado)
                    resposta = self._credenciais_siafe_para(json.loads(linha_limpa[len(PREFIXO_PEDIR_SIAFE):]))
                    try:
                        process.stdin.write(json.dumps(resposta) + "\n")
                        process.stdin.flush()
                    except OSError:
                        pass  # o processo já foi encerrado (cancelamento)
                elif linha_limpa.startswith(PREFIXO_DETALHE_ERRO):
                    try:
                        detalhe = base64.b64decode(linha_limpa[len(PREFIXO_DETALHE_ERRO):]).decode("utf-8")
                        if self.erros_acumulados:
                            self.erros_acumulados[-1]["traceback"] = detalhe
                    except Exception:
                        pass
                elif linha_limpa.startswith(PREFIXO_DESPACHO_PARCIAL):
                    processo_parcial = linha_limpa[len(PREFIXO_DESPACHO_PARCIAL):].strip()
                    if processo_parcial:
                        processos_despacho_parcial.append(processo_parcial)
                # linhas de log: tira o prefixo do nível e mostra na interface
                elif linha_limpa.startswith(("INFO:", "WARNING:", "ERROR:")):
                    tag = linha_limpa.split(":", 1)[0] + ":"
                    mensagem = linha_limpa[len(tag):].strip()

                    self.log(mensagem)

                    if "ERROR" in tag:
                        logger.error(f"[{nome}] {mensagem}")

                else:
                    self.log(linha_limpa)

            process.stdout.close()
            ret_code = process.wait()  # espera o processo terminar e pega o código de saída

            # traduz o código de retorno num aviso pro usuário
            if not self.stop_event:
                if ret_code == 0:
                    self.finalize_progress("Concluído", "Sucesso", "Processo concluído com sucesso!", "info")
                elif ret_code == 1:
                    self.finalize_progress("Concluído com Alertas", "Aviso", "O processo foi concluído de forma parcial.", "info")
                elif ret_code == 2:
                    self.finalize_progress("Erro de Login", "Erro", "Erro ao autenticar no SEI. Verifique usuário e senha.", "error")
                    self.retorno_automatico = False
                    self.after(3000, self.show_sei_login_frame)
                elif ret_code == 3:
                    self.finalize_progress("Erro de Login", "Erro", "Erro ao autenticar no SIAFE. Verifique usuário e senha.", "error")
                    self.credenciais_siafe.clear()
                elif ret_code == 4:
                    self.finalize_progress("Erro de Navegador", "Erro", "Erro de navegador. A sessão do navegador foi encerrada.", "error")
                    self.retorno_automatico = False
                elif ret_code == 5:
                    self.finalize_progress("Erro Crítico", "Erro", "Ocorreu um erro inesperado durante a execução.", "error")
                else:
                    self.finalize_progress("Finalizado com Erros", "Erro", f"O processo foi finalizado com código {ret_code}.", "error")

                if processos_despacho_parcial:
                    lista = "\n".join(f"- {p}" for p in processos_despacho_parcial)
                    mensagem_parcial = (
                        f"O(s) processo(s) a seguir tiveram o despacho de {nome} "
                        "preenchido parcialmente, por faltarem dados no "
                        f"sistema:\n\n{lista}\n\n"
                        "Complete as informações faltantes manualmente antes de assinar."
                    )
                    self.after(
                        200,
                        lambda: self.messagebox_warning("Despacho(s) Incompleto(s)", mensagem_parcial),
                    )

        except (NoSuchElementException, SessionNotCreatedException, InvalidSessionIdException):
            if not self.stop_event:
                logger.error(f"Erro de Navegador", exc_info=True)

        except Exception:
            if not self.stop_event:
                logger.error(f"Erro inesperado", exc_info=True)

        finally:
            if process and process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            self.operacao_em_andamento = False
            # volta pra tela do módulo depois de alguns segundos, se estiver em retorno automático
            if self.retorno_automatico:
                self.after(15000, lambda: self.show_modulo_frame(codigo))


if __name__ == "__main__":

    app = SeiApp()

    # configura os logs: um geral na rede e um só de erros na pasta local
    pasta_erros = Path(__file__).parent / "logs"
    caminho_geral, caminho_erros = configurar_log("Programa SEI", PASTA_LOG_GERAL, pasta_erros, callback_interface=app.log)

    # Coleta automática de todo logger.error() (do app e da biblioteca) para o resumo enviado ao fechar.
    coletor_erros = ColetorErros(app.erros_acumulados)
    for nome_logger in ("jupiter", "automaweb"):
        logging.getLogger(nome_logger).addHandler(coletor_erros)

    # Notificação de erros aos desenvolvedores: credenciais em config.json (gitignored).
    # Se o arquivo faltar/estiver incompleto, a notificação apenas fica desativada.
    try:
        config = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
        app.dev_emails = config.get("desenvolvedores", [])
        app.teams_webhook = config.get("webhook_teams") or None
        # só cria o GraphAPI se todas as credenciais estiverem presentes
        if all(config.get("graph_api", {}).get(k) for k in ("tenant_id", "client_id", "client_secret", "conta_corporativa")):
            app.graph = GraphAPI(**config["graph_api"])
    except Exception:
        logger.warning("Notificação de erros desativada (config.json ausente ou inválido)", exc_info=True)

    def ao_fechar():
        # ao fechar a janela: envia o resumo e só então encerra liberando os recursos
        app._enviar_resumo_erros()  # resumo dos erros acumulados na sessão (se houver)
        app.safe_exit(app.limpar_recursos)

    # liga o clique no "X" da janela à rotina de fechamento e inicia o loop da interface
    app.protocol("WM_DELETE_WINDOW", ao_fechar)
    app.mainloop()
