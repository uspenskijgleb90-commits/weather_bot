#!/usr/bin/env python3
"""
🌤️ Weather Bot - Телеграм-бот погоды с уведомлениями
✨ Красивые эмодзи, сохранение состояния
🚀 Адаптирован для Render.com, Python 3.13.4
"""

import os
import asyncio
import aiohttp
import logging
from datetime import datetime
from typing import Dict, List, Optional
from collections import defaultdict
import threading
import time

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
    
    # 🏙️ Города для быстрого доступа
    POPULAR_CITIES = [
        "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
        "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
        "Уфа", "Красноярск", "Пермь", "Воронеж", "Волгоград", "Йошкар-Ола",
        "Минск", "Киев", "Астана", "Бишкек", "Ташкент", "Алматы", "Баку",
        "Тбилиси", "Ереван", "Кишинев", "Вильнюс", "Рига", "Таллин",
        "Харьков", "Одесса", "Львов", "Днепр", "Запорожье", "Брест",
        "Гомель", "Витебск", "Махачкала", "Симферополь", "Севастополь"
    ]
    
    # 🔄 Псевдонимы городов
    CITY_ALIASES = {
        "йошкар дыра": "Йошкар-Ола",
        "йошкардыра": "Йошкар-Ола",
        "йошкар": "Йошкар-Ола",
        "спб": "Санкт-Петербург",
        "питер": "Санкт-Петербург",
        "нск": "Новосибирск",
        "екб": "Екатеринбург",
        "нн": "Нижний Новгород",
        "челяба": "Челябинск"
    }
    
    # ⏰ Время для уведомлений (UTC)
    TIME_SLOTS = ["05:00", "06:00", "07:00", "08:00", "09:00", "15:00", "16:00", "17:00", "18:00"]

# ============= ЛОГГИРОВАНИЕ =============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ =============
# Внимание: на Render.com данные очистятся при перезапуске
# Это нормально для бесплатного хостинга
user_sessions = defaultdict(dict)
weather_cache = {}
notifications = defaultdict(dict)
last_notification = {}

# ============= ПОМОЩНИКИ =============
def normalize_city(city: str) -> str:
    """Нормализация названия города"""
    city_lower = city.lower().strip()
    
    # Проверяем псевдонимы
    if city_lower in Config.CITY_ALIASES:
        return Config.CITY_ALIASES[city_lower]
    
    # Ищем в популярных городах
    for popular_city in Config.POPULAR_CITIES:
        if city_lower == popular_city.lower():
            return popular_city
    
    return city.strip().title()

def get_user_city(user_id: int) -> str:
    """Получение города пользователя"""
    session = user_sessions.get(user_id, {})
    return session.get("city", "Москва")  # По умолчанию Москва

def set_user_city(user_id: int, city: str):
    """Установка города пользователя"""
    if user_id not in user_sessions:
        user_sessions[user_id] = {}
    user_sessions[user_id]["city"] = normalize_city(city)

