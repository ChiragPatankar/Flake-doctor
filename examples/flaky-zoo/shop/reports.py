import time
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Report:
    generated_at: datetime
    total: float
    count: int


def _load_orders(store_id):
    time.sleep(0.15)  # stands in for a database query
    return [19.99, 5.00, 42.50]


def build_report(store_id):
    generated_at = datetime.now().replace(microsecond=0)
    orders = _load_orders(store_id)
    return Report(generated_at=generated_at, total=round(sum(orders), 2), count=len(orders))
