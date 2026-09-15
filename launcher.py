# -*- coding: utf-8 -*-
"""程序启动器。

启动前检查数据库文件（constants.DB_FILE）：
- 不存在：创建 DatabaseManager 实例（其构造会自动建表）并显式
  调用 init_tables()，随后关闭连接；
- 已存在：跳过创建，由主程序自行打开。

然后导入 main 模块并调用 main() 启动主程序。
"""
import os


def ensure_database() -> bool:
    """确保数据库文件存在并完成建表。

    返回:
        True 表示本次创建了数据库；False 表示数据库已存在。
    """
    from constants import DB_FILE

    if os.path.exists(DB_FILE):
        return False

    from db_manager import DatabaseManager
    db_manager = DatabaseManager(DB_FILE, device_id="")
    db_manager.init_tables()
    db_manager.close()
    return True


def main() -> None:
    """启动器入口：初始化数据库后启动主程序。"""
    ensure_database()

    import main as main_module
    main_module.main()


if __name__ == "__main__":
    main()
