"""Funções puras e utilitárias reutilizáveis (DRY)."""

from __future__ import annotations
from selenium.common.exceptions import WebDriverException, NoSuchElementException
from pathlib import Path
import unicodedata
import pdfplumber
import automaweb
import logging
import time
import re

from .config import MESES, PASTA_GR

log = logging.getLogger("jupiter.processarCC")

ERROS_NAVEGADOR_PERDIDO = (WebDriverException)


def navegador_perdido(exc: BaseException) -> bool:
    """
    Verifica se a exceção (ou alguma na sua cadeia de causas) indica que o
    navegador/sessão morreu. Necessário porque o código de negócio costuma
    envolver a exceção original do Selenium em ErroSEI/ErroSIAFE/ErroBB
    (``except Exception as e: raise ErroSEI(f"...: {e}") from e``), perdendo
    o tipo na cláusula except mas preservando a causa em __cause__/__context__.

    Usado tanto no loop de lote (orchestrator.py) quanto nos loops de
    candidato (services.py), pra garantir que uma sessao morta nunca seja
    tratada como "so pular este item e tentar o proximo".
    """
    vista = set()
    atual = exc
    while atual is not None and id(atual) not in vista:
        if isinstance(atual, ERROS_NAVEGADOR_PERDIDO) and not isinstance(atual, NoSuchElementException):
            return True
        vista.add(id(atual))
        atual = atual.__cause__ or atual.__context__
    return False


def sessao_siafe_viva(siafe) -> bool:
    """
    Confirma se a sessão do driver ainda responde, consultando ``window_handles``. 
    Usado no lote de download de GR (sub-fase B da Etapa 1) para diferenciar 
    "GR nao encontrada" (pular item) de "navegador morreu" (abortar o lote).
    """
    try:
        _ = siafe.driver.window_handles
        return True
    except Exception:
        return False


def mensagem_curta(e: Exception) -> str:
    """
    Reduz uma exceção a uma única linha, para uso no log da interface (categoria 1).
    WebDriverException (Selenium) embute no str() o stacktrace nativo do
    msedgedriver, multi-linha — sem isso, cada linha do traceback vira uma
    linha solta no log exibido ao usuário. Os arquivos de log (categorias 2 e 3)
    continuam recebendo o traceback completo via exc_info, não afetado por isto.
    """
    primeira_linha = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return primeira_linha.removeprefix("Message: ").rstrip()


def buscar_regex(texto: str, padrao: str, grupo: int = 1, flags: int = re.IGNORECASE) -> str | None:
    """Busca regex seguro. Retorna o grupo especificado ou None."""
    if not texto:
        return None
    m = re.search(padrao, texto, flags)
    return m.group(grupo) if m else None


def formatar_moeda(valor: float) -> str:
    """Formata float para moeda brasileira (1.234,56)."""
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def normalizar_cnpj(cnpj_raw: str | None) -> str | None:
    """Normaliza CNPJ: remove não-dígitos, valida 14 dígitos, formata."""
    if not cnpj_raw:
        return None
    digitos = re.sub(r"\D", "", cnpj_raw)
    if len(digitos) != 14:
        return None
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


def normalizar_processo_judicial(valor: str | None) -> str | None:
    """Limpa e valida número CNJ (14 a 20 dígitos)."""
    if not valor:
        return None
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) < 14:
        return None
    if len(digitos) == 20:
        return (
            f"{digitos[:7]}-{digitos[7:9]}.{digitos[9:13]}."
            f"{digitos[13]}.{digitos[14:16]}.{digitos[16:]}"
        )
    return digitos


def converter_data_por_extenso(data_str: str | None) -> str | None:
    """Converte '5 de agosto de 2024' -> '05/08/2024'."""
    if not data_str:
        return None
    m = re.match(r"(\d{1,2})\s*de\s+(\w+)\s+de\s+(\d{4})", data_str.strip(), re.IGNORECASE)
    if not m:
        return None
    dia, mes_nome, ano = m.groups()
    mes = MESES.get(mes_nome.lower())
    if not mes:
        return None
    return f"{int(dia):02d}/{mes}/{ano}"


def converter_valor_moeda(valor_str: str | None) -> float | None:
    """Converte string monetária brasileira para float."""
    if not valor_str:
        return None
    try:
        return float(valor_str.replace(".", "").replace(",", "."))
    except ValueError:
        log.error(f"Nao foi possivel converter o valor '{valor_str}' para float.", exc_info=True)
        return None


