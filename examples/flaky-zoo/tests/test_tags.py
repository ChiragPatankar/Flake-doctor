from shop.tags import unique_tags


def test_unique_tags_removes_duplicates():
    assert sorted(unique_tags(["vip", "VIP", "billing"])) == ["billing", "vip"]


def test_unique_tags_keeps_order():
    assert unique_tags(["urgent", "billing", "urgent"]) == ["urgent", "billing"]
