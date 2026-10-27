from django import template


register = template.Library()


@register.filter
def yen(value):
    if value is None:
        return "未取込"
    return f"{int(value):,}円"


@register.filter
def jp_month(value):
    if not value:
        return ""
    return f"{value.year}年{value.month}月"
