from django import forms

from leads.forms import validate_phone


class SubscribeForm(forms.Form):
    name = forms.CharField(max_length=120, label="Your name", widget=forms.TextInput(attrs={"autocomplete": "name"}))
    business_name = forms.CharField(
        max_length=160,
        label="Funeral parlour or business name",
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    email = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(
        max_length=30,
        label="Phone or WhatsApp number",
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    accept = forms.BooleanField(required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "accept":
                field.widget.attrs["class"] = "field"

    def clean_phone(self):
        return validate_phone(self.cleaned_data["phone"])
