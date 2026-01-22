"""Rate limiting configuration for K2 API.

Provides shared rate limiter instance for use across endpoints.
"""

from slowapi import Limiter

from k2.api.middleware import get_api_key_for_limit

# Shared rate limiter instance
# Rate limits are applied per API key (falls back to IP if no key)
limiter = Limiter(key_func=get_api_key_for_limit)
