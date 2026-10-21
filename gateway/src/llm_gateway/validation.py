import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .errors import ErrorCode, GatewayError


def validate_schema(schema: dict) -> Draft202012Validator:
    if schema.get("type") != "object":
        raise GatewayError(
            ErrorCode.INVALID_SCHEMA, "The root schema must be an object", status=422
        )
    if len(json.dumps(schema).encode()) > 40_000:
        raise GatewayError(ErrorCode.INVALID_SCHEMA, "The output schema is too large", status=422)

    def inspect(node: Any, depth: int = 0) -> None:
        if depth > 24:
            raise GatewayError(
                ErrorCode.INVALID_SCHEMA, "The output schema is too deep", status=422
            )
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"$dynamicRef", "$recursiveRef", "$id"} or (
                    key == "$ref" and (not isinstance(item, str) or not item.startswith("#/"))
                ):
                    raise GatewayError(
                        ErrorCode.INVALID_SCHEMA,
                        "Only bounded local schema references are allowed",
                        status=422,
                    )
                inspect(item, depth + 1)
        elif isinstance(node, list):
            for item in node:
                inspect(item, depth + 1)

    inspect(schema)
    inline_schema(schema)  # Validate targets, cycles and expansion limits before any provider call.
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise GatewayError(
            ErrorCode.INVALID_SCHEMA, "The supplied JSON Schema is invalid", status=422
        ) from None
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def inline_schema(schema: dict) -> dict:
    """Expand Pydantic local definitions without network/file resolution or recursion."""
    visited_nodes = 0

    def expand(node: Any, stack: tuple[str, ...] = (), depth: int = 0) -> Any:
        nonlocal visited_nodes
        visited_nodes += 1
        if visited_nodes > 3_000 or depth > 24:
            raise GatewayError(
                ErrorCode.INVALID_SCHEMA,
                "Schema reference expansion exceeded its limit",
                status=422,
            )
        if isinstance(node, list):
            return [expand(item, stack, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            reference = node["$ref"]
            if (
                reference in stack
                or not isinstance(reference, str)
                or not reference.startswith("#/")
            ):
                raise GatewayError(
                    ErrorCode.INVALID_SCHEMA,
                    "Recursive or external references are disabled",
                    status=422,
                )
            target: Any = schema
            try:
                for component in reference[2:].split("/"):
                    target = target[component.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError, IndexError):
                raise GatewayError(
                    ErrorCode.INVALID_SCHEMA,
                    "Local schema reference target does not exist",
                    status=422,
                ) from None
            expanded = expand(target, (*stack, reference), depth + 1)
            siblings = {key: item for key, item in node.items() if key != "$ref"}
            if siblings:
                # Preserve JSON Schema sibling semantics instead of overwriting target constraints.
                return {"allOf": [expanded, expand(siblings, stack, depth + 1)]}
            return expanded
        return {
            key: expand(item, stack, depth + 1)
            for key, item in node.items()
            if key not in {"$defs", "definitions"}
        }

    result = expand(schema)
    if len(json.dumps(result).encode()) > 80_000:
        raise GatewayError(
            ErrorCode.INVALID_SCHEMA, "Expanded output schema is too large", status=422
        )
    return result


def validate_output(value: Any, validator: Draft202012Validator) -> dict:
    if not isinstance(value, dict) or not validator.is_valid(value):
        raise GatewayError(
            ErrorCode.INVALID_OUTPUT,
            "Provider output failed the required JSON Schema",
            retryable=True,
        )
    return value
