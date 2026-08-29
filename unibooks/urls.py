from django.contrib import admin
from django.urls import path, include
from accounts.views import HomeMetadataView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('accounts.urls')),
    path('api/v1/books/', include('catalog.urls')),
    path('api/v1/listings/', include('listings.urls')),
    path('api/v1/search/', include('search.urls')),
    path('api/v1/messaging/', include('messaging.urls')),
    path('api/v1/subscriptions/', include('subscriptions.urls')),
    path('api/v1/orders/', include('orders.urls')),
    path('api/v1/moderation/', include('moderation.urls')),
    path('api/v1/admin/', include('adminapi.urls')),
    # Renamed from 'ads/' to 'promotions/' to prevent adblockers from blocking internal banners
    path('api/v1/promotions/', include('ads.urls')),
    path('api/cron/', include('cron.urls')),
    # HomeMetadataView depends on accounts.School and subscriptions.Subscription,
    # so it's wired directly at the project level to keep the core app dependency-free.
    # The path remains /api/v1/core/metadata/ for frontend compatibility.
    path('api/v1/core/metadata/', HomeMetadataView.as_view(), name='home-metadata'),
    path('api/v1/core/', include('core.urls')),
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
