from __future__ import annotations

import json

from ..models import Report


def render_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, default=str) + "\n"
