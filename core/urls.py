from django.contrib.auth import views as auth_views
from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("facilities/", views.facilities, name="facilities"),
    path("facilities/<uuid:pk>/", views.facility_detail, name="facility_detail"),
    path("facilities/<uuid:pk>/edit/", views.facility_edit, name="facility_edit"),
    path("facilities/<uuid:pk>/toggle-deleted/", views.facility_toggle_deleted, name="facility_toggle_deleted"),
    path("groups/", views.groups, name="groups"),
    path("groups/<int:pk>/delete/", views.group_delete, name="group_delete"),
    path("employees/", views.employees, name="employees"),
    path("employees/<int:pk>/", views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/toggle-hidden/", views.employee_toggle_hidden, name="employee_toggle_hidden"),
    path("employees/<int:pk>/assignments/<int:assignment_pk>/delete/", views.assignment_delete, name="assignment_delete"),
    path("employees/<int:pk>/assignments/impact/", views.assignment_impact, name="assignment_impact"),
    path("imports/", views.imports, name="imports"),
    path("imports/jobs/<int:pk>/", views.import_job, name="import_job"),
    path("imports/jobs/<int:pk>/commit/", views.import_commit, name="import_commit"),
    path("users/", views.users, name="users"),
    path("api/me", views.api_me),
    path("api/dashboard", views.api_dashboard),
    path("api/facilities/<uuid:pk>/metrics", views.api_facility_metrics),
    path("api/imports/jobs/<int:pk>", views.api_import_job),
]
