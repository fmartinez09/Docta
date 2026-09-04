from dataclasses import dataclass
from typing import Protocol

import httpx
import psycopg
from anyio import to_thread


class DependencyProbe(Protocol):
    async def check(self) -> None: ...


@dataclass(frozen=True)
class PostgresProbe:
    database_url: str
    timeout_seconds: float

    async def check(self) -> None:
        await to_thread.run_sync(self._check_sync)

    def _check_sync(self) -> None:
        with psycopg.connect(
            self.database_url,
            connect_timeout=self.timeout_seconds,
        ) as connection:
            connection.execute("SELECT 1")


@dataclass(frozen=True)
class MinioProbe:
    health_url: str
    timeout_seconds: float

    async def check(self) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(self.health_url)
            response.raise_for_status()


@dataclass(frozen=True)
class ReadinessProbes:
    postgres: DependencyProbe
    object_storage: DependencyProbe

    async def check_all(self) -> dict[str, str]:
        checks: dict[str, str] = {}
        for name, probe in (
            ("postgres", self.postgres),
            ("object_storage", self.object_storage),
        ):
            try:
                await probe.check()
            except Exception:  # Adapters intentionally map provider details to a safe status.
                checks[name] = "unavailable"
            else:
                checks[name] = "ok"
        return checks
