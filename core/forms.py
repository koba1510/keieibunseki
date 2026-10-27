from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import Employee, EmployeeAssignment, Facility, FacilityAlias, FacilityGroup


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class FacilityGroupForm(StyledModelForm):
    class Meta:
        model = FacilityGroup
        fields = ["name"]


class FacilityForm(StyledModelForm):
    class Meta:
        model = Facility
        fields = ["official_name", "pos_store_code", "type", "facility_group", "ownership", "status"]


class FacilityAliasForm(StyledModelForm):
    class Meta:
        model = FacilityAlias
        fields = ["source_kind", "source_name"]


class EmployeeForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = ["employee_code", "name"]


class EmployeeAssignmentForm(StyledModelForm):
    effective_month = forms.DateField(
        label="開始月", input_formats=["%Y-%m"], widget=forms.DateInput(attrs={"type": "month"})
    )

    class Meta:
        model = EmployeeAssignment
        fields = ["effective_month", "facility"]


class AdminUserCreationForm(UserCreationForm):
    email = forms.EmailField(label="メールアドレス", required=True)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "email")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"].lower()
        user.email = self.cleaned_data["email"].lower()
        if commit:
            user.save()
        return user
