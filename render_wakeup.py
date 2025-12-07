#!/usr/bin/env python3
"""
🔄 Render Wakeup Service
⏰ Отдельный сервис для постоянного пробуждения Render.com
🚀 Работает независимо от основного бота
"""

import os
import asyncio
import aiohttp
import logging
from datetime import datetime
import time
import sys

# ============= КОНФИГУРАЦИЯ =============
class Config:
    RENDER_WAKEUP_URL = os.getenv("RENDER_WAKEUP_URL", "")
    
    # ⚙️ Настройки пробуждения
    WAKEUP_INTERVAL = 300  # 5 минут (300 секунд)
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    TIMEOUT = 30  # секунд

# ============= ЛОГГИРОВАНИЕ =============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('render_wakeup.log')
    ]
)
logger = logging.getLogger(__name__)

# ============= ОСНОВНЫЕ ФУНКЦИИ =============
async def wakeup_render_once():
    """🔄 Однократное пробуждение Render.com"""
    if not Config.RENDER_WAKEUP_URL:
        logger.warning("⚠️ RENDER_WAKEUP_URL не установлен")
        return False
    
    for attempt in range(Config.MAX_RETRIES):
        try:
            logger.info(f"🔄 Попытка {attempt + 1}/{Config.MAX_RETRIES} пробуждения Render...")
            
            start_time = time.time()
            
            async with aiohttp.ClientSession() as session:
                timeout = aiohttp.ClientTimeout(total=Config.TIMEOUT)
                
                async with session.get(
                    Config.RENDER_WAKEUP_URL, 
                    timeout=timeout
                ) as response:
                    
                    elapsed = time.time() - start_time
                    
                    if response.status in [200, 201, 202, 204]:
                        logger.info(f"✅ Render пробужден за {elapsed:.2f} сек, статус: {response.status}")
                        return True
                    else:
                        logger.warning(f"⚠️ Render ответил статусом {response.status} за {elapsed:.2f} сек")
                        
        except aiohttp.ClientError as e:
            logger.error(f"❌ Ошибка сети: {e}")
        except asyncio.TimeoutError:
            logger.error(f"⏰ Таймаут ({Config.TIMEOUT} сек) при пробуждении Render")
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка: {e}")
        
        # Ждем перед повторной попыткой (кроме последней)
        if attempt < Config.MAX_RETRIES - 1:
            logger.info(f"⏳ Ожидание {Config.RETRY_DELAY} сек перед повторной попыткой...")
            await asyncio.sleep(Config.RETRY_DELAY)
    
    logger.error("❌ Не удалось пробудить Render после всех попыток")
    return False

async def wakeup_render_continuous():
    """♾️ Непрерывное пробуждение Render.com"""
    logger.info("🚀 Запуск службы пробуждения Render")
    logger.info(f"⏰ Интервал пробуждения: {Config.WAKEUP_INTERVAL} сек")
    logger.info(f"🔄 URL для пробуждения: {Config.RENDER_WAKEUP_URL[:30]}..." if Config.RENDER_WAKEUP_URL else "❌ URL не установлен")
    
    wakeup_count = 0
    success_count = 0
    
    try:
        while True:
            wakeup_count += 1
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            logger.info(f"\n{'='*50}")
            logger.info(f"🔄 Пробуждение #{wakeup_count} в {current_time}")
            logger.info(f"{'='*50}")
            
            success = await wakeup_render_once()
            
            if success:
                success_count += 1
                success_rate = (success_count / wakeup_count) * 100
                logger.info(f"📊 Статистика: {success_count}/{wakeup_count} успешных ({success_rate:.1f}%)")
            
            # Ждем перед следующим пробуждением
            logger.info(f"⏳ Следующее пробуждение через {Config.WAKEUP_INTERVAL} сек...")
            
            try:
                # Разбиваем ожидание на интервалы для возможности прерывания
                for _ in range(Config.WAKEUP_INTERVAL // 10):
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                logger.info("👋 Получен сигнал прерывания")
                break
            
    except KeyboardInterrupt:
        logger.info("👋 Остановка по запросу пользователя")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в wakeup_loop: {e}")
    
    finally:
        logger.info(f"\n{'='*50}")
        logger.info("🛑 Служба пробуждения остановлена")
        logger.info(f"📊 Итоговая статистика: {success_count}/{wakeup_count} успешных")
        logger.info(f"{'='*50}")

async def health_check():
    """🏥 Проверка здоровья сервиса"""
    while True:
        try:
            logger.debug("🏥 Сервис работает нормально")
            await asyncio.sleep(60)  # Проверка каждую минуту
        except Exception as e:
            logger.error(f"❌ Ошибка в health check: {e}")

def main():
    """🚀 Главная функция"""
    if not Config.RENDER_WAKEUP_URL:
        logger.error("❌ RENDER_WAKEUP_URL не установлен!")
        logger.info("📝 Установите переменную окружения RENDER_WAKEUP_URL")
        logger.info("📝 Пример: https://your-bot-name.onrender.com")
        return
    
    logger.info("🚀 Запуск службы пробуждения Render.com")
    logger.info(f"🔄 URL: {Config.RENDER_WAKEUP_URL}")
    logger.info(f"⏰ Интервал: {Config.WAKEUP_INTERVAL} сек")
    logger.info(f"🔄 Максимум попыток: {Config.MAX_RETRIES}")
    
    try:
        # Создаем event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Создаем задачи
        tasks = [
            loop.create_task(wakeup_render_continuous()),
            loop.create_task(health_check())
        ]
        
        # Запускаем все задачи
        loop.run_until_complete(asyncio.gather(*tasks))
        
    except KeyboardInterrupt:
        logger.info("\n👋 Остановка по Ctrl+C")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}")
    finally:
        # Аккуратно закрываем loop
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.close()
        except:
            pass
        
        logger.info("🛑 Служба полностью остановлена")

if __name__ == "__main__":
    # Добавляем обработчик для Ctrl+C
    import signal
    
    def signal_handler(sig, frame):
        logger.info("\n⚠️ Получен сигнал остановки")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    main()
