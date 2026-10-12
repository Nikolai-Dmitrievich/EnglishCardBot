# src/ai/utils.py
from typing import Any, Protocol

from pydantic import BaseModel


class MCPToolResult(Protocol):
    data: Any


def extract_tool_data(result: MCPToolResult) -> Any:
    if result is None:
        return None
    data = result.data

    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")

    if isinstance(data, list):
        return [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in data
        ]

    return data


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
