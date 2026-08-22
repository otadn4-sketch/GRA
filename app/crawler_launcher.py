"""Launch the installed Selenium crawler with the duplicate-send guard enabled."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from .config import PROJECT_ROOT
from .crawler_send_guard import install


def crawler_script_path() -> Path:
    return PROJECT_ROOT / "crawler" / "bale_crawler_api_sender.py"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    script = crawler_script_path()
    if not script.is_file():
        print("فایل اجرایی کرولر پیدا نشد.", file=sys.stderr)
        return 2
    install()
    sys.argv = [str(script), *args]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
