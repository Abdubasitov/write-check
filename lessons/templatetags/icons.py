"""{% icon "bell" %} — иконка Lucide из static/icons/sprite.svg (ISC, https://lucide.dev)."""
from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def icon(name, cls=""):
    return format_html(
        '<svg class="icon {}" aria-hidden="true" focusable="false"><use href="{}#{}"></use></svg>',
        cls,
        static("icons/sprite.svg"),
        name,
    )


@register.simple_tag
def icon_sprite_url():
    return static("icons/sprite.svg")
