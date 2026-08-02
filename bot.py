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

# ==================== ПРИНУДИТЕЛЬНОЕ ЛОГИРОВАНИЕ ВСЕХ ОБНОВЛЕНИЙ ====================

@dp.update()
async def log_all_updates(update: Update):
    """ЛОГИРОВАНИЕ ВСЕХ ВХОДЯЩИХ ОБНОВЛЕНИЙ"""
    logger.info("=" * 80)
    logger.info(f"📥 ПОЛУЧЕНО ОБНОВЛЕНИЕ ТИПА: {type(update).__name__}")
    logger.info(f"📥 ПОЛНЫЙ ОБЪЕКТ: {update}")
    logger.info("=" * 80)
    
    # ==================== ГЛАВНОЕ: ОБРАБОТКА УДАЛЕННЫХ СООБЩЕНИЙ ====================
    if update.business_messages_deleted:
        deleted: BusinessMessagesDeleted = update.business_messages_deleted
        logger.info(f"🗑️ УДАЛЕННЫЕ СООБЩЕНИЯ (BUSINESS_MESSAGES_DELETED):")
        logger.info(f"   business_connection_id: {deleted.business_connection_id}")
        logger.info(f"   chat_id: {deleted.chat.id}")
        logger.info(f"   chat: {deleted.chat}")
        logger.info(f"   message_ids: {deleted.message_ids}")
        
        # Обработка удаленных сообщений
        await handle_business_messages_deleted(deleted)
        return True
    
    # Обработка обычных бизнес-сообщений
    if update.message and hasattr(update.message, 'business_connection_id'):
        # Это бизнес-сообщение, обрабатываем отдельно
        pass
    
    return True

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def get_media_data(message: Message):
    media_type = None
    media_data = None
    
    if not message.media:
        return media_type, media_data
    
    try:
        if message.photo:
            media_type = 'photo'
            media_data = json.dumps({'file_id': message.photo[-1].file_id})
        elif message.document:
            media_type = 'document'
            media_data = json.dumps({
                'file_name': message.document.file_name,
                'size': message.document.file_size
            })
        elif message.video:
            media_type = 'video'
            media_data = json.dumps({
                'duration': message.video.duration,
                'width': message.video.width,
                'height': message.video.height
            })
        elif message.audio:
            media_type = 'audio'
            media_data = json.dumps({
                'duration': message.audio.duration,
                'title': message.audio.title
            })
        elif message.voice:
            media_type = 'voice'
            media_data = json.dumps({'duration': message.voice.duration})
        elif message.sticker:
            media_type = 'sticker'
            media_data = json.dumps({'emoji': message.sticker.emoji})
    except Exception as e:
        logger.error(f"Ошибка обработки медиа: {e}")
    
    return media_type, media_data

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

# ==================== ОБРАБОТЧИК УДАЛЕННЫХ СООБЩЕНИЙ (BUSINESS_MESSAGES_DELETED) ====================

