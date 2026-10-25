"""初始化模拟数据、知识库与本地索引（离线，可重复运行）。

用法：python scripts/init_data.py [--reset]
  --reset  先清空种子拥有的业务表再重建（不影响审计表）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bootstrap import initialize  # noqa: E402
from app.settings import get_settings  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    result = initialize(get_settings(), reset=args.reset)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
