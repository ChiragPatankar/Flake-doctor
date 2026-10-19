import time

from shop.exports import start_export


def test_export_finishes():
    job = start_export([(1, "apple"), (2, "pear")])
    time.sleep(0.02)
    assert job.done
    assert job.result == "1,apple\n2,pear"
