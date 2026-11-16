"""知识库管理弹窗：导入、检索测试、删改、重建索引。

## 为什么是独立弹窗

设置页的 `SettingsPopupSurface` 规范面向"选择/切换"这类短交互，
而知识库要展示文档列表、导入进度、检索结果——是典型的**管理型**界面，
和 `ChatMemoryDialog` 同级。这里沿用同一套 QSS 变量与行高，视觉一致，
但不塞进设置页的 SettingRow 结构。

## 数据位置

``<config.dir>/kb/{docs,chunks}.json``，与记忆文件同盘同级，
用户备份/清理时是一整块，不会散落在系统目录。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .knowledge import KnowledgeBase

_HELP = (
    "知识库是「你主动喂给我的资料」，与对话记忆分开存放：\n"
    "· 记忆会随时间衰减、由对话自动提炼；知识库不会，只由你增删。\n"
    "· 支持 .md / .txt 导入，按标题与段落自动切片并建索引。\n"
    "· 对话时相关片段会自动注入；也可以在这里直接试检索。"
)


class KnowledgeDialog(QDialog):
    """知识库管理窗。"""

    def __init__(self, kb: KnowledgeBase, parent=None):
        super().__init__(parent)
        self.kb = kb
        self.setWindowTitle("知识库管理")
        self.resize(760, 620)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        help_label = QLabel(_HELP, self)
        help_label.setWordWrap(True)
        help_label.setProperty("variant", "muted")
        root.addWidget(help_label)

        row = QHBoxLayout()
        self.stats_label = QLabel("", self)
        row.addWidget(self.stats_label, 1)
        self.import_file_btn = QPushButton("导入文件…", self)
        self.import_file_btn.clicked.connect(self._import_file)
        row.addWidget(self.import_file_btn)
        self.import_dir_btn = QPushButton("导入目录…", self)
        self.import_dir_btn.clicked.connect(self._import_dir)
        row.addWidget(self.import_dir_btn)
        root.addLayout(row)

        self.doc_list = QListWidget(self)
        self.doc_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.doc_list.setMinimumHeight(180)
        root.addWidget(self.doc_list, 2)

        manage = QHBoxLayout()
        self.delete_btn = QPushButton("删除选中", self)
        self.delete_btn.clicked.connect(self._delete_selected)
        manage.addWidget(self.delete_btn)
        self.retag_btn = QPushButton("改标签…", self)
        self.retag_btn.clicked.connect(self._retag_selected)
        manage.addWidget(self.retag_btn)
        self.rebuild_btn = QPushButton("重建索引", self)
        self.rebuild_btn.clicked.connect(self._rebuild)
        manage.addWidget(self.rebuild_btn)
        self.clear_btn = QPushButton("清空知识库", self)
        self.clear_btn.clicked.connect(self._clear)
        manage.addWidget(self.clear_btn)
        manage.addStretch(1)
        root.addLayout(manage)

        search_row = QHBoxLayout()
        self.query_edit = QLineEdit(self)
        self.query_edit.setPlaceholderText("输入一句话，测试会命中哪些片段…")
        self.query_edit.returnPressed.connect(self._do_search)
        search_row.addWidget(self.query_edit, 1)
        self.topk_spin = QSpinBox(self)
        self.topk_spin.setRange(1, 10)
        self.topk_spin.setValue(3)
        search_row.addWidget(QLabel("Top", self))
        search_row.addWidget(self.topk_spin)
        self.search_btn = QPushButton("检索", self)
        self.search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self.search_btn)
        root.addLayout(search_row)

        self.result_view = QPlainTextEdit(self)
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("检索结果会显示在这里")
        self.result_view.setMinimumHeight(140)
        root.addWidget(self.result_view, 2)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.close_btn = QPushButton("关闭", self)
        self.close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)

    # ---------------------------------------------------------------- 刷新
    def refresh(self) -> None:
        stats = self.kb.stats()
        self.stats_label.setText(
            f"文档 {stats['documents']} 篇 · 切片 {stats['chunks']} 条 · "
            f"共 {stats['chars']} 字")
        current = self._selected_doc_id()
        self.doc_list.clear()
        for doc in reversed(stats["docs"]):
            tags = f"  [{', '.join(doc['tags'])}]" if doc["tags"] else ""
            item = QListWidgetItem(f"{doc['title']}{tags}    （{doc['chunks']} 片）")
            item.setData(Qt.ItemDataRole.UserRole, doc["id"])
            self.doc_list.addItem(item)
            if doc["id"] == current:
                self.doc_list.setCurrentItem(item)

    def _selected_doc_id(self) -> str:
        item = self.doc_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    # ---------------------------------------------------------------- 操作
    def _import_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的文件", "",
            "文本文件 (*.md *.txt *.json *.csv);;所有文件 (*.*)")
        if not paths:
            return
        added = 0
        for path in paths:
            if self.kb.add_file(path):
                added += 1
        self.refresh()
        QMessageBox.information(self, "导入完成", f"成功导入 {added} 个文件。")

    def _import_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择要导入的目录")
        if not directory:
            return
        docs = self.kb.add_directory(directory, pattern="*.md")
        docs += self.kb.add_directory(directory, pattern="*.txt")
        self.refresh()
        QMessageBox.information(self, "导入完成", f"成功导入 {len(docs)} 篇文档。")

    def _delete_selected(self) -> None:
        doc_id = self._selected_doc_id()
        if not doc_id:
            QMessageBox.information(self, "未选择", "请先在列表里选中一篇文档。")
            return
        if QMessageBox.question(self, "确认删除", "删除后该文档的所有切片都会移除，确定吗？"
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.kb.delete_document(doc_id)
        self.refresh()

    def _retag_selected(self) -> None:
        doc_id = self._selected_doc_id()
        if not doc_id:
            QMessageBox.information(self, "未选择", "请先在列表里选中一篇文档。")
            return
        from PySide6.QtWidgets import QInputDialog
        current = ""
        for doc in self.kb.docs:
            if doc.id == doc_id:
                current = ", ".join(doc.tags)
                break
        text, ok = QInputDialog.getText(self, "修改标签", "逗号分隔，留空表示无标签：", text=current)
        if not ok:
            return
        tags = [t.strip() for t in text.replace("，", ",").split(",") if t.strip()]
        self.kb.update_tags(doc_id, tags)
        self.refresh()

    def _rebuild(self) -> None:
        count = self.kb.rebuild_index()
        QMessageBox.information(self, "重建完成", f"已重建 {count} 条切片的向量索引。")

    def _clear(self) -> None:
        if QMessageBox.question(self, "确认清空", "将删除知识库全部文档与切片，不可撤销。确定吗？"
                                ) != QMessageBox.StandardButton.Yes:
            return
        count = self.kb.clear()
        self.refresh()
        QMessageBox.information(self, "已清空", f"删除了 {count} 篇文档。")

    def _do_search(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            return
        hits = self.kb.search(query, top_k=int(self.topk_spin.value()))
        if not hits:
            self.result_view.setPlainText("没有命中。可以试试更贴近文档内容的说法，"
                                          "或先确认文档已导入。")
            return
        lines = []
        for index, (score, chunk) in enumerate(hits, 1):
            body = chunk.text
            prefix = f"{chunk.title}："
            if body.startswith(prefix):
                body = body[len(prefix):]
            lines.append(f"[{index}] 相似度 {score:.3f} · {chunk.title}\n{body[:400]}\n")
        self.result_view.setPlainText("\n".join(lines))


def open_knowledge_dialog(config, parent=None) -> KnowledgeDialog:
    """按当前配置目录打开知识库（设置页与菜单共用这一个入口）。"""
    root = Path(getattr(config, "dir", ".") or ".")
    dialog = KnowledgeDialog(KnowledgeBase(root), parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dialog.show()
    return dialog
