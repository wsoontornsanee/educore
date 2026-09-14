"""Pagination classes for EduCore API (spec/01 §8.1)."""
from rest_framework.pagination import CursorPagination

class StandardCursorPagination(CursorPagination):
    """Cursor pagination ordered by -created_at by default (spec/01 §8.1)."""
    ordering = '-created_at'
    page_size = 50
