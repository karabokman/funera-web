from django.contrib.sitemaps.views import sitemap
from django.urls import path

from . import views
from .sitemaps import StaticPagesSitemap

app_name = "pages"
urlpatterns = [
    path("", views.Home.as_view(), name="home"),
    path("features/", views.Features.as_view(), name="features"),
    path("pricing/", views.Pricing.as_view(), name="pricing"),
    path("security/", views.Security.as_view(), name="security"),
    path("privacy/", views.Privacy.as_view(), name="privacy"),
    path("terms/", views.Terms.as_view(), name="terms"),
    path("subscription-terms/", views.SubscriptionTerms.as_view(), name="subscription_terms"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": {"static": StaticPagesSitemap}}, name="sitemap"),
    path("healthz/", views.healthz, name="healthz"),
]
