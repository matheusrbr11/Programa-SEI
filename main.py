from selenium.common.exceptions import NoSuchElementException, SessionNotCreatedException, InvalidSessionIdException
import customtkinter as ctk
from pathlib import Path
import pandas as pd
import subprocess
import threading
import traceback
import logging
import json
import sys
import os

from eop_ui import AppConfig, BaseApp
from jupiter import configurar_log, Siafe, GraphAPI
from processar_cc.config import PASTA_LOG_GERAL

logger = logging.getLogger("jupiter.SEI")


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
            app_version="1.0.0",
            login_subtitle="⚠️ Faça Login com os dados do SEI. ⚠️",
            login_user_label="Usuário do SEI:",
            image_filename="img/SEI.png",
            logo_size=(360, 125),
        )
        super().__init__(cfg)

        self.siafeVersao = 1  # 1 = Prod | 2 = Beta | 3 = Homologação

        # caminho usado pelo app: script do subprocesso de crédito em conta
        self.ProcessarCCPath = self.cfg.base_path / "run_processar_cc.py"

        self.siafe = Siafe()             # controla o navegador/sessão do siafe (login da etapa 1)
        self.stop_event = False          # vira True quando o usuário cancela a rotina
        self.retorno_automatico = True   # se True, volta sozinho pra tela anterior ao terminar

        self.sei_usuario = ""            # credenciais do sei, capturadas no login inicial
        self.sei_senha = ""

        self.siafe_usuario = ""          # credenciais do siafe, só preenchidas se a Etapa 1 rodar
        self.siafe_senha = ""

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
        try:
            if self.siafe.driver:
                self.siafe.fechar_driver()
        except Exception:
            pass
        self.after(0, tela_retorno_fn)

    def limpar_recursos(self):

        # chamado ao fechar o app: garante que o navegador não fique aberto
        self.stop_event = True
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

        # wrapper do componente generico de login (eop_ui.BaseApp) pro SEI,
        # no mesmo padrao de show_siafe_login_frame
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
        self.make_primary_button(frame_modulos, text="Crédito em Conta", command=self.show_cc_execution_frame).pack(pady=10)
        self.make_primary_button(frame_modulos, text="Depósito Judicial", command=self.show_em_desenvolvimento_frame).pack(pady=10)
        self._add_footer()

    def show_em_desenvolvimento_frame(self):

        # placeholder pra opção que ainda não foi implementada
        self.clear_frame()
        self.create_menu()
        self.add_back_button(self.show_main_frame)
        self._add_logo()

        self.make_header_label("Programa SEI", pady=(20, 5))

        frame_aviso = self.make_section_frame()
        frame_aviso.pack(fill="both", expand=True, pady=40)

        ctk.CTkLabel(frame_aviso, text="🚧", font=ctk.CTkFont(size=60)).pack(pady=(0, 10))
        ctk.CTkLabel(frame_aviso, text="Módulo em Desenvolvimento", font=self.font_bold, text_color="#1f6aa5").pack(pady=(0, 10))
        ctk.CTkLabel(frame_aviso, text="A opção 'Depósito Judicial' estará disponível\nem futuras atualizações.", font=ctk.CTkFont(size=14), text_color="gray").pack()

        self._add_footer()

    def show_cc_execution_frame(self):

        # tela do crédito em conta: as duas etapas do fluxo, cada uma com seu botão
        self.clear_frame()
        self.create_menu()
        self.add_back_button(self.show_main_frame)
        self._add_logo()
        self.make_header_label("Crédito em Conta", pady=(20, 5))
        self.make_subtitle_label("Instrução de Processos", pady=(0, 20))

        frame_etapas = self.make_section_frame()
        self.make_primary_button(
            frame_etapas, text="PROCESSAR PROCESSOS",
            command=self._iniciar_processar_processos
        ).pack(pady=10)
        self.make_primary_button(
            frame_etapas, text="RESPONDER PROCESSOS",
            command=lambda: self.iniciar_operacao_thread("etapa2", "Responder Processos")
        ).pack(pady=10)
        self._add_footer()

    def _iniciar_processar_processos(self):

        # se o siafe ja foi logado com sucesso nesta sessao, pula a tela de
        # login e roda a etapa 1 direto; senao, pede o login primeiro
        if self.siafe_usuario and self.siafe_senha:
            self.iniciar_operacao_thread("etapa1", "Processar Processos")
        else:
            self.show_siafe_login_frame()

    def show_siafe_login_frame(self):

        # troca o cfg pras regras do siafe (cpf de 11 dígitos), mantendo o padrão do login
        self.cfg_salvo_app_name = self.cfg.app_name
        self.cfg_salvo_subtitle = self.cfg.login_subtitle
        self.cfg_salvo_user_label = self.cfg.login_user_label
        self.cfg_salvo_digits_only = self.cfg.user_digits_only
        self.cfg_salvo_exact_length = self.cfg.user_exact_length
        self.cfg_salvo_max_length = self.cfg.user_max_length

        self.cfg.app_name = "Programa SEI"
        self.cfg.login_subtitle = "⚠️ Faça Login com os dados do SIAFE-Rio. ⚠️"
        self.cfg.login_user_label = "Usuário (CPF):"
        self.cfg.user_digits_only = True
        self.cfg.user_exact_length = True
        self.cfg.user_max_length = 11

        self.show_login_frame(on_success=self.processar_login_siafe)
        self.add_back_button(self._cancelar_login_siafe)

    def _cancelar_login_siafe(self):

        # volta pra tela de execução do crédito em conta, desfazendo o cfg do siafe
        self._restaurar_cfg_login_sei()
        self.show_cc_execution_frame()

    def _restaurar_cfg_login_sei(self):

        # restaura as regras de login originais do sei
        self.cfg.app_name = self.cfg_salvo_app_name
        self.cfg.login_subtitle = self.cfg_salvo_subtitle
        self.cfg.login_user_label = self.cfg_salvo_user_label
        self.cfg.user_digits_only = self.cfg_salvo_digits_only
        self.cfg.user_exact_length = self.cfg_salvo_exact_length
        self.cfg.user_max_length = self.cfg_salvo_max_length

    def processar_login_siafe(self, usuario_digitado, senha_digitada):

        # guarda as credenciais do siafe, restaura o cfg do sei e dispara a etapa 1
        self.siafe_usuario = usuario_digitado
        self.siafe_senha = senha_digitada
        self._restaurar_cfg_login_sei()
        self.iniciar_operacao_thread("etapa1", "Processar Processos")


