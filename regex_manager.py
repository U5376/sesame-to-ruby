import re
from pathlib import Path
import tkinter as tk
from loguru import logger
from tooltip import ToolTip # tooltip.py
import sys

class RegexManager:
    def __init__(self, root, config_path="config.ini", log_level_var=None):
        self.root = root
        self.config_file = Path(config_path)
        self.regex_entries = []
        self.tooltips = []
        self.log_level_var = log_level_var  # 新增
        self.init_ui()
        self.load_config()

    def init_ui(self):
        """初始化界面组件"""
        self.frame = tk.Frame(self.root)
        self.frame.pack(fill=tk.BOTH, padx=5, pady=5, expand=True)
        btn_frame = tk.Frame(self.frame)
        btn_frame.pack(fill=tk.X, pady=3)
        buttons = [
            ("添加正则", self.add_entry),
            ("保存设置", self.save_config),
            ("加载默认正则", self.reset_to_default)
        ]
        for text, cmd in buttons:
            tk.Button(btn_frame, text=text, command=cmd, font=("宋体", 12)).pack(side=tk.LEFT, padx=2)

        # 添加日志级别下拉框（原始UI：放在btn_frame里，不用log_level_var参数）
        from tkinter import ttk
        log_level_var = tk.StringVar(btn_frame)
        log_level_var.set("info")  # 默认值
        log_levels = ["info", "debug"]
        log_level_menu = ttk.Combobox(btn_frame, textvariable=log_level_var, values=log_levels, state="readonly", font=("宋体", 12), width=5)
        log_level_menu.pack(side=tk.LEFT, padx=2)
        log_level_menu.bind("<<ComboboxSelected>>", lambda e: self.set_log_level(log_level_var.get()))
        # 初始化时设置日志级别
        self.set_log_level("info")

    def set_log_level(self, level):
        """设置日志级别"""
        logger.remove()
        logger.add(sys.stderr, level=level.upper())
        logger.log(level.upper(), "日志级别: {}", level)

    def save_config(self):
        """空实现，主程序会重绑定按钮为实际保存方法"""
        pass

    def load_config(self):
        """加载配置"""
        if self.config_file.exists():
            self._load_from_ini()
        else:
            self._create_default_rules()
            self.save_config()

    def _load_from_ini(self):
        """ini配置加载"""
        with open(self.config_file, 'r', encoding='utf-8') as f:
            current_rule = {}
            current_key = None
            for line in f:
                line = line.rstrip('\n')  # 保留行尾空白
                # 检测节头
                if line.strip() == "[RegexRules]":
                    continue
                # 检测规则头 (rule_数字)
                if line.startswith('rule_'):
                    if current_rule:
                        self._add_rule_from_dict(current_rule)
                    current_rule = {}
                    current_key = None
                    continue
                # 空行跳过
                if not line.strip():
                    continue
                # 处理键值对
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    current_rule[key] = value
                    current_key = key
                elif current_key and (line.startswith(' ') or line.startswith('\t')):
                    # 续行内容（追加到当前键值）
                    current_rule[current_key] += '\n' + line.lstrip()
            # 提交最后一个规则
            if current_rule:
                self._add_rule_from_dict(current_rule)

    def _add_rule_from_dict(self, rule_dict):
        """从字典添加规则"""
        regex = rule_dict.get('regex', '')
        replace = rule_dict.get('replace', '')
        tooltip = rule_dict.get('tooltip', '')
        if regex:  # 有效性检查
            self.add_entry(regex, replace, tooltip)

    def _create_default_rules(self):
        """创建默认规则"""
        defaults = [
            (r"<body\s.*?>", "<body>", "清除body样式"),
            (r"<div\s.*?>", "<div>", "清除div样式"),
            (r"<p\s.*?>", "<p>", "清除p样式"),
            (r"<p>[ 　\t]", "<p>", "清除P标签行首空格"),
            (r'<span class="tcy">(.*?)</span>', r'\1', "清除tcy标签"),
            (r'(<ruby>.*?<rt>)([^・].*?)(<\/rt><\/ruby>)', r'\1\2\3《\2》', "Ruby兼容处理")
        ]
        for regex, replace, tip in defaults:
            self.add_entry(regex, replace, tip)

    def add_entry(self, regex="", replace="", tooltip=None):
        """添加正则条目 拖动手动排序"""
        entry_frame = tk.Frame(self.frame)
        entry_frame.pack(fill=tk.X, pady=2)
        # 正则框
        regex_entry = tk.Entry(entry_frame, font=("宋体", 12), width=15)
        regex_entry.insert(0, regex)
        regex_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        # 替换框
        replace_entry = tk.Entry(entry_frame, font=("宋体", 12), width=10)
        replace_entry.insert(0, replace)
        replace_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        # 绑定拖动事件到所有子控件（除删除按钮）
        for widget in (regex_entry, replace_entry, entry_frame):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_motion)
            widget.bind("<ButtonRelease-1>", self._drag_end)
        # 右键菜单绑定
        for entry in (regex_entry, replace_entry):
            entry.bind("<Button-3>", lambda e, w=entry_frame: self._edit_tooltip(w))
        # 创建共享的tooltip对象
        shared_tooltip = ToolTip(regex_entry, tooltip or "")
        replace_tooltip = ToolTip(replace_entry, tooltip or "", follow_widget=regex_entry)
        # 删除按钮（不绑定拖动）
        del_btn = tk.Button(
            entry_frame, text="×", font=("宋体", 10),
            command=lambda: self._delete_entry(entry_frame)
        )
        del_btn.pack(side=tk.RIGHT)
        # 保存条目信息（包含框架和共享的tooltip）
        self.regex_entries.append((regex_entry, replace_entry, entry_frame, shared_tooltip, replace_tooltip))

    def _edit_tooltip(self, widget):
        """编辑悬浮提示"""
        # 获取当前提示内容
        entry = next(e for e in self.regex_entries if e[2] == widget)
        regex_entry, replace_entry, entry_frame, regex_tooltip, replace_tooltip = entry
        current_text = regex_tooltip.text if regex_tooltip else ""
        # 创建编辑窗口
        top = tk.Toplevel(self.root)
        top.title("悬浮提示编辑")
        top.geometry(f"+{self.root.winfo_x()+20}+{self.root.winfo_y()+150}")
        font = ("宋体", 12)
        # 文本编辑框
        text = tk.Text(top, width=40, height=4, font=font, padx=5, pady=5)
        text.pack(padx=3, pady=3)
        text.insert("1.0", regex_tooltip.text or "")
        top.focus_set()  # 焦到窗口到文本
        text.focus_set()
        tk.Button(
            top,
            text="保存", 
            font=font,
            width=6,
            command=lambda: [
                setattr(regex_tooltip, 'text', text.get("1.0", "end-1c").strip()),
                setattr(replace_tooltip, 'text', text.get("1.0", "end-1c").strip()),
                top.destroy()
            ]
        ).pack(pady=(0, 3))

    def apply_rules(self, content):
        """应用所有正则规则到指定内容"""
        try:
            for pattern, replacement in self.get_rules():
                content = pattern.sub(replacement, content)
        except re.error as e:
            logger.error(f"正则替换错误: {str(e)}")
        except Exception as e:
            logger.error(f"应用正则规则时出错: {str(e)}")
        return content

    def _drag_start(self, event):
        """拖动开始事件处理"""
        # 排除删除按钮
        if isinstance(event.widget, tk.Button): return
        # 获取实际拖动的框架
        if isinstance(event.widget, tk.Entry):
            self.dragged_item = event.widget.master  # 输入框的父框架
        else:
            self.dragged_item = event.widget
        # 记录初始索引
        self.start_index = next(
            (i for i, entry in enumerate(self.regex_entries)
             if entry[2] == self.dragged_item),
            None
        )

    def _drag_motion(self, event):
        if not hasattr(self, 'start_index') or self.start_index is None:return
        # 获取当前鼠标位置对应的条目索引
        y = event.widget.winfo_pointery()
        target_index = next((i for i, entry in enumerate(self.regex_entries) 
            if entry[2].winfo_rooty() + entry[2].winfo_height()/2 > y), len(self.regex_entries))
        # 调整位置
        if 0 <= target_index < len(self.regex_entries) and target_index != self.start_index:
            item = self.regex_entries.pop(self.start_index)
            self.regex_entries.insert(target_index, item)
            self.start_index = target_index
            # 重新排列界面
            for entry in self.regex_entries:entry[2].pack_forget()
            for entry in self.regex_entries:entry[2].pack(fill=tk.X, pady=2)

    def _drag_end(self, event):
        if hasattr(self, 'start_index'):
            del self.start_index

    def _delete_entry(self, entry_frame):
        """删除按钮功能"""
        self.regex_entries = [entry for entry in self.regex_entries if entry[2] != entry_frame]
        for child in entry_frame.winfo_children():
            if isinstance(child, tk.Entry):
                self.tooltips = [t for t in self.tooltips if t.widget != child]
        entry_frame.destroy()

    def reset_to_default(self):
        """重置正则"""
        for entry in self.regex_entries:
            entry[2].destroy()
        self.regex_entries.clear()
        self._create_default_rules()

    def get_rules_content(self):
        """返回正则规则文本块（用于写入配置文件）"""
        content = "[RegexRules]\n"
        for i, entry in enumerate(self.regex_entries):
            regex_entry, replace_entry, frame, regex_tooltip, replace_tooltip = entry
            if not frame.winfo_exists():
                continue
            tooltip_text = regex_tooltip.text if regex_tooltip else ""
            formatted_tooltip = tooltip_text.replace("\n", "\n\t")
            rule_block = (
                f"rule_{i+1}\n"
                f"regex={regex_entry.get()}\n"
                f"replace={replace_entry.get()}\n"
                f"tooltip={formatted_tooltip}\n\n"
            )
            content += rule_block
        return content.strip()

    def _get_tooltip_text(self, entry_widget):
        """安全获取工具提示内容"""
        for tip in self.tooltips:
            # 检查提示对象和控件是否有效
            if hasattr(tip, 'widget') and tip.widget == entry_widget:
                return getattr(tip, 'text', '')
        return ''

    def get_rules(self):
        """获取编译后的规则"""
        return [
            (re.compile(entry[0].get()), entry[1].get())
            for entry in self.regex_entries
            if entry[0].get().strip()
        ]
