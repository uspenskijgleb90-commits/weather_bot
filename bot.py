#!/usr/bin/env python3
"""
Телеграм-бот "Погода 7 дней" - Рабочая версия для Python 3.13.4
Использует python-telegram-bot==21.7
"""

import os
import asyncio
import aiohttp
import logging
from datetime import datetime
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
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    RENDER_WAKEUP_URL = os.getenv("RENDER_WAKEUP_URL", "")
    AUTO_DELETE_DELAY = 35
    
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

# ============= ЛОГГИРОВАНИЕ =============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= ПРОСТОЕ ХРАНИЛИЩЕ =============
class MemoryStorage:
    def __init__(self):
        self.cache = {}
        self.history = defaultdict(list)
        self.favorites = defaultdict(list)
    
    def normalize_city(self, city: str) -> str:
        city_lower = city.lower().strip()
        if city_lower in Config.CITY_ALIASES:
            return Config.CITY_ALIASES[city_lower]
        
        for popular in Config.POPULAR_CITIES:
            if city_lower == popular.lower():
                return popular
        
        return city.strip().title()
    
    def add_history(self, user_id: int, city: str):
        norm_city = self.normalize_city(city)
        history = self.history[user_id]
        if norm_city in history:
            history.remove(norm_city)
        history.insert(0, norm_city)
        if len(history) > 10:
            self.history[user_id] = history[:10]
    
    def get_history(self, user_id: int) -> List[str]:
        return self.history.get(user_id, [])