# ============= СЕРВИС ПОГОДЫ =============
async def get_weather_async(city: str) -> Optional[Dict]:
    """Получение прогноза погоды"""
    normalized_city = normalize_city(city)
    cache_key = f"weather_{normalized_city}"
    
    # Проверяем кэш (15 минут)
    if cache_key in weather_cache:
        timestamp, data = weather_cache[cache_key]
        if time.time() - timestamp < 900:  # 15 минут
            return data
    
    try:
        async with aiohttp.ClientSession() as session:
            # 🔍 Ищем координаты города
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={normalized_city}&count=1"
            async with session.get(geo_url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("results"):
                        result = data["results"][0]
                        lat = result["latitude"]
                        lon = result["longitude"]
                        city_name = result.get("name", normalized_city)
                        
                        # 🌤️ Получаем погоду
                        weather_url = "https://api.open-meteo.com/v1/forecast"
                        params = {
                            "latitude": lat,
                            "longitude": lon,
                            "daily": ["temperature_2m_max", "temperature_2m_min", 
                                     "precipitation_sum", "wind_speed_10m_max",
                                     "weather_code"],
                            "timezone": "auto",
                            "forecast_days": 1
                        }
                        
                        async with session.get(weather_url, params=params, timeout=10) as weather_response:
                            if weather_response.status == 200:
                                weather_data = await weather_response.json()
                                
                                forecast = {
                                    "city": city_name,
                                    "daily": weather_data.get("daily", {})
                                }
                                
                                # 💾 Кэшируем
                                weather_cache[cache_key] = (time.time(), forecast)
                                return forecast
    except Exception as e:
        logger.error(f"❌ Ошибка получения погоды: {e}")
    
    return None

def get_weather_emoji(weather_code: int) -> str:
    """✨ Красивые эмодзи для погоды"""
    if weather_code == 0:
        return "☀️"  # Ясно
    elif weather_code == 1:
        return "🌤️"  # Преимущественно ясно
    elif weather_code == 2:
        return "⛅"  # Переменная облачность
    elif weather_code == 3:
        return "☁️"  # Пасмурно
    elif weather_code in [45, 48]:
        return "🌫️"  # Туман
    elif weather_code in [51, 53, 55]:
        return "🌦️"  # Морось
    elif weather_code in [61, 63, 65]:
        return "🌧️"  # Дождь
    elif weather_code in [71, 73, 75]:
        return "❄️"  # Снег
    elif weather_code in [77]:
        return "🌨️"  # Град
    elif weather_code in [80, 81, 82]:
        return "⛈️"  # Ливень
    elif weather_code in [85, 86]:
        return "🌨️"  # Снегопад
    elif weather_code in [95, 96, 99]:
        return "⛈️"  # Гроза
    else:
        return "🌤️"

def get_temperature_emoji(temp: float) -> str:
    """🌡️ Эмодзи для температуры"""
    if temp > 30:
        return "🔥"
    elif temp > 25:
        return "🥵"
    elif temp > 20:
        return "☀️"
    elif temp > 15:
        return "😊"
    elif temp > 10:
        return "😐"
    elif temp > 5:
        return "🧥"
    elif temp > 0:
        return "❄️"
    elif temp > -10:
        return "🥶"
    else:
        return "🧊"

def format_weather_daily(forecast: Dict) -> str:
    """✨ Красивое форматирование погоды"""
    if not forecast or "daily" not in forecast:
        return "❌ Не удалось получить прогноз погоды"
    
    daily = forecast["daily"]
    city = forecast.get("city", "Неизвестный город")
    
    dates = daily.get("time", [])
    temps_max = daily.get("temperature_2m_max", [])
    temps_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    wind = daily.get("wind_speed_10m_max", [])
    weather_codes = daily.get("weather_code", [])
    
    if not dates:
        return "❌ Нет данных о погоде"
    
    try:
        weather_code = weather_codes[0] if weather_codes else 0
        weather_emoji = get_weather_emoji(weather_code)
        temp_avg = (temps_max[0] + temps_min[0]) / 2 if temps_max and temps_min else 0
        temp_emoji = get_temperature_emoji(temp_avg)
        
        # 🎨 Форматируем красиво
        lines = []
        lines.append(f"✨ <b>{weather_emoji} Погода в {city}</b> ✨")
        lines.append("══════════════════════════")
        
        # 🌡️ Температура
        if temps_max and temps_min:
            lines.append(f"{temp_emoji} <b>Температура:</b> <code>{temps_min[0]:.0f}°C ... {temps_max[0]:.0f}°C</code>")
        
        # 💧 Осадки
        if precip:
            if precip[0] > 0:
                rain_emoji = "🌧️" if precip[0] < 5 else "🌨️" if precip[0] < 10 else "⛈️"
                lines.append(f"{rain_emoji} <b>Осадки:</b> <code>{precip[0]:.1f} мм</code>")
            else:
                lines.append(f"☀️ <b>Осадки:</b> <code>нет</code>")
        
        # 💨 Ветер
        if wind:
            wind_emoji = "🍃" if wind[0] < 5 else "💨" if wind[0] < 10 else "🌬️"
            lines.append(f"{wind_emoji} <b>Ветер:</b> <code>{wind[0]:.1f} м/с</code>")
        
        # 📝 Описание
        descriptions = {
            0: "Ясно и солнечно ☀️",
            1: "Преимущественно ясно 🌤️",
            2: "Переменная облачность ⛅",
            3: "Пасмурно ☁️",
            45: "Туманно 🌫️",
            48: "Туман с инеем ❄️",
            51: "Легкая морось 🌦️",
            53: "Умеренная морось 🌧️",
            55: "Сильная морось 🌧️",
            61: "Небольшой дождь 🌧️",
            63: "Умеренный дождь 🌧️",
            65: "Сильный дождь 🌧️",
            71: "Небольшой снег ❄️",
            73: "Умеренный снег ❄️",
            75: "Сильный снег ❄️",
            77: "Град 🌨️",
            80: "Кратковременный дождь ⛈️",
            81: "Умеренный ливень ⛈️",
            82: "Сильный ливень ⛈️",
            85: "Небольшой снегопад 🌨️",
            86: "Сильный снегопад 🌨️",
            95: "Гроза ⛈️",
            96: "Гроза с градом ⛈️",
            99: "Сильная гроза ⛈️"
        }
        
        desc = descriptions.get(weather_code, "Неизвестно 🌤️")
        lines.append(f"📝 <b>Описание:</b> {desc}")
        
        lines.append("══════════════════════════")
        lines.append(f"🕐 <i>Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"❌ Ошибка форматирования: {e}")
        return "❌ Ошибка обработки данных о погоде"

# ============= КРАСИВЫЕ КЛАВИАТУРЫ =============
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """✨ Главное меню с красивыми эмодзи"""
    keyboard = [
        [InlineKeyboardButton("🌤️ Погода сейчас", callback_data="weather_now")],
        [InlineKeyboardButton("📍 Выбрать город", callback_data="select_city")],
        [InlineKeyboardButton("⏰ Уведомления", callback_data="notifications")],
        [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_city_selection_keyboard() -> InlineKeyboardMarkup:
    """📍 Выбор города"""
    keyboard = []
    
    # 🏙️ Добавляем города по 3 в ряд
    for i in range(0, min(15, len(Config.POPULAR_CITIES)), 3):
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
    """⏰ Меню уведомлений"""
    notif_data = notifications.get(user_id, {})
    
    city = notif_data.get("city", "❓ Не выбран")
    utc_time = notif_data.get("utc_time", "❓ Не установлено")
    enabled = notif_data.get("enabled", False)
    
    status = "✅ ВКЛ" if enabled else "❌ ВЫКЛ"
    
    keyboard = [
        [InlineKeyboardButton(f"📍 Город: {city}", callback_data="notif_city")],
        [InlineKeyboardButton(f"⏰ Время UTC: {utc_time}", callback_data="notif_time")],
        [InlineKeyboardButton(f"🔔 Статус: {status}", callback_data="notif_toggle")],
        [InlineKeyboardButton("🗑️ Удалить настройки", callback_data="notif_delete")],
        [InlineKeyboardButton("↩️ Назад", callback_data="back_main")]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def get_time_selection_keyboard() -> InlineKeyboardMarkup:
    """⏰ Выбор времени"""
    keyboard = []
    
    for i in range(0, len(Config.TIME_SLOTS), 3):
        row = []
        for time_slot in Config.TIME_SLOTS[i:i+3]:
            row.append(InlineKeyboardButton(f"🕐 {time_slot}", callback_data=f"time_{time_slot}"))
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="notifications")])
    
    return InlineKeyboardMarkup(keyboard)

# ============= ОБРАБОТЧИКИ =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """✨ Команда /start с красивым приветствием"""
    user = update.effective_user
    user_id = user.id
    
    # 📝 Инициализируем сессию пользователя
    if user_id not in user_sessions:
        user_sessions[user_id] = {"city": "Москва"}
    
    # 🎨 Красивое приветствие
    welcome_text = (
        f"✨ <b>Добро пожаловать, {user.first_name}!</b> ✨\n\n"
        f"🌤️ <b>Weather Bot</b> - ваш личный метеоролог\n\n"
        f"<i>Что я умею:</i>\n"
        f"• 🌡️ Показывать текущую погоду\n"
        f"• 📍 Работать с городами СНГ\n"
        f"• ⏰ Отправлять ежедневные уведомления\n"
        f"• 🔄 Автоматически обновлять данные\n\n"
        f"<b>Выберите действие:</b>"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🔄 Обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    action = query.data
    
    # 🏠 Главное меню
    if action == "back_main":
        await show_main_menu(query)
    
    # 🌤️ Погода сейчас
    elif action == "weather_now":
        city = get_user_city(user_id)
        await get_weather_for_user(query, user_id, city)
    
    # 📍 Выбор города
    elif action == "select_city":
        await query.edit_message_text(
            "📍 <b>Выберите город:</b>\n\n"
            "<i>Самые популярные города:</i>",
            reply_markup=get_city_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    # 🏙️ Выбор конкретного города
    elif action.startswith("city_"):
        city = action[5:]  # Убираем "city_"
        set_user_city(user_id, city)
        
        await query.edit_message_text(
            f"✅ <b>Город установлен:</b> {city}\n\n"
            f"<i>Что дальше?</i>",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    # ✏️ Ввод города
    elif action == "input_city":
        await query.edit_message_text(
            "✏️ <b>Введите название города:</b>\n\n"
            "<i>Примеры: Москва, Йошкар-Ола, Санкт-Петербург</i>",
            parse_mode=ParseMode.HTML
        )
    
    # 📋 Список городов
    elif action == "city_list":
        cities_text = "📋 <b>Доступные города:</b>\n\n"
        
        for i in range(0, len(Config.POPULAR_CITIES), 5):
            chunk = Config.POPULAR_CITIES[i:i+5]
            cities_text += "• " + " • ".join(chunk) + "\n"
        
        keyboard = [
            [InlineKeyboardButton("📍 Выбрать город", callback_data="select_city")],
            [InlineKeyboardButton("↩️ Назад", callback_data="back_main")]
        ]
        
        await query.edit_message_text(
            cities_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    # ⏰ Уведомления
    elif action == "notifications":
        await show_notifications_menu(query, user_id)
    
    # 📍 Выбор города для уведомлений
    elif action == "notif_city":
        await query.edit_message_text(
            "📍 <b>Выберите город для уведомлений:</b>",
            reply_markup=get_city_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    # ⏰ Выбор времени для уведомлений
    elif action == "notif_time":
        await query.edit_message_text(
            "⏰ <b>Выберите время уведомления (UTC):</b>\n\n"
            "<i>Бот работает по времени UTC.\n"
            "Москва = UTC+3 (выберите время на 3 часа раньше)</i>",
            reply_markup=get_time_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    # 🕐 Выбор конкретного времени
    elif action.startswith("time_"):
        time_slot = action[5:]
        
        if user_id not in notifications:
            notifications[user_id] = {}
        
        notifications[user_id]["utc_time"] = time_slot
        
        city = notifications[user_id].get("city", "Не выбран")
        
        await query.edit_message_text(
            f"✅ <b>Время уведомления установлено:</b> {time_slot} UTC\n\n"
            f"<i>Не забудьте:</i>\n"
            f"1. 📍 Выбрать город: {city}\n"
            f"2. 🔔 Включить уведомления\n\n"
            f"<b>Готово к настройке!</b>",
            reply_markup=get_notification_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
    
    # 🔔 Включение/выключение уведомлений
    elif action == "notif_toggle":
        if user_id not in notifications:
            notifications[user_id] = {"enabled": True}
        else:
            notifications[user_id]["enabled"] = not notifications[user_id].get("enabled", False)
        
        status = "включены ✅" if notifications[user_id]["enabled"] else "выключены ❌"
        await query.answer(f"🔔 Уведомления {status}")
        await show_notifications_menu(query, user_id)
    
    # 🗑️ Удаление настроек уведомлений
    elif action == "notif_delete":
        if user_id in notifications:
            del notifications[user_id]
        await query.answer("🗑️ Настройки удалены")
        await show_main_menu(query)

async def show_main_menu(query):
    """🏠 Показать главное меню"""
    await query.edit_message_text(
        "🌤️ <b>Главное меню</b>\n\n"
        "<i>Выберите действие:</i>",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def show_notifications_menu(query, user_id):
    """⏰ Показать меню уведомлений"""
    notif_data = notifications.get(user_id, {})
    
    if not notif_data:
        text = (
            "⏰ <b>Настройка уведомлений</b>\n\n"
            "<i>Получайте ежедневный прогноз погоды!</i>\n\n"
            "<b>Как настроить:</b>\n"
            "1. 📍 Выберите город\n"
            "2. ⏰ Укажите время (UTC)\n"
            "3. 🔔 Включите уведомления\n\n"
            "<i>Москва = UTC+3 (выберите время на 3 часа раньше)</i>"
        )
    else:
        city = notif_data.get("city", "Не выбран")
        utc_time = notif_data.get("utc_time", "Не установлено")
        enabled = notif_data.get("enabled", False)
        status = "✅ ВКЛЮЧЕНЫ" if enabled else "❌ ВЫКЛЮЧЕНЫ"
        
        text = (
            f"⏰ <b>Настройки уведомлений</b>\n\n"
            f"📍 <b>Город:</b> {city}\n"
            f"🕐 <b>Время UTC:</b> {utc_time}\n"
            f"🔔 <b>Статус:</b> {status}\n\n"
            f"<i>Для изменения нажмите соответствующую кнопку</i>"
        )
    
    await query.edit_message_text(
        text,
        reply_markup=get_notification_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )

async def get_weather_for_user(query, user_id: int, city: str):
    """🌤️ Получить погоду для пользователя"""
    await query.edit_message_text(
        f"⏳ <b>Загружаю погоду для {city}...</b>",
        parse_mode=ParseMode.HTML
    )
    
    forecast = await get_weather_async(city)
    
    if forecast:
        formatted = format_weather_daily(forecast)
        
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"city_{city}")],
            [InlineKeyboardButton("📍 Сменить город", callback_data="select_city")],
            [InlineKeyboardButton("⏰ Настроить уведомления", callback_data="notifications")]
        ]
        
        await query.edit_message_text(
            formatted,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    else:
        keyboard = [
            [InlineKeyboardButton("📍 Выбрать другой город", callback_data="select_city")],
            [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
        ]
        
        await query.edit_message_text(
            f"❌ <b>Не удалось получить погоду для {city}</b>\n\n"
            f"<i>Попробуйте:</i>\n"
            f"• Проверить написание\n"
            f"• Выбрать город из списка\n"
            f"• Подождать и попробовать позже",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """✏️ Обработчик текстовых сообщений"""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if not text or text.startswith('/'):
        return
    
    message = await update.message.reply_text(
        f"⏳ <b>Ищу погоду для {text}...</b>",
        parse_mode=ParseMode.HTML
    )
    
    forecast = await get_weather_async(text)
    
    if forecast:
        city_name = forecast.get("city", text)
        set_user_city(user_id, city_name)
        formatted = format_weather_daily(forecast)
        
        keyboard = [
            [InlineKeyboardButton("📍 Сменить город", callback_data="select_city")],
            [InlineKeyboardButton("⏰ Настроить уведомления", callback_data="notifications")],
            [InlineKeyboardButton("🌤️ Главное меню", callback_data="back_main")]
        ]
        
        await message.edit_text(
            formatted,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    else:
        keyboard = [
            [InlineKeyboardButton("📍 Выбрать из списка", callback_data="select_city")],
            [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
        ]
        
        await message.edit_text(
            f"❌ <b>Не удалось найти погоду для '{text}'</b>\n\n"
            f"<i>Попробуйте выбрать город из списка:</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """❌ Обработчик ошибок"""
    logger.error(f"❌ Ошибка: {context.error}", exc_info=True)

# ============= СИСТЕМА УВЕДОМЛЕНИЙ =============
async def check_and_send_notifications(app):
    """🔔 Проверка и отправка уведомлений"""
    current_utc = datetime.utcnow().strftime("%H:%M")
    
    for user_id, notif_data in list(notifications.items()):
        try:
            if not notif_data.get("enabled", False):
                continue
            
            utc_time = notif_data.get("utc_time")
            if not utc_time:
                continue
            
            # Проверяем, не отправляли ли уже сегодня
            last_sent = last_notification.get(user_id)
            if last_sent == datetime.utcnow().date():
                continue
            
            # Проверяем время
            if utc_time == current_utc:
                city = notif_data.get("city", get_user_city(user_id))
                if not city or city == "Не выбран":
                    continue
                
                forecast = await get_weather_async(city)
                if forecast:
                    formatted = format_weather_daily(forecast)
                    
                    # Добавляем приветствие
                    hour = int(utc_time.split(":")[0])
                    if hour < 12:
                        greeting = "🌅 Доброе утро!"
                    elif hour < 18:
                        greeting = "🌇 Добрый день!"
                    else:
                        greeting = "🌃 Добрый вечер!"
                    
                    message_text = f"{greeting}\n\n{formatted}"
                    
                    await app.bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML
                    )
                    
                    logger.info(f"✅ Отправлено уведомление пользователю {user_id}")
                    last_notification[user_id] = datetime.utcnow().date()
                    
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления: {e}")

def notification_worker(app):
    """👷‍♂️ Рабочий поток для уведомлений"""
    async def worker_loop():
        while True:
            try:
                await check_and_send_notifications(app)
                await asyncio.sleep(60)  # Проверяем каждую минуту
            except Exception as e:
                logger.error(f"❌ Ошибка в worker_loop: {e}")
                await asyncio.sleep(60)
    
    def run_worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(worker_loop())
    
    worker_thread = threading.Thread(target=run_worker, daemon=True)
    worker_thread.start()
    logger.info("✅ Служба уведомлений запущена")

# ============= ПРОБУЖДЕНИЕ RENDER =============
async def wakeup_render_task():
    """🔄 Пробуждение Render.com"""
    if Config.RENDER_WAKEUP_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.RENDER_WAKEUP_URL, timeout=10):
                    logger.info("🔄 Render пробужден")
        except Exception as e:
            logger.error(f"❌ Ошибка пробуждения Render: {e}")

def render_wakeup_worker():
    """⏰ Рабочий поток для пробуждения Render"""
    async def wakeup_loop():
        while True:
            await wakeup_render_task()
            await asyncio.sleep(600)  # 10 минут
    
    def run_wakeup():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(wakeup_loop())
    
    wakeup_thread = threading.Thread(target=run_wakeup, daemon=True)
    wakeup_thread.start()
    logger.info("✅ Служба пробуждения Render запущена")

# ============= ОСНОВНАЯ ФУНКЦИЯ =============
def main():
    """🚀 Запуск бота"""
    if not Config.BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        logger.info("📝 Установите переменную окружения BOT_TOKEN на Render.com")
        return
    
    # Даем время предыдущему экземпляру завершиться
    logger.info("⏳ Ожидание завершения предыдущего экземпляра...")
    time.sleep(5)
    
    logger.info("🤖 Бот запускается...")
    logger.info(f"🏙️ Загружено {len(Config.POPULAR_CITIES)} городов")
    
    app = Application.builder().token(Config.BOT_TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    
    # Запускаем службы
    notification_worker(app)
    
    if Config.RENDER_WAKEUP_URL:
        render_wakeup_worker()
    
    logger.info("✅ Бот запущен и ожидает сообщений...")
    logger.info("✨ Готов к работе!")
    
    # Запускаем polling
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        close_loop=False
    )

if __name__ == "__main__":
    main()
