#!/usr/bin/env python3
"""
Телеграм-бот "Погода" с ежедневными оповещениями
Упрощенная версия для Render.com
"""

import os
import asyncio
import aiohttp
import logging
import json
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
import threading
import schedule

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.constants import ParseMode

# ============= КОНФИГУРАЦИЯ =============
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    RENDER_WAKEUP_URL = os.getenv("RENDER_WAKEUP_URL", "")
    
    POPULAR_CITIES = [
        "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
        "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
        "Уфа", "Красноярск", "Пермь", "Воронеж", "Волгоград",
        "Минск", "Киев", "Астана", "Бишкек", "Ташкент", "Йошкар-Ола"
    ]
    
    CITY_ALIASES = {
        "йошкар дыра": "Йошкар-Ола",
        "йошкардыра": "Йошкар-Ола",
        "йошкар": "Йошкар-Ола",
        "спб": "Санкт-Петербург",
        "питер": "Санкт-Петербург",
        "нск": "Новосибирск",
        "екб": "Екатеринбург"
    }
    
    TIME_SLOTS = ["07:00", "08:00", "09:00", "10:00", "18:00", "19:00", "20:00"]

# ============= ЛОГГИРОВАНИЕ =============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ =============
# Внимание: данные очистятся при перезагрузке сервера!
user_data = defaultdict(dict)
weather_cache = {}
notifications = defaultdict(dict)

# ============= ПОМОЩНИКИ =============
def normalize_city(city: str) -> str:
    """Нормализация названия города"""
    city_lower = city.lower().strip()
    if city_lower in Config.CITY_ALIASES:
        return Config.CITY_ALIASES[city_lower]
    
    for popular in Config.POPULAR_CITIES:
        if city_lower == popular.lower():
            return popular
    
    return city.strip().title()

def save_user_data():
    """Сохранение данных пользователя (в памяти)"""
    # В этой упрощенной версии данные только в памяти
    pass

def load_user_data():
    """Загрузка данных пользователя"""
    # В этой версии данные только в памяти
    pass

