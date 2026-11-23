from django.urls import path

from . import views as v

app_name = "backoffice"
urlpatterns = [
    path("login/", v.StaffLoginView.as_view(), name="login"),
    path("logout/", v.StaffLogoutView.as_view(), name="logout"),
    path("", v.DashboardView.as_view(), name="dashboard"),
    path("leads/", v.LeadListView.as_view(), name="leads"),
    path("leads/<int:pk>/", v.LeadDetailView.as_view(), name="lead"),
    path("quotes/", v.QuoteListView.as_view(), name="quotes"),
    path("quotes/new/", v.QuoteEditView.as_view(), name="quote_new"),
    path("quotes/<int:pk>/", v.QuoteDetailView.as_view(), name="quote"),
    path("quotes/<int:pk>/edit/", v.QuoteEditView.as_view(), name="quote_edit"),
    path("quotes/<int:pk>/pdf/", v.QuotePdf.as_view(), name="quote_pdf"),
    path("quotes/<int:pk>/send/", v.QuoteSend.as_view(), name="quote_send"),
    path("quotes/<int:pk>/cancel/", v.QuoteCancel.as_view(), name="quote_cancel"),
    path("quotes/<int:pk>/duplicate/", v.QuoteDuplicate.as_view(), name="quote_duplicate"),
    path("customers/", v.CustomerListView.as_view(), name="customers"),
    path("customers/<int:pk>/", v.CustomerDetailView.as_view(), name="customer"),
    path("subscriptions/", v.SubscriptionListView.as_view(), name="subscriptions"),
    path("subscriptions/<int:pk>/", v.SubscriptionDetailView.as_view(), name="subscription"),
    path("subscriptions/<int:pk>/pause/", v.SubscriptionPause.as_view(), name="subscription_pause"),
    path("subscriptions/<int:pk>/resume/", v.SubscriptionResume.as_view(), name="subscription_resume"),
    path("subscriptions/<int:pk>/cancel/", v.SubscriptionCancel.as_view(), name="subscription_cancel"),
    path("subscriptions/<int:pk>/refund/", v.RefundRaise.as_view(), name="subscription_refund"),
    path("refunds/", v.RefundListView.as_view(), name="refunds"),
    path("refunds/<int:pk>/<str:action>/", v.RefundAction.as_view(), name="refund_action"),
]
