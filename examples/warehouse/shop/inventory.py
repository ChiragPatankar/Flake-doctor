class Inventory:
    def __init__(self, stock=None):
        self.stock = dict(stock or {})

    def available(self, sku):
        return self.stock.get(sku, 0)

    def reserve(self, sku, qty):
        if qty <= 0:
            raise ValueError("quantity must be positive")
        if self.available(sku) < qty:
            raise ValueError("only %d of %s left" % (self.available(sku), sku))
        self.stock[sku] -= qty

    def restock(self, sku, qty):
        self.stock[sku] = self.available(sku) + qty
