import asyncio
import logging
import sys
import json
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    BusinessConnection,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
    Chat,
    User
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from aiogram.client.default import DefaultBotProperties

from config import config
from database import db

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Инициализация бота - БЕЗ HTML
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=None)
)
dp = Dispatcher()

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

async def safe_send_message(chat_id: int, text: str, **kwargs):
    """Безопасная отправка сообщения с проверкой"""
    try:
        # Проверяем, что чат - не бот (через get_chat_member)
        try:
            # Пробуем получить информацию о пользователе
            chat = await bot.get_chat(chat_id)
            
            # Проверяем тип чата
            if chat.type == 'private':
                # Для приватных чатов пытаемся получить информацию о пользователе
                try:
                    # Используем другой метод для проверки
                    member = await bot.get_chat_member(chat_id, chat_id)
                    if member.user and member.user.is_bot:
                        logger.warning(f"Попытка отправить сообщение боту {chat_id} - пропускаем")
                        return None
                except Exception as e:
                    # Если не можем получить информацию, пробуем отправить
                    pass
        except Exception as e:
            logger.warning(f"Не удалось проверить чат {chat_id}: {e}")
        
        return await bot.send_message(chat_id, text, parse_mode=None, **kwargs)
    except TelegramRetryAfter as e:
        logger.warning(f"Flood wait: {e.retry_after} seconds")
        await asyncio.sleep(e.retry_after)
        return await bot.send_message(chat_id, text, parse_mode=None, **kwargs)
    except TelegramAPIError as e:
        if "can't send messages to the bot" in str(e):
            logger.warning(f"Нельзя отправлять сообщения ботам: {chat_id}")
            return None
        logger.error(f"Ошибка отправки: {e}")
        return None

def is_bot_user(user_id: int) -> bool:
    """Простая проверка - боты обычно имеют username заканчивающийся на 'bot'"""
    # Это не надежно, но работает как fallback
    return False

# ==================== ОБРАБОТЧИКИ ====================

@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    try:
        user_id = connection.user.id
        
        # Проверяем, что пользователь - не бот
        if connection.user.is_bot:
            logger.warning(f"Бот пытается подключиться: {user_id}")
            return
        
        logger.info(f"Business подключение от {user_id}")
        
        await db.register_user(
            user_id,
            connection.user.username,
            connection.user.first_name,
            connection.user.last_name,
            is_premium=True
        )
        
        await db.save_connection(
            connection.connection_id,
            user_id,
            f"{connection.user.first_name or ''} {connection.user.last_name or ''}".strip(),
            connection.can_reply
        )
        
        settings = await db.get_user_settings(user_id)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
        ])
        
        await safe_send_message(
            user_id,
            f"🤖 Бот подключен к бизнес-аккаунту!\n\n"
            f"✅ Сохраняю все сообщения\n"
            f"✏️ Отслеживаю изменения\n"
            f"🗑️ Сохраняю удаленные\n\n"
            f"📌 Настройки:\n"
            f"• Удаления: {'✅' if settings and settings[0] else '❌'}\n"
            f"• Изменения: {'✅' if settings and settings[1] else '❌'}\n"
            f"• Медиа: {'✅' if settings and settings[2] else '❌'}",
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"Ошибка business_connection: {e}")

@dp.message(F.business_connection_id)
async def handle_business_message(message: Message):
    try:
        if isinstance(message, InaccessibleMessage):
            await handle_business_message_deleted(message)
            return
        
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return
        
        # Пропускаем сообщения от ботов
        if message.from_user and message.from_user.is_bot:
            logger.info(f"Пропущено сообщение от бота {user_id}")
            return
        
        user = await db.get_user(user_id)
        if not user or user[5] == 0:
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
        logger.info(f"Сохранено сообщение {message.message_id}")
    except Exception as e:
        logger.error(f"Ошибка business_message: {e}")

