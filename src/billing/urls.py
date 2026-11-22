from django.urls import path

from . import views

app_name = "billing"
urlpatterns = [
    path("subscribe/<slug:plan>/", views.SubscribeView.as_view(), name="subscribe"),
    path("subscribe/pay/<str:reference>/", views.PayView.as_view(), name="pay"),
    path("subscribe/return/<str:reference>/", views.ReturnView.as_view(), name="return"),
    path("subscribe/cancelled/<str:reference>/", views.CancelledView.as_view(), name="cancelled"),
    path("billing/payfast/itn/", views.PayFastITNView.as_view(), name="payfast_itn"),
]
