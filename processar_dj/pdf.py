"""Conversão da planilha de Resgate para PDF (Comprovante DJO), via automação COM do Excel."""

from __future__ import annotations

import win32com.client as win32
from pathlib import Path
import logging

from .core import ErroProcesso

log = logging.getLogger("jupiter.processarDJ")

XL_TYPE_PDF = 0
XL_LANDSCAPE = 2


def converter_planilha_para_pdf(caminho_xlsx: Path, caminho_pdf: Path | None = None) -> Path:
    """Exporta a primeira aba da planilha para PDF, em paisagem, ajustada à
    largura de uma página (a altura flui livremente em múltiplas páginas se
    necessário), repetindo o cabeçalho institucional e os títulos de coluna
    em cada página. Margens estreitas para maximizar a área útil.
    """
    caminho_xlsx = Path(caminho_xlsx).resolve()
    caminho_pdf = Path(caminho_pdf).resolve() if caminho_pdf else caminho_xlsx.with_suffix(".pdf")
    caminho_pdf.parent.mkdir(parents=True, exist_ok=True)

    excel = win32.Dispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        wb = excel.Workbooks.Open(str(caminho_xlsx))
        
        try:
            ws = wb.Worksheets(1)
            ws.PageSetup.Orientation = XL_LANDSCAPE
            ws.PageSetup.Zoom = False
            ws.PageSetup.FitToPagesWide = 1
            ws.PageSetup.FitToPagesTall = False
            ws.PageSetup.PrintArea = ws.UsedRange.Address
            ws.PageSetup.PrintTitleRows = "$1:$5"
            ws.PageSetup.TopMargin = 54
            ws.PageSetup.BottomMargin = 54
            ws.PageSetup.LeftMargin = 18
            ws.PageSetup.RightMargin = 18
            ws.PageSetup.HeaderMargin = 21.6
            ws.PageSetup.FooterMargin = 21.6
            wb.ExportAsFixedFormat(XL_TYPE_PDF, str(caminho_pdf))
            
        finally:
            wb.Close(SaveChanges=False)
            
    except Exception as e:
        raise ErroProcesso(f"Erro ao converter '{caminho_xlsx.name}' para PDF: {e}") from e
    finally:
        excel.Quit()

    if not caminho_pdf.exists():
        raise ErroProcesso(f"Conversão para PDF não gerou o arquivo esperado: {caminho_pdf}")

    log.info(f"Planilha convertida para PDF: {caminho_pdf.name}")
    return caminho_pdf
