"""Forces id-ID (LANGUAGE_CODE) as the actual default for first-time visitors.

Django's LocaleMiddleware resolves the active language in this order: the
django_language cookie, the session, the request's Accept-Language header,
then LANGUAGE_CODE. Browser Accept-Language sits ahead of LANGUAGE_CODE in
that chain, so a visitor whose browser is set to English got served the
English translation on their very first visit — before they ever touched
the language switcher — even though `id-ID` is meant to be the site's
actual default (spec/appendix §2.10).

Stripping Accept-Language before LocaleMiddleware runs, but only when no
django_language cookie is present yet, closes that gap without touching
the explicit-choice path: once a visitor picks a language via the site's
switcher (`set_language`), the cookie is set and takes priority over
Accept-Language on every later request regardless of this middleware.
"""
from django.conf import settings


class ForceDefaultLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cookie_name = getattr(settings, 'LANGUAGE_COOKIE_NAME', 'django_language')
        if cookie_name not in request.COOKIES and 'HTTP_ACCEPT_LANGUAGE' in request.META:
            del request.META['HTTP_ACCEPT_LANGUAGE']
        return self.get_response(request)
