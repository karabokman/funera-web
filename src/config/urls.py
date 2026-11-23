from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Funera website admin"
admin.site.site_title = "Funera admin"
admin.site.index_title = "Enquiries and plans"

handler404 = "pages.views.not_found"
handler500 = "pages.views.server_error"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("pages.urls")),
    path("", include("leads.urls")),
    path("", include("billing.urls")),
    path("", include("quotes.urls")),
    path(settings.STAFF_URL, include("backoffice.urls")),
]
