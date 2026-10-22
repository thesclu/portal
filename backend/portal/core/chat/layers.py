"""Team chat's channel layer settings (P22; P23a), kept here so the settings module and the tests
build exactly the same thing. No Django imports: `portal/settings.py` reads this at import time.

Redis when REDIS_URL is set; otherwise the in-memory layer, which reaches only the sockets of its
own process (one dev server, the tests).

The Redis connections' read timeout (P23a): channels-redis waits for a socket's next note with a
blocking pop that Redis ends after `RedisChannelLayer.brpop_timeout` (5 s), and since redis-py 8
every connection also gives up reading after 5 s by default. With both at 5 s the client could
give up first, by a hair of network latency, and the consumer died with "Timeout reading from
…": the socket closed under the person and the client fell back to polling. On a laptop the race
was rare (P22a saw it "once in many disconnects"); between containers it killed nearly every
idle socket within seconds (P23a's Docker run). The read timeout is set well above the block.
"""

from typing import Any

# Seconds a Redis read may take before redis-py gives up: comfortably above channels-redis's
# 5-second blocking pop, still finite so a Redis that went away is noticed.
REDIS_READ_TIMEOUT = 20
REDIS_CONNECT_TIMEOUT = 5


def channel_layers(redis_url: str, *, prefix: str = "sclu-portal") -> dict[str, Any]:
    if not redis_url:
        return {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    return {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [
                    {
                        "address": redis_url,
                        "socket_timeout": REDIS_READ_TIMEOUT,
                        "socket_connect_timeout": REDIS_CONNECT_TIMEOUT,
                    }
                ],
                "prefix": prefix,
            },
        }
    }
