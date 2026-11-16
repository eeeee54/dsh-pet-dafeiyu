# -*- coding: utf-8 -*-
"""离屏构建设置页，确认新增控件不报错且默认值正确。"""
import sys

sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication

from pet.chat.ai_settings_page import _AiSettingsPage
from pet.config import Config

app = QApplication([])
config = Config()
page = _AiSettingsPage(config)
print("设置页构建 OK")
print("语义强度控件:", page.chat_memory_semantic_weight.currentData())
print("向量来源控件:", page.chat_memory_embed_provider.currentData())
print("知识库开关:", page.chat_memory_knowledge_enabled.isChecked())
print("知识库按钮:", page.chat_knowledge_manage_btn.text())
