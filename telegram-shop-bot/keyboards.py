from aiogram.filters.callback_data import CallbackData
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import ADMIN_IDS
from utils import STATUS, money

BTN_CATALOG = "🛍 Каталог"
BTN_CART = "🛒 Корзина"
BTN_ORDERS = "📦 Мои заказы"
BTN_CONTACTS = "ℹ️ Контакты"
BTN_ADMIN = "⚙️ Админка"
BTN_CANCEL = "❌ Отмена"
BTN_SKIP = "➡️ Пропустить"
MENU_TEXTS = {BTN_CATALOG, BTN_CART, BTN_ORDERS, BTN_CONTACTS, BTN_ADMIN, BTN_CANCEL}


class Age(CallbackData, prefix="age"):
    ok: int


class Nav(CallbackData, prefix="nav"):
    to: str


class Cat(CallbackData, prefix="cat"):
    id: int


class Prod(CallbackData, prefix="prod"):
    id: int


class Flv(CallbackData, prefix="flv"):
    pid: int
    fid: int


class Qty(CallbackData, prefix="qty"):
    pid: int
    fid: int
    qty: int
    add: int = 0


class CartAct(CallbackData, prefix="cart"):
    action: str
    pid: int = 0
    fid: int = 0


class Pick(CallbackData, prefix="pick"):
    kind: str
    idx: int


class OrdSt(CallbackData, prefix="ost"):
    oid: int
    st: str


class Adm(CallbackData, prefix="adm"):
    a: str
    id: int = 0
    x: int = 0


# ---------- reply ----------

def main_menu(uid: int) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=BTN_CATALOG)
    kb.button(text=BTN_CART)
    kb.button(text=BTN_ORDERS)
    kb.button(text=BTN_CONTACTS)
    if uid in ADMIN_IDS:
        kb.button(text=BTN_ADMIN)
    kb.adjust(2, 2, 1)
    return kb.as_markup(resize_keyboard=True)


def input_kb(*suggestions: str, contact: bool = False, skip: bool = False) -> ReplyKeyboardMarkup:
    rows = []
    if contact:
        rows.append([KeyboardButton(text="📱 Отправить мой номер", request_contact=True)])
    rows += [[KeyboardButton(text=s)] for s in suggestions if s]
    if skip:
        rows.append([KeyboardButton(text=BTN_SKIP)])
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------- inline: shop ----------

def age_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, мне есть", callback_data=Age(ok=1))
    kb.button(text="❌ Нет", callback_data=Age(ok=0))
    return kb.as_markup()


def categories_kb(cats):
    kb = InlineKeyboardBuilder()
    for c in cats:
        kb.button(text=c["name"], callback_data=Cat(id=c["id"]))
    kb.adjust(2)
    return kb.as_markup()


def products_kb(prods):
    kb = InlineKeyboardBuilder()
    for p in prods:
        kb.button(text=f"{p['name']} — {money(p['price'])}", callback_data=Prod(id=p["id"]))
    kb.button(text="⬅️ Категории", callback_data=Nav(to="catalog"))
    kb.adjust(1)
    return kb.as_markup()