async def handle_business_messages_deleted(deleted: BusinessMessagesDeleted):
    """
    ОСНОВНОЙ ОБРАБОТЧИК УДАЛЕННЫХ СООБЩЕНИЙ
    Это событие приходит, когда пользователь удаляет сообщение в личном чате
    """
    try:
        user_id = deleted.chat.id
        msg_ids = deleted.message_ids
        
        logger.info(f"🗑️ ОБРАБОТКА УДАЛЕННЫХ СООБЩЕНИЙ: {msg_ids} от пользователя {user_id}")
        
        # Получаем настройки пользователя
        settings = await db.get_user_settings(user_id)
        if not settings or settings[0] == 0:
            logger.info(f"ℹ️ Уведомления об удалении выключены для {user_id}")
            return
        
        # Обрабатываем каждое удаленное сообщение
        for msg_id in msg_ids:
            logger.info(f"   ➜ Обработка msg_id: {msg_id}")
            
            # Ищем сообщение в БД
            old_data = await db.get_message(user_id, msg_id, deleted.chat.id)
            
            if not old_data:
                logger.warning(f"⚠️ Сообщение {msg_id} не найдено в БД для user_id={user_id}")
                # Пробуем найти без user_id
                try:
                    async with aiosqlite.connect(db.db_path) as conn:
                        cursor = await conn.execute(
                            "SELECT text, sender_name, chat_title FROM messages WHERE id = ? AND chat_id = ?",
                            (msg_id, deleted.chat.id)
                        )
                        old_data = await cursor.fetchone()
                        if old_data:
                            logger.info(f"✅ Найдено сообщение {msg_id} в БД (без user_id)")
                except Exception as e:
                    logger.error(f"Ошибка поиска в БД: {e}")
            
            if old_data:
                # Отмечаем как удаленное в БД
                await db.mark_deleted(user_id, msg_id, deleted.chat.id)
                
                # Формируем уведомление
                text = f"🗑️ Сообщение удалено\n\n"
                text += f"Чат: {old_data[2] or str(deleted.chat.id)}\n"
                text += f"От: {old_data[1] or 'Неизвестно'}\n"
                text += f"Текст: {old_data[0][:300]}{'...' if len(old_data[0]) > 300 else ''}"
                
                logger.info(f"📤 Отправка уведомления об удалении для {user_id}")
                await safe_send_message(user_id, text)
                logger.info(f"✅ Уведомление об удалении отправлено для msg_id={msg_id}")
            else:
                logger.warning(f"⚠️ Сообщение {msg_id} НЕ НАЙДЕНО в БД")
                # Отправляем уведомление, что сообщение было удалено, но не сохранено
                await safe_send_message(
                    user_id,
                    f"🗑️ Сообщение было удалено, но не сохранено (ID: {msg_id})\n"
                    f"Возможно, оно было отправлено слишком быстро или это сообщение от бота."
                )
                logger.info(f"✅ Отправлено уведомление о неудачном сохранении для msg_id={msg_id}")
                
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_business_messages_deleted: {e}", exc_info=True)

# ==================== ОБРАБОТЧИКИ BUSINESS API ====================

@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    try:
        user_id = connection.user.id
        logger.info(f"🔗 Business подключение от {user_id}")
        logger.info(f"   user: {connection.user}")
        logger.info(f"   can_reply: {connection.can_reply}")
        
        if connection.user.is_bot:
            logger.warning(f"⚠️ Бот пытается подключиться: {user_id}")
            return
        
        await db.register_user(
            user_id,
            connection.user.username,
            connection.user.first_name,
            connection.user.last_name,
            is_premium=True
        )
        
        await db.save_connection(
            connection.id,
            user_id,
            f"{connection.user.first_name or ''} {connection.user.last_name or ''}".strip(),
            connection.can_reply
        )
        
        await show_main_menu(user_id)
        logger.info(f"✅ Бизнес-подключение завершено для {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка business_connection: {e}", exc_info=True)

@dp.message(F.business_connection_id)
async def handle_business_message(message: Message):
    try:
        logger.info(f"📩 Business message: id={message.message_id}, chat={message.chat.id}")
        logger.info(f"   type={type(message).__name__}")
        logger.info(f"   is_inaccessible={isinstance(message, InaccessibleMessage)}")
        
        # Проверяем, является ли сообщение недоступным (удаленным)
        # В некоторых случаях удаленные сообщения приходят как InaccessibleMessage
        if isinstance(message, InaccessibleMessage):
            logger.info(f"🗑️ ОБНАРУЖЕНО УДАЛЕННОЕ СООБЩЕНИЕ (InaccessibleMessage): {message.message_id}")
            # Это уже обрабатывается через BusinessMessagesDeleted, но на всякий случай
            return
        
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            logger.warning(f"⚠️ Нет user_id в сообщении {message.message_id}")
            return
        
        if message.from_user and message.from_user.is_bot:
            logger.info(f"🤖 Пропущено сообщение от бота {user_id}")
            return
        
        user = await db.get_user(user_id)
        if not user or user[5] == 0:
            logger.warning(f"⚠️ Пользователь {user_id} не найден или неактивен")
            return
        
        media_type, media_data = get_media_data(message)
        
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
        await db.save_message(user_id, message_data, connection_id)
        logger.info(f"✅ СОХРАНЕНО сообщение {message.message_id} от {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка business_message: {e}", exc_info=True)

