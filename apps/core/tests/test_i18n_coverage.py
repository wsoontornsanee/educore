"""Every user-facing gettext literal in Python code must have an EN translation.

Reads the compiled catalog (`locale/en/.../django.mo`), so empty and `#, fuzzy`
entries count as missing exactly as they do at runtime. On failure, hand-edit
`locale/en/LC_MESSAGES/django.po` (never `makemessages`, it fuzzy-clobbers
existing translations) and run `manage.py compilemessages -l en`.
"""
import ast
import gettext
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

GETTEXT_NAMES = {'_', 'gettext', 'gettext_lazy'}
SCANNED_ROOTS = ('apps', 'educore')


def _literal_msgids(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
        first = node.args[0]
        if name in GETTEXT_NAMES and isinstance(first, ast.Constant) and isinstance(first.value, str):
            yield first.value, node.lineno


class EnglishCatalogCoverageTests(SimpleTestCase):
    def test_every_python_gettext_literal_has_an_english_translation(self):
        base = Path(settings.BASE_DIR)
        with open(base / 'locale' / 'en' / 'LC_MESSAGES' / 'django.mo', 'rb') as fh:
            catalog = gettext.GNUTranslations(fh)._catalog

        missing = {}
        for root in SCANNED_ROOTS:
            for path in sorted((base / root).rglob('*.py')):
                rel = path.relative_to(base).as_posix()
                if '/tests/' in rel or '/migrations/' in rel:
                    continue
                for msgid, lineno in _literal_msgids(path):
                    if msgid not in catalog:
                        missing.setdefault(msgid, f'{rel}:{lineno}')

        self.assertEqual(
            missing, {},
            f'{len(missing)} gettext literal(s) have no EN translation (first location shown)',
        )
