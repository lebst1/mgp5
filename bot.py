import asyncio
import logging
import sys
import json
import aiosqlite
from datetime import datetime
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
    Update
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from aiogram.client.default import DefaultBotProperties

from config import config
from database import db

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=None)
)
dp = Dispatcher()

# ==================== ПРИНУДИТЕЛЬНОЕ ЛОГИРОВАНИЕ ====================

@dp.update()
async def log_all_updates(update: Update):
    """ЛОГИРОВАНИЕ ВСЕХ ВХОДЯЩИХ ОБНОВЛЕНИЙ"""
    logger.info("=" * 80)
    logger.info(f"📥 ПОЛУЧЕНО ОБНОВЛЕНИЕ ТИПА: {type(update).__name__}")
    
    # Проверяем наличие бизнес-сообщения
    if update.message and hasattr(update.message, 'business_connection_id'):
        logger.info(f"📩 БИЗНЕС-СООБЩЕНИЕ: id={update.message.message_id}, chat={update.message.chat.id}")
        logger.info(f"   text={update.message.text}")
        # Сохраняем сообщение
        await save_business_message(update.message)
        return True
    
    # Проверяем удаленные сообщения
    if update.business_messages_deleted:
        deleted: BusinessMessagesDeleted = update.business_messages_deleted
        logger.info(f"🗑️ УДАЛЕННЫЕ СООБЩЕНИЯ: {deleted.message_ids} в чате {deleted.chat.id}")
        await handle_business_messages_deleted(deleted)
        return True
    
    # Проверяем бизнес-подключение
    if update.business_connection:
        logger.info(f"🔗 BUSINESS CONNECTION: {update.business_connection}")
    
    logger.info(f"📥 ПОЛНЫЙ ОБЪЕКТ: {update}")
    logger.info("=" * 80)
    return True

# ==================== ФУНКЦИЯ СОХРАНЕНИЯ СООБЩЕНИЙ ====================

async def save_business_message(message: Message):
    """Сохранение бизнес-сообщения"""
    try:
        logger.info(f"💾 СОХРАНЯЮ СООБЩЕНИЕ: id={message.message_id}, chat={message.chat.id}")
        
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            logger.warning(f"⚠️ Нет user_id в сообщении {message.message_id}")
            return
        
        if message.from_user and message.from_user.is_bot:
            logger.info(f"🤖 Пропущено сообщение от бота {user_id}")
            return
        
        # Проверяем пользователя
        user = await db.get_user(user_id)
        if not user:
            logger.warning(f"⚠️ Пользователь {user_id} не найден, регистрируем...")
            await db.register_user(user_id, "unknown", "unknown", "unknown")
        
        # Получаем медиа
        media_type = None
        media_data = None
        if message.media:
            try:
                if message.photo:
                    media_type = 'photo'
                    media_data = json.dumps({'file_id': message.photo[-1].file_id})
                elif message.document:
                    media_type = 'document'
                    media_data = json.dumps({'file_name': message.document.file_name})
            except Exception as e:
                logger.error(f"Ошибка обработки медиа: {e}")
        
        # Формируем данные
        message_data = {
            'message_id': message.message_id,
            'chat_id': message.chat.id,
            'chat_title': message.chat.title or f"Chat {message.chat.id}",
            'chat_type': message.chat.type,
            'sender_id': message.from_user.id if message.from_user else None,
            'sender_name': f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() if message.from_user else '',
            'text': message.text or message.caption or '',
            'media_type': media_type,
            'media_data': media_data,
            'date': int(message.date.timestamp())
        }
        
        connection_id = getattr(message, 'business_connection_id', None)
        
        # СОХРАНЯЕМ В БД
        result = await db.save_message(user_id, message_data, connection_id)
        if result:
            logger.info(f"✅ СОХРАНЕНО сообщение {message.message_id} от {user_id}")
            logger.info(f"   Текст: {message.text[:100] if message.text else 'Нет текста'}")
        else:
            logger.error(f"❌ Ошибка сохранения сообщения {message.message_id}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка в save_business_message: {e}", exc_info=True)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

