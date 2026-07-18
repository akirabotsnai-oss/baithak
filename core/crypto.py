import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# We generate a key if none is provided, but this means restarts will invalidate tokens.
# A real deployment MUST set ENCRYPTION_KEY in .env
_key = os.environ.get("ENCRYPTION_KEY")
if not _key:
    # Fallback for dev: this will reset every time the server restarts
    # meaning tokens added in one session won't work in the next!
    _key = Fernet.generate_key().decode()
    print("WARNING: ENCRYPTION_KEY not set in .env. Using ephemeral key.")

cipher = Fernet(_key.encode())

def encrypt_token(plain_token: str) -> str:
    if not plain_token: return ""
    return cipher.encrypt(plain_token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    if not encrypted_token: return ""
    try:
        return cipher.decrypt(encrypted_token.encode()).decode()
    except Exception:
        return ""
