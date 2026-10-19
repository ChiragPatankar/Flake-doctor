# Warehouse (hard benchmark)

A ~180-test inventory/checkout service with four flaky tests that are hard to
find by reading code alone. It's flake-doctor's hard benchmark. **Spoilers below.**

| Test | How it shows up | Root cause |
|------|-----------------|------------|
| `tests/test_checkout.py::test_order_total[standard]` | fails ~50% under `pytest-randomly`; default order always passes | `shop/promotions.py::flash_sale_price` mutates the dict returned by the `lru_cache`d catalog. Only `test_flash_sale_price[WID-100S-20]`, one of 176 tests, pollutes it. A real product bug: one flash sale permanently reprices the item for the whole process. |
| `tests/test_invoices.py::test_invoice_total` | fails ~9% of runs | `invoice_total` sums floats in `set()` order, which depends on per-process string hashing, so the last bits of the total vary. |
| `tests/test_ratelimit.py::test_blocks_fourth_call` | ~2% idle, ~35% on a loaded machine | Signing takes ~5 ms of CPU per call against a 20 ms real-time window; on a busy CI runner three calls take longer than the window. |
| `tests/test_refunds.py::test_refund_card_order` | passes in the full suite, fails alone or on a CI shard | The card provider registers itself only when `shop.payments.card` is imported, which only `test_payments_card.py` does. A real product bug: `refund_order` can't find the provider in production. |
