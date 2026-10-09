import logging
import re

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
import pricing
from config import (
    ADMIN_IDS, BLOCKED_REGIONS, CONTACTS, DELIVERY_METHODS, HIDE_PRICES, MIN_AGE, PAYMENT_METHODS,
    REGION_LABEL, REGIONS, SHOP_NAME,
)
from keyboards import (
    BTN_CANCEL, BTN_CART, BTN_CATALOG, BTN_CONTACTS, BTN_ORDERS, BTN_SKIP,
    Adm, Age, CartAct, Cat, Flv, Nav, Pick, Prod, Qty, Quote, Rev, Wait,
    added_kb, age_kb, cart_kb, categories_kb, confirm_kb, input_kb, main_menu,
    order_status_kb, pick_kb, product_kb, products_kb, qty_kb, stars_kb, wait_kb,
)
from utils import STATUS, esc, local_time, money, order_text, order_total, product_text, send_product, stars

router = Router()
log = logging.getLogger(__name__)


class Checkout(StatesGroup):
    name = State()
    phone = State()
    region = State()
    address = State()
    delivery = State()
    payment = State()
    comment = State()
    confirm = State()


class PromoInput(StatesGroup):
    code = State()


class ReviewText(StatesGroup):
    text = State()


class AgeGate(BaseMiddleware):
    """Пускает в магазин только после подтверждения возраста."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if MIN_AGE <= 0 or user is None or user.id in ADMIN_IDS:
            return await handler(event, data)
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and (event.data or "").startswith("age:"):
            return await handler(event, data)
        if await db.is_age_ok(user.id):
            return await handler(event, data)
        await data["bot"].send_message(user.id, f"🔞 Вам уже есть {MIN_AGE}?", reply_markup=age_kb())
        if isinstance(event, CallbackQuery):
            await event.answer()


router.message.middleware(AgeGate())
router.callback_query.middleware(AgeGate())


# ---------- старт и меню ----------

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    u = message.from_user
    await db.upsert_user(u.id, u.username, u.full_name)
    if MIN_AGE > 0 and u.id not in ADMIN_IDS and not await db.is_age_ok(u.id):
        await message.answer(
            f"👋 Добро пожаловать в <b>{esc(SHOP_NAME)}</b>!\n\n🔞 Вам уже есть {MIN_AGE}?",
            reply_markup=age_kb(),
        )
        return
    await message.answer(
        f"👋 Добро пожаловать в <b>{esc(SHOP_NAME)}</b>!\nВыберите раздел в меню 👇",
        reply_markup=main_menu(u.id),
    )


@router.callback_query(Age.filter())
async def age_answer(call: CallbackQuery, callback_data: Age):
    await call.message.delete()
    if not callback_data.ok:
        await call.message.answer(f"😔 Извините, магазин только для покупателей {MIN_AGE}+.")
        return
    u = call.from_user
    await db.upsert_user(u.id, u.username, u.full_name)
    await db.set_age_ok(u.id)
    await call.message.answer("✅ Спасибо! Выберите раздел в меню 👇", reply_markup=main_menu(u.id))


@router.message(F.text == BTN_CANCEL)
@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu(message.from_user.id))


@router.message(F.text == BTN_CONTACTS)
async def contacts(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"ℹ️ <b>{esc(SHOP_NAME)}</b>\n\n{esc(CONTACTS)}")


@router.message(F.text == BTN_ORDERS)
async def my_orders(message: Message, state: FSMContext):
    await state.clear()
    orders = await db.user_orders(message.from_user.id)
    if not orders:
        await message.answer("У вас пока нет заказов. Загляните в 🛍 Каталог!")
        return
    lines = ["📦 <b>Ваши заказы:</b>", ""]
    for o in orders:
        lines.append(f"#{o['id']} · {local_time(o['created_at'])} · {order_total(o)} · {STATUS[o['status']]}")
    await message.answer("\n".join(lines))


# ---------- каталог ----------

async def show_catalog(bot: Bot, chat_id: int):
    cats = await db.categories_with_products()
    if not cats:
        await bot.send_message(chat_id, "Каталог пока пуст — скоро здесь появятся товары 🙌")
        return
    await bot.send_message(chat_id, "🛍 <b>Каталог</b>\nВыберите категорию:", reply_markup=categories_kb(cats))


@router.message(F.text == BTN_CATALOG)
async def catalog(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await show_catalog(bot, message.chat.id)


@router.callback_query(Nav.filter(F.to == "catalog"))
async def catalog_cb(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await call.answer()
    await show_catalog(bot, call.message.chat.id)


@router.callback_query(Nav.filter(F.to == "noop"))
async def noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(Cat.filter())
async def category(call: CallbackQuery, callback_data: Cat):
    prods = await db.products(callback_data.id)
    await call.answer()
    if not prods:
        await call.message.answer("В этой категории пока нет товаров.")
        return
    await call.message.answer("Выберите товар:", reply_markup=products_kb(prods, HIDE_PRICES))


@router.callback_query(Prod.filter())
async def product(call: CallbackQuery, callback_data: Prod, bot: Bot):
    p = await db.product(callback_data.id)
    if not p or not p["active"]:
        await call.answer("Товар недоступен", show_alert=True)
        return
    await call.answer()
    flavors = await db.flavors(p["id"])
    rating = await db.rating(p["id"])
    text = product_text(p, flavors, tiers=await db.tiers(p["id"]), rating=rating, hidden=pricing.price_hidden(p))
    await send_product(bot, call.message.chat.id, await db.media(p["id"]), text, product_kb(p, flavors, rating[1]))


@router.callback_query(Flv.filter())
async def pick_flavor(call: CallbackQuery, callback_data: Flv):
    f = await db.flavor(callback_data.fid)
    p = await db.product(callback_data.pid)
    if not p or not p["active"] or not f:
        await call.answer("Товар недоступен", show_alert=True)
        return
    if not f["in_stock"]:
        await call.answer()
        await call.message.answer(
            f"😔 Вкус «{esc(f['name'])}» ({esc(p['name'])}) сейчас закончился.\n"
            "Нажмите кнопку — бот напишет, как только он снова появится.",
            reply_markup=wait_kb(f["id"]),
        )
        return
    await call.answer()
    text, markup = await qty_view(call.from_user.id, p, f, 1)
    await call.message.answer(text, reply_markup=markup)


@router.callback_query(Wait.filter())
async def stock_wait(call: CallbackQuery, callback_data: Wait):
    f = await db.flavor(callback_data.fid)
    if not f:
        await call.answer("Вкус больше не продаётся", show_alert=True)
        return
    if f["in_stock"]:
        await call.answer("Он уже в наличии — заказывайте! 🎉", show_alert=True)
        return
    await db.add_stock_wait(call.from_user.id, f["id"])
    await call.answer("🔔 Готово! Напишем, как только появится.", show_alert=True)
    await call.message.edit_reply_markup(reply_markup=None)


async def qty_view(uid: int, p, f, qty: int):
    """Текст и клавиатура выбора количества с живой оптовой ценой."""
    label = esc(p["name"]) + (f" — {esc(f['name'])}" if f else "")
    if pricing.price_hidden(p):
        text = f"<b>{label}</b>\n💰 Цена по запросу — менеджер пришлёт стоимость после заявки.\n\nВыберите количество:"
        return text, qty_kb(p["id"], f["id"] if f else 0, qty, None)
    tiers = await db.tiers(p["id"])
    in_cart = sum(it["qty"] for it in await db.cart_items(uid) if it["product_id"] == p["id"])
    unit = pricing.unit_price(p["price"], tiers, in_cart + qty)
    lines = [f"<b>{label}</b>", f"Цена за шт.: <b>{money(unit)}</b>"]
    if unit < p["price"]:
        lines[-1] += f" <s>{money(p['price'])}</s> 🔥"
    if in_cart:
        lines.append(f"<i>В корзине уже {in_cart} шт. этой модели — они тоже учитываются в скидке.</i>")
    nt = pricing.next_tier(tiers, in_cart + qty)
    if nt:
        lines.append(f"💡 Возьмите ещё {nt['min_qty'] - in_cart - qty} шт. — будет по {money(nt['price'])}/шт.")
    lines += ["", "Выберите количество:"]
    return "\n".join(lines), qty_kb(p["id"], f["id"] if f else 0, qty, unit)


@router.callback_query(Qty.filter())
async def quantity(call: CallbackQuery, callback_data: Qty):
    cd = callback_data
    p = await db.product(cd.pid)
    f = await db.flavor(cd.fid) if cd.fid else None
    if not p or not p["active"] or (cd.fid and (not f or not f["in_stock"])):
        await call.answer("😔 Товар закончился", show_alert=True)
        return
    label = f"{esc(p['name'])}" + (f" — {esc(f['name'])}" if f else "")

    if not cd.add:
        text, markup = await qty_view(call.from_user.id, p, f, cd.qty)
        if not call.message.text:  # карточка товара с фото — шлём выбор отдельным сообщением
            await call.message.answer(text, reply_markup=markup)
        elif call.message.reply_markup != markup:
            await call.message.edit_text(text, reply_markup=markup)
        await call.answer()
        return

    await db.cart_add(call.from_user.id, cd.pid, cd.fid, cd.qty)
    await call.answer("Добавлено в корзину ✅")
    await call.message.edit_text(f"✅ Добавлено: <b>{label}</b> × {cd.qty}", reply_markup=added_kb())


# ---------- корзина ----------

async def render_cart(uid: int):
    c = await pricing.cart_summary(uid)
    if not c["items"]:
        return "🛒 Корзина пуста.", None
    lines = ["🛒 <b>Ваша корзина:</b>", ""]
    for i, it in enumerate(c["items"], 1):
        flavor = f" ({esc(it['flavor'])})" if it["flavor"] else ""
        if not it["ok"]:
            lines.append(f"{i}. {esc(it['name'])}{flavor} × {it['qty']} ⚠️ нет в наличии")
            continue
        if c["quote"]:
            lines.append(f"{i}. {esc(it['name'])}{flavor} × {it['qty']}")
            continue
        old = f" <s>{money(it['price'])}</s>" if it["unit"] < it["price"] else ""
        lines.append(f"{i}. {esc(it['name'])}{flavor} × {it['qty']} по {money(it['unit'])}{old} = {money(it['line'])}")
    lines.append("")
    if c["quote"]:
        lines.append("💬 <b>Цену сообщит менеджер</b> — отправьте заявку, и вы получите стоимость прямо здесь.")
        return "\n".join(lines), cart_kb(c["items"], False, quote=True)
    if c["wholesale_savings"]:
        lines.append(f"📉 Оптовая скидка: −{money(c['wholesale_savings'])}")
    if c["promo"]:
        lines += [f"Сумма: {money(c['subtotal'])}", f"🎟 Промокод {esc(c['promo']['code'])}: −{money(c['discount'])}"]
    if c["promo_error"]:
        lines.append(f"⚠️ Промокод снят: {c['promo_error']}")
    lines.append(f"💰 <b>Итого: {money(c['total'])}</b>")
    for name, more, price in c["hints"]:
        lines.append(f"💡 {esc(name)}: ещё {more} шт. — и будет по {money(price)}/шт.")
    return "\n".join(lines), cart_kb(c["items"], bool(c["promo"]))


@router.message(F.text == BTN_CART)
async def cart(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await render_cart(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(Nav.filter(F.to == "cart"))
async def cart_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    text, kb = await render_cart(call.from_user.id)
    await call.message.answer(text, reply_markup=kb)


@router.callback_query(CartAct.filter(F.action.in_({"del", "clear"})))
async def cart_edit(call: CallbackQuery, callback_data: CartAct):
    uid = call.from_user.id
    if callback_data.action == "clear":
        await db.cart_clear(uid)
    else:
        await db.cart_remove(uid, callback_data.pid, callback_data.fid)
    await call.answer("Готово")
    text, kb = await render_cart(uid)
    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(CartAct.filter(F.action == "promo"))
async def promo_ask(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(PromoInput.code)
    await call.message.answer("🎟 Введите промокод:", reply_markup=input_kb())


@router.message(PromoInput.code, F.text)
async def promo_enter(message: Message, state: FSMContext):
    uid = message.from_user.id
    promo, error = await pricing.check_promo(message.text.strip(), uid)
    if not promo:
        await message.answer(f"😔 {error}. Попробуйте другой или нажмите «{BTN_CANCEL}».")
        return
    await state.clear()
    await db.set_user_promo(uid, promo["code"])
    discount = f"{promo['value']:g}%" if promo["kind"] == "percent" else money(promo["value"])
    await message.answer(f"✅ Промокод <b>{esc(promo['code'])}</b> применён: скидка {discount}",
                         reply_markup=main_menu(uid))
    text, kb = await render_cart(uid)
    await message.answer(text, reply_markup=kb)


@router.callback_query(CartAct.filter(F.action == "promo_off"))
async def promo_off(call: CallbackQuery):
    await db.set_user_promo(call.from_user.id, "")
    await call.answer("Промокод убран")
    text, kb = await render_cart(call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)


# ---------- оформление заказа ----------

def step(n: int) -> str:
    return f"<b>Шаг {n}/{6 + bool(REGIONS)}.</b>"


@router.callback_query(CartAct.filter(F.action == "checkout"))
async def checkout_start(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    dropped = await db.cart_drop_unavailable(uid)
    if dropped:
        await call.message.answer("⚠️ Убрали из корзины (нет в наличии): " + esc(", ".join(dropped)))
    if not await db.cart_items(uid):
        await call.answer("Корзина пуста", show_alert=True)
        return
    await call.answer()
    last = await db.last_order(uid)
    await state.set_state(Checkout.name)
    await state.update_data(last=dict(last) if last else {})
    await call.message.answer(
        f"📝 Оформляем заказ.\n\n{step(1)} Как к вам обращаться?",
        reply_markup=input_kb(last["customer_name"] if last else call.from_user.full_name),
    )


@router.message(Checkout.name, F.text)
async def checkout_name(message: Message, state: FSMContext):
    name = message.text.strip()[:100]
    data = await state.get_data()
    await state.update_data(name=name)
    await state.set_state(Checkout.phone)
    await message.answer(
        f"{step(2)} 📞 Ваш номер телефона:",
        reply_markup=input_kb(data["last"].get("phone", ""), contact=True),
    )


@router.message(Checkout.phone, F.contact | F.text)
async def checkout_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact else message.text.strip()
    if len(re.sub(r"\D", "", phone)) < 6:
        await message.answer("Похоже, номер некорректный. Введите ещё раз, например +1 202 555 0147")
        return
    data = await state.get_data()
    await state.update_data(phone=phone[:30])
    if REGIONS:
        await state.set_state(Checkout.region)
        await message.answer(f"{step(3)} 🗺 {esc(REGION_LABEL)} — код из 2 букв (например, TX):",
                             reply_markup=input_kb(data["last"].get("region", "")))
        return
    await ask_address(message, state)


@router.message(Checkout.region, F.text)
async def checkout_region(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    if code not in REGIONS:
        await message.answer(f"Не нашли «{esc(code)}». Введите код из 2 букв, например TX, FL, NV.")
        return
    if code in BLOCKED_REGIONS:
        await message.answer(f"😔 К сожалению, в {esc(code)} мы не доставляем из-за ограничений местного закона.")
        return
    await state.update_data(region=code)
    await ask_address(message, state)


async def ask_address(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Checkout.address)
    await message.answer(
        f"{step(3 + bool(REGIONS))} 📍 Адрес доставки (город, улица, дом, квартира, ZIP):",
        reply_markup=input_kb(data["last"].get("address", "")),
    )


@router.message(Checkout.address, F.text)
async def checkout_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text.strip()[:300])
    await state.set_state(Checkout.delivery)
    await message.answer(f"{step(4 + bool(REGIONS))} 🚚 Способ доставки:", reply_markup=pick_kb("delivery", DELIVERY_METHODS))


@router.callback_query(Checkout.delivery, Pick.filter(F.kind == "delivery"))
async def checkout_delivery(call: CallbackQuery, callback_data: Pick, state: FSMContext):
    choice = DELIVERY_METHODS[callback_data.idx]
    await state.update_data(delivery=choice)
    await state.set_state(Checkout.payment)
    await call.answer()
    await call.message.edit_text(f"🚚 Доставка: <b>{esc(choice)}</b>")
    await call.message.answer(f"{step(5 + bool(REGIONS))} 💳 Способ оплаты:", reply_markup=pick_kb("payment", PAYMENT_METHODS))


@router.callback_query(Checkout.payment, Pick.filter(F.kind == "payment"))
async def checkout_payment(call: CallbackQuery, callback_data: Pick, state: FSMContext):
    choice = PAYMENT_METHODS[callback_data.idx]
    await state.update_data(payment=choice)
    await state.set_state(Checkout.comment)
    await call.answer()
    await call.message.edit_text(f"💳 Оплата: <b>{esc(choice)}</b>")
    await call.message.answer(
        f"{step(6 + bool(REGIONS))} 💬 Комментарий к заказу (удобное время, пожелания) — или нажмите «Пропустить»:",
        reply_markup=input_kb(skip=True),
    )


@router.message(Checkout.comment, F.text)
async def checkout_comment(message: Message, state: FSMContext):
    comment = "" if message.text == BTN_SKIP else message.text.strip()[:500]
    await state.update_data(comment=comment)
    await state.set_state(Checkout.confirm)
    d = await state.get_data()
    cart_text, _ = await render_cart(message.from_user.id)
    lines = [
        cart_text, "",
        f"👤 {esc(d['name'])}",
        f"📞 {esc(d['phone'])}",
        f"📍 {esc(d['address'])}" + (f", {esc(d['region'])}" if d.get("region") else ""),
        f"🚚 {esc(d['delivery'])}",
        f"💳 {esc(d['payment'])}",
    ]
    if comment:
        lines.append(f"💬 {esc(comment)}")
    if MIN_AGE > 0:
        lines += ["", f"🔞 Подтверждая, вы подтверждаете, что вам {MIN_AGE}+. "
                      "При получении курьер попросит документ и подпись совершеннолетнего."]
    quote = (await pricing.cart_summary(message.from_user.id))["quote"]
    await message.answer("Проверьте заказ 👇", reply_markup=ReplyKeyboardRemove())
    await message.answer("\n".join(lines), reply_markup=confirm_kb(quote))


@router.callback_query(Checkout.confirm, CartAct.filter(F.action == "confirm"))
async def checkout_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    uid = call.from_user.id
    data = await state.get_data()
    await state.clear()
    summary = await pricing.cart_summary(uid)
    oid = await db.create_order(uid, data, summary)
    if not oid:
        await call.answer("Корзина пуста — товары закончились", show_alert=True)
        return
    o = await db.order(oid)
    quote = o["status"] == "quote"
    await call.answer("Заявка отправлена!" if quote else "Заказ оформлен!")
    await call.message.edit_reply_markup(reply_markup=None)
    if quote:
        done = (f"📨 <b>Заявка #{oid} отправлена!</b>\nМенеджер рассчитает стоимость и пришлёт её сюда — "
                "останется только подтвердить.")
    else:
        done = f"🎉 <b>Заказ #{oid} оформлен!</b>\nМенеджер свяжется с вами в ближайшее время."
    await call.message.answer(f"{done}\nСтатус — в разделе «{BTN_ORDERS}».", reply_markup=main_menu(uid))
    title = "🔔 <b>НОВАЯ ЗАЯВКА — назначьте цену!</b>" if quote else "🔔 <b>НОВЫЙ ЗАКАЗ!</b>"
    await notify_admins(bot, f"{title}\n\n" + order_text(o, await db.order_items(oid), admin=True),
                        order_status_kb(oid, o["status"]))


async def notify_admins(bot: Bot, text: str, markup=None):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except Exception as e:  # админ не запускал бота / заблокировал
            log.warning("Не удалось уведомить админа %s: %s", admin_id, e)


@router.callback_query(Quote.filter())
async def quote_answer(call: CallbackQuery, callback_data: Quote, bot: Bot):
    o = await db.order(callback_data.oid)
    if not o or o["user_id"] != call.from_user.id or o["status"] != "priced":
        await call.answer("Это предложение уже неактуально", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=None)
    if callback_data.ok:
        await db.set_order_status(o["id"], "new")
        await call.answer("Заказ подтверждён!")
        await call.message.answer(f"🎉 <b>Заказ #{o['id']} подтверждён</b> на {money(o['total'])}.\n"
                                  "Менеджер свяжется с вами по оплате и отправке.")
        o = await db.order(o["id"])
        await notify_admins(bot, "✅ <b>КЛИЕНТ ПОДТВЕРДИЛ ЦЕНУ!</b>\n\n"
                            + order_text(o, await db.order_items(o["id"]), admin=True),
                            order_status_kb(o["id"], "new"))
    else:
        await db.set_order_status(o["id"], "cancelled")
        await call.answer("Заказ отменён")
        await call.message.answer(f"Заказ #{o['id']} отменён. Если передумаете — мы всегда на связи 🙌")
        await notify_admins(bot, f"❌ Клиент отказался от заявки #{o['id']} (цена {money(o['total'])}).")


@router.callback_query(CartAct.filter(F.action == "abort"))
async def checkout_abort(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Оформление отменено, корзина сохранена.", reply_markup=main_menu(call.from_user.id))


# ---------- отзывы ----------

@router.callback_query(Rev.filter(F.a == "list"))
async def reviews_list(call: CallbackQuery, callback_data: Rev):
    await call.answer()
    p = await db.product(callback_data.pid)
    rows = await db.reviews(callback_data.pid)
    if not p or not rows:
        await call.message.answer("Отзывов пока нет.")
        return
    avg, cnt = await db.rating(p["id"])
    lines = [f"💬 <b>Отзывы: {esc(p['name'])}</b>", f"{stars(avg)} {avg:.1f} · {cnt} отзыв(ов)", ""]
    for r in rows:
        name = esc((r["full_name"] or "Покупатель").split()[0])
        lines.append(f"{stars(r['rating'])} <b>{name}</b> · {local_time(r['created_at'])[:10]}")
        if r["text"]:
            lines.append(esc(r["text"]))
        lines.append("")
    await call.message.answer("\n".join(lines))


@router.callback_query(Rev.filter(F.a == "start"))
async def review_start(call: CallbackQuery, callback_data: Rev):
    if not await db.can_review(call.from_user.id, callback_data.oid, callback_data.pid):
        await call.answer("Отзыв можно оставить один раз на товар из доставленного заказа 👍", show_alert=True)
        return
    p = await db.product(callback_data.pid)
    await call.answer()
    await call.message.answer(f"Как вам <b>{esc(p['name'])}</b>? Поставьте оценку:",
                              reply_markup=stars_kb(callback_data.oid, callback_data.pid))


@router.callback_query(Rev.filter(F.a == "rate"))
async def review_rate(call: CallbackQuery, callback_data: Rev, state: FSMContext):
    if not await db.can_review(call.from_user.id, callback_data.oid, callback_data.pid):
        await call.answer("Отзыв можно оставить один раз на товар из доставленного заказа 👍", show_alert=True)
        return
    await call.answer()
    await state.set_state(ReviewText.text)
    await state.update_data(oid=callback_data.oid, pid=callback_data.pid, rating=max(1, min(5, callback_data.r)))
    await call.message.edit_text(f"Ваша оценка: {stars(callback_data.r)}")
    await call.message.answer("Напишите пару слов о товаре (вкус, качество, сколько хватает) — "
                              "или нажмите «Пропустить»:", reply_markup=input_kb(skip=True))


@router.message(ReviewText.text, F.text)
async def review_text(message: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    if not await db.can_review(uid, d["oid"], d["pid"]):
        await message.answer("Отзыв уже сохранён 👍", reply_markup=main_menu(uid))
        return
    text = "" if message.text == BTN_SKIP else message.text.strip()[:1000]
    rid = await db.add_review(d["pid"], uid, d["oid"], d["rating"], text)
    await message.answer("🙏 Спасибо за отзыв!", reply_markup=main_menu(uid))
    p = await db.product(d["pid"])
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить отзыв", callback_data=Adm(a="rv_del", id=rid))
    note = (f"⭐ <b>Новый отзыв</b> · {esc(p['name'])}\n{stars(d['rating'])} от {esc(message.from_user.full_name)}"
            + (f"\n\n{esc(text)}" if text else ""))
    await notify_admins(bot, note, kb.as_markup())


@router.callback_query(Pick.filter())
async def stale_pick(call: CallbackQuery):
    await call.answer("Эта кнопка устарела — начните оформление заново из корзины", show_alert=True)


@router.message(StateFilter(Checkout, PromoInput, ReviewText))
async def checkout_wrong_input(message: Message):
    await message.answer("Пожалуйста, ответьте текстом или выберите вариант кнопкой 👆")
