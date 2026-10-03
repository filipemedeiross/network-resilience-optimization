import json

from uuid import UUID, uuid4
from json import JSONDecodeError

from django.urls                  import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET      , require_POST
from django.shortcuts             import get_object_or_404, redirect    , render

from django.db.models import Count  , Q
from django.http      import Http404, JsonResponse

from .models import Play            , \
                    Scenario        , \
                    ScenarioRevision, \
                    SolverResult

from .services.gameplay import PlayNotFound                         , \
                               SolutionUnavailable                  , \
                               VersionConflict                      , \
                               create_next_play                     , \
                               restart_play as create_restarted_play, \
                               reveal_play                          , \
                               submit_attempt

from .services.evaluation import InvalidSelection
from .services.instances  import InstanceProvisionError
from .services.generation import DEFAULT_MILITARY_BUDGET


VISITOR_SESSION_KEY = "visitor_id"


class ApiInputError(ValueError):
    """A client error that is safe to return through the JSON API."""


def _visitor_id(request, *, create=False):
    raw_visitor_id = request.session.get(VISITOR_SESSION_KEY)

    if raw_visitor_id:
        try:
            return UUID(str(raw_visitor_id))
        except (TypeError, ValueError, AttributeError):
            request.session.pop(VISITOR_SESSION_KEY, None)

    if not create:
        return None

    visitor_id = uuid4()

    request.session[VISITOR_SESSION_KEY] = str(visitor_id)

    return visitor_id


def _read_json_object(request):
    if request.content_type != "application/json":
        raise ApiInputError("Envie o corpo como application/json.")

    try:
        payload = json.loads(request.body or b"{}")
    except (JSONDecodeError, UnicodeDecodeError):
        raise ApiInputError("O corpo da requisição não contém JSON válido.")

    if not isinstance(payload, dict):
        raise ApiInputError("O corpo JSON precisa ser um objeto.")

    return payload


def _required_uuid(payload, key):
    try:
        return UUID(str(payload[key]))
    except KeyError:
        raise ApiInputError(f"O campo '{key}' é obrigatório.")
    except (TypeError, ValueError, AttributeError):
        raise ApiInputError(f"O campo '{key}' precisa ser um UUID válido.")


def _required_version(payload):
    try:
        version = payload["expected_version"]
    except KeyError:
        raise ApiInputError("O campo 'expected_version' é obrigatório.")

    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ApiInputError("O campo 'expected_version' precisa ser um inteiro não negativo.")

    return version


def _error_response(code, message, *, status, **details):
    error = {
        "code"    : code   ,
        "message" : message,
    }

    error.update(details)

    return JsonResponse({"error": error}, status=status)


def _conflict_response(exc):
    return _error_response(
        "version_conflict"                                                         ,
        "A partida mudou em outra requisição. Atualize o estado e tente novamente.",

        status         =409,
        current_version=exc.current_version,
        current_status =exc.current_status ,
        revealed       =exc.revealed       ,
    )


def _instance_error_response(
    request,
    *,

    message  ,
    retry_url,
    back_url ,
):
    response = render(
        request                    ,
        "games/instance_error.html",

        {
            "message"   : message  ,
            "retry_url" : retry_url,
            "back_url"  : back_url ,
        },

        status=503,
    )

    response["Retry-After"] = "2"

    return response


@require_GET
def scenario_list(request):
    cached_optimum = Q(
        revisions__publication_status__in=(
            ScenarioRevision.PublicationStatus.PUBLISHED,
            ScenarioRevision.PublicationStatus.SOLVING  ,
        ),
        revisions__solver_results__status  =SolverResult.Status.OPTIMAL,
        revisions__solver_results__verified=True                       ,
    )

    scenarios = list(
        Scenario
        .objects
        .filter  (active=True)
        .annotate(
            cached_instance_count=Count(
                "revisions__content_hash",
                filter  =cached_optimum,
                distinct=True          ,
            )
        )
        .order_by("title")
    )

    for scenario in scenarios:
        scenario.default_budget = (
            DEFAULT_MILITARY_BUDGET
            if   scenario.kind == Scenario.Kind.MILITARY
            else None
        )

    return render(request, "games/scenario_list.html", {"scenarios": scenarios})


@require_POST
def start_play(request, slug):
    scenario   = get_object_or_404(
        Scenario,
        slug  =slug,
        active=True,
    )

    visitor_id = _visitor_id(request, create=True)

    try:
        play = create_next_play(
            scenario_id=scenario.pk,
            visitor_id =visitor_id ,
        )
    except InstanceProvisionError as exc:
        return _instance_error_response(
            request,

            message  =exc.public_message,
            retry_url=reverse("games:start-play"   , kwargs={"slug": scenario.slug}),
            back_url =reverse("games:scenario-list"                                ),
        )
    except SolutionUnavailable:
        return _instance_error_response(
            request,
            message  ="Não foi possível criar a partida com a solução verificada."  ,
            retry_url=reverse("games:start-play"   , kwargs={"slug": scenario.slug}),
            back_url =reverse("games:scenario-list"                                ),
        )

    return redirect("games:play-detail", play_id=play.pk)


