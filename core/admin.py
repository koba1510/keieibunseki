from django.contrib import admin

from .models import (
    DatasetImport,
    Employee,
    EmployeeAssignment,
    Facility,
    FacilityAlias,
    FacilityGroup,
    ImportJob,
    MonthlyPayroll,
    MonthlyRoyalty,
    MonthlySales,
    MonthlySupplies,
)


admin.site.register(
    [
        FacilityGroup,
        Facility,
        FacilityAlias,
        Employee,
        EmployeeAssignment,
        MonthlySales,
        MonthlyRoyalty,
        MonthlyPayroll,
        MonthlySupplies,
        DatasetImport,
        ImportJob,
    ]
)

# Register your models here.
