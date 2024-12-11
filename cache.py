from functools import wraps
import json
from typing import Any, Callable, Optional
from redis import Redis
from fastapi import HTTPException
from datetime import timedelta
from enum import Enum
from pydantic import BaseModel, Field
from typing import Dict

class CacheType(Enum):
    """Enum for different types of cached data"""
    TEXTBOOK = "textbook"
    CHAPTER = "chapter"
    CONVERSATION = "conversation"
    AI_RESPONSE = "ai_response"
    QUIZ = "quiz"

class CacheTTLConfig(BaseModel):
    """Configuration model for cache TTLs"""
    textbook: int = Field(default=3600, description="TTL for textbook cache in seconds")
    chapter: int = Field(default=3600, description="TTL for chapter cache in seconds")
    conversation: int = Field(default=1800, description="TTL for conversation cache in seconds")
    ai_response: int = Field(default=86400, description="TTL for AI response cache in seconds")
    quiz: int = Field(default=7200, description="TTL for quiz cache in seconds")

    class Config:
        use_enum_values = True

class RedisConfig(BaseModel):
    """Configuration model for Redis connection"""
    host: str = Field(default="localhost", description="Redis host")
    port: int = Field(default=6379, description="Redis port")
    db: int = Field(default=0, description="Redis database number")
    password: Optional[str] = Field(default=None, description="Redis password")

class CacheService:
    """Service class for handling all caching operations"""
    
    def __init__(
        self,
        redis_config: RedisConfig,
        ttl_config: CacheTTLConfig = CacheTTLConfig()
    ):
        """Initialize cache service with configurations"""
        self.client = Redis(
            host=redis_config.host,
            port=redis_config.port,
            db=redis_config.db,
            password=redis_config.password,
            decode_responses=True
        )
        self.ttl_config = ttl_config
        
    def _get_ttl(self, cache_type: CacheType) -> int:
        """Get TTL for specific cache type"""
        return getattr(self.ttl_config, cache_type.value)

    def _generate_key(self, cache_type: CacheType, *args, **kwargs) -> str:
        """Generate a unique cache key"""
        key_parts = [cache_type.value]
        key_parts.extend(str(arg) for arg in args)
        key_parts.extend(f"{k}:{v}" for k, v in sorted(kwargs.items()))
        return ":".join(key_parts)

    async def get(self, cache_type: CacheType, *args, **kwargs) -> Optional[str]:
        """Get value from cache"""
        key = self._generate_key(cache_type, *args, **kwargs)
        return self.client.get(key)

    async def set(
        self,
        cache_type: CacheType,
        value: Any,
        *args,
        **kwargs
    ) -> None:
        """Set value in cache"""
        key = self._generate_key(cache_type, *args, **kwargs)
        try:
            serialized_value = (
                json.dumps(value) if not isinstance(value, str) else value
            )
            self.client.setex(
                key,
                self._get_ttl(cache_type),
                serialized_value
            )
        except (TypeError, ValueError) as e:
            print(f"Failed to cache value: {e}")

    async def invalidate(self, cache_type: CacheType, *args, **kwargs) -> None:
        """Invalidate specific cache entry"""
        key = self._generate_key(cache_type, *args, **kwargs)
        self.client.delete(key)

    async def invalidate_pattern(self, pattern: str) -> None:
        """Invalidate all cache entries matching pattern"""
        for key in self.client.scan_iter(pattern):
            self.client.delete(key)

    def cache_decorator(
        self,
        cache_type: CacheType,
        skip_cache_if: Callable[[dict], bool] = None
    ):
        """Decorator for caching function results"""
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                cache_key = self._generate_key(cache_type, *args, **kwargs)
                cached_value = self.client.get(cache_key)
                if cached_value:
                    try:
                        cached_data = json.loads(cached_value)
                        if not skip_cache_if or not skip_cache_if(cached_data):
                            return cached_data
                    except json.JSONDecodeError:
                        if not skip_cache_if or not skip_cache_if(cached_value):
                            return cached_value

                result = await func(*args, **kwargs)
                await self.set(cache_type, result, *args, **kwargs)
                return result
            return wrapper
        return decorator

class AIResponseCache:
    def __init__(self, cache_service: CacheService):
        self.cache_service = cache_service

    async def get_response(self, prompt: str) -> Optional[str]:
        return await self.cache_service.get(CacheType.AI_RESPONSE, prompt)

    async def cache_response(self, prompt: str, response: str) -> None:
        await self.cache_service.set(CacheType.AI_RESPONSE, response, prompt)

def init_cache_service(
    redis_config: Optional[RedisConfig] = None,
    ttl_config: Optional[CacheTTLConfig] = None
) -> CacheService:
    redis_config = redis_config or RedisConfig()
    ttl_config = ttl_config or CacheTTLConfig()
    return CacheService(redis_config, ttl_config)