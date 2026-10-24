"""galtl —— galgame 日语字幕实时翻译 / 语法解析工具。

纯本地运行：
  * 截屏 -> Windows 内置 OCR（离线，日语引擎）-> 文本
  * 文本 -> OpenAI 兼容大模型 -> 中文翻译 + 语法解析
  * 结果 -> 置顶悬浮窗（可点击穿透）
仅依赖 Python 标准库；numpy 若存在则用于加速缩放与二值化。
"""

__version__ = "1.0.15"
APP_NAME = "galgame 日语助手"
