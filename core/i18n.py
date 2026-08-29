"""Lightweight content localization helpers.

Convention: models store canonical English values in their regular fields and
carry a `translations` JSONField shaped as
{"zh-TW": {"field": "value", ...}, "<lang>": {...}} for any number of
languages. Requests resolve a language via the `lang` query parameter or the
Accept-Language header; unknown languages fall back to the canonical fields.
"""

DEFAULT_LANGUAGE = 'en'


def normalize_language(lang, region=None):
    """Normalize a raw language tag considering the active region.
    
    - `zh-HK` / `zh-Hant-HK` / `yue` / `zh-yue` → `zh-HK`
    - `zh-TW` / `zh-Hant-TW` / `zh-hant` / `zh` → if region defaults to zh, use it, else `zh-TW`
    - `zh-CN` / `zh-Hans` → fallback to region default, else `zh-TW`
    - others pass through unchanged.
    """
    if not lang:
        return DEFAULT_LANGUAGE
    lang = lang.strip().replace('_', '-')
    if not lang:
        return DEFAULT_LANGUAGE
        
    lang_lower = lang.lower()
    
    if lang_lower in ('zh-hk', 'zh-hant-hk', 'yue', 'zh-yue'):
        return 'zh-HK'
        
    if lang_lower in ('zh-cn', 'zh-hans'):
        if region and region.default_language_id:
            return region.default_language_id
        return 'zh-TW'
        
    if lang_lower in ('zh-tw', 'zh-hant-tw', 'zh-hant', 'zh'):
        if region and region.default_language_id and region.default_language_id.startswith('zh'):
            return region.default_language_id
        return 'zh-TW'
        
    return lang


def resolve_language(request):
    """Language for this request: ?lang= wins, then Accept-Language, then the
    region's default_language (English only when there is no region)."""
    lang = request.GET.get('lang', '').strip()
    if not lang:
        accept = request.headers.get('Accept-Language', '')
        lang = accept.split(',')[0].split(';')[0].strip()
        
    from core.region import get_region
    region = get_region(request)

    if not lang:
        # Nothing was asked for, so answer with the region's own default
        # rather than routing an empty string through normalize_language(),
        # which turns it into DEFAULT_LANGUAGE ('en') — indistinguishable
        # from an explicit request for English.
        #
        # This used to be masked: regions created by 0006_backfill_regions had
        # an empty `languages` list, so the `not in supported_langs` check
        # below fired for every value and sent everything to the region
        # default anyway. Filling that list in (0009_complete_tw_region_config)
        # made 'en' genuinely supported, and unspecified requests started
        # coming back in English.
        return region.default_language_id if region else DEFAULT_LANGUAGE

    normalized = normalize_language(lang, region)

    if region:
        supported_langs = [l.code for l in region.languages.all()]
        if normalized not in supported_langs:
            return region.default_language_id

    return normalized


def pick_translation(translations, lang):
    """The translation dict for `lang`, trying the exact tag then its base
    language (e.g. de-AT → de). Returns {} when nothing matches, so callers
    can fall back to canonical fields."""
    if not isinstance(translations, dict) or not lang:
        return {}
    exact = translations.get(lang)
    if isinstance(exact, dict):
        return exact
    base = lang.split('-')[0]
    for key, value in translations.items():
        if key.split('-')[0] == base and isinstance(value, dict):
            return value
    return {}
