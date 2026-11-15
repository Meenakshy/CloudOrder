from django.http import JsonResponse
from django.urls import include, path
from strawberry.django.views import GraphQLView

from cloudorder.orders.api.graphql import schema


def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health, name="health"),
    path("api/v1/", include("cloudorder.orders.api.urls")),
    path("graphql/", GraphQLView.as_view(schema=schema), name="graphql"),
]