from django.urls import path

from . import views


app_name = "games"


urlpatterns = [
    path(""                        , views.scenario_list, name="scenario-list"),
    path("games/<slug:slug>/start/", views.start_play   , name="start-play"   ),

    path("plays/<uuid:play_id>/"        , views.play_detail , name="play-detail" ),
    path("plays/<uuid:play_id>/restart/", views.restart_play, name="restart-play"),

    path("api/plays/<uuid:play_id>/attempts/", views.create_attempt , name="create-attempt" ),
    path("api/plays/<uuid:play_id>/reveal/"  , views.reveal_solution, name="reveal-solution"),
]
