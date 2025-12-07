import asyncio
import pickle
import logging
import time
import os
import aiohttp
import re
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import pytz
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, 
    KeyboardButton,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, PicklePersistence, MessageHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import Conflict

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WEATHER_CACHE_DURATION = 1800
CHECK_NOTIFICATIONS_INTERVAL = 60
WAKEUP_INTERVAL = 600

# Основные команды для меню
MAIN_MENU = [
    ["🌤️ Погода сейчас"],
    ["📍 Выбрать город", "🔍 Поиск города"],
    ["⏰ Уведомления", "📋 Список городов"],
    ["🔄 Обновить", "❓ Помощь"]
]

# Кнопки для выбора времени уведомлений
TIME_BUTTONS = [
    ["🕖 07:00", "🕗 08:00", "🕘 09:00"],
    ["🕙 10:00", "🕕 18:00", "🕖 19:00"],
    ["🕗 20:00", "🕘 21:00", "🔙 Назад"]
]

# Кнопки для выбора города
CITY_BUTTONS = [
    ["Москва", "Санкт-Петербург"],
    ["Новосибирск", "Екатеринбург"],
    ["Казань", "Нижний Новгород"],
    ["Киев", "Минск"],
    ["🔍 Поиск города", "📋 Все города"],
    ["🔙 Главное меню"]
]

# Популярные города
MAIN_CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
    "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград",
    "Краснодар", "Саратов", "Тюмень", "Тольятти", "Ижевск",
    "Барнаул", "Ульяновск", "Иркутск", "Хабаровск", "Ярославль",
    "Владивосток", "Махачкала", "Томск", "Оренбург", "Кемерово",
    "Новокузнецк", "Рязань", "Астрахань", "Пенза", "Киров",
    "Липецк", "Чебоксары", "Тула", "Калининград", "Курск",
    "Севастополь", "Сочи", "Ставрополь", "Улан-Удэ", "Тверь",
    "Магнитогорск", "Иваново", "Брянск", "Белгород", "Сургут",
    "Владимир", "Архангельск", "Чита", "Симферополь", "Калуга",
    "Смоленск", "Волжский", "Якутск", "Саранск", "Череповец",
    "Вологда", "Орёл", "Курган", "Мурманск", "Тамбов",
    "Петрозаводск", "Кострома", "Новороссийск", "Йошкар-Ола", "Химки",
    "Таганрог", "Сыктывкар", "Нальчик", "Шахты", "Орск",
    "Братск", "Ангарск", "Благовещенск", "Псков", "Бийск",
    "Прокопьевск", "Рыбинск", "Балаково", "Киев", "Харьков",
    "Одесса", "Днепр", "Львов", "Запорожье", "Винница",
    "Херсон", "Чернигов", "Полтава", "Черкассы", "Хмельницкий",
    "Черновцы", "Житомир", "Сумы", "Ровно", "Минск",
    "Гомель", "Могилёв", "Витебск", "Гродно", "Брест",
    "Бобруйск", "Барановичи", "Борисов", "Пинск", "Алматы",
    "Нур-Султан", "Шымкент", "Караганда", "Актобе", "Тараз",
    "Павлодар", "Усть-Каменогорск", "Семей", "Атырау", "Баку",
    "Ереван", "Кишинёв", "Бишкек", "Душанбе", "Ташкент"
]

NOTIFICATION_TIMES = ["07:00", "08:00", "09:00", "10:00", "18:00", "19:00", "20:00", "21:00"]

@dataclass
class UserSettings:
    city: str = ""
    notification_time_local: str = ""
    notification_time_utc: str = ""
    timezone_offset: int = 0
    notifications_enabled: bool = False
    last_weather_update: float = 0
    weather_cache: Dict = field(default_factory=dict)

@dataclass
class WeatherData:
    temperature_min: float = 0
    temperature_max: float = 0
    wind_speed: float = 0
    humidity: int = 0
    precipitation: float = 0
    description: str = ""
    timestamp: float = 0

