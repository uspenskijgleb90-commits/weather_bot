#!/usr/bin/env python3
"""
Телеграм-бот "Погода 7 дней"
Разработан для хостинга Render.com
Python 3.13.4
"""

import os
import json
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import psycopg2
from psycopg2 import pool
from psycopg2.extras import DictCursor
import pytz
from urllib.parse import urlparse

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
    """Конфигурация бота"""
    # Получите токен бота через переменную окружения
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    
    # Для Render.com - PostgreSQL database URL
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    
    # Для Render.com - автоматическое обновление каждые 10 минут
    RENDER_AUTO_WAKEUP = True
    RENDER_WAKEUP_URL = os.getenv("RENDER_WAKEUP_URL", "")
    
    # Настройки кэширования (в секундах)
    CACHE_DURATION = 1800  # 30 минут
    
    # Настройки автоудаления (в секундах)
    AUTO_DELETE_DELAY = 35  # 35 секунд
    
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
    """Класс для работы с базой данных PostgreSQL на Render.com"""
    
    def __init__(self):
        self.connection_pool = None
        self.init_connection_pool()
        self.init_db()
    
    def init_connection_pool(self):
        """Инициализация пула соединений с PostgreSQL"""
        try:
            # Для Render.com используем DATABASE_URL
            if Config.DATABASE_URL:
                # Парсим URL базы данных
                result = urlparse(Config.DATABASE_URL)
                username = result.username
                password = result.password
                database = result.path[1:]
                hostname = result.hostname
                port = result.port or 5432
                
                # Создаем пул соединений
                self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                    1, 20,
                    user=username,
                    password=password,
                    host=hostname,
                    port=port,
                    database=database
                )
                logger.info("✅ Подключение к PostgreSQL установлено")
            else:
                # Для локальной разработки (SQLite)
                logger.warning("DATABASE_URL не найден, используем локальную БД")
                import sqlite3
                self.db_type = "sqlite"
                self.conn = sqlite3.connect("weather_bot.db", check_same_thread=False)
                self.cursor = self.conn.cursor()
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            # Фолбэк на локальную БД
            import sqlite3
            self.db_type = "sqlite"
            self.conn = sqlite3.connect("weather_bot.db", check_same_thread=False)
            self.cursor = self.conn.cursor()
    
    def get_connection(self):
        """Получение соединения из пула"""
        if self.connection_pool:
            return self.connection_pool.getconn()
        return self.conn
    
    def return_connection(self, conn):
        """Возврат соединения в пул"""
        if self.connection_pool:
            self.connection_pool.putconn(conn)
    
    def init_db(self):
        """Инициализация таблиц в базе данных"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Пользователи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_admin BOOLEAN DEFAULT FALSE
                )
            ''')
            
            # Запросы погоды
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weather_requests (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    city TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                )
            ''')
            
            # Избранные города
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS favorite_cities (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    city TEXT,
                    UNIQUE(user_id, city),
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                )
            ''')
            
            # Кэш погоды
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weather_cache (
                    city TEXT PRIMARY KEY,
                    data TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Системные логи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_logs (
                    id SERIAL PRIMARY KEY,
                    level TEXT,
                    message TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Создаем администратора
            admin_id = os.getenv("ADMIN_ID")
            if admin_id:
                try:
                    cursor.execute('''
                        INSERT INTO users (user_id, username, is_admin, joined_date)
                        VALUES (%s, %s, TRUE, CURRENT_TIMESTAMP)
                        ON CONFLICT (user_id) DO UPDATE SET is_admin = TRUE
                    ''', (int(admin_id), "admin"))
                except:
                    pass
            
            conn.commit()
            logger.info("✅ Таблицы БД инициализированы")
            
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации БД: {e}")
            conn.rollback()
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def add_user(self, user_id: int, username: str, first_name: str, last_name: str):
        """Добавление нового пользователя"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, joined_date, last_activity)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_activity = CURRENT_TIMESTAMP
            ''', (user_id, username, first_name, last_name))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка добавления пользователя: {e}")
            conn.rollback()
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def update_activity(self, user_id: int):
        """Обновление времени последней активности"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET last_activity = CURRENT_TIMESTAMP
                WHERE user_id = %s
            ''', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка обновления активности: {e}")
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def add_weather_request(self, user_id: int, city: str):
        """Добавление запроса погоды"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO weather_requests (user_id, city, timestamp)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
            ''', (user_id, city))
            
            # Удаляем старые записи, оставляем последние 15
            cursor.execute('''
                DELETE FROM weather_requests 
                WHERE id NOT IN (
                    SELECT id FROM weather_requests 
                    WHERE user_id = %s 
                    ORDER BY timestamp DESC 
                    LIMIT 15
                ) AND user_id = %s
            ''', (user_id, user_id))
            
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка добавления запроса: {e}")
            conn.rollback()
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def get_user_history(self, user_id: int, limit: int = 15) -> List[Tuple]:
        """Получение истории запросов пользователя"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT city, timestamp::text
                FROM weather_requests 
                WHERE user_id = %s 
                ORDER BY timestamp DESC 
                LIMIT %s
            ''', (user_id, limit))
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Ошибка получения истории: {e}")
            return []
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def add_favorite_city(self, user_id: int, city: str):
        """Добавление города в избранное"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO favorite_cities (user_id, city)
                VALUES (%s, %s)
                ON CONFLICT (user_id, city) DO NOTHING
            ''', (user_id, city))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка добавления в избранное: {e}")
            conn.rollback()
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def remove_favorite_city(self, user_id: int, city: str):
        """Удаление города из избранного"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM favorite_cities 
                WHERE user_id = %s AND city = %s
            ''', (user_id, city))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка удаления из избранного: {e}")
            conn.rollback()
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def get_favorite_cities(self, user_id: int) -> List[str]:
        """Получение избранных городов пользователя"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT city 
                FROM favorite_cities 
                WHERE user_id = %s 
                ORDER BY id DESC
            ''', (user_id,))
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения избранного: {e}")
            return []
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def cache_weather_data(self, city: str, data: str):
        """Кэширование данных о погоде"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO weather_cache (city, data, timestamp)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (city) DO UPDATE SET
                data = EXCLUDED.data,
                timestamp = CURRENT_TIMESTAMP
            ''', (city, data))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка кэширования: {e}")
            conn.rollback()
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def get_cached_weather(self, city: str, max_age: int = Config.CACHE_DURATION) -> Optional[str]:
        """Получение кэшированных данных о погоде"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT data 
                FROM weather_cache 
                WHERE city = %s 
                AND EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - timestamp)) < %s
            ''', (city, max_age))
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Ошибка получения кэша: {e}")
            return None
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def add_system_log(self, level: str, message: str):
        """Добавление системного лога"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO system_logs (level, message)
                VALUES (%s, %s)
            ''', (level, message))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка добавления лога: {e}")
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def get_statistics(self) -> Dict:
        """Получение статистики"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Общее количество пользователей
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            
            # Активные пользователи (последние 7 дней)
            cursor.execute('''
                SELECT COUNT(DISTINCT user_id) 
                FROM weather_requests 
                WHERE timestamp >= CURRENT_TIMESTAMP - INTERVAL '7 days'
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
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return {"total_users": 0, "active_users": 0, "total_requests": 0, "popular_cities": []}
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def get_recent_logs(self, limit: int = 50) -> List[Tuple]:
        """Получение последних логов"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT level, message, timestamp::text
                FROM system_logs 
                ORDER BY timestamp DESC 
                LIMIT %s
            ''', (limit,))
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Ошибка получения логов: {e}")
            return []
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def is_admin(self, user_id: int) -> bool:
        """Проверка, является ли пользователь администратором"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT is_admin FROM users WHERE user_id = %s
            ''', (user_id,))
            result = cursor.fetchone()
            return result and result[0] if result else False
        except Exception as e:
            logger.error(f"Ошибка проверки админа: {e}")
            return False
        finally:
            if self.connection_pool:
                self.return_connection(conn)
    
    def clear_cache(self):
        """Очистка кэша"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM weather_cache")
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка очистки кэша: {e}")
            conn.rollback()
        finally:
            if self.connection_pool:
                self.return_connection(conn)

# ============= СЕРВИС ПОГОДЫ =============
class WeatherService:
    """Сервис для получения прогноза погоды"""
    
    @classmethod
    def normalize_city_name(cls, city: str) -> str:
        """Нормализация названия города"""
        city_lower = city.lower().strip()
        
        # Проверяем псевдонимы
        if city_lower in Config.CITY_ALIASES:
            return Config.CITY_ALIASES[city_lower]
        
        # Если город не найден, возвращаем оригинал с заглавной буквой
        return city.strip().title()
    
    @staticmethod
    async def fetch_weather(city: str) -> Optional[Dict]:
        """Получение прогноза погоды с API"""
        try:
            normalized_city = WeatherService.normalize_city_name(city)
            
            async with aiohttp.ClientSession() as session:
                # Сначала получаем координаты города
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={normalized_city}&count=1&language=ru"
                async with session.get(geo_url) as geo_response:
                    if geo_response.status == 200:
                        geo_data = await geo_response.json()
                        
                        if not geo_data.get("results"):
                            # Пробуем англоязычный поиск
                            geo_url_en = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
                            async with session.get(geo_url_en) as geo_response_en:
                                if geo_response_en.status == 200:
                                    geo_data = await geo_response_en.json()
                        
                        if geo_data.get("results"):
                            result = geo_data["results"][0]
                            lat = result["latitude"]
                            lon = result["longitude"]
                            city_name = result.get("name", normalized_city)
                            
                            # Теперь получаем погоду
                            params = {
                                "latitude": lat,
                                "longitude": lon,
                                "daily": ["temperature_2m_max", "temperature_2m_min", 
                                         "precipitation_sum", "wind_speed_10m_max",
                                         "relative_humidity_2m_max"],
                                "timezone": "auto",
                                "forecast_days": 7
                            }
                            
                            weather_url = "https://api.open-meteo.com/v1/forecast"
                            async with session.get(weather_url, params=params) as weather_response:
                                if weather_response.status == 200:
                                    data = await weather_response.json()
                                    
                                    forecast_data = {
                                        "city": city_name,
                                        "daily": data.get("daily", {}),
                                        "timezone": data.get("timezone", "UTC")
                                    }
                                    
                                    return forecast_data
                        
            return None
                        
        except Exception as e:
            logger.error(f"Ошибка получения погоды: {e}")
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
        
        # Получаем данные с API
        forecast = await cls.fetch_weather(city)
        if forecast:
            # Кэшируем данные
            db.cache_weather_data(normalized_city, json.dumps(forecast))
        
        return forecast

# ============= ФОРМАТИРОВАНИЕ =============
class WeatherFormatter:
    """Класс для форматирования прогноза погоды"""
    
    @staticmethod
    def get_weather_emoji(temp: Optional[float] = None) -> str:
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
                day_name = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"][date_obj.weekday()]
                date_str = date_obj.strftime("%d.%m")
                
                # Эмодзи для дня
                if i == 0:
                    day_emoji = "📅"
                elif day_name == "СБ" or day_name == "ВС":
                    day_emoji = "🎉"
                else:
                    day_emoji = "📆"
                
                # Эмодзи для погоды
                temp_avg = (temps_max[i] + temps_min[i]) / 2 if i < len(temps_max) and i < len(temps_min) else 0
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
                logger.error(f"Ошибка форматирования дня {i}: {e}")
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
        for i in range(0, min(6, len(Config.POPULAR_CITIES)), 2):
            row = []
            for city in Config.POPULAR_CITIES[i:i+2]:
                row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
            keyboard.append(row)
        
        # Дополнительные опции
        keyboard.append([
            InlineKeyboardButton("🔍 Другой город", callback_data="other_city"),
            InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")
        ])
        
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
    
    async def auto_delete_message(self, chat_id: int, message_id: int, delay: int = Config.AUTO_DELETE_DELAY):
        """Автоматическое удаление сообщения через указанное время"""
        await asyncio.sleep(delay)
        try:
            await self.application.bot.delete_message(chat_id, message_id)
        except Exception as e:
            logger.debug(f"Автоудаление не удалось: {e}")
    
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
        await query.answer()
        
        user_id = query.from_user.id
        
        # Обновляем активность
        self.db.update_activity(user_id)
        
        # Обрабатываем действие
        action = query.data
        
        # Проверяем, активен ли бот
        if not self.bot_active and not self.db.is_admin(user_id):
            await query.edit_message_text("⛔ Бот временно отключен администратором")
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
        
        # Избранное
        elif action == "favorites":
            await self.show_favorites(query)
        
        elif action == "favorites_list":
            await self.show_favorites_list(query)
        
        # Популярные города
        elif action == "popular":
            await self.show_popular_cities(query)
        
        # Админ-панель
        elif action == "admin_panel":
            if self.db.is_admin(user_id):
                await self.show_admin_panel(query)
            else:
                await query.edit_message_text("⛔ Доступ запрещен")
        
        elif action.startswith("admin_"):
            if self.db.is_admin(user_id):
                await self.handle_admin_action(query, action)
            else:
                await query.edit_message_text("⛔ Доступ запрещен")
        
        else:
            await query.edit_message_text(
                "⚙️ <b>Настройки</b>\n\n"
                "<i>Эта функция в разработке</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=KeyboardManager.get_back_keyboard()
            )
    
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
            
            message = await query.edit_message_text(
                formatted,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            keyboard = [[InlineKeyboardButton("↩️ Попробовать снова", callback_data="weather_city")]]
            message = await query.edit_message_text(
                f"❌ <b>Не удалось получить прогноз для {city}</b>\n\n"
                f"<i>Проверьте правильность названия города или попробуйте позже.</i>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        
        # Автоудаление для обычных пользователей
        if not self.db.is_admin(query.from_user.id):
            asyncio.create_task(self.auto_delete_message(
                query.message.chat_id,
                message.message_id
            ))
    
    async def show_history(self, query):
        """Показать историю"""
        user_id = query.from_user.id
        history = self.db.get_user_history(user_id)
        
        if not history:
            text = "📚 <b>История запросов</b>\n\n📭 История запросов пуста"
        else:
            text = "📚 <b>История запросов</b>\n\n"
            for i, (city, timestamp) in enumerate(history[:10], 1):
                try:
                    time_obj = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    time_str = time_obj.strftime("%d.%m %H:%M")
                except:
                    time_str = timestamp
                
                text += f"{i}. <b>{city}</b> - {time_str}\n"
        
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def show_favorites(self, query):
        """Показать избранное"""
        user_id = query.from_user.id
        favorites = self.db.get_favorite_cities(user_id)
        
        if not favorites:
            text = "⭐ <b>Избранные города</b>\n\n📭 У вас нет избранных городов"
            keyboard = [
                [InlineKeyboardButton("➕ Добавить город", callback_data="other_city")],
                [InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]
            ]
        else:
            text = "⭐ <b>Избранные города</b>\n\n"
            for i, city in enumerate(favorites, 1):
                text += f"{i}. <b>{city}</b>\n"
            
            keyboard = []
            for i in range(0, len(favorites), 2):
                row = []
                for city in favorites[i:i+2]:
                    row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
                keyboard.append(row)
            
            keyboard.append([
                InlineKeyboardButton("➕ Добавить город", callback_data="other_city"),
                InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")
            ])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def show_admin_panel(self, query):
        """Показать админ-панель"""
        await query.edit_message_text(
            "👑 <b>Админ-панель</b>\n\n"
            "<i>Выберите действие:</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
                [InlineKeyboardButton("📋 Логи", callback_data="admin_logs")],
                [InlineKeyboardButton("🧹 Очистить кэш", callback_data="admin_clear_cache")],
                [InlineKeyboardButton("🔄 Вкл/Выкл", callback_data="admin_toggle")],
                [InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]
            ])
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
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        user = update.effective_user
        text = update.message.text.strip()
        
        # Обновляем активность
        self.db.update_activity(user.id)
        
        # Проверяем, если пользователь ввел город
        if text:
            # Проверяем, не команда ли это
            if text.startswith('/'):
                return
            
            # Показываем загрузку
            message = await update.message.reply_text(
                f"⏳ <b>Загружаю прогноз для {text}...</b>",
                parse_mode=ParseMode.HTML
            )
            
            # Получаем прогноз
            forecast = await WeatherService.get_weather_forecast(text, self.db)
            
            if forecast:
                # Добавляем запрос в историю
                self.db.add_weather_request(user.id, text)
                
                # Форматируем ответ
                formatted = WeatherFormatter.format_weather_forecast(forecast)
                
                # Кнопки действий
                keyboard = [
                    [
                        InlineKeyboardButton("⭐ Добавить в избранное", callback_data=f"add_fav_{text}"),
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
                    f"❌ <b>Не удалось получить прогноз для {text}</b>\n\n"
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
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)
        self.db.add_system_log("ERROR", str(context.error))

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
