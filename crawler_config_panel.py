# -*- coding: utf-8 -*-
"""爬虫配置面板（设计文档 30.5 节，紧凑实用版）。

以 ``tk.Frame`` 形式提供完整的爬虫配置界面：
- URL 库列表（增 / 删 / 改、占位符、轮换模式）；
- 提取规则列表（增 / 删 / 改、启用勾选，支持 snippet / css / regex / custom
  四种规则类型）；
- 测试区（输入番号测试 URL 连通性；选中规则对内置示例 HTML 测试提取效果）；
- 保存按钮（校验后经 ``CrawlerConfigManager.save_config`` 落盘）。

所有数据读写一律经 ``CrawlerConfigManager`` / ``ExtractionEngine``，
本模块不直接触碰 config.json。
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from crawler_config import (
    CrawlerConfig,
    ExtractionRule,
    RULE_TYPES,
    URL_MODE_ROTATE,
    URL_MODES,
)

# 规则类型的显示名称（中文界面用）
RULE_TYPE_LABELS = {
    "snippet": "代码段(snippet)",
    "css": "CSS选择器",
    "regex": "正则表达式",
    "custom": "自定义代码",
}

# URL 轮换模式的显示名称
URL_MODE_LABELS = {
    "rotate": "轮流使用(rotate)",
    "failover": "故障切换(failover)",
}

# 内置示例 HTML（供提取规则离线测试，含番号/日期/演员/标题常见结构）
SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>ABC-123 示例影片页面</title></head>
<body>
<h1 class="title">ABC-123 示例影片标题</h1>
<span class="release-date">2025-01-15</span>
<div id="genres">
<span class="genre"><a href="/actor/1">山田花子</a></span>
<span class="genre"><a href="/actor/2">佐藤美咲</a></span>
</div>
<span class="studio">示例发行商</span>
</body>
</html>"""