@require_POST
def restart_play(request, play_id):
    visitor_id = _visitor_id(request)

    if visitor_id is None:
        raise Http404("Partida não encontrada.")

    try:
        play = create_restarted_play(play_id=play_id, visitor_id=visitor_id)
    except PlayNotFound:
        raise Http404("Partida não encontrada.")
    except InstanceProvisionError as exc:
        return _instance_error_response(
            request,

            message  =exc.public_message,
            retry_url=reverse("games:restart-play", kwargs={"play_id": play_id}),
            back_url =reverse("games:play-detail" , kwargs={"play_id": play_id}),
        )
    except SolutionUnavailable:
        return _instance_error_response(
            request,
            message  ="Não foi possível criar a nova partida."                  ,
            retry_url=reverse("games:restart-play", kwargs={"play_id": play_id}),
            back_url =reverse("games:play-detail" , kwargs={"play_id": play_id}),
        )

    return redirect("games:play-detail", play_id=play.pk)


@ensure_csrf_cookie
@require_GET
def play_detail(request, play_id):
    visitor_id = _visitor_id(request)

    if visitor_id is None:
        raise Http404("Partida não encontrada.")

    play = get_object_or_404(
        Play.objects.select_related("revision__scenario"),

        pk        =play_id   ,
        visitor_id=visitor_id,
    )

    revision = play.revision

    game_payload = {
        "kind"         : revision.scenario.kind,
        "topology"     : revision.topology     ,
        "rules"        : revision.rules        ,
        "presentation" : revision.presentation ,
    }
    context = {
        "play"         : play             ,
        "scenario"     : revision.scenario,
        "revision"     : revision         ,
        "game_payload" : game_payload     ,
    }

    return render(request, "games/play_detail.html", context)


@require_POST
def create_attempt(request, play_id):
    visitor_id = _visitor_id(request)

    if visitor_id is None:
        return _error_response(
            "play_not_found", "Partida não encontrada.", status=404
        )

    try:
        payload          = _read_json_object(request)
        request_id       = _required_uuid   (payload, "request_id")
        expected_version = _required_version(payload)

        if "selection" not in payload:
            raise ApiInputError("O campo 'selection' é obrigatório.")

        selection = payload["selection"]

        if not isinstance(selection, dict):
            raise ApiInputError("O campo 'selection' precisa ser um objeto.")

        attempt = submit_attempt(
            play_id         =play_id         ,
            visitor_id      =visitor_id      ,
            request_id      =request_id      ,
            expected_version=expected_version,
            selection       =selection       ,
        )
    except ApiInputError as exc:
        return _error_response("invalid_request", str(exc), status=400)
    except InvalidSelection as exc:
        return _error_response("invalid_selection", str(exc), status=400)
    except VersionConflict as exc:
        return _conflict_response(exc)
    except PlayNotFound:
        return _error_response(
            "play_not_found", "Partida não encontrada.", status=404
        )
    except SolutionUnavailable:
        return _error_response(
            "solution_unavailable"                                   ,
            "A solução verificada deste cenário não está disponível.",
            status=409,
        )

    play = attempt.play

    return JsonResponse(
        {
            "attempt" : {
                "id"         : str(attempt.pk        ),
                "request_id" : str(attempt.request_id),

                "committed_version" : attempt.committed_version,
                "selection"         : attempt.selection        ,
                "result"            : attempt.result           ,
            },
            "play": {
                "id" : str(play.pk),

                "version" : play.version,
                "status"  : play.status ,
            },
        }
    )


@require_POST
def reveal_solution(request, play_id):
    visitor_id = _visitor_id(request)

    if visitor_id is None:
        return _error_response(
            "play_not_found", "Partida não encontrada.", status=404
        )

    try:
        payload          = _read_json_object(request)
        expected_version = _required_version(payload)
        play             = reveal_play(
            play_id         =play_id         ,
            visitor_id      =visitor_id      ,
            expected_version=expected_version,
        )
    except ApiInputError as exc:
        return _error_response("invalid_request", str(exc), status=400)
    except VersionConflict as exc:
        return _conflict_response(exc)
    except PlayNotFound:
        return _error_response(
            "play_not_found", "Partida não encontrada.", status=404
        )
    except SolutionUnavailable:
        return _error_response(
            "solution_unavailable"                                   ,
            "A solução verificada deste cenário não está disponível.",

            status=409,
        )

    solver_result = play.solver_result

    if solver_result is None:
        return _error_response(
            "solution_unavailable"                                   ,
            "A solução verificada deste cenário não está disponível.",

            status=409,
        )

    return JsonResponse(
        {
            "play" : {
                "id" : str(play.pk),

                "version"  : play.version ,
                "status"   : play.status  ,
                "revealed" : play.revealed,
            },

            "solution" : {
                "objective_value" : solver_result.objective_value,
                "selection"       : solver_result.solution       ,
            },
        }
    )
