"""全局热键映射（key = 虚拟键码）。"""
from __future__ import annotations

from . import winapi

HOTKEY_MAP: dict[int, str] = {
    winapi.VK_F6: "F6",    # 鼠标穿透开关
    winapi.VK_F7: "F7",    # 框选区域
    winapi.VK_F8: "F8",    # 自动翻译开关
    winapi.VK_F9: "F9",    # 立即翻译
    winapi.VK_F10: "F10",  # 字幕显隐
    winapi.VK_F11: "F11",  # 覆盖原文（中文盖住日文）
}
