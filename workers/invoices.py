from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def render_invoice(order) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.setTitle(f"CloudOrder invoice {order.id}")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(54, 740, "CloudOrder Invoice")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(54, 716, f"Order: {order.id}")
    pdf.drawString(54, 700, f"Customer: {order.customer_id}")
    pdf.drawString(54, 684, f"Created: {order.created_at.isoformat()}")
    y = 646
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(54, y, "Product")
    pdf.drawString(330, y, "Qty")
    pdf.drawString(390, y, "Unit price")
    pdf.drawString(500, y, "Line total")
    y -= 18
    pdf.setFont("Helvetica", 10)
    for item in order.items.all():
        if y < 72:
            pdf.showPage()
            y = 740
        pdf.drawString(54, y, item.product_name[:42])
        pdf.drawRightString(360, y, str(item.quantity))
        pdf.drawRightString(470, y, f"${item.unit_price:.2f}")
        pdf.drawRightString(558, y, f"${item.unit_price * item.quantity:.2f}")
        y -= 16
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawRightString(558, max(y - 12, 54), f"Total: ${order.total_amount:.2f}")
    pdf.save()
    return output.getvalue()