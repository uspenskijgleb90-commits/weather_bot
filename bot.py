#!/usr/bin/env python3
"""
Телеграм-бот "Погода" с улучшенным поиском городов
"""

import os
import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
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
    
    # Координаты популярных городов (заранее известные)
    CITY_COORDINATES = {
        "Москва": (55.7558, 37.6173),
        "Санкт-Петербург": (59.9343, 30.3351),
        "Новосибирск": (55.0084, 82.9357),
        "Екатеринбург": (56.8389, 60.6057),
        "Казань": (55.7961, 49.1064),
        "Нижний Новгород": (56.3269, 44.0065),
        "Челябинск": (55.1644, 61.4368),
        "Самара": (53.1959, 50.1002),
        "Омск": (54.9893, 73.3686),
        "Ростов-на-Дону": (47.2357, 39.7015),
        "Уфа": (54.7355, 55.9587),
        "Красноярск": (56.0153, 92.8932),
        "Пермь": (58.0105, 56.2502),
        "Воронеж": (51.6720, 39.1843),
        "Волгоград": (48.7080, 44.5133),
        "Йошкар-Ола": (56.6344, 47.8999),
        "Минск": (53.9006, 27.5590),
        "Киев": (50.4501, 30.5234),
        "Астана": (51.1694, 71.4491),
        "Бишкек": (42.8746, 74.5698),
        "Ташкент": (41.2995, 69.2401),
        "Алматы": (43.2220, 76.8512),
        "Баку": (40.4093, 49.8671),
        "Тбилиси": (41.7151, 44.8271),
        "Ереван": (40.1792, 44.4991),
        "Душанбе": (38.5598, 68.7870),
        "Ашхабад": (37.9601, 58.3261),
        "Вильнюс": (54.6872, 25.2797),
        "Рига": (56.9496, 24.1052),
        "Таллин": (59.4370, 24.1056),
        "Кишинев": (47.0105, 28.8638),
        "Харьков": (49.9935, 36.2304),
        "Одесса": (46.4825, 30.7233),
        "Львов": (49.8397, 24.0297),
        "Днепр": (48.4647, 35.0462),
        "Запорожье": (47.8388, 35.1396),
        "Брест": (52.0976, 23.7341),
        "Гомель": (52.4412, 30.9878),
        "Витебск": (55.1848, 30.2016),
        "Махачкала": (42.9849, 47.5047),
        "Симферополь": (44.9521, 34.1024),
        "Севастополь": (44.6167, 33.5254)
    }
    
    CITY_ALIASES = {
        "йошкар дыра": "Йошкар-Ола",
        "йошкардыра": "Йошкар-Ола",
        "йошкар": "Йошкар-Оla",
        "спб": "Санкт-Петербург",
        "питер": "Санкт-Петербург",
        "нск": "Новосибирск",
        "екб": "Екатеринбург",
        "нн": "Нижний Новгород",
        "челяба": "Челябинск",
        "казань": "Казань",
        "ростов": "Ростов-на-Дону",
        "краснодар": "Краснодар"
    }
    
    TIME_SLOTS = ["07:00", "08:00", "09:00", "10:00", "18:00", "19:00", "20:00"]

# ============= ЛОГГИРОВАНИЕ =============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ =============
user_data = defaultdict(dict)
weather_cache = {}
notifications = defaultdict(dict)
last_notification_check = {}
city_coordinates_cache = {}

# ============= ПОМОЩНИКИ =============
def normalize_city(city: str) -> str:
    """Нормализация названия города"""
    city_lower = city.lower().strip()
    
    # Проверяем псевдонимы
    if city_lower in Config.CITY_ALIASES:
        return Config.CITY_ALIASES[city_lower]
    
    # Ищем в известных городах (регистронезависимо)
    for known_city in Config.CITY_COORDINATES.keys():
        if city_lower == known_city.lower():
            return known_city
    
    # Если город не найден, возвращаем с заглавной буквой
    return city.strip().title()