# ============= СЕРВИС ПОГОДЫ =============
async def get_weather_async(city: str, days: int = 1) -> Optional[Dict]:
    """Получение прогноза погоды"""
    try:
        async with aiohttp.ClientSession() as session:
            # Геокодирование
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
            async with session.get(geo_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("results"):
                        result = data["results"][0]
                        lat = result["latitude"]
                        lon = result["longitude"]
                        city_name = result.get("name", city)
                        
                        # Погода на сегодня
                        weather_url = "https://api.open-meteo.com/v1/forecast"
                        params = {
                            "latitude": lat,
                            "longitude": lon,
                            "hourly": ["temperature_2m", "precipitation", "weather_code"],
                            "daily": ["temperature_2m_max", "temperature_2m_min", 
                                     "precipitation_sum", "wind_speed_10m_max",
                                     "weather_code"],
                            "timezone": "auto",
                            "forecast_days": 1
                        }
                        
                        async with session.get(weather_url, params=params) as weather_response:
                            if weather_response.status == 200:
                                weather_data = await weather_response.json()
                                return {
                                    "city": city_name,
                                    "current": weather_data.get("current", {}),
                                    "daily": weather_data.get("daily", {}),
                                    "hourly": weather_data.get("hourly", {})
                                }
    except Exception as e:
        logger.error(f"Ошибка получения погоды: {e}")
    return None

def get_weather_emoji(weather_code: int) -> str:
    """Получение эмодзи по коду погоды"""
    # Коды погоды от Open-Meteo
    if weather_code in [0, 1]:
        return "☀️"  # Ясно или малооблачно
    elif weather_code in [2, 3]:
        return "⛅"  # Переменная облачность
    elif weather_code in [45, 48]:
        return "🌫️"  # Туман
    elif weather_code in [51, 53, 55]:
        return "🌧️"  # Морось
    elif weather_code in [61, 63, 65]:
        return "🌧️"  # Дождь
    elif weather_code in [71, 73, 75]:
        return "❄️"  # Снег
    elif weather_code in [95, 96, 99]:
        return "⛈️"  # Гроза
    else:
        return "🌤️"

def format_weather_daily(forecast: Dict) -> str:
    """Форматирование погоды на день"""
    if not forecast or "daily" not in forecast:
        return "❌ Не удалось получить прогноз погоды"
    
    daily = forecast["daily"]
    city = forecast.get("city", "Неизвестный город")
    
    # Получаем данные на сегодня
    dates = daily.get("time", [])
    temps_max = daily.get("temperature_2m_max", [])
    temps_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    wind = daily.get("wind_speed_10m_max", [])
    weather_codes = daily.get("weather_code", [])
    
    if not dates:
        return "❌ Нет данных о погоде"
    
    # Берем данные на сегодня (первый элемент)
    try:
        weather_code = weather_codes[0] if weather_codes else 0
        weather_emoji = get_weather_emoji(weather_code)
        
        lines = [f"<b>{weather_emoji} Погода в {city} на сегодня</b>\n"]
        lines.append("─" * 30)
        
        if temps_max and temps_min:
            lines.append(f"🌡️ <b>Температура:</b> {temps_min[0]:.0f}°C ... {temps_max[0]:.0f}°C")
        
        if precip:
            if precip[0] > 0:
                lines.append(f"💧 <b>Осадки:</b> {precip[0]:.1f} мм")
            else:
                lines.append(f"💧 <b>Осадки:</b> нет")
        
        if wind:
            lines.append(f"💨 <b>Ветер:</b> {wind[0]:.1f} м/с")
        
        # Описание погоды по коду
        if weather_code == 0:
            lines.append(f"📝 <b>Описание:</b> Ясно")
        elif weather_code == 1:
            lines.append(f"📝 <b>Описание:</b> Преимущественно ясно")
        elif weather_code == 2:
            lines.append(f"📝 <b>Описание:</b> Переменная облачность")
        elif weather_code == 3:
            lines.append(f"📝 <b>Описание:</b> Пасмурно")
        elif weather_code >= 45 and weather_code <= 48:
            lines.append(f"📝 <b>Описание:</b> Туман")
        elif weather_code >= 51 and weather_code <= 55:
            lines.append(f"📝 <b>Описание:</b> Морось")
        elif weather_code >= 61 and weather_code <= 65:
            lines.append(f"📝 <b>Описание:</b> Дождь")
        elif weather_code >= 71 and weather_code <= 77:
            lines.append(f"📝 <b>Описание:</b> Снег")
        elif weather_code >= 80 and weather_code <= 82:
            lines.append(f"📝 <b>Описание:</b> Ливень")
        elif weather_code >= 95 and weather_code <= 99:
            lines.append(f"📝 <b>Описание:</b> Гроза")
        
        lines.append(f"\n🕐 <i>Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
        
        return "\n".join(lines)
        
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка форматирования: {e}")
        return "❌ Ошибка обработки данных о погоде"

# ============= КЛАВИАТУРЫ =============
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню"""
    keyboard = [
        [InlineKeyboardButton("🌤️ Погода сейчас", callback_data="weather_now")],
        [InlineKeyboardButton("📍 Выбрать город", callback_data="select_city")],
        [InlineKeyboardButton("⏰ Ежедневное оповещение", callback_data="notifications")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_city_selection_keyboard() -> InlineKeyboardMarkup:
    """Выбор города"""
    keyboard = []
    
    # Добавляем популярные города
    for i in range(0, len(Config.POPULAR_CITIES), 3):
        row = []
        for city in Config.POPULAR_CITIES[i:i+3]:
            row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton("✏️ Ввести город", callback_data="input_city"),
        InlineKeyboardButton("↩️ Назад", callback_data="back_main")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_notification_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Меню уведомлений"""
    user_notifications = notifications.get(user_id, {})
    city = user_notifications.get("city", "Не выбран")
    time_slot = user_notifications.get("time", "Не установлено")
    enabled = user_notifications.get("enabled", False)
    
    status = "✅ ВКЛ" if enabled else "❌ ВЫКЛ"
    
    keyboard = [
        [InlineKeyboardButton(f"📍 Город: {city}", callback_data="notif_city")],
        [InlineKeyboardButton(f"⏰ Время: {time_slot}", callback_data="notif_time")],
        [InlineKeyboardButton(f"🔔 Статус: {status}", callback_data="notif_toggle")],
        [InlineKeyboardButton("↩️ Назад", callback_data="back_main")]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def get_time_selection_keyboard() -> InlineKeyboardMarkup:
    """Выбор времени"""
    keyboard = []
    
    for i in range(0, len(Config.TIME_SLOTS), 3):
        row = []
        for time_slot in Config.TIME_SLOTS[i:i+3]:
            row.append(InlineKeyboardButton(time_slot, callback_data=f"time_{time_slot}"))
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="notifications")])
    
    return InlineKeyboardMarkup(keyboard)

