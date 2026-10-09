import logging
import re

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import db
from config import ADMIN_IDS, CONTACTS, DELIVERY_METHODS, MIN_AGE, PAYMENT_METHODS, SHOP_NAME
from keyboards import (
    BTN_CANCEL, BTN_CART, BTN_CATALOG, BTN_CONTACTS, BTN_ORDERS, BTN_SKIP,
    Age, CartAct, Cat, Flv, Nav, Pick, Prod, Qty,
    added_kb, age_kb, cart_kb, categories_kb, confirm_kb, input_kb, main_menu,
    order_status_kb, pick_kb, product_kb, products_kb, qty_kb,
)
from utils import STATUS, esc, local_time, money, order_text, product_text, send_product

router = Router()
log = logging.getLogger(__name__)


class Checkout(StatesGroup):
    name = State()
    phone = State()
    address = State()
    delivery = State()
    payment = State()
    comment = State()
    confirm = State()


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
        await data["bot"].send_message(user.id, f"🔞 Вам уже исполнилось {MIN_AGE} лет?", reply_markup=age_kb())
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
            f"👋 Добро пожаловать в <b>{esc(SHOP_NAME)}</b>!\n\n🔞 Вам уже исполнилось {MIN_AGE} лет?",
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
        await call.message.answer(f"😔 Извините, магазин доступен только лицам старше {MIN_AGE} лет.")
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
        lines.append(f"#{o['id']} · {local_time(o['created_at'])} · {money(o['total'])} · {STATUS[o['status']]}")
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
    await call.message.answer("Выберите товар:", reply_markup=products_kb(prods))


@router.callback_query(Prod.filter())
async def product(call: CallbackQuery, callback_data: Prod, bot: Bot):
    p = await db.product(callback_data.id)
    if not p or not p["active"]:
        await call.answer("Товар недоступен", show_alert=True)
        return
    await call.answer()
    flavors = await db.flavors(p["id"])
    await send_product(bot, call.message.chat.id, await db.media(p["id"]), product_text(p, flavors), product_kb(p, flavors))


@router.callback_query(Flv.filter())
async def pick_flavor(call: CallbackQuery, callback_data: Flv):
    f = await db.flavor(callback_data.fid)
    p = await db.product(callback_data.pid)
    if not p or not p["active"] or not f:
        await call.answer("Товар недоступен", show_alert=True)
        return
    if not f["in_stock"]:
        await call.answer(f"😔 Вкус «{f['name']}» закончился", show_alert=True)
        return
    await call.answer()
    await call.message.answer(
        f"<b>{esc(p['name'])}</b> — {esc(f['name'])}\nЦена: {money(p['price'])}\n\nВыберите количество:",
        reply_markup=qty_kb(p["id"], f["id"], 1, p["price"]),
    )


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
        text = f"<b>{label}</b>\nЦена: {money(p['price'])}\n\nВыберите количество:"
        markup = qty_kb(p["id"], cd.fid, cd.qty, p["price"])
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
    items = await db.cart_items(uid)
    if not items:
        return "🛒 Корзина пуста.", None
    lines = ["🛒 <b>Ваша корзина:</b>", ""]
    total = 0
    for i, it in enumerate(items, 1):
        flavor = f" ({esc(it['flavor'])})" if it["flavor"] else ""
        bad = "" if it["active"] and it["in_stock"] else " ⚠️ нет в наличии"
        lines.append(f"{i}. {esc(it['name'])}{flavor} × {it['qty']} = {money(it['price'] * it['qty'])}{bad}")
        if not bad:
            total += it["price"] * it["qty"]
    lines += ["", f"💰 <b>Итого: {money(total)}</b>"]
    return "\n".join(lines), cart_kb(items)


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


# ---------- оформление заказа ----------

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
        "📝 Оформляем заказ.\n\n<b>Шаг 1/6.</b> Как к вам обращаться?",
        reply_markup=input_kb(last["customer_name"] if last else call.from_user.full_name),
    )


@router.message(Checkout.name, F.text)
async def checkout_name(message: Message, state: FSMContext):
    name = message.text.strip()[:100]
    data = await state.get_data()
    await state.update_data(name=name)
    await state.set_state(Checkout.phone)
    await message.answer(
        "<b>Шаг 2/6.</b> 📞 Ваш номер телефона:",
        reply_markup=input_kb(data["last"].get("phone", ""), contact=True),
    )


