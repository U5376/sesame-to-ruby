import os
import re
import sys
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from loguru import logger
from tooltip import ToolTip # tooltip.py

class AutoScrollbar(ttk.Scrollbar):
    """自动隐藏的滚动条，place到canvas左侧，不影响布局宽度"""
    def __init__(self, parent, canvas=None, width=3, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas, self._shown, self._width = canvas, False, width
    def set(self, lo, hi):
        (lambda lo, hi: (
            (self.place(in_=self.canvas, x=0, y=0, relheight=1, width=self._width), setattr(self, '_shown', True))
            if float(lo) > 0.0 or float(hi) < 1.0 else
            (self.place_forget(), setattr(self, '_shown', False)) if self._shown else None
        ))(lo, hi)
        super().set(lo, hi)

class RegexManager:
    def __init__(self, root, config_path="config.ini", log_level_var=None, parent=None):
        self.root = root
        self.config_file = Path(config_path)
        self.regex_entries = []
        self.tooltips = []
        self.log_level_var = log_level_var  # 新增
        self.ini_files = []  # 所有ini文件列表
        self.selected_ini = tk.StringVar(value=str(self.config_file))  # 当前选中的ini文件
        self.parent = parent  # 主程序对象
        self.init_ui()
        self.load_config()

    def _init_ini_files(self):
        """初始化ini文件列表并更新下拉框"""
        base_dir = Path(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.argv[0]))))
        ini_paths = sorted([p for p in base_dir.glob('*.ini')])
        config_ini = base_dir / 'config.ini'
        if config_ini in ini_paths: ini_paths.remove(config_ini), ini_paths.insert(0, config_ini)
        if not ini_paths: ini_paths = [config_ini]
        self.ini_files, self.ini_names = [str(p) for p in ini_paths], [p.name for p in ini_paths]
        if not hasattr(self, '_ini_inited'):
            self.selected_ini.set(self.config_file.name)
            self.ini_menu['values'], self.ini_menu['state'] = self.ini_names, 'readonly'
            self.ini_menu.set(self.config_file.name)
            self._ini_inited = True
        elif self.config_file.name in self.ini_names:
            self.selected_ini.set(self.config_file.name), self.ini_menu.set(self.config_file.name)
        else:
            self.selected_ini.set(self.ini_names[0]), self.ini_menu.set(self.ini_names[0])
            self.config_file = Path(self.ini_files[0])

    def init_ui(self):
        """初始化界面组件"""
        self.frame = tk.Frame(self.root)
        self.frame.pack(fill=tk.BOTH, padx=5, pady=5, expand=True)
        btn_frame = tk.Frame(self.frame)
        btn_frame.pack(fill=tk.X, pady=3)
        [tk.Button(btn_frame, text=t, command=c, font=("宋体", 12)).pack(side=tk.LEFT, padx=2)
         for t, c in [("添加正则", self.add_entry), ("保存设置", self.save_config)]]
        # 配置文件下拉框，限制宽度为20
        ini_menu = ttk.Combobox(btn_frame, textvariable=self.selected_ini, values=[], state="readonly", font=("宋体", 12), width=11)
        ini_menu.pack(side=tk.LEFT, padx=2)
        ini_menu.bind("<<ComboboxSelected>>", self._on_ini_selected)
        self.ini_menu = ini_menu
        self._init_ini_files()

        # 日志级别下拉框
        log_level_var = tk.StringVar(btn_frame); log_level_var.set("info")
        log_levels = ["info", "debug"]
        log_level_menu = ttk.Combobox(btn_frame, textvariable=log_level_var, values=log_levels, state="readonly", font=("宋体", 12), width=5)
        log_level_menu.pack(side=tk.LEFT, padx=2)
        log_level_menu.bind("<<ComboboxSelected>>", lambda e: self.set_log_level(log_level_var.get()))
        # 初始化时设置日志级别
        self.set_log_level("info")
        # ====== 可滚动条目区（canvas + inner_frame） ======
        self.scroll_container = tk.Frame(self.frame); self.scroll_container.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self.scroll_container, borderwidth=0, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        style = ttk.Style(self.root)
        sb_style = (lambda: (style.layout('arrowless.Vertical.TScrollbar', [
            ('Vertical.Scrollbar.trough', {'children': [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})], 'sticky': 'ns'})]),
            style.configure('arrowless.Vertical.TScrollbar', width=2),
            'arrowless.Vertical.TScrollbar')[2])() if hasattr(style, 'layout') else None
        self.vsb = AutoScrollbar(self.scroll_container, canvas=self.canvas, orient='vertical',
                                 command=self.canvas.yview, style=sb_style if sb_style else None)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.inner_frame = tk.Frame(self.canvas)
        self._canvas_window = self.canvas.create_window((0, 0), window=self.inner_frame, anchor='nw')
        self.inner_frame.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(self._canvas_window, width=e.width))
        _on_mousewheel = lambda event: self.canvas.yview_scroll(
            int(-1 * (event.delta / 120)) if getattr(event, 'delta', 0) else (1 if getattr(event, 'num', 0) == 5 else -1),
            'units') if getattr(event, 'delta', 0) or getattr(event, 'num', 0) in (4, 5) else None
        [self.canvas.bind(ev, fn) for ev, fn in [
            ('<Enter>', lambda e: [self.canvas.bind_all(x, _on_mousewheel) for x in ('<MouseWheel>', '<Button-4>', '<Button-5>')]),
            ('<Leave>', lambda e: [self.canvas.unbind_all(x) for x in ('<MouseWheel>', '<Button-4>', '<Button-5>')])
        ]]
        self.inner_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        
        self._add_ini_menu_tooltip()
        self._add_ini_menu_manage()


    def set_log_level(self, level):
        """设置日志级别"""
        logger.remove()
        logger.add(sys.stderr, level=level.upper())
        logger.log(level.upper(), "日志级别: {}", level)

    def save_config(self):
        """空实现，主程序会重绑定按钮为实际保存方法"""
        pass

    def load_config(self, config_path=None):
        if config_path:
            self.config_file = Path(config_path)
        # 清空UI（防止重复加载）
        [entry[2].destroy() for entry in getattr(self, 'regex_entries', []) if hasattr(entry[2], 'destroy')]
        self.regex_entries, self.tooltips = [], []
        self._load_from_ini() if self.config_file.exists() else (self._create_default_rules(), self.save_config())
        self._init_ini_files()
        # 保持下拉框选中项同步
        if hasattr(self, 'ini_names') and hasattr(self, 'ini_menu'):
            try:
                idx = self.ini_files.index(str(self.config_file))
                [func(self.ini_names[idx]) for func in (self.selected_ini.set, self.ini_menu.set)]
            except Exception:
                pass

    def _load_from_ini(self):
        """ini配置加载"""
        with open(self.config_file, 'r', encoding='utf-8') as f:
            current_rule, current_key = { }, None
            lines = [line.rstrip('\n') for line in f]
            for line in lines:
                if line.strip() == "[RegexRules]": continue
                if line.startswith('rule_'):
                    current_rule and self._add_rule_from_dict(current_rule)
                    current_rule, current_key = {}, None
                    continue
                if not line.strip(): continue
                if '=' in line:
                    key, value = map(str.strip, line.split('=', 1))
                    current_rule[key], current_key = value, key
                elif current_key and (line.startswith(' ') or line.startswith('\t')):
                    current_rule[current_key] += '\n' + line.lstrip()
            current_rule and self._add_rule_from_dict(current_rule)

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
        entry_frame = tk.Frame(self.inner_frame)
        entry_frame.pack(fill=tk.X, pady=2)
        # 正则框
        regex_entry = tk.Entry(entry_frame, font=("宋体", 12), width=15)
        regex_entry.insert(0, regex)
        regex_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        # 拖动条（仅用于拖拽）
        drag_bar = tk.Frame(entry_frame, width=3, bg="#E6E6E6", cursor="fleur")
        drag_bar.pack(side=tk.LEFT, fill=tk.Y, padx=0, pady=1)
        # 替换框
        replace_entry = tk.Entry(entry_frame, font=("宋体", 12), width=10)
        replace_entry.insert(0, replace)
        replace_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        # 只在拖动条上绑定拖拽事件
        drag_bar.bind("<ButtonPress-1>", self._drag_start)
        drag_bar.bind("<B1-Motion>", self._drag_motion)
        drag_bar.bind("<ButtonRelease-1>", self._drag_end)
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
        # 更新 canvas 的滚动区域，确保自动显示/隐藏滚动条
        self.inner_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

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
        if not isinstance(event.widget, tk.Frame) or event.widget.cget("cursor") != "fleur":
            return
        self.dragged_item = event.widget.master  # 拖动条的父框架
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

    def update_ini_files(self):
        """刷新ini列表"""
        ini = str(self.config_file)
        self.ini_files = [ini] + [str(p) for p in Path('.').glob('*.ini') if str(p) != ini]
        self.ini_menu['values'] = self.ini_files
        self.ini_menu.set(ini)

    def _on_ini_selected(self, event=None):
        """ini列表切换配置刷新"""
        sel = self.selected_ini.get()
        if hasattr(self, 'ini_names') and sel in self.ini_names:
            idx = self.ini_names.index(sel)
            self.config_file = Path(self.ini_files[idx])
            if self.parent: self.parent.config_file = self.config_file
            self.selected_ini.set(self.ini_names[idx])
            self.ini_menu.set(self.ini_names[idx])
            self.load_config(str(self.config_file))
            if self.parent: self.parent.load_app_settings()
        else:
            self._init_ini_files()
            self.selected_ini.set(self.ini_names[0])
            self.ini_menu.set(self.ini_names[0])
            if self.parent:
                self.parent.config_file = Path(self.ini_files[0])
                self.parent.load_app_settings()

    def _add_ini_menu_tooltip(self):
        """下拉框悬浮提示配置名"""
        get_tip = lambda: (
            f"{(self.ini_names[idx] if 0<=(idx:=self.ini_menu.current())<len(self.ini_names) else self.selected_ini.get())}"
            "\n\n右键管理配置文件\n复制为新配置不会主动加载\n可手动保存现有配置进新ini")
        self._ini_menu_tip = ToolTip(self.ini_menu, get_tip(), wrap_length=400)
        update = lambda e: setattr(self._ini_menu_tip, 'text', get_tip())
        for event in ("<Enter>", "<<ComboboxSelected>>"): self.ini_menu.bind(event, update, add="+")

    def _add_ini_menu_manage(self):
        self.ini_menu.bind("<Button-3>", lambda e: self._show_ini_manage_window()) #ini下拉框添加右键管理菜单

    def _show_ini_manage_window(self):
        """ini配置文件管理窗口"""
        win = tk.Toplevel(self.root)
        win.title("配置文件管理")
        win.geometry(f"450x300+{self.root.winfo_x()+200}+{self.root.winfo_y()+150}")
        frame = ttk.Frame(win); frame.pack(fill="both", expand=True, padx=8, pady=8)
        tree = ttk.Treeview(frame, columns=("name", "path"), show="headings")
        [tree.heading(c, text=t) for c, t in zip(("name", "path"), ("文件名", "完整路径"))]
        [tree.column(c, width=w) for c, w in zip(("name", "path"), (120, 220))]
        [tree.insert("", "end", iid=i, values=(n, p)) for i, (n, p) in enumerate(zip(self.ini_names, self.ini_files))]
        tree.pack(fill="both", expand=True, side="left")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set); vsb.pack(side="right", fill="y")
        def on_double_click(event):
            region, col, row = tree.identify("region", event.x, event.y), tree.identify_column(event.x), tree.identify_row(event.y)
            if region != "cell" or col != "#1" or not row: return
            x, y, width, height = tree.bbox(row, col)
            old_name, old_path = tree.item(row, "values")
            entry = tk.Entry(tree); entry.place(x=x, y=y, width=width, height=height)
            entry.insert(0, old_name); entry.focus_set()
            def save_edit(event=None):
                new_name = entry.get().strip()
                if not new_name or new_name == old_name: entry.destroy(); return
                new_path = str(Path(old_path).parent / new_name)
                try:
                    os.rename(old_path, new_path)
                    tree.item(row, values=(new_name, new_path))
                    self._init_ini_files(); self.config_file = Path(new_path)
                    if self.parent: self.parent.config_file = Path(new_path)
                    self.ini_menu['values'] = self.ini_names
                    self.ini_menu.set(new_name); self.selected_ini.set(new_name)
                except Exception as e:
                    messagebox.showerror("重命名失败", str(e))
                entry.destroy()
            entry.bind("<Return>", save_edit)
            entry.bind("<FocusOut>", lambda e: entry.destroy())
        tree.bind("<Double-1>", on_double_click)
        menu = tk.Menu(tree, tearoff=0)
        def copy_config():
            sel = tree.selection()
            if not sel: return
            iid = sel[0]
            old_name, old_path = tree.item(iid, "values")
            base = Path(old_path).parent
            for i in range(1, 100):
                new_name = f"{Path(old_name).stem}{i}{Path(old_name).suffix}"
                new_path = base / new_name
                if not new_path.exists(): break
            try:
                shutil.copy2(old_path, new_path)
                self._init_ini_files(); self.config_file = Path(new_path)
                if self.parent: self.parent.config_file = Path(new_path)
                self.ini_menu['values'] = self.ini_names
                self.ini_menu.set(new_name); self.selected_ini.set(new_name)
                tree.delete(*tree.get_children())
                [tree.insert("", "end", iid=i, values=(n, p)) for i, (n, p) in enumerate(zip(self.ini_names, self.ini_files))]
                [tree.selection_set(iid), tree.see(iid)] if tree.item(iid, "values")[0] == new_name else None
            except Exception as e:
                messagebox.showerror("复制失败", str(e))
        menu.add_command(label="复制为新配置", command=copy_config)
        def delete_config():
            sel = tree.selection()
            if not sel: return
            iid = sel[0]
            name, path = tree.item(iid, "values")
            if messagebox.askyesno("确认删除", f"确定要删除 {name} 吗？"):
                try:
                    os.remove(path)
                    tree.delete(iid)
                    self._init_ini_files(); self.load_config()
                    self.ini_menu['values'] = self.ini_names
                    self.ini_menu.set(self.config_file.name); self.selected_ini.set(self.config_file.name)
                except Exception as e:
                    messagebox.showerror("删除失败", str(e))
        menu.add_command(label="删除配置", command=delete_config)
        tree.bind("<Button-3>", lambda event: (tree.selection_set(tree.identify_row(event.y)), menu.post(event.x_root, event.y_root)) if tree.identify_row(event.y) else None)