def get_city_coordinates(city: str) -> Optional[Tuple[float, float]]:
    """Получение координат города"""
    normalized_city = normalize_city(city)
    
    # Проверяем кэш
    if normalized_city in city_coordinates_cache:
        return city_coordinates_cache[normalized_city]
    
    # Проверяем предустановленные координаты
    if normalized_city in Config.CITY_COORDINATES:
        coords = Config.CITY_COORDINATES[normalized_city]
        city_coordinates_cache[normalized_city] = coords
        return coords
    
    # Если город не найден, возвращаем None
    return None

async def find_city_coordinates(city: str) -> Optional[Tuple[float, float, str]]:
    """Поиск координат города через API"""
    try:
        async with aiohttp.ClientSession() as session:
            # Пробуем несколько API
            
            # 1. Open-Meteo
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=ru"
            async with session.get(geo_url, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("results"):
                        result = data["results"][0]
                        lat = result["latitude"]
                        lon = result["longitude"]
                        city_name = result.get("name", city)
                        return lat, lon, city_name
            
            # 2. Nominatim (OpenStreetMap)
            try:
                nominatim_url = f"https://nominatim.openstreetmap.org/search?q={city}&format=json&limit=1"
                headers = {'User-Agent': 'WeatherBot/1.0'}
                async with session.get(nominatim_url, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data:
                            result = data[0]
                            lat = float(result["lat"])
                            lon = float(result["lon"])
                            city_name = result.get("display_name", city).split(",")[0]
                            return lat, lon, city_name
            except:
                pass
                
    except Exception as e:
        logger.error(f"Ошибка поиска координат: {e}")
    
    return None

# ============= СЕРВИС ПОГОДЫ =============
async def get_weather_async(city: str) -> Optional[Dict]:
    """Получение прогноза погоды на сегодня"""
    try:
        # Сначала пробуем получить координаты из предустановленных
        coords = get_city_coordinates(city)
        
        if not coords:
            # Пробуем найти через API
            result = await find_city_coordinates(city)
            if result:
                lat, lon, city_name = result
            else:
                logger.error(f"Не удалось найти координаты для города: {city}")
                return None
        else:
            lat, lon = coords
            city_name = normalize_city(city)
        
        async with aiohttp.ClientSession() as session:
            # Погода на сегодня
            weather_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": ["temperature_2m_max", "temperature_2m_min", 
                         "precipitation_sum", "wind_speed_10m_max",
                         "weather_code", "sunrise", "sunset"],
                "hourly": ["temperature_2m", "precipitation", "weather_code"],
                "timezone": "auto",
                "forecast_days": 1
            }
            
            async with session.get(weather_url, params=params, timeout=10) as weather_response:
                if weather_response.status == 200:
                    weather_data = await weather_response.json()
                    return {
                        "city": city_name,
                        "daily": weather_data.get("daily", {}),
                        "hourly": weather_data.get("hourly", {}),
                        "latitude": lat,
                        "longitude": lon
                    }
                else:
                    logger.error(f"API погоды вернул статус {weather_response.status}")
                    
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при получении погоды для {city}")
    except Exception as e:
        logger.error(f"Ошибка получения погоды для {city}: {e}")
    
    return None

