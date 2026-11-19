from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AdapterResult:
    ok: bool
    message: str
    rows: int = 0
    payload: object | None = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)


class BaseAdapter(ABC):
    """Every data source adapter must subclass this."""

    name: str = "base"

    @abstractmethod
    def health_check(self) -> AdapterResult:
        ...

    @abstractmethod
    def fetch_instruments(self) -> AdapterResult:
        ...

    @abstractmethod
    def fetch_prices(self, symbol: str, days: int = 30) -> AdapterResult:
        ...