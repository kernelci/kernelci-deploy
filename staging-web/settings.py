#!/usr/bin/env python3

from models import Settings
from database import SessionLocal
from config import SETTINGS_KEYS

def get_setting(key: str, default: str = None) -> str:
    """Get a setting value"""
    db = SessionLocal()
    try:
        setting = db.query(Settings).filter(Settings.key == key).first()
        value = setting.value if setting else default
        
        # Debug logging for GitHub token (but don't log the actual token)
        if key == GITHUB_TOKEN:
            has_token = bool(value and value.strip())
            print(f"DEBUG: Getting GitHub token setting - has_token: {has_token}")
        
        return value
    finally:
        db.close()

def set_setting(key: str, value: str):
    """Set a setting value"""
    db = SessionLocal()
    try:
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            setting = Settings(key=key, value=value)
            db.add(setting)
        db.commit()
        
        # Debug logging for GitHub token (but don't log the actual token)
        if key == GITHUB_TOKEN:
            has_token = bool(value and value.strip())
            print(f"DEBUG: Setting GitHub token - has_token: {has_token}")
        
    finally:
        db.close()

# Setting keys from config.py
DISCORD_WEBHOOK_URL = SETTINGS_KEYS["DISCORD_WEBHOOK_URL"]
GITHUB_TOKEN = SETTINGS_KEYS["GITHUB_TOKEN"]
SKIP_SELF_UPDATE = SETTINGS_KEYS["SKIP_SELF_UPDATE"]