def get_weather_emoji(weather_code: int) -> str:
    """Получение эмодзи по коду погоды"""
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
        return "🌧️"  # Морось
    elif weather_code in [61, 63, 65]:
        return "🌧️"  # Дождь
    elif weather_code in [71, 73, 75]:
        return "❄️"  # Снег
    elif weather_code in [95, 96, 99]:
        return "⛈️"  # Гроза
    elif weather_code in [80, 81, 82]:
        return "🌧️"  # Ливень
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
    sunrise = daily.get("sunrise", [])
    sunset = daily.get("sunset", [])
    
    if not dates:
        return "❌ Нет данных о погоде"
    
    try:
        weather_code = weather_codes[0] if weather_codes else 0
        weather_emoji = get_weather_emoji(weather_code)
        
        # Форматируем время восхода и захода солнца
        sunrise_time = ""
        sunset_time = ""
        if sunrise and sunset:
            try:
                sunrise_dt = datetime.fromisoformat(sunrise[0].replace('Z', '+00:00'))
                sunset_dt = datetime.fromisoformat(sunset[0].replace('Z', '+00:00'))
                sunrise_time = sunrise_dt.strftime("%H:%M")
                sunset_time = sunset_dt.strftime("%H:%M")
            except:
                pass
        
        lines = [f"<b>{weather_emoji} Погода в {city} на сегодня</b>\n"]
        lines.append("─" * 30)
        
        if temps_max and temps_min:
            temp_avg = (temps_max[0] + temps_min[0]) / 2
            temp_feeling = ""
            if temp_avg > 25:
                temp_feeling = " (жара)"
            elif temp_avg > 20:
                temp_feeling = " (тепло)"
            elif temp_avg > 10:
                temp_feeling = " (прохладно)"
            elif temp_avg > 0:
                temp_feeling = " (холодно)"
            else:
                temp_feeling = " (мороз)"
            
            lines.append(f"🌡️ <b>Температура:</b> {temps_min[0]:.0f}°C ... {temps_max[0]:.0f}°C{temp_feeling}")
        
        if precip:
            if precip[0] > 0:
                rain_intensity = ""
                if precip[0] < 2.5:
                    rain_intensity = " (небольшие)"
                elif precip[0] < 7.5:
                    rain_intensity = " (умеренные)"
                else:
                    rain_intensity = " (сильные)"
                lines.append(f"💧 <b>Осадки:</b> {precip[0]:.1f} мм{rain_intensity}")
            else:
                lines.append(f"💧 <b>Осадки:</b> нет")
        
        if wind:
            wind_strength = ""
            if wind[0] < 5:
                wind_strength = " (слабый)"
            elif wind[0] < 10:
                wind_strength = " (умеренный)"
            elif wind[0] < 15:
                wind_strength = " (сильный)"
            else:
                wind_strength = " (очень сильный)"
            lines.append(f"💨 <b>Ветер:</b> {wind[0]:.1f} м/с{wind_strength}")
        
        # Описание погоды
        descriptions = {
            0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность",
            3: "Пасмурно", 45: "Туман", 48: "Туман с инеем",
            51: "Легкая морось", 53: "Умеренная морось", 55: "Сильная морось",
            61: "Небольшой дождь", 63: "Умеренный дождь", 65: "Сильный дождь",
            71: "Небольшой снег", 73: "Умеренный снег", 75: "Сильный снег",
            80: "Кратковременный дождь", 81: "Умеренный ливень", 82: "Сильный ливень",
            95: "Гроза", 96: "Гроза с градом", 99: "Сильная гроза с градом"
        }
        
        desc = descriptions.get(weather_code, "Неизвестно")
        lines.append(f"📝 <b>Описание:</b> {desc}")
        
        # Время восхода и захода солнца
        if sunrise_time and sunset_time:
            lines.append(f"🌅 <b>Восход:</b> {sunrise_time}")
            lines.append(f"🌇 <b>Закат:</b> {sunset_time}")
        
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
        [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_city_selection_keyboard() -> InlineKeyboardMarkup:
    """Выбор города"""
    keyboard = []
    
    # Получаем список известных городов
    known_cities = list(Config.CITY_COORDINATES.keys())
    
    # Добавляем города по 3 в ряд
    for i in range(0, min(15, len(known_cities)), 3):
        row = []
        for city in known_cities[i:i+3]:
            row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton("✏️ Ввести город", callback_data="input_city"),
        InlineKeyboardButton("↩️ Назад", callback_data="back_main")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_city_list_keyboard() -> InlineKeyboardMarkup:
    """Список городов"""
    keyboard = []
    
    known_cities = list(Config.CITY_COORDINATES.keys())
    cities_per_page = 20
    
    for i in range(0, len(known_cities), cities_per_page):
        page_cities = known_cities[i:i + cities_per_page]
        for city in page_cities:
            keyboard.append([InlineKeyboardButton(city, callback_data=f"city_{city}")])
    
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="back_main")])
    
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
            "city": "Москва"
        }
    
    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"🌤️ <b>Погодный бот</b> с ежедневными оповещениями\n\n"
        f"<b>✅ Поддерживается {len(Config.CITY_COORDINATES)} городов СНГ</b>\n\n"
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
            "📍 <b>Выберите город:</b>\n\n<i>Самые популярные города:</i>",
            reply_markup=get_city_selection_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    elif action == "city_list":
        await query.edit_message_text(
            f"📋 <b>Список городов</b>\n\n"
            f"<i>Доступно {len(Config.CITY_COORDINATES)} городов:</i>",
            reply_markup=get_city_list_keyboard(),
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
            "✏️ <b>Введите название города:</b>\n\n"
            "<i>Поддерживаются русские и английские названия\n"
            "Пример: Москва, Йошкар-Ола, New York</i>",
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
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"city_{city}")],
            [InlineKeyboardButton("📍 Сменить город", callback_data="select_city")],
            [InlineKeyboardButton("⏰ Настроить оповещения", callback_data="notifications")]
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
            f"<i>Возможные причины:</i>\n"
            f"• Город не найден\n"
            f"• Проблемы с интернет-соединением\n"
            f"• Сервис погоды временно недоступен\n\n"
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
        user_data[user_id]["city"] = city_name
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
        keyboard = [
            [InlineKeyboardButton("📍 Выбрать из списка", callback_data="select_city")],
            [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
        ]
        await message.edit_text(
            f"❌ <b>Не удалось найти погоду для '{text}'</b>\n\n"
            f"<i>Попробуйте:</i>\n"
            f"• Проверить написание города\n"
            f"• Использовать русское название\n"
            f"• Выбрать город из списка доступных\n\n"
            f"<b>✅ Доступно {len(Config.CITY_COORDINATES)} городов</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=True)

# ============= СИСТЕМА ОПОВЕЩЕНИЙ =============
async def check_and_send_notifications(app):
    """Проверка и отправка оповещений"""
    current_time = datetime.now().strftime("%H:%M")
    
    for user_id, notif_data in list(notifications.items()):
        try:
            if (notif_data.get("enabled") and 
                notif_data.get("time") == current_time and
                last_notification_check.get(user_id) != current_time):
                
                city = notif_data.get("city", user_data.get(user_id, {}).get("city", "Москва"))
                
                forecast = await get_weather_async(city)
                if forecast:
                    formatted = format_weather_daily(forecast)
                    
                    hour = int(current_time.split(":")[0])
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
                    
                    logger.info(f"✅ Отправлено оповещение пользователю {user_id}")
                    last_notification_check[user_id] = current_time
                    
        except Exception as e:
            logger.error(f"❌ Ошибка отправки оповещения пользователю {user_id}: {e}")

def notification_worker(app):
    """Рабочий поток для оповещений"""
    async def worker_loop():
        while True:
            try:
                await check_and_send_notifications(app)
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Ошибка в worker_loop: {e}")
                await asyncio.sleep(60)
    
    def run_worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(worker_loop())
    
    worker_thread = threading.Thread(target=run_worker, daemon=True)
    worker_thread.start()
    logger.info("✅ Служба оповещений запущена")

# ============= ПРОБУЖДЕНИЕ RENDER =============
async def wakeup_render_task():
    """Задача для пробуждения Render"""
    if Config.RENDER_WAKEUP_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.RENDER_WAKEUP_URL, timeout=10):
                    logger.info("🔄 Render пробужден")
        except Exception as e:
            logger.error(f"❌ Ошибка пробуждения Render: {e}")

def render_wakeup_worker():
    """Рабочий поток для пробуждения Render"""
    async def wakeup_loop():
        while True:
            await wakeup_render_task()
            await asyncio.sleep(600)
    
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
        logger.info("Установите переменную окружения BOT_TOKEN на Render.com")
        return
    
    logger.info("🤖 Бот запускается...")
    logger.info(f"✅ Загружено {len(Config.CITY_COORDINATES)} городов")
    
    app = Application.builder().token(Config.BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    
    notification_worker(app)
    
    if Config.RENDER_WAKEUP_URL:
        render_wakeup_worker()
    
    logger.info("✅ Бот запущен и ожидает сообщений...")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
