import html
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo

from config import CURRENCY, TZ_OFFSET_HOURS

STATUS = {
    "new": "🆕 Новый",
    "accepted": "✅ Принят",
    "shipped": "🚚 Отправлен",
    "delivered": "📬 Доставлен",
    "cancelled": "❌ Отменён",
}

STATUS_CLIENT_MSG = {
    "accepted": "✅ Ваш заказ #{id} принят и собирается!",
    "shipped": "🚚 Ваш заказ #{id} отправлен!",
    "delivered": "📬 Заказ #{id} доставлен. Спасибо за покупку! 🙌",
    "cancelled": "❌ Заказ #{id} отменён. Если есть вопросы — напишите нам.",
}

CAPTION_LIMIT = 1024


def esc(s) -> str:
    return html.escape(str(s or ""))


def money(v) -> str:
    s = f"{float(v):,.2f}".rstrip("0").rstrip(".").replace(",", " ")
    return f"{s} {CURRENCY}"


def local_time(ts: str) -> str:
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") + timedelta(hours=TZ_OFFSET_HOURS)
    return dt.strftime("%d.%m.%Y %H:%M")


def stars(n: float) -> str:
    return "⭐" * int(round(n))


def tiers_text(base: float, tiers) -> str:
    if not tiers:
        return ""
    first = tiers[0]["min_qty"] - 1
    span = "1 шт." if first == 1 else f"1–{first} шт."
    lines = ["📉 <b>Чем больше — тем дешевле:</b>", f"• {span} — {money(base)}/шт."]
    for t in tiers:
        lines.append(f"• от {t['min_qty']} шт. — {money(t['price'])}/шт.")
    lines.append("<i>Вкусы одной модели можно миксовать.</i>")
    return "\n".join(lines)


def product_text(p, flavors, tiers=(), rating=(0.0, 0), admin: bool = False) -> str:
    lines = [f"<b>{esc(p['name'])}</b>"]
    avg, cnt = rating
    if cnt:
        lines.append(f"{stars(avg)} {avg:.1f} · {cnt} отзыв(ов)")
    if p["description"]:
        lines += ["", esc(p["description"])]
    lines += ["", f"💰 Цена: <b>{money(p['price'])}</b>"]
    if tiers:
        lines += ["", tiers_text(p["price"], tiers)]
    if flavors:
        if admin:
            lines.append("🍬 Вкусы: " + ", ".join(("✅" if f["in_stock"] else "❌") + esc(f["name"]) for f in flavors))
        else:
            lines += ["", "👇 Выберите вкус:"]
    if admin:
        lines.append("Статус: " + ("👁 в продаже" if p["active"] else "🙈 скрыт"))
    return "\n".join(lines)


async def send_product(bot: Bot, chat_id: int, media, text: str, kb) -> None:
    """Карточка товара: одно фото/видео с подписью, альбом + текст, или просто текст."""
    if len(media) == 1 and len(text) <= CAPTION_LIMIT:
        m = media[0]
        send = bot.send_video if m["kind"] == "video" else bot.send_photo
        await send(chat_id, m["file_id"], caption=text, reply_markup=kb)
        return
    if media:
        group = [
            (InputMediaVideo if m["kind"] == "video" else InputMediaPhoto)(media=m["file_id"])
            for m in media[:10]
        ]
        await bot.send_media_group(chat_id, group)
    await bot.send_message(chat_id, text, reply_markup=kb)


def order_text(o, items, admin: bool = False) -> str:
    lines = [
        f"🧾 <b>Заказ #{o['id']}</b> — {STATUS.get(o['status'], o['status'])}",
        f"🕒 {local_time(o['created_at'])}",
        "",
        f"👤 {esc(o['customer_name'])}",
        f"📞 {esc(o['phone'])}",
        f"📍 {esc(o['address'])}",
        f"🚚 {esc(o['delivery'])}",
        f"💳 {esc(o['payment'])}",
    ]
    if o["comment"]:
        lines.append(f"💬 {esc(o['comment'])}")
    if admin:
        tg = f"@{esc(o['username'])}" if o["username"] else esc(o["tg_name"] or "профиль")
        lines.append(f'✈️ Telegram: <a href="tg://user?id={o["user_id"]}">{tg}</a> (id {o["user_id"]})')
    lines += ["", "<b>Товары:</b>"]
    count = 0
    for i, it in enumerate(items, 1):
        flavor = f" ({esc(it['flavor_name'])})" if it["flavor_name"] else ""
        lines.append(f"{i}. {esc(it['product_name'])}{flavor} × {it['qty']} по {money(it['price'])} = "
                     f"{money(it['price'] * it['qty'])}")
        count += it["qty"]
    lines += ["", f"📦 Всего штук: {count}"]
    if o["discount"]:
        lines += [f"Сумма: {money(o['subtotal'])}", f"🎟 Промокод {esc(o['promo'])}: −{money(o['discount'])}"]
    lines.append(f"💰 <b>ИТОГО: {money(o['total'])}</b>")
    return "\n".join(lines)
