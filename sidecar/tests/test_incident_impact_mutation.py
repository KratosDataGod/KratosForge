"""Incident reach edges the main tests don't pin down (mutation batch 138, docs/quality/mutation-testing.md):
the defaults for a product or link that leaves fields out."""

from fabric_desktop.workloads.impact import blast_radius


def test_a_product_and_link_that_leave_fields_out_get_the_defaults():
    products = [{"id": "dp-1", "project": "sales", "ports": [{"port": "orders", "entity": "orders", "env": "prod"}],
                 "consumers": [{"port": "orders", "consumer_project": "finance"}]}]
    out = blast_radius(products, "sales", "orders", "prod")
    [p] = out["products"]
    assert p["name"] == "dp-1" and p["owner"] == "" and p["domain_id"] == "" and p["status"] == ""
    assert out["consumers"] == [{"project": "finance", "alias": "", "product_id": "dp-1", "port": "orders",
                                 "linked_by": ""}]


def test_a_product_and_link_that_have_the_fields_keep_them():
    products = [{"id": "dp-1", "name": "Orders", "owner": "dana@contoso.com", "domain_id": "d1", "status": "published",
                 "project": "sales", "ports": [{"port": "orders", "entity": "orders", "env": "prod"}],
                 "consumers": [{"port": "orders", "consumer_project": "finance", "alias": "o"}]}]
    out = blast_radius(products, "sales", "orders", "prod")
    [p] = out["products"]
    assert (p["name"], p["owner"], p["domain_id"], p["status"]) == ("Orders", "dana@contoso.com", "d1", "published")
    assert out["consumers"][0]["alias"] == "o"
