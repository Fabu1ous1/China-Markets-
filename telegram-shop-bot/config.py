import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in _list("ADMIN_IDS")}

SHOP_NAME = os.getenv("SHOP_NAME", "Vape Shop")
CURRENCY = os.getenv("CURRENCY", "$")
# 0 — отключить проверку возраста
MIN_AGE = int(os.getenv("MIN_AGE", "18"))
# Часовой пояс продавца для отображения времени заказов (UTC+N)
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "8"))

DELIVERY_METHODS = _list("DELIVERY_METHODS", "Курьер,Самовывоз,Транспортная компания")
PAYMENT_METHODS = _list("PAYMENT_METHODS", "Наличные,Перевод на карту,USDT")
CONTACTS = os.getenv("CONTACTS", "Пишите менеджеру: @your_manager").replace("\\n", "\n")

# 1 — цены скрыты: клиент оформляет заявку, админ присылает цену, клиент подтверждает.
# Отдельный товар можно сделать «по запросу», указав цену «-» в админке.
HIDE_PRICES = os.getenv("HIDE_PRICES", "0") == "1"

# Регион доставки (для США — штаты). Пусто — шаг не спрашивается.
REGION_LABEL = os.getenv("REGION_LABEL", "Штат")
REGIONS = [x.upper() for x in _list("REGIONS")]
BLOCKED_REGIONS = {x.upper() for x in _list("BLOCKED_REGIONS")}

DB_PATH = os.getenv("DB_PATH", "shop.db")
