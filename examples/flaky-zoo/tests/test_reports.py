from datetime import datetime

from shop.reports import build_report


def test_report_totals():
    report = build_report(store_id=1)
    assert report.total == 67.49
    assert report.count == 3


def test_report_timestamp():
    report = build_report(store_id=1)
    assert report.generated_at == datetime.now().replace(microsecond=0)
