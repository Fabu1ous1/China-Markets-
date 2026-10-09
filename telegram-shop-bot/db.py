import aiosqlite

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    age_ok INTEGER NOT NULL DEFAULT 0,
    promo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flavors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    in_stock INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cart (
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    flavor_id INTEGER NOT NULL DEFAULT 0,
    qty INTEGER NOT NULL,
    PRIMARY KEY (user_id, product_id, flavor_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    address TEXT NOT NULL,
    delivery TEXT NOT NULL,
    payment TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    subtotal REAL NOT NULL DEFAULT 0,
    discount REAL NOT NULL DEFAULT 0,
    promo TEXT NOT NULL DEFAULT '',
    total REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL DEFAULT 0,
    product_name TEXT NOT NULL,
    flavor_name TEXT NOT NULL DEFAULT '',
    price REAL NOT NULL,
    qty INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS price_tiers (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    min_qty INTEGER NOT NULL,
    price REAL NOT NULL,
    PRIMARY KEY (product_id, min_qty)
);
CREATE TABLE IF NOT EXISTS promos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,              -- 'percent' | 'fixed'
    value REAL NOT NULL,
    max_uses INTEGER NOT NULL DEFAULT 0,  -- 0 = без лимита
    uses INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS promo_uses (
    promo_id INTEGER NOT NULL REFERENCES promos(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (promo_id, user_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, product_id, order_id)
);
CREATE TABLE IF NOT EXISTS stock_waits (
    user_id INTEGER NOT NULL,
    flavor_id INTEGER NOT NULL REFERENCES flavors(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, flavor_id)
);
"""

# Колонки, добавленные после первой версии, — докатываем на существующие базы
MIGRATIONS = [
    ("users", "promo", "promo TEXT NOT NULL DEFAULT ''"),
    ("orders", "subtotal", "subtotal REAL NOT NULL DEFAULT 0"),
    ("orders", "discount", "discount REAL NOT NULL DEFAULT 0"),
    ("orders", "promo", "promo TEXT NOT NULL DEFAULT ''"),
    ("orders", "region", "region TEXT NOT NULL DEFAULT ''"),
    ("order_items", "product_id", "product_id INTEGER NOT NULL DEFAULT 0"),
]


async def init(path: str) -> None:
    global _db
    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA foreign_keys = ON")
    await _db.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:
        cur = await _db.execute(f"PRAGMA table_info({table})")
        if column not in [r["name"] for r in await cur.fetchall()]:
            await _db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    await _db.commit()


async def close() -> None:
    if _db:
        await _db.close()


async def _all(sql: str, *args):
    cur = await _db.execute(sql, args)
    return await cur.fetchall()


async def _one(sql: str, *args):
    cur = await _db.execute(sql, args)
    return await cur.fetchone()


async def _run(sql: str, *args) -> int:
    cur = await _db.execute(sql, args)
    await _db.commit()
    return cur.lastrowid


# ---------- users ----------

async def upsert_user(uid: int, username: str | None, full_name: str) -> None:
    await _run(
        "INSERT INTO users (id, username, full_name) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
        uid, username, full_name,
    )


async def is_age_ok(uid: int) -> bool:
    row = await _one("SELECT age_ok FROM users WHERE id=?", uid)
    return bool(row and row["age_ok"])


async def set_age_ok(uid: int) -> None:
    await _run("UPDATE users SET age_ok=1 WHERE id=?", uid)


async def all_user_ids() -> list[int]:
    return [r["id"] for r in await _all("SELECT id FROM users WHERE age_ok=1")]


# ---------- categories ----------

async def categories():
    return await _all("SELECT * FROM categories ORDER BY id")


async def categories_with_products():
    return await _all(
        "SELECT c.* FROM categories c WHERE EXISTS "
        "(SELECT 1 FROM products p WHERE p.category_id=c.id AND p.active=1) ORDER BY c.id"
    )


async def add_category(name: str) -> int:
    return await _run("INSERT INTO categories (name) VALUES (?)", name)


async def delete_category(cid: int) -> bool:
    if await _one("SELECT 1 FROM products WHERE category_id=?", cid):
        return False
    await _run("DELETE FROM categories WHERE id=?", cid)
    return True


# ---------- products ----------

async def products(category_id: int | None = None, only_active: bool = True):
    sql = "SELECT * FROM products WHERE 1=1"
    args = []
    if category_id is not None:
        sql += " AND category_id=?"
        args.append(category_id)
    if only_active:
        sql += " AND active=1"
    return await _all(sql + " ORDER BY id", *args)


async def product(pid: int):
    return await _one("SELECT * FROM products WHERE id=?", pid)


async def add_product(category_id: int, name: str, description: str, price: float,
                      media: list[tuple[str, str]], flavors: list[str],
                      tiers: list[tuple[int, float]] = ()) -> int:
    cur = await _db.execute(
        "INSERT INTO products (category_id, name, description, price) VALUES (?, ?, ?, ?)",
        (category_id, name, description, price),
    )
    pid = cur.lastrowid
    await _db.executemany(
        "INSERT INTO product_media (product_id, file_id, kind) VALUES (?, ?, ?)",
        [(pid, fid, kind) for fid, kind in media],
    )
    await _db.executemany(
        "INSERT INTO flavors (product_id, name) VALUES (?, ?)", [(pid, f) for f in flavors]
    )
    await _db.executemany(
        "INSERT INTO price_tiers (product_id, min_qty, price) VALUES (?, ?, ?)",
        [(pid, q, pr) for q, pr in tiers],
    )
    await _db.commit()
    return pid


async def update_product(pid: int, field: str, value) -> None:
    assert field in {"name", "description", "price", "active"}
    await _run(f"UPDATE products SET {field}=? WHERE id=?", value, pid)


async def delete_product(pid: int) -> None:
    await _run("DELETE FROM products WHERE id=?", pid)


async def media(pid: int):
    return await _all("SELECT * FROM product_media WHERE product_id=? ORDER BY id", pid)


async def replace_media(pid: int, items: list[tuple[str, str]]) -> None:
    await _db.execute("DELETE FROM product_media WHERE product_id=?", (pid,))
    await _db.executemany(
        "INSERT INTO product_media (product_id, file_id, kind) VALUES (?, ?, ?)",
        [(pid, fid, kind) for fid, kind in items],
    )
    await _db.commit()


# ---------- flavors ----------

async def flavors(pid: int):
    return await _all("SELECT * FROM flavors WHERE product_id=? ORDER BY id", pid)


async def flavor(fid: int):
    return await _one("SELECT * FROM flavors WHERE id=?", fid)


async def add_flavors(pid: int, names: list[str]) -> None:
    await _db.executemany(
        "INSERT INTO flavors (product_id, name) VALUES (?, ?)", [(pid, n) for n in names]
    )
    await _db.commit()


async def toggle_flavor(fid: int) -> bool:
    """Переключает наличие, возвращает True, если вкус снова появился."""
    await _run("UPDATE flavors SET in_stock = 1 - in_stock WHERE id=?", fid)
    return bool((await flavor(fid))["in_stock"])


async def delete_flavor(fid: int) -> None:
    await _db.execute("DELETE FROM cart WHERE flavor_id=?", (fid,))
    await _run("DELETE FROM flavors WHERE id=?", fid)


# ---------- cart ----------

async def cart_add(uid: int, pid: int, fid: int, qty: int) -> None:
    await _run(
        "INSERT INTO cart (user_id, product_id, flavor_id, qty) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, product_id, flavor_id) DO UPDATE SET qty = MIN(qty + excluded.qty, 999)",
        uid, pid, fid, qty,
    )


async def cart_items(uid: int):
    return await _all(
        "SELECT c.product_id, c.flavor_id, c.qty, p.name, p.price, p.active, "
        "COALESCE(f.name, '') AS flavor, COALESCE(f.in_stock, CASE WHEN c.flavor_id=0 THEN 1 ELSE 0 END) AS in_stock "
        "FROM cart c JOIN products p ON p.id=c.product_id "
        "LEFT JOIN flavors f ON f.id=c.flavor_id WHERE c.user_id=? ORDER BY p.name",
        uid,
    )


async def cart_remove(uid: int, pid: int, fid: int) -> None:
    await _run("DELETE FROM cart WHERE user_id=? AND product_id=? AND flavor_id=?", uid, pid, fid)


async def cart_clear(uid: int) -> None:
    await _run("DELETE FROM cart WHERE user_id=?", uid)


async def cart_drop_unavailable(uid: int) -> list[str]:
    """Убирает из корзины скрытые товары и закончившиеся вкусы, возвращает их названия."""
    dropped = []
    for it in await cart_items(uid):
        if not it["active"] or not it["in_stock"]:
            dropped.append(f"{it['name']} {it['flavor']}".strip())
            await cart_remove(uid, it["product_id"], it["flavor_id"])
    return dropped


# ---------- orders ----------

async def create_order(uid: int, d: dict, summary: dict) -> int | None:
    """summary — результат pricing.cart_summary: цены уже с учётом опта и промокода."""
    items = [it for it in summary["items"] if it["ok"]]
    if not items:
        return None
    promo = summary["promo"]
    status = "quote" if summary["quote"] else "new"  # quote — ждёт цену от админа
    cur = await _db.execute(
        "INSERT INTO orders (user_id, customer_name, phone, address, region, delivery, payment, comment, "
        "subtotal, discount, promo, total, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, d["name"], d["phone"], d["address"], d.get("region", ""), d["delivery"], d["payment"],
         d.get("comment", ""), summary["subtotal"], summary["discount"], promo["code"] if promo else "",
         summary["total"], status),
    )
    oid = cur.lastrowid
    await _db.executemany(
        "INSERT INTO order_items (order_id, product_id, product_name, flavor_name, price, qty) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(oid, it["product_id"], it["name"], it["flavor"], 0 if it["hidden"] else it["unit"], it["qty"])
         for it in items],
    )
    await _db.execute("DELETE FROM cart WHERE user_id=?", (uid,))
    if promo:
        await _db.execute("UPDATE promos SET uses = uses + 1 WHERE id=?", (promo["id"],))
        await _db.execute("INSERT OR IGNORE INTO promo_uses (promo_id, user_id) VALUES (?, ?)", (promo["id"], uid))
        await _db.execute("UPDATE users SET promo='' WHERE id=?", (uid,))
    await _db.commit()
    return oid


async def order(oid: int):
    return await _one(
        "SELECT o.*, u.username, u.full_name AS tg_name FROM orders o "
        "LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?",
        oid,
    )


async def order_items(oid: int):
    return await _all("SELECT * FROM order_items WHERE order_id=? ORDER BY id", oid)


async def user_orders(uid: int, limit: int = 10):
    return await _all("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?", uid, limit)


async def last_order(uid: int):
    return await _one("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", uid)


async def active_orders(limit: int = 30):
    return await _all(
        "SELECT * FROM orders WHERE status IN ('quote', 'priced', 'new', 'accepted', 'shipped') "
        "ORDER BY id DESC LIMIT ?", limit
    )


async def set_order_status(oid: int, status: str) -> None:
    await _run("UPDATE orders SET status=? WHERE id=?", status, oid)


async def set_order_price(oid: int, total: float) -> None:
    await _run("UPDATE orders SET subtotal=?, discount=0, total=?, status='priced' WHERE id=?", total, total, oid)


SOLD = "status IN ('new', 'accepted', 'shipped', 'delivered')"  # заявки без цены и отмены не считаем


async def stats(period: str | None):
    where = SOLD
    if period:
        where += f" AND created_at >= datetime('now', '{period}')"
    row = await _one(f"SELECT COUNT(*) AS cnt, COALESCE(SUM(total), 0) AS revenue FROM orders WHERE {where}")
    return row["cnt"], row["revenue"]


async def top_products(limit: int = 5):
    return await _all(
        "SELECT oi.product_name, oi.flavor_name, SUM(oi.qty) AS qty, SUM(oi.qty * oi.price) AS revenue "
        f"FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE o.{SOLD} "
        "GROUP BY oi.product_name, oi.flavor_name ORDER BY qty DESC LIMIT ?",
        limit,
    )


async def users_count() -> int:
    return (await _one("SELECT COUNT(*) AS c FROM users"))["c"]


# ---------- оптовые цены ----------

async def tiers(pid: int):
    return await _all("SELECT min_qty, price FROM price_tiers WHERE product_id=? ORDER BY min_qty", pid)


async def set_tiers(pid: int, items: list[tuple[int, float]]) -> None:
    await _db.execute("DELETE FROM price_tiers WHERE product_id=?", (pid,))
    await _db.executemany(
        "INSERT INTO price_tiers (product_id, min_qty, price) VALUES (?, ?, ?)", [(pid, q, p) for q, p in items]
    )
    await _db.commit()


# ---------- промокоды ----------

async def promo(code: str):
    return await _one("SELECT * FROM promos WHERE code=?", code.upper())


async def promo_by_id(promo_id: int):
    return await _one("SELECT * FROM promos WHERE id=?", promo_id)


async def promos():
    return await _all("SELECT * FROM promos ORDER BY id DESC")


async def add_promo(code: str, kind: str, value: float, max_uses: int) -> None:
    await _run(
        "INSERT INTO promos (code, kind, value, max_uses) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(code) DO UPDATE SET kind=excluded.kind, value=excluded.value, "
        "max_uses=excluded.max_uses, active=1",
        code.upper(), kind, value, max_uses,
    )


async def toggle_promo(promo_id: int) -> None:
    await _run("UPDATE promos SET active = 1 - active WHERE id=?", promo_id)


async def delete_promo(promo_id: int) -> None:
    await _run("DELETE FROM promos WHERE id=?", promo_id)


async def promo_used_by(promo_id: int, uid: int) -> bool:
    return bool(await _one("SELECT 1 FROM promo_uses WHERE promo_id=? AND user_id=?", promo_id, uid))


async def user_promo(uid: int) -> str:
    row = await _one("SELECT promo FROM users WHERE id=?", uid)
    return row["promo"] if row else ""


async def set_user_promo(uid: int, code: str) -> None:
    await _run("UPDATE users SET promo=? WHERE id=?", code.upper(), uid)


# ---------- отзывы ----------

async def rating(pid: int) -> tuple[float, int]:
    row = await _one("SELECT AVG(rating) AS avg, COUNT(*) AS cnt FROM reviews WHERE product_id=?", pid)
    return (row["avg"] or 0.0), row["cnt"]


async def reviews(pid: int, limit: int = 10):
    return await _all(
        "SELECT r.*, u.full_name FROM reviews r LEFT JOIN users u ON u.id=r.user_id "
        "WHERE r.product_id=? ORDER BY r.id DESC LIMIT ?",
        pid, limit,
    )


async def latest_reviews(limit: int = 10):
    return await _all(
        "SELECT r.*, u.full_name, p.name AS product_name FROM reviews r "
        "LEFT JOIN users u ON u.id=r.user_id JOIN products p ON p.id=r.product_id "
        "ORDER BY r.id DESC LIMIT ?",
        limit,
    )


async def can_review(uid: int, oid: int, pid: int) -> bool:
    return bool(await _one(
        "SELECT 1 FROM orders o JOIN order_items oi ON oi.order_id=o.id "
        "WHERE o.id=? AND o.user_id=? AND o.status='delivered' AND oi.product_id=? "
        "AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.user_id=o.user_id AND r.order_id=o.id AND r.product_id=?)",
        oid, uid, pid, pid,
    ))


async def add_review(pid: int, uid: int, oid: int, rating_: int, text: str) -> int:
    return await _run(
        "INSERT INTO reviews (product_id, user_id, order_id, rating, text) VALUES (?, ?, ?, ?, ?)",
        pid, uid, oid, rating_, text,
    )


async def delete_review(rid: int) -> None:
    await _run("DELETE FROM reviews WHERE id=?", rid)


async def order_products(oid: int):
    """Товары заказа, которые ещё существуют в каталоге (для отзывов)."""
    return await _all(
        "SELECT DISTINCT p.id, p.name FROM order_items oi JOIN products p ON p.id=oi.product_id "
        "WHERE oi.order_id=?",
        oid,
    )


# ---------- «сообщить о поступлении» ----------

async def add_stock_wait(uid: int, fid: int) -> None:
    await _run("INSERT OR IGNORE INTO stock_waits (user_id, flavor_id) VALUES (?, ?)", uid, fid)


async def pop_stock_waiters(fid: int) -> list[int]:
    rows = await _all("SELECT user_id FROM stock_waits WHERE flavor_id=?", fid)
    await _run("DELETE FROM stock_waits WHERE flavor_id=?", fid)
    return [r["user_id"] for r in rows]
