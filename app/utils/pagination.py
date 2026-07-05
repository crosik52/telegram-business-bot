"""Pagination helpers shared by dashboard pages."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Page:
    items: list
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(self.total / self.page_size))

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def start_index(self) -> int:
        return (self.page - 1) * self.page_size + 1 if self.total else 0

    @property
    def end_index(self) -> int:
        return min(self.page * self.page_size, self.total)
