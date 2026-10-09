"""Расчёт цен: оптовая шкала (чем больше штук — тем дешевле) и промокоды."""
import db
from config import HIDE_PRICES


def price_hidden(p) -> bool:
    """Цена по запросу: глобально (HIDE_PRICES) или у товара цена не задана."""
    return HIDE_PRICES or not p["price"]


def unit_price(base: float, tiers, qty: int) -> float:
    """Цена за штуку при заданном количестве. tiers отсортированы по min_qty."""
    price = base
    for t in tiers:
        if qty >= t["min_qty"]:
            price = t["price"]
    return price


def next_tier(tiers, qty: int):
    """Ближайшая следующая ступень скидки или None."""
    for t in tiers:
        if t["min_qty"] > qty:
            return t
    return None


def calc_discount(promo, subtotal: float) -> float:
    if not promo:
        return 0.0
    d = subtotal * promo["value"] / 100 if promo["kind"] == "percent" else promo["value"]
    return round(min(d, subtotal), 2)


async def check_promo(code: str, uid: int):
    """Возвращает (promo, ошибка)."""
    p = await db.promo(code)
    if not p or not p["active"]:
        return None, "Промокод не найден или больше не действует"
    if p["max_uses"] and p["uses"] >= p["max_uses"]:
        return None, "Лимит использований этого промокода исчерпан"
    if await db.promo_used_by(p["id"], uid):
        return None, "Вы уже использовали этот промокод"
    return p, ""


async def cart_summary(uid: int) -> dict:
    """Корзина с итоговыми ценами.

    Оптовая цена считается по общему количеству одной модели в корзине —
    вкусы можно миксовать: 5 × Манго + 5 × Мята = цена «от 10 шт».
    """
    items = [dict(r) for r in await db.cart_items(uid)]
    for it in items:
        it["ok"] = bool(it["active"] and it["in_stock"])
        it["hidden"] = price_hidden(it)

    qty_by_product: dict[int, int] = {}
    for it in items:
        if it["ok"]:
            qty_by_product[it["product_id"]] = qty_by_product.get(it["product_id"], 0) + it["qty"]

    tiers_cache: dict[int, list] = {}
    for it in items:
        pid = it["product_id"]
        if pid not in tiers_cache:
            tiers_cache[pid] = await db.tiers(pid)
        it["unit"] = unit_price(it["price"], tiers_cache[pid], qty_by_product.get(pid, it["qty"]))
        it["line"] = round(it["unit"] * it["qty"], 2)

    ok = [it for it in items if it["ok"]]
    if any(it["hidden"] for it in ok):
        # Есть товар «цена по запросу» — итог назначит админ, промокод не тратим
        return {"items": items, "quote": True, "subtotal": 0, "wholesale_savings": 0, "promo": None,
                "promo_error": "", "discount": 0, "total": 0, "hints": []}
    subtotal = round(sum(it["line"] for it in ok), 2)
    full_price = round(sum(it["price"] * it["qty"] for it in ok), 2)

    promo, promo_error = None, ""
    code = await db.user_promo(uid)
    if code:
        promo, promo_error = await check_promo(code, uid)
        if not promo:
            await db.set_user_promo(uid, "")
    discount = calc_discount(promo, subtotal)

    # Подсказка «добавь ещё N шт. — будет дешевле»
    hints = []
    for pid, qty in qty_by_product.items():
        nt = next_tier(tiers_cache[pid], qty)
        if nt:
            name = next(it["name"] for it in ok if it["product_id"] == pid)
            hints.append((name, nt["min_qty"] - qty, nt["price"]))

    return {
        "items": items,
        "quote": False,
        "subtotal": subtotal,
        "wholesale_savings": round(full_price - subtotal, 2),
        "promo": promo,
        "promo_error": promo_error,
        "discount": discount,
        "total": round(subtotal - discount, 2),
        "hints": hints,
    }
