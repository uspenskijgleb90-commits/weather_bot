#!/usr/bin/env python3
"""
Телеграм-бот "Погода 7 дней"
Разработан для хостинга Render.com
Python 3.13.4
"""

import os
import json
import logging
import sqlite3
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import pytz
from dataclasses import dataclass

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from telegram.constants import ParseMode

# ============= КОНФИГУРАЦИЯ =============
class Config:
    """Конфигурация бота"""
    # Получите токен бота через переменную окружения
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    
    # Для Render.com - автоматическое обновление каждые 10 минут
    RENDER_AUTO_WAKEUP = True
    RENDER_WAKEUP_URL = os.getenv("RENDER_WAKEUP_URL", "")
    
    # Настройки кэширования (в секундах)
    CACHE_DURATION = 1800  # 30 минут
    
    # Настройки автоудаления (в секундах)
    AUTO_DELETE_DELAY = 35  # 35 секунд
    
    # Настройки БД
    DB_NAME = "weather_bot.db"
    
    # Бесплатное API погоды (без ключа)
    WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
    
    # Города для быстрого доступа
    POPULAR_CITIES = [
        "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
        "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
        "Уфа", "Красноярск", "Пермь", "Воронеж", "Волгоград",
        "Минск", "Киев", "Астана", "Бишкек", "Ташкент"
    ]
    
    # Псевдонимы городов
    CITY_ALIASES = {
        "йошкар дыра": "Йошкар-Ола",
        "йошкардыра": "Йошкар-Ола",
        "йошкар": "Йошкар-Ола",
        "спб": "Санкт-Петербург",
        "питер": "Санкт-Петербург",
        "нск": "Новосибирск",
        "екб": "Екатеринбург",
        "казань": "Казань",
        "нн": "Нижний Новгород",
        "челяба": "Челябинск"
    }

