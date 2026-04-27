class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.get_calls = 0
        self.setex_calls = 0
        self.delete_calls = 0

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        return key in self.store

    def ttl(self, key: str) -> int:
        return 60 if key in self.store else -2

    def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self.store.get(key)

    def setex(self, key: str, ttl_seconds: int, value: str) -> bool:
        self.setex_calls += 1
        self.store[key] = value
        return True

    def scan_iter(self, match: str, count: int = 100):
        if match.endswith("*"):
            prefix = match[:-1]
            return (key for key in list(self.store) if key.startswith(prefix))
        return (key for key in list(self.store) if key == match)

    def delete(self, *keys: str) -> int:
        self.delete_calls += 1
        deleted = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                deleted += 1
        return deleted
