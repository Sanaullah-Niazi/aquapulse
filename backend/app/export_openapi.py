"""Write the OpenAPI document to backend/openapi.json: `make openapi`.

The file is committed, so changes to the API show up in pull requests,
and a test fails if someone forgets to regenerate it (FR-29).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    # Building the document needs no database, but the settings must exist.
    for name in ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "JWT_SECRET"):
        os.environ.setdefault(name, "not-used-for-export")
    from .main import app

    TARGET.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {TARGET.name}")


if __name__ == "__main__":
    main()