class CrawlerConfigPanel(tk.Frame):
    """爬虫配置面板。

    属性:
        manager: CrawlerConfigManager  爬虫配置管理器
        config: CrawlerConfig          当前正在编辑的配置（工作副本）
        url_listbox: tk.Listbox        URL 库列表
        rules_tree: ttk.Treeview       提取规则列表
        placeholder_var: tk.StringVar  URL 占位符变量
        mode_var: tk.StringVar         URL 轮换模式变量
        test_code_var: tk.StringVar    测试番号变量
        result_text: tk.Text           测试结果输出区
    """

    def __init__(self, parent: tk.Widget, crawler_config_manager) -> None:
        """初始化配置面板。

        参数:
            parent: tkinter 父容器
            crawler_config_manager: CrawlerConfigManager 实例
        """
        super().__init__(parent)
        self.manager = crawler_config_manager
        self.config = self.manager.load_config()

        self._build_ui()
        self._reload_ui()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """创建面板布局：左 URL 库 | 右 规则列表 + 测试区，底部保存栏。"""
        main_paned = tk.PanedWindow(self, orient=tk.HORIZONTAL,
                                    sashwidth=4, bd=0)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # ---- 左侧：URL 库 ----
        url_frame = tk.LabelFrame(main_paned, text="URL 库（含 {番号} 占位符）")
        main_paned.add(url_frame, minsize=220)

        self.url_listbox = tk.Listbox(url_frame, activestyle="dotbox",
                                      exportselection=False)
        url_scroll = ttk.Scrollbar(url_frame, orient="vertical",
                                   command=self.url_listbox.yview)
        self.url_listbox.configure(yscrollcommand=url_scroll.set)
        url_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.url_listbox.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                              padx=4, pady=4)
        self.url_listbox.bind("<Double-1>", lambda e: self._edit_url())

        url_btn_frame = tk.Frame(url_frame)
        url_btn_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))
        tk.Button(url_btn_frame, text="添加", width=6,
                  command=self._add_url).pack(side=tk.LEFT)
        tk.Button(url_btn_frame, text="编辑", width=6,
                  command=self._edit_url).pack(side=tk.LEFT, padx=4)
        tk.Button(url_btn_frame, text="删除", width=6,
                  command=self._remove_url).pack(side=tk.LEFT)

        opt_frame = tk.Frame(url_frame)
        opt_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 6))
        tk.Label(opt_frame, text="占位符:").grid(row=0, column=0, sticky="w")
        self.placeholder_var = tk.StringVar()
        tk.Entry(opt_frame, textvariable=self.placeholder_var,
                 width=12).grid(row=0, column=1, sticky="we", padx=(2, 8))
        tk.Label(opt_frame, text="模式:").grid(row=0, column=2, sticky="w")
        self.mode_var = tk.StringVar()
        mode_combo = ttk.Combobox(opt_frame, textvariable=self.mode_var,
                                  values=list(URL_MODE_LABELS.keys()),
                                  state="readonly", width=10)
        mode_combo.grid(row=0, column=3, sticky="w", padx=(2, 0))
        opt_frame.columnconfigure(1, weight=1)

        # ---- 右侧：规则 + 测试 ----
        right_frame = tk.Frame(main_paned)
        main_paned.add(right_frame, minsize=420)

        rules_frame = tk.LabelFrame(right_frame, text="提取规则")
        rules_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                         padx=2, pady=2)

        columns = ("enabled", "field_id", "field_name", "rule_type")
        self.rules_tree = ttk.Treeview(
            rules_frame, columns=columns, show="headings", height=8,
            selectmode="browse")
        self.rules_tree.heading("enabled", text="启用")
        self.rules_tree.heading("field_id", text="字段标识")
        self.rules_tree.heading("field_name", text="字段名称")
        self.rules_tree.heading("rule_type", text="类型")
        self.rules_tree.column("enabled", width=46, anchor=tk.CENTER,
                               stretch=False)
        self.rules_tree.column("field_id", width=90, anchor=tk.W)
        self.rules_tree.column("field_name", width=130, anchor=tk.W)
        self.rules_tree.column("rule_type", width=120, anchor=tk.W)
        rules_scroll = ttk.Scrollbar(rules_frame, orient="vertical",
                                     command=self.rules_tree.yview)
        self.rules_tree.configure(yscrollcommand=rules_scroll.set)
        rules_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.rules_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                             padx=4, pady=4)
        self.rules_tree.bind("<Double-1>", lambda e: self._edit_rule())

        rule_btn_frame = tk.Frame(rules_frame)
        rule_btn_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))
        tk.Button(rule_btn_frame, text="添加", width=6,
                  command=self._add_rule).pack(side=tk.LEFT)
        tk.Button(rule_btn_frame, text="编辑", width=6,
                  command=self._edit_rule).pack(side=tk.LEFT, padx=4)
        tk.Button(rule_btn_frame, text="删除", width=6,
                  command=self._remove_rule).pack(side=tk.LEFT)
        tk.Button(rule_btn_frame, text="启用/禁用", width=9,
                  command=self._toggle_rule).pack(side=tk.LEFT, padx=4)

        # ---- 测试区 ----
        test_frame = tk.LabelFrame(right_frame, text="测试区")
        test_frame.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

        test_top = tk.Frame(test_frame)
        test_top.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        tk.Label(test_top, text="测试番号:").pack(side=tk.LEFT)
        self.test_code_var = tk.StringVar(value="ABC-123")
        tk.Entry(test_top, textvariable=self.test_code_var,
                 width=14).pack(side=tk.LEFT, padx=(2, 8))
        tk.Button(test_top, text="测试网址连通性",
                  command=self._test_url).pack(side=tk.LEFT)
        tk.Button(test_top, text="测试提取规则(示例HTML)",
                  command=self._test_rule).pack(side=tk.LEFT, padx=4)

        self.result_text = tk.Text(test_frame, height=6, wrap=tk.WORD,
                                   bg="#1e1e1e", fg="#d4d4d4")
        result_scroll = ttk.Scrollbar(test_frame, orient="vertical",
                                      command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=result_scroll.set)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                              padx=4, pady=(0, 4))

        # ---- 底部保存栏 ----
        bottom_frame = tk.Frame(self)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)
        tk.Button(bottom_frame, text="保存配置",
                  command=self._save_config).pack(side=tk.RIGHT)
        tk.Button(bottom_frame, text="重置为默认",
                  command=self._reset_default).pack(side=tk.RIGHT, padx=4)

    # ------------------------------------------------------------------
    # 数据 -> UI
    # ------------------------------------------------------------------
    def _reload_ui(self) -> None:
        """把当前配置刷新到界面控件。"""
        self.config = self.manager.get_config()

        # URL 列表
        self.url_listbox.delete(0, tk.END)
        for url in self.config.url_library.get("urls", []) or []:
            self.url_listbox.insert(tk.END, str(url))

        # 占位符与模式
        self.placeholder_var.set(
            self.config.url_library.get("placeholder", "{番号}"))
        mode = self.config.url_library.get("mode", URL_MODE_ROTATE)
        self.mode_var.set(mode if mode in URL_MODES else URL_MODE_ROTATE)

        # 规则列表
        self._refresh_rules_tree()

    def _refresh_rules_tree(self) -> None:
        """刷新规则 Treeview（按规则在配置中的顺序显示）。"""
        self.rules_tree.delete(*self.rules_tree.get_children())
        for index, rule in enumerate(self.config.extraction_rules or []):
            self.rules_tree.insert(
                "", tk.END, iid=str(index),
                values=("✔" if rule.enabled else "✘",
                        rule.field_id, rule.field_name,
                        RULE_TYPE_LABELS.get(rule.rule_type,
                                             rule.rule_type)))

    def _sync_basic_options(self) -> None:
        """把界面上的占位符/模式写回配置工作副本。"""
        self.config.url_library["placeholder"] = self.placeholder_var.get()
        mode = self.mode_var.get()
        self.config.url_library["mode"] = (
            mode if mode in URL_MODES else URL_MODE_ROTATE)

    # ------------------------------------------------------------------
    # URL 库操作
    # ------------------------------------------------------------------
    def _get_urls(self) -> list:
        """获取配置中的 URL 列表（工作副本，可直接修改）。"""
        urls = self.config.url_library.get("urls")
        if not isinstance(urls, list):
            urls = []
            self.config.url_library["urls"] = urls
        return urls

    def _add_url(self) -> None:
        """添加一条 URL（simpledialog 输入）。"""
        url = simpledialog.askstring(
            "添加 URL", "请输入数据源网址（需含占位符，如 https://example.com/{番号}）：",
            parent=self.winfo_toplevel())
        if not url or not url.strip():
            return
        self._get_urls().append(url.strip())
        self.url_listbox.insert(tk.END, url.strip())

    def _edit_url(self) -> None:
        """编辑当前选中的 URL。"""
        selection = self.url_listbox.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先在列表中选中要编辑的网址",
                                parent=self.winfo_toplevel())
            return
        index = selection[0]
        urls = self._get_urls()
        old_url = urls[index] if index < len(urls) else ""
        url = simpledialog.askstring("编辑 URL", "修改数据源网址：",
                                     initialvalue=old_url,
                                     parent=self.winfo_toplevel())
        if not url or not url.strip():
            return
        urls[index] = url.strip()
        self.url_listbox.delete(index)
        self.url_listbox.insert(index, url.strip())

    def _remove_url(self) -> None:
        """删除当前选中的 URL。"""
        selection = self.url_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        urls = self._get_urls()
        if index < len(urls):
            del urls[index]
        self.url_listbox.delete(index)

    # ------------------------------------------------------------------
    # 提取规则操作
    # ------------------------------------------------------------------
    def _selected_rule(self):
        """获取 Treeview 当前选中的规则对象。

        返回:
            ExtractionRule 或 None
        """
        selection = self.rules_tree.selection()
        if not selection:
            return None
        try:
            index = int(selection[0])
        except (ValueError, IndexError):
            return None
        rules = self.config.extraction_rules or []
        if 0 <= index < len(rules):
            return rules[index]
        return None

    def _add_rule(self) -> None:
        """添加一条提取规则（弹出规则编辑对话框）。"""
        dialog = _RuleEditDialog(self.winfo_toplevel(), None)
        if dialog.result is None:
            return
        if not dialog.result.rule_id:
            dialog.result.rule_id = "rule_%d" % (
                len(self.config.extraction_rules or []) + 1)
        self.config.extraction_rules.append(dialog.result)
        self._refresh_rules_tree()

    def _edit_rule(self) -> None:
        """编辑当前选中的提取规则。"""
        rule = self._selected_rule()
        if rule is None:
            messagebox.showinfo("提示", "请先在列表中选中要编辑的规则",
                                parent=self.winfo_toplevel())
            return
        dialog = _RuleEditDialog(self.winfo_toplevel(), rule)
        if dialog.result is None:
            return
        # 保留原 rule_id，替换其他字段
        dialog.result.rule_id = rule.rule_id
        index = self.config.extraction_rules.index(rule)
        self.config.extraction_rules[index] = dialog.result
        self._refresh_rules_tree()

    def _remove_rule(self) -> None:
        """删除当前选中的提取规则。"""
        rule = self._selected_rule()
        if rule is None:
            return
        if not messagebox.askyesno(
                "确认", "确定删除规则「%s」吗？" % (rule.field_name or rule.field_id),
                parent=self.winfo_toplevel()):
            return
        self.config.extraction_rules.remove(rule)
        self._refresh_rules_tree()

    def _toggle_rule(self) -> None:
        """切换当前选中规则的启用状态。"""
        rule = self._selected_rule()
        if rule is None:
            return
        rule.enabled = not rule.enabled
        self._refresh_rules_tree()

    # ------------------------------------------------------------------
    # 测试
    # ------------------------------------------------------------------
    def _append_result(self, text: str) -> None:
        """向测试结果区追加一行文本。"""
        self.result_text.insert(tk.END, text + "\n")
        self.result_text.see(tk.END)

    def _clear_result(self) -> None:
        """清空测试结果区。"""
        self.result_text.delete("1.0", tk.END)

    def _run_async(self, work, on_done) -> None:
        """在工作线程执行耗时操作，完成后回到 UI 线程回调。

        参数:
            work: 无参函数，返回值传给 on_done
            on_done: 单参回调（UI 线程执行）
        """
        holder = {}

        def _worker():
            try:
                holder["value"] = work()
            except Exception as exc:  # noqa: BLE001 - 测试必须捕获一切异常
                holder["error"] = exc
            holder["done"] = True

        threading.Thread(target=_worker, daemon=True).start()

        def _poll():
            if not holder.get("done"):
                try:
                    self.after(100, _poll)
                except tk.TclError:
                    pass
                return
            if "error" in holder:
                self._append_result("执行异常: %s" % holder["error"])
            else:
                on_done(holder.get("value"))

        self.after(100, _poll)

    def _test_url(self) -> None:
        """测试当前选中 URL（或第一条 URL）的连通性。"""
        self._sync_basic_options()
        selection = self.url_listbox.curselection()
        urls = self._get_urls()
        if selection:
            url = urls[selection[0]] if selection[0] < len(urls) else ""
        else:
            url = urls[0] if urls else ""
        if not url:
            messagebox.showinfo("提示", "URL 库为空，请先添加网址",
                                parent=self.winfo_toplevel())
            return

        test_code = self.test_code_var.get().strip() or "ABC-123"
        self._clear_result()
        self._append_result("测试网址: %s（番号: %s）" % (url, test_code))
        self._append_result("请求中...")

        def _work():
            return self.manager.test_url_connectivity(url, test_code)

        def _done(result):
            self._append_result(
                "结果: %s" % ("成功" if result.get("success") else "失败"))
            self._append_result("详情: %s" % result.get("message", ""))
            if result.get("status_code"):
                self._append_result(
                    "HTTP 状态码: %s，耗时: %sms"
                    % (result.get("status_code"), result.get("elapsed_ms")))

        self._run_async(_work, _done)

    def _test_rule(self) -> None:
        """用内置示例 HTML 测试当前选中的提取规则。"""
        self._sync_basic_options()
        rule = self._selected_rule()
        if rule is None:
            messagebox.showinfo("提示", "请先在规则列表中选中要测试的规则",
                                parent=self.winfo_toplevel())
            return
        self._clear_result()
        self._append_result("测试规则: %s（%s）"
                            % (rule.field_name or rule.field_id,
                               RULE_TYPE_LABELS.get(rule.rule_type,
                                                    rule.rule_type)))
        self._append_result("测试内容: 内置示例 HTML")

        def _work():
            return self.manager.test_extraction_rule(rule, SAMPLE_HTML)

        def _done(result):
            if result.get("success"):
                self._append_result("提取成功，结果:")
                self._append_result("  %r" % (result.get("result"),))
            else:
                self._append_result("提取失败: %s" % result.get("error", ""))

        self._run_async(_work, _done)

    # ------------------------------------------------------------------
    # 保存 / 重置
    # ------------------------------------------------------------------
    def _save_config(self) -> None:
        """校验并把当前配置保存到 config.json。"""
        self._sync_basic_options()
        problems = self.manager.validate_config(self.config)
        if problems:
            messagebox.showwarning(
                "配置校验未通过",
                "存在以下问题，请修正后再保存：\n\n- " + "\n- ".join(problems),
                parent=self.winfo_toplevel())
            return
        self.manager.save_config(self.config)
        messagebox.showinfo("提示", "爬虫配置已保存",
                            parent=self.winfo_toplevel())

    def _reset_default(self) -> None:
        """重置为默认配置（需确认）。"""
        if not messagebox.askyesno("确认", "确定重置为默认爬虫配置吗？",
                                   parent=self.winfo_toplevel()):
            return
        self.config = self.manager.reset_to_default()
        self._reload_ui()
        self._clear_result()
        self._append_result("已重置为默认配置（尚未保存到配置文件）")