@router.message(Checkout.phone, F.contact | F.text)
async def checkout_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact else message.text.strip()
    if len(re.sub(r"\D", "", phone)) < 6:
        await message.answer("Похоже, номер некорректный. Введите ещё раз, например +7 999 123-45-67")
        return
    data = await state.get_data()
    await state.update_data(phone=phone[:30])
    await state.set_state(Checkout.address)
    await message.answer(
        "<b>Шаг 3/6.</b> 📍 Адрес доставки (город, улица, дом, квартира):",
        reply_markup=input_kb(data["last"].get("address", "")),
    )


@router.message(Checkout.address, F.text)
async def checkout_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text.strip()[:300])
    await state.set_state(Checkout.delivery)
    await message.answer("<b>Шаг 4/6.</b> 🚚 Способ доставки:", reply_markup=pick_kb("delivery", DELIVERY_METHODS))


@router.callback_query(Checkout.delivery, Pick.filter(F.kind == "delivery"))
async def checkout_delivery(call: CallbackQuery, callback_data: Pick, state: FSMContext):
    choice = DELIVERY_METHODS[callback_data.idx]
    await state.update_data(delivery=choice)
    await state.set_state(Checkout.payment)
    await call.answer()
    await call.message.edit_text(f"🚚 Доставка: <b>{esc(choice)}</b>")
    await call.message.answer("<b>Шаг 5/6.</b> 💳 Способ оплаты:", reply_markup=pick_kb("payment", PAYMENT_METHODS))


@router.callback_query(Checkout.payment, Pick.filter(F.kind == "payment"))
async def checkout_payment(call: CallbackQuery, callback_data: Pick, state: FSMContext):
    choice = PAYMENT_METHODS[callback_data.idx]
    await state.update_data(payment=choice)
    await state.set_state(Checkout.comment)
    await call.answer()
    await call.message.edit_text(f"💳 Оплата: <b>{esc(choice)}</b>")
    await call.message.answer(
        "<b>Шаг 6/6.</b> 💬 Комментарий к заказу (удобное время, пожелания) — или нажмите «Пропустить»:",
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
        f"📍 {esc(d['address'])}",
        f"🚚 {esc(d['delivery'])}",
        f"💳 {esc(d['payment'])}",
    ]
    if comment:
        lines.append(f"💬 {esc(comment)}")
    await message.answer("Проверьте заказ 👇", reply_markup=ReplyKeyboardRemove())
    await message.answer("\n".join(lines), reply_markup=confirm_kb())


@router.callback_query(Checkout.confirm, CartAct.filter(F.action == "confirm"))
async def checkout_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    uid = call.from_user.id
    data = await state.get_data()
    await state.clear()
    oid = await db.create_order(uid, data)
    if not oid:
        await call.answer("Корзина пуста — товары закончились", show_alert=True)
        return
    await call.answer("Заказ оформлен!")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"🎉 <b>Заказ #{oid} оформлен!</b>\nМенеджер свяжется с вами в ближайшее время.\n"
        f"Статус заказа можно посмотреть в разделе «{BTN_ORDERS}».",
        reply_markup=main_menu(uid),
    )
    o = await db.order(oid)
    text = "🔔 <b>НОВЫЙ ЗАКАЗ!</b>\n\n" + order_text(o, await db.order_items(oid), admin=True)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=order_status_kb(oid, "new"))
        except Exception as e:  # админ не запускал бота / заблокировал
            log.warning("Не удалось уведомить админа %s: %s", admin_id, e)


@router.callback_query(CartAct.filter(F.action == "abort"))
async def checkout_abort(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Оформление отменено, корзина сохранена.", reply_markup=main_menu(call.from_user.id))


@router.callback_query(Pick.filter())
async def stale_pick(call: CallbackQuery):
    await call.answer("Эта кнопка устарела — начните оформление заново из корзины", show_alert=True)


@router.message(StateFilter(Checkout))
async def checkout_wrong_input(message: Message):
    await message.answer("Пожалуйста, ответьте текстом или выберите вариант кнопкой 👆")
