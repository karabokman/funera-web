from django.urls import path

from . import views

app_name = "quotes"
urlpatterns = [
    path("quote/<str:token>/", views.PublicQuoteView.as_view(), name="public"),
    path("quote/<str:token>/pdf/", views.PublicQuotePdfView.as_view(), name="pdf"),
    path("quote/<str:token>/accept/", views.AcceptView.as_view(), name="accept"),
    path("quote/<str:token>/decline/", views.DeclineView.as_view(), name="decline"),
]
