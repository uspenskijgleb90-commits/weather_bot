import asyncio
import pickle
import logging
import time
import os
import aiohttp
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, PicklePersistence, MessageHandler, filters
)
from telegram.constants import ParseMode

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WEATHER_CACHE_DURATION = 1800
CHECK_NOTIFICATIONS_INTERVAL = 60
WAKEUP_INTERVAL = 600

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
        weather_codes = {
            0: "Ясно ☀️",
            1: "Преимущественно ясно 🌤",
            2: "Переменная облачность ⛅",
            3: "Пасмурно ☁️",
            45: "Туман 🌫",
            48: "Изморозь ❄️",
            51: "Морось 🌧",
            53: "Умеренная морось 🌧",
            55: "Сильная морось 🌧",
            56: "Ледяная морось 🌧❄️",
            57: "Сильная ледяная морось 🌧❄️",
            61: "Небольшой дождь 🌦",
            63: "Умеренный дождь 🌧",
            65: "Сильный дождь 🌧💧",
            66: "Ледяной дождь 🌧❄️",
            67: "Сильный ледяной дождь 🌧💧❄️",
            71: "Небольшой снег 🌨",
            73: "Умеренный снег 🌨❄️",
            75: "Сильный снег 🌨💨",
            77: "Снежные зерна ❄️",
            80: "Небольшой ливень 🌦",
            81: "Умеренный ливень 🌧",
            82: "Сильный ливень 🌧💧",
            85: "Небольшой снегопад 🌨",
            86: "Сильный снегопад 🌨❄️",
            95: "Гроза ⛈",
            96: "Гроза с небольшим градом ⛈🌨",
            99: "Гроза с сильным градом ⛈💨"
        }
        return weather_codes.get(code, "Неизвестно")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data:
            self.user_data[user_id] = UserSettings()
            self.save_data()
        
        keyboard = [
            [InlineKeyboardButton("🌤 Погода сейчас", callback_data="weather_now")],
            [InlineKeyboardButton("📍 Выбрать город", callback_data="select_city")],
            [InlineKeyboardButton("🔍 Поиск города", callback_data="search_city")],
            [InlineKeyboardButton("⏰ Уведомления", callback_data="notifications")],
            [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Добро пожаловать в Weather Bot!\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        if data == "weather_now":
            await self.show_weather_now(update, context)
        elif data == "select_city":
            await self.select_city_menu(update, context)
        elif data == "search_city":
            await self.search_city(update, context)
        elif data == "notifications":
            await self.notifications_menu(update, context)
        elif data == "city_list":
            await self.show_city_list(update, context)
        elif data.startswith("city_"):
            city_name = data[5:]
            await self.set_city(update, context, city_name)
        elif data.startswith("time_"):
            time_str = data[5:]
            await self.set_notification_time(update, context, time_str)
        elif data == "toggle_notifications":
            await self.toggle_notifications(update, context)
        elif data == "back_to_menu":
            await self.show_main_menu(update, context)
        elif data.startswith("page_"):
            page_num = int(data[5:])
            await self.show_city_list_page(update, context, page_num)
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🌤 Погода сейчас", callback_data="weather_now")],
            [InlineKeyboardButton("📍 Выбрать город", callback_data="select_city")],
            [InlineKeyboardButton("🔍 Поиск города", callback_data="search_city")],
            [InlineKeyboardButton("⏰ Уведомления", callback_data="notifications")],
            [InlineKeyboardButton("📋 Список городов", callback_data="city_list")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                "Главное меню:",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                "Главное меню:",
                reply_markup=reply_markup
            )
    
    async def show_weather_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data or not self.user_data[user_id].city:
            keyboard = [[InlineKeyboardButton("📍 Выбрать город", callback_data="select_city")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                "Сначала выберите город:",
                reply_markup=reply_markup
            )
            return
        
        city = self.user_data[user_id].city
        weather = await self.get_weather(city)
        
        if not weather:
            await update.callback_query.edit_message_text(
                f"Не удалось получить погоду для {city}. Попробуйте другой город.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📍 Выбрать другой город", callback_data="select_city")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
                ])
            )
            return
        
        precipitation_text = "нет" if weather.precipitation < 0.1 else f"{weather.precipitation} мм"
        
        message = (
            f"🌤 Погода в {city} на сегодня\n"
            f"{'─' * 30}\n"
            f"🌡 Температура: {weather.temperature_min:.0f}°C ... {weather.temperature_max:.0f}°C\n"
            f"💧 Осадки: {precipitation_text}\n"
            f"💨 Ветер: {weather.wind_speed:.1f} м/с\n"
            f"💦 Влажность: {weather.humidity}%\n"
            f"📝 {weather.description}\n\n"
            f"🕐 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data="weather_now")],
            [InlineKeyboardButton("📍 Сменить город", callback_data="select_city")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def select_city_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = []
        
        popular_cities = MAIN_CITIES[:12]
        for i in range(0, len(popular_cities), 2):
            row = []
            if i < len(popular_cities):
                row.append(InlineKeyboardButton(popular_cities[i], callback_data=f"city_{popular_cities[i]}"))
            if i + 1 < len(popular_cities):
                row.append(InlineKeyboardButton(popular_cities[i+1], callback_data=f"city_{popular_cities[i+1]}"))
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔍 Поиск города", callback_data="search_city")])
        keyboard.append([InlineKeyboardButton("📋 Все города", callback_data="city_list")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "Выберите город из списка или найдите свой:",
            reply_markup=reply_markup
        )
    
    async def search_city(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text(
            "Введите название города:\n\n"
            "Пример: Москва, Санкт-Петербург, Минск\n"
            "Или даже: Йошкар дыра (распознается как Йошкар-Ола)"
        )
        
        context.user_data['waiting_for_city'] = True
    
    async def handle_city_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if 'waiting_for_city' not in context.user_data:
            return
        
        city_name = update.message.text.strip()
        user_id = update.effective_user.id
        
        city_normalized = self.normalize_city_name(city_name)
        found_city = None
        
        for city in MAIN_CITIES:
            if self.normalize_city_name(city) == city_normalized:
                found_city = city
                break
        
        if not found_city:
            geocode_result = await self.geocode_city(city_name)
            if geocode_result:
                lat, lon, offset, tz_name = geocode_result
                found_city = city_name
            else:
                keyboard = [
                    [InlineKeyboardButton("🔍 Попробовать снова", callback_data="search_city")],
                    [InlineKeyboardButton("📋 Выбрать из списка", callback_data="select_city")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    f"Город '{city_name}' не найден. Попробуйте другой вариант.",
                    reply_markup=reply_markup
                )
                return
        
        if user_id not in self.user_data:
            self.user_data[user_id] = UserSettings()
        
        self.user_data[user_id].city = found_city
        
        geocode_result = await self.geocode_city(found_city)
        if geocode_result:
            lat, lon, offset, tz_name = geocode_result
            self.user_data[user_id].timezone_offset = offset
        
        self.save_data()
        
        keyboard = [
            [InlineKeyboardButton("🌤 Показать погоду", callback_data="weather_now")],
            [InlineKeyboardButton("⏰ Настроить уведомления", callback_data="notifications")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Город установлен: {found_city}\n"
            f"Часовой пояс: UTC{offset:+d}",
            reply_markup=reply_markup
        )
        
        context.user_data.pop('waiting_for_city', None)
    
    async def set_city(self, update: Update, context: ContextTypes.DEFAULT_TYPE, city_name: str):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data:
            self.user_data[user_id] = UserSettings()
        
        self.user_data[user_id].city = city_name
        
        geocode_result = await self.geocode_city(city_name)
        if geocode_result:
            lat, lon, offset, tz_name = geocode_result
            self.user_data[user_id].timezone_offset = offset
        
        self.save_data()
        
        keyboard = [
            [InlineKeyboardButton("🌤 Показать погоду", callback_data="weather_now")],
            [InlineKeyboardButton("⏰ Настроить уведомления", callback_data="notifications")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            f"✅ Город установлен: {city_name}\n"
            f"Часовой пояс: UTC{offset:+d}",
            reply_markup=reply_markup
        )
    
    async def notifications_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data or not self.user_data[user_id].city:
            keyboard = [
                [InlineKeyboardButton("📍 Сначала выберите город", callback_data="select_city")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                "Сначала выберите город для настройки уведомлений:",
                reply_markup=reply_markup
            )
            return
        
        user_settings = self.user_data[user_id]
        city = user_settings.city
        status = "✅ Включены" if user_settings.notifications_enabled else "❌ Выключены"
        time_display = user_settings.notification_time_local if user_settings.notification_time_local else "Не установлено"
        
        message = (
            f"⏰ Настройки уведомлений\n"
            f"{'─' * 30}\n"
            f"📍 Город: {city}\n"
            f"🔔 Статус: {status}\n"
            f"🕐 Время: {time_display} (местное)\n"
            f"🌍 Часовой пояс: UTC{user_settings.timezone_offset:+d}\n\n"
            f"Выберите время уведомления:"
        )
        
        keyboard = []
        
        for i in range(0, len(NOTIFICATION_TIMES), 2):
            row = []
            if i < len(NOTIFICATION_TIMES):
                row.append(InlineKeyboardButton(NOTIFICATION_TIMES[i], callback_data=f"time_{NOTIFICATION_TIMES[i]}"))
            if i + 1 < len(NOTIFICATION_TIMES):
                row.append(InlineKeyboardButton(NOTIFICATION_TIMES[i+1], callback_data=f"time_{NOTIFICATION_TIMES[i+1]}"))
            keyboard.append(row)
        
        toggle_text = "❌ Выключить" if user_settings.notifications_enabled else "✅ Включить"
        keyboard.append([InlineKeyboardButton(toggle_text, callback_data="toggle_notifications")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            message,
            reply_markup=reply_markup
        )
    
    async def set_notification_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE, time_str: str):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data:
            return
        
        user_settings = self.user_data[user_id]
        user_settings.notification_time_local = time_str
        
        if user_settings.timezone_offset != 0:
            utc_time = self.local_time_to_utc(time_str, user_settings.timezone_offset)
            user_settings.notification_time_utc = utc_time
            logger.info(f"Пользователь {user_id}: местное время {time_str} -> UTC {utc_time} (смещение: {user_settings.timezone_offset})")
        
        user_settings.notifications_enabled = True
        
        self.save_data()
        
        await self.notifications_menu(update, context)
    
    async def toggle_notifications(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in self.user_data:
            return
        
        user_settings = self.user_data[user_id]
        user_settings.notifications_enabled = not user_settings.notifications_enabled
        
        self.save_data()
        
        await self.notifications_menu(update, context)
    
    async def show_city_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_city_list_page(update, context, 0)
    
    async def show_city_list_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
        cities_per_page = 20
        start_idx = page * cities_per_page
        end_idx = start_idx + cities_per_page
        
        cities_page = MAIN_CITIES[start_idx:end_idx]
        
        message = "📋 Список доступных городов:\n\n"
        
        for i, city in enumerate(cities_page, start=start_idx + 1):
            message += f"{i}. {city}\n"
        
        message += f"\nСтраница {page + 1}/{(len(MAIN_CITIES) + cities_per_page - 1) // cities_per_page}"
        
        keyboard = []
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"page_{page-1}"))
        
        if end_idx < len(MAIN_CITIES):
            nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"page_{page+1}"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        for i in range(0, len(cities_page), 2):
            row = []
            if i < len(cities_page):
                row.append(InlineKeyboardButton(cities_page[i], callback_data=f"city_{cities_page[i]}"))
            if i + 1 < len(cities_page):
                row.append(InlineKeyboardButton(cities_page[i+1], callback_data=f"city_{cities_page[i+1]}"))
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔍 Поиск города", callback_data="search_city")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            message,
            reply_markup=reply_markup
        )
    
    async def check_notifications(self, context: ContextTypes.DEFAULT_TYPE):
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
                                precipitation_text = "нет" if weather.precipitation < 0.1 else f"{weather.precipitation} мм"
                                
                                message = (
                                    f"⏰ Ежедневный прогноз погоды для {settings.city}\n"
                                    f"{'─' * 30}\n"
                                    f"🌡 Температура: {weather.temperature_min:.0f}°C ... {weather.temperature_max:.0f}°C\n"
                                    f"💧 Осадки: {precipitation_text}\n"
                                    f"💨 Ветер: {weather.wind_speed:.1f} м/с\n"
                                    f"💦 Влажность: {weather.humidity}%\n"
                                    f"📝 {weather.description}\n\n"
                                    f"Хорошего дня!"
                                )
                                
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text=message,
                                    parse_mode=ParseMode.HTML
                                )
                                
                                logger.info(f"Уведомление отправлено пользователю {user_id}")
                                
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка в check_notifications: {e}")
    
    async def wakeup_service(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            if self.wakeup_url:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.wakeup_url) as response:
                        if response.status == 200:
                            logger.info("Сервис пробуждения активирован")
                        else:
                            logger.warning(f"Ошибка пробуждения: {response.status}")
            else:
                logger.info("Проверка работы бота...")
        except Exception as e:
            logger.error(f"Ошибка сервиса пробуждения: {e}")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {context.error}")
        
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Произошла ошибка. Пожалуйста, попробуйте снова."
                )
            except:
                pass

def main():
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    RENDER_WAKEUP_URL = os.getenv('RENDER_WAKEUP_URL')
    
    if not BOT_TOKEN:
        logger.error("Токен бота не найден! Установите переменную окружения BOT_TOKEN")
        return
    
    bot = WeatherBot(token=BOT_TOKEN, wakeup_url=RENDER_WAKEUP_URL)
    
    async def run_bot():
        persistence = PicklePersistence(filepath='bot_persistence')
        
        application = Application.builder()\
            .token(BOT_TOKEN)\
            .persistence(persistence)\
            .build()
        
        application.add_handler(CommandHandler("start", bot.start))
        application.add_handler(CommandHandler("weather", bot.show_weather_now))
        application.add_handler(CallbackQueryHandler(bot.button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_city_input))
        
        application.add_error_handler(bot.error_handler)
        
        job_queue = application.job_queue
        
        if job_queue:
            job_queue.run_repeating(
                bot.check_notifications, 
                interval=CHECK_NOTIFICATIONS_INTERVAL, 
                first=10
            )
            
            job_queue.run_repeating(
                bot.wakeup_service,
                interval=WAKEUP_INTERVAL,
                first=5
            )
        
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        logger.info("Бот запущен и работает")
        
        await asyncio.Future()
    
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
