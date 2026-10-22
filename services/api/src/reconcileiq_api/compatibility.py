from typing import Any, cast

from graphql import (
    IntrospectionQuery,
    build_client_schema,
    build_schema,
    introspection_from_schema,
    lexicographic_sort_schema,
    print_schema,
)


def normalized_introspection(data: dict[str, Any]) -> str:
    return print_schema(
        lexicographic_sort_schema(build_client_schema(cast(IntrospectionQuery, data)))
    )


def assert_graphql_contract(canonical: str, actual: dict[str, Any]) -> None:
    expected = introspection_from_schema(build_schema(canonical), descriptions=False)
    if normalized_introspection(actual) != normalized_introspection(cast(dict[str, Any], expected)):
        raise AssertionError("Served GraphQL schema differs from the reviewed canonical SDL.")
