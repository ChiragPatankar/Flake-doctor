from shop.payments import register


@register("invoice")
class InvoiceProvider:
    def charge(self, amount):
        return {"status": "invoiced", "amount": amount, "fee": 0}

    def refund(self, amount):
        return {"status": "credit_note", "amount": amount}
