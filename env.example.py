import os

# Gemini
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

# DigitalOcean Spaces
DO_SPACES_NAME = os.environ.get('DO_SPACES_NAME', '')
DO_SPACES_REGION = os.environ.get('DO_SPACES_REGION', 'sfo2')
DO_SPACES_ACCESS_KEY = os.environ.get('DO_SPACES_ACCESS_KEY', '')
DO_SPACES_SECRET_KEY = os.environ.get('DO_SPACES_SECRET_KEY', '')
DO_SPACES_CDN_ENDPOINT = os.environ.get('DO_SPACES_CDN_ENDPOINT', '')
DO_SPACES_BASE_PATH = os.environ.get('DO_SPACES_BASE_PATH', 'siliconfriendly')

# Django
DJANGO_SECRET = os.environ.get('DJANGO_SECRET', 'change-me-in-production')
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://localhost:8000')
FRONTEND_BASE_URL = os.environ.get('FRONTEND_BASE_URL', 'https://siliconfriendly.com')

# Dodo Payments
DODOPAYMENTS_API_KEY = os.environ.get('DODOPAYMENTS_API_KEY', '')
DODOPAYMENTS_WEBHOOK_KEY = os.environ.get('DODOPAYMENTS_WEBHOOK_KEY', '')

# Database
DB_NAME = os.environ.get('DB_NAME', 'siliconfriendly')
DB_USER = os.environ.get('DB_USER', 'siliconfriendly')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')

# USDC Wallets (public addresses, safe to hardcode)
USDC_EVM_ADDRESS = '0xAfdC6947d877431282F57d9Db843E052F3405f80'
USDC_SOLANA_ADDRESS = '5n48pGS3ZC4ePJggg1N2ue4aUhzYNNM3ebq4vDdG2kc6'
