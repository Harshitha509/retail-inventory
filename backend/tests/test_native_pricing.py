import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, MetaData, String, Table, create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config

from app.generate_data import CATALOGUE, generate
from app.models import Base, Product, Supplier
from app.pipeline import validate_transform

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_generator_is_reproducible_and_uses_native_inr(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    params = dict(seed=42, products=100, days=180, reference_date=date(2026, 11, 24))
    counts1 = generate(first, **params)
    counts2 = generate(second, **params)
    assert counts1 == counts2 == {"products": 100, "suppliers": 8, "sales": 16827, "inventory": 100}
    for filename in ("suppliers.csv", "products.csv", "sales.csv", "inventory.csv", "pricing_map.csv"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_generated_catalogue_has_unique_names_and_category_price_bands(tmp_path):
    output = tmp_path / "generated"
    generate(output, seed=42, products=100, days=180, reference_date=date(2026, 11, 24))
    catalogue = rows(output / "pricing_map.csv")
    assert len(catalogue) == 100
    assert len({row["product_id"] for row in catalogue}) == 100
    assert len({row["product_name"] for row in catalogue}) == 100
    assert all(" – " in row["product_name"] and row["pack_size"] in row["product_name"] for row in catalogue)
    assert {row["category"] for row in catalogue} == set(CATALOGUE)
    assert all(row["subtype"] and Decimal(row["unit_price_inr"]) > 0 for row in catalogue)
    for category, options in CATALOGUE.items():
        prices = [Decimal(row["unit_price_inr"]) for row in catalogue if row["category"] == category]
        bounds = [Decimal(price) for option in options for price in option.price_points_inr]
        assert len(prices) == 20
        assert min(bounds) <= min(prices) <= max(prices) <= max(bounds)


def test_catalogue_does_not_depend_on_sales_history_length(tmp_path):
    short, long = tmp_path / "short", tmp_path / "long"
    generate(short, seed=42, products=100, days=8, reference_date=date(2026, 11, 24))
    generate(long, seed=42, products=100, days=180, reference_date=date(2026, 11, 24))
    assert rows(short / "pricing_map.csv") == rows(long / "pricing_map.csv")


def test_generator_preserves_existing_sales_inventory_and_supplier_relationships(tmp_path):
    output = tmp_path / "generated"
    generate(output, seed=42, products=100, days=180, reference_date=date(2026, 11, 24))
    old_sales, new_sales = rows(PROJECT_ROOT / "data/sales.csv"), rows(output / "sales.csv")
    old_inventory, new_inventory = rows(PROJECT_ROOT / "data/inventory.csv"), rows(output / "inventory.csv")
    old_products, new_products = rows(PROJECT_ROOT / "data/products.csv"), rows(output / "products.csv")
    assert [(r["transaction_id"], r["product_id"], r["quantity_sold"], r["transaction_date"]) for r in new_sales] == [
        (r["transaction_id"], r["product_id"], r["quantity_sold"], r["transaction_date"]) for r in old_sales]
    assert [(r["product_id"], r["available_stock"], r["reserved_stock"], r["updated_at"]) for r in new_inventory] == [
        (r["product_id"], r["available_stock"], r["reserved_stock"], r["updated_at"]) for r in old_inventory]
    assert [(r["product_id"], r["supplier_id"], r["lead_time_days"], r["reorder_threshold"], r["status"]) for r in new_products] == [
        (r["product_id"], r["supplier_id"], r["lead_time_days"], r["reorder_threshold"], r["status"]) for r in old_products]


def test_pipeline_accepts_historical_totals_and_rejects_excess_currency_precision():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(Supplier(supplier_id="SUP-TEST", supplier_name="Synthetic supplier", lead_time_days=5, reliability=.95))
        db.add(Product(product_id="SKU-TEST", product_name="Synthetic product", category="Pantry",
                        unit_price=Decimal("199.00"), supplier_id="SUP-TEST", lead_time_days=5,
                        reorder_threshold=10, status="active"))
    frames = {
        "suppliers": [{"supplier_id":"SUP-TEST","supplier_name":"Synthetic supplier","lead_time_days":"5","reliability":"0.95"}],
        "products": [{"product_id":"SKU-TEST","product_name":"Synthetic product","category":"Pantry","unit_price":"199.00","supplier_id":"SUP-TEST","lead_time_days":"5","reorder_threshold":"10","status":"active"}],
        "sales": [{"transaction_id":"TX-HIST","product_id":"SKU-TEST","quantity_sold":"2","transaction_date":"2026-11-24","revenue":"17.50"}],
        "inventory": [{"product_id":"SKU-TEST","available_stock":"10","reserved_stock":"1","updated_at":"2026-11-24T00:00:00Z"}],
    }
    with factory() as db:
        valid = validate_transform(frames, db)
        assert valid["accepted"]["sales"][0]["revenue"] == Decimal("17.50")
        frames["sales"][0]["revenue"] = "17.501"
        invalid = validate_transform(frames, db)
        assert invalid["accepted"]["sales"] == []
        assert invalid["rejected"]["sales"][0]["field"] == "revenue"
    engine.dispose()


def test_each_sale_total_uses_canonical_product_inr_price(tmp_path):
    output = tmp_path / "generated"
    generate(output, seed=42, products=100, days=180, reference_date=date(2026, 11, 24))
    product_prices = {row["product_id"]: Decimal(row["unit_price"]) for row in rows(output / "products.csv")}
    for sale in rows(output / "sales.csv"):
        assert Decimal(sale["revenue"]) == product_prices[sale["product_id"]] * int(sale["quantity_sold"])


def test_sqlite_migration_updates_money_atomically_and_preserves_operational_rows(tmp_path, monkeypatch):
    output = tmp_path / "generated"
    generate(output, seed=42, products=100, days=8, reference_date=date(2026, 11, 24))
    products = rows(output / "products.csv")
    db_path = tmp_path / "legacy.sqlite"
    engine = create_engine(URL.create("sqlite", database=str(db_path)))
    metadata = MetaData()
    suppliers = Table("suppliers", metadata,
                      Column("supplier_id", String(20), primary_key=True),
                      Column("supplier_name", String(150)), Column("lead_time_days", Integer), Column("reliability", Float))
    product_table = Table("products", metadata,
                          Column("product_id", String(20), primary_key=True), Column("product_name", String(200)),
                          Column("category", String(80)), Column("unit_price", Float),
                          Column("supplier_id", ForeignKey("suppliers.supplier_id")),
                          Column("lead_time_days", Integer), Column("reorder_threshold", Integer), Column("status", String(20)))
    sales = Table("sales", metadata,
                  Column("transaction_id", String(40), primary_key=True), Column("product_id", ForeignKey("products.product_id")),
                  Column("quantity_sold", Integer), Column("transaction_date", Date), Column("revenue", Float))
    inventory = Table("inventory", metadata,
                      Column("product_id", ForeignKey("products.product_id"), primary_key=True),
                      Column("available_stock", Integer), Column("reserved_stock", Integer))
    version = Table("alembic_version", metadata, Column("version_num", String(32), primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(suppliers.insert(), [{"supplier_id": f"SUP-{i:03}", "supplier_name": "Synthetic supplier",
                                                  "lead_time_days": 5, "reliability": .95} for i in range(1, 9)])
        connection.execute(product_table.insert(), [{"product_id": row["product_id"], "product_name": "Old demo name",
                                                      "category": row["category"], "unit_price": 4.5,
                                                      "supplier_id": f"SUP-{((int(row['product_id'][-4:])-1)%8)+1:03}",
                                                      "lead_time_days": 5, "reorder_threshold": 10, "status": "active"}
                                                     for row in products])
        connection.execute(sales.insert(), [{"transaction_id": f"TEST-{i:04}", "product_id": row["product_id"],
                                              "quantity_sold": i % 4 + 1, "transaction_date": date(2026, 11, 24),
                                              "revenue": 4.5 * (i % 4 + 1)} for i, row in enumerate(products)])
        connection.execute(inventory.insert(), [{"product_id": row["product_id"], "available_stock": 37, "reserved_stock": 2}
                                                 for row in products])
        connection.execute(version.insert().values(version_num="0005_rate_limit_events"))
        before_sales = connection.execute(text("SELECT transaction_id,product_id,quantity_sold,transaction_date FROM sales ORDER BY transaction_id")).all()
        before_inventory = connection.execute(text("SELECT product_id,available_stock,reserved_stock FROM inventory ORDER BY product_id")).all()

    monkeypatch.setenv("DATABASE_URL", URL.create("sqlite", database=str(db_path)).render_as_string())
    config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.set_main_option("sqlalchemy.url", URL.create("sqlite", database=str(db_path)).render_as_string())
    command.upgrade(config, "head")

    price_by_id = {row["product_id"]: Decimal(row["unit_price"]) for row in products}
    with engine.connect() as connection:
        after_sales = connection.execute(text("SELECT transaction_id,product_id,quantity_sold,transaction_date FROM sales ORDER BY transaction_id")).all()
        after_inventory = connection.execute(text("SELECT product_id,available_stock,reserved_stock FROM inventory ORDER BY product_id")).all()
        totals = connection.execute(text("SELECT transaction_id,product_id,quantity_sold,revenue FROM sales ORDER BY transaction_id")).all()
        changed_products = connection.execute(text("SELECT product_id,product_name,unit_price FROM products")).all()
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_native_inr_prices"
    assert after_sales == before_sales
    assert after_inventory == before_inventory
    assert len(changed_products) == len(products)
    mismatches = [(transaction_id, product_id, str(revenue), str(price_by_id[product_id] * quantity))
                  for transaction_id, product_id, quantity, revenue in totals
                  if Decimal(str(revenue)) != price_by_id[product_id] * quantity]
    assert not mismatches, mismatches[:5]
    assert {column["name"]: str(column["type"]).upper() for table in ("products", "sales") for column in inspect(engine).get_columns(table)}["unit_price"].startswith("NUMERIC(14, 2)")
    assert any(column["name"] == "revenue" and str(column["type"]).upper().startswith("NUMERIC(14, 2)") for column in inspect(engine).get_columns("sales"))
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()

