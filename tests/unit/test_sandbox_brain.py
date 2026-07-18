"""Sandbox result contract + Brain fallback (no docker/LLM needed)."""

from app.orchestration.brain import Route, RouteDecision
from app.tools.sandbox.base import ErrorKind, SandboxResult


def test_error_kind_semantics():
    code = SandboxResult(ok=False, error_kind=ErrorKind.CODE, stderr="Traceback...")
    infra = SandboxResult(ok=False, error_kind=ErrorKind.INFRA, stderr="docker gone")
    # code errors are retryable (regenerate), infra errors are terminal
    assert code.error_kind == ErrorKind.CODE
    assert infra.error_kind == ErrorKind.INFRA
    assert code.error_kind != infra.error_kind


def test_route_decision_defaults():
    d = RouteDecision(routes=[Route.RAG], reason="corpus question", confidence=0.8,
                      search_query="pricing coverage")
    assert Route.RAG in d.routes
    assert d.search_query == "pricing coverage"


def test_route_enum_values():
    assert {r.value for r in Route} == {"MEMORY", "RAG", "WEB", "LLM"}