# ============= ЛОГГИРОВАНИЕ =============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============= БАЗА ДАННЫХ =============
class Database:
    """Класс для работы с базой данных SQLite"""
    
    def __init__(self, db_name: str = Config.DB_NAME):
        self.db_name = db_name
        self.init_db()
    
    def init_db(self):
        """Инициализация таблиц в базе данных"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            
            # Пользователи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    joined_date TIMESTAMP,
                    last_activity TIMESTAMP,
                    is_admin INTEGER DEFAULT 0
                )
            ''')
            
            # Запросы погоды
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weather_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    city TEXT,
                    timestamp TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Избранные города
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS favorite_cities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    city TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    UNIQUE(user_id, city)
                )
            ''')
            
            # Кэш погоды
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weather_cache (
                    city TEXT PRIMARY KEY,
                    data TEXT,
                    timestamp TIMESTAMP
                )
            ''')
            
            # Системные логи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT,
                    message TEXT,
                    timestamp TIMESTAMP
                )
            ''')
            
            # Создаем администратора (замените на свой ID)
            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username, is_admin, joined_date)
                VALUES (?, ?, 1, datetime('now'))
            ''', (os.getenv("ADMIN_ID", 0), "admin"))
            
            conn.commit()
    
    def add_user(self, user_id: int, username: str, first_name: str, last_name: str):
        """Добавление нового пользователя"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, username, first_name, last_name, joined_date, last_activity)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            ''', (user_id, username, first_name, last_name))
            conn.commit()
    
    def update_activity(self, user_id: int):
        """Обновление времени последней активности"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET last_activity = datetime('now')
                WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
    
    def add_weather_request(self, user_id: int, city: str):
        """Добавление запроса погоды"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO weather_requests (user_id, city, timestamp)
                VALUES (?, ?, datetime('now'))
            ''', (user_id, city))
            
            # Удаляем старые записи, оставляем последние 15
            cursor.execute('''
                DELETE FROM weather_requests 
                WHERE id NOT IN (
                    SELECT id FROM weather_requests 
                    WHERE user_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT 15
                ) AND user_id = ?
            ''', (user_id, user_id))
            
            conn.commit()
    
    def get_user_history(self, user_id: int, limit: int = 15) -> List[Tuple]:
        """Получение истории запросов пользователя"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT city, timestamp 
                FROM weather_requests 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (user_id, limit))
            return cursor.fetchall()
    
    def add_favorite_city(self, user_id: int, city: str):
        """Добавление города в избранное"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO favorite_cities (user_id, city)
                VALUES (?, ?)
            ''', (user_id, city))
            conn.commit()
    
    def remove_favorite_city(self, user_id: int, city: str):
        """Удаление города из избранного"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM favorite_cities 
                WHERE user_id = ? AND city = ?
            ''', (user_id, city))
            conn.commit()
    
    def get_favorite_cities(self, user_id: int) -> List[str]:
        """Получение избранных городов пользователя"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT city 
                FROM favorite_cities 
                WHERE user_id = ? 
                ORDER BY id DESC
            ''', (user_id,))
            return [row[0] for row in cursor.fetchall()]
    
    def cache_weather_data(self, city: str, data: str):
        """Кэширование данных о погоде"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO weather_cache (city, data, timestamp)
                VALUES (?, ?, datetime('now'))
            ''', (city, data))
            conn.commit()
    
    def get_cached_weather(self, city: str, max_age: int = Config.CACHE_DURATION) -> Optional[str]:
        """Получение кэшированных данных о погоде"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT data, timestamp 
                FROM weather_cache 
                WHERE city = ? AND 
                (strftime('%s', 'now') - strftime('%s', timestamp)) < ?
            ''', (city, max_age))
            result = cursor.fetchone()
            return result[0] if result else None
    
    def add_system_log(self, level: str, message: str):
        """Добавление системного лога"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO system_logs (level, message, timestamp)
                VALUES (?, ?, datetime('now'))
            ''', (level, message))
            conn.commit()
    
    def get_statistics(self) -> Dict:
        """Получение статистики"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            
            # Общее количество пользователей
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            
            # Активные пользователи (последние 7 дней)
            cursor.execute('''
                SELECT COUNT(DISTINCT user_id) 
                FROM weather_requests 
                WHERE date(timestamp) >= date('now', '-7 days')
            ''')
            active_users = cursor.fetchone()[0]
            
            # Всего запросов
            cursor.execute("SELECT COUNT(*) FROM weather_requests")
            total_requests = cursor.fetchone()[0]
            
            # Популярные города
            cursor.execute('''
                SELECT city, COUNT(*) as count 
                FROM weather_requests 
                GROUP BY city 
                ORDER BY count DESC 
                LIMIT 10
            ''')
            popular_cities = cursor.fetchall()
            
            return {
                "total_users": total_users,
                "active_users": active_users,
                "total_requests": total_requests,
                "popular_cities": popular_cities
            }
    
    def get_recent_logs(self, limit: int = 50) -> List[Tuple]:
        """Получение последних логов"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT level, message, timestamp 
                FROM system_logs 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()
    
    def is_admin(self, user_id: int) -> bool:
        """Проверка, является ли пользователь администратором"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT is_admin FROM users WHERE user_id = ?
            ''', (user_id,))
            result = cursor.fetchone()
            return result and result[0] == 1 if result else False
    
    def clear_cache(self):
        """Очистка кэша"""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM weather_cache")
            conn.commit()

