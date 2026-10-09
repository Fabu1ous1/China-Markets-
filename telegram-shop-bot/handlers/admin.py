import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import ADMIN_IDS
from keyboards import (
    BTN_ADMIN, MENU_TEXTS, Adm, OrdSt,
    admin_flavors_kb, admin_menu_kb, admin_product_kb, main_menu, order_status_kb,
)
from utils import STATUS, STATUS_CLIENT_MSG, esc, money, order_text, product_text, send_product

log = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        return event.from_user is not None and event.from_user.id in ADMIN_IDS


router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

# Текст в состоянии админки, но не кнопка главного меню
ADMIN_TEXT = F.text & ~F.text.in_(MENU_TEXTS)


class AddProduct(StatesGroup):
    name = State()
    description = State()
    price = State()
    media = State()
    flavors = State()


class EditProduct(StatesGroup):
    value = State()
    media = State()


class AddCategory(StatesGroup):
    name = State()


class Broadcast(StatesGroup):
    message = State()
    confirm = State()


# Медиа копим здесь, а не в FSM: альбом приходит пачкой сообщений одновременно
_pending_media: dict[int, list[tuple[str, str]]] = {}


def _media_from(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.video:
        return message.video.file_id, "video"
    return None


def _parse_price(text: str) -> float | None:
    try:
        v = float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return v if v > 0 else None


def _parse_flavors(text: str) -> list[str]:
    if text.strip() in {"-", "—", "нет"}:
        return []
    parts = text.replace("\n", ",").split(",")
    return [p.strip()[:60] for p in parts if p.strip()]


def _done_kb(action: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Готово", callback_data=Adm(a=action))
    return kb.as_markup()


async def show_admin_product(bot: Bot, chat_id: int, pid: int):
    p = await db.product(pid)
    if not p:
        await bot.send_message(chat_id, "Товар не найден.")
        return
    flavors = await db.flavors(pid)
    await send_product(bot, chat_id, await db.media(pid), product_text(p, flavors, admin=True), admin_product_kb(p))


# ---------- меню ----------

@router.message(Command("admin"))
@router.message(F.text == BTN_ADMIN)
async def admin_menu(message: Message, state: FSMContext):
    await state.clear()
    _pending_media.pop(message.from_user.id, None)
    await message.answer("⚙️ <b>Админ-панель</b>", reply_markup=admin_menu_kb())


@router.callback_query(Adm.filter(F.a == "menu"))
async def admin_menu_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await call.message.answer("⚙️ <b>Админ-панель</b>", reply_markup=admin_menu_kb())


@router.message(Command("cancel"), StateFilter("*"))
async def admin_cancel(message: Message, state: FSMContext):
    await state.clear()
    _pending_media.pop(message.from_user.id, None)
    await message.answer("Отменено.", reply_markup=main_menu(message.from_user.id))


# ---------- категории ----------

@router.callback_query(Adm.filter(F.a == "cats"))
async def cats(call: CallbackQuery):
    await call.answer()
    kb = InlineKeyboardBuilder()
    rows = await db.categories()
    for c in rows:
        kb.button(text=f"🗑 {c['name']}", callback_data=Adm(a="cat_del", id=c["id"]))
    kb.button(text="➕ Новая категория", callback_data=Adm(a="cat_add"))
    kb.button(text="⬅️ Меню", callback_data=Adm(a="menu"))
    kb.adjust(1)
    text = "📂 <b>Категории</b>\nНажмите на категорию, чтобы удалить её." if rows else "📂 Категорий пока нет."
    await call.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(Adm.filter(F.a == "cat_add"))
async def cat_add(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AddCategory.name)
    await call.message.answer("Введите название категории (например: <i>Одноразовые</i>, <i>Pod-системы</i>, <i>Жидкости</i>):")


@router.message(AddCategory.name, ADMIN_TEXT)
async def cat_add_name(message: Message, state: FSMContext):
    await db.add_category(message.text.strip()[:60])
    await state.clear()
    await message.answer(f"✅ Категория «{esc(message.text.strip())}» создана.", reply_markup=admin_menu_kb())


@router.callback_query(Adm.filter(F.a == "cat_del"))
async def cat_del(call: CallbackQuery, callback_data: Adm):
    if await db.delete_category(callback_data.id):
        await call.answer("Категория удалена")
        await call.message.delete()
    else:
        await call.answer("Сначала удалите или перенесите товары из этой категории", show_alert=True)


# ---------- добавление товара ----------

@router.callback_query(Adm.filter(F.a == "add"))
async def add_start(call: CallbackQuery):
    await call.answer()
    cats_ = await db.categories()
    if not cats_:
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Создать категорию", callback_data=Adm(a="cat_add"))
        await call.message.answer("Сначала создайте хотя бы одну категорию.", reply_markup=kb.as_markup())
        return
    kb = InlineKeyboardBuilder()
    for c in cats_:
        kb.button(text=c["name"], callback_data=Adm(a="add_cat", id=c["id"]))
    kb.adjust(2)
    await call.message.answer("➕ <b>Новый товар</b>\nВыберите категорию:", reply_markup=kb.as_markup())


@router.callback_query(Adm.filter(F.a == "add_cat"))
async def add_cat(call: CallbackQuery, callback_data: Adm, state: FSMContext):
    await call.answer()
    await state.set_state(AddProduct.name)
    await state.update_data(category_id=callback_data.id)
    await call.message.answer("<b>1/5.</b> Название товара (например: <i>Elf Bar BC5000</i>):\n\n/cancel — отмена")


@router.message(AddProduct.name, ADMIN_TEXT)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip()[:100])
    await state.set_state(AddProduct.description)
    await message.answer(
        "<b>2/5.</b> Описание: затяжки, объём, крепость, батарея, комплектация и т.д.\n"
        "Отправьте «-», если без описания."
    )


@router.message(AddProduct.description, ADMIN_TEXT)
async def add_description(message: Message, state: FSMContext):
    desc = "" if message.text.strip() == "-" else message.text.strip()[:3000]
    await state.update_data(description=desc)
    await state.set_state(AddProduct.price)
    await message.answer("<b>3/5.</b> Цена за 1 шт. (только число, например <code>15</code> или <code>12.5</code>):")


@router.message(AddProduct.price, ADMIN_TEXT)
async def add_price(message: Message, state: FSMContext):
    price = _parse_price(message.text)
    if price is None:
        await message.answer("Нужно положительное число, например 15 или 12.5")
        return
    await state.update_data(price=price)
    await state.set_state(AddProduct.media)
    _pending_media[message.from_user.id] = []
    await message.answer(
        "<b>4/5.</b> Отправьте фото и/или видео товара (можно альбомом, до 10 шт.).\n"
        "Когда закончите — нажмите «Готово». Без медиа — сразу «Готово».",
        reply_markup=_done_kb("add_media_done"),
    )


@router.message(StateFilter(AddProduct.media, EditProduct.media), F.photo | F.video)
async def collect_media(message: Message):
    items = _pending_media.setdefault(message.from_user.id, [])
    if len(items) >= 10:
        if not message.media_group_id:
            await message.answer("Максимум 10 файлов. Нажмите «Готово».")
        return
    items.append(_media_from(message))
    if not message.media_group_id:
        await message.answer(f"📎 Добавлено файлов: {len(items)}. Ещё или «Готово».")


@router.callback_query(AddProduct.media, Adm.filter(F.a == "add_media_done"))
async def add_media_done(call: CallbackQuery, state: FSMContext):
    await call.answer()
    count = len(_pending_media.get(call.from_user.id, []))
    await state.set_state(AddProduct.flavors)
    await call.message.answer(
        f"📎 Медиа: {count} шт.\n\n<b>5/5.</b> Вкусы — через запятую или каждый с новой строки:\n"
        "<i>Манго, Арбуз, Клубника-банан, Ледяная мята</i>\n\n"
        "Если у товара нет вкусов — отправьте «-»."
    )


@router.message(AddProduct.flavors, ADMIN_TEXT)
async def add_flavors(message: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    media = _pending_media.pop(message.from_user.id, [])
    pid = await db.add_product(d["category_id"], d["name"], d["description"], d["price"], media,
                               _parse_flavors(message.text))
    await state.clear()
    await message.answer("✅ <b>Товар добавлен!</b> Вот как его видят покупатели:")
    await show_admin_product(bot, message.chat.id, pid)


# ---------- список и редактирование товаров ----------

@router.callback_query(Adm.filter(F.a == "prods"))
async def prods(call: CallbackQuery):
    await call.answer()
    rows = await db.products(only_active=False)
    if not rows:
        await call.message.answer("Товаров пока нет.", reply_markup=admin_menu_kb())
        return
    kb = InlineKeyboardBuilder()
    for p in rows:
        mark = "👁" if p["active"] else "🙈"
        kb.button(text=f"{mark} {p['name']} — {money(p['price'])}", callback_data=Adm(a="p", id=p["id"]))
    kb.button(text="⬅️ Меню", callback_data=Adm(a="menu"))
    kb.adjust(1)
    await call.message.answer("📋 <b>Товары</b> (👁 в продаже, 🙈 скрыт):", reply_markup=kb.as_markup())


@router.callback_query(Adm.filter(F.a == "p"))
async def admin_product(call: CallbackQuery, callback_data: Adm, bot: Bot):
    await call.answer()
    await show_admin_product(bot, call.message.chat.id, callback_data.id)


@router.callback_query(Adm.filter(F.a == "toggle"))
async def toggle(call: CallbackQuery, callback_data: Adm):
    p = await db.product(callback_data.id)
    if not p:
        await call.answer("Товар не найден", show_alert=True)
        return
    await db.update_product(p["id"], "active", 0 if p["active"] else 1)
    p = await db.product(p["id"])
    await call.answer("Товар показан в каталоге" if p["active"] else "Товар скрыт из каталога")
    await call.message.edit_reply_markup(reply_markup=admin_product_kb(p))


@router.callback_query(Adm.filter(F.a == "del"))
async def delete_ask(call: CallbackQuery, callback_data: Adm):
    await call.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=Adm(a="del_yes", id=callback_data.id))
    kb.button(text="Отмена", callback_data=Adm(a="p", id=callback_data.id))
    await call.message.answer("Точно удалить товар? Это необратимо. (Можно просто скрыть 🙈)", reply_markup=kb.as_markup())


@router.callback_query(Adm.filter(F.a == "del_yes"))
async def delete_yes(call: CallbackQuery, callback_data: Adm):
    await db.delete_product(callback_data.id)
    await call.answer("Удалено")
    await call.message.edit_text("🗑 Товар удалён.", reply_markup=admin_menu_kb())


EDIT_PROMPTS = {
    "name": "Новое название:",
    "description": "Новое описание (или «-», чтобы убрать):",
    "price": "Новая цена (число):",
    "flavors": "Добавьте вкусы через запятую или с новой строки:",
}


@router.callback_query(Adm.filter(F.a.startswith("e_")))
async def edit_start(call: CallbackQuery, callback_data: Adm, state: FSMContext):
    await call.answer()
    field = callback_data.a[2:]
    await state.update_data(pid=callback_data.id, field=field)
    if field == "media":
        await state.set_state(EditProduct.media)
        _pending_media[call.from_user.id] = []
        await call.message.answer(
            "Отправьте новые фото/видео (старые будут заменены), затем «Готово».\n/cancel — отмена",
            reply_markup=_done_kb("edit_media_done"),
        )
        return
    await state.set_state(EditProduct.value)
    await call.message.answer(EDIT_PROMPTS[field] + "\n/cancel — отмена")


@router.message(EditProduct.value, ADMIN_TEXT)
async def edit_value(message: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    pid, field, text = d["pid"], d["field"], message.text.strip()
    if field == "price":
        price = _parse_price(text)
        if price is None:
            await message.answer("Нужно положительное число.")
            return
        await db.update_product(pid, "price", price)
    elif field == "flavors":
        await db.add_flavors(pid, _parse_flavors(text))
    elif field == "description":
        await db.update_product(pid, "description", "" if text == "-" else text[:3000])
    else:
        await db.update_product(pid, "name", text[:100])
    await state.clear()
    await message.answer("✅ Сохранено.")
    await show_admin_product(bot, message.chat.id, pid)


@router.callback_query(EditProduct.media, Adm.filter(F.a == "edit_media_done"))
async def edit_media_done(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    pid = (await state.get_data())["pid"]
    await db.replace_media(pid, _pending_media.pop(call.from_user.id, []))
    await state.clear()
    await call.message.answer("✅ Медиа обновлены.")
    await show_admin_product(bot, call.message.chat.id, pid)


@router.callback_query(Adm.filter(F.a == "flv"))
async def flavors_menu(call: CallbackQuery, callback_data: Adm):
    await call.answer()
    flavors = await db.flavors(callback_data.id)
    if not flavors:
        await call.message.answer("У товара нет вкусов. Добавьте через «➕ Вкусы».")
        return
    await call.message.answer(
        "🍬 Нажмите на вкус, чтобы переключить наличие (✅ есть / ❌ нет). 🗑 — удалить вкус.",
        reply_markup=admin_flavors_kb(callback_data.id, flavors),
    )


@router.callback_query(Adm.filter(F.a.in_({"ftog", "fdel"})))
async def flavor_change(call: CallbackQuery, callback_data: Adm):
    if callback_data.a == "ftog":
        await db.toggle_flavor(callback_data.id)
    else:
        await db.delete_flavor(callback_data.id)
    await call.answer("Готово")
    await call.message.edit_reply_markup(reply_markup=admin_flavors_kb(callback_data.x, await db.flavors(callback_data.x)))


# ---------- заказы ----------

@router.callback_query(Adm.filter(F.a == "orders"))
async def orders(call: CallbackQuery):
    await call.answer()
    rows = await db.active_orders()
    if not rows:
        await call.message.answer("Активных заказов нет 👌")
        return
    kb = InlineKeyboardBuilder()
    for o in rows:
        kb.button(text=f"#{o['id']} · {o['customer_name']} · {money(o['total'])} · {STATUS[o['status']]}",
                  callback_data=Adm(a="o", id=o["id"]))
    kb.adjust(1)
    await call.message.answer("📦 <b>Активные заказы:</b>", reply_markup=kb.as_markup())


@router.callback_query(Adm.filter(F.a == "o"))
async def order_open(call: CallbackQuery, callback_data: Adm):
    await call.answer()
    o = await db.order(callback_data.id)
    if not o:
        await call.message.answer("Заказ не найден.")
        return
    await call.message.answer(order_text(o, await db.order_items(o["id"]), admin=True),
                              reply_markup=order_status_kb(o["id"], o["status"]))


@router.callback_query(OrdSt.filter())
async def order_status(call: CallbackQuery, callback_data: OrdSt, bot: Bot):
    oid, st = callback_data.oid, callback_data.st
    o = await db.order(oid)
    if not o:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await db.set_order_status(oid, st)
    o = await db.order(oid)
    await call.answer(f"Статус: {STATUS[st]}")
    try:
        await call.message.edit_text(order_text(o, await db.order_items(oid), admin=True),
                                     reply_markup=order_status_kb(oid, st))
    except TelegramBadRequest:
        pass
    try:
        await bot.send_message(o["user_id"], STATUS_CLIENT_MSG[st].format(id=oid))
    except Exception as e:
        log.warning("Не удалось уведомить клиента %s: %s", o["user_id"], e)


# ---------- статистика ----------

@router.callback_query(Adm.filter(F.a == "stats"))
async def stats(call: CallbackQuery):
    await call.answer()
    lines = ["📊 <b>Статистика</b> (без отменённых)", ""]
    for label, period in (("24 часа", "-1 day"), ("7 дней", "-7 days"), ("30 дней", "-30 days"), ("Всё время", None)):
        cnt, revenue = await db.stats(period)
        lines.append(f"<b>{label}:</b> {cnt} заказ(ов) · {money(revenue)}")
    lines += ["", f"👥 Пользователей: {await db.users_count()}"]
    top = await db.top_products()
    if top:
        lines += ["", "🏆 <b>Топ продаж:</b>"]
        for i, t in enumerate(top, 1):
            flavor = f" ({esc(t['flavor_name'])})" if t["flavor_name"] else ""
            lines.append(f"{i}. {esc(t['product_name'])}{flavor} — {t['qty']} шт. · {money(t['revenue'])}")
    await call.message.answer("\n".join(lines))


# ---------- рассылка ----------

@router.callback_query(Adm.filter(F.a == "bc"))
async def bc_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Broadcast.message)
    await call.message.answer(
        "📣 Отправьте сообщение для рассылки (текст, фото или видео с подписью) — "
        "его получат все покупатели.\n/cancel — отмена"
    )


@router.message(Broadcast.message, ~F.text.in_(MENU_TEXTS))
async def bc_message(message: Message, state: FSMContext):
    await state.update_data(chat_id=message.chat.id, message_id=message.message_id)
    await state.set_state(Broadcast.confirm)
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Отправить всем", callback_data=Adm(a="bc_go"))
    kb.button(text="Отмена", callback_data=Adm(a="menu"))
    await message.answer(f"Отправить это сообщение {len(await db.all_user_ids())} пользователям?",
                         reply_markup=kb.as_markup())


@router.callback_query(Broadcast.confirm, Adm.filter(F.a == "bc_go"))
async def bc_go(call: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    await call.answer("Рассылка запущена")
    await call.message.edit_text("⏳ Рассылка идёт...")
    ok = fail = 0
    for uid in await db.all_user_ids():
        try:
            await bot.copy_message(uid, d["chat_id"], d["message_id"])
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)  # лимит Telegram ~30 сообщений/сек
    await call.message.edit_text(f"✅ Рассылка завершена: доставлено {ok}, ошибок {fail}.")
