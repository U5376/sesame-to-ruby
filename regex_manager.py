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
        self.set_log_level(log_level_var.get()) if (setattr(self, 'log_level_var', log_level_var) or log_level_var) else None
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
        add_btn = tk.Button(btn_frame, text="添加正则", command=lambda: self.add_entry(scroll=True), font=("宋体", 12))
        add_btn.pack(side=tk.LEFT, padx=2)
        add_btn.bind("<Button-3>", lambda e: self.add_entry(entry_type='dom', scroll=True))  # 右键: 直接添加DOM操作条目
        ToolTip(add_btn, "左键:添加正则条目\n右键:添加DOM操作条目(目前只用于匹配单标签,清理闭合标签代码块)")
        [tk.Button(btn_frame, text=t, command=c, font=("宋体", 12)).pack(side=tk.LEFT, padx=2)
         for t, c in [("保存设置", None)]]
        # 配置文件下拉框，限制宽度为20
        ini_menu = ttk.Combobox(btn_frame, textvariable=self.selected_ini, values=[], state="readonly", font=("宋体", 12), width=11)
        ini_menu.pack(side=tk.LEFT, padx=2)
        ini_menu.bind("<<ComboboxSelected>>", self._on_ini_selected)
        self.ini_menu = ini_menu
        self._init_ini_files()

        # 日志级别下拉框
        self.log_level_var = getattr(self, 'log_level_var', None) or tk.StringVar(value="info")
        cb = ttk.Combobox(btn_frame, textvariable=self.log_level_var, values=["info", "debug"], 
                        width=5, state="readonly", font=("宋体", 12))
        cb.pack(side=tk.LEFT, padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self.set_log_level(self.log_level_var.get(), show_log=True))
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
        # event=None：免疫悬空 after 定时器的零参误调(funcid 撞名场景)，None 时下方 getattr 守卫自然短路
        _on_mousewheel = lambda event=None: self.canvas.yview_scroll(
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


    def set_log_level(self, level, show_log=False):
        """设置日志级别"""
        logger.remove()
        logger.add(sys.stderr, level=level.upper())
        if show_log: logger.log(level.upper(), "日志级别: {}", level)

    def load_config(self, config_path=None):
        if config_path:
            self.config_file = Path(config_path)
        # 清空UI（防止重复加载）
        [entry[2].destroy() for entry in getattr(self, 'regex_entries', []) if hasattr(entry[2], 'destroy')]
        self.regex_entries, self.tooltips = [], []
        self._load_from_ini() if self.config_file.exists() else (self._create_default_rules())
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
            current_rule, current_key, in_sec = {}, None, False
            for line in f:
                line = line.rstrip('\n')
                # 仅读取[RegexRules]段
                if line.startswith('['):
                    current_rule and self._add_rule_from_dict(current_rule)
                    current_rule, current_key, in_sec = {}, None, (line.strip() == "[RegexRules]")
                    continue
                if not in_sec: continue

                if line.startswith('rule_'):
                    current_rule and self._add_rule_from_dict(current_rule)
                    current_rule, current_key = {}, None
                # 读取tooltip多行续行:拼接换行并剥离首个缩进符(保留空格跟空行)
                elif current_key == 'tooltip' and (line.startswith('\t') or line.startswith(' ')):
                    current_rule[current_key] += '\n' + line[1:]
                elif '=' in line:
                    key, value = line.split('=', 1)
                    current_rule[key.strip()], current_key = value, key.strip()
            current_rule and self._add_rule_from_dict(current_rule)

    def _add_rule_from_dict(self, rule_dict):
        """从字典添加规则 type=dom时以DOM模式加载(无type行默认regex)"""
        regex = rule_dict.get('regex', '')
        replace = rule_dict.get('replace', '')
        tooltip = rule_dict.get('tooltip', '')
        if regex or replace or tooltip:  # 有效性检查 三个为空则不加载
            self.add_entry(regex, replace, tooltip,
                           entry_type='dom' if rule_dict.get('type', '').strip() == 'dom' else 'regex')

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

    def add_entry(self, regex="", replace="", tooltip=None, index=None, entry_type='regex', scroll=False):
        """添加正则/DOM条目 拖动手动排序 scroll=交互式添加时滚动视野"""
        entry_frame = tk.Frame(self.inner_frame)
        entry_frame.rule_type = entry_type  # 类型标记 持久化/规则提取/执行分组均依此识别
        is_dom = (entry_type == 'dom')
        default_tip = ""
        if is_dom:
            # DOM条目: 橙色拖动条打头 动作框拉满整行(实心bg 宽6px highlight方案会被裁剪不可见)
            drag_bar = tk.Frame(entry_frame, width=6, bg="#FFB74D", cursor="fleur")
            drag_bar.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0), pady=1)  # 左侧留间隔 防误触紧邻滚动条
            regex_entry = tk.Entry(entry_frame, font=("宋体", 12))
            regex_entry.insert(0, regex)  # 默认空条目
            regex_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            replace_entry, replace_tooltip = None, None  # DOM无替换框 5元组占位保持形状
            # 默认语法说明仅悬停兜底显示(fallback) 不进text不写ini 编辑器带不出全文
            entry_frame._tip_default = not (tooltip and tooltip.strip())
            if entry_frame._tip_default:
                default_tip = ('DOM操作 只匹配单标签进行操作\n'
                               '<标签正则>           删标签留内容\n'
                               '-<标签正则>          连内容整块删除\n'
                               '=>h3                改名 丢属性\n'
                               '=>h3@               改名 保留原属性\n'
                               '=>h3[class=gaiji]   改名+丢属性+注入属性\n'
                               '=>h3@[class=gaiji]  改名+保留+注入(同名覆盖)\n'
                               '属性串:名=值 空格分隔多对 @紧跟标签名\n'
                               '具体例:<div[^>]*font-120per[^>]*>=>h3@[class=gaiji bold style=font-size:1.2em]\n'
                               '不以<开头的条目仅匹配class属性;正则内|需转义写\\|')
                tooltip = ""
        else:
            # 原正则条目
            regex_entry = tk.Entry(entry_frame, font=("宋体", 12), width=15)
            regex_entry.insert(0, regex)
            regex_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            # 拖动条（仅用于拖拽）
            drag_bar = tk.Frame(entry_frame, width=3, highlightthickness=3, highlightbackground="#E6E6E6", cursor="fleur")
            drag_bar.pack(side=tk.LEFT, fill=tk.Y, padx=0, pady=1)
            # 替换框
            replace_entry = tk.Entry(entry_frame, font=("宋体", 12), width=10)
            replace_entry.insert(0, replace)
            replace_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            replace_tooltip = ToolTip(replace_entry, tooltip or "", follow_widget=regex_entry)
        # 只在拖动条上绑定拖拽事件(两种条目通用)
        drag_bar.bind("<ButtonPress-1>", self._drag_start)
        drag_bar.bind("<B1-Motion>", self._drag_motion)
        drag_bar.bind("<ButtonRelease-1>", self._drag_end)
        # 右键菜单绑定
        for entry in ((regex_entry,) if is_dom else (regex_entry, replace_entry)):
            entry.bind("<Button-3>", lambda e, w=entry_frame: self._show_entry_context_menu(e, w))
        # 创建共享的tooltip对象(默认语法说明作fallback 仅悬停显示)
        shared_tooltip = ToolTip(regex_entry, tooltip or "", fallback=default_tip)
        # 删除按钮（不绑定拖动）
        del_btn = tk.Button(
            entry_frame, text="×", font=("宋体", 10),
            command=lambda: self._delete_entry(entry_frame)
        )
        del_btn.pack(side=tk.RIGHT)
        # 保存条目信息（包含框架和共享的tooltip 支持末尾追加或指定位置插入）
        item = (regex_entry, replace_entry, entry_frame, shared_tooltip, replace_tooltip)
        if index is None:
            entry_frame.pack(fill=tk.X, pady=2)
            self.regex_entries.append(item)
        else:
            self.regex_entries.insert(index, item)
            for entry_item in self.regex_entries:
                entry_item[2].pack_forget()
            for entry_item in self.regex_entries:
                entry_item[2].pack(fill=tk.X, pady=2)
        # 更新 canvas 的滚动区域，确保自动显示/隐藏滚动条
        self.inner_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        # 仅交互式添加(按钮/右键菜单)滚动视野 初始化/ini加载不滚动
        if scroll:
            self.canvas.update_idletasks()
            if index is None:
                self.canvas.yview_moveto(1.0)
            else:
                self.canvas.yview_moveto(entry_frame.winfo_y() / max(1, self.inner_frame.winfo_height()))

    def get_dom_rules(self):
        """获取编译后的DOM操作规则 主进程预编译一次 纯数据可pickle直传子进程"""
        # <正则>=unwrap(配对闭包自动删,内容上提);-<正则>=decompose(连内容整块删);<正则>=>tag=rename(改标签名,@保留属性 [名=值]注入)
        rules = []
        for entry in self.regex_entries:
            frame = entry[2]
            if getattr(frame, 'rule_type', 'regex') != 'dom' or not frame.winfo_exists():
                continue
            text = entry[0].get()
            if not text.strip():  # 空条目不加载(判空用strip)
                continue
            action, tag, keep_attrs, inject, pat = 'unwrap', None, False, None, text
            if text.startswith('-'):
                action, pat = 'decompose', text[1:]
            elif '=>' in text:  # 改名后缀(正则内需匹配字面=>时写\=\>)
                pat, _, suffix = text.partition('=>')
                suffix = suffix.strip()  # 动作后缀去空白(后缀无需严格空白)
                tag_part, bracket, tail = suffix.partition('[')
                if tag_part.endswith('@'):  # @紧跟标签名=保留原属性
                    keep_attrs, tag_part = True, tag_part[:-1]
                if bracket:  # [名=值 ...]属性注入段 孤立token并入上一属性值(class多值空格连写) 引号值兼容
                    if not tail.endswith(']'):
                        logger.warning(f"DOM属性段缺]已跳过: {text}")
                        continue
                    inject, last_name, in_q = {}, None, False
                    for tok in tail[:-1].split():
                        if in_q:  # 引号续接: 拼接至闭引号token
                            inject[last_name] += ' ' + tok[:-1] if tok.endswith('"') else ' ' + tok
                            in_q = not tok.endswith('"')
                            continue
                        m = re.fullmatch(r'([\w:.-]+)=(.+)', tok)
                        if m:  # 新属性
                            last_name, val = m.group(1), m.group(2)
                            if val.startswith('"') and not (val.endswith('"') and len(val) > 1):
                                inject[last_name], in_q = val[1:], True  # 剥开引号 跨token续接
                            elif val.startswith('"'):
                                val = val[1:-1]
                            if last_name not in inject:
                                inject[last_name] = val
                        elif last_name:  # 孤立token并入上一属性值(如class=gaiji bold)
                            inject[last_name] += ' ' + tok
                        else:
                            in_q = True  # 无前置属性的孤立token 借用标志触发拒绝
                            break
                    if in_q or not inject:
                        logger.warning(f"DOM属性段格式无效(名=值 孤立token并入上值)已跳过: {text}")
                        continue
                    inject = {k: (v.split() if k == 'class' else v) for k, v in inject.items()}  # class拆list(bs4多值属性)
                if not tag_part or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9:-]*', tag_part):
                    logger.warning(f"DOM改名目标无效已跳过: {text}")
                    continue
                action, tag = 'rename', tag_part
            pat = pat.strip()  # 去首尾空白(匹配目标无首尾空白 去空白无损 且避免误判class通道)
            try:
                rules.append((re.compile(pat), (action, tag, keep_attrs, inject), pat))
            except re.error as e:
                logger.warning(f"DOM规则无效已跳过: {text} | {e}")
        return rules

    def _show_entry_context_menu(self, event, entry_frame):
        """正则条目右键菜单"""
        idx = next((i for i, entry in enumerate(self.regex_entries) if entry[2] == entry_frame), None)
        if idx is None: return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="上方插入正则", command=lambda: self.add_entry(index=idx))
        menu.add_command(label="下方插入正则", command=lambda: self.add_entry(index=idx + 1))
        menu.add_separator()
        menu.add_command(label="上方插入DOM操作", command=lambda: self.add_entry(index=idx, entry_type='dom'))
        menu.add_command(label="下方插入DOM操作", command=lambda: self.add_entry(index=idx + 1, entry_type='dom'))
        menu.add_separator()
        menu.add_command(label="编辑悬浮提示", command=lambda: self._edit_tooltip(entry_frame))
        menu.add_command(label="删除正则", command=lambda: self._delete_entry(entry_frame))
        menu.post(event.x_root, event.y_root)

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
        def _save_tip():
            t = text.get("1.0", "end-1c")
            setattr(regex_tooltip, 'text', t)
            if replace_tooltip:  # DOM条目无replace_tooltip 守卫跳过
                setattr(replace_tooltip, 'text', t)
            setattr(widget, '_tip_default', False)  # 编辑过即视为自定义 随条目持久化
            top.destroy()
        tk.Button(top, text="保存", font=font, width=6, command=_save_tip).pack(pady=(0, 3))

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
        """返回正则规则文本块（用于写入配置文件） DOM条目额外写入type=dom行且无replace行"""
        content = "[RegexRules]\n"
        for i, entry in enumerate(self.regex_entries):
            regex_entry, replace_entry, frame, regex_tooltip, replace_tooltip = entry
            if not frame.winfo_exists():
                continue
            # 默认悬浮提示仅内存显示不写入配置 自定义后才持久化
            tooltip_text = "" if getattr(frame, '_tip_default', False) else (regex_tooltip.text if regex_tooltip else "")
            formatted_tooltip = tooltip_text.replace("\n", "\n\t")
            is_dom = getattr(frame, 'rule_type', 'regex') == 'dom'
            type_line = "type=dom\n" if is_dom else ""  # 老配置无此行加载时默认regex
            replace_line = "" if is_dom else f"replace={replace_entry.get()}\n"
            rule_block = (
                f"rule_{i+1}\n"
                f"{type_line}"
                f"regex={regex_entry.get()}\n"
                f"{replace_line}"
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
        """获取编译后的规则(过滤DOM条目与空条目)"""
        return [
            (re.compile(entry[0].get()), entry[1].get())
            for entry in self.regex_entries
            if entry[0].get().strip()  # 空条目不加载
            and getattr(entry[2], 'rule_type', 'regex') != 'dom'  # DOM条目由get_dom_rules提取
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
        win.geometry(f"450x300+{self.root.winfo_x()+150}+{self.root.winfo_y()+150}"); win.focus_force()
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
        def save_as_new():
            old_path = self.config_file
            base = Path(old_path).parent
            stem, suffix = Path(old_path).stem, Path(old_path).suffix
            for i in range(1, 100):
                new_name = f"{stem}{i}{suffix}"
                new_path = base / new_name
                if not new_path.exists(): break
            try:
                self.config_file = Path(new_path)
                if self.parent: self.parent.config_file = Path(new_path)
                if self.parent and hasattr(self.parent, 'save_app_settings'):
                    self.parent.save_app_settings()
                else:
                    shutil.copy2(old_path, new_path)
                if not new_path.exists(): raise RuntimeError("新配置文件未生成")
                self._init_ini_files()
                self.ini_menu['values'] = self.ini_names
                self.ini_menu.set(new_name); self.selected_ini.set(new_name)
                tree.delete(*tree.get_children())
                [tree.insert("", "end", iid=i, values=(n, p)) for i, (n, p) in enumerate(zip(self.ini_names, self.ini_files))]
                for iid in tree.get_children():
                    if tree.item(iid, "values")[0] == new_name:
                        tree.selection_set(iid); tree.see(iid); break
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
                self.config_file = Path(old_path)
                if self.parent: self.parent.config_file = Path(old_path)
        empty_menu = tk.Menu(tree, tearoff=0)
        empty_menu.add_command(label="另存为新配置", command=save_as_new)
        def on_right_click(event):
            row = tree.identify_row(event.y)
            if row:
                tree.selection_set(row); menu.post(event.x_root, event.y_root)
            else:
                empty_menu.post(event.x_root, event.y_root)
        tree.bind("<Button-3>", on_right_click)