def product_kb(p, flavors):
    kb = InlineKeyboardBuilder()
    if flavors:
        for f in flavors:
            text = f"🍬 {f['name']}" if f["in_stock"] else f"❌ {f['name']} (нет)"
            kb.button(text=text, callback_data=Flv(pid=p["id"], fid=f["id"]))
        n = len(flavors)
        sizes = [2] * (n // 2) + [1] * (n % 2)
    else:
        kb.button(text="🛒 Выбрать количество", callback_data=Qty(pid=p["id"], fid=0, qty=1))
        sizes = [1]
    kb.button(text="⬅️ К товарам", callback_data=Cat(id=p["category_id"]))
    kb.button(text="🛒 Корзина", callback_data=Nav(to="cart"))
    kb.adjust(*sizes, 2)
    return kb.as_markup()


def qty_kb(pid: int, fid: int, qty: int, price: float):
    kb = InlineKeyboardBuilder()
    kb.button(text="➖", callback_data=Qty(pid=pid, fid=fid, qty=max(1, qty - 1)))
    kb.button(text=str(qty), callback_data=Nav(to="noop"))
    kb.button(text="➕", callback_data=Qty(pid=pid, fid=fid, qty=min(99, qty + 1)))
    kb.button(text="➕5", callback_data=Qty(pid=pid, fid=fid, qty=min(99, qty + 5)))
    kb.button(text=f"✅ В корзину — {money(price * qty)}", callback_data=Qty(pid=pid, fid=fid, qty=qty, add=1))
    kb.button(text="⬅️ Назад к товару", callback_data=Prod(id=pid))
    kb.adjust(4, 1, 1)
    return kb.as_markup()


def added_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Корзина", callback_data=Nav(to="cart"))
    kb.button(text="🛍 Продолжить покупки", callback_data=Nav(to="catalog"))
    kb.adjust(2)
    return kb.as_markup()


def cart_kb(items):
    kb = InlineKeyboardBuilder()
    for it in items:
        label = f"{it['name']} {it['flavor']}".strip()
        kb.button(text=f"❌ {label}", callback_data=CartAct(action="del", pid=it["product_id"], fid=it["flavor_id"]))
    kb.button(text="🗑 Очистить", callback_data=CartAct(action="clear"))
    kb.button(text="✅ Оформить заказ", callback_data=CartAct(action="checkout"))
    kb.adjust(*([1] * len(items)), 2)
    return kb.as_markup()


def pick_kb(kind: str, options: list[str]):
    kb = InlineKeyboardBuilder()
    for i, o in enumerate(options):
        kb.button(text=o, callback_data=Pick(kind=kind, idx=i))
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить заказ", callback_data=CartAct(action="confirm"))
    kb.button(text="❌ Отменить", callback_data=CartAct(action="abort"))
    kb.adjust(1)
    return kb.as_markup()


# ---------- inline: admin ----------

def order_status_kb(oid: int, current: str):
    kb = InlineKeyboardBuilder()
    for st in ("accepted", "shipped", "delivered", "cancelled"):
        if st != current:
            kb.button(text=STATUS[st], callback_data=OrdSt(oid=oid, st=st))
    kb.adjust(2)
    return kb.as_markup()


def admin_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить товар", callback_data=Adm(a="add"))
    kb.button(text="📋 Товары", callback_data=Adm(a="prods"))
    kb.button(text="📂 Категории", callback_data=Adm(a="cats"))
    kb.button(text="📦 Активные заказы", callback_data=Adm(a="orders"))
    kb.button(text="📊 Статистика", callback_data=Adm(a="stats"))
    kb.button(text="📣 Рассылка", callback_data=Adm(a="bc"))
    kb.adjust(1, 2, 1, 2)
    return kb.as_markup()


def admin_product_kb(p):
    pid = p["id"]
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=Adm(a="e_name", id=pid))
    kb.button(text="📝 Описание", callback_data=Adm(a="e_description", id=pid))
    kb.button(text="💲 Цена", callback_data=Adm(a="e_price", id=pid))
    kb.button(text="🖼 Фото/видео", callback_data=Adm(a="e_media", id=pid))
    kb.button(text="➕ Вкусы", callback_data=Adm(a="e_flavors", id=pid))
    kb.button(text="🍬 Наличие вкусов", callback_data=Adm(a="flv", id=pid))
    kb.button(text="🙈 Скрыть" if p["active"] else "👁 Показать", callback_data=Adm(a="toggle", id=pid))
    kb.button(text="🗑 Удалить", callback_data=Adm(a="del", id=pid))
    kb.button(text="⬅️ К товарам", callback_data=Adm(a="prods"))
    kb.adjust(3, 3, 2, 1)
    return kb.as_markup()


def admin_flavors_kb(pid: int, flavors):
    kb = InlineKeyboardBuilder()
    for f in flavors:
        kb.button(text=("✅ " if f["in_stock"] else "❌ ") + f["name"], callback_data=Adm(a="ftog", id=f["id"], x=pid))
        kb.button(text="🗑", callback_data=Adm(a="fdel", id=f["id"], x=pid))
    kb.button(text="⬅️ К товару", callback_data=Adm(a="p", id=pid))
    kb.adjust(*([2] * len(flavors)), 1)
    return kb.as_markup()
