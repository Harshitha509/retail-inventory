"""Convert catalogue and sales money to approved native synthetic INR.

This revision is intentionally non-reversible: historical source amounts are
replaced by the approved INR catalogue. Restore a verified pre-migration backup
instead of downgrading this migration.
"""
from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "0006_native_inr_prices"
down_revision = "0005_rate_limit_events"
branch_labels = None
depends_on = None

# Generated from docs/native-inr-pricing-map.csv (seed 42, 100 products).
# Values remain in the migration so it is self-contained in the backend image.
PRICING = (
    ('SKU-0001', 'Premium Basmati Rice – 1 kg', 'Pantry', '129.00'),
    ('SKU-0002', 'Assam Black Tea – 250 g', 'Beverages', '149.00'),
    ('SKU-0003', 'Everyday Laundry Detergent – 500 g', 'Home care', '79.00'),
    ('SKU-0004', 'Moisturising Bath Soap – 100 g', 'Personal care', '35.00'),
    ('SKU-0005', 'Salted Biscuits – 100 g', 'Snacks', '25.00'),
    ('SKU-0006', 'Premium Basmati Rice – 5 kg', 'Pantry', '499.00'),
    ('SKU-0007', 'Assam Black Tea – 500 g', 'Beverages', '249.00'),
    ('SKU-0008', 'Everyday Laundry Detergent – 2 kg', 'Home care', '249.00'),
    ('SKU-0009', 'Moisturising Bath Soap – Pack of 4', 'Personal care', '149.00'),
    ('SKU-0010', 'Salted Biscuits – 200 g', 'Snacks', '39.00'),
    ('SKU-0011', 'Everyday Sona Masoori Rice – 5 kg', 'Pantry', '349.00'),
    ('SKU-0012', 'Premium Green Tea – 100 g', 'Beverages', '119.00'),
    ('SKU-0013', 'Concentrated Liquid Detergent – 1 L', 'Home care', '199.00'),
    ('SKU-0014', 'Herbal Shampoo – 180 ml', 'Personal care', '139.00'),
    ('SKU-0015', 'Oat Cookies – 150 g', 'Snacks', '89.00'),
    ('SKU-0016', 'Everyday Sona Masoori Rice – 10 kg', 'Pantry', '649.00'),
    ('SKU-0017', 'Premium Green Tea – 250 g', 'Beverages', '249.00'),
    ('SKU-0018', 'Concentrated Liquid Detergent – 2 L', 'Home care', '449.00'),
    ('SKU-0019', 'Herbal Shampoo – 340 ml', 'Personal care', '229.00'),
    ('SKU-0020', 'Chocolate Cookies – 200 g', 'Snacks', '79.00'),
    ('SKU-0021', 'Whole Wheat Atta – 1 kg', 'Pantry', '55.00'),
    ('SKU-0022', 'Instant Coffee – 50 g', 'Beverages', '99.00'),
    ('SKU-0023', 'Dishwashing Bar – 250 g', 'Home care', '25.00'),
    ('SKU-0024', 'Nourishing Conditioner – 180 ml', 'Personal care', '149.00'),
    ('SKU-0025', 'Classic Mixture Namkeen – 200 g', 'Snacks', '69.00'),
    ('SKU-0026', 'Whole Wheat Atta – 5 kg', 'Pantry', '279.00'),
    ('SKU-0027', 'Instant Coffee – 100 g', 'Beverages', '239.00'),
    ('SKU-0028', 'Dishwashing Liquid – 500 ml', 'Home care', '99.00'),
    ('SKU-0029', 'Fluoride Toothpaste – 100 g', 'Personal care', '79.00'),
    ('SKU-0030', 'Classic Mixture Namkeen – 400 g', 'Snacks', '119.00'),
    ('SKU-0031', 'Whole Wheat Atta – 10 kg', 'Pantry', '449.00'),
    ('SKU-0032', 'Filter Coffee Powder – 200 g', 'Beverages', '169.00'),
    ('SKU-0033', 'Dishwashing Liquid – 1 L', 'Home care', '179.00'),
    ('SKU-0034', 'Fluoride Toothpaste – 200 g', 'Personal care', '129.00'),
    ('SKU-0035', 'Potato Chips – 50 g', 'Snacks', '30.00'),
    ('SKU-0036', 'Toor Dal – 500 g', 'Pantry', '69.00'),
    ('SKU-0037', 'Filter Coffee Powder – 500 g', 'Beverages', '399.00'),
    ('SKU-0038', 'Floor Cleaner – 500 ml', 'Home care', '99.00'),
    ('SKU-0039', 'Soft Bristle Toothbrush – Pack of 2', 'Personal care', '89.00'),
    ('SKU-0040', 'Potato Chips – 150 g', 'Snacks', '59.00'),
    ('SKU-0041', 'Toor Dal – 1 kg', 'Pantry', '139.00'),
    ('SKU-0042', 'Mango Fruit Drink – 200 ml', 'Beverages', '25.00'),
    ('SKU-0043', 'Floor Cleaner – 1 L', 'Home care', '199.00'),
    ('SKU-0044', 'Daily Moisturising Lotion – 100 ml', 'Personal care', '149.00'),
    ('SKU-0045', 'Roasted Chana – 200 g', 'Snacks', '49.00'),
    ('SKU-0046', 'Chana Dal – 1 kg', 'Pantry', '109.00'),
    ('SKU-0047', 'Mango Fruit Drink – 1 L', 'Beverages', '99.00'),
    ('SKU-0048', 'Multipurpose Surface Cleaner – 500 ml', 'Home care', '139.00'),
    ('SKU-0049', 'Daily Moisturising Lotion – 400 ml', 'Personal care', '449.00'),
    ('SKU-0050', 'Roasted Peanuts – 200 g', 'Snacks', '59.00'),
    ('SKU-0051', 'Moong Dal – 1 kg', 'Pantry', '129.00'),
    ('SKU-0052', 'Mixed Fruit Juice – 1 L', 'Beverages', '129.00'),
    ('SKU-0053', 'Toilet Cleaner – 500 ml', 'Home care', '129.00'),
    ('SKU-0054', 'Aloe Vera Gel – 100 g', 'Personal care', '119.00'),
    ('SKU-0055', 'Milk Chocolate Bar – 40 g', 'Snacks', '55.00'),
    ('SKU-0056', 'Sunflower Oil – 1 L', 'Pantry', '139.00'),
    ('SKU-0057', 'Sparkling Lemon Drink – 500 ml', 'Beverages', '45.00'),
    ('SKU-0058', 'Toilet Cleaner – 1 L', 'Home care', '169.00'),
    ('SKU-0059', 'Aloe Vera Gel – 300 g', 'Personal care', '229.00'),
    ('SKU-0060', 'Assorted Toffees – 200 g', 'Snacks', '89.00'),
    ('SKU-0061', 'Sunflower Oil – 5 L', 'Pantry', '649.00'),
    ('SKU-0062', 'Sparkling Lemon Drink – 2 L', 'Beverages', '89.00'),
    ('SKU-0063', 'Room Freshener – 250 ml', 'Home care', '169.00'),
    ('SKU-0064', 'Everyday Deodorant – 150 ml', 'Personal care', '149.00'),
    ('SKU-0065', 'Murukku – 200 g', 'Snacks', '69.00'),
    ('SKU-0066', 'Groundnut Oil – 1 L', 'Pantry', '179.00'),
    ('SKU-0067', 'Packaged Drinking Water – 1 L', 'Beverages', '25.00'),
    ('SKU-0068', 'Room Freshener – 400 ml', 'Home care', '249.00'),
    ('SKU-0069', 'Shaving Cream – 100 g', 'Personal care', '69.00'),
    ('SKU-0070', 'Banana Chips – 200 g', 'Snacks', '79.00'),
    ('SKU-0071', 'Refined Sugar – 1 kg', 'Pantry', '55.00'),
    ('SKU-0072', 'Packaged Drinking Water – 2 L', 'Beverages', '40.00'),
    ('SKU-0073', 'Mosquito Repellent Refill – 45 ml', 'Home care', '79.00'),
    ('SKU-0074', 'Razor with 2 Blades – Pack of 1', 'Personal care', '99.00'),
    ('SKU-0075', 'Roasted Makhana – 100 g', 'Snacks', '99.00'),
    ('SKU-0076', 'Refined Sugar – 5 kg', 'Pantry', '219.00'),
    ('SKU-0077', 'Malted Cocoa Drink – 200 g', 'Beverages', '119.00'),
    ('SKU-0078', 'Household Insect Spray – 400 ml', 'Home care', '229.00'),
    ('SKU-0079', 'Sanitary Pads – Pack of 7', 'Personal care', '79.00'),
    ('SKU-0080', 'Trail Mix – 200 g', 'Snacks', '179.00'),
    ('SKU-0081', 'Turmeric Powder – 200 g', 'Pantry', '49.00'),
    ('SKU-0082', 'Malted Cocoa Drink – 500 g', 'Beverages', '299.00'),
    ('SKU-0083', 'Fabric Conditioner – 800 ml', 'Home care', '169.00'),
    ('SKU-0084', 'Sanitary Pads – Pack of 30', 'Personal care', '229.00'),
    ('SKU-0085', 'Whole Wheat Crackers – 150 g', 'Snacks', '69.00'),
    ('SKU-0086', 'Garam Masala – 100 g', 'Pantry', '79.00'),
    ('SKU-0087', 'Tender Coconut Water – 200 ml', 'Beverages', '49.00'),
    ('SKU-0088', 'Microfibre Cleaning Cloth – Pack of 2', 'Home care', '149.00'),
    ('SKU-0089', 'Gentle Baby Wash – 200 ml', 'Personal care', '169.00'),
    ('SKU-0090', 'Granola Snack Bar – Pack of 6', 'Snacks', '99.00'),
    ('SKU-0091', 'Poha – 1 kg', 'Pantry', '59.00'),
    ('SKU-0092', 'Rose Sharbat – 750 ml', 'Beverages', '119.00'),
    ('SKU-0093', 'Scrub Sponge – Pack of 3', 'Home care', '59.00'),
    ('SKU-0094', 'Moisturising Hand Wash – 250 ml', 'Personal care', '99.00'),
    ('SKU-0095', 'Ready-to-Cook Popcorn – Pack of 3', 'Snacks', '89.00'),
    ('SKU-0096', 'Besan – 1 kg', 'Pantry', '109.00'),
    ('SKU-0097', 'Lemon Iced Tea Mix – 250 g', 'Beverages', '169.00'),
    ('SKU-0098', 'Garbage Bags – Pack of 30', 'Home care', '149.00'),
    ('SKU-0099', 'Sunscreen Lotion SPF 30 – 100 ml', 'Personal care', '349.00'),
    ('SKU-0100', 'Masala Peanuts – 200 g', 'Snacks', '69.00'),
)


