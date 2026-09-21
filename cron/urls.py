from django.urls import path
from cron import views

urlpatterns = [
    path('waitlist-notify/', views.WaitlistNotifyView.as_view(), name='cron-waitlist-notify'),
    path('meetup-reminder/', views.MeetupReminderView.as_view(), name='cron-meetup-reminder'),
    path('cleanup/', views.CleanupView.as_view(), name='cron-cleanup'),
]
