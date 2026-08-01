import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден в .env файле!")
    
    DB_PATH = os.getenv('DB_PATH', 'bot_database.db')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'bot.log')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    
    DEFAULT_NOTIFY_DELETED = int(os.getenv('DEFAULT_NOTIFY_DELETED', 1))
    DEFAULT_NOTIFY_EDITED = int(os.getenv('DEFAULT_NOTIFY_EDITED', 1))
    DEFAULT_SAVE_MEDIA = int(os.getenv('DEFAULT_SAVE_MEDIA', 1))
    DEFAULT_AUTO_FORWARD = int(os.getenv('DEFAULT_AUTO_FORWARD', 0))
    
    MAX_HISTORY_MESSAGES = int(os.getenv('MAX_HISTORY_MESSAGES', 1000))
    MAX_TEXT_LENGTH = int(os.getenv('MAX_TEXT_LENGTH', 4096))

config = Config()