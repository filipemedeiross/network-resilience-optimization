from django.urls import include, path

from .views import health_live, health_ready


urlpatterns = [
    path("health/", health_ready, name="health"),
    path("health/live/", health_live, name="health-live"),
    path("health/ready/", health_ready, name="health-ready"),
    path("", include("games.urls")),
]
