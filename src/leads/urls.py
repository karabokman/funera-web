from django.urls import path

from . import views

app_name = "leads"
urlpatterns = [
    path("contact/", views.ContactView.as_view(), name="contact"),
    path("contact/thank-you/", views.ThanksView.as_view(), name="thanks"),
]
