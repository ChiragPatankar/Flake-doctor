from shop.payments import register


@register("card")
class CardProvider:
    fee_pct = 2.9

    def charge(self, amount):
        return {"status": "charged", "amount": amount, "fee": round(amount * self.fee_pct / 100, 2)}

    def refund(self, amount):
        return {"status": "refunded", "amount": amount}