@dp.edited_message(F.business_connection_id)
async def handle_edited_business_message(message: Message):
    try:
        logger.info(f"✏️ Edited message: id={message.message_id}")
        
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return
        
        if message.from_user and message.from_user.is_bot:
            return
        
        settings = await db.get_user_settings(user_id)
        if not settings or settings[1] == 0:
            logger.info(f"ℹ️ Уведомления об изменениях выключены для {user_id}")
            return
        
        old_data = await db.get_message(user_id, message.message_id, message.chat.id)
        if old_data:
            old_text = old_data[0] or ''
            new_text = message.text or message.caption or ''
            
            await db.save_edit(user_id, message.message_id, message.chat.id, old_text, new_text)
            
            text = f"✏️ Сообщение изменено\n\n"
            text += f"Было: {old_text[:200]}{'...' if len(old_text) > 200 else ''}\n\n"
            text += f"Стало: {new_text[:200]}{'...' if len(new_text) > 200 else ''}"
            
            await safe_send_message(user_id, text)
            logger.info(f"✅ Отправлено уведомление об изменении для {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка edited_message: {e}", exc_info=True)

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
    
    await safe_send_message(user_id, "⚙️ Настройки\n\nВыберите что хотите настроить:", reply_markup=kb)

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
    logger.info(f"📋 Callback menu_start от {callback.from_user.id}")
    await callback.answer("📋 Главное меню")
    await show_main_menu(callback.from_user.id, callback.message)

@dp.callback_query(F.data == "menu_stats")
async def callback_menu_stats(callback: CallbackQuery):
    logger.info(f"📋 Callback menu_stats от {callback.from_user.id}")
    await callback.answer("📊 Статистика")
    class FakeMessage:
        def __init__(self, user_id, chat_id):
            self.from_user = type('obj', (object,), {'id': user_id, 'is_bot': False})()
            self.chat = type('obj', (object,), {'id': chat_id})()
            self.text = "/stats"
    fake_msg = FakeMessage(callback.from_user.id, callback.message.chat.id)
    await cmd_stats(fake_msg)

@dp.callback_query(F.data == "menu_settings")
async def callback_menu_settings(callback: CallbackQuery):
    logger.info(f"📋 Callback menu_settings от {callback.from_user.id}")
    await callback.answer("⚙️ Настройки")
    class FakeMessage:
        def __init__(self, user_id, chat_id):
            self.from_user = type('obj', (object,), {'id': user_id, 'is_bot': False})()
            self.chat = type('obj', (object,), {'id': chat_id})()
            self.text = "/settings"
    fake_msg = FakeMessage(callback.from_user.id, callback.message.chat.id)
    await cmd_settings(fake_msg)

@dp.callback_query(F.data == "menu_help")
async def callback_menu_help(callback: CallbackQuery):
    logger.info(f"📋 Callback menu_help от {callback.from_user.id}")
    await callback.answer("❓ Помощь")
    class FakeMessage:
        def __init__(self, user_id, chat_id):
            self.from_user = type('obj', (object,), {'id': user_id, 'is_bot': False})()
            self.chat = type('obj', (object,), {'id': chat_id})()
            self.text = "/help"
    fake_msg = FakeMessage(callback.from_user.id, callback.message.chat.id)
    await cmd_help(fake_msg)

@dp.callback_query(F.data.startswith("toggle_"))
async def callback_toggle(callback: CallbackQuery):
    user_id = callback.from_user.id
    setting = callback.data.replace("toggle_", "")
    
    logger.info(f"📋 Callback toggle_{setting} от {user_id}")
    
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
        fake_msg = FakeMessage(user_id, callback.message.chat.id)
        await cmd_settings(fake_msg)

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
    logger.info("📌 Удаленные сообщения будут приходить как BusinessMessagesDeleted")
    logger.info("=" * 80)
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("⏹️ Бот остановлен")

if __name__ == '__main__':
    asyncio.run(main())