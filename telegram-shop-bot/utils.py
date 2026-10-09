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


def product_text(p, flavors, admin: bool = False) -> str:
    lines = [f"<b>{esc(p['name'])}</b>"]
    if p["description"]:
        lines += ["", esc(p["description"])]
    lines += ["", f"💰 Цена: <b>{money(p['price'])}</b>"]
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
        lines.append(f"{i}. {esc(it['product_name'])}{flavor} × {it['qty']} = {money(it['price'] * it['qty'])}")
        count += it["qty"]
    lines += ["", f"📦 Всего штук: {count}", f"💰 <b>ИТОГО: {money(o['total'])}</b>"]
    return "\n".join(lines)
