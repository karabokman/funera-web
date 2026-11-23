"""The quotation as a PDF, built on the server with ReportLab (pure Python, nothing to install on the host)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Quote

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#d7dee6")
LOGO = Path(settings.BASE_DIR) / "static" / "img" / "logo-compact.png"


def _money(value) -> str:
    return f"R{value:,.2f}".replace(",", " ")


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def render(quote: Quote) -> bytes:
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, textColor=MUTED)
    label = ParagraphStyle("label", parent=small, fontName="Helvetica-Bold", fontSize=7.5, textColor=MUTED)
    bold = ParagraphStyle("bold", parent=body, fontName="Helvetica-Bold")
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=20, leading=24, alignment=TA_RIGHT)
    right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)
    right_small = ParagraphStyle("right_small", parent=small, alignment=TA_RIGHT)
    right_bold = ParagraphStyle("right_bold", parent=bold, alignment=TA_RIGHT)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Quotation {quote.number}", author=settings.COMPANY_LEGAL_NAME, subject=f"Quotation for {quote.business_name}",
    )  # fmt: skip
    width = A4[0] - 40 * mm
    story = []

    seller = [settings.COMPANY_LEGAL_NAME]
    if settings.VAT_NUMBER:
        seller.append(f"VAT no. {settings.VAT_NUMBER}")
    seller.append(settings.CONTACT_EMAIL)
    left_cell = [_p("\n".join(seller), small)]
    if LOGO.exists():
        left_cell.insert(0, Image(str(LOGO), width=34 * mm, height=34 * mm * 261 / 690))
        left_cell.insert(1, Spacer(1, 2 * mm))
    right_cell = [
        Paragraph("Quotation", title),
        _p(quote.number, right_bold),
        _p(f"Valid until {quote.valid_until:%d %B %Y}", right_small),
    ]
    head = Table([[left_cell, right_cell]], colWidths=[width * 0.55, width * 0.45])
    head.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story += [head, Spacer(1, 6 * mm)]

    contact = "\n".join([quote.contact_name, quote.email, quote.phone])
    parties = Table(
        [[
            [_p("PREPARED FOR", label), _p(quote.business_name, bold), _p(contact, small)],
            [_p("PLAN", label), _p(f"{quote.plan.name} - billed {quote.get_billing_period_display().lower()}", bold), _p(quote.plan.tagline, small)],
        ]],
        colWidths=[width / 2, width / 2],
    )  # fmt: skip
    parties.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, LINE),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
            ]
        )
    )
    story += [parties, Spacer(1, 5 * mm)]
    if quote.intro:
        story += [_p(quote.intro, body), Spacer(1, 5 * mm)]

    rows = [[_p("ITEM", label), _p("QTY", right_small), _p("PRICE", right_small), _p("AMOUNT", right_small)]]
    period = "year" if quote.billing_period == "annual" else "month"
    rows.append([
        [_p(f"{quote.plan.name} plan subscription", bold), _p(f"Charged every {period} until cancelled", small)],
        _p("1", right), _p(_money(quote.recurring_gross), right), _p(_money(quote.recurring_gross), right),
    ])  # fmt: skip
    for line in quote.lines.all():
        rows.append(
            [
                _p(f"{line.description} (once-off)", body),
                _p(line.quantity, right),
                _p(_money(line.unit_price), right),
                _p(_money(line.total), right),
            ]
        )
    items = Table(rows, colWidths=[width * 0.52, width * 0.1, width * 0.19, width * 0.19], repeatRows=1)
    items.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (0, 0), (-1, 0), 0.6, LINE), ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("LEFTPADDING", (0, 0), (0, -1), 0), ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))  # fmt: skip
    story += [items, Spacer(1, 4 * mm)]

    vat_label = " (incl. VAT)" if quote.has_vat else ""
    totals = [[_p(f"Every {period}{vat_label}", body), _p(_money(quote.recurring_gross), right_bold)]]
    if quote.once_off_gross:
        totals.append([_p(f"Once-off items{vat_label}", body), _p(_money(quote.once_off_gross), right_bold)])
    if quote.has_vat:
        totals.append(
            [_p(f"VAT included ({quote.vat_rate * 100:g}%)", small), _p(_money(quote.total_vat), right_small)]
        )
    totals.append([_p("Due on acceptance", bold), _p(_money(quote.first_payment_gross), right_bold)])
    box = Table(totals, colWidths=[width * 0.28, width * 0.17], hAlign="RIGHT")
    box.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, LINE),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [box, Spacer(1, 5 * mm)]
    story.append(_p("All prices include VAT." if quote.has_vat else "No VAT is charged.", small))
    story.append(_p("Accept online using the link in the e-mail we sent you. Our subscription terms apply.", small))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10 * mm, f"{quote.number} - {settings.COMPANY_LEGAL_NAME}")
        canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
