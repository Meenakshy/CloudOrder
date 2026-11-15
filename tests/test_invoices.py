from types import SimpleNamespace

from workers.invoices import render_invoice


class InvoiceItems:
    def all(self):
        return [
            SimpleNamespace(
                product_name="Desk lamp",
                quantity=2,
                unit_price=12.5,
            )
        ]


def test_invoice_renderer_emits_a_pdf_document():
    order = SimpleNamespace(
        id="order-123",
        customer_id="customer-123",
        created_at=SimpleNamespace(isoformat=lambda: "2026-11-14T12:00:00+00:00"),
        total_amount=25.0,
        items=InvoiceItems(),
    )

    pdf = render_invoice(order)

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")