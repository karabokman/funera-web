from django.urls import path

from . import portal_views as portal
from . import views

app_name = "billing"
urlpatterns = [
    path("subscribe/<slug:plan>/", views.SubscribeView.as_view(), name="subscribe"),
    path("subscribe/pay/<str:reference>/", views.PayView.as_view(), name="pay"),
    path("subscribe/return/<str:reference>/", views.ReturnView.as_view(), name="return"),
    path("subscribe/cancelled/<str:reference>/", views.CancelledView.as_view(), name="cancelled"),
    path("billing/payfast/itn/", views.PayFastITNView.as_view(), name="payfast_itn"),
    path("portal/", portal.DashboardView.as_view(), name="portal"),
    path("portal/login/", portal.LoginView.as_view(), name="portal_login"),
    path("portal/login/<str:token>/", portal.LoginConfirmView.as_view(), name="portal_login_confirm"),
    path("portal/logout/", portal.LogoutView.as_view(), name="portal_logout"),
    path("portal/subscriptions/<str:reference>/", portal.SubscriptionView.as_view(), name="portal_subscription"),
    path("portal/subscriptions/<str:reference>/pause/", portal.PauseView.as_view(), name="portal_pause"),
    path("portal/subscriptions/<str:reference>/resume/", portal.ResumeView.as_view(), name="portal_resume"),
    path("portal/subscriptions/<str:reference>/switch/", portal.SwitchView.as_view(), name="portal_switch"),
    path("portal/subscriptions/<str:reference>/cancel/", portal.CancelView.as_view(), name="portal_cancel"),
    path("portal/payments/<str:pf_payment_id>/refund/", portal.RefundRequestView.as_view(), name="portal_refund"),
]