class _RuleEditDialog(tk.Toplevel):
    """提取规则编辑对话框。

    根据规则类型显示对应的类型专属字段：
    snippet -> html_snippet + placeholder；
    css     -> selector + attribute；
    regex   -> pattern + group_index；
    custom  -> code。

    属性:
        result: ExtractionRule | None   确认时为新规则对象，取消时为 None
    """

    def __init__(self, parent: tk.Widget, rule: ExtractionRule = None) -> None:
        """初始化规则编辑对话框。

        参数:
            parent: 父窗口
            rule:   待编辑的规则；None 表示新建
        """
        super().__init__(parent)
        self.title("编辑提取规则" if rule else "添加提取规则")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None

        self._rule = rule
        self._type_var = tk.StringVar(
            value=rule.rule_type if rule else "css")
        self._field_id_var = tk.StringVar(value=rule.field_id if rule else "")
        self._field_name_var = tk.StringVar(
            value=rule.field_name if rule else "")
        self._enabled_var = tk.BooleanVar(
            value=rule.enabled if rule else True)
        self._multi_var = tk.BooleanVar(
            value=rule.is_multi_value if rule else False)
        self._selector_var = tk.StringVar(
            value=rule.selector if rule else "")
        self._attribute_var = tk.StringVar(
            value=rule.attribute if rule else "text")
        self._snippet_var = tk.StringVar(
            value=rule.html_snippet if rule else "")
        self._placeholder_var = tk.StringVar(
            value=rule.placeholder if rule else "")
        self._pattern_var = tk.StringVar(
            value=rule.pattern if rule else "")
        self._group_var = tk.IntVar(value=rule.group_index if rule else 1)
        self._desc_var = tk.StringVar(
            value=rule.description if rule else "")

        self._build_ui()
        self._on_type_change()
        self.grab_set()
        self.wait_visibility()
        self.focus_set()

    def _build_ui(self) -> None:
        """创建对话框表单。"""
        body = tk.Frame(self, padx=10, pady=10)
        body.pack(fill=tk.BOTH, expand=True)

        row = 0
        tk.Label(body, text="字段标识:").grid(row=row, column=0, sticky="w")
        tk.Entry(body, textvariable=self._field_id_var,
                 width=20).grid(row=row, column=1, sticky="we", pady=2)
        row += 1
        tk.Label(body, text="字段名称:").grid(row=row, column=0, sticky="w")
        tk.Entry(body, textvariable=self._field_name_var,
                 width=20).grid(row=row, column=1, sticky="we", pady=2)
        row += 1
        tk.Label(body, text="规则类型:").grid(row=row, column=0, sticky="w")
        type_combo = ttk.Combobox(body, textvariable=self._type_var,
                                  values=list(RULE_TYPES), state="readonly",
                                  width=18)
        type_combo.grid(row=row, column=1, sticky="w", pady=2)
        type_combo.bind("<<ComboboxSelected>>",
                        lambda e: self._on_type_change())
        row += 1

        option_frame = tk.Frame(body)
        option_frame.grid(row=row, column=0, columnspan=2, sticky="w",
                          pady=2)
        tk.Checkbutton(option_frame, text="启用", variable=self._enabled_var
                       ).pack(side=tk.LEFT)
        tk.Checkbutton(option_frame, text="多值(返回列表)",
                       variable=self._multi_var).pack(side=tk.LEFT, padx=8)
        row += 1

        # ---- 类型专属区域 ----
        self._type_frame = tk.LabelFrame(body, text="类型参数")
        self._type_frame.grid(row=row, column=0, columnspan=2,
                              sticky="we", pady=4)
        row += 1

        # snippet 参数
        self._snippet_block = tk.Frame(self._type_frame)
        tk.Label(self._snippet_block, text="HTML代码段模板:").pack(anchor="w")
        tk.Entry(self._snippet_block, textvariable=self._snippet_var,
                 width=56).pack(fill=tk.X, pady=2)
        tk.Label(self._snippet_block, text="占位符:").pack(anchor="w")
        tk.Entry(self._snippet_block, textvariable=self._placeholder_var,
                 width=20).pack(anchor="w", pady=2)

        # css 参数
        self._css_block = tk.Frame(self._type_frame)
        tk.Label(self._css_block, text="CSS选择器:").pack(anchor="w")
        tk.Entry(self._css_block, textvariable=self._selector_var,
                 width=56).pack(fill=tk.X, pady=2)
        tk.Label(self._css_block, text="取值属性:").pack(anchor="w")
        tk.Entry(self._css_block, textvariable=self._attribute_var,
                 width=20).pack(anchor="w", pady=2)
        tk.Label(self._css_block, text='(填 "text" 或留空表示取元素文本)',
                 fg="#808080").pack(anchor="w")

        # regex 参数
        self._regex_block = tk.Frame(self._type_frame)
        tk.Label(self._regex_block, text="正则表达式:").pack(anchor="w")
        tk.Entry(self._regex_block, textvariable=self._pattern_var,
                 width=56).pack(fill=tk.X, pady=2)
        tk.Label(self._regex_block, text="捕获组序号:").pack(anchor="w")
        tk.Spinbox(self._regex_block, textvariable=self._group_var, from_=0,
                   to=9, width=6).pack(anchor="w", pady=2)

        # custom 参数
        self._custom_block = tk.Frame(self._type_frame)
        tk.Label(self._custom_block,
                 text="自定义代码（可用变量 soup / html / re，结果赋值给 result）:"
                 ).pack(anchor="w")
        self._code_text = tk.Text(self._custom_block, width=56, height=6,
                                  wrap=tk.NONE)
        self._code_text.pack(fill=tk.X, pady=2)
        if self._rule is not None and self._rule.code:
            self._code_text.insert("1.0", self._rule.code)

        tk.Label(body, text="说明:").grid(row=row, column=0, sticky="w")
        tk.Entry(body, textvariable=self._desc_var,
                 width=40).grid(row=row, column=1, sticky="we", pady=2)
        row += 1

        # ---- 按钮 ----
        btn_frame = tk.Frame(body)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(8, 0))
        tk.Button(btn_frame, text="确定", width=8,
                  command=self._on_ok).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="取消", width=8,
                  command=self._on_cancel).pack(side=tk.LEFT, padx=4)

    def _on_type_change(self) -> None:
        """按当前规则类型切换显示对应的类型参数块。"""
        for block in (self._snippet_block, self._css_block,
                      self._regex_block, self._custom_block):
            block.pack_forget()
        rule_type = self._type_var.get()
        if rule_type == "snippet":
            self._snippet_block.pack(fill=tk.X, padx=4, pady=4)
            self._type_frame.config(text="代码段(snippet)参数")
        elif rule_type == "css":
            self._css_block.pack(fill=tk.X, padx=4, pady=4)
            self._type_frame.config(text="CSS选择器参数")
        elif rule_type == "regex":
            self._regex_block.pack(fill=tk.X, padx=4, pady=4)
            self._type_frame.config(text="正则表达式参数")
        elif rule_type == "custom":
            self._custom_block.pack(fill=tk.X, padx=4, pady=4)
            self._type_frame.config(text="自定义代码参数")

    def _on_ok(self) -> None:
        """确认：收集表单数据生成规则对象并关闭对话框。"""
        rule_type = self._type_var.get()
        if rule_type not in RULE_TYPES:
            messagebox.showwarning("提示", "请选择有效的规则类型",
                                   parent=self)
            return
        field_id = self._field_id_var.get().strip()
        if not field_id:
            messagebox.showwarning("提示", "字段标识不能为空", parent=self)
            return
        try:
            group_index = int(self._group_var.get())
        except (tk.TclError, ValueError):
            group_index = 1

        self.result = ExtractionRule(
            rule_id=self._rule.rule_id if self._rule else "",
            field_id=field_id,
            field_name=self._field_name_var.get().strip(),
            enabled=bool(self._enabled_var.get()),
            rule_type=rule_type,
            is_multi_value=bool(self._multi_var.get()),
            selector=self._selector_var.get().strip(),
            attribute=self._attribute_var.get().strip() or "text",
            html_snippet=self._snippet_var.get(),
            placeholder=self._placeholder_var.get().strip(),
            pattern=self._pattern_var.get(),
            group_index=group_index,
            code=self._code_text.get("1.0", tk.END).strip()
            if rule_type == "custom" else
            (self._rule.code if self._rule else ""),
            description=self._desc_var.get().strip(),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        """取消：关闭对话框，结果为 None。"""
        self.result = None
        self.destroy()
