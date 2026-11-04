import os

# litellm calls load_dotenv() on import unless it runs in PRODUCTION mode. Configuration comes only
# from Settings: a stray .env must never inject a real key or provider into tests or workers.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