# ============= СЕРВИС ПОГОДЫ =============
class WeatherService:
    """Сервис для получения прогноза погоды"""
    
    # Координаты для городов СНГ
    CITY_COORDINATES = {
        "Москва": {"lat": 55.7558, "lon": 37.6173},
        "Санкт-Петербург": {"lat": 59.9343, "lon": 30.3351},
        "Новосибирск": {"lat": 55.0084, "lon": 82.9357},
        "Екатеринбург": {"lat": 56.8389, "lon": 60.6057},
        "Казань": {"lat": 55.7961, "lon": 49.1064},
        "Нижний Новгород": {"lat": 56.3269, "lon": 44.0065},
        "Челябинск": {"lat": 55.1644, "lon": 61.4368},
        "Самара": {"lat": 53.1959, "lon": 50.1002},
        "Омск": {"lat": 54.9893, "lon": 73.3686},
        "Ростов-на-Дону": {"lat": 47.2357, "lon": 39.7015},
        "Уфа": {"lat": 54.7355, "lon": 55.9587},
        "Красноярск": {"lat": 56.0153, "lon": 92.8932},
        "Пермь": {"lat": 58.0105, "lon": 56.2502},
        "Воронеж": {"lat": 51.6720, "lon": 39.1843},
        "Волгоград": {"lat": 48.7080, "lon": 44.5133},
        "Йошкар-Ола": {"lat": 56.6344, "lon": 47.8999},
        "Минск": {"lat": 53.9006, "lon": 27.5590},
        "Киев": {"lat": 50.4501, "lon": 30.5234},
        "Астана": {"lat": 51.1694, "lon": 71.4491},
        "Бишкек": {"lat": 42.8746, "lon": 74.5698},
        "Ташкент": {"lat": 41.2995, "lon": 69.2401},
        "Алматы": {"lat": 43.2220, "lon": 76.8512},
        "Баку": {"lat": 40.4093, "lon": 49.8671},
        "Тбилиси": {"lat": 41.7151, "lon": 44.8271},
        "Ереван": {"lat": 40.1792, "lon": 44.4991},
        "Душанбе": {"lat": 38.5598, "lon": 68.7870},
        "Ашхабад": {"lat": 37.9601, "lon": 58.3261},
        "Вильнюс": {"lat": 54.6872, "lon": 25.2797},
        "Рига": {"lat": 56.9496, "lon": 24.1052},
        "Таллин": {"lat": 59.4370, "lon": 24.7536},
        "Кишинев": {"lat": 47.0105, "lon": 28.8638},
        "Астана": {"lat": 51.1694, "lon": 71.4491},
        "Брест": {"lat": 52.0976, "lon": 23.7341},
        "Гомель": {"lat": 52.4412, "lon": 30.9878},
        "Витебск": {"lat": 55.1848, "lon": 30.2016},
        "Харьков": {"lat": 49.9935, "lon": 36.2304},
        "Одесса": {"lat": 46.4825, "lon": 30.7233},
        "Львов": {"lat": 49.8397, "lon": 24.0297},
        "Днепр": {"lat": 48.4647, "lon": 35.0462},
        "Запорожье": {"lat": 47.8388, "lon": 35.1396},
    }
    
    @classmethod
    def normalize_city_name(cls, city: str) -> str:
        """Нормализация названия города"""
        city_lower = city.lower().strip()
        
        # Проверяем псевдонимы
        if city_lower in Config.CITY_ALIASES:
            return Config.CITY_ALIASES[city_lower]
        
        # Ищем в списке городов (регистронезависимо)
        for known_city in cls.CITY_COORDINATES.keys():
            if city_lower == known_city.lower():
                return known_city
        
        # Если город не найден, возвращаем оригинал с заглавной буквой
        return city.strip().title()
    
    @classmethod
    def get_coordinates(cls, city: str) -> Optional[Tuple[float, float]]:
        """Получение координат для города"""
        normalized_city = cls.normalize_city_name(city)
        
        if normalized_city in cls.CITY_COORDINATES:
            coords = cls.CITY_COORDINATES[normalized_city]
            return coords["lat"], coords["lon"]
        
        return None
    
    @staticmethod
    async def fetch_weather(lat: float, lon: float, city: str) -> Optional[Dict]:
        """Получение прогноза погоды с API"""
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "daily": ["temperature_2m_max", "temperature_2m_min", 
                             "precipitation_sum", "wind_speed_10m_max",
                             "relative_humidity_2m_max"],
                    "timezone": "auto",
                    "forecast_days": 7
                }
                
                async with session.get(Config.WEATHER_API_URL, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Форматируем данные для кэширования
                        forecast_data = {
                            "city": city,
                            "daily": data.get("daily", {}),
                            "timezone": data.get("timezone", "UTC")
                        }
                        
                        return forecast_data
                    else:
                        logger.error(f"API error: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Weather fetch error: {e}")
            return None
    
    @classmethod
    async def get_weather_forecast(cls, city: str, db: Database) -> Optional[Dict]:
        """Получение прогноза погоды (с кэшированием)"""
        normalized_city = cls.normalize_city_name(city)
        
        # Проверяем кэш
        cached = db.get_cached_weather(normalized_city)
        if cached:
            try:
                return json.loads(cached)
            except:
                pass
        
        # Получаем координаты
        coords = cls.get_coordinates(normalized_city)
        if not coords:
            return None
        
        lat, lon = coords
        
        # Получаем данные с API
        forecast = await cls.fetch_weather(lat, lon, normalized_city)
        if forecast:
            # Кэшируем данные
            db.cache_weather_data(normalized_city, json.dumps(forecast))
        
        return forecast

# ============= ФОРМАТИРОВАНИЕ =============
class WeatherFormatter:
    """Класс для форматирования прогноза погоды"""
    
    @staticmethod
    def get_weather_emoji(weather_code: Optional[int] = None, temp: Optional[float] = None) -> str:
        """Получение эмодзи для погоды"""
        if temp is not None:
            if temp > 30:
                return "🔥"
            elif temp > 20:
                return "☀️"
            elif temp > 10:
                return "⛅"
            elif temp > 0:
                return "🌤️"
            elif temp > -10:
                return "❄️"
            else:
                return "🥶"
        
        return "🌡️"
    
    @staticmethod
    def format_weather_forecast(forecast: Dict) -> str:
        """Форматирование прогноза погоды на 7 дней"""
        if not forecast or "daily" not in forecast:
            return "❌ Не удалось получить прогноз погоды"
        
        daily = forecast["daily"]
        city = forecast.get("city", "Неизвестный город")
        
        # Получаем даты
        dates = daily.get("time", [])[:7]
        temps_max = daily.get("temperature_2m_max", [])[:7]
        temps_min = daily.get("temperature_2m_min", [])[:7]
        precip = daily.get("precipitation_sum", [])[:7]
        wind = daily.get("wind_speed_10m_max", [])[:7]
        humidity = daily.get("relative_humidity_2m_max", [])[:7]
        
        if not dates:
            return "❌ Нет данных о погоде"
        
        # Заголовок
        lines = [f"<b>🌤️ Прогноз погоды для {city}</b>\n"]
        lines.append(f"<i>На 7 дней (обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')})</i>\n")
        lines.append("─" * 30)
        
        # Прогноз по дням
        for i in range(min(7, len(dates))):
            try:
                date_obj = datetime.strptime(dates[i], "%Y-%m-%d")
                day_name = date_obj.strftime("%a").upper()
                date_str = date_obj.strftime("%d.%m")
                
                # Эмодзи для дня
                if i == 0:
                    day_emoji = "📅"
                elif day_name == "СБ" or day_name == "ВС":
                    day_emoji = "🎉"
                else:
                    day_emoji = "📆"
                
                # Эмодзи для погоды
                temp_avg = (temps_max[i] + temps_min[i]) / 2
                weather_emoji = WeatherFormatter.get_weather_emoji(temp=temp_avg)
                
                # Форматируем строку
                line = (
                    f"{day_emoji} <b>{day_name} {date_str}:</b> {weather_emoji}\n"
                    f"   🌡️ <i>Температура:</i> <b>{temps_min[i]:.0f}°C ... {temps_max[i]:.0f}°C</b>\n"
                )
                
                if precip[i] > 0:
                    line += f"   💧 <i>Осадки:</i> <b>{precip[i]:.1f} мм</b>\n"
                
                line += (
                    f"   💨 <i>Ветер:</i> <b>{wind[i]:.1f} м/с</b>\n"
                    f"   💦 <i>Влажность:</i> <b>{humidity[i]:.0f}%</b>\n"
                )
                
                lines.append(line)
                
                if i < 6:
                    lines.append("─" * 30)
                    
            except (IndexError, ValueError) as e:
                logger.error(f"Error formatting day {i}: {e}")
                continue
        
        lines.append("\n<i>❓ Для нового запроса нажмите /start</i>")
        
        return "\n".join(lines)

# ============= КЛАВИАТУРЫ =============
class KeyboardManager:
    """Менеджер клавиатур"""
    
    @staticmethod
    def get_main_menu_keyboard() -> InlineKeyboardMarkup:
        """Главное меню"""
        keyboard = [
            [
                InlineKeyboardButton("🌤️ Погода в городе", callback_data="weather_city"),
                InlineKeyboardButton("📍 Мои города", callback_data="my_cities")
            ],
            [
                InlineKeyboardButton("📚 История запросов", callback_data="history"),
                InlineKeyboardButton("⭐ Избранное", callback_data="favorites")
            ],
            [
                InlineKeyboardButton("🎯 Популярные города", callback_data="popular"),
                InlineKeyboardButton("⚙️ Настройки", callback_data="settings")
            ],
            [
                InlineKeyboardButton("👑 Админ-панель", callback_data="admin_panel")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_city_selection_keyboard(user_id: int, db: Database) -> InlineKeyboardMarkup:
        """Клавиатура выбора города"""
        keyboard = []
        
        # Избранные города
        favorites = db.get_favorite_cities(user_id)
        if favorites:
            keyboard.append([InlineKeyboardButton("⭐ Избранные", callback_data="favorites_list")])
        
        # История
        history = db.get_user_history(user_id)
        if history:
            keyboard.append([InlineKeyboardButton("📚 История", callback_data="history_list")])
        
        # Популярные города (первые 6)
        for i in range(0, len(Config.POPULAR_CITIES), 3):
            row = []
            for city in Config.POPULAR_CITIES[i:i+3]:
                row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
            keyboard.append(row)
        
        # Дополнительные опции
        keyboard.append([
            InlineKeyboardButton("🔍 Другой город", callback_data="other_city"),
            InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_favorites_keyboard(user_id: int, db: Database) -> InlineKeyboardMarkup:
        """Клавиатура избранных городов"""
        favorites = db.get_favorite_cities(user_id)
        keyboard = []
        
        if not favorites:
            keyboard.append([InlineKeyboardButton("⭐ Добавить город в избранное", callback_data="add_favorite")])
        else:
            for city in favorites:
                keyboard.append([
                    InlineKeyboardButton(f"📍 {city}", callback_data=f"city_{city}"),
                    InlineKeyboardButton("❌", callback_data=f"remove_fav_{city}")
                ])
        
        keyboard.append([
            InlineKeyboardButton("➕ Добавить город", callback_data="add_favorite"),
            InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_history_keyboard(user_id: int, db: Database) -> InlineKeyboardMarkup:
        """Клавиатура истории"""
        history = db.get_user_history(user_id)
        keyboard = []
        
        if not history:
            keyboard.append([InlineKeyboardButton("📭 История пуста", callback_data="noop")])
        else:
            for city, timestamp in history[:10]:  # Последние 10
                try:
                    time_obj = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    time_str = time_obj.strftime("%d.%m %H:%M")
                except:
                    time_str = timestamp
                
                keyboard.append([
                    InlineKeyboardButton(f"📍 {city} ({time_str})", callback_data=f"city_{city}")
                ])
        
        keyboard.append([
            InlineKeyboardButton("🗑️ Очистить историю", callback_data="clear_history"),
            InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
        """Админ-панель"""
        keyboard = [
            [
                InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
                InlineKeyboardButton("📋 Логи", callback_data="admin_logs")
            ],
            [
                InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
                InlineKeyboardButton("✉️ Рассылка", callback_data="admin_broadcast")
            ],
            [
                InlineKeyboardButton("🧹 Очистить кэш", callback_data="admin_clear_cache"),
                InlineKeyboardButton("🔄 Вкл/Выкл", callback_data="admin_toggle")
            ],
            [
                InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_back_keyboard() -> InlineKeyboardMarkup:
        """Кнопка Назад"""
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]]
        return InlineKeyboardMarkup(keyboard)

# ============= ОСНОВНОЙ БОТ =============
class WeatherBot:
    """Основной класс бота"""
    
    def __init__(self):
        self.db = Database()
        self.application = None
        self.bot_active = True
        
        # Сообщения для автоудаления
        self.messages_to_delete = {}
    
    async def auto_delete_message(self, chat_id: int, message_id: int, delay: int = Config.AUTO_DELETE_DELAY):
        """Автоматическое удаление сообщения через указанное время"""
        await asyncio.sleep(delay)
        try:
            await self.application.bot.delete_message(chat_id, message_id)
        except Exception as e:
            logger.debug(f"Auto-delete failed: {e}")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = (
            f"👋 Привет, {user.first_name}!\n\n"
            f"🌤️ <b>Погода 7 дней</b> - ваш личный метеоролог\n\n"
            f"<i>Выберите действие в меню ниже:</i>"
        )
        
        message = await update.message.reply_text(
            welcome_text,
            reply_markup=KeyboardManager.get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
        
        # Автоудаление для обычных пользователей
        if not self.db.is_admin(user.id):
            asyncio.create_task(self.auto_delete_message(update.effective_chat.id, message.message_id))
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Обновляем активность
        self.db.update_activity(user_id)
        
        # Обрабатываем действие
        action = query.data
        
        # Проверяем, активен ли бот
        if not self.bot_active and not self.db.is_admin(user_id):
            await query.answer("Бот временно отключен администратором", show_alert=True)
            return
        
        # Главное меню
        if action == "back_to_main":
            await self.show_main_menu(query)
            return
        
        # Погода
        elif action == "weather_city":
            await self.show_city_selection(query)
        
        elif action.startswith("city_"):
            city = action[5:]  # Убираем "city_"
            await self.get_weather_for_city(query, city)
        
        elif action == "other_city":
            await query.edit_message_text(
                "✏️ <b>Введите название города:</b>\n\n"
                "<i>Пример: Москва, Санкт-Петербург, Йошкар-Ола</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=KeyboardManager.get_back_keyboard()
            )
        
        # История
        elif action == "history":
            await self.show_history(query)
        
        elif action == "history_list":
            await self.show_history_list(query)
        
        elif action == "clear_history":
            # В реальной реализации нужно добавить метод очистки истории
            await query.answer("Функция в разработке", show_alert=True)
        
        # Избранное
        elif action == "favorites":
            await self.show_favorites(query)
        
        elif action == "favorites_list":
            await self.show_favorites_list(query)
        
        elif action.startswith("remove_fav_"):
            city = action[11:]  # Убираем "remove_fav_"
            self.db.remove_favorite_city(user_id, city)
            await query.answer(f"❌ {city} удален из избранного")
            await self.show_favorites(query)
        
        elif action == "add_favorite":
            await query.edit_message_text(
                "⭐ <b>Добавить город в избранное:</b>\n\n"
                "<i>Введите название города:</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=KeyboardManager.get_back_keyboard()
            )
        
        # Мои города
        elif action == "my_cities":
            await self.show_my_cities(query)
        
        # Популярные города
        elif action == "popular":
            await self.show_popular_cities(query)
        
        # Настройки
        elif action == "settings":
            await self.show_settings(query)
        
        # Админ-панель
        elif action == "admin_panel":
            if self.db.is_admin(user_id):
                await self.show_admin_panel(query)
            else:
                await query.answer("⛔ Доступ запрещен", show_alert=True)
        
        elif action.startswith("admin_"):
            if self.db.is_admin(user_id):
                await self.handle_admin_action(query, action)
            else:
                await query.answer("⛔ Доступ запрещен", show_alert=True)
        
        await query.answer()
    
    async def show_main_menu(self, query):
        """Показать главное меню"""
        await query.edit_message_text(
            "🌤️ <b>Главное меню</b>\n\n"
            "<i>Выберите действие:</i>",
            reply_markup=KeyboardManager.get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    async def show_city_selection(self, query):
        """Показать выбор города"""
        await query.edit_message_text(
            "📍 <b>Выберите город:</b>",
            reply_markup=KeyboardManager.get_city_selection_keyboard(
                query.from_user.id, self.db
            ),
            parse_mode=ParseMode.HTML
        )
    
    async def get_weather_for_city(self, query, city):
        """Получить погоду для города"""
        # Показываем загрузку
        await query.edit_message_text(
            f"⏳ <b>Загружаю прогноз для {city}...</b>",
            parse_mode=ParseMode.HTML
        )
        
        # Получаем прогноз
        forecast = await WeatherService.get_weather_forecast(city, self.db)
        
        if forecast:
            # Добавляем запрос в историю
            self.db.add_weather_request(query.from_user.id, city)
            
            # Форматируем ответ
            formatted = WeatherFormatter.format_weather_forecast(forecast)
            
            # Кнопки действий
            keyboard = [
                [
                    InlineKeyboardButton("⭐ Добавить в избранное", callback_data=f"add_fav_{city}"),
                    InlineKeyboardButton("🔄 Обновить", callback_data=f"city_{city}")
                ],
                [
                    InlineKeyboardButton("📍 Другой город", callback_data="weather_city"),
                    InlineKeyboardButton("↩️ Главное меню", callback_data="back_to_main")
                ]
            ]
            
            await query.edit_message_text(
                formatted,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            keyboard = [[InlineKeyboardButton("↩️ Попробовать снова", callback_data="weather_city")]]
            await query.edit_message_text(
                f"❌ <b>Не удалось получить прогноз для {city}</b>\n\n"
                f"<i>Проверьте правильность названия города или попробуйте позже.</i>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
    
    async def show_history(self, query):
        """Показать историю"""
        await query.edit_message_text(
            "📚 <b>История запросов</b>\n\n"
            "<i>Последние 15 запросов:</i>",
            reply_markup=KeyboardManager.get_history_keyboard(
                query.from_user.id, self.db
            ),
            parse_mode=ParseMode.HTML
        )
    
    async def show_favorites(self, query):
        """Показать избранное"""
        await query.edit_message_text(
            "⭐ <b>Избранные города</b>",
            reply_markup=KeyboardManager.get_favorites_keyboard(
                query.from_user.id, self.db
            ),
            parse_mode=ParseMode.HTML
        )
    
    async def show_admin_panel(self, query):
        """Показать админ-панель"""
        await query.edit_message_text(
            "👑 <b>Админ-панель</b>\n\n"
            "<i>Выберите действие:</i>",
            reply_markup=KeyboardManager.get_admin_panel_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    async def handle_admin_action(self, query, action):
        """Обработка действий админ-панели"""
        if action == "admin_stats":
            stats = self.db.get_statistics()
            
            stats_text = (
                "📊 <b>Статистика бота</b>\n\n"
                f"👥 <b>Всего пользователей:</b> {stats['total_users']}\n"
                f"🎯 <b>Активных (7 дней):</b> {stats['active_users']}\n"
                f"📈 <b>Всего запросов:</b> {stats['total_requests']}\n\n"
                "<b>🏙️ Популярные города:</b>\n"
            )
            
            for city, count in stats['popular_cities'][:5]:
                stats_text += f"  • {city}: {count} запросов\n"
            
            keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="admin_panel")]]
            await query.edit_message_text(
                stats_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        
        elif action == "admin_logs":
            logs = self.db.get_recent_logs(10)
            
            if not logs:
                logs_text = "📋 <b>Логи системы</b>\n\nНет записей в логах."
            else:
                logs_text = "📋 <b>Последние логи:</b>\n\n"
                for level, message, timestamp in logs:
                    try:
                        time_obj = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                        time_str = time_obj.strftime("%d.%m %H:%M")
                    except:
                        time_str = timestamp
                    
                    emoji = "❌" if level == "ERROR" else "⚠️" if level == "WARNING" else "ℹ️"
                    logs_text += f"{emoji} <b>{time_str}</b> [{level}]: {message[:50]}...\n"
            
            keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="admin_panel")]]
            await query.edit_message_text(
                logs_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        
        elif action == "admin_clear_cache":
            self.db.clear_cache()
            await query.answer("✅ Кэш очищен", show_alert=True)
            await self.show_admin_panel(query)
        
        elif action == "admin_toggle":
            self.bot_active = not self.bot_active
            status = "✅ ВКЛЮЧЕН" if self.bot_active else "⛔ ВЫКЛЮЧЕН"
            await query.answer(f"Бот {status}", show_alert=True)
            await self.show_admin_panel(query)
        
        else:
            await query.answer("Функция в разработке", show_alert=True)
            await self.show_admin_panel(query)
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        user = update.effective_user
        text = update.message.text.strip()
        
        # Обновляем активность
        self.db.update_activity(user.id)
        
        # Проверяем, если пользователь ввел город
        if text:
            # Нормализуем название города
            city = WeatherService.normalize_city_name(text)
            
            # Показываем загрузку
            message = await update.message.reply_text(
                f"⏳ <b>Загружаю прогноз для {city}...</b>",
                parse_mode=ParseMode.HTML
            )
            
            # Получаем прогноз
            forecast = await WeatherService.get_weather_forecast(city, self.db)
            
            if forecast:
                # Добавляем запрос в историю
                self.db.add_weather_request(user.id, city)
                
                # Форматируем ответ
                formatted = WeatherFormatter.format_weather_forecast(forecast)
                
                # Кнопки действий
                keyboard = [
                    [
                        InlineKeyboardButton("⭐ Добавить в избранное", callback_data=f"add_fav_{city}"),
                        InlineKeyboardButton("📍 Другой город", callback_data="weather_city")
                    ],
                    [
                        InlineKeyboardButton("↩️ Главное меню", callback_data="back_to_main")
                    ]
                ]
                
                await message.edit_text(
                    formatted,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            else:
                keyboard = [[InlineKeyboardButton("↩️ Попробовать снова", callback_data="weather_city")]]
                await message.edit_text(
                    f"❌ <b>Не удалось получить прогноз для {city}</b>\n\n"
                    f"<i>Проверьте правильность названия города или попробуйте позже.</i>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            
            # Автоудаление для обычных пользователей
            if not self.db.is_admin(user.id):
                asyncio.create_task(self.auto_delete_message(
                    update.effective_chat.id, 
                    message.message_id
                ))
    
    async def add_favorite_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Добавление города в избранное через callback"""
        query = update.callback_query
        if query.data.startswith("add_fav_"):
            city = query.data[8:]  # Убираем "add_fav_"
            self.db.add_favorite_city(query.from_user.id, city)
            await query.answer(f"⭐ {city} добавлен в избранное")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)
        self.db.add_system_log("ERROR", str(context.error))
        
        if update and update.effective_chat:
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Произошла ошибка. Пожалуйста, попробуйте позже."
                )
            except:
                pass

# ============= РЕНДЕР-СПЕЦИФИЧНЫЕ ФУНКЦИИ =============
async def wake_up_render():
    """Пробуждение приложения на Render.com"""
    if Config.RENDER_WAKEUP_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.RENDER_WAKEUP_URL) as response:
                    if response.status == 200:
                        logger.info("Render.com: Приложение пробуждено")
                    else:
                        logger.warning(f"Render.com: Ошибка пробуждения: {response.status}")
        except Exception as e:
            logger.error(f"Render.com: Ошибка пробуждения: {e}")
    else:
        logger.debug("Render.com: URL пробуждения не настроен")

async def scheduled_wakeup():
    """Планировщик пробуждения каждые 10 минут"""
    while True:
        await asyncio.sleep(600)  # 10 минут
        await wake_up_render()

# ============= ЗАПУСК БОТА =============
def main():
    """Основная функция запуска бота"""
    
    # Проверка токена
    if not Config.BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        logger.info("Установите переменную окружения BOT_TOKEN на Render.com")
        return
    
    # Создаем бота
    bot = WeatherBot()
    
    # Создаем приложение
    app = Application.builder().token(Config.BOT_TOKEN).build()
    bot.application = app
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CallbackQueryHandler(bot.button_handler))
    app.add_handler(CallbackQueryHandler(bot.add_favorite_handler, pattern="^add_fav_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text_message))
    app.add_error_handler(bot.error_handler)
    
    # Логирование запуска
    logger.info("🤖 Бот запускается...")
    logger.info(f"📊 Версия Python: {os.sys.version}")
    logger.info(f"🌐 Режим Render.com: {'АКТИВЕН' if Config.RENDER_AUTO_WAKEUP else 'неактивен'}")
    
    # Добавляем системный лог
    bot.db.add_system_log("INFO", "Бот запущен")
    
    # Запускаем планировщик пробуждения (если настроено)
    if Config.RENDER_AUTO_WAKEUP and Config.RENDER_WAKEUP_URL:
        logger.info("⏰ Активирован авто-пробуждение каждые 10 минут")
        loop = asyncio.get_event_loop()
        loop.create_task(scheduled_wakeup())
    
    # Запускаем бота
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