### BACKEND: EXECUÇÃO DO CRÉDITO EM CONTA

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

    def iniciar_operacao_thread(self, etapa, titulo_operacao):

        # prepara a tela de execução e roda a etapa escolhida numa thread
        self.show_execution_frame(on_cancel=lambda: self.cancelar_e_voltar(self.show_cc_execution_frame))
        self.stop_event = False
        self.retorno_automatico = True
        threading.Thread(target=self.execucao_cc_thread, args=(etapa, titulo_operacao), daemon=True).start()

    def execucao_cc_thread(self, etapa, titulo_operacao):
        process = None
        try:
            script_path = str(self.ProcessarCCPath)

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

            # passa as credenciais pro script filho via stdin (json); etapa2 não precisa do siafe
            credenciais = {
                "sei_user": self.sei_usuario,
                "sei_pass": self.sei_senha,
                "siafe_user": self.siafe_usuario,
                "siafe_pass": self.siafe_senha,
            }

            process.stdin.write(json.dumps(credenciais))
            process.stdin.close()

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
                PREFIXO_BARRA = "__PROGRESSO__:"
                if linha_limpa.startswith(PREFIXO_BARRA):
                    try:
                        atual, total = map(int, linha_limpa[len(PREFIXO_BARRA):].split(":"))
                        valor_barra = atual / total if total > 0 else 1
                        self.update_progress(valor_barra)
                    except ValueError:
                        pass
                # linhas de log: tira o prefixo do nível e mostra na interface
                elif linha_limpa.startswith(("INFO:", "WARNING:", "ERROR:")):
                    tag = linha_limpa.split(":", 1)[0] + ":"
                    mensagem = linha_limpa[len(tag):].strip()

                    self.log(mensagem)

                    if "ERROR" in tag:
                        logger.error(f"[Crédito em Conta] {mensagem}")

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
                    self.retorno_automatico = False
                    self.siafe_usuario = ""
                    self.siafe_senha = ""
                    self.after(3000, self.show_siafe_login_frame)
                elif ret_code == 4:
                    self.finalize_progress("Erro de Navegador", "Erro", "Erro de navegador. A sessão do navegador foi encerrada.", "error")
                    self.retorno_automatico = False
                elif ret_code == 5:
                    self.finalize_progress("Erro Crítico", "Erro", "Ocorreu um erro inesperado durante a execução.", "error")
                else:
                    self.finalize_progress("Finalizado com Erros", "Erro", f"O processo foi finalizado com código {ret_code}.", "error")

        except (NoSuchElementException, SessionNotCreatedException, InvalidSessionIdException):
            if not self.stop_event:
                logger.error(f"Erro de Navegador", exc_info=True)

        except Exception:
            if not self.stop_event:
                logger.error(f"Erro inesperado", exc_info=True)

        finally:
            # volta pra tela de execução do crédito em conta depois de alguns segundos, se estiver em retorno automático
            if self.retorno_automatico:
                self.after(500000, self.show_cc_execution_frame)


if __name__ == "__main__":

    app = SeiApp()

    # configura os logs: um geral na rede e um só de erros na pasta local
    pasta_erros = Path(__file__).parent / "logs"
    caminho_geral, caminho_erros = configurar_log("Programa SEI", PASTA_LOG_GERAL, pasta_erros, callback_interface=app.log)

    # Coleta automática de todo logger.error() (do app e da biblioteca) para o resumo enviado ao fechar
    logging.getLogger("jupiter").addHandler(ColetorErros(app.erros_acumulados))

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