# ============= СЕРВИС ПОГОДЫ =============
class WeatherService:
    @staticmethod
    async def get_weather(city: str) -> Optional[Dict]:
        try:
            async with aiohttp.ClientSession() as session:
                # Получаем координаты
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
                async with session.get(geo_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("results"):
                            result = data["results"][0]
                            lat = result["latitude"]
                            lon = result["longitude"]
                            
                            # Получаем погоду
                            weather_url = "https://api.open-meteo.com/v1/forecast"
                            params = {
                                "latitude": lat,
                                "longitude": lon,
                                "daily": ["temperature_2m_max", "temperature_2m_min", 
                                         "precipitation_sum", "wind_speed_10m_max",
                                         "relative_humidity_2m_max"],
                                "timezone": "auto",
                                "forecast_days": 7
                            }
                            
                            async with session.get(weather_url, params=params) as weather_response:
                                if weather_response.status == 200:
                                    weather_data = await weather_response.json()
                                    return {
                                        "city": result.get("name", city),
                                        "daily": weather_data.get("daily", {})
                                    }
        except Exception as e:
            logger.error(f"Weather error: {e}")
        return None

# ============= ФОРМАТИРОВАНИЕ =============
def format_weather(forecast: Dict) -> str:
    if not forecast:
        return "❌ Не удалось получить прогноз"
    
    daily = forecast["daily"]
    city = forecast["city"]
    
    dates = daily.get("time", [])[:7]
    temps_max = daily.get("temperature_2m_max", [])[:7]
    temps_min = daily.get("temperature_2m_min", [])[:7]
    
    if not dates:
        return "❌ Нет данных"
    
    lines = [f"<b>🌤️ Прогноз погоды для {city}</b>\n"]
    lines.append(f"<i>На 7 дней</i>\n")
    
    for i in range(min(7, len(dates))):
        try:
            date_obj = datetime.strptime(dates[i], "%Y-%m-%d")
            day_name = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"][date_obj.weekday()]
            date_str = date_obj.strftime("%d.%m")
            
            temp_avg = (temps_max[i] + temps_min[i]) / 2
            if temp_avg > 20:
                emoji = "☀️"
            elif temp_avg > 10:
                emoji = "⛅"
            elif temp_avg > 0:
                emoji = "🌤️"
            else:
                emoji = "❄️"
            
            line = f"<b>{day_name} {date_str}</b> {emoji}: {temps_min[i]:.0f}°C ... {temps_max[i]:.0f}°C"
            lines.append(line)
        except:
            continue
    
    lines.append("\n<i>Для нового запроса нажмите /start</i>")
    return "\n".join(lines)

# ============= ОСНОВНОЙ БОТ =============
class WeatherBot:
    def __init__(self):
        self.storage = MemoryStorage()
        self.app = None
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        keyboard = [
            [
                InlineKeyboardButton("🌤️ Погода в городе", callback_data="weather"),
                InlineKeyboardButton("📚 История", callback_data="history")
            ],
            [
                InlineKeyboardButton("🎯 Популярные города", callback_data="popular"),
                InlineKeyboardButton("🔍 Ввести город", callback_data="input_city")
            ]
        ]
        
        text = f"👋 Привет, {user.first_name}!\n🌤️ <b>Погода 7 дней</b>\n\nВыберите действие:"
        
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        action = query.data
        
        if action == "weather":
            await self.show_cities(query)
        elif action == "history":
            await self.show_history(query)
        elif action == "popular":
            await self.show_popular(query)
        elif action == "input_city":
            await query.edit_message_text(
                "✏️ <b>Введите название города:</b>\n\n<i>Например: Москва, Йошкар-Ола</i>",
                parse_mode=ParseMode.HTML
            )
        elif action.startswith("city_"):
            city = action[5:]
            await self.get_weather(query, city)
    
    async def show_cities(self, query):
        keyboard = []
        for i in range(0, len(Config.POPULAR_CITIES), 2):
            row = []
            for city in Config.POPULAR_CITIES[i:i+2]:
                row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔍 Ввести другой город", callback_data="input_city")])
        
        await query.edit_message_text(
            "📍 <b>Выберите город:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def show_history(self, query):
        user_id = query.from_user.id
        history = self.storage.get_history(user_id)
        
        if not history:
            text = "📚 <b>История запросов</b>\n\nИстория пуста"
        else:
            text = "📚 <b>История запросов</b>\n\n"
            for i, city in enumerate(history, 1):
                text += f"{i}. {city}\n"
        
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="weather")]]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def show_popular(self, query):
        text = "🎯 <b>Популярные города</b>\n\n"
        for city in Config.POPULAR_CITIES:
            text += f"• {city}\n"
        
        keyboard = [
            [InlineKeyboardButton("🌤️ Выбрать город", callback_data="weather")],
            [InlineKeyboardButton("↩️ Назад", callback_data="weather")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def get_weather(self, query, city):
        await query.edit_message_text(
            f"⏳ <b>Загружаю прогноз для {city}...</b>",
            parse_mode=ParseMode.HTML
        )
        
        forecast = await WeatherService.get_weather(city)
        
        if forecast:
            self.storage.add_history(query.from_user.id, city)
            formatted = format_weather(forecast)
            
            keyboard = [
                [InlineKeyboardButton("🔄 Обновить", callback_data=f"city_{city}")],
                [InlineKeyboardButton("📍 Другой город", callback_data="weather")]
            ]
            
            await query.edit_message_text(
                formatted,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            keyboard = [[InlineKeyboardButton("↩️ Попробовать снова", callback_data="weather")]]
            await query.edit_message_text(
                f"❌ <b>Не удалось получить прогноз для {city}</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text:
            await self.get_weather_for_text(update, text)
    
    async def get_weather_for_text(self, update, city):
        message = await update.message.reply_text(
            f"⏳ <b>Загружаю прогноз для {city}...</b>",
            parse_mode=ParseMode.HTML
        )
        
        forecast = await WeatherService.get_weather(city)
        
        if forecast:
            self.storage.add_history(update.effective_user.id, city)
            formatted = format_weather(forecast)
            
            keyboard = [
                [InlineKeyboardButton("📍 Другой город", callback_data="weather")],
                [InlineKeyboardButton("📚 История", callback_data="history")]
            ]
            
            await message.edit_text(
                formatted,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            await message.edit_text(
                f"❌ <b>Не удалось получить прогноз для {city}</b>",
                parse_mode=ParseMode.HTML
            )
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}")

# ============= ЗАПУСК =============
async def main():
    bot_token = Config.BOT_TOKEN
    if not bot_token:
        logger.error("❌ BOT_TOKEN не установлен!")
        return
    
    bot = WeatherBot()
    
    app = Application.builder().token(bot_token).build()
    bot.app = app
    
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CallbackQueryHandler(bot.button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    app.add_error_handler(bot.error_handler)
    
    logger.info("🤖 Бот запускается...")
    
    # Автопробуждение для Render
    if Config.RENDER_WAKEUP_URL:
        async def wake_up():
            try:
                async with aiohttp.ClientSession() as session:
                    await session.get(Config.RENDER_WAKEUP_URL)
                    logger.info("🔄 Render пробужден")
            except:
                pass
        
        async def wakeup_scheduler():
            while True:
                await asyncio.sleep(600)  # 10 минут
                await wake_up()
        
        asyncio.create_task(wakeup_scheduler())
    
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
