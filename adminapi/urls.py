from django.urls import path

from .views import (
    AdminRegionListView, AdminRegionDetailView, AdminCurrencyListView, AdminCurrencyDetailView,
    AdminListingDetailView,
    AdminListingListView,
    AdminOrderDetailView,
    AdminOrderForceCancelView,
    AdminOrderListView,
    AdminUserDetailView,
    AdminUserListView,
    AdminManagerToggleView,
    AdminSchoolListView,
    AdminSchoolDetailView,
    AdminCategoryListView, 
    AdminCategoryDetailView,
    AdminSchoolBulkImportView,
    AdminCategoryBulkImportView,
    AdminChatReportListView,
    AdminChatReportDetailView,
    AdminChatReportTokenView,
)
from .views.ads import (
    AdminAdvertiserListView,
    AdminAdvertiserDetailView,
    AdminAdListView,
    AdminAdDetailView,
)
from .views.uploads import (
    AdminAdUploadURLView,
    AdminAdUploadDirectView,
)

urlpatterns = [
    path('regions/', AdminRegionListView.as_view(), name='admin-region-list'),
    path('regions/<str:pk>/', AdminRegionDetailView.as_view(), name='admin-region-detail'),
    path('currencies/', AdminCurrencyListView.as_view(), name='admin-currency-list'),
    path('currencies/<str:pk>/', AdminCurrencyDetailView.as_view(), name='admin-currency-detail'),
    path('users/', AdminUserListView.as_view(), name='admin-user-list'),
    path('users/<int:pk>/', AdminUserDetailView.as_view(), name='admin-user-detail'),
    path('managers/<int:pk>/toggle/', AdminManagerToggleView.as_view(), name='admin-manager-toggle'),
    path('listings/', AdminListingListView.as_view(), name='admin-listing-list'),
    path('listings/<uuid:pk>/', AdminListingDetailView.as_view(), name='admin-listing-detail'),
    path('orders/', AdminOrderListView.as_view(), name='admin-order-list'),
    path('orders/<uuid:pk>/', AdminOrderDetailView.as_view(), name='admin-order-detail'),
    path('orders/<uuid:pk>/force_cancel/', AdminOrderForceCancelView.as_view(), name='admin-order-force-cancel'),
    path('schools/', AdminSchoolListView.as_view(), name='admin-school-list'),
    path('schools/bulk/', AdminSchoolBulkImportView.as_view(), name='admin-school-bulk'),
    path('schools/<int:pk>/', AdminSchoolDetailView.as_view(), name='admin-school-detail'),
    path('categories/', AdminCategoryListView.as_view(), name='admin-categories-list'),
    path('categories/bulk/', AdminCategoryBulkImportView.as_view(), name='admin-categories-bulk'),
    path('categories/<int:pk>/', AdminCategoryDetailView.as_view(), name='admin-categories-detail'),
    path('chat-reports/', AdminChatReportListView.as_view(), name='admin-chat-report-list'),
    path('chat-reports/<uuid:pk>/', AdminChatReportDetailView.as_view(), name='admin-chat-report-detail'),
    path('chat-reports/<uuid:pk>/chat-token/', AdminChatReportTokenView.as_view(), name='admin-chat-report-token'),
    # Routes renamed from 'advertisers/' and 'ads/' to 'sponsors/' and 'promotions/' to evade adblockers
    path('sponsors/', AdminAdvertiserListView.as_view(), name='admin-advertiser-list'),
    path('sponsors/<int:pk>/', AdminAdvertiserDetailView.as_view(), name='admin-advertiser-detail'),
    path('promotions/', AdminAdListView.as_view(), name='admin-ad-list'),
    # NOTE: upload routes must be listed before <int:pk> to avoid any future routing ambiguity
    path('promotions/uploads/presign/', AdminAdUploadURLView.as_view(), name='admin-ad-upload-presign'),
    path('promotions/uploads/direct/', AdminAdUploadDirectView.as_view(), name='admin-ad-upload-direct'),
    path('promotions/<int:pk>/', AdminAdDetailView.as_view(), name='admin-ad-detail'),
]
