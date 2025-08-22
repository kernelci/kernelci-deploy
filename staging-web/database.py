from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, UserRole, Settings
from config import DATABASE_URL, DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_EMAIL, SETTINGS_KEYS

SQLALCHEMY_DATABASE_URL = DATABASE_URL

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initialize database and create default admin user"""
    # i know its ugly, but we can fix it later (TODO)
    from auth import get_password_hash  # Import here to avoid circular import
    
    Base.metadata.create_all(bind=engine)
    
    # Create default admin user if not exists
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first()
        if not admin_user:
            admin_user = User(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=get_password_hash(DEFAULT_ADMIN_PASSWORD),
                role=UserRole.ADMIN,
                email=DEFAULT_ADMIN_EMAIL
            )
            db.add(admin_user)
            db.commit()
            print("Default admin user created")
            
        # Create default settings
        for setting_name, setting_key in SETTINGS_KEYS.items():
            setting = db.query(Settings).filter(Settings.key == setting_key).first()
            if not setting:
                setting = Settings(key=setting_key, value="")
                db.add(setting)
            
        db.commit()
        
    finally:
        db.close()