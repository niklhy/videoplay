#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序启动器 - 自动检查并初始化数据库
"""
import os
import sys
import subprocess


def check_and_init_db():
    """检查数据库是否存在，不存在则自动初始化"""
    db_path = os.path.join(os.path.dirname(__file__), "media_library.db")
    init_script = os.path.join(os.path.dirname(__file__), "init_database.py")
    
    # 检查数据库文件是否存在
    if not os.path.exists(db_path):
        print("首次运行，正在初始化数据库...")
        if os.path.exists(init_script):
            result = subprocess.run([sys.executable, init_script], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"数据库初始化失败: {result.stderr}")
                return False
            print(result.stdout)
            return True
        else:
            print(f"错误: 找不到数据库初始化脚本 {init_script}")
            return False
    return True


if __name__ == "__main__":
    # 检查/初始化数据库
    if not check_and_init_db():
        print("程序无法启动，请检查错误信息")
        input("按回车键退出...")
        sys.exit(1)
    
    # 导入并运行主程序
    try:
        from main import main
        main()
    except ImportError as e:
        print(f"导入主程序失败: {e}")
        print("请确保所有程序文件在同一目录下")
        input("按回车键退出...")
        sys.exit(1)