@dp.edited_message(F.business_connection_id)
async def handle_edited_business_message(message: Message):
    try:
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return
        
        # Пропускаем сообщения от ботов
        if message.from_user and message.from_user.is_bot:
            return
        
        settings = await db.get_user_settings(user_id)
        if not settings or settings[1] == 0:
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
    except Exception as e:
        logger.error(f"Ошибка edited_message: {e}")

async def handle_business_message_deleted(message: InaccessibleMessage):
    try:
        user_id = message.chat.id
        
        # Проверяем, что пользователь - не бот через простой способ
        # Пропускаем если ID похож на бота (обычно боты имеют ID начинающийся с 5)
        if str(user_id).startswith('5') and len(str(user_id)) >= 10:
            logger.info(f"Пропущено удаление от бота {user_id}")
            return
        
        settings = await db.get_user_settings(user_id)
        if not settings or settings[0] == 0:
            return
        
        old_data = await db.get_message(user_id, message.message_id, message.chat.id)
        if old_data and old_data[0]:
            await db.mark_deleted(user_id, message.message_id, message.chat.id)
            
            text = f"🗑️ Сообщение удалено\n\n"
            text += f"Чат: {old_data[2] or str(message.chat.id)}\n"
            text += f"От: {old_data[1] or 'Неизвестно'}\n"
            text += f"Текст: {old_data[0][:300]}{'...' if len(old_data[0]) > 300 else ''}"
            
            await safe_send_message(user_id, text)
    except Exception as e:
        logger.error(f"Ошибка deleted_message: {e}")

# ==================== КОМАНДЫ ====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, что пользователь - не бот
    if message.from_user.is_bot:
        await message.answer("❌ Боты не могут использовать этого бота.")
        return
    
    await db.register_user(user_id, message.from_user.username, 
                          message.from_user.first_name, message.from_user.last_name)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ])
    
    await safe_send_message(
        user_id,
        f"🤖 MGP5 Business Bot\n\n"
        f"Бот сохраняет все сообщения из бизнес-аккаунта!\n\n"
        f"📌 Что умеет:\n"
        f"✅ Сохранять сообщения\n"
        f"✏️ Отслеживать изменения\n"
        f"🗑️ Сохранять удаленные\n\n"
        f"🔥 Требуется Telegram Premium",
        reply_markup=kb
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    # Проверяем, что пользователь - не бот
    if message.from_user.is_bot:
        return
    
    await safe_send_message(
        message.from_user.id,
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

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, что пользователь - не бот
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
    
    await safe_send_message(user_id, text)

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, что пользователь - не бот
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
        )]
    ])
    
    await safe_send_message(user_id, "⚙️ Настройки", reply_markup=kb)

@dp.message(Command("history"))
async def cmd_history(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, что пользователь - не бот
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
        
        await safe_send_message(user_id, text)
    except ValueError:
        await safe_send_message(user_id, "❌ ID должен быть числом")

# ==================== CALLBACKS ====================

@dp.callback_query()
async def handle_callbacks(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    # Проверяем, что пользователь - не бот
    if callback.from_user.is_bot:
        await callback.answer("❌ Боты не могут использовать этого бота.")
        return
    
    if callback.data == "stats":
        await cmd_stats(callback.message)
    elif callback.data == "settings":
        await cmd_settings(callback.message)
    elif callback.data == "help":
        await cmd_help(callback.message)
    elif callback.data.startswith("toggle_"):
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
            await callback.answer("✅ Обновлено!")
            await cmd_settings(callback.message)
    
    await callback.answer()

# ==================== ЗАПУСК ====================

async def main():
    logger.info("🚀 Запуск MGP5 Business Bot...")
    
    await db.init_database()
    
    bot_info = await bot.get_me()
    logger.info(f"Бот: @{bot_info.username}")
    logger.info("Ожидаем подключений...")
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == '__main__':
    asyncio.run(main())