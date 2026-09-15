# -*- coding: utf-8 -*-
"""CJK 字体渲染支持模块（对应设计文档第三十二章）。

在程序启动时扫描系统可用字体，按优先级队列选择第一个可用的
CJK 字体作为全局 UI 字体，保证中文/日文界面文字正确渲染。
"""
import tkinter.font as tkfont


# CJK 字体优先级队列（设计文档 32.1 节）
PREFERRED_FONTS = [
    "Noto Sans CJK SC",        # 简体中文，覆盖最全CJK统一表意文字
    "Noto Sans CJK TC",        # 繁体中文，补充台湾/香港字形
    "Noto Sans CJK JP",        # 日文，补充假名和日文汉字
    "Microsoft YaHei UI",      # Windows 简体后备
    "Microsoft JhengHei UI",   # Windows 繁体后备
    "Yu Gothic UI",            # Windows 日文后备
]

# 检测失败时的静默回退字体
FALLBACK_FONT = "Microsoft YaHei UI"


def scan_system_fonts():
    """扫描系统可用字体族列表。

    返回:
        可用字体族名称集合（set）；检测失败时返回空集合。
    """
    try:
        return set(tkfont.families())
    except Exception:
        return set()


def first_available(preferred, available_fonts):
    """从优先级队列中选出第一个可用字体。

    参数:
        preferred: 按优先级排列的字体名称列表
        available_fonts: 系统可用字体族集合
    返回:
        第一个可用的字体名称；全部不可用时返回 None
    """
    for name in preferred:
        if name in available_fonts:
            return name
    return None


def init_cjk_fonts(root):
    """初始化全局 CJK 字体（设计文档 32.2 节）。

    扫描系统可用字体，按优先级队列选择第一个可用字体，
    通过 root.option_add 设置为全局默认字体。

    参数:
        root: tkinter 根窗口（Tk 实例）
    返回:
        config 风格的字体配置字典：
        {
            "ui": (选中字体, 10),
            "title": (选中字体, 12, "bold"),
            "metadata": (选中字体, 9),
            "log": ("Consolas", 9),
        }
    """
    available_fonts = scan_system_fonts()
    selected = first_available(PREFERRED_FONTS, available_fonts)
    if not selected:
        # 全部优先级字体都不可用，静默回退 Windows 简体后备
        selected = FALLBACK_FONT

    # 设置 tkinter 全局默认字体
    try:
        root.option_add("*Font", (selected, 10))
    except Exception:
        # option_add 失败（如无显示环境）时静默忽略，仅返回配置
        pass

    fonts_config = {
        "ui": (selected, 10),
        "title": (selected, 12, "bold"),
        "metadata": (selected, 9),
        "log": ("Consolas", 9),
    }
    return fonts_config
