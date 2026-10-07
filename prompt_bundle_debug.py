# -*- coding: utf-8 -*-
"""Public-manifest prompt dimensions; never persist prompt or source text."""
from __future__ import annotations

import json


def bundle_debug_json(bundle: dict) -> str:
    """Separate fixed and daily character counts without exposing either body."""
    summary = {k: v for k, v in bundle.items()
               if k not in ("developer_instructions", "user_payload",
                            "response_schema")}
    # Character counts are not token counts or escaped HTTP-body lengths.
    schema = bundle.get("response_schema")
    summary["request_component_chars"] = {
        "developer_instructions": len(bundle.get("developer_instructions") or ""),
        "user_payload": len(bundle.get("user_payload") or ""),
        "response_schema_json": len(json.dumps(
            schema, ensure_ascii=False, default=str)) if schema is not None else 0,
    }
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)
