import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-key")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # tenant fallback only (used when no subdomain / dev)
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")

    # ✅ bind for platform registry
    SQLALCHEMY_BINDS = {
        "platform": os.getenv("PLATFORM_DATABASE_URL")
    }

    BASE_DOMAIN = os.getenv("BASE_DOMAIN", "localhost")
    DEFAULT_TENANT_SLUG = os.getenv("DEFAULT_TENANT_SLUG", None)