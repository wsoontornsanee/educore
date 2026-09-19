"""Every user-facing string marked for translation must have an EN translation.

Covers Python gettext calls (`_`, `gettext`, `gettext_lazy`, `gettext_noop`, `ngettext[_lazy]`, `pgettext[_lazy]`,
`npgettext[_lazy]`) and the template tags `{% translate %}`, `{% trans %}` and `{% blocktranslate %}`.

Reads the compiled catalog (`locale/en/.../django.mo`), so empty and `#, fuzzy` entries count as missing exactly as
they do at runtime. On failure, hand-edit `locale/en/LC_MESSAGES/django.po` (never `makemessages`, it fuzzy-clobbers
existing translations) and run `manage.py compilemessages -l en`.

What a scan cannot see, so it is not covered: a message passed to gettext through a variable (`_(text)`), and strings
that are never wrapped at all.
"""
import ast
import gettext
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.utils.translation.template import templatize

SINGULAR = {'_', 'gettext', 'gettext_lazy', 'gettext_noop', 'ngettext', 'ngettext_lazy'}
CONTEXTUAL = {'pgettext', 'pgettext_lazy', 'npgettext', 'npgettext_lazy'}
PYTHON_ROOTS = ('apps', 'educore')
TEMPLATE_ROOTS = ('frontend', 'apps')
SKIPPED_DIRS = {'tests', 'migrations', 'node_modules', 'collected_static'}

# templatize() rewrites a template into gettext calls for xgettext (the same step makemessages uses); the output is
# not valid Python (markup is masked with filler), so pull the calls out with a literal-aware pattern.
_STR = r"(u?'(?:[^'\\\n]|\\.)*'|u?\"(?:[^\"\\\n]|\\.)*\")"
_TEMPLATE_SINGULAR = re.compile(r'\b(?:gettext|ngettext|_)\(\s*' + _STR)
_TEMPLATE_CONTEXTUAL = re.compile(r'\bn?pgettext\(\s*' + _STR + r'\s*,\s*' + _STR)


def _skipped(path):
    return bool(SKIPPED_DIRS.intersection(path.parts)) or path.name.startswith('test_')


def _load_catalog():
    with open(Path(settings.BASE_DIR) / 'locale' / 'en' / 'LC_MESSAGES' / 'django.mo', 'rb') as fh:
        return gettext.GNUTranslations(fh)._catalog


def _translated(catalog, msgid, context=None):
    key = msgid if context is None else f'{context}\x04{msgid}'
    return key in catalog or (key, 0) in catalog  # plural entries are keyed (msgid, 0)


def _calls(path):
    """Yield (function name, call node) for every gettext-family call in a Python file."""
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
            if name in SINGULAR or name in CONTEXTUAL:
                yield name, node


def _python_files(base):
    for root in PYTHON_ROOTS:
        for path in sorted((base / root).rglob('*.py')):
            if not _skipped(path.relative_to(base)):
                yield path, path.relative_to(base).as_posix()


class EnglishCatalogCoverageTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base = Path(settings.BASE_DIR)
        cls.catalog = _load_catalog()

    def test_every_python_gettext_literal_has_an_english_translation(self):
        missing = {}
        for path, rel in _python_files(self.base):
            for name, node in _calls(path):
                context, msgid = None, node.args[0]
                if name in CONTEXTUAL:
                    if len(node.args) < 2:
                        continue
                    context, msgid = node.args[0], node.args[1]
                    context = context.value if isinstance(context, ast.Constant) else None
                if isinstance(msgid, ast.Constant) and isinstance(msgid.value, str):
                    if not _translated(self.catalog, msgid.value, context):
                        missing.setdefault((context, msgid.value), f'{rel}:{node.lineno}')
        self.assertEqual(
            missing, {}, f'{len(missing)} gettext literal(s) have no EN translation (first location shown)',
        )

    def test_no_gettext_call_wraps_an_fstring_concatenation_or_format(self):
        # The argument is evaluated before gettext sees it, so the interpolated text is never in the catalog
        # and EN users get the Indonesian source string. Use `_("... %(x)s ...") % {...}` instead.
        offenders = []
        for path, rel in _python_files(self.base):
            for name, node in _calls(path):
                arg = node.args[1] if name in CONTEXTUAL and len(node.args) > 1 else node.args[0]
                interpolated = isinstance(arg, (ast.JoinedStr, ast.BinOp)) or (
                    isinstance(arg, ast.Call) and getattr(arg.func, 'attr', None) == 'format'
                )
                if interpolated:
                    offenders.append(f'{rel}:{node.lineno}')
        self.assertEqual(offenders, [], 'gettext called on an already-interpolated string')

    def test_every_template_translate_tag_has_an_english_translation(self):
        missing, scanned = {}, 0
        for root in TEMPLATE_ROOTS:
            for path in sorted((self.base / root).rglob('*.html')):
                rel = path.relative_to(self.base)
                if _skipped(rel):
                    continue
                source = path.read_text(encoding='utf-8-sig')
                if 'trans' not in source:
                    continue
                scanned += 1
                extracted = templatize(source, origin=rel.as_posix())
                where = lambda m: f'{rel.as_posix()}:{extracted.count(chr(10), 0, m.start()) + 1}'  # noqa: E731
                for m in _TEMPLATE_SINGULAR.finditer(extracted):
                    msgid = ast.literal_eval(m.group(1))
                    if not _translated(self.catalog, msgid):
                        missing.setdefault((None, msgid), where(m))
                for m in _TEMPLATE_CONTEXTUAL.finditer(extracted):
                    context, msgid = ast.literal_eval(m.group(1)), ast.literal_eval(m.group(2))
                    if not _translated(self.catalog, msgid, context):
                        missing.setdefault((context, msgid), where(m))
        self.assertGreater(scanned, 20, 'template scan found suspiciously few templates')
        self.assertEqual(
            missing, {}, f'{len(missing)} template string(s) have no EN translation (first location shown)',
        )

    def test_translations_keep_the_placeholders_of_their_source_string(self):
        # A dropped or renamed %(name)s makes `_("...") % {...}` raise KeyError or silently lose a value.
        placeholder = re.compile(r'%\((\w+)\)[sdif]')
        broken = []
        for key, translation in self.catalog.items():
            msgid = key[0] if isinstance(key, tuple) else key.split('\x04')[-1]
            if isinstance(translation, str) and set(placeholder.findall(msgid)) != set(placeholder.findall(translation)):
                broken.append(msgid)
        self.assertEqual(broken, [])