class WeatherBot:
    
    def __init__(self, token: str, wakeup_url: str = None):
        self.token = token
        self.wakeup_url = wakeup_url
        self.user_data: Dict[int, UserSettings] = {}
        self.weather_cache: Dict[str, Tuple[WeatherData, float]] = {}
        self.load_data()
        
    def save_data(self):
        try:
            with open('user_data.pkl', 'wb') as f:
                pickle.dump(self.user_data, f)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
    
    def load_data(self):
        try:
            if os.path.exists('user_data.pkl'):
                with open('user_data.pkl', 'rb') as f:
                    self.user_data = pickle.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
            self.user_data = {}
    
    def normalize_city_name(self, city_name: str) -> str:
        city_name = city_name.lower().strip()
        
        corrections = {
            "йошкар дыра": "йошкар-ола",
            "йошкардыра": "йошкар-ола", 
            "йошкар": "йошкар-ола",
            "спб": "санкт-петербург",
            "питер": "санкт-петербург",
            "нск": "новосибирск",
            "екб": "екатеринбург",
            "нн": "нижний новгород",
            "рнр": "ростов-на-дону",
            "владик": "владивосток"
        }
        
        for wrong, correct in corrections.items():
            if wrong == city_name:
                return correct
        
        return city_name
    
    async def geocode_city(self, city_name: str) -> Optional[Tuple[float, float, int, str]]:
        try:
            city_name_normalized = self.normalize_city_name(city_name)
            
            async with aiohttp.ClientSession() as session:
                url = f"https://nominatim.openstreetmap.org/search"
                params = {
                    'q': city_name_normalized,
                    'format': 'json',
                    'limit': 1,
                    'accept-language': 'ru'
                }
                headers = {'User-Agent': 'WeatherBot/1.0'}
                
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data:
                            lat = float(data[0]['lat'])
                            lon = float(data[0]['lon'])
                            
                            tz_url = f"https://api.open-meteo.com/v1/forecast"
                            tz_params = {
                                'latitude': lat,
                                'longitude': lon,
                                'timezone': 'auto'
                            }
                            
                            async with session.get(tz_url, params=tz_params) as tz_response:
                                if tz_response.status == 200:
                                    tz_data = await tz_response.json()
                                    timezone_str = tz_data.get('timezone', 'Europe/Moscow')
                                    
                                    tz = pytz.timezone(timezone_str)
                                    now = datetime.now(tz)
                                    utc_offset = now.utcoffset()
                                    
                                    if utc_offset:
                                        offset_hours = utc_offset.total_seconds() / 3600
                                    else:
                                        offset_hours = 3
                                    
                                    return lat, lon, int(offset_hours), timezone_str
            
            return None
        except Exception as e:
            logger.error(f"Ошибка геокодирования {city_name}: {e}")
            return None
    
    def local_time_to_utc(self, local_time_str: str, timezone_offset: int) -> str:
        try:
            hour, minute = map(int, local_time_str.split(':'))
            
            local_dt = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            utc_dt = local_dt - timedelta(hours=timezone_offset)
            
            return utc_dt.strftime("%H:%M")
        except Exception as e:
            logger.error(f"Ошибка конвертации времени: {e}")
            return "00:00"
    
    def utc_to_local_time(self, utc_time_str: str, timezone_offset: int) -> str:
        try:
            hour, minute = map(int, utc_time_str.split(':'))
            
            utc_dt = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            local_dt = utc_dt + timedelta(hours=timezone_offset)
            
            return local_dt.strftime("%H:%M")
        except Exception as e:
            logger.error(f"Ошибка конвертации UTC->местное: {e}")
            return "00:00"
    
    async def get_weather(self, city_name: str) -> Optional[WeatherData]:
        try:
            cache_key = city_name.lower()
            if cache_key in self.weather_cache:
                data, timestamp = self.weather_cache[cache_key]
                if time.time() - timestamp < WEATHER_CACHE_DURATION:
                    return data
            
            geocode_result = await self.geocode_city(city_name)
            if not geocode_result:
                return None
            
            lat, lon, offset, tz_name = geocode_result
            
            async with aiohttp.ClientSession() as session:
                url = "https://api.open-meteo.com/v1/forecast"
                params = {
                    'latitude': lat,
                    'longitude': lon,
                    'current': 'temperature_2m,wind_speed_10m,relative_humidity_2m',
                    'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code',
                    'timezone': tz_name,
                    'forecast_days': 1
                }
                
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        weather_data = WeatherData()
                        weather_data.temperature_min = data['daily']['temperature_2m_min'][0]
                        weather_data.temperature_max = data['daily']['temperature_2m_max'][0]
                        weather_data.wind_speed = data['current']['wind_speed_10m']
                        weather_data.humidity = data['current']['relative_humidity_2m']
                        weather_data.precipitation = data['daily']['precipitation_sum'][0]
                        
                        weather_code = data['daily']['weather_code'][0]
                        weather_data.description = self._weather_code_to_description(weather_code)
                        weather_data.timestamp = time.time()
                        
                        self.weather_cache[cache_key] = (weather_data, time.time())
                        
                        return weather_data
            
            return None
        except Exception as e:
            logger.error(f"Ошибка получения погоды для {city_name}: {e}")
            return None
    
    def _weather_code_to_description(self, code: int) -> str:
        """Преобразование кода погоды в описание (с заменой 'морось' на 'легкий дождь')"""
        weather_codes = {
            0: "Ясно ☀️",
            1: "Преимущественно ясно 🌤️",
            2: "Переменная облачность ⛅",
            3: "Пасмурно ☁️",
            45: "Туман 🌫️",
            48: "Изморозь ❄️",
            51: "Легкий дождь 🌦️",  # Заменено
            53: "Умеренный дождь 🌧️",  # Заменено
            55: "Сильный дождь 🌧️💧",  # Заменено
            56: "Легкий ледяной дождь 🌧️❄️",  # Заменено
            57: "Сильный ледяной дождь 🌧️💧❄️",  # Заменено
            61: "Небольшой дождь 🌦️",
            63: "Умеренный дождь 🌧️",
            65: "Сильный дождь 🌧️💧",
            66: "Ледяной дождь 🌧️❄️",
            67: "Сильный ледяной дождь 🌧️💧❄️",
            71: "Небольшой снег 🌨️",
            73: "Умеренный снег 🌨️❄️",
            75: "Сильный снег 🌨️💨",
            77: "Снежные зерна ❄️",
            80: "Небольшой ливень 🌦️",
            81: "Умеренный ливень 🌧️",
            82: "Сильный ливень 🌧️💧",
            85: "Небольшой снегопад 🌨️",
            86: "Сильный снегопад 🌨️❄️",
            95: "Гроза ⛈️",
            96: "Гроза с небольшим градом ⛈️🌨️",
            99: "Гроза с сильным градом ⛈️💨"
        }
        return weather_codes.get(code, "Неизвестно")
    
    def format_weather_message(self, city: str, weather: WeatherData) -> str:
        """Красивое форматирование сообщения о погоде"""
        precipitation_text = "нет" if weather.precipitation < 0.1 else f"{weather.precipitation} мм"
        
        # Определяем иконку по температуре
        avg_temp = (weather.temperature_min + weather.temperature_max) / 2
        if avg_temp > 25:
            temp_icon = "🔥"
            temp_comment = "Жарко"
        elif avg_temp > 20:
            temp_icon = "☀️"
            temp_comment = "Тепло"
        elif avg_temp > 15:
            temp_icon = "😊"
            temp_comment = "Комфортно"
        elif avg_temp > 10:
            temp_icon = "⛅"
            temp_comment = "Прохладно"
        elif avg_temp > 0:
            temp_icon = "⛄"
            temp_comment = "Холодно"
        elif avg_temp > -10:
            temp_icon = "❄️"
            temp_comment = "Мороз"
        else:
            temp_icon = "🥶"
            temp_comment = "Сильный мороз"
        
        # Определяем иконку ветра
        if weather.wind_speed < 5:
            wind_icon = "🍃"
            wind_comment = "Слабый"
        elif weather.wind_speed < 10:
            wind_icon = "💨"
            wind_comment = "Умеренный"
        elif weather.wind_speed < 15:
            wind_icon = "🌬️"
            wind_comment = "Сильный"
        else:
            wind_icon = "💨💨"
            wind_comment = "Очень сильный"
        
        # Определяем иконку влажности
        if weather.humidity < 40:
            humidity_icon = "🏜️"
            humidity_comment = "Сухо"
        elif weather.humidity < 70:
            humidity_icon = "💧"
            humidity_comment = "Нормально"
        else:
            humidity_icon = "💦"
            humidity_comment = "Влажно"
        
        message = (
            f"🌤️ <b>ПОГОДА В {city.upper()}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            
            f"🌡️ <b>ТЕМПЕРАТУРА:</b>\n"
            f"{temp_icon} <b>{weather.temperature_min:.0f}°C ... {weather.temperature_max:.0f}°C</b>\n"
            f"<i>{temp_comment}</i>\n\n"
            
            f"💨 <b>ВЕТЕР:</b>\n"
            f"{wind_icon} <b>{weather.wind_speed:.1f} м/с</b>\n"
            f"<i>{wind_comment}</i>\n\n"
            
            f"💧 <b>ОСАДКИ:</b>\n"
            f"🌧️ <b>{precipitation_text}</b>\n\n"
            
            f"💦 <b>ВЛАЖНОСТЬ:</b>\n"
            f"{humidity_icon} <b>{weather.humidity}%</b>\n"
            f"<i>{humidity_comment}</i>\n\n"
            
            f"📝 <b>ОПИСАНИЕ:</b>\n"
            f"{weather.description}\n\n"
            
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>🕐 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
        )
        
        return message
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает главное меню"""
        reply_markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
        
        if update.message:
            await update.message.reply_text(
                "📱 <b>ГЛАВНОЕ МЕНЮ</b>\n"
                "Выберите действие:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        elif update.callback_query:
            await update.callback_query.message.reply_text(
                "📱 <b>ГЛАВНОЕ МЕНЮ</b>\n"
                "Выберите действие:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data:
            self.user_data[user_id] = UserSettings()
            self.save_data()
        
        reply_markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
        
        welcome_text = (
            "🌟 <b>ДОБРО ПОЖАЛОВАТЬ В WEATHER BOT!</b> 🌟\n\n"
            "🌍 <b>Узнайте погоду в любом городе СНГ</b>\n"
            "⏰ <b>Настраивайте ежедневные уведомления</b>\n"
            "📱 <b>Простой и удобный интерфейс</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✨ <b>ВОЗМОЖНОСТИ:</b>\n"
            "• Прогноз на сегодня\n"
            "• 100+ городов СНГ\n"
            "• Автоматические уведомления\n"
            "• Учет часовых поясов\n\n"
            "Используйте меню ниже 👇"
        )
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def show_weather_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data or not self.user_data[user_id].city:
            reply_markup = ReplyKeyboardMarkup(
                [["📍 Выбрать город"], ["🔙 Главное меню"]],
                resize_keyboard=True
            )
            
            await update.message.reply_text(
                "❌ <b>Сначала выберите город</b>\n\n"
                "Нажмите кнопку ниже, чтобы выбрать город:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return
        
        city = self.user_data[user_id].city
        
        # Показываем сообщение о загрузке
        loading_msg = await update.message.reply_text(
            f"⏳ <b>Загружаем погоду для {city}...</b>",
            parse_mode=ParseMode.HTML
        )
        
        weather = await self.get_weather(city)
        
        if not weather:
            await loading_msg.delete()
            reply_markup = ReplyKeyboardMarkup(
                [["📍 Выбрать другой город"], ["🔙 Главное меню"]],
                resize_keyboard=True
            )
            
            await update.message.reply_text(
                f"❌ <b>Не удалось получить погоду для {city}</b>\n\n"
                "Попробуйте выбрать другой город:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return
        
        await loading_msg.delete()
        
        message = self.format_weather_message(city, weather)
        
        reply_markup = ReplyKeyboardMarkup(
            [
                ["🔄 Обновить", "📍 Сменить город"],
                ["⏰ Уведомления", "🔙 Главное меню"]
            ],
            resize_keyboard=True
        )
        
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def select_city_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню выбора города"""
        reply_markup = ReplyKeyboardMarkup(CITY_BUTTONS, resize_keyboard=True)
        
        await update.message.reply_text(
            "📍 <b>ВЫБОР ГОРОДА</b>\n\n"
            "Выберите город из списка или найдите свой:\n\n"
            "✨ <b>Популярные города:</b>\n"
            "• Москва\n"
            "• Санкт-Петербург\n"
            "• Новосибирск\n"
            "• Екатеринбург\n"
            "• Киев\n"
            "• Минск\n\n"
            "Или нажмите '🔍 Поиск города'",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def search_city(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Поиск города по названию"""
        reply_markup = ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True)
        
        await update.message.reply_text(
            "🔍 <b>ПОИСК ГОРОДА</b>\n\n"
            "Введите название города:\n\n"
            "<b>Примеры:</b>\n"
            "• Москва\n"
            "• Санкт-Петербург\n"
            "• Минск\n"
            "• Киев\n\n"
            "✨ <b>Особенности:</b>\n"
            "• Работает с любым городом мира\n"
            "• Распознает 'Йошкар дыра' как Йошкар-Ола\n"
            "• Автоматически определяет часовой пояс\n\n"
            "<i>Просто введите название и отправьте сообщение</i>",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        
        context.user_data['waiting_for_city'] = True
    
    async def handle_city_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка введенного города"""
        if 'waiting_for_city' not in context.user_data:
            # Если город выбран из меню
            text = update.message.text
            if text in MAIN_CITIES or text == "🔙 Главное меню":
                if text == "🔙 Главное меню":
                    await self.show_main_menu(update, context)
                    return
                
                await self.set_city(update, context, text)
                return
            
            # Проверяем другие команды
            if text in ["🔍 Поиск города", "📋 Все города", "🔙 Назад"]:
                if text == "🔍 Поиск города":
                    await self.search_city(update, context)
                elif text == "📋 Все города":
                    await self.show_city_list(update, context)
                elif text == "🔙 Назад":
                    await self.select_city_menu(update, context)
                return
            
            return
        
        # Обработка введенного города при поиске
        city_name = update.message.text.strip()
        user_id = update.effective_user.id
        
        if not city_name:
            await update.message.reply_text(
                "❌ <b>Введите название города</b>",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Показываем сообщение о поиске
        searching_msg = await update.message.reply_text(
            f"🔍 <b>Ищем город '{city_name}'...</b>",
            parse_mode=ParseMode.HTML
        )
        
        city_normalized = self.normalize_city_name(city_name)
        found_city = None
        
        # Сначала проверяем в списке городов
        for city in MAIN_CITIES:
            if self.normalize_city_name(city) == city_normalized:
                found_city = city
                break
        
        if not found_city:
            # Пробуем геокодировать
            geocode_result = await self.geocode_city(city_name)
            if geocode_result:
                lat, lon, offset, tz_name = geocode_result
                found_city = city_name
            else:
                await searching_msg.delete()
                reply_markup = ReplyKeyboardMarkup(
                    [["🔍 Попробовать снова"], ["📍 Выбрать из списка"], ["🔙 Главное меню"]],
                    resize_keyboard=True
                )
                
                await update.message.reply_text(
                    f"❌ <b>Город '{city_name}' не найден</b>\n\n"
                    "Возможные причины:\n"
                    "• Опечатка в названии\n"
                    "• Город слишком маленький\n"
                    "• Проблемы с подключением\n\n"
                    "Попробуйте:\n"
                    "• Проверить написание\n"
                    "• Выбрать из списка городов\n"
                    "• Попробовать снова",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
                
                context.user_data.pop('waiting_for_city', None)
                return
        
        await searching_msg.delete()
        
        # Сохраняем город
        if user_id not in self.user_data:
            self.user_data[user_id] = UserSettings()
        
        self.user_data[user_id].city = found_city
        
        # Получаем часовой пояс
        geocode_result = await self.geocode_city(found_city)
        if geocode_result:
            lat, lon, offset, tz_name = geocode_result
            self.user_data[user_id].timezone_offset = offset
        
        self.save_data()
        
        reply_markup = ReplyKeyboardMarkup(
            [
                ["🌤️ Показать погоду"],
                ["⏰ Настроить уведомления"],
                ["🔙 Главное меню"]
            ],
            resize_keyboard=True
        )
        
        await update.message.reply_text(
            f"✅ <b>ГОРОД УСПЕШНО УСТАНОВЛЕН!</b>\n\n"
            f"📍 <b>{found_city}</b>\n"
            f"🌍 <b>Часовой пояс:</b> UTC{offset:+d}\n\n"
            f"✨ <b>Что дальше?</b>\n"
            f"• Узнать текущую погоду\n"
            f"• Настроить ежедневные уведомления\n"
            f"• Выбрать другой город",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        
        context.user_data.pop('waiting_for_city', None)
    
    async def set_city(self, update: Update, context: ContextTypes.DEFAULT_TYPE, city_name: str):
        """Установка выбранного города"""
        user_id = update.effective_user.id
        
        if user_id not in self.user_data:
            self.user_data[user_id] = UserSettings()
        
        self.user_data[user_id].city = city_name
        
        # Получаем часовой пояс
        geocode_result = await self.geocode_city(city_name)
        if geocode_result:
            lat, lon, offset, tz_name = geocode_result
            self.user_data[user_id].timezone_offset = offset
        else:
            offset = 3  # По умолчанию
        
        self.save_data()
        
        reply_markup = ReplyKeyboardMarkup(
            [
                ["🌤️ Показать погоду"],
                ["⏰ Настроить уведомления"],
                ["🔙 Главное меню"]
            ],
            resize_keyboard=True
        )
        
        await update.message.reply_text(
            f"✅ <b>ГОРОД УСПЕШНО УСТАНОВЛЕН!</b>\n\n"
            f"📍 <b>{city_name}</b>\n"
            f"🌍 <b>Часовой пояс:</b> UTC{offset:+d}\n\n"
            f"✨ <b>Что дальше?</b>\n"
            f"• Узнать текущую погоду\n"
            f"• Настроить ежедневные уведомления",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def notifications_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню настройки уведомлений"""
        user_id = update.effective_user.id
        
        if user_id not in self.user_data or not self.user_data[user_id].city:
            reply_markup = ReplyKeyboardMarkup(
                [["📍 Сначала выберите город"], ["🔙 Главное меню"]],
                resize_keyboard=True
            )
            
            await update.message.reply_text(
                "⏰ <b>НАСТРОЙКА УВЕДОМЛЕНИЙ</b>\n\n"
                "❌ <b>Сначала выберите город</b>\n\n"
                "Уведомления привязаны к конкретному городу.\n"
                "Выберите город, чтобы настроить время получения прогноза.",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return
        
        user_settings = self.user_data[user_id]
        city = user_settings.city
        
        status_icon = "✅" if user_settings.notifications_enabled else "❌"
        status_text = "ВКЛЮЧЕНЫ" if user_settings.notifications_enabled else "ВЫКЛЮЧЕНЫ"
        
        time_display = user_settings.notification_time_local if user_settings.notification_time_local else "Не установлено"
        
        message = (
            f"⏰ <b>НАСТРОЙКА УВЕДОМЛЕНИЙ</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 <b>Город:</b> {city}\n"
            f"🌍 <b>Часовой пояс:</b> UTC{user_settings.timezone_offset:+d}\n\n"
            f"🔔 <b>Статус:</b> {status_icon} {status_text}\n"
            f"🕐 <b>Время уведомлений:</b> {time_display} (местное)\n\n"
            f"✨ <b>Как это работает:</b>\n"
            f"• Выбираете время (например, 08:00)\n"
            f"• Бот автоматически конвертирует в UTC\n"
            f"• Каждый день в это время получаете прогноз\n\n"
            f"<b>Выберите время уведомления:</b>"
        )
        
        reply_markup = ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True)
        
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def handle_notifications(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка действий в меню уведомлений"""
        text = update.message.text
        user_id = update.effective_user.id
        
        if text == "🔙 Назад":
            await self.show_main_menu(update, context)
            return
        
        if text.startswith("🕗") or text.startswith("🕖") or text.startswith("🕘") or text.startswith("🕙") or text.startswith("🕕"):
            # Извлекаем время из текста (например: "🕗 08:00" -> "08:00")
            time_str = text.split()[1]
            await self.set_notification_time(update, context, time_str)
            return
    
    async def set_notification_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE, time_str: str):
        """Установка времени уведомлений"""
        user_id = update.effective_user.id
        
        if user_id not in self.user_data:
            return
        
        user_settings = self.user_data[user_id]
        user_settings.notification_time_local = time_str
        
        # Конвертируем местное время в UTC
        if user_settings.timezone_offset != 0:
            utc_time = self.local_time_to_utc(time_str, user_settings.timezone_offset)
            user_settings.notification_time_utc = utc_time
            logger.info(f"Пользователь {user_id}: местное время {time_str} -> UTC {utc_time} (смещение: {user_settings.timezone_offset})")
        
        # Автоматически включаем уведомления
        user_settings.notifications_enabled = True
        
        self.save_data()
        
        reply_markup = ReplyKeyboardMarkup(
            [["✅ Продолжить настройку"], ["🔙 Главное меню"]],
            resize_keyboard=True
        )
        
        await update.message.reply_text(
            f"✅ <b>ВРЕМЯ УВЕДОМЛЕНИЙ УСТАНОВЛЕНО!</b>\n\n"
            f"🕐 <b>{time_str}</b> (местное время)\n"
            f"🌍 <b>UTC:</b> {user_settings.notification_time_utc}\n"
            f"📍 <b>Город:</b> {user_settings.city}\n\n"
            f"✨ <b>Уведомления активированы!</b>\n"
            f"Каждый день в {time_str} вы будете получать\n"
            f"прогноз погоды для {user_settings.city}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def show_city_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список всех городов"""
        # Разбиваем города на страницы
        cities_per_page = 15
        total_pages = (len(MAIN_CITIES) + cities_per_page - 1) // cities_per_page
        
        # Сохраняем текущую страницу в контексте
        if 'city_page' not in context.user_data:
            context.user_data['city_page'] = 0
        
        page = context.user_data['city_page']
        start_idx = page * cities_per_page
        end_idx = start_idx + cities_per_page
        cities_page = MAIN_CITIES[start_idx:end_idx]
        
        message = "📋 <b>СПИСОК ВСЕХ ГОРОДОВ</b>\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, city in enumerate(cities_page, start=start_idx + 1):
            message += f"• {city}\n"
        
        message += f"\n📄 <b>Страница {page + 1} из {total_pages}</b>\n"
        message += f"📍 <b>Всего городов:</b> {len(MAIN_CITIES)}"
        
        # Создаем клавиатуру для навигации
        keyboard = []
        
        # Добавляем города (по 3 в строке)
        city_buttons = []
        for i in range(0, len(cities_page), 3):
            row = []
            for j in range(3):
                if i + j < len(cities_page):
                    row.append(cities_page[i + j])
            city_buttons.append(row)
        
        keyboard.extend(city_buttons)
        
        # Кнопки навигации
        nav_buttons = []
        if page > 0:
            nav_buttons.append("◀️ Назад")
        
        nav_buttons.append(f"{page + 1}/{total_pages}")
        
        if page < total_pages - 1:
            nav_buttons.append("Вперед ▶️")
        
        keyboard.append(nav_buttons)
        keyboard.append(["🔙 Главное меню"])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def handle_city_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка действий в списке городов"""
        text = update.message.text
        
        if text == "🔙 Главное меню":
            await self.show_main_menu(update, context)
            context.user_data.pop('city_page', None)
            return
        
        elif text == "◀️ Назад":
            if 'city_page' in context.user_data:
                context.user_data['city_page'] = max(0, context.user_data['city_page'] - 1)
            await self.show_city_list(update, context)
            return
        
        elif text == "Вперед ▶️":
            if 'city_page' in context.user_data:
                total_pages = (len(MAIN_CITIES) + 15 - 1) // 15
                context.user_data['city_page'] = min(total_pages - 1, context.user_data['city_page'] + 1)
            await self.show_city_list(update, context)
            return
        
        elif text in MAIN_CITIES:
            await self.set_city(update, context, text)
            context.user_data.pop('city_page', None)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда помощи"""
        reply_markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
        
        help_text = (
            "❓ <b>ПОМОЩЬ ПО ИСПОЛЬЗОВАНИЮ БОТА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            "✨ <b>ОСНОВНЫЕ ВОЗМОЖНОСТИ:</b>\n"
            "• Прогноз погоды на сегодня\n"
            "• Ежедневные уведомления\n"
            "• Поддержка 100+ городов СНГ\n"
            "• Автоматический учет часовых поясов\n\n"
            
            "📍 <b>КАК ВЫБРАТЬ ГОРОД:</b>\n"
            "1. Нажмите '📍 Выбрать город'\n"
            "2. Выберите из списка или найдите\n"
            "3. Или введите название вручную\n\n"
            
            "⏰ <b>КАК НАСТРОИТЬ УВЕДОМЛЕНИЯ:</b>\n"
            "1. Выберите город\n"
            "2. Нажмите '⏰ Уведомления'\n"
            "3. Выберите удобное время\n"
            "4. Бот будет присылать прогноз каждый день\n\n"
            
            "🔍 <b>ПОИСК ГОРОДА:</b>\n"
            "• Работает с любым городом мира\n"
            "• Распознает 'Йошкар дыра' как Йошкар-Ола\n"
            "• Автоматически определяет часовой пояс\n\n"
            
            "📞 <b>ПОДДЕРЖКА:</b>\n"
            "Если возникли проблемы:\n"
            "• Перезапустите бота командой /start\n"
            "• Выберите другой город\n"
            "• Проверьте подключение к интернету\n\n"
            
            "💡 <b>СОВЕТ:</b> Используйте кнопки меню\n"
            "для удобной навигации!"
        )
        
        await update.message.reply_text(
            help_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def handle_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка действий в главном меню"""
        text = update.message.text
        
        if text == "🔄 Обновить":
            await self.show_weather_now(update, context)
        
        elif text == "❓ Помощь":
            await self.help_command(update, context)
        
        elif text in ["🌤️ Погода сейчас", "📍 Выбрать город", "🔍 Поиск города", 
                     "⏰ Уведомления", "📋 Список городов"]:
            if text == "🌤️ Погода сейчас":
                await self.show_weather_now(update, context)
            elif text == "📍 Выбрать город":
                await self.select_city_menu(update, context)
            elif text == "🔍 Поиск города":
                await self.search_city(update, context)
            elif text == "⏰ Уведомления":
                await self.notifications_menu(update, context)
            elif text == "📋 Список городов":
                await self.show_city_list(update, context)
    
    async def check_notifications(self, context: ContextTypes.DEFAULT_TYPE):
        """Проверка и отправка уведомлений"""
        try:
            current_utc = datetime.utcnow()
            current_time_str = current_utc.strftime("%H:%M")
            
            logger.info(f"Проверка уведомлений. Текущее UTC время: {current_time_str}")
            
            for user_id, settings in list(self.user_data.items()):
                try:
                    if (settings.notifications_enabled and 
                        settings.city and 
                        settings.notification_time_utc):
                        
                        if current_time_str == settings.notification_time_utc:
                            logger.info(f"Отправка уведомления пользователю {user_id} для города {settings.city}")
                            
                            weather = await self.get_weather(settings.city)
                            
                            if weather:
                                message = self.format_weather_message(settings.city, weather)
                                
                                # Добавляем заголовок уведомления
                                notification_text = (
                                    "⏰ <b>ЕЖЕДНЕВНЫЙ ПРОГНОЗ ПОГОДЫ</b>\n"
                                    f"📍 <b>Город:</b> {settings.city}\n"
                                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                                ) + message.split("━━━━━━━━━━━━━━━━━━━━", 1)[1]
                                
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text=notification_text,
                                    parse_mode=ParseMode.HTML
                                )
                                
                                logger.info(f"Уведомление отправлено пользователю {user_id}")
                                
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка в check_notifications: {e}")
    
    async def wakeup_service(self, context: ContextTypes.DEFAULT_TYPE):
        """Сервис пробуждения для Render.com"""
        try:
            if self.wakeup_url:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.wakeup_url) as response:
                        if response.status == 200:
                            logger.info("✅ Сервис пробуждения активирован")
                        else:
                            logger.warning(f"⚠️ Ошибка пробуждения: {response.status}")
            else:
                logger.info("📱 Бот работает...")
        except Exception as e:
            logger.error(f"❌ Ошибка сервиса пробуждения: {e}")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"❌ Ошибка: {context.error}")
        
        if update and update.effective_message:
            try:
                reply_markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
                await update.effective_message.reply_text(
                    "❌ <b>Произошла ошибка</b>\n\n"
                    "Пожалуйста, попробуйте снова или перезапустите бота командой /start",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
    
    async def run(self):
        """Запуск бота"""
        persistence = PicklePersistence(filepath='bot_persistence')
        
        application = Application.builder()\
            .token(self.token)\
            .persistence(persistence)\
            .build()
        
        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("weather", self.show_weather_now))
        
        # Обработчик текстовых сообщений
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_message
        ))
        
        # Сначала проверяем, ожидаем ли мы ввод города
        async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            text = update.message.text
            
            # Проверяем состояние ожидания города
            if 'waiting_for_city' in context.user_data:
                await self.handle_city_input(update, context)
                return
            
            # Проверяем меню уведомлений
            if text in [btn for row in TIME_BUTTONS for btn in row] or text in ["✅ Продолжить настройку"]:
                await self.handle_notifications(update, context)
                return
            
            # Проверяем список городов
            if text in ["◀️ Назад", "Вперед ▶️"] or text in MAIN_CITIES:
                await self.handle_city_list(update, context)
                return
            
            # Проверяем главное меню
            if text in [btn for row in MAIN_MENU for btn in row]:
                await self.handle_main_menu(update, context)
                return
            
            # Проверяем меню выбора города
            if text in [btn for row in CITY_BUTTONS for btn in row]:
                await self.handle_city_input(update, context)
                return
            
            # Если ни одно условие не подошло, пробуем как город
            await self.handle_city_input(update, context)
        
        # Добавляем основной обработчик сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
        
        application.add_error_handler(self.error_handler)
        
        # Настройка фоновых задач
        job_queue = application.job_queue
        
        if job_queue:
            # Проверка уведомлений каждую минуту
            job_queue.run_repeating(
                self.check_notifications, 
                interval=CHECK_NOTIFICATIONS_INTERVAL, 
                first=10
            )
            
            # Сервис пробуждения каждые 10 минут
            job_queue.run_repeating(
                self.wakeup_service,
                interval=WAKEUP_INTERVAL,
                first=5
            )
        
        # Запуск бота
        await application.initialize()
        await application.start()
        
        try:
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
        except Conflict:
            logger.warning("⚠️ Обнаружен конфликт. Перезапускаем бота...")
            await asyncio.sleep(5)
            await application.updater.stop()
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
        
        logger.info("✅ Бот успешно запущен и работает")
        
        # Бесконечный цикл
        await asyncio.Future()

def main():
    """Основная функция"""
    # Получение токена из переменных окружения
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    RENDER_WAKEUP_URL = os.getenv('RENDER_WAKEUP_URL')
    
    if not BOT_TOKEN:
        logger.error("❌ Токен бота не найден! Установите переменную окружения BOT_TOKEN")
        return
    
    # Создание и запуск бота
    bot = WeatherBot(token=BOT_TOKEN, wakeup_url=RENDER_WAKEUP_URL)
    
    # Запуск с обработкой ошибок
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
