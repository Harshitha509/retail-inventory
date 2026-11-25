"""Generate reproducible synthetic Indian retail inputs with native INR prices."""
from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class CatalogueOption:
    category: str
    subtype: str
    name: str
    pack_size: str
    price_points_inr: tuple[int, ...]


def _options(category: str, entries: list[tuple[str, str, str, tuple[int, ...]]]) -> tuple[CatalogueOption, ...]:
    return tuple(CatalogueOption(category, subtype, name, pack, prices) for subtype, name, pack, prices in entries)


# Prices are synthetic category/type/pack-size assumptions, not live market quotes.
# Each category has 20 explicit catalogue variants for the default 100-product run.
CATALOGUE: dict[str, tuple[CatalogueOption, ...]] = {
    "Pantry": _options("Pantry", [
        ("Rice", "Premium Basmati Rice", "1 kg", (119, 129, 149)),
        ("Rice", "Premium Basmati Rice", "5 kg", (499, 549, 599)),
        ("Rice", "Everyday Sona Masoori Rice", "5 kg", (299, 329, 349)),
        ("Rice", "Everyday Sona Masoori Rice", "10 kg", (579, 599, 649)),
        ("Flour", "Whole Wheat Atta", "1 kg", (49, 55, 59)),
        ("Flour", "Whole Wheat Atta", "5 kg", (229, 249, 279)),
        ("Flour", "Whole Wheat Atta", "10 kg", (449, 479, 499)),
        ("Pulses", "Toor Dal", "500 g", (69, 75, 79)),
        ("Pulses", "Toor Dal", "1 kg", (129, 139, 149)),
        ("Pulses", "Chana Dal", "1 kg", (89, 99, 109)),
        ("Pulses", "Moong Dal", "1 kg", (119, 129, 139)),
        ("Oil", "Sunflower Oil", "1 L", (139, 149, 159)),
        ("Oil", "Sunflower Oil", "5 L", (649, 699, 749)),
        ("Oil", "Groundnut Oil", "1 L", (179, 199, 219)),
        ("Sugar", "Refined Sugar", "1 kg", (45, 49, 55)),
        ("Sugar", "Refined Sugar", "5 kg", (219, 239, 249)),
        ("Spices", "Turmeric Powder", "200 g", (49, 59, 69)),
        ("Spices", "Garam Masala", "100 g", (69, 79, 89)),
        ("Staples", "Poha", "1 kg", (59, 69, 79)),
        ("Staples", "Besan", "1 kg", (89, 99, 109)),
    ]),
    "Beverages": _options("Beverages", [
        ("Tea", "Assam Black Tea", "250 g", (119, 139, 149)),
        ("Tea", "Assam Black Tea", "500 g", (229, 249, 279)),
        ("Tea", "Premium Green Tea", "100 g", (119, 139, 159)),
        ("Tea", "Premium Green Tea", "250 g", (249, 279, 299)),
        ("Coffee", "Instant Coffee", "50 g", (99, 119, 129)),
        ("Coffee", "Instant Coffee", "100 g", (199, 219, 239)),
        ("Coffee", "Filter Coffee Powder", "200 g", (149, 169, 189)),
        ("Coffee", "Filter Coffee Powder", "500 g", (349, 379, 399)),
        ("Juice", "Mango Fruit Drink", "200 ml", (20, 25, 30)),
        ("Juice", "Mango Fruit Drink", "1 L", (99, 119, 129)),
        ("Juice", "Mixed Fruit Juice", "1 L", (129, 149, 169)),
        ("Soft drink", "Sparkling Lemon Drink", "500 ml", (35, 40, 45)),
        ("Soft drink", "Sparkling Lemon Drink", "2 L", (89, 99, 109)),
        ("Water", "Packaged Drinking Water", "1 L", (20, 20, 25)),
        ("Water", "Packaged Drinking Water", "2 L", (30, 35, 40)),
        ("Health drink", "Malted Cocoa Drink", "200 g", (119, 139, 149)),
        ("Health drink", "Malted Cocoa Drink", "500 g", (279, 299, 329)),
        ("Coconut water", "Tender Coconut Water", "200 ml", (35, 40, 49)),
        ("Syrup", "Rose Sharbat", "750 ml", (99, 119, 129)),
        ("Beverage mix", "Lemon Iced Tea Mix", "250 g", (149, 169, 189)),
    ]),
    "Home care": _options("Home care", [
        ("Laundry", "Everyday Laundry Detergent", "500 g", (69, 79, 89)),
        ("Laundry", "Everyday Laundry Detergent", "2 kg", (249, 279, 299)),
        ("Laundry", "Concentrated Liquid Detergent", "1 L", (199, 229, 249)),
        ("Laundry", "Concentrated Liquid Detergent", "2 L", (379, 429, 449)),
        ("Dish care", "Dishwashing Bar", "250 g", (25, 30, 35)),
        ("Dish care", "Dishwashing Liquid", "500 ml", (99, 119, 129)),
        ("Dish care", "Dishwashing Liquid", "1 L", (179, 199, 219)),
        ("Surface care", "Floor Cleaner", "500 ml", (99, 119, 129)),
        ("Surface care", "Floor Cleaner", "1 L", (179, 199, 229)),
        ("Surface care", "Multipurpose Surface Cleaner", "500 ml", (119, 139, 149)),
        ("Toilet care", "Toilet Cleaner", "500 ml", (99, 119, 129)),
        ("Toilet care", "Toilet Cleaner", "1 L", (169, 189, 209)),
        ("Air care", "Room Freshener", "250 ml", (149, 169, 199)),
        ("Air care", "Room Freshener", "400 ml", (229, 249, 279)),
        ("Pest control", "Mosquito Repellent Refill", "45 ml", (79, 89, 99)),
        ("Pest control", "Household Insect Spray", "400 ml", (179, 199, 229)),
        ("Laundry", "Fabric Conditioner", "800 ml", (149, 169, 189)),
        ("Cleaning tools", "Microfibre Cleaning Cloth", "Pack of 2", (99, 119, 149)),
        ("Cleaning tools", "Scrub Sponge", "Pack of 3", (39, 49, 59)),
        ("Storage", "Garbage Bags", "Pack of 30", (99, 129, 149)),
    ]),
    "Personal care": _options("Personal care", [
        ("Bath", "Moisturising Bath Soap", "100 g", (35, 40, 49)),
        ("Bath", "Moisturising Bath Soap", "Pack of 4", (129, 149, 169)),
        ("Hair care", "Herbal Shampoo", "180 ml", (119, 139, 149)),
        ("Hair care", "Herbal Shampoo", "340 ml", (229, 249, 279)),
        ("Hair care", "Nourishing Conditioner", "180 ml", (149, 169, 189)),
        ("Oral care", "Fluoride Toothpaste", "100 g", (69, 79, 89)),
        ("Oral care", "Fluoride Toothpaste", "200 g", (129, 149, 159)),
        ("Oral care", "Soft Bristle Toothbrush", "Pack of 2", (89, 99, 119)),
        ("Skin care", "Daily Moisturising Lotion", "100 ml", (129, 149, 169)),
        ("Skin care", "Daily Moisturising Lotion", "400 ml", (349, 399, 449)),
        ("Skin care", "Aloe Vera Gel", "100 g", (99, 119, 139)),
        ("Skin care", "Aloe Vera Gel", "300 g", (229, 249, 279)),
        ("Deodorant", "Everyday Deodorant", "150 ml", (149, 169, 199)),
        ("Shaving", "Shaving Cream", "100 g", (69, 79, 89)),
        ("Shaving", "Razor with 2 Blades", "Pack of 1", (99, 119, 139)),
        ("Feminine care", "Sanitary Pads", "Pack of 7", (59, 69, 79)),
        ("Feminine care", "Sanitary Pads", "Pack of 30", (229, 249, 279)),
        ("Baby care", "Gentle Baby Wash", "200 ml", (149, 169, 189)),
        ("Hand care", "Moisturising Hand Wash", "250 ml", (89, 99, 119)),
        ("Skin care", "Sunscreen Lotion SPF 30", "100 ml", (299, 349, 399)),
    ]),
    "Snacks": _options("Snacks", [
        ("Biscuits", "Salted Biscuits", "100 g", (20, 25, 30)),
        ("Biscuits", "Salted Biscuits", "200 g", (39, 45, 49)),
        ("Biscuits", "Oat Cookies", "150 g", (69, 79, 89)),
        ("Biscuits", "Chocolate Cookies", "200 g", (79, 89, 99)),
        ("Namkeen", "Classic Mixture Namkeen", "200 g", (49, 59, 69)),
        ("Namkeen", "Classic Mixture Namkeen", "400 g", (99, 119, 129)),
        ("Chips", "Potato Chips", "50 g", (20, 25, 30)),
        ("Chips", "Potato Chips", "150 g", (49, 59, 69)),
        ("Snack mix", "Roasted Chana", "200 g", (39, 49, 59)),
        ("Snack mix", "Roasted Peanuts", "200 g", (49, 59, 69)),
        ("Confectionery", "Milk Chocolate Bar", "40 g", (40, 49, 55)),
        ("Confectionery", "Assorted Toffees", "200 g", (79, 89, 99)),
        ("Traditional snacks", "Murukku", "200 g", (59, 69, 79)),
        ("Traditional snacks", "Banana Chips", "200 g", (69, 79, 89)),
        ("Healthy snacks", "Roasted Makhana", "100 g", (99, 119, 139)),
        ("Healthy snacks", "Trail Mix", "200 g", (179, 199, 229)),
        ("Crackers", "Whole Wheat Crackers", "150 g", (49, 59, 69)),
        ("Cereal", "Granola Snack Bar", "Pack of 6", (99, 119, 139)),
        ("Popcorn", "Ready-to-Cook Popcorn", "Pack of 3", (79, 89, 99)),
        ("Snack mix", "Masala Peanuts", "200 g", (49, 59, 69)),
    ]),
}