def extrair_texto_pdf(caminho_pdf: str) -> str:
    """Extrai texto de todas as páginas de um PDF.

    Usa dedupe_chars para lidar com PDFs que simulam negrito duplicando
    glifos com um offset sub-pixel (ex.: Alvará de Levantamento), o que
    sem isso resultaria em texto como 'PPooddeerr'.
    """
    texto = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages:
                texto += (pagina.dedupe_chars().extract_text() or "") + "\n"
    except Exception as e:
        log.error(f"Erro ao extrair texto do PDF {caminho_pdf}: {mensagem_curta(e)}", exc_info=True)
    return texto


def aguardar_novo_pdf(pasta: Path, arquivos_antes: set[Path], timeout: int = 30) -> Path | None:
    """Aguarda surgimento de novo PDF na pasta de downloads."""
    novo_pdf: Path | None = None
    tamanho_anterior = -1
    for _ in range(timeout):
        if novo_pdf is None:
            novos = set(pasta.glob("*.pdf")) - arquivos_antes
            if novos:
                novo_pdf = list(novos)[0]
                tamanho_anterior = novo_pdf.stat().st_size
                time.sleep(1)
                continue
        else:
            tamanho_atual = novo_pdf.stat().st_size
            if tamanho_atual == tamanho_anterior:
                return novo_pdf
            tamanho_anterior = tamanho_atual
        time.sleep(1)
    log.warning("Timeout: nenhum PDF novo detectado na pasta Downloads.")
    return None


def formatar_nome_arquivo(nome: str) -> str:
    """Remove acentos, barras e contra-barras de nomes de arquivo."""
    sem_acentos = "".join(
        c for c in unicodedata.normalize("NFD", nome)
        if unicodedata.category(c) != "Mn"
    )
    return sem_acentos.replace("/", "").replace("\\", "")


_cache_pasta_gr: list[str] | None = None


def _listar_arquivos(diretorio: Path, extensao: str) -> list[str]:
    """Lista arquivos recursivamente. Para PASTA_GR, cacheia em memória
    pelo tempo de vida do processo — só este programa escreve nela, e o
    cache é atualizado a cada gravação via _registrar_arquivo_pasta_gr."""
    global _cache_pasta_gr
    if diretorio == PASTA_GR:
        if _cache_pasta_gr is None:
            _cache_pasta_gr = automaweb.listar_recursivo(diretorio=str(diretorio), extensao=extensao)
        return _cache_pasta_gr
    return automaweb.listar_recursivo(diretorio=str(diretorio), extensao=extensao)


def _registrar_arquivo_pasta_gr(caminho: str) -> None:
    """Registra um arquivo recem-gravado em PASTA_GR, mantendo o cache coerente."""
    if _cache_pasta_gr is not None:
        _cache_pasta_gr.append(str(caminho))


def localizar_arquivo_em_disco(
    diretorio: Path,
    *,
    nome_igual: str | None = None,
    substring: str | None = None,
    extensao: str = ".pdf",
) -> str | None:
    """Busca recursiva de arquivo por critérios flexíveis."""
    lista = _listar_arquivos(diretorio, extensao)
    for arq in lista:
        path = Path(arq)
        if nome_igual and path.stem == nome_igual:
            return arq
        if substring and substring in path.name:
            return arq
    return None


def localizar_gr_em_disco(num_documento: str | None = None, valor: float | None = None) -> str | None:
    """Wrapper específico para GRs."""
    if num_documento:
        arq = localizar_arquivo_em_disco(PASTA_GR, substring=num_documento)
        if arq:
            return arq
    if valor is not None:
        valor_fmt = f"R$ {formatar_moeda(float(valor))}"
        arq = localizar_arquivo_em_disco(PASTA_GR, substring=valor_fmt)
        if arq:
            return arq
    return None


def localizar_comprovante_em_disco(conta_judicial: str) -> str | None:
    """Wrapper específico para comprovantes BB."""
    return localizar_arquivo_em_disco(PASTA_GR, nome_igual=conta_judicial)


def mover_gr_para_destino(arquivo_baixado: Path, num_doc: str, valor: float) -> str:
    """Renomeia e move GR para a pasta definitiva."""
    import automaweb
    valor_fmt = formatar_moeda(float(valor))
    caminho_final = PASTA_GR / f"{num_doc} - R$ {valor_fmt}.pdf"
    automaweb.mover_arquivo(str(arquivo_baixado), str(caminho_final))
    _registrar_arquivo_pasta_gr(str(caminho_final))
    return str(caminho_final)