def upgrade():
    bind = op.get_bind()
    expected = {row[0] for row in PRICING}
    actual = set(bind.execute(sa.text("SELECT product_id FROM products")).scalars())
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        raise RuntimeError(f"Product catalogue does not match approved INR map (missing={len(missing)}, extra={len(extra)}); no pricing updates applied")

    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.alter_column("products", "unit_price", existing_type=sa.Float(), type_=sa.Numeric(14, 2),
                        postgresql_using="unit_price::numeric(14,2)")
        op.alter_column("sales", "revenue", existing_type=sa.Float(), type_=sa.Numeric(14, 2),
                        postgresql_using="revenue::numeric(14,2)")
    elif dialect == "sqlite":
        with op.batch_alter_table("products", recreate="always") as batch:
            batch.alter_column("unit_price", existing_type=sa.Float(), type_=sa.Numeric(14, 2))
        with op.batch_alter_table("sales", recreate="always") as batch:
            batch.alter_column("revenue", existing_type=sa.Float(), type_=sa.Numeric(14, 2))
    else:
        raise RuntimeError(f"Unsupported database dialect for native INR migration: {dialect}")

    product_updates = [{"product_id": product_id, "product_name": name, "category": category,
                        "unit_price": Decimal(price).quantize(Decimal("0.01"))}
                       for product_id, name, category, price in PRICING]
    update_products = sa.text("""UPDATE products SET product_name=:product_name, category=:category,
                                unit_price=:unit_price WHERE product_id=:product_id""").bindparams(
        sa.bindparam("unit_price", type_=sa.Numeric(14, 2)))
    bind.execute(update_products, product_updates)
    if dialect == "postgresql":
        bind.execute(sa.text("""UPDATE sales AS s SET revenue=ROUND(s.quantity_sold * p.unit_price, 2)
                              FROM products AS p WHERE p.product_id=s.product_id"""))
    else:
        bind.execute(sa.text("""UPDATE sales SET revenue=ROUND(quantity_sold *
                              (SELECT unit_price FROM products WHERE products.product_id=sales.product_id), 2)
                              WHERE EXISTS (SELECT 1 FROM products WHERE products.product_id=sales.product_id)"""))


def downgrade():
    raise NotImplementedError("Native INR repricing cannot be reversed safely; restore a verified pre-migration backup into a separate database.")
