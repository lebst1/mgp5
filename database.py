import aiosqlite
import json
import logging
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from config import config

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
    
    async def init_database(self):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        is_premium INTEGER DEFAULT 0,
                        is_active INTEGER DEFAULT 1,
                        connected_at INTEGER,
                        last_activity INTEGER,
                        language_code TEXT DEFAULT 'ru'
                    )
                ''')
                
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER,
                        chat_id INTEGER,
                        user_id INTEGER,
                        chat_title TEXT,
                        chat_type TEXT,
                        sender_id INTEGER,
                        sender_name TEXT,
                        text TEXT,
                        media_type TEXT,
                        media_data TEXT,
                        date INTEGER,
                        edit_date INTEGER,
                        delete_date INTEGER,
                        is_deleted INTEGER DEFAULT 0,
                        business_connection_id TEXT,
                        PRIMARY KEY (id, chat_id, user_id)
                    )
                ''')
                
                await conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_messages_user_chat 
                    ON messages(user_id, chat_id)
                ''')
                
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS edits (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id INTEGER,
                        chat_id INTEGER,
                        user_id INTEGER,
                        old_text TEXT,
                        new_text TEXT,
                        edit_date INTEGER
                    )
                ''')
                
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS connections (
                        connection_id TEXT PRIMARY KEY,
                        user_id INTEGER,
                        user_name TEXT,
                        is_enabled INTEGER DEFAULT 1,
                        can_reply INTEGER DEFAULT 0,
                        created_at INTEGER,
                        last_active INTEGER
                    )
                ''')
                
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_settings (
                        user_id INTEGER PRIMARY KEY,
                        notify_deleted INTEGER DEFAULT 1,
                        notify_edited INTEGER DEFAULT 1,
                        save_media INTEGER DEFAULT 1,
                        auto_forward INTEGER DEFAULT 0,
                        notify_chat_id INTEGER
                    )
                ''')
                
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS stats (
                        user_id INTEGER,
                        total_messages INTEGER DEFAULT 0,
                        deleted_messages INTEGER DEFAULT 0,
                        edited_messages INTEGER DEFAULT 0,
                        media_messages INTEGER DEFAULT 0,
                        last_updated INTEGER,
                        PRIMARY KEY (user_id)
                    )
                ''')
                
                await conn.commit()
                logger.info("База данных инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
            raise
    
    async def register_user(self, user_id: int, username: str = None, first_name: str = None, 
                           last_name: str = None, is_premium: bool = False, language_code: str = 'ru'):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                    INSERT OR REPLACE INTO users 
                    (user_id, username, first_name, last_name, is_premium, is_active, connected_at, last_activity, language_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, username or '', first_name or '', last_name or '', 
                      1 if is_premium else 0, 1, int(datetime.now().timestamp()), 
                      int(datetime.now().timestamp()), language_code))
                
                await conn.execute('''
                    INSERT OR IGNORE INTO user_settings 
                    (user_id, notify_deleted, notify_edited, save_media, auto_forward)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, config.DEFAULT_NOTIFY_DELETED, config.DEFAULT_NOTIFY_EDITED,
                      config.DEFAULT_SAVE_MEDIA, config.DEFAULT_AUTO_FORWARD))
                
                await conn.execute('''
                    INSERT OR IGNORE INTO stats 
                    (user_id, total_messages, deleted_messages, edited_messages, media_messages, last_updated)
                    VALUES (?, 0, 0, 0, 0, ?)
                ''', (user_id, int(datetime.now().timestamp())))
                
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка регистрации пользователя: {e}")
            return False
    
    async def get_user(self, user_id: int):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT * FROM users WHERE user_id = ?
                ''', (user_id,))
                return await cursor.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения пользователя: {e}")
            return None
    
    async def save_message(self, user_id: int, message_data: Dict[str, Any], connection_id: str = None):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                    INSERT OR REPLACE INTO messages 
                    (id, chat_id, user_id, chat_title, chat_type, sender_id, sender_name, 
                     text, media_type, media_data, date, edit_date, delete_date, is_deleted, business_connection_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    message_data['message_id'],
                    message_data['chat_id'],
                    user_id,
                    message_data.get('chat_title', ''),
                    message_data.get('chat_type', 'private'),
                    message_data.get('sender_id'),
                    message_data.get('sender_name', ''),
                    message_data.get('text', ''),
                    message_data.get('media_type'),
                    message_data.get('media_data'),
                    message_data.get('date'),
                    None, None, 0, connection_id
                ))
                
                await conn.execute('''
                    UPDATE stats SET total_messages = total_messages + 1, last_updated = ?
                    WHERE user_id = ?
                ''', (int(datetime.now().timestamp()), user_id))
                
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка сохранения сообщения: {e}")
            return False
    
    async def save_edit(self, user_id: int, message_id: int, chat_id: int, old_text: str, new_text: str):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                    INSERT INTO edits (message_id, chat_id, user_id, old_text, new_text, edit_date)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (message_id, chat_id, user_id, old_text or '', new_text or '', int(datetime.now().timestamp())))
                
                await conn.execute('''
                    UPDATE messages SET text = ?, edit_date = ? 
                    WHERE id = ? AND chat_id = ? AND user_id = ?
                ''', (new_text or '', int(datetime.now().timestamp()), message_id, chat_id, user_id))
                
                await conn.execute('''
                    UPDATE stats SET edited_messages = edited_messages + 1, last_updated = ?
                    WHERE user_id = ?
                ''', (int(datetime.now().timestamp()), user_id))
                
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка сохранения редактирования: {e}")
            return False
    
    async def mark_deleted(self, user_id: int, message_id: int, chat_id: int):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                    UPDATE messages SET delete_date = ?, is_deleted = 1 
                    WHERE id = ? AND chat_id = ? AND user_id = ?
                ''', (int(datetime.now().timestamp()), message_id, chat_id, user_id))
                
                await conn.execute('''
                    UPDATE stats SET deleted_messages = deleted_messages + 1, last_updated = ?
                    WHERE user_id = ?
                ''', (int(datetime.now().timestamp()), user_id))
                
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка отметки удаления: {e}")
            return False
    
    async def get_message(self, user_id: int, message_id: int, chat_id: int):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT text, sender_name, chat_title, date, edit_date, delete_date, is_deleted
                    FROM messages WHERE id = ? AND chat_id = ? AND user_id = ?
                ''', (message_id, chat_id, user_id))
                return await cursor.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения сообщения: {e}")
            return None
    
    async def get_message_edits(self, user_id: int, message_id: int, chat_id: int, limit: int = 5):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT old_text, new_text, edit_date FROM edits 
                    WHERE message_id = ? AND chat_id = ? AND user_id = ?
                    ORDER BY edit_date DESC LIMIT ?
                ''', (message_id, chat_id, user_id, limit))
                return await cursor.fetchall()
        except Exception as e:
            logger.error(f"Ошибка получения изменений: {e}")
            return []
    
    async def get_user_settings(self, user_id: int):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT notify_deleted, notify_edited, save_media, auto_forward, notify_chat_id
                    FROM user_settings WHERE user_id = ?
                ''', (user_id,))
                return await cursor.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения настроек: {e}")
            return None
    
    async def update_user_settings(self, user_id: int, **kwargs):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                fields = []
                values = []
                for key, value in kwargs.items():
                    if key in ['notify_deleted', 'notify_edited', 'save_media', 'auto_forward', 'notify_chat_id']:
                        fields.append(f"{key} = ?")
                        values.append(value)
                
                if not fields:
                    return True
                
                values.append(user_id)
                await conn.execute(f'''
                    UPDATE user_settings SET {", ".join(fields)}
                    WHERE user_id = ?
                ''', values)
                
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка обновления настроек: {e}")
            return False
    
    async def get_stats(self, user_id: int):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT total_messages, deleted_messages, edited_messages, media_messages, last_updated
                    FROM stats WHERE user_id = ?
                ''', (user_id,))
                return await cursor.fetchone()
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return None
    
    async def get_active_connections_count(self, user_id: int):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT COUNT(*) FROM connections 
                    WHERE user_id = ? AND is_enabled = 1
                ''', (user_id,))
                result = await cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"Ошибка получения подключений: {e}")
            return 0
    
    async def save_connection(self, connection_id: str, user_id: int, user_name: str, can_reply: bool = False):
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                    INSERT OR REPLACE INTO connections 
                    (connection_id, user_id, user_name, is_enabled, can_reply, created_at, last_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (connection_id, user_id, user_name or '', 1, 1 if can_reply else 0,
                      int(datetime.now().timestamp()), int(datetime.now().timestamp())))
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка сохранения подключения: {e}")
            return False

db = Database()