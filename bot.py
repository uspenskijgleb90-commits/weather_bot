#!/usr/bin/env python3
"""
Телеграм-бот "Погода" с уведомлениями по часовому поясу пользователя
Адаптирован для Render.com, Python 3.13.4
"""

import os
import asyncio
import aiohttp
import logging
import json
import pickle
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import threading
import time
from zoneinfo import ZoneInfo

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
    
    # Сохранение данных в файл (будет работать на Render с ephemeral storage)
    DATA_FILE = "user_data.pkl"
    
    # Города с предустановленными координатами и часовыми поясами
    CITY_DATA = {
        "Москва": {"lat": 55.7558, "lon": 37.6173, "tz": "Europe/Moscow"},
        "Санкт-Петербург": {"lat": 59.9343, "lon": 30.3351, "tz": "Europe/Moscow"},
        "Новосибирск": {"lat": 55.0084, "lon": 82.9357, "tz": "Asia/Novosibirsk"},
        "Екатеринбург": {"lat": 56.8389, "lon": 60.6057, "tz": "Asia/Yekaterinburg"},
        "Казань": {"lat": 55.7961, "lon": 49.1064, "tz": "Europe/Moscow"},
        "Нижний Новгород": {"lat": 56.3269, "lon": 44.0065, "tz": "Europe/Moscow"},
        "Челябинск": {"lat": 55.1644, "lon": 61.4368, "tz": "Asia/Yekaterinburg"},
        "Самара": {"lat": 53.1959, "lon": 50.1002, "tz": "Europe/Samara"},
        "Омск": {"lat": 54.9893, "lon": 73.3686, "tz": "Asia/Omsk"},
        "Ростов-на-Дону": {"lat": 47.2357, "lon": 39.7015, "tz": "Europe/Moscow"},
        "Уфа": {"lat": 54.7355, "lon": 55.9587, "tz": "Asia/Yekaterinburg"},
        "Красноярск": {"lat": 56.0153, "lon": 92.8932, "tz": "Asia/Krasnoyarsk"},
        "Пермь": {"lat": 58.0105, "lon": 56.2502, "tz": "Asia/Yekaterinburg"},
        "Воронеж": {"lat": 51.6720, "lon": 39.1843, "tz": "Europe/Moscow"},
        "Волгоград": {"lat": 48.7080, "lon": 44.5133, "tz": "Europe/Volgograd"},
        "Йошкар-Ола": {"lat": 56.6344, "lon": 47.8999, "tz": "Europe/Moscow"},
        "Минск": {"lat": 53.9006, "lon": 27.5590, "tz": "Europe/Minsk"},
        "Киев": {"lat": 50.4501, "lon": 30.5234, "tz": "Europe/Kiev"},
        "Астана": {"lat": 51.1694, "lon": 71.4491, "tz": "Asia/Almaty"},
        "Бишкек": {"lat": 42.8746, "lon": 74.5698, "tz": "Asia/Bishkek"},
        "Ташкент": {"lat": 41.2995, "lon": 69.2401, "tz": "Asia/Tashkent"},
        "Алматы": {"lat": 43.2220, "lon": 76.8512, "tz": "Asia/Almaty"},
        "Баку": {"lat": 40.4093, "lon": 49.8671, "tz": "Asia/Baku"},
        "Тбилиси": {"lat": 41.7151, "lon": 44.8271, "tz": "Asia/Tbilisi"},
        "Ереван": {"lat": 40.1792, "lon": 44.4991, "tz": "Asia/Yerevan"}
    }
    
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
    
    # Временные слоты для уведомлений
    TIME_SLOTS = ["07:00", "08:00", "09:00", "10:00", "18:00", "19:00", "20:00", "21:00"]