CATEGORY_ORDER = ("Pantry", "Beverages", "Home care", "Personal care", "Snacks")
PRICE_MAP_COLUMNS = ("product_id", "product_name", "category", "subtype", "pack_size", "unit_price_inr")


def generate(out: Path, seed: int = 42, products: int = 100, days: int = 180,
             reference_date: date | None = None) -> dict[str, int]:
    if products < 1 or days < 1:
        raise ValueError("products and days must be positive")
    rng = random.Random(seed)
    price_rng = random.Random(seed ^ 0x494E52)
    out.mkdir(parents=True, exist_ok=True)
    reference_date = reference_date or date.today()
    first_sales_date = reference_date - timedelta(days=days - 1)
    suppliers = [{"supplier_id": f"SUP-{i:03}", "supplier_name": f"Northstar Supply {i}",
                  "lead_time_days": 4 + i * 2, "reliability": round(rng.uniform(.88, .995), 3)}
                 for i in range(1, 9)]
    products_rows: list[dict[str, object]] = []
    inventory_rows: list[dict[str, object]] = []
    sales_rows: list[dict[str, object]] = []
    map_rows: list[dict[str, object]] = []
    category_offsets = {category: 0 for category in CATEGORY_ORDER}
    for i in range(1, products + 1):
        category = CATEGORY_ORDER[(i - 1) % len(CATEGORY_ORDER)]
        occurrence = category_offsets[category]
        category_offsets[category] += 1
        choices = CATALOGUE[category]
        option = choices[occurrence % len(choices)]
        # Preserve the former single price draw in the legacy stream so all later
        # inventory and sales randomness stays stable. A separate stream makes the
        # catalogue independent of the number of generated sales-history days.
        rng.random()
        draw = price_rng.random()
        price = option.price_points_inr[int(draw * len(option.price_points_inr))]
        product_id = f"SKU-{i:04}"
        product_name = f"{option.name} – {option.pack_size}"
        lead = suppliers[(i - 1) % len(suppliers)]["lead_time_days"]
        supplier_id = suppliers[(i - 1) % len(suppliers)]["supplier_id"]
        products_rows.append({"product_id": product_id, "product_name": product_name,
                              "category": category, "unit_price": f"{price:.2f}",
                              "supplier_id": supplier_id, "lead_time_days": lead,
                              "reorder_threshold": max(5, lead * 2), "status": "active"})
        map_rows.append({"product_id": product_id, "product_name": product_name, "category": category,
                         "subtype": option.subtype, "pack_size": option.pack_size,
                         "unit_price_inr": f"{price:.2f}"})
        inventory_rows.append({"product_id": product_id, "available_stock": rng.randint(20, 500),
                               "reserved_stock": rng.randint(0, 15),
                               "updated_at": reference_date.isoformat() + "T00:00:00Z"})
        for day_offset in range(days):
            quantity = max(0, round(rng.gauss(5, 3)))
            if quantity:
                sales_rows.append({"transaction_id": f"TX-{i:04}-{day_offset:04}",
                                   "product_id": product_id, "quantity_sold": quantity,
                                   "transaction_date": (first_sales_date + timedelta(days=day_offset)).isoformat(),
                                   "revenue": f"{quantity * price:.2f}"})

    outputs = (("products.csv", products_rows), ("inventory.csv", inventory_rows),
               ("sales.csv", sales_rows), ("pricing_map.csv", map_rows))
    for filename, rows in outputs:
        with (out / filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    with (out / "suppliers.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(suppliers[0]))
        writer.writeheader()
        writer.writerows(suppliers)
    return {"products": len(products_rows), "suppliers": len(suppliers),
            "sales": len(sales_rows), "inventory": len(inventory_rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--products", type=int, default=100)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=None,
                        help="Inclusive end date in YYYY-MM-DD format (defaults to today)")
    args = parser.parse_args()
    print(generate(Path(args.out), args.seed, args.products, args.days, args.reference_date))
