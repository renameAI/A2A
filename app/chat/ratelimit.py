"""속도 제한 — 토큰 버킷. 사용자 단위와 테넌트 단위를 함께 건다.

Redis가 있으면 Lua 스크립트 하나로 '읽고-계산하고-쓰기'를 원자 실행한다 — 여러
인스턴스가 같은 버킷을 공유해야 서버리스 확장 시에도 한도가 지켜진다.
Redis가 없으면 프로세스 안 버킷으로 대신하되, 이것은 인스턴스마다 따로 세므로
실제 한도가 '한도 × 인스턴스 수'가 된다는 사실을 /platform/health가 드러낸다.

기본값(환경변수로 조정):
  사용자: 초당 5건, 순간 20건 — 사람 손으로는 넘기 어렵고 스크립트 폭주는 막는다
  테넌트: 초당 300건, 순간 600건 — 중규모 목표(전체 200건/초)를 한 테넌트가
          독점하지 못하게. 시끄러운 이웃이 같은 셀의 다른 테넌트를 굶기지 않게 한다.
"""
import os
import threading
import time

from ..errors import EngineError

USER_RATE = float(os.environ.get("CHAT_RL_USER_RATE", "5"))
USER_BURST = float(os.environ.get("CHAT_RL_USER_BURST", "20"))
TENANT_RATE = float(os.environ.get("CHAT_RL_TENANT_RATE", "300"))
TENANT_BURST = float(os.environ.get("CHAT_RL_TENANT_BURST", "600"))

_LUA = """
local tokens_key = KEYS[1]
local rate = tonumber(ARGV[1]); local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3]); local cost = tonumber(ARGV[4])
local b = redis.call('HMGET', tokens_key, 't', 'ts')
local t = tonumber(b[1]); local ts = tonumber(b[2])
if t == nil then t = burst; ts = now end
t = math.min(burst, t + (now - ts) * rate)
local ok = 0
if t >= cost then t = t - cost; ok = 1 end
redis.call('HSET', tokens_key, 't', t, 'ts', now)
redis.call('PEXPIRE', tokens_key, math.ceil(burst / rate * 1000) + 1000)
return {ok, tostring(t)}
"""


class _Local:
    def __init__(self):
        self.b: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def take(self, key, rate, burst, now):
        with self.lock:
            t, ts = self.b.get(key, (burst, now))
            t = min(burst, t + (now - ts) * rate)
            ok = t >= 1
            if ok:
                t -= 1
            self.b[key] = (t, now)
            return ok


class Limiter:
    def __init__(self):
        url = os.environ.get("CHAT_REDIS_URL")
        self.mode = "local"
        self.local = _Local()
        self.script = None
        if url:
            import redis
            self.r = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
            self.script = self.r.register_script(_LUA)
            self.mode = "redis"

    def _take(self, key, rate, burst) -> bool:
        now = time.time()
        if self.script is not None:
            try:
                ok, _ = self.script(keys=[key], args=[rate, burst, now, 1])
                return bool(ok)
            except Exception:  # noqa: BLE001 — Redis 장애 시 로컬로 강등(가용성 우선, 기록)
                from . import events
                events.stats["ratelimit_fallback"] += 1
        return self.local.take(key, rate, burst, now)

    def check_post(self, tenant_id: str, uid: str) -> None:
        if not self._take(f"rl:u:{tenant_id}:{uid}", USER_RATE, USER_BURST):
            raise EngineError(429, "rate_limited", "너무 빠르게 보내고 있어요 — 잠시 후 다시")
        if not self._take(f"rl:t:{tenant_id}", TENANT_RATE, TENANT_BURST):
            raise EngineError(429, "rate_limited", "이 워크스페이스의 전송량 한도에 도달했어요")


_lim: "Limiter | None" = None


def limiter() -> Limiter:
    global _lim
    if _lim is None:
        _lim = Limiter()
    return _lim


def reset() -> None:
    global _lim
    _lim = None