async def safe_send_message(chat_id: int, text: str, reply_markup=None):
    """Безопасная отправка сообщения"""
    try:
        msg = await bot.send_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
        logger.info(f"✅ Отправлено сообщение в {chat_id}")
        return msg
    except TelegramRetryAfter as e:
        logger.warning(f"Flood wait: {e.retry_after} seconds")
        await asyncio.sleep(e.retry_after)
        return await bot.send_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
    except TelegramAPIError as e:
        if "can't send messages to the bot" in str(e):
            logger.warning(f"Нельзя отправлять сообщения ботам: {chat_id}")
            return None
        logger.error(f"Ошибка отправки: {e}")
        return None

async def show_main_menu(chat_id: int, message: Message = None):
    """Показать главное меню"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")]
    ])
    
    text = (
        f"🤖 MGP5 Business Bot\n\n"
        f"Бот сохраняет все сообщения из бизнес-аккаунта!\n\n"
        f"📌 Что умеет:\n"
        f"✅ Сохранять сообщения\n"
        f"✏️ Отслеживать изменения\n"
        f"🗑️ Сохранять удаленные\n\n"
        f"🔥 Требуется Telegram Premium\n\n"
        f"Нажмите на кнопку ниже:"
    )
    
    if message:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode=None)
            return
        except:
            pass
    
    return await safe_send_message(chat_id, text, reply_markup=kb)

# ==================== ОБРАБОТЧИК УДАЛЕННЫХ СООБЩЕНИЙ ====================

async def handle_business_messages_deleted(deleted: BusinessMessagesDeleted):
    """Обработчик удаленных сообщений"""
    try:
        user_id = deleted.chat.id
        msg_ids = deleted.message_ids
        
        logger.info(f"🗑️ ОБРАБОТКА УДАЛЕННЫХ СООБЩЕНИЙ: {msg_ids} от пользователя {user_id}")
        
        settings = await db.get_user_settings(user_id)
        if not settings or settings[0] == 0:
            logger.info(f"ℹ️ Уведомления об удалении выключены для {user_id}")
            return
        
        for msg_id in msg_ids:
            old_data = await db.get_message(user_id, msg_id, deleted.chat.id)
            
            if old_data:
                await db.mark_deleted(user_id, msg_id, deleted.chat.id)
                
                text = f"🗑️ Сообщение удалено\n\n"
                text += f"Чат: {old_data[2] or str(deleted.chat.id)}\n"
                text += f"От: {old_data[1] or 'Неизвестно'}\n"
                text += f"Текст: {old_data[0][:300]}{'...' if len(old_data[0]) > 300 else ''}"
                
                await safe_send_message(user_id, text)
                logger.info(f"✅ Уведомление об удалении отправлено для msg_id={msg_id}")
            else:
                logger.warning(f"⚠️ Сообщение {msg_id} НЕ НАЙДЕНО в БД")
                
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_business_messages_deleted: {e}", exc_info=True)

# ==================== КОСТЫЛЬ: ПРОВЕРКА УДАЛЕННЫХ ====================

async def check_deleted_messages():
    """Периодическая проверка удаленных сообщений (костыль)"""
    logger.info("🔄 Запущен фоновый процесс проверки удаленных сообщений")
    
    while True:
        try:
            async with aiosqlite.connect(db.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT id, chat_id, user_id, text, sender_name, chat_title FROM messages WHERE is_deleted = 0 AND date < ?",
                    (int(datetime.now().timestamp()) - 30,)
                )
                messages = await cursor.fetchall()
                
                if messages:
                    logger.info(f"🔍 Проверка {len(messages)} сообщений на удаление")
                
                for msg_id, chat_id, user_id, text, sender_name, chat_title in messages:
                    try:
                        await bot.get_messages(chat_id, msg_id)
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "message not found" in error_msg or "not found" in error_msg:
                            logger.info(f"🗑️ Обнаружено удаленное сообщение (костыль): {msg_id}")
                            await db.mark_deleted(user_id, msg_id, chat_id)
                            
                            settings = await db.get_user_settings(user_id)
                            if settings and settings[0] != 0:
                                notification = f"🗑️ Сообщение удалено\n\n"
                                notification += f"Чат: {chat_title or str(chat_id)}\n"
                                notification += f"От: {sender_name or 'Неизвестно'}\n"
                                notification += f"Текст: {text[:300]}{'...' if len(text) > 300 else ''}"
                                await safe_send_message(user_id, notification)
                                logger.info(f"✅ Отправлено уведомление об удалении (костыль)")
                        await asyncio.sleep(0.2)
                        
        except Exception as e:
            logger.error(f"❌ Ошибка в check_deleted_messages: {e}", exc_info=True)
        
        await asyncio.sleep(30)

# ==================== КОМАНДЫ ====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    logger.info(f"📋 Команда /start от {user_id}")
    
    if message.from_user.is_bot:
        await message.answer("❌ Боты не могут использовать этого бота.")
        return
    
    await db.register_user(user_id, message.from_user.username, 
                          message.from_user.first_name, message.from_user.last_name)
    await show_main_menu(user_id, message)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    user_id = message.from_user.id
    logger.info(f"📋 Команда /help от {user_id}")
    
    if message.from_user.is_bot:
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_start")]
    ])
    
    text = (
        f"❓ Помощь\n\n"
        f"/start - главное меню\n"
        f"/stats - статистика\n"
        f"/settings - настройки\n"
        f"/history <id> - история сообщения\n"
        f"/help - помощь\n\n"
        f"🔌 Как подключить:\n"
        f"1. Купите Telegram Premium\n"
        f"2. Настройки → Telegram Business → Боты\n"
        f"3. Добавьте бота"
    )
    await safe_send_message(user_id, text, reply_markup=kb)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    logger.info(f"📋 Команда /stats от {user_id}")
    
    if message.from_user.is_bot:
        return
    
    stats = await db.get_stats(user_id)
    connections = await db.get_active_connections_count(user_id)
    
    if not stats:
        await safe_send_message(user_id, "📊 Статистика пока пуста")
        return
    
    text = f"📊 Статистика\n\n"
    text += f"📩 Всего: {stats[0]}\n"
    text += f"🗑️ Удалено: {stats[1]}\n"
    text += f"✏️ Изменений: {stats[2]}\n"
    text += f"📎 Медиа: {stats[3]}\n"
    text += f"🔗 Чатов: {connections}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_start")]
    ])
    await safe_send_message(user_id, text, reply_markup=kb)

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    user_id = message.from_user.id
    logger.info(f"📋 Команда /settings от {user_id}")
    
    if message.from_user.is_bot:
        return
    
    settings = await db.get_user_settings(user_id)
    if not settings:
        settings = (1, 1, 1, 0, None)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'✅' if settings[0] else '❌'} Удаления",
            callback_data="toggle_deleted"
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if settings[1] else '❌'} Изменения",
            callback_data="toggle_edited"
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if settings[2] else '❌'} Медиа",
            callback_data="toggle_media"
        )],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_start")]
    ])
    await safe_send_message(user_id, "⚙️ Настройки", reply_markup=kb)

@dp.message(Command("history"))
async def cmd_history(message: Message):
    user_id = message.from_user.id
    logger.info(f"📋 Команда /history от {user_id}")
    
    if message.from_user.is_bot:
        return
    
    args = message.text.split()
    if len(args) < 2:
        await safe_send_message(user_id, "Использование: /history <id>")
        return
    
    try:
        msg_id = int(args[1])
        msg = await db.get_message(user_id, msg_id, message.chat.id)
        if not msg:
            await safe_send_message(user_id, f"❌ Сообщение {msg_id} не найдено")
            return
        
        edits = await db.get_message_edits(user_id, msg_id, message.chat.id)
        
        text = f"📜 История\n\n"
        text += f"ID: {msg_id}\n"
        text += f"Текст: {msg[0] or 'Нет текста'}\n"
        text += f"Дата: {datetime.fromtimestamp(msg[3]).strftime('%Y-%m-%d %H:%M:%S') if msg[3] else 'Неизвестно'}\n"
        
        if msg[6] == 1:
            text += f"\n❌ Удалено\n"
        
        if edits:
            text += f"\n📝 Изменения:\n"
            for old_t, new_t, edit_d in edits[:3]:
                text += f"• {datetime.fromtimestamp(edit_d).strftime('%H:%M')}\n"
                text += f"  Было: {old_t[:50]}{'...' if len(old_t) > 50 else ''}\n"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_start")]
        ])
        await safe_send_message(user_id, text, reply_markup=kb)
    except ValueError:
        await safe_send_message(user_id, "❌ ID должен быть числом")

# ==================== CALLBACKS ====================

@dp.callback_query(F.data == "menu_start")
async def callback_menu_start(callback: CallbackQuery):
    await callback.answer("📋 Главное меню")
    await show_main_menu(callback.from_user.id, callback.message)

@dp.callback_query(F.data == "menu_stats")
async def callback_menu_stats(callback: CallbackQuery):
    await callback.answer("📊 Статистика")
    class FakeMessage:
        def __init__(self, user_id, chat_id):
            self.from_user = type('obj', (object,), {'id': user_id, 'is_bot': False})()
            self.chat = type('obj', (object,), {'id': chat_id})()
            self.text = "/stats"
    await cmd_stats(FakeMessage(callback.from_user.id, callback.message.chat.id))

@dp.callback_query(F.data == "menu_settings")
async def callback_menu_settings(callback: CallbackQuery):
    await callback.answer("⚙️ Настройки")
    class FakeMessage:
        def __init__(self, user_id, chat_id):
            self.from_user = type('obj', (object,), {'id': user_id, 'is_bot': False})()
            self.chat = type('obj', (object,), {'id': chat_id})()
            self.text = "/settings"
    await cmd_settings(FakeMessage(callback.from_user.id, callback.message.chat.id))

@dp.callback_query(F.data == "menu_help")
async def callback_menu_help(callback: CallbackQuery):
    await callback.answer("❓ Помощь")
    class FakeMessage:
        def __init__(self, user_id, chat_id):
            self.from_user = type('obj', (object,), {'id': user_id, 'is_bot': False})()
            self.chat = type('obj', (object,), {'id': chat_id})()
            self.text = "/help"
    await cmd_help(FakeMessage(callback.from_user.id, callback.message.chat.id))

@dp.callback_query(F.data.startswith("toggle_"))
async def callback_toggle(callback: CallbackQuery):
    user_id = callback.from_user.id
    setting = callback.data.replace("toggle_", "")
    
    settings = await db.get_user_settings(user_id)
    if not settings:
        settings = [1, 1, 1]
    
    setting_map = {
        "deleted": (0, "notify_deleted"),
        "edited": (1, "notify_edited"),
        "media": (2, "save_media")
    }
    
    if setting in setting_map:
        index, db_field = setting_map[setting]
        new_value = 0 if settings[index] == 1 else 1
        await db.update_user_settings(user_id, **{db_field: new_value})
        await callback.answer(f"✅ {'Включено' if new_value else 'Выключено'}!")
        
        class FakeMessage:
            def __init__(self, user_id, chat_id):
                self.from_user = type('obj', (object,), {'id': user_id, 'is_bot': False})()
                self.chat = type('obj', (object,), {'id': chat_id})()
                self.text = "/settings"
        await cmd_settings(FakeMessage(user_id, callback.message.chat.id))

# ==================== ЗАПУСК ====================

async def main():
    logger.info("=" * 80)
    logger.info("🚀 ЗАПУСК MGP5 BUSINESS BOT")
    logger.info("=" * 80)
    
    await db.init_database()
    logger.info("✅ База данных инициализирована")
    
    bot_info = await bot.get_me()
    logger.info(f"✅ Бот: @{bot_info.username} (ID: {bot_info.id})")
    logger.info("=" * 80)
    logger.info("📌 Ожидаем подключений...")
    logger.info("📌 Сообщения будут сохраняться в БД")
    logger.info("=" * 80)
    
    # Запускаем фоновый процесс
    asyncio.create_task(check_deleted_messages())
    logger.info("✅ Фоновый процесс проверки удалений запущен")
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("⏹️ Бот остановлен")

if __name__ == '__main__':
    asyncio.run(main())