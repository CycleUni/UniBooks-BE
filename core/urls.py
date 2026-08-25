from django.urls import path
from core.views import HealthCheckView

# HomeMetadataView is routed at the project level (`cycleuni/urls.py`) to 
# `accounts.views.HomeMetadataView` to keep the `core` app independent.
# The URL path remains `/api/v1/core/metadata/`.
urlpatterns = [
    path('healthz/', HealthCheckView.as_view(), name='healthz'),
]

