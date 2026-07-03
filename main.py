"""本地开发入口。

等价于执行 `work-order-process` 命令，方便直接用 `uv run python main.py` 启动。
"""

from work_order_process.cli import main


if __name__ == "__main__":
    main()
