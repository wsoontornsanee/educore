"""Cursor pagination for the partner API (spec/18 §4).

Shape contract: `?cursor=&limit=` request -> `{results: [...], next_cursor}`
response. Unlike the platform's StandardCursorPagination (DRF cursor format,
`next`/`previous` links), partner integrators parse `next_cursor` directly.
"""
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response


class PartnerCursorPagination(CursorPagination):
    page_size = 50
    max_page_size = 200  # §8: rows per cursor page
    page_size_query_param = 'limit'
    cursor_query_param = 'cursor'
    ordering = '-id'

    def get_paginated_response(self, data):
        return Response({
            'results': data,
            'next_cursor': self.get_next_cursor(),
        })

    def get_next_cursor(self):
        if not self.has_next:
            return None
        # DRF builds the full cursor URL; extract just the opaque cursor token.
        url = self.get_next_link()
        if not url:
            return None
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(url).query)
        values = query.get(self.cursor_query_param)
        return values[0] if values else None
