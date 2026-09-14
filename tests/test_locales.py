"""core/locales: the language files behind every email the backend writes.

The files are edited by hand, so these tests are what keeps them usable: the
same keys in every language, the same {placeholders} in every translation, and
no key the code asks for that a file lacks (or that nothing asks for any more).
"""

import json
import re
from pathlib import Path

import pytest

from accounts.views.auth import LINK_EMAIL_KINDS
from core import i18n
from core.i18n import CATALOGS, DEFAULT_LANGUAGE, EMAIL_LANGUAGES, LOCALES_DIR, t

BACKEND_DIR = Path(__file__).resolve().parent.parent
SKIP_DIRS = {'.venv', 'tests', 'migrations', 'node_modules', 'staticfiles_build'}
PLACEHOLDER = re.compile(r'\{(\w+)\}')
LOCALE_FILES = sorted(LOCALES_DIR.glob('*.json'))


def _source_files():
    for path in BACKEND_DIR.rglob('*.py'):
        if not SKIP_DIRS.intersection(path.relative_to(BACKEND_DIR).parts):
            yield path


def _keys_used_in_code():
    literal = re.compile(r"""\bt\(\s*\w+\s*,\s*["']([\w.]+)["']""")
    used = set()
    for path in _source_files():
        used.update(literal.findall(path.read_text(encoding='utf-8')))
    # _link_email builds its keys from a kind name, so expand those.
    used.update(f"email.{kind}.{part}" for kind in LINK_EMAIL_KINDS for part in ('subject', 'intro', 'ignore'))
    return used


def test_the_default_language_has_a_file():
    assert DEFAULT_LANGUAGE in CATALOGS


def test_email_languages_are_the_locale_files():
    assert set(EMAIL_LANGUAGES) == {path.stem for path in LOCALE_FILES}
    assert {'zh-TW', 'zh-HK', 'en'} <= set(EMAIL_LANGUAGES)


@pytest.mark.parametrize('path', LOCALE_FILES, ids=lambda p: p.name)
def test_a_locale_file_declares_each_key_once(path):
    # json.loads keeps the last of two identical keys without a word, so a
    # copy-pasted line would silently replace the translation above it.
    pairs = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=lambda items: items)
    keys = [key for key, _ in pairs]
    assert sorted({k for k in keys if keys.count(k) > 1}) == []
    assert all(isinstance(value, str) and value.strip() for _, value in pairs)


@pytest.mark.parametrize('lang', sorted(set(CATALOGS) - {DEFAULT_LANGUAGE}))
def test_every_language_has_exactly_the_default_languages_keys(lang):
    reference = set(CATALOGS[DEFAULT_LANGUAGE])
    assert sorted(reference - set(CATALOGS[lang])) == [], "missing"
    assert sorted(set(CATALOGS[lang]) - reference) == [], "not in the default language"


@pytest.mark.parametrize('lang', sorted(set(CATALOGS) - {DEFAULT_LANGUAGE}))
def test_translations_keep_the_placeholders(lang):
    # A renamed or dropped {sender} would raise, or lose the value, only when
    # that email is sent in that one language.
    mismatched = {
        key: (sorted(PLACEHOLDER.findall(text)), sorted(PLACEHOLDER.findall(CATALOGS[lang].get(key, ''))))
        for key, text in CATALOGS[DEFAULT_LANGUAGE].items()
        if set(PLACEHOLDER.findall(text)) != set(PLACEHOLDER.findall(CATALOGS[lang].get(key, '')))
    }
    assert mismatched == {}


def test_every_key_the_code_uses_exists():
    assert sorted(_keys_used_in_code() - set(CATALOGS[DEFAULT_LANGUAGE])) == []


def test_every_key_is_used_by_the_code():
    assert sorted(set(CATALOGS[DEFAULT_LANGUAGE]) - _keys_used_in_code()) == []


def test_the_key_scan_sees_the_call_sites():
    # Guards the two tests above: a scan that matched nothing would find no
    # missing keys and call every key unused.
    assert {'email.chatMessage.subject', 'email.waitlist.stop'} <= _keys_used_in_code()


def test_every_link_email_call_uses_a_known_kind():
    call = re.compile(r"""_link_email\([^,]+,\s*["'](\w+)["']""")
    kinds = set()
    for path in _source_files():
        kinds.update(call.findall(path.read_text(encoding='utf-8')))
    assert kinds == set(LINK_EMAIL_KINDS)


def test_t_fills_placeholders_in_the_requested_language():
    assert t('zh-TW', 'email.chatMessage.listing', title='微積分') == '書籍：微積分'
    assert t('en', 'email.chatMessage.listing', title='Calculus') == 'Listing: Calculus'


def test_t_leaves_braces_in_values_alone():
    assert t('en', 'email.chatMessage.message', preview='{link} {0}') == 'Message: {link} {0}'


@pytest.mark.parametrize('lang, expected', [
    ('en-GB', 'Listing: X'),   # base language
    ('fr', 'Listing: X'),      # no file: the default
    ('', 'Listing: X'),
    (None, 'Listing: X'),
])
def test_t_falls_back_for_languages_without_a_file(lang, expected):
    assert t(lang, 'email.chatMessage.listing', title='X') == expected


def test_t_falls_back_to_the_default_language_for_a_missing_key(monkeypatch, caplog):
    partial = {**CATALOGS, 'zh-TW': {k: v for k, v in CATALOGS['zh-TW'].items() if k != 'email.waitlist.logIn'}}
    monkeypatch.setattr(i18n, 'CATALOGS', partial)

    assert t('zh-TW', 'email.waitlist.logIn') == CATALOGS[DEFAULT_LANGUAGE]['email.waitlist.logIn']
    assert 'email.waitlist.logIn' in caplog.text