# ============= ЛОГГИРОВАНИЕ =============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= СИСТЕМА ХРАНЕНИЯ ДАННЫХ =============
class DataStorage:
    """Класс для сохранения и загрузки данных"""
    
    def __init__(self):
        self.data_file = Config.DATA_FILE
        self.data = self.load_data()
    
    def load_data(self) -> Dict:
        """Загрузка данных из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'rb') as f:
                    return pickle.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
        return {
            "users": {},      # Основные данные пользователей
            "notifications": {},  # Настройки уведомлений
            "cache": {}       # Кэш погоды
        }
    
    def save_data(self):
        """Сохранение данных в файл"""
        try:
            with open(self.data_file, 'wb') as f:
                pickle.dump(self.data, f)
            logger.debug("✅ Данные сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
    
    def get_user(self, user_id: int) -> Dict:
        """Получение данных пользователя"""
        return self.data["users"].get(user_id, {})
    
    def save_user(self, user_id: int, user_data: Dict):
        """Сохранение данных пользователя"""
        self.data["users"][user_id] = user_data
        self.save_data()
    
    def get_notification(self, user_id: int) -> Dict:
        """Получение настроек уведомлений"""
        return self.data["notifications"].get(user_id, {})
    
    def save_notification(self, user_id: int, notification_data: Dict):
        """Сохранение настроек уведомлений"""
        self.data["notifications"][user_id] = notification_data
        self.save_data()
    
    def delete_notification(self, user_id: int):
        """Удаление настроек уведомлений"""
        if user_id in self.data["notifications"]:
            del self.data["notifications"][user_id]
            self.save_data()
    
    def get_cache(self, key: str) -> Optional[Any]:
        """Получение данных из кэша"""
        cache_entry = self.data["cache"].get(key)
        if cache_entry:
            timestamp, data = cache_entry
            if time.time() - timestamp < 1800:  # 30 минут
                return data
        return None
    
    def save_cache(self, key: str, data: Any):
        """Сохранение данных в кэш"""
        self.data["cache"][key] = (time.time(), data)
        self.save_data()

# ============= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =============
storage = DataStorage()

# ============= ПОМОЩНИКИ =============
def normalize_city(city: str) -> str:
    """Нормализация названия города"""
    city_lower = city.lower().strip()
    if city_lower in Config.CITY_ALIASES:
        return Config.CITY_ALIASES[city_lower]
    
    for known_city in Config.CITY_DATA.keys():
        if city_lower == known_city.lower():
            return known_city
    
    return city.strip().title()

def get_city_info(city: str) -> Optional[Dict]:
    """Получение информации о городе"""
    normalized_city = normalize_city(city)
    return Config.CITY_DATA.get(normalized_city)

async def find_city_info(city: str) -> Optional[Dict]:
    """Поиск информации о городе через API"""
    cache_key = f"city_info_{city}"
    cached = storage.get_cache(cache_key)
    if cached:
        return cached
    
    try:
        async with aiohttp.ClientSession() as session:
            # Используем Open-Meteo для геокодирования
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
            async with session.get(geo_url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("results"):
                        result = data["results"][0]
                        city_name = result.get("name", city)
                        
                        # Определяем часовой пояс
                        timezone_name = result.get("timezone", "UTC")
                        
                        city_info = {
                            "name": city_name,
                            "lat": result["latitude"],
                            "lon": result["longitude"],
                            "tz": timezone_name
                        }
                        
                        storage.save_cache(cache_key, city_info)
                        return city_info
    except Exception as e:
        logger.error(f"Ошибка поиска города: {e}")
    
    return None

def convert_local_to_utc(local_time_str: str, timezone_str: str) -> Optional[str]:
    """Конвертация местного времени в UTC"""
    try:
        # Парсим время
        local_time = datetime.strptime(local_time_str, "%H:%M").time()
        
        # Получаем текущую дату с указанным временем и часовым поясом
        now = datetime.now(ZoneInfo(timezone_str))
        local_datetime = now.replace(
            hour=local_time.hour,
            minute=local_time.minute,
            second=0,
            microsecond=0
        )
        
        # Конвертируем в UTC
        utc_datetime = local_datetime.astimezone(timezone.utc)
        
        # Возвращаем время в формате HH:MM
        return utc_datetime.strftime("%H:%M")
    
    except Exception as e:
        logger.error(f"Ошибка конвертации времени: {e}")
        return None

def get_next_notification_time(local_time_str: str, timezone_str: str) -> Optional[datetime]:
    """Получение следующего времени для уведомления в UTC"""
    try:
        # Парсим время
        local_time = datetime.strptime(local_time_str, "%H:%M").time()
        
        # Текущее время в указанном часовом поясе
        user_tz = ZoneInfo(timezone_str)
        now_user = datetime.now(user_tz)
        
        # Время уведомления на сегодня
        notification_today = now_user.replace(
            hour=local_time.hour,
            minute=local_time.minute,
            second=0,
            microsecond=0
        )
        
        # Если время уже прошло сегодня, планируем на завтра
        if notification_today < now_user:
            notification_today += timedelta(days=1)
        
        # Конвертируем в UTC
        notification_utc = notification_today.astimezone(timezone.utc)
        
        return notification_utc
    
    except Exception as e:
        logger.error(f"Ошибка расчета времени уведомления: {e}")
        return None

# ============= СЕРВИС ПОГОДЫ =============
async def get_weather_async(city: str) -> Optional[Dict]:
    """Получение прогноза погоды"""
    cache_key = f"weather_{city}"
    cached = storage.get_cache(cache_key)
    if cached:
        return cached
    
    city_info = get_city_info(city)
    if not city_info:
        city_info_data = await find_city_info(city)
        if not city_info_data:
            return None
        city_info = city_info_data
    
    try:
        async with aiohttp.ClientSession() as session:
            weather_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": city_info["lat"],
                "longitude": city_info["lon"],
                "daily": ["temperature_2m_max", "temperature_2m_min", 
                         "precipitation_sum", "wind_speed_10m_max",
                         "weather_code"],
                "timezone": city_info.get("tz", "auto"),
                "forecast_days": 1
            }
            
            async with session.get(weather_url, params=params, timeout=10) as response:
                if response.status == 200:
                    weather_data = await response.json()
                    
                    forecast = {
                        "city": city_info.get("name", city),
                        "timezone": city_info.get("tz", "UTC"),
                        "daily": weather_data.get("daily", {})
                    }
                    
                    storage.save_cache(cache_key, forecast)
                    return forecast
    
    except Exception as e:
        logger.error(f"Ошибка получения погоды: {e}")
    
    return None

def get_weather_emoji(weather_code: int) -> str:
    """Получение эмодзи по коду погоды"""
    if weather_code == 0:
        return "☀️"
    elif weather_code == 1:
        return "🌤️"
    elif weather_code == 2:
        return "⛅"
    elif weather_code == 3:
        return "☁️"
    elif weather_code in [45, 48]:
        return "🌫️"
    elif weather_code in [51, 53, 55]:
        return "🌧️"
    elif weather_code in [61, 63, 65]:
        return "🌧️"
    elif weather_code in [71, 73, 75]:
        return "❄️"
    elif weather_code in [95, 96, 99]:
        return "⛈️"
    else:
        return "🌤️"

def format_weather_daily(forecast: Dict) -> str:
    """Форматирование погоды на день"""
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
        
        descriptions = {
            0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность",
            3: "Пасмурно", 45: "Туман", 48: "Туман с инеем",
            51: "Легкая морось", 53: "Умеренная морось", 55: "Сильная морось",
            61: "Небольшой дождь", 63: "Умеренный дождь", 65: "Сильный дождь",
            71: "Небольшой снег", 73: "Умеренный снег", 75: "Сильный снег",
            95: "Гроза", 96: "Гроза с градом", 99: "Сильная гроза с градом"
        }
        
        desc = descriptions.get(weather_code, "Неизвестно")
        lines.append(f"📝 <b>Описание:</b> {desc}")
        
        lines.append(f"\n🕐 <i>Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
        
        return "\n".join(lines)
        
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка форматирования: {e}")
        return "❌ Ошибка обработки данных о погоде"

# ============= КЛАВИАТУРЫ =============
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🌤️ Погода сейчас", callback_data="weather_now")],
        [InlineKeyboardButton("📍 Выбрать город", callback_data="select_city")],
        [InlineKeyboardButton("⏰ Уведомления", callback_data="notifications")],
        [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_city_selection_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    cities = list(Config.CITY_DATA.keys())
    
    for i in range(0, min(15, len(cities)), 3):
        row = []
        for city in cities[i:i+3]:
            row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton("✏️ Ввести город", callback_data="input_city"),
        InlineKeyboardButton("↩️ Назад", callback_data="back_main")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_notification_keyboard(user_id: int) -> InlineKeyboardMarkup:
    notif_data = storage.get_notification(user_id)
    
    city = notif_data.get("city", "Не выбран")
    local_time = notif_data.get("local_time", "Не установлено")
    enabled = notif_data.get("enabled", False)
    next_time = notif_data.get("next_utc_time", "Не рассчитано")
    
    status = "✅ ВКЛ" if enabled else "❌ ВЫКЛ"
    
    keyboard = [
        [InlineKeyboardButton(f"📍 Город: {city}", callback_data="notif_city")],
        [InlineKeyboardButton(f"⏰ Время: {local_time}", callback_data="notif_time")],
        [InlineKeyboardButton(f"🔔 Статус: {status}", callback_data="notif_toggle")],
        [InlineKeyboardButton("🗑️ Удалить", callback_data="notif_delete")],
        [InlineKeyboardButton("↩️ Назад", callback_data="back_main")]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def get_time_selection_keyboard() -> InlineKeyboardMarkup:
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
    
    # Инициализация пользователя
    user_data = storage.get_user(user_id)
    if not user_data:
        user_data = {
            "id": user_id,
            "name": user.first_name,
            "username": user.username,
            "city": "Москва",
            "created_at": datetime.now().isoformat()
        }
        storage.save_user(user_id, user_data)
    
    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"🌤️ <b>Погодный бот с умными уведомлениями</b>\n\n"
        f"<b>✅ Поддерживается {len(Config.CITY_DATA)} городов</b>\n"
        f"<b>⏰ Уведомления по вашему местному времени</b>\n\n"
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
        await show_main_menu(query)
    
    elif action == "weather_now":
        user_data = storage.get_user(user_id)
        city = user_data.get("city", "Москва")
        await get_weather_for_user(query, user_id, city)
    
    elif action == "select_city":
        await query.edit_message_text(
            "📍 <b>Выберите город:</b>\n\n<i>Самые популярные города:</i>",
            reply_markup=get_city_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "city_list":
        cities = list(Config.CITY_DATA.keys())
        text = f"📋 <b>Список городов</b>\n\n<i>Доступно {len(cities)} городов:</i>\n\n"
        
        for i in range(0, len(cities), 5):
            text += " • " + " • ".join(cities[i:i+5]) + "\n"
        
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="back_main")]]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    elif action.startswith("city_"):
        city = action[5:]
        user_data = storage.get_user(user_id)
        user_data["city"] = city
        storage.save_user(user_id, user_data)
        
        await query.edit_message_text(
            f"✅ Город установлен: <b>{city}</b>\n\nЧто дальше?",
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "input_city":
        await query.edit_message_text(
            "✏️ <b>Введите название города:</b>\n\n"
            "<i>Поддерживаются русские названия\n"
            "Пример: Москва, Йошкар-Ола</i>",
            parse_mode=ParseMode.HTML
        )
    
    elif action == "notifications":
        await show_notifications_menu(query, user_id)
    
    elif action == "notif_city":
        await query.edit_message_text(
            "📍 <b>Выберите город для уведомлений:</b>",
            reply_markup=get_city_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "notif_time":
        await query.edit_message_text(
            "⏰ <b>Выберите время уведомления:</b>\n\n"
            "<i>Время указывается по местному времени выбранного города</i>",
            reply_markup=get_time_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action.startswith("time_"):
        local_time = action[5:]
        notif_data = storage.get_notification(user_id)
        
        if not notif_data or "city" not in notif_data:
            await query.answer("❌ Сначала выберите город!", show_alert=True)
            await show_notifications_menu(query, user_id)
            return
        
        city_info = get_city_info(notif_data["city"])
        if not city_info:
            city_info_data = await find_city_info(notif_data["city"])
            if not city_info_data:
                await query.answer("❌ Не удалось определить часовой пояс города", show_alert=True)
                return
            city_info = city_info_data
        
        # Конвертируем местное время в UTC
        timezone_str = city_info.get("tz", "UTC")
        utc_time = convert_local_to_utc(local_time, timezone_str)
        
        if not utc_time:
            await query.answer("❌ Ошибка конвертации времени", show_alert=True)
            return
        
        # Сохраняем настройки
        notif_data["local_time"] = local_time
        notif_data["timezone"] = timezone_str
        notif_data["utc_time"] = utc_time
        
        # Рассчитываем следующее время уведомления
        next_time = get_next_notification_time(local_time, timezone_str)
        if next_time:
            notif_data["next_utc_time"] = next_time.isoformat()
        
        storage.save_notification(user_id, notif_data)
        
        await query.edit_message_text(
            f"✅ Время уведомления установлено:\n\n"
            f"<b>Местное время:</b> {local_time}\n"
            f"<b>Часовой пояс:</b> {timezone_str}\n"
            f"<b>Время UTC:</b> {utc_time}\n\n"
            f"<i>Не забудьте включить уведомления!</i>",
            reply_markup=get_notification_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "notif_toggle":
        notif_data = storage.get_notification(user_id)
        
        if not notif_data:
            await query.answer("❌ Сначала настройте уведомления!", show_alert=True)
            return
        
        # Переключаем статус
        notif_data["enabled"] = not notif_data.get("enabled", False)
        storage.save_notification(user_id, notif_data)
        
        status = "включены" if notif_data["enabled"] else "выключены"
        await query.answer(f"🔔 Уведомления {status}")
        await show_notifications_menu(query, user_id)
    
    elif action == "notif_delete":
        storage.delete_notification(user_id)
        await query.answer("🗑️ Настройки уведомлений удалены")
        await show_main_menu(query)

async def show_main_menu(query):
    await query.edit_message_text(
        "🌤️ <b>Главное меню</b>\n\n<i>Выберите действие:</i>",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def show_notifications_menu(query, user_id):
    notif_data = storage.get_notification(user_id)
    
    if not notif_data:
        text = (
            "⏰ <b>Ежедневные уведомления</b>\n\n"
            "<i>Настройте получение ежедневного прогноза погоды:</i>\n\n"
            "1️⃣ Выберите город\n"
            "2️⃣ Укажите время (по местному времени города)\n"
            "3️⃣ Включите уведомления\n\n"
            "<b>✅ Уведомления приходят точно в указанное время!</b>"
        )
        keyboard = [
            [InlineKeyboardButton("📍 Выбрать город", callback_data="notif_city")],
            [InlineKeyboardButton("↩️ Назад", callback_data="back_main")]
        ]
    else:
        city = notif_data.get("city", "Не выбран")
        local_time = notif_data.get("local_time", "Не установлено")
        timezone = notif_data.get("timezone", "UTC")
        utc_time = notif_data.get("utc_time", "Не рассчитано")
        enabled = notif_data.get("enabled", False)
        
        status_text = "✅ ВКЛЮЧЕНЫ" if enabled else "❌ ВЫКЛЮЧЕНЫ"
        
        text = (
            f"⏰ <b>Настройки уведомлений</b>\n\n"
            f"<b>Город:</b> {city}\n"
            f"<b>Местное время:</b> {local_time}\n"
            f"<b>Часовой пояс:</b> {timezone}\n"
            f"<b>Время UTC:</b> {utc_time}\n"
            f"<b>Статус:</b> {status_text}\n\n"
            f"<i>Для изменения нажмите соответствующую кнопку</i>"
        )
    
    await query.edit_message_text(
        text,
        reply_markup=get_notification_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )

async def get_weather_for_user(query, user_id: int, city: str):
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
            [InlineKeyboardButton("⏰ Уведомления", callback_data="notifications")]
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
            f"<i>Попробуйте выбрать город из списка</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
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
        
        # Сохраняем город для пользователя
        user_data = storage.get_user(user_id)
        user_data["city"] = city_name
        storage.save_user(user_id, user_data)
        
        formatted = format_weather_daily(forecast)
        
        keyboard = [
            [InlineKeyboardButton("📍 Сменить город", callback_data="select_city")],
            [InlineKeyboardButton("⏰ Уведомления", callback_data="notifications")],
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
            f"<i>Попробуйте выбрать город из списка доступных</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)

# ============= СИСТЕМА УВЕДОМЛЕНИЙ =============
async def check_and_send_notifications(app):
    """Проверка и отправка уведомлений"""
    current_utc = datetime.now(timezone.utc)
    current_utc_str = current_utc.strftime("%H:%M")
    
    for user_id, notif_data in storage.data["notifications"].items():
        try:
            if not notif_data.get("enabled", False):
                continue
            
            utc_time = notif_data.get("utc_time")
            if not utc_time:
                continue
            
            # Проверяем, наступило ли время уведомления
            if utc_time == current_utc_str:
                city = notif_data.get("city")
                if not city:
                    continue
                
                forecast = await get_weather_async(city)
                if forecast:
                    formatted = format_weather_daily(forecast)
                    
                    # Добавляем приветствие
                    local_time = notif_data.get("local_time", "")
                    hour = int(local_time.split(":")[0]) if ":" in local_time else 12
                    
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
                    
                    # Обновляем следующее время уведомления
                    timezone_str = notif_data.get("timezone", "UTC")
                    local_time = notif_data.get("local_time", "08:00")
                    next_time = get_next_notification_time(local_time, timezone_str)
                    
                    if next_time:
                        notif_data["next_utc_time"] = next_time.isoformat()
                        storage.save_notification(user_id, notif_data)
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления: {e}")

def notification_worker(app):
    """Рабочий поток для уведомлений"""
    async def worker_loop():
        while True:
            try:
                await check_and_send_notifications(app)
                await asyncio.sleep(60)  # Проверяем каждую минуту
            except Exception as e:
                logger.error(f"Ошибка в worker_loop: {e}")
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
    if Config.RENDER_WAKEUP_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.RENDER_WAKEUP_URL, timeout=10):
                    logger.info("🔄 Render пробужден")
        except Exception as e:
            logger.error(f"❌ Ошибка пробуждения Render: {e}")

def render_wakeup_worker():
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
    """Запуск бота"""
    if not Config.BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        return
    
    # Даем время предыдущему экземпляру завершиться
    time.sleep(5)
    
    logger.info("🤖 Бот запускается...")
    logger.info(f"✅ Загружено {len(Config.CITY_DATA)} городов")
    logger.info(f"📊 Пользователей в базе: {len(storage.data['users'])}")
    logger.info(f"🔔 Активных уведомлений: {len([n for n in storage.data['notifications'].values() if n.get('enabled')])}")
    
    app = Application.builder().token(Config.BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    
    notification_worker(app)
    
    if Config.RENDER_WAKEUP_URL:
        render_wakeup_worker()
    
    logger.info("✅ Бот запущен и ожидает сообщений...")
    
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        close_loop=False
    )

if __name__ == "__main__":
    main()