# ============= ОБРАБОТЧИКИ =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    user_id = user.id
    
    # Инициализация данных пользователя
    if user_id not in user_data:
        user_data[user_id] = {
            "name": user.first_name,
            "city": "Москва",  # город по умолчанию
            "notifications": False
        }
    
    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"🌤️ <b>Погодный бот</b> с ежедневными оповещениями\n\n"
        f"<i>Выберите действие:</i>"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    action = query.data
    
    if action == "back_main":
        await query.edit_message_text(
            "🌤️ <b>Главное меню</b>\n\n<i>Выберите действие:</i>",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "weather_now":
        city = user_data.get(user_id, {}).get("city", "Москва")
        await get_weather_for_user(query, user_id, city)
    
    elif action == "select_city":
        await query.edit_message_text(
            "📍 <b>Выберите город:</b>",
            reply_markup=get_city_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action.startswith("city_"):
        city = action[5:]
        user_data[user_id]["city"] = city
        await query.edit_message_text(
            f"✅ Город установлен: <b>{city}</b>\n\nЧто дальше?",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "input_city":
        await query.edit_message_text(
            "✏️ <b>Введите название города:</b>\n\n<i>Например: Москва, Йошкар-Ола</i>",
            parse_mode=ParseMode.HTML
        )
    
    elif action == "notifications":
        await query.edit_message_text(
            "⏰ <b>Ежедневные оповещения</b>\n\n"
            "Настройте время получения ежедневного прогноза погоды:",
            reply_markup=get_notification_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "notif_city":
        await query.edit_message_text(
            "📍 <b>Выберите город для оповещений:</b>",
            reply_markup=get_city_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "notif_time":
        await query.edit_message_text(
            "⏰ <b>Выберите время оповещения:</b>",
            reply_markup=get_time_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action.startswith("time_"):
        time_slot = action[5:]
        if user_id not in notifications:
            notifications[user_id] = {}
        notifications[user_id]["time"] = time_slot
        
        await query.edit_message_text(
            f"✅ Время оповещения установлено: <b>{time_slot}</b>\n\n"
            "Не забудьте включить оповещения и выбрать город!",
            reply_markup=get_notification_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "notif_toggle":
        if user_id not in notifications:
            notifications[user_id] = {"enabled": True}
        else:
            notifications[user_id]["enabled"] = not notifications[user_id].get("enabled", False)
        
        status = "включены" if notifications[user_id]["enabled"] else "выключены"
        await query.answer(f"🔔 Оповещения {status}")
        await query.edit_message_text(
            "⏰ <b>Ежедневные оповещения</b>\n\n"
            "Настройте время получения ежедневного прогноза погоды:",
            reply_markup=get_notification_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "settings":
        city = user_data.get(user_id, {}).get("city", "Не установлен")
        
        settings_text = (
            f"⚙️ <b>Настройки</b>\n\n"
            f"📍 <b>Текущий город:</b> {city}\n"
            f"👤 <b>Пользователь:</b> {query.from_user.first_name}\n\n"
            f"<i>Для смены города нажмите 'Выбрать город' в главном меню</i>"
        )
        
        await query.edit_message_text(
            settings_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Назад", callback_data="back_main")]
            ]),
            parse_mode=ParseMode.HTML
        )

async def get_weather_for_user(query, user_id: int, city: str):
    """Получить погоду для пользователя"""
    await query.edit_message_text(
        f"⏳ <b>Загружаю погоду для {city}...</b>",
        parse_mode=ParseMode.HTML
    )
    
    forecast = await get_weather_async(city)
    
    if forecast:
        formatted = format_weather_daily(forecast)
        
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data="weather_now")],
            [InlineKeyboardButton("📍 Сменить город", callback_data="select_city")],
            [InlineKeyboardButton("⏰ Настроить оповещения", callback_data="notifications")]
        ]
        
        await query.edit_message_text(
            formatted,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    else:
        keyboard = [[InlineKeyboardButton("↩️ Попробовать снова", callback_data="weather_now")]]
        await query.edit_message_text(
            f"❌ <b>Не удалось получить погоду для {city}</b>\n\n"
            f"<i>Проверьте название города или попробуйте позже.</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if not text or text.startswith('/'):
        return
    
    # Если пользователь ввел город
    normalized_city = normalize_city(text)
    user_data[user_id]["city"] = normalized_city
    
    message = await update.message.reply_text(
        f"⏳ <b>Загружаю погоду для {normalized_city}...</b>",
        parse_mode=ParseMode.HTML
    )
    
    forecast = await get_weather_async(normalized_city)
    
    if forecast:
        formatted = format_weather_daily(forecast)
        
        keyboard = [
            [InlineKeyboardButton("📍 Сменить город", callback_data="select_city")],
            [InlineKeyboardButton("⏰ Настроить оповещения", callback_data="notifications")],
            [InlineKeyboardButton("🌤️ Главное меню", callback_data="back_main")]
        ]
        
        await message.edit_text(
            formatted,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.edit_text(
            f"❌ <b>Не удалось получить погоду для {normalized_city}</b>",
            parse_mode=ParseMode.HTML
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=True)

# ============= СИСТЕМА ОПОВЕЩЕНИЙ =============
async def send_daily_notifications(app):
    """Отправка ежедневных оповещений"""
    logger.info("🔄 Проверка оповещений...")
    
    current_time = datetime.now().strftime("%H:%M")
    
    for user_id, notif_data in notifications.items():
        if notif_data.get("enabled") and notif_data.get("time") == current_time:
            city = notif_data.get("city", user_data.get(user_id, {}).get("city", "Москва"))
            
            try:
                forecast = await get_weather_async(city)
                if forecast:
                    formatted = format_weather_daily(forecast)
                    
                    # Добавляем приветствие для утренних оповещений
                    hour = int(current_time.split(":")[0])
                    greeting = "🌅 Доброе утро!" if hour < 12 else "🌇 Добрый день!" if hour < 18 else "🌃 Добрый вечер!"
                    
                    message_text = f"{greeting}\n\n{formatted}"
                    
                    await app.bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML
                    )
                    
                    logger.info(f"✅ Отправлено оповещение пользователю {user_id}")
                    
            except Exception as e:
                logger.error(f"❌ Ошибка отправки оповещения пользователю {user_id}: {e}")

def notification_scheduler(app):
    """Планировщик оповещений"""
    async def check_and_send():
        await send_daily_notifications(app)
    
    def run_scheduler():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Проверяем каждую минуту
        schedule.every(1).minutes.do(lambda: loop.create_task(check_and_send()))
        
        while True:
            schedule.run_pending()
            loop.run_until_complete(asyncio.sleep(1))
    
    # Запускаем планировщик в отдельном потоке
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ Служба оповещений запущена")

# ============= ПРОБУЖДЕНИЕ RENDER =============
async def wakeup_render():
    """Пробуждение Render"""
    if Config.RENDER_WAKEUP_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.RENDER_WAKEUP_URL, timeout=10):
                    logger.info("🔄 Render пробужден")
        except Exception as e:
            logger.error(f"❌ Ошибка пробуждения Render: {e}")

def render_wakeup_scheduler():
    """Планировщик пробуждения Render"""
    async def wakeup_task():
        await wakeup_render()
    
    def run_wakeup():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Будим каждые 10 минут
        schedule.every(10).minutes.do(lambda: loop.create_task(wakeup_task()))
        
        while True:
            schedule.run_pending()
            loop.run_until_complete(asyncio.sleep(1))
    
    wakeup_thread = threading.Thread(target=run_wakeup, daemon=True)
    wakeup_thread.start()
    logger.info("✅ Служба пробуждения Render запущена")

# ============= ОСНОВНАЯ ФУНКЦИЯ =============
def main():
    """Запуск бота"""
    if not Config.BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        return
    
    logger.info("🤖 Бот запускается...")
    
    # Создаем приложение
    app = Application.builder().token(Config.BOT_TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    
    # Запускаем планировщик оповещений
    notification_scheduler(app)
    
    # Запускаем пробуждение Render
    if Config.RENDER_WAKEUP_URL:
        render_wakeup_scheduler()
    
    logger.info("✅ Бот запущен и ожидает сообщений...")
    
    # Запускаем бота
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
