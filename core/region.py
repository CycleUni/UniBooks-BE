from django.conf import settings
from django.core.cache import cache
from django.utils.functional import SimpleLazyObject

DEFAULT_REGION_CODE = getattr(settings, 'DEFAULT_REGION', 'TW')

def _get_active_regions():
    """
    Get active regions from cache, falling back to DB.
    """
    cached = cache.get('active_regions')
    if cached is not None:
        return cached
        
    from core.models import Region
    regions = list(Region.objects.filter(is_active=True).select_related('currency', 'default_language').prefetch_related('languages'))
    # force evaluation of prefetch cache so it doesn't query DB after unpickling
    for r in regions:
        list(r.languages.all())
        
    regions_dict = {r.code: r for r in regions}
    cache.set('active_regions', regions_dict, 3600)
    return regions_dict

def get_region(request):
    """
    Definitive entry point for region resolution in DRF views.
    Caches the result on the request object.
    
    Resolution order (new spec):
    1. ?region= (Query parameter)
    2. X-Region (Header)
    3. region (Cookie)
    4. Authenticated user's single verified region (only if exactly one exists)
    5. CF-IPCountry (Cloudflare Header)
    6. settings.DEFAULT_REGION
    7. The first active region in DB
    """
    if hasattr(request, '_cached_region'):
        return request._cached_region

    try:
        active_regions = _get_active_regions()
    except Exception:
        return None
        
    if not active_regions:
        return None
    
    candidates = []
    
    query_region = request.GET.get('region')
    if query_region:
        candidates.append(query_region.upper())
        
    header_region = request.headers.get('X-Region')
    if header_region:
        candidates.append(header_region.upper())
        
    cookie_region = request.COOKIES.get('region')
    if cookie_region:
        candidates.append(cookie_region.upper())
        
    # Priority 4: Verified regions of authenticated user (only if exactly one)
    if hasattr(request, 'user') and request.user.is_authenticated:
        if hasattr(request.user, 'verified_regions'):
            # Only use if exactly one verified region
            # (If not evaluated, evaluating is needed. Usually a queryset.)
            regions = list(request.user.verified_regions.all())
            if len(regions) == 1:
                candidates.append(regions[0].code.upper())
        
    cf_region = request.headers.get('CF-IPCountry')
    if cf_region:
        candidates.append(cf_region.upper())
        
    candidates.append(DEFAULT_REGION_CODE.upper())
    
    resolved = None
    for code in candidates:
        if code in active_regions:
            resolved = active_regions[code]
            break
            
    if not resolved:
        resolved = list(active_regions.values())[0]

    request._cached_region = resolved
    return resolved

def resolve_region(request):
    """
    Deprecated backwards compatible alias for get_region.
    """
    return get_region(request)
