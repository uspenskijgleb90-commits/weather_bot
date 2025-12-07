#!/usr/bin/env python3
"""
Телеграм-бот "Погода 7 дней" - Минимальная версия
Без базы данных, только погода
Python 3.13.4
"""

import os
import json
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

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
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    
    # Для Render.com - автоматическое пробуждение
    RENDER_AUTO_WAKEUP = True
    RENDER_WAKEUP_URL = os.getenv("RENDER_WAKEUP_URL", "")
    
    # Настройки
    AUTO_DELETE_DELAY = 35  # 35 секунд
    
    # Города для быстрого доступа
    POPULAR_CITIES = [
        "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
        "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
        "Уфа", "Красноярск", "Пермь", "Воронеж", "Волгоград",
        "Минск", "Киев", "Астана", "Бишкек", "Ташкент",
        "Йошкар-Ола", "Алматы", "Баку", "Тбилиси", "Ереван"
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

# ============= ХРАНЕНИЕ В ПАМЯТИ =============
class MemoryStorage:
    """Хранилище в оперативной памяти"""
    
    def __init__(self):
        self.weather_cache = {}
        self.user_history = defaultdict(list)
        self.favorites = defaultdict(list)
        self.cache_timestamps = {}
        
    def normalize_city(self, city: str) -> str:
        """Нормализация названия города"""
        city_lower = city.lower().strip()
        
        if city_lower in Config.CITY_ALIASES:
            return Config.CITY_ALIASES[city_lower]
        
        # Ищем в популярных городах
        for popular_city in Config.POPULAR_CITIES:
            if city_lower == popular_city.lower():
                return popular_city
        
        return city.strip().title()
    
    def add_to_history(self, user_id: int, city: str):
        """Добавление в историю"""
        normalized_city = self.normalize_city(city)
        history = self.user_history[user_id]
        
        # Удаляем если уже есть
        if normalized_city in history:
            history.remove(normalized_city)
        
        # Добавляем в начало
        history.insert(0, normalized_city)
        
        # Ограничиваем 15 записями
        if len(history) > 15:
            self.user_history[user_id] = history[:15]
    
    def get_history(self, user_id: int) -> List[str]:
        """Получение истории"""
        return self.user_history.get(user_id, [])
    
    def add_favorite(self, user_id: int, city: str):
        """Добавление в избранное"""
        normalized_city = self.normalize_city(city)
        if normalized_city not in self.favorites[user_id]:
            self.favorites[user_id].append(normalized_city)
    
    def remove_favorite(self, user_id: int, city: str):
        """Удаление из избранного"""
        normalized_city = self.normalize_city(city)
        if normalized_city in self.favorites[user_id]:
            self.favorites[user_id].remove(normalized_city)
    
    def get_favorites(self, user_id: int) -> List[str]:
        """Получение избранного"""
        return self.favorites.get(user_id, [])
    
    def cache_weather(self, city: str, data: dict):
        """Кэширование погоды"""
        normalized_city = self.normalize_city(city)
        self.weather_cache[normalized_city] = data
        self.cache_timestamps[normalized_city] = datetime.now()
    
    def get_cached_weather(self, city: str, max_age: int = 1800) -> Optional[dict]:
        """Получение кэшированной погоды"""
        normalized_city = self.normalize_city(city)
        
        if normalized_city in self.weather_cache:
            timestamp = self.cache_timestamps.get(normalized_city)
            if timestamp and (datetime.now() - timestamp).seconds < max_age:
                return self.weather_cache[normalized_city]
        
        return None

# ============= СЕРВИС ПОГОДЫ =============
class WeatherService:
    """Сервис для получения прогноза погоды"""
    
    @staticmethod
    async def fetch_weather(city: str) -> Optional[Dict]:
        """Получение прогноза погоды с API"""
        try:
            async with aiohttp.ClientSession() as session:
                # Сначала получаем координаты города
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=ru"
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
                            city_name = result.get("name", city)
                            
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
        
        dates = daily.get("time", [])[:7]
        temps_max = daily.get("temperature_2m_max", [])[:7]
        temps_min = daily.get("temperature_2m_min", [])[:7]
        precip = daily.get("precipitation_sum", [])[:7]
        wind = daily.get("wind_speed_10m_max", [])[:7]
        humidity = daily.get("relative_humidity_2m_max", [])[:7]
        
        if not dates:
            return "❌ Нет данных о погоде"
        
        lines = [f"<b>🌤️ Прогноз погоды для {city}</b>\n"]
        lines.append(f"<i>На 7 дней (обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')})</i>\n")
        lines.append("─" * 30)
        
        for i in range(min(7, len(dates))):
            try:
                date_obj = datetime.strptime(dates[i], "%Y-%m-%d")
                day_name = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"][date_obj.weekday()]
                date_str = date_obj.strftime("%d.%m")
                
                if i == 0:
                    day_emoji = "📅"
                elif day_name in ["СБ", "ВС"]:
                    day_emoji = "🎉"
                else:
                    day_emoji = "📆"
                
                temp_avg = (temps_max[i] + temps_min[i]) / 2
                weather_emoji = WeatherFormatter.get_weather_emoji(temp=temp_avg)
                
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
                continue
        
        lines.append("\n<i>❓ Для нового запроса нажмите /start</i>")
        
        return "\n".join(lines)

# ============= КЛАВИАТУРЫ =============
class KeyboardManager:
    """Менеджер клавиатур"""
    
    @staticmethod
    def get_main_menu_keyboard() -> InlineKeyboardMarkup:
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
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_city_selection_keyboard(user_id: int, storage: MemoryStorage) -> InlineKeyboardMarkup:
        keyboard = []
        
        favorites = storage.get_favorites(user_id)
        if favorites:
            keyboard.append([InlineKeyboardButton("⭐ Избранные", callback_data="favorites_list")])
        
        history = storage.get_history(user_id)
        if history:
            keyboard.append([InlineKeyboardButton("📚 История", callback_data="history_list")])
        
        for i in range(0, min(6, len(Config.POPULAR_CITIES)), 2):
            row = []
            for city in Config.POPULAR_CITIES[i:i+2]:
                row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
            keyboard.append(row)
        
        keyboard.append([
            InlineKeyboardButton("🔍 Другой город", callback_data="other_city"),
            InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_back_keyboard() -> InlineKeyboardMarkup:
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]]
        return InlineKeyboardMarkup(keyboard)

# ============= ОСНОВНОЙ БОТ =============
class WeatherBot:
    """Основной класс бота"""
    
    def __init__(self):
        self.storage = MemoryStorage()
        self.application = None
    
    async def auto_delete_message(self, chat_id: int, message_id: int, delay: int = Config.AUTO_DELETE_DELAY):
        """Автоматическое удаление сообщения"""
        await asyncio.sleep(delay)
        try:
            await self.application.bot.delete_message(chat_id, message_id)
        except:
            pass
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        
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
        
        asyncio.create_task(self.auto_delete_message(update.effective_chat.id, message.message_id))
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        action = query.data
        
        if action == "back_to_main":
            await self.show_main_menu(query)
            return
        
        elif action == "weather_city":
            await self.show_city_selection(query)
        
        elif action.startswith("city_"):
            city = action[5:]
            await self.get_weather_for_city(query, city)
        
        elif action == "other_city":
            await query.edit_message_text(
                "✏️ <b>Введите название города:</b>\n\n"
                "<i>Пример: Москва, Санкт-Петербург, Йошкар-Ола</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=KeyboardManager.get_back_keyboard()
            )
        
        elif action == "history":
            await self.show_history(query)
        
        elif action == "favorites":
            await self.show_favorites(query)
        
        elif action == "popular":
            await self.show_popular_cities(query)
        
        elif action == "settings":
            await query.edit_message_text(
                "⚙️ <b>Настройки</b>\n\n"
                "<i>Версия бота: 1.0 (упрощенная)</i>\n"
                "<i>Хранение данных: в памяти</i>\n"
                "<i>При перезагрузке сервера данные очистятся</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=KeyboardManager.get_back_keyboard()
            )
        
        elif action.startswith("add_fav_"):
            city = action[8:]
            self.storage.add_favorite(user_id, city)
            await query.answer(f"⭐ {city} добавлен в избранное")
        
        elif action.startswith("remove_fav_"):
            city = action[11:]
            self.storage.remove_favorite(user_id, city)
            await query.answer(f"❌ {city} удален из избранного")
            await self.show_favorites(query)
    
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
                query.from_user.id, self.storage
            ),
            parse_mode=ParseMode.HTML
        )
    
    async def get_weather_for_city(self, query, city):
        """Получить погоду для города"""
        await query.edit_message_text(
            f"⏳ <b>Загружаю прогноз для {city}...</b>",
            parse_mode=ParseMode.HTML
        )
        
        # Проверяем кэш
        cached = self.storage.get_cached_weather(city)
        
        if cached:
            forecast = cached
        else:
            # Получаем с API
            forecast = await WeatherService.fetch_weather(city)
            if forecast:
                # Кэшируем
                self.storage.cache_weather(city, forecast)
        
        if forecast:
            # Добавляем в историю
            self.storage.add_to_history(query.from_user.id, city)
            
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
        
        asyncio.create_task(self.auto_delete_message(
            query.message.chat_id,
            message.message_id
        ))
    
    async def show_history(self, query):
        """Показать историю"""
        user_id = query.from_user.id
        history = self.storage.get_history(user_id)
        
        if not history:
            text = "📚 <b>История запросов</b>\n\n📭 История запросов пуста"
        else:
            text = "📚 <b>История запросов</b>\n\n"
            for i, city in enumerate(history[:10], 1):
                text += f"{i}. <b>{city}</b>\n"
        
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def show_favorites(self, query):
        """Показать избранное"""
        user_id = query.from_user.id
        favorites = self.storage.get_favorites(user_id)
        
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
    
    async def show_popular_cities(self, query):
        """Показать популярные города"""
        text = "🎯 <b>Популярные города</b>\n\n"
        
        for i in range(0, len(Config.POPULAR_CITIES), 5):
            cities_chunk = Config.POPULAR_CITIES[i:i+5]
            text += "  • " + " • ".join(cities_chunk) + "\n"
        
        keyboard = [
            [InlineKeyboardButton("🌤️ Выбрать город", callback_data="weather_city")],
            [InlineKeyboardButton("↩️ Назад", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        user = update.effective_user
        text = update.message.text.strip()
        
        if text and not text.startswith('/'):
            message = await update.message.reply_text(
                f"⏳ <b>Загружаю прогноз для {text}...</b>",
                parse_mode=ParseMode.HTML
            )
            
            # Проверяем кэш
            cached = self.storage.get_cached_weather(text)
            
            if cached:
                forecast = cached
            else:
                forecast = await WeatherService.fetch_weather(text)
                if forecast:
                    self.storage.cache_weather(text, forecast)
            
            if forecast:
                self.storage.add_to_history(user.id, text)
                formatted = WeatherFormatter.format_weather_forecast(forecast)
                
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
            
            asyncio.create_task(self.auto_delete_message(
                update.effective_chat.id, 
                message.message_id
            ))
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)

# ============= ФУНКЦИИ ДЛЯ RENDER =============
async def wake_up_render():
    """Пробуждение приложения на Render.com"""
    if Config.RENDER_WAKEUP_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(Config.RENDER_WAKEUP_URL) as response:
                    if response.status == 200:
                        logger.info("Render.com: Приложение пробуждено")
        except:
            pass

async def scheduled_wakeup():
    """Планировщик пробуждения каждые 10 минут"""
    while True:
        await asyncio.sleep(600)
        await wake_up_render()

# ============= ЗАПУСК БОТА =============
def main():
    """Основная функция запуска бота"""
    
    if not Config.BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        logger.info("Установите переменную окружения BOT_TOKEN на Render.com")
        return
    
    bot = WeatherBot()
    app = Application.builder().token(Config.BOT_TOKEN).build()
    bot.application = app
    
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CallbackQueryHandler(bot.button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text_message))
    app.add_error_handler(bot.error_handler)
    
    logger.info("🤖 Бот запускается...")
    logger.info(f"📊 Версия Python: {os.sys.version}")
    
    if Config.RENDER_AUTO_WAKEUP and Config.RENDER_WAKEUP_URL:
        logger.info("⏰ Активирован авто-пробуждение каждые 10 минут")
        loop = asyncio.get_event_loop()
        loop.create_task(scheduled_wakeup())
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
