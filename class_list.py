import atexit
import configparser
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import namedtuple
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
import lxml.html
from bs4 import BeautifulSoup
from loguru import logger
from tkinterdnd2 import DND_FILES

class ClassList:
    ColorTerm = namedtuple('ColorTerm', 'color pattern raw name_only role exclude', defaults=(False, None, False))

    def __init__(self, root, epub_path, get_temp, set_temp, append_temp, workers_cfg='Auto', win_size=None, text_search_terms=None, style_filter_terms=None, config_file=None, text_search_history=None, style_filter_history=None, tag_menu_terms=None, replace_history=None, search_state=None):
        self.root, self.epub_path = root, epub_path
        self.get_temp_style_content, self.set_temp_style_content, self.append_temp_style_content = get_temp, set_temp, append_temp
        self.workers_cfg = workers_cfg
        self.win_size = win_size
        # 下拉词条为内存记忆 落盘仅在对话框保存或首次注入默认词条时(经save_search_box_only局部写)
        self.text_search_terms = text_search_terms if text_search_terms is not None else []
        self.style_filter_terms = style_filter_terms if style_filter_terms is not None else []
        # 历史由主程序持有 跨ClassList实例共享; 关class窗口时经save_search_box_only局部落盘(各上限10条)
        self.text_search_history = text_search_history if text_search_history is not None else []
        self.style_filter_history = style_filter_history if style_filter_history is not None else []
        self.replace_history = replace_history if replace_history is not None else []  # 替换框独立历史
        # 搜索状态记忆: 替换模式开关 + 全局匹配勾选 (与主程序共享同一dict 勾选变化即时回传 供同会话重开与关窗落盘)
        self._persist_search_state = search_state if search_state is not None else {'replace_mode': False, 'global_match': False}
        # 关窗对比基线: 历史/状态相对本次打开无实际变化时关窗不写文件(载入的既有历史不算变化)
        self._hist_state_snapshot = (list(self.text_search_history), list(self.replace_history), dict(self._persist_search_state))
        # 双击标签快速换种的备选标签列表(JSON持久化于[SearchBox].tag_terms) 默认h3/p/span
        self.tag_menu_terms = list(tag_menu_terms) if tag_menu_terms else ['h3', 'p', 'span']
        self.config_file = config_file
        self.ensure_default_terms()  # 无[SearchBox]段时注入默认 opacity 词条并落盘
        self._search_boxes, self._style_boxes, self._replace_boxes = [], [], []  # 存活的搜索/筛选/替换下拉框引用 用于词条变更后刷新
        self.style_data, self.samples_data, self.counts_data, self.img_counts = {}, {}, {}, {}
        self.cats = {k: set() for k in ['Class列表', 'Span列表', '图片Class列表', '非P标签列表', '非P、img、body标签列表']}
        self.all_items_refs, self.n_map, self.st = [], {"": ""}, {"#0": False, "count": False}
        self.preview_window = self.details_window = None
        self._after_ids = []
        self._running = True
        self.modified_files = {}
        self._dragging = False # 拖入拖出 互斥锁
        self.sesame_root = Path(tempfile.gettempdir(), "sesame_cache"); self.sesame_root.mkdir(parents=True, exist_ok=True)
        self.show_class_list()

    @staticmethod
    def _add_clear_btn(combo, on_clear=None):
        """在Combobox下拉箭头左侧叠加✕按钮 快速清空输入 (无背景色差 沿用默认字体与前景色)"""
        # 从ttk样式取Combobox真实底色 融入背景消除色差 取不到则回退白色
        bg = ttk.Style().lookup("TCombobox", "fieldbackground") or "#ffffff"
        btn = tk.Button(combo, text="✕", relief="flat", bd=0, overrelief="flat", takefocus=0,
                        bg=bg, activebackground=bg, cursor="hand2", highlightthickness=0,
                        command=lambda: (combo.set(""), on_clear() if on_clear else None))
        btn.place(relx=1.0, rely=0.5, x=-36, y=-7, width=15, height=14)

    @staticmethod
    def _bind_edit_menu(widget):
        """为Entry/Combobox绑定右键编辑菜单(剪切/复制/黏贴/删除/全选) 补Windows下缺失的右键菜单。
        用event_generate触发<<Cut>>/<<Copy>>/<<Paste>>/<<Clear>>/<<SelectAll>>虚拟事件 复用Entry类绑定。"""
        menu = tk.Menu(widget, tearoff=0)
        def on_post(e):
            try: widget.focus_set()
            except tk.TclError: pass
            try: has_sel = bool(widget.selection_present())
            except Exception: has_sel = False
            menu.delete(0, "end")
            st = "normal" if has_sel else "disabled"
            menu.add_command(label="剪切", command=lambda: widget.event_generate("<<Cut>>"), state=st)
            menu.add_command(label="复制", command=lambda: widget.event_generate("<<Copy>>"), state=st)
            menu.add_command(label="黏贴", command=lambda: widget.event_generate("<<Paste>>"))
            menu.add_command(label="删除", command=lambda: widget.event_generate("<<Clear>>"), state=st)
            menu.add_separator()
            menu.add_command(label="全选", command=lambda: widget.event_generate("<<SelectAll>>"))
            menu.post(e.x_root, e.y_root)
        widget.bind("<Button-3>", on_post)
        return widget

    # 下拉列表中分隔固定词条与临时(历史)词条的横线 选中时会被识别并跳过
    _SEP_LINE = "─" * 24

    # 合法色号: #RGB / #RRGGBB 十六进制
    _HEX_COLOR_RE = r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})'
    # 默认 opacity 灰字颜色词条(无[SearchBox]段时注入) 复用既有 opacity 匹配正则
    _DEFAULT_OPACITY_TERM = r'[#7E7A7A] (?i)<(p|span|div|h[1-6])\b[^>]*\bstyle=[\'"][^\'"]*opacity:\s*0?\.\d+[^>]*>[\s\S]*?</\1>'
    @staticmethod
    def _is_valid_color(color):
        """色号合法性: #RGB/#RRGGBB 十六进制"""
        return bool(re.fullmatch(ClassList._HEX_COLOR_RE, color))

    @staticmethod
    def _color_tag_name(color, role=None):
        """由色号与角色(fg/bg)生成唯一tag名 避免与sel/m/cur冲突"""
        return "color_" + (role or '') + color.lstrip('#')

    @staticmethod
    def _parse_color_term(term_text, default_color="#7E7A7A"):
        """解析[-fg/bg@色号]正则 词条: -纯标色不入下拉 fg前景/bg背景(缺省按上下文) @仅匹配类名
        [色号]须为合法#hex或[@](名匹配默认色) 否则视为普通正则(如字符类[0-9])返回None
        空格规则: 色号前行首空白视为缩进; ]后直接进入正则本体(空格有效 不吞分隔)"""
        m = re.match(r'^\s*(?:\[(-)?(fg|bg)?(@?)([^\]]*)\])?(.*)$', term_text, re.IGNORECASE)
        if not m or not m[5]: return None
        exclude, role, name_only, color_raw, rule = m.groups()
        if color_raw is None: return None  # 无[色号]前缀 普通词条
        if not (ClassList._is_valid_color(color_raw) or (name_only and color_raw == '')): return None  # 内容非合法色号(如字符类[0-9])
        color = color_raw if ClassList._is_valid_color(color_raw) else default_color
        try: pattern = re.compile(rule)
        except re.error: pattern = re.compile(re.escape(rule))
        return ClassList.ColorTerm(color, pattern, rule, bool(name_only), (role or '').lower() or None, bool(exclude))

    @staticmethod
    def write_terms(items):
        """词条列表 -> JSON字符串(配置存储用)"""
        return json.dumps(list(items), ensure_ascii=False)

    @staticmethod
    def read_terms(raw):
        """JSON字符串 -> 词条列表 非法/空值返回空列表"""
        if not raw or not raw.strip(): return []
        try:
            v = json.loads(raw.strip())
            if isinstance(v, list): return [str(x) for x in v if x != '']
        except Exception:
            pass
        return []

    @staticmethod
    def _bind_multiline_paste(w, on_change=None):
        """跨行粘贴转义(Combobox/Text通用): 实际换行符自动转义为\\n成单行(同Sigil);
        Text按住Shift粘贴保留多行原样(每行一条词条); on_change: 粘贴后额外回调(如触发搜索)"""
        is_text = isinstance(w, tk.Text)
        def _paste(e):
            if is_text and e.state & 0x0001: return  # Shift按住: 保留多行原样
            try: raw = w.clipboard_get()
            except tk.TclError: return
            if '\n' not in raw and '\r' not in raw: return  # 无跨行 走默认粘贴
            norm = re.sub(r'\r\n|\r|\n', r'\\n', raw)
            try: w.delete("sel.first", "sel.last")  # 选区替换
            except tk.TclError: pass
            w.insert("insert", norm)  # Combobox/Text 的 "insert" 索引通用 插入后光标自动后移
            if is_text: w.see("insert")
            if on_change: on_change()
            return "break"
        w.bind("<<Paste>>", _paste)

    @staticmethod
    def _dropdown_term(term):
        """下拉显示用: 去掉[色号]前缀仅留正则; 纯标色词条(色号前带-)返回None不入下拉
        (不strip词条本体 保留正则首尾空格; 色号前行首空白视为缩进)"""
        if not term.strip().startswith('['): return term  # 非色号词条原样返回
        ct = ClassList._parse_color_term(term)
        if ct is None: return term  # 非色号词条(如正则字符类[0-9]) 原样返回
        if ct.exclude: return None  # 纯标色 不入下拉
        return ct.raw  # 去色号前缀仅留正则

    def _combo_values(self, fixed_terms, history):
        """下拉词条: 自定义词条(去色号前缀,排除纯标色) + 分隔横线 + 历史(新的在上 内部按旧→新存 显示时反转)"""
        fixed = [t for t in (self._dropdown_term(x) for x in fixed_terms) if t]
        slots = max(0, 20 - len(fixed))
        hist = [h for h in history if h not in fixed][-slots:] if slots else []
        sep = [self._SEP_LINE] if fixed and hist else []
        return (fixed + sep + hist[::-1])[:20]

    def _record_history(self, q, history, fixed=()):
        """记录历史(去重 内部追加末尾 下拉显示反转置顶 上限10条 内存+关窗落盘); 固定词条(下拉词条)不重复入历史"""
        if not q or q in fixed: return
        if q in history: history.remove(q)
        history.append(q)
        if len(history) > 10: del history[:-10]
        self._refresh_search_boxes()

    def _bind_sep_combo(self, combo, parent, boxes_list, on_select=None, on_clear=None):
        """为组合框绑定分隔横线处理: 选中横线还原输入, 其余调on_select; 自动注册清空按钮与销毁清理"""
        prev = [""]
        def on_sel(_e):
            v = combo.get()
            if v == self._SEP_LINE:
                combo.set(prev[0]); return
            prev[0] = v
            if on_select: on_select()
        combo.bind("<<ComboboxSelected>>", on_sel)
        combo.bind("<KeyRelease>", lambda e: prev.__setitem__(0, combo.get()), add="+")
        boxes_list.append(combo)
        self._add_clear_btn(combo, on_clear)
        parent.bind("<Destroy>", lambda e: boxes_list.remove(combo) if combo in boxes_list else None, add="+")

    def _refresh_search_boxes(self):
        """刷新所有存活搜索/筛选下拉框的词条"""
        for boxes, terms in ((self._search_boxes, self._combo_values(self.text_search_terms, self.text_search_history)),
                             (self._style_boxes, self._combo_values(self.style_filter_terms, self.style_filter_history))):
            for w in boxes[:]:
                try:
                    if w.winfo_exists(): w["values"] = terms
                    else: boxes.remove(w)
                except tk.TclError:
                    boxes.remove(w)

    def _refresh_replace_boxes(self):
        """刷新所有存活替换输入框的独立历史下拉(新的在上)"""
        for w in self._replace_boxes[:]:
            try:
                if w.winfo_exists(): w["values"] = self.replace_history[::-1]
                else: self._replace_boxes.remove(w)
            except tk.TclError:
                self._replace_boxes.remove(w)

    def save_search_box_only(self):
        """局部保存: 仅更新[SearchBox]段与search_cfg_editor窗口尺寸"""
        if not self.config_file: return
        try:
            raw = self.config_file.read_text('utf-8')
            pre, sep, rules = raw.partition('[RegexRules]')  # 正则段由regex_manager管理 原样切出
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read_string(pre)
            if geom := self.win_size._states.get('search_cfg_editor'):
                if 'WinSize' not in cfg: cfg.add_section('WinSize')
                cfg['WinSize']['search_cfg_editor'] = geom
            _ps = getattr(self, '_persist_search_state', {})
            cfg['SearchBox'] = {'text_terms': self.write_terms(self.text_search_terms),
                                'style_terms': self.write_terms(self.style_filter_terms),
                                'tag_terms': self.write_terms(getattr(self, 'tag_menu_terms', None) or ['h3', 'p', 'span']),
                                'search_history': self.write_terms(getattr(self, 'text_search_history', [])[-10:]),
                                'replace_history': self.write_terms(getattr(self, 'replace_history', [])[-10:]),
                                'replace_mode': '1' if _ps.get('replace_mode') else '0',
                                'global_match': '1' if _ps.get('global_match') else '0'}
            import io
            with io.StringIO() as buf:
                cfg.write(buf)
                out = buf.getvalue().strip()
            if sep: out += '\n\n[RegexRules]' + rules
            self.config_file.write_text(out + '\n', encoding='utf-8')
            logger.info(f"搜索词条/预览窗保存(局部): {self.config_file}")
        except Exception as e: logger.error(f"保存搜索词条失败: {e}")

    def save_history_state_only(self):
        """关class窗口局部保存: 仅当搜索/替换历史或全局匹配/替换模式状态相对本次打开时有变化才写文件 无变化静默跳过"""
        ps = self._persist_search_state
        base_h, base_r, base_s = self._hist_state_snapshot
        if (self.text_search_history == base_h and self.replace_history == base_r
                and ps.get('replace_mode') == base_s.get('replace_mode') and ps.get('global_match') == base_s.get('global_match')):
            return
        self.save_search_box_only()

    def ensure_default_terms(self):
        """无[SearchBox]段时默认写入 opacity 灰字颜色词条"""
        if not self.config_file: return
        try:
            if '[SearchBox]' in self.config_file.read_text('utf-8'): return  # 已有段不注入
        except Exception: return
        if self._DEFAULT_OPACITY_TERM not in self.text_search_terms:
            self.text_search_terms.append(self._DEFAULT_OPACITY_TERM)
        self.save_search_box_only()

    _sash_cache = {}  # 分割栏位置内存记忆(跨实例 不落盘)
    def edit_search_cfg(self):
        """自定义搜索: 手动编辑正文搜索与样式筛选的下拉词条(保存写入当前配置)"""
        parent = getattr(self, '_cw', self.root)
        d = tk.Toplevel(parent)
        d.title("搜索备选词条与颜色标记"); d.transient(parent)
        if self.win_size:
            rec = self.win_size.setup(d, "search_cfg_editor", f"380x420+{self.root.winfo_x()+80}+{self.root.winfo_y()+80}", mode='cascade')
            d.bind('<Configure>', rec, add='+')
        d.protocol("WM_DELETE_WINDOW", d.destroy); d.focus_force()
        d.minsize(340, 220)  # 保底尺寸 防止记录的窗口几何过小压没按钮区
        # 先pack底部按钮区(side="bottom") 再pack文本框 保证任意窗口尺寸下按钮始终可见
        row = ttk.Frame(d); row.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        def read_terms(t):
            return list(dict.fromkeys(l for l in t.get("1.0", "end").splitlines() if l.strip()))  # 保留首尾空格(词条空格有效) 仅滤空行
        def save_entries():
            self.text_search_terms[:] = read_terms(text_search_box)
            self.style_filter_terms[:] = read_terms(style_filter_box)
            self.tag_menu_terms[:] = [l.strip() for l in tag_menu_box.get("1.0", "end").splitlines() if l.strip()] or ['h3', 'p', 'span']
            self._refresh_search_boxes()
            self.save_search_box_only()  # 局部保存: 仅[SearchBox]段与search_cfg_editor 不影响其他
            if cb := getattr(self, '_on_color_terms_changed', None): cb()  # 预览窗存在: 重标类表 + 预览正文失效重渲染
            elif cb := getattr(self, '_paint_tree_colors', None): cb()  # 无预览窗: 直接重标类表颜色(修复需重开窗口才生效)
            d.destroy()
        ttk.Button(row, text="保存", width=5, command=save_entries).pack(side="right")
        ttk.Button(row, text="关闭", width=5, command=d.destroy).pack(side="right", padx=(0, 5))
        pw_cfg = ttk.PanedWindow(d, orient="vertical"); pw_cfg.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        f1 = ttk.Frame(pw_cfg); pw_cfg.add(f1, weight=1)
        ttk.Label(f1, text="正文搜索词条(每行一条;[fg/bg#色号]前景/背景,缺省时前景;[-色号]纯标色不入下拉;双击色号改色)").pack(side="top", anchor="w")
        text_search_box = tk.Text(f1, font=('Consolas', 10), undo=True, height=6)
        sb1 = ttk.Scrollbar(f1, command=text_search_box.yview); sb1.pack(side="right", fill="y")
        text_search_box.config(yscrollcommand=sb1.set); text_search_box.pack(fill="both", expand=True)
        text_search_box.insert("1.0", "\n".join(self.text_search_terms))
        self._bind_multiline_paste(text_search_box)  # 跨行粘贴自动转义\n (Shift粘贴保留多行)
        f2 = ttk.Frame(pw_cfg); pw_cfg.add(f2, weight=1)
        ttk.Label(f2, text="样式筛选词条(每行一条;[@色号]仅匹配类名,缺省时背景色;[-色号]纯标色不入下拉)").pack(side="top", anchor="w")
        style_filter_box = tk.Text(f2, font=('Consolas', 10), undo=True, height=6)
        sb2 = ttk.Scrollbar(f2, command=style_filter_box.yview); sb2.pack(side="right", fill="y")
        style_filter_box.config(yscrollcommand=sb2.set); style_filter_box.pack(fill="both", expand=True)
        style_filter_box.insert("1.0", "\n".join(self.style_filter_terms))
        self._bind_multiline_paste(style_filter_box)  # 跨行粘贴自动转义\n (Shift粘贴保留多行)
        f3 = ttk.Frame(pw_cfg); pw_cfg.add(f3, weight=1)
        ttk.Label(f3, text="双击/右键标签弹窗词条(每行一条, 多列): [列头]条目 指定所在列(如 [换种]h3); h3 换种; h3- 换种并清空属性; div class=\"a b\" 换种+替换class; div +class=\"a b\" 换种+追加class; class=\"a b\" 仅替换class; style=\"x:y\" 仅替换style; +cls / +color:red 追加(自动识别); <hr/> <br/> 选中标签时插在闭标签后否则光标处; --- 列内分隔横线").pack(side="top", anchor="w")
        tag_menu_box = tk.Text(f3, font=('Consolas', 10), undo=True, height=4)
        sb3 = ttk.Scrollbar(f3, command=tag_menu_box.yview); sb3.pack(side="right", fill="y")
        tag_menu_box.config(yscrollcommand=sb3.set); tag_menu_box.pack(fill="both", expand=True)
        tag_menu_box.insert("1.0", "\n".join(self.tag_menu_terms))
        self._bind_multiline_paste(tag_menu_box)  # 跨行粘贴自动转义\n (Shift粘贴保留多行)
        if _sash := ClassList._sash_cache.get('search_cfg'):
            d.after(100, lambda: pw_cfg.sashpos(0, int(_sash)))
        pw_cfg.bind('<ButtonRelease-1>', lambda e: ClassList._sash_cache.update({'search_cfg': str(pw_cfg.sashpos(0))}) if pw_cfg.winfo_exists() else None)
        # 色号标签按前景/背景渲染实际效果 双击弹调色板替换颜色
        _CRE = re.compile(r'^\[(-)?(fg|bg)?(@?)(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}))?\]', re.IGNORECASE | re.MULTILINE)
        def hl_color_tags(tb, default_role='fg'):
            # 先清旧区间防残留(tag定义可复用)
            for tag in tb.tag_names():
                if tag.startswith('cfg_color_'):
                    tb.tag_remove(tag, '1.0', 'end')
            body = tb.get('1.0', 'end-1c')
            for m in _CRE.finditer(body):
                role = (m.group(2) or '').lower() or default_role
                hex_c = (m.group(4) or '').upper()  # 无色号时空串: 仅占位标签供双击命中不渲染颜色
                tag = f'cfg_color_{role}_{hex_c}'
                if hex_c:  # 有效色号才配置颜色; 空[]/[fg]等仅加标签供双击触发调色板
                    if role == 'bg':
                        tb.tag_configure(tag, background=hex_c)
                    else:  # fg
                        tb.tag_configure(tag, foreground=hex_c)
                tb.tag_add(tag, f'1.0 + {m.start()} chars', f'1.0 + {m.end()} chars')
        def on_color_dblclick(tb, e, default_role='fg'):
            idx = tb.index(f'@{e.x},{e.y}')
            hit = [t for t in tb.tag_names(idx) if t.startswith('cfg_color_')]
            if not hit: return
            ranges = tb.tag_ranges(hit[0])
            for i in range(0, len(ranges), 2):
                if tb.compare(ranges[i], '<=', idx) and tb.compare(idx, '<', ranges[i+1]):
                    m = _CRE.match(tb.get(ranges[i], ranges[i+1]))
                    if not m: break
                    prev = m.group(4)  # 已有色号(无色号时为None)
                    _, hex_c = colorchooser.askcolor(parent=d, initialcolor=prev) if prev else colorchooser.askcolor(parent=d)
                    if hex_c:
                        new = f'[{m.group(1) or ""}{m.group(2) or ""}{m.group(3) or ""}{hex_c.upper()}]'
                        tb.delete(ranges[i], ranges[i+1]); tb.insert(ranges[i], new); hl_color_tags(tb, default_role)
                    break
        # 正文搜索框缺省前景色 样式筛选框缺省背景色(与各自实际渲染一致)
        for tb, dr in ((text_search_box, 'fg'), (style_filter_box, 'bg')):
            hl_color_tags(tb, dr)
            tb.bind('<Double-1>', lambda e, _tb=tb, _dr=dr: on_color_dblclick(_tb, e, _dr))
            tb.bind('<KeyRelease>', lambda e, _tb=tb, _dr=dr: hl_color_tags(_tb, _dr))

    def show_class_list(self):
        cw = tk.Toplevel(self.root)
        self._cw = cw  # 供自定义搜索对话框等定位父窗口
        cw.title("html内样式收集分析")
        def on_class_list_close():
            self._running = False
            # 只取消最新一个链式定时器(run_step每1ms重排,任意时刻仅一个存活,旧id无需逐个取消)
            if getattr(self, '_after_id', None):
                try:
                    cw.after_cancel(self._after_id)
                except Exception:
                    logger.warning("after_cancel 失败(窗口可能已销毁)")
                self._after_id = None
            [cw.after_cancel(aid) for aid in self._after_ids]  # 兜底取消旧列表遗留
            self._after_ids.clear()
            # 兜底取消所有未触发的after定时器(destroy只删命令不取消定时器，悬空定时器会误炸其它回调)
            try:
                for _aid in cw.tk.splitlist(cw.tk.call('after', 'info')):
                    try: cw.after_cancel(_aid)
                    except Exception: pass
            except Exception:
                logger.warning("after info 定时器清理失败")
            # 递归解绑所有深层子组件的 Destroy 事件，彻底阻断 TkinterDnD2 的异常 lambda 回调
            def unbind_destroy_recursive(widget):
                for child in widget.winfo_children():
                    child.unbind('<Destroy>')
                    unbind_destroy_recursive(child)
            unbind_destroy_recursive(cw)
            try:
                cw.unbind('<Destroy>')
            except Exception:
                logger.warning("cw.unbind('<Destroy>') 失败")
            self.save_history_state_only()  # 关窗局部保存: 仅当有搜索/替换历史或状态变化时写入(独立info日志)
            clean_old_epub_cache()  # 关闭时清理旧缓存
            # 逐个销毁子组件容错TclError: tkdnd已删widget的Tcl命令, Tkinter再删报错中断destroy致僵尸窗口, 故逐个销毁再兜底destroy主窗
            for c in list(cw.children.values()):
                try: c.destroy()
                except tk.TclError: pass
            try:
                cw.destroy()
            except tk.TclError as e:
                logger.warning(f"主窗口销毁告警(可忽略): {e}")
        cw.protocol("WM_DELETE_WINDOW", on_class_list_close)
        rec = self.win_size.setup(cw, "class_list_main", f"600x480+{self.root.winfo_x()+30}+{self.root.winfo_y()+30}", mode='cascade')
        cw.bind('<Configure>', rec, add='+')
        pw = ttk.PanedWindow(cw, orient="horizontal"); pw.pack(fill="both", expand=True)

        # 左侧文件树
        lf, rf = ttk.Frame(pw, width=150), ttk.Frame(pw, width=330)
        [pw.add(f, weight=w) for f, w in [(lf, 1), (rf, 0)]]
        
        # 顶部工具栏
        lf_top = ttk.Frame(lf); lf_top.pack(fill="x", padx=3, pady=4)
        ttk.Label(lf_top, text="文件列表").pack(side="left")
        
        def save_changes():
            if not self.modified_files: return messagebox.showinfo("保存", "没有检测到任何更改。", parent=cw)
            try:
                logger.info(f"保存EPUB，共 {len(self.modified_files)} 个项被修改或删除。")
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".epub"); os.close(tmp_fd)
                with zipfile.ZipFile(self.epub_path, "r") as z_in, zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as z_out:
                    # 写入原包中未被修改的文件
                    [z_out.writestr(item.filename, z_in.read(item.filename)) for item in z_in.infolist() if item.filename not in self.modified_files]
                    # 写入内存中新增或修改的文件内容（包括_sync_opf生成的opf字节流），content为None则代表删除
                    [z_out.writestr(path, content) for path, content in self.modified_files.items() if content is not None]
                shutil.move(tmp_path, self.epub_path); self.modified_files.clear()
                logger.success("修改已成功保存至epub。"); messagebox.showinfo("保存", "修改已成功保存至EPUB。", parent=cw)
            except Exception as e: 
                logger.exception(f"保存epub失败: {e}"); messagebox.showerror("保存失败", str(e), parent=cw)
        
        ttk.Button(lf_top, text="保存", width=5, command=save_changes).pack(side="right")
        ttk.Button(lf_top, text="词条/标色", width=8, command=self.edit_search_cfg).pack(side="right", padx=(0, 4))
        lf_tree_frame = ttk.Frame(lf); lf_tree_frame.pack(fill="both", expand=True, padx=(3, 0), pady=2)
        # 添加一个隐藏列用于右对齐显示图片计数，设置 width 并禁止拉伸
        ftree = ttk.Treeview(lf_tree_frame, show="tree", selectmode="extended", columns=("img_count",))
        f_vsb = ttk.Scrollbar(lf_tree_frame, command=ftree.yview); f_vsb.pack(side="right", fill="y")
        ftree.config(yscrollcommand=f_vsb.set)
        ftree.pack(side="left", fill="both", expand=True)
        ftree.column("img_count", width=30, anchor="center", stretch=False)
        
        # 文件树右键菜单 选中项删除确认及内存标记
        ftree_menu = tk.Menu(ftree, tearoff=0)
        ftree_menu.add_command(label="删除文件", command=lambda: (sel := ftree.selection()) and
                                    messagebox.askyesno("确认", f"确认删除选中的 {len(sel)} 个项目?", parent=cw) and
                                    ([paths := [ftree.item(i, "tags")[0] for i in sel]],
                                    _sync_opf([p for p in paths if not p.endswith('/')]), # 局部函数 同步OPF引用删除
                                    [(self.modified_files.__setitem__(p, None), ftree.delete(i)) for i, p in zip(sel, paths)]))
        ftree_menu.add_separator()
        # 右键复制: 多选时逐条换行拼接 目录条目以/结尾取Path().name为空 则原样复制
        def _copy_paths(transform, tip):
            paths = [ftree.item(i, "tags")[0] for i in ftree.selection()]
            out = [transform(p) or p for p in paths]
            if out:
                self.root.clipboard_clear(); self.root.clipboard_append('\r\n'.join(out))
                logger.info(f"{tip}: 复制了 {len(out)} 项")
        ftree_menu.add_command(label="复制文件名", command=lambda: _copy_paths(lambda p: os.path.splitext(os.path.basename(p))[0], "文件名(不含后缀)"))
        ftree_menu.add_command(label="复制含后缀文件名", command=lambda: _copy_paths(lambda p: os.path.basename(p), "文件名"))
        ftree_menu.add_command(label="复制完整路径", command=lambda: _copy_paths(lambda p: p, "完整路径"))
        ftree.bind("<Button-3>", lambda e: (iid := ftree.identify_row(e.y)) and (ftree.selection_add(iid), ftree_menu.post(e.x_root, e.y_root)))

        if DND_FILES:
            # 拖入处理函数：识别目标目录、集成覆盖确认与动态统计
            def drop_handler(event):
                if self._dragging: return "break"
                try:
                    raw = re.findall(r"\{(.*?)\}|\S+", event.data) if "{" in event.data else event.data.split()
                    paths = [p for f in raw if (p := Path(f.strip('{} "'))) and p.is_file()]
                    if not paths: return messagebox.showwarning("提示", f"数据无效: {event.data[:50]}", parent=cw)
                    rid = ftree.identify_row(event.y_root - ftree.winfo_rooty())
                    tag = ftree.item(rid, "tags")[0] if rid else ""
                    tdir = (Path(tag).as_posix() + "/" if tag.endswith('/') else Path(tag).parent.as_posix() + "/").lstrip("./")
                    piid, (a, c, s) = self.n_map.get(tdir.rstrip("/"), ""), [0, 0, 0]
                    exs = {ftree.item(i, "tags")[0]: i for i in ftree.get_children(piid)}
                    for src in paths:
                        dst = f"{tdir}{src.name}"
                        if (ex := dst in exs) and not messagebox.askyesno("覆盖", f"替换 {dst}?", parent=cw):
                            s += 1; continue
                        self.modified_files[dst] = src.read_bytes() # 写入内存暂存
                        (ftree.insert(piid, "end", text=dst, tags=(dst,)), [a := a + 1]) if not ex else [c := c + 1]
                    res = [f"- {k}: {v}个" for k, v in zip(["新增", "覆盖", "跳过"], [a, c, s]) if v]
                    logger.info(f"拖入导入完成 | 目标目录: '{tdir}' | 结果: 新增{a} 覆盖{c} 跳过{s}")
                    messagebox.showinfo("导入结果", "完成：\n" + "\n".join(res), parent=cw)
                except Exception as e: 
                    logger.exception(f"拖入文件处理发生错误: {e}")
                    messagebox.showerror("错误", str(e), parent=cw)
            # 拖出处理函数
            def drag_out_handler(event):
                # 双击预览的DragInit由preview_file设suppress标志拦截(一次性)
                if getattr(drag_out_handler, 'suppress_drag', False):
                    drag_out_handler.suppress_drag = False
                    ftree.tk.call('set', '::tkdnd::_state', 'press'); return "break"
                # 0.5秒内刚按下过则视为点击 非拖拽
                pt = getattr(drag_out_handler, 'press_t', None)
                if pt is not None and time.time() - pt < 0.5:
                    ftree.tk.call('set', '::tkdnd::_state', 'press'); return "break"
                if self._dragging: return "break"
                self._dragging = True # 上锁
                sel = getattr(drag_out_handler, 'locked_sel', ftree.selection())
                if not sel: return "break"
                ftree.selection_set(sel) 
                ftree.update_idletasks() # 强制 UI 立即重绘高亮，防止视觉闪烁
                try: # 指纹目录：epub_out_{路径Hash}_{修改时间} 按需创建 atexit回收
                    st = Path(self.epub_path).stat()
                    h_p = abs(hash(str(Path(self.epub_path).resolve())))
                    ts = time.strftime("%y%m%d_%H%M%S", time.localtime(st.st_mtime))
                    out = self.sesame_root / f"epub_out_{h_p}_{ts}"
                    out.exists() or [out.mkdir(parents=True), atexit.register(lambda: shutil.rmtree(out, ignore_errors=True))]
                    files = [f'{{{t.resolve().as_posix()}}}' for i in sel if not (p := ftree.item(i, "tags")[0]).endswith('/')
                             and (t := out / Path(p).name).write_bytes(self.modified_files.get(p) or 
                             zipfile.ZipFile(self.epub_path).read(p))]
                    if files: logger.info(f"拖出导出了 {len(files)} 个文件到临时目录")
                    return ('copy', DND_FILES, " ".join(files)) if files else "break"
                except Exception as ex: 
                    logger.exception(f"拖出文件发生错误: {ex}")
                    messagebox.showerror("导出错误", str(ex), parent=cw); return "break"
                finally: # 延迟解锁，给 UI 响应留出缓冲时间
                    cw.after(500, lambda: setattr(self, '_dragging', False))
            # 锁定多选: Button-1按下时记录选中集 供DragInit恢复(防tkdnd重置)
            ftree.bind("<<TreeviewSelect>>", lambda e: setattr(drag_out_handler, 'last_sel', ftree.selection()))
            def lock_sel(e):
                drag_out_handler.press_t = time.time()
                rid, l_sel = ftree.identify_row(e.y), getattr(drag_out_handler, 'last_sel', ())
                setattr(drag_out_handler, 'locked_sel', l_sel if rid in l_sel else (rid,))
            ftree.bind("<Button-1>", lock_sel, add="+")
            # 兜底: toplevel层记录按下时刻 防tkdnd在widget层break掉lock_sel
            cw.bind("<Button-1>", lambda e: setattr(drag_out_handler, 'press_t', time.time()), add="+")

            # 注册拖放事件
            ftree.drop_target_register(DND_FILES); ftree.dnd_bind("<<Drop>>", drop_handler)
            ftree.drag_source_register(1, DND_FILES); ftree.dnd_bind("<<DragInitCmd>>", drag_out_handler)

        # 右侧class列表
        filter_frame = ttk.Frame(rf); filter_frame.pack(fill="x", padx=(0, 3), pady=2)
        filter_var = tk.StringVar()
        # 样式筛选改为可编辑下拉组合框(自定义词条+筛选历史 仅内存)
        fb = ttk.Combobox(filter_frame, textvariable=filter_var, values=self._combo_values(self.style_filter_terms, self.style_filter_history))
        fb.pack(side="left", fill="x", expand=True)
        self._bind_sep_combo(fb, cw, self._style_boxes)  # 筛选由textvariable的trace自动触发do_filter
        self._bind_edit_menu(fb)  # 右键编辑菜单(剪切/复制/黏贴/删除/全选)
        self._bind_multiline_paste(fb)  # 跨行粘贴自动转义\n
        tf = ttk.Frame(rf); tf.pack(fill="both", expand=True, padx=(0, 3), pady=2)
        tree = ttk.Treeview(tf, columns=("count",), show="tree headings", selectmode="extended")

        # 排序逻辑函数
        self.lc = None
        def apply_sort():
            col = self.lc or "#0"
            # 未点过表头时默认类名升序(与初始插入时的字母顺序重排一致)
            reverse = not self.st[col] if self.lc else False
            for n in nodes.values():
                items = sorted([(tree.set(c, "count"), tree.item(c, "text"), c) for c in tree.get_children(n)], key=lambda x: int(x[0]) if col=="count" else x[1].lower(), reverse=reverse)
                [tree.move(it[2], n, i) for i, it in enumerate(items)]
            # 表头箭头指示: 仅在点过表头后显示 默认状态(含筛选/清空后)无箭头
            [tree.heading(c, text=f"{'类名' if c=='#0' else '总量'}{(' ▲' if self.st[c] else ' ▼') if c==self.lc else ''}") for c in ["#0", "count"]]
        def sort_col(col):
            # 首次点击即切换到当前默认排序的反向(类名默认升序→首点降序▼ 总量默认降序→首点升序▲) 再点切回
            self.st[col], self.lc = ((col != "#0") if col != self.lc else not self.st[col]), col
            apply_sort()

        # 初始表头设置
        [tree.heading(c, text=t, anchor=a, command=lambda _c=c: sort_col(_c)) for c, t, a in [("#0", "类名", "w"), ("count", "总量", "center")]]
        [tree.column(c, width=w, anchor=a) for c, w, a in [("#0", 200, "w"), ("count", 50, "center")]]
        tree.grid(row=0, column=0, sticky="nsew"); vsb = ttk.Scrollbar(tf, command=tree.yview); vsb.grid(row=0, column=1, sticky="ns")
        tree.config(yscrollcommand=vsb.set); tf.columnconfigure(0, weight=1); tf.rowconfigure(0, weight=1)

        ttk.Style().configure("Treeview", indent=8) #调整Treeview缩进余白
        # py3.8/Tk8.6 Bug: Treeview map 的 ('!disabled','!selected') 条目覆盖 tag 颜色
        try:
            s = ttk.Style()
            for opt in ('foreground', 'background'):
                s.map('Treeview', **{opt: [e for e in (s.map('Treeview', opt) or []) if e[:2] != ('!disabled', '!selected')]})
            s.layout('ColorTV', s.layout('Treeview'))
            s.layout('ColorTV.Item', s.layout('Treeview.Item'))
            s.configure('ColorTV', indent=8, background='#ffffff', fieldbackground='#ffffff', borderwidth=0, foreground='black')
            s.map('ColorTV', foreground=[('selected', '#ffffff')], background=[('selected', '#0078D7')])
            tree.configure(style='ColorTV')
        except Exception as e: logger.debug(f"Treeview 样式失败: {e}")
        # 默认展开控制
        nodes = {k: tree.insert("", "end", text=k, open=(k in ['Class列表', 'Span列表', '图片Class列表'])) for k in self.cats}
        class_to_iid = {}
        filter_var.trace_add("write", lambda *args: do_filter()) # 绑定筛选输入变化事件

        # 预览逻辑+搜索框
        def preview_file(e):
            if DND_FILES:
                ftree.tk.call('set', '::tkdnd::_state', 'press')  # 重置tkdnd状态机
                drag_out_handler.suppress_drag = True  # 一次性拦截下次DragInit(防双击预览误触发拖出)
                cw.after(2000, lambda: setattr(drag_out_handler, 'suppress_drag', False))
            if not (sel := ftree.selection()) or not ftree.exists(sel[0]) or not (p := ftree.item(sel[0], "tags")[0]) or p.endswith('/'): return
            try:
                mtime = os.path.getmtime(self.epub_path)
                # 图片读取逻辑:解压至临时文件并调用默认图片查看器
                exts = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
                if p.lower().endswith(exts):
                    ts = time.strftime("%y%m%d_%H%M%S", time.localtime(mtime))
                    is_mod = p in self.modified_files and self.modified_files[p]
                    prefix = "mod" if is_mod else "img"
                    td = self.sesame_root / f"epub_{prefix}_{abs(hash(self.epub_path))}_{ts}"
                    # 目录创建与回收逻辑：若不存在则创建并atexit注册删除
                    if not td.exists():
                        td.mkdir(parents=True, exist_ok=True)
                        atexit.register(lambda d=td: shutil.rmtree(d, ignore_errors=True))
                    target = td / Path(p).name
                    if is_mod:
                        target.write_bytes(self.modified_files[p])
                    elif not target.exists():
                        # 一次性全量解压(这里可能需要性能优化 改成异步处理或者按需解压)
                        with zipfile.ZipFile(self.epub_path, 'r') as z:
                            [(td / Path(x).name).write_bytes(z.read(x)) for x in z.namelist() if x.lower().endswith(exts)]
                    return os.startfile(target) if hasattr(os, 'startfile') else __import__('subprocess').run(['open', target])
                # 内存读取预览文本逻辑 显示内容+正则搜索
                key = "class_list_preview"
                rec = self.win_size.setup(win := tk.Toplevel(cw), key, f"600x500+{self.root.winfo_x()-300}+{self.root.winfo_y()+50}", mode='cascade')
                win.bind('<Configure>', rec, add='+')
                win.protocol("WM_DELETE_WINDOW", win.destroy); win.focus_force()
                
                # 状态存储 (用于搜索) 
                state = {"current_file": p, "results": [], "by_file": {}, "search_index": -1,
                         "last_q": None, "paint_token": 0, "painted": None, "file_pos": {},
                         "html_cache": {}, "cache_order": [], "scanned_files": set(),
                         "color_cache": {}, "color_tags": {}, "color_compiled": None,
                         "color_terms_key": None, "color_painted": None,
                         "tag_pinned": False}

                # 性能优化: 兼容 py3.8，将绝对字符索引秒转为 Tkinter 的 "line.col" 格式，实现 O(1) 直接内存定位，彻底消灭 B-Tree 遍历卡顿
                def to_tk_idx(pos, text): return "%d.%d" % (text.count('\n', 0, pos) + 1, pos - text.rfind('\n', 0, pos) - 1)

                # 定义跳过图片的获取逻辑 (用于左右键切换)
                def get_next_text(rev):
                    curr_sel = ftree.selection()[0]
                    bro = [b for b in ftree.get_children(ftree.parent(curr_sel)) if ftree.exists(b)]
                    valid = [b for b in bro if not (p := ftree.item(b, "tags")[0].lower()).endswith(exts) and not p.endswith('/')]
                    if not valid: return curr_sel
                    if curr_sel in valid:
                        return valid[(valid.index(curr_sel) + (-1 if rev else 1)) % len(valid)]
                    return valid[0]

                sf = ttk.Frame(win); sf.pack(fill="x", padx=2, pady=2)
                
                # 增加输入验证，限制最大输入长度为1000，防止粘贴超长文本卡死
                def validate_entry(new_value):
                    return len(new_value) <= 1000
                vcmd = (win.register(validate_entry), '%P')
                # 正文搜索框改为可编辑下拉组合框(自定义词条+搜索历史 仅内存)
                se = ttk.Combobox(sf, validate="key", validatecommand=vcmd, values=self._combo_values(self.text_search_terms, self.text_search_history))
                se.pack(side="left", fill="x", expand=1)
                se.bind("<Return>", lambda e: do_find(reset=True))  # 回车触发搜索(历史由do_find延迟1秒记录)
                self._bind_sep_combo(se, win, self._search_boxes, on_select=lambda: do_find(reset=True), on_clear=lambda: do_find(reset=True))
                self._bind_edit_menu(se)  # 右键编辑菜单(剪切/复制/黏贴/删除/全选)
                self._bind_multiline_paste(se, on_change=lambda: do_find(reset=True))  # 跨行粘贴自动转义\n 并立即搜索
                
                # 全局搜索复选框(状态随[SearchBox]局部记忆)
                global_search_var = tk.BooleanVar(value=bool(self._persist_search_state.get('global_match')))
                global_search_var.trace_add("write", lambda *a: self._persist_search_state.__setitem__('global_match', global_search_var.get()))
                # 勾选切换时强制重扫但不导航跳转(避免把用户"弹回"到结果1所在的文件) 重扫后同步回切换前的匹配位置
                ttk.Checkbutton(sf, text="全局匹配", variable=global_search_var, command=lambda: do_find(reset=True, force=True, navigate=False)).pack(side="left", padx=5)
                sl = ttk.Label(sf, text="0/0"); sl.pack(side="right", padx=5)
                [ttk.Button(sf, text=t, width=3, command=lambda r=v: do_find(rev=r)).pack(side="right") for t, v in [("↓", 0), ("↑", 1)]]

                # 替换模式: 勾选"替换"在搜索行与正文间展开替换行(无缝切换 不动搜索结果/标色/当前匹配位置)
                replace_mode_var = tk.BooleanVar(value=bool(self._persist_search_state.get('replace_mode')))
                replace_mode_var.trace_add("write", lambda *a: self._persist_search_state.__setitem__('replace_mode', replace_mode_var.get()))
                # 替换行: 替换输入框(独立历史下拉)+单个替换(当前目标)/全部替换; 替换语法沿用regex_manager(re.sub模板: \1 \g<name>...)
                rf2 = ttk.Frame(win)
                re_box = ttk.Combobox(rf2, validate="key", validatecommand=vcmd, values=self.replace_history[::-1])
                self._bind_edit_menu(re_box)  # 右键编辑菜单(剪切/复制/黏贴/删除/全选)
                self._bind_multiline_paste(re_box)  # 跨行粘贴自动转义\n
                self._add_clear_btn(re_box)
                # 右侧垫片与搜索行右侧(计数+↑↓)簇等宽 使替换框与搜索框左右缘对齐(动态同步)
                rspacer = ttk.Frame(rf2, width=1)
                btn_reall = ttk.Button(rf2, text="全部替换", command=lambda: do_replace_all())
                btn_re = ttk.Button(rf2, text="替换", command=lambda: do_replace_cur())
                rspacer.pack(side="right"); btn_reall.pack(side="right"); btn_re.pack(side="right", padx=(0, 3))
                re_box.pack(side="left", fill="x", expand=1)
                def _align_replace_row(_e=None):
                    if not rf2.winfo_ismapped(): return
                    try:
                        target = sf.winfo_x() + se.winfo_x() + se.winfo_width() - rf2.winfo_x()  # 搜索框右缘(相对win)
                        rspacer.config(width=max(0, rf2.winfo_width() - btn_re.winfo_width() - 3 - btn_reall.winfo_width() - int(target)))
                    except Exception: pass
                win.bind("<Configure>", lambda e: win.after_idle(_align_replace_row), add="+")
                rf2.bind("<Map>", lambda e: win.after_idle(_align_replace_row))  # 切换显示时重对齐
                sl.bind("<Configure>", lambda e: win.after_idle(_align_replace_row), add="+")  # 计数宽度变化(0/0→12/345)时重对齐
                win.after(80, _align_replace_row)
                self._replace_boxes.append(re_box)
                re_box.bind("<Destroy>", lambda e: self._replace_boxes.remove(re_box) if re_box in self._replace_boxes else None, add="+")
                # 注意: 不绑Return执行替换(IME确认回车会以未写完的替换串误替换 致匹配丢失) 执行只走"替换/全部替换"按钮
                def toggle_replace():
                    if replace_mode_var.get(): rf2.pack(fill="x", padx=2, pady=(0, 2), before=txt)  # before=txt保持行序(初始pack时txt未pack无需before)
                    else: rf2.pack_forget()
                ttk.Checkbutton(sf, text="替换", variable=replace_mode_var, command=toggle_replace).pack(side="left", padx=5)
                if replace_mode_var.get(): rf2.pack(fill="x", padx=2, pady=(0, 2))  # 记忆为替换模式时初始展开
                # wrap="word"渲染颜色多起来会造成卡顿 改为wrap="char"减轻渲染压力
                txt = tk.Text(win, font=('Consolas', 10), wrap="char", undo=True, autoseparators=True)
                sv = ttk.Scrollbar(win, command=txt.yview); txt.config(yscrollcommand=sv.set)
                [f.pack(side=s, fill=y, expand=e) for f,s,y,e in [(sv,"right","y",0), (txt,"left","both",1)]]
                [txt.tag_config(k, background=v) for k,v in [("m", "yellow"), ("cur", "orange")]]
                # sel(系统选中)提至最高优先级，避免选中标黄文本会难以辨别文字(白字黄底)
                txt.tag_raise("sel")
                # ===== 双击: 命中标签→弹窗修改 / 命中空白→切换编辑态(仅方向键移光标) =====
                # 左键命中标签→高亮开闭配对(标出闭合 不滚动) 双击标签→快速菜单(换种/删标签/删整块) 右键→同菜单
                # 方向键: 浏览态(默认)→切文件/导航结果(原逻辑) 编辑态(空白双击)→移动光标; 正文始终可编辑/可复制; Ctrl+Z/Y 撤销重做
                txt.tag_config("tagsel", background="#379BEA", foreground="#000000")  # 选中的标签本身(饱和蓝+黑字)
                txt.tag_config("tagpair", background="#379BEA", foreground="#000000")   # 配对的闭合标签(与tagsel同色 头/闭合一致强调)
                # 层级(自下而上): tagsel/tagpair(点选蓝) < m/cur(匹配黄/橙) < 标色tag < sel(手动选中)
                # 点选蓝垫底: 匹配黄叠加其上(黄底+tagsel黑字) 双方均可见不互相覆盖
                txt.tag_lower("tagsel", "m"); txt.tag_lower("tagpair", "m")
                txt.tag_raise("sel")  # 系统手动选中始终最上
                state.update({"tagsel": None, "loading": False, "loaded_content": "", "edit_mode": False})  # tagsel=(open_s,open_e,close_s,close_e,name) close可能None; loaded_content=载入态基准; edit_mode=编辑态(双击切换)

                _TAG_HEAD = re.compile(r'<(/?)([a-zA-Z][a-zA-Z0-9]*)\b')
                _ATTR_RE = re.compile(r'(\+?)([A-Za-z_][\w:-]*)\s*=\s*"([^"]*)"|(\+?)([A-Za-z_][\w:-]*)\s*=\s*\'([^\']*)\'')  # 组1/4=可选+ 2/5=属性名 3/6=值
                _VOID_TAGS = {'br','hr','img','input','meta','link','col','area','base','embed','source','track','wbr',
                              'path','rect','circle','ellipse','line','polyline','polygon','stop','use','image','param','colgroup'}

                def _tk_to_off(tk_idx, content):
                    """tk 'line.col' → 绝对字符偏移(按当前正文重建行首表 仅点击时调用 O(n))"""
                    line, col = tk_idx.split("."); line, col = int(line), int(col)
                    starts = [0]
                    for i, ch in enumerate(content):
                        if ch == '\n': starts.append(i + 1)
                    return (starts[line - 1] + col) if line - 1 < len(starts) else len(content)

                def _tag_at(content, off):
                    """定位 content[off] 处 HTML 标签; 返回 (tag_start, tag_end, name, is_closing) 或 None"""
                    lt = content.rfind('<', 0, off + 1)
                    if lt < 0: return None
                    gt = content.find('>', lt)
                    if gt < 0 or off > gt: return None  # off 须落在 <..> 区间内
                    m = _TAG_HEAD.match(content[lt:gt + 1])
                    if not m: return None
                    return (lt, gt + 1, m.group(2).lower(), bool(m.group(1)))

                def _pair_of(content, ts, te, name, is_closing):
                    """由开找闭 / 由闭找开(处理嵌套); 返回 (pair_start, pair_end) 或 None"""
                    if is_closing:
                        depth = 1; pos = ts
                        while pos > 0:
                            lt = content.rfind('<', 0, pos)
                            if lt < 0: return None
                            gt = content.find('>', lt)
                            if gt < 0 or gt >= ts: pos = lt; continue
                            seg = content[lt:gt + 1]; m = _TAG_HEAD.match(seg)
                            if m and m.group(2).lower() == name:
                                if m.group(1): depth += 1
                                elif not seg.endswith('/>') and name not in _VOID_TAGS:
                                    depth -= 1
                                    if depth == 0: return (lt, gt + 1)
                            pos = lt
                        return None
                    else:
                        if name in _VOID_TAGS or content[te - 2:te - 1] == '/': return None  # 自闭合/void无配对
                        depth = 1; pos = te
                        while pos < len(content):
                            lt = content.find('<', pos)
                            if lt < 0: return None
                            gt = content.find('>', lt)
                            if gt < 0: return None
                            seg = content[lt:gt + 1]; m = _TAG_HEAD.match(seg)
                            if m and m.group(2).lower() == name:
                                if m.group(1):
                                    depth -= 1
                                    if depth == 0: return (lt, gt + 1)
                                elif not seg.endswith('/>'): depth += 1
                            pos = gt + 1
                        return None

                def _clear_tagsel():
                    txt.tag_remove("tagsel", "1.0", "end"); txt.tag_remove("tagpair", "1.0", "end")
                    state["tagsel"] = None
                    if state.get("refresh_popup"): state["refresh_popup"]()

                def _select_tag(off):
                    """选中 off 处标签: 高亮本身+配对闭合(仅标色不滚动 避免大标签瞬间飞到闭合处致失向); 返回 tagsel 或 None。
                    点选蓝垫底: 匹配黄/标色叠加其上(叠加显示 双方均可见)"""
                    _clear_tagsel()
                    content = txt.get("1.0", "end-1c")
                    info = _tag_at(content, off)
                    if not info: return None
                    ts, te, name, is_closing = info
                    pair = _pair_of(content, ts, te, name, is_closing)
                    txt.tag_add("tagsel", to_tk_idx(ts, content), to_tk_idx(te, content))
                    if pair:
                        txt.tag_add("tagpair", to_tk_idx(pair[0], content), to_tk_idx(pair[1], content))
                        state["tagsel"] = (ts, te, pair[0], pair[1], name)
                    else:
                        state["tagsel"] = (ts, te, None, None, name)
                    if state.get("refresh_popup"): state["refresh_popup"]()
                    return state["tagsel"]

                def _commit_current():
                    """把当前正文(若有改动)写入 modified_files 并同步缓存"""
                    cf = state.get("current_file")
                    if not cf or state.get("loading"): return
                    try: cur = txt.get("1.0", "end-1c")
                    except tk.TclError: return
                    if cur != state["html_cache"].get(cf, ""):
                        self.modified_files[cf] = cur.encode("utf-8")
                        state["html_cache"][cf] = cur

                def _sync_dirty_mark():
                    """刷新标题: 文件名 + ●(相对载入态有改动; 撤销到最前即清●)"""
                    cf = state.get("current_file") or ""
                    try: cur = txt.get("1.0", "end-1c")
                    except tk.TclError: cur = state.get("loaded_content")
                    win.title(f"{cf}{' ●' if cur != state.get('loaded_content') else ''}")

                def _set_edit_mode(on):
                    """双击空白处切换编辑态: 仅切换方向键行为(浏览=切文件/导航结果 编辑=移光标)。
                    正文始终可编辑/可复制 不变色; 退出时提交改动并清标签选中。"""
                    if state.get("edit_mode") == on: return
                    state["edit_mode"] = on
                    if on:
                        txt.focus_set()
                    else:
                        _commit_current(); _clear_tagsel()
                    _sync_dirty_mark()

                def _ensure_editable():
                    """返回是否临时解锁了正文(供标签操作/撤销重做在只读态下执行) 调用方需按返回值还原"""
                    if str(txt.cget('state')) == 'disabled':
                        txt.config(state="normal"); return True
                    return False

                def _apply_splices(splices, content, resel_off=None):
                    """对 txt 做多段替换(按原偏移 倒序执行防漂移) 包入undo分隔符; 之后同步缓存并重渲染。
                    不做全量delete+insert 故不产生位移/yview跳变。只读态下临时解锁执行。
                    resel_off: 操作后重选该偏移处标签(恢复点选 防目标瞬间丢失; 前段splice不改变其前偏移)。"""
                    unlocked = _ensure_editable()
                    txt.configure(autoseparators=False)  # 关自动分隔 保证本次多段编辑归为单一撤销单元
                    txt.edit_separator()
                    for s, e, repl in sorted(splices, key=lambda x: x[0], reverse=True):
                        txt.delete(to_tk_idx(s, content), to_tk_idx(e, content))
                        if repl: txt.insert(to_tk_idx(s, content), repl)
                    txt.edit_separator()
                    txt.configure(autoseparators=True)
                    if unlocked: txt.config(state="disabled")
                    new = txt.get("1.0", "end-1c")
                    cf = state["current_file"]
                    state["html_cache"][cf] = new
                    self.modified_files[cf] = new.encode("utf-8")
                    for k in ("painted", "color_painted"): state[k] = None
                    state["by_file"].pop(cf, None); state["scanned_files"].discard(cf)
                    state["color_cache"].pop(cf, None)
                    _clear_tagsel()
                    async_render_colors(new, cf)
                    paint_m_async(new)
                    if state.get("last_q"): do_find(reset=True, navigate=False)
                    _sync_dirty_mark()
                    if resel_off is not None: _select_tag(resel_off)  # 重渲染后再恢复点选(混合色按新tags计算)
                    return new

                def _parse_open_tag(seg):
                    """解析开标签: 返回 (slash, name, attrs_dict, order_list, self_closing)"""
                    m = _TAG_HEAD.match(seg)
                    slash, name = m.group(1), m.group(2)
                    attrs, order = {}, []
                    if not slash:  # 闭标签无属性
                        seen = set()
                        for am in _ATTR_RE.finditer(seg):
                            k = am.group(2) if am.group(2) is not None else am.group(5)
                            v = am.group(3) if am.group(1) is not None else am.group(6)
                            if k.lower() not in seen: order.append(k); seen.add(k.lower())
                            attrs[k] = v
                    return slash, name, attrs, order, seg.rstrip().endswith('/>')

                def _build_open_tag(name, attrs, order, self_closing):
                    s = '<' + name
                    for k in order: s += f' {k}="{attrs[k]}"'
                    return s + ('/>' if self_closing else '>')

                def _parse_tag_item(item):
                    """解析菜单条目语法: 返回 (tag, attrs, order, append_map, clear_attrs)
                    语法:
                      h3                      仅换标签种类(属性不动)
                      h3-                     换标签种类并清空标签属性(正文不动)
                      div class="a b"         换标签+替换class
                      div +class="a b"        换标签+追加class
                      class="a b"             仅替换class(不动标签)
                      +class="a" / +style="x" 追加到指定属性(不动标签)
                      +cls / +color:red       裸token追加 自动识别(含:为style 否则class)"""
                    s = item.strip()
                    attrs, order, append_map = {}, [], {}
                    for am in _ATTR_RE.finditer(s):
                        plus = am.group(1) if am.group(1) is not None else am.group(4)
                        k = am.group(2) if am.group(2) is not None else am.group(5)
                        v = am.group(3) if am.group(1) is not None else am.group(6)
                        lk = k.lower()
                        if lk not in append_map: order.append(k)
                        attrs[k] = v; append_map[lk] = (plus == '+')
                    rest = _ATTR_RE.sub('', s).strip()  # 去掉属性后剩余
                    tag, clear_attrs = None, False
                    if rest:
                        if rest.startswith('+'):  # 裸token追加 自动识别 class/style
                            tok = rest[1:].strip()
                            if tok and not attrs:
                                if ':' in tok: attrs['style'] = tok; order = ['style']; append_map['style'] = True
                                else: attrs['class'] = tok; order = ['class']; append_map['class'] = True
                        elif rest.endswith('-'):  # 形如 h3- 换种并清空标签属性(正文不动)
                            cand = rest[:-1].strip()
                            if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9]*', cand):
                                tag = cand; clear_attrs = True
                            else:
                                m2 = re.match(r'([a-zA-Z][a-zA-Z0-9]*)', rest)
                                if m2: tag = m2.group(1)
                        elif re.fullmatch(r'[a-zA-Z][a-zA-Z0-9]*', rest):
                            tag = rest
                        else:
                            m2 = re.match(r'([a-zA-Z][a-zA-Z0-9]*)', rest)
                            if m2: tag = m2.group(1)
                    return tag, attrs, order, append_map, clear_attrs

                def _apply_tag_item(item):
                    """按条目语法修改选中标签: 换种类/替换或追加class或style"""
                    s = state.get("tagsel")
                    if not s: return
                    ts, te, cs, ce, name = s
                    content = txt.get("1.0", "end-1c")
                    # tagsel的(ts,te)是被点中的标签(可能开/闭) 需定位真正的开标签
                    clicked = content[ts:te]
                    if clicked.startswith('</'):
                        if cs is None: return  # 闭标签无配对 无法改属性
                        o_ts, o_te, c_ts, c_te = cs, ce, ts, te
                    else:
                        o_ts, o_te, c_ts, c_te = ts, te, cs, ce
                    slash, oname, attrs, order, sc = _parse_open_tag(content[o_ts:o_te])
                    itag, iattrs, iorder, append_map, clear_attrs = _parse_tag_item(item)
                    new_name = itag if itag else oname
                    if clear_attrs: attrs, order = {}, []  # 清空标签全部属性(正文不动)
                    existing = {k.lower(): k for k in order}
                    for k in iorder:
                        v, lk = iattrs[k], k.lower()
                        ap = append_map.get(lk, False)
                        if lk in existing:
                            ek = existing[lk]
                            if ap:
                                ev = attrs[ek]
                                if lk == 'class': attrs[ek] = (ev + ' ' + v).strip() if ev else v
                                elif lk == 'style':
                                    # 声明级去重: 已存在的同名同值声明(如height:100vh)不重复追加 仅补新声明
                                    if ev:
                                        have = {d.strip() for d in ev.split(';') if d.strip()}
                                        add = [d.strip() for d in v.split(';') if d.strip() and d.strip() not in have]
                                        if add: attrs[ek] = (ev.rstrip().rstrip(';') + '; ' + '; '.join(add)).strip()
                                    else: attrs[ek] = v
                                else: attrs[ek] = v
                            else: attrs[ek] = v
                        else:
                            attrs[k] = v; order.append(k); existing[lk] = k
                    new_open = _build_open_tag(new_name, attrs, order, sc)
                    close_change = c_ts is not None and itag and itag.lower() != oname.lower()
                    if new_open == content[o_ts:o_te] and not close_change and not clear_attrs:
                        sl.config(text="无变化"); return  # 同名同属性且不清属性: 无操作 不落modified标记
                    sp = [(o_ts, o_te, new_open)]
                    if close_change: sp.append((c_ts, c_te, f'</{new_name}>'))
                    _apply_splices(sp, content, resel_off=o_ts)  # 前段splice不动o_ts前偏移: 操作后重选新开标签
                    sl.config(text=f"已应用: {item}")

                def _op_insert_literal(literal):
                    """插入字面标签(如 <hr/> <br/>): 有选中标签时插在其闭标签后(无闭标签则开标签后) 否则插在光标处; 插入后恢复原选中"""
                    content = txt.get("1.0", "end-1c")
                    s = state.get("tagsel")
                    res = None
                    if s:
                        ts, te, cs, ce, name = s
                        if content[ts:te].startswith('</'):
                            after = te; res = cs  # 点中闭标签: 插在其后 重选配对开标签
                        elif cs is not None:
                            after = ce; res = ts  # 有闭标签: 插在闭标签后 重选开标签
                        else:
                            after = te; res = ts  # 自闭/void无闭标签: 插在开标签后
                    else:
                        try: after = _tk_to_off(txt.index("insert"), content)
                        except tk.TclError: after = 0
                    _apply_splices([(after, after, literal)], content, resel_off=res)
                    sl.config(text=f"已插入: {literal}")

                def _op_delete_tag_only():
                    s = state.get("tagsel")
                    if not s or s[2] is None: return  # 无配对(自闭合/void) 不适用
                    ts, te, cs, ce, name = s
                    content = txt.get("1.0", "end-1c")
                    _apply_splices([(ts, te, ""), (cs, ce, "")], content)
                    sl.config(text="已删标签")

                def _op_delete_block():
                    s = state.get("tagsel")
                    if not s: return
                    ts, te, cs, ce, name = s
                    content = txt.get("1.0", "end-1c")
                    # 点中闭标签时tagsel=(闭区间,开区间): 取min/max规范化 防反向区间(start>end)删除无效
                    st, en = (min(ts, cs), max(te, ce)) if cs is not None else (ts, te)
                    _apply_splices([(st, en, "")], content)
                    sl.config(text="已删整块")

                def _copy_tag():
                    """复制选中的标签本身(仅主要开标签 不含正文/闭合); 点中闭标签则复制其配对开标签"""
                    s = state.get("tagsel")
                    if not s: return
                    ts, te, cs, ce, name = s
                    content = txt.get("1.0", "end-1c")
                    seg = (content[cs:ce] if cs is not None else content[ts:te]) if content[ts:te].startswith('</') else content[ts:te]
                    txt.clipboard_clear(); txt.clipboard_append(seg)
                    sl.config(text=f"已复制标签 {len(seg)} 字符")

                def _op_change_custom():
                    from tkinter import simpledialog
                    item = simpledialog.askstring("改标签/属性",
                        '语法:\n  h3  仅换种类\n  h3-  换种并清空属性(正文不动)\n  div class="a b"  换种+替换class\n  div +class="a b"  换种+追加class\n  class="a b"  仅替换class\n  +cls / +color:red  追加(自动识别class/style)',
                        parent=win)
                    if not item: return
                    itag, iattrs, _o, _ap, _cc = _parse_tag_item(item)
                    if not itag and not iattrs:
                        messagebox.showwarning("无效", "无法解析(需标签名 或 属性=值 或 +追加)", parent=win); return
                    _apply_tag_item(item)

                # 多列弹窗(双击/右键共用): 条目按 [列头] 分列(支持 [1][2][3] 数值定列序); --- 列内分隔横线; <hr/> 字面插入; 其余修改选中标签
                def _popup_tag_menu(sel, e):
                    # 解析条目按 [列头] 分列: [换种]h3 -> 列"换种" 条目"h3"; [1]h3 -> 数值列; 无前缀入默认列
                    cols, col_order = {}, []
                    for it in self.tag_menu_terms:
                        s = it.strip()
                        if not s: continue
                        col, body = "", s
                        m = re.match(r'\[([^\]]*)\]\s*(.*)$', s)
                        if m: col, body = m.group(1).strip(), m.group(2).strip()
                        if col not in cols: cols[col] = []; col_order.append(col)
                        if body: cols[col].append(body)
                    if not col_order: col_order = [""]  # 无条目兜底空列
                    # 列序: 无[]前缀默认列最左; [1][2][3]数值列按数字; 其余命名列按出现顺序
                    def _col_key(item):
                        i, c = item
                        if c == "": return (0, 0, i)
                        if c.isdigit(): return (1, int(c), i)
                        return (2, 0, i)
                    col_order = [c for _, c in sorted(enumerate(col_order), key=_col_key)]

                    # 固定态弹窗存活则复用: 不重建不重置固定(双击/右键仅切换选中 灰显已由refresh_popup刷新)
                    old = state.get("tag_popup")
                    if old is not None and state.get("tag_pinned"):
                        try:
                            if old.winfo_exists(): return
                        except tk.TclError: pass
                    # 关闭旧弹窗(防叠加)
                    if old is not None:
                        try: old.destroy()
                        except tk.TclError: pass

                    popup = tk.Toplevel(win)
                    popup.overrideredirect(True)  # 无边框 菜单外观
                    POPUP_BG = "#F0F0F0"  # 弹窗底色(改这一处即可: overrideredirect窗自身bg不重绘 底色实际由host及子Frame承载)
                    popup.configure(bg=POPUP_BG)
                    state["tag_popup"] = popup
                    state["tag_pinned"] = False  # 新弹窗重置固定态
                    has_pair = bool(sel) and sel[2] is not None

                    def _close(_e=None, force=False):
                        if state.get("tag_pinned") and not force: return  # 固定态: 点外部/常规操作不关闭
                        try: popup.destroy()
                        except tk.TclError: pass
                        if state.get("tag_popup") is popup: state["tag_popup"] = None

                    SEP_BG = "#909090"  # 统一分隔线颜色(外边框/列间竖线/排间横线 同色同粗=1px, 取旧版 ttk 细线与上版 2px 之中)
                    # py3.8/Tk8.6: overrideredirect 窗口自身 bg 不重绘, 用一个 fill 子 Frame 承载底色(同 tooltip.py 写法); highlightthickness 作 1px 外边框
                    host = tk.Frame(popup, bg=POPUP_BG,
                        highlightthickness=1, highlightbackground=SEP_BG, highlightcolor=SEP_BG)
                    host.pack(fill="both", expand=True)

                    # 按钮工厂: 无边框 左对齐 悬停高亮如右键菜单(始终绑command: 固定态弹窗随选中状态动态恢复可用)
                    def _mk_btn(parent, text, cmd=None, disabled=False):
                        b = tk.Button(parent, text=text, relief="flat", bd=0,
                            highlightthickness=0, anchor="w",
                            padx=6, pady=1, bg=POPUP_BG,
                            activebackground="#0078D4", activeforeground="white",
                            disabledforeground="#999999")
                        if cmd: b.config(command=cmd)
                        if disabled: b.config(state="disabled")
                        orig_fg = b.cget("fg")
                        def _ent(_e):
                            try:
                                if str(b.cget("state")) != "disabled":
                                    b.config(bg="#0078D4", fg="white")
                            except tk.TclError: pass
                        def _lev(_e):
                            try: b.config(bg=POPUP_BG, fg=orig_fg)
                            except tk.TclError: pass
                        b.bind("<Enter>", _ent)
                        b.bind("<Leave>", _lev)
                        return b

                    # 依赖选中态的按钮(二元组: 按钮, 需求'sel'=有选中/'pair'=选中且带配对): 固定态随 tagsel 动态灰显/恢复
                    mutable = []
                    def _refresh_pinned():
                        tp = state.get("tag_popup")
                        if tp is None or not state.get("tag_pinned"): return
                        try:
                            if not tp.winfo_exists(): return
                        except tk.TclError: return
                        s = state.get("tagsel")
                        for b, needs in mutable:
                            ok = bool(s) if needs == 'sel' else (bool(s) and s[2] is not None)
                            try: b.config(state="normal" if ok else "disabled")
                            except tk.TclError: pass
                    state["refresh_popup"] = _refresh_pinned

                    # 各列(纵向堆叠按钮) 列间竖线分隔(rowspan 贯穿至底部横线 使之粘合); 不显示列头
                    col_idx = 0
                    for ci, col in enumerate(col_order):
                        if ci > 0:  # 列间竖线(贯穿列区与底部分隔线)
                            tk.Frame(host, width=1, bg=SEP_BG).grid(
                                row=0, column=col_idx, rowspan=2, sticky="ns")
                            col_idx += 1
                        cf = tk.Frame(host, bg=POPUP_BG, pady=3)  # padx=0: 条目/横线贴列分隔线 文字间隔由按钮内边距控制
                        cf.grid(row=0, column=col_idx, sticky="ns")
                        col_idx += 1
                        for body in cols.get(col, []):
                            if re.fullmatch(r'-{3,}', body):  # 列内分隔横线(统一1px)
                                tk.Frame(cf, height=1, bg=SEP_BG).pack(fill="x", pady=3); continue  # padx=0 两端贴列分隔线
                            if body.startswith('<') and body.endswith('>'):  # 字面插入(不依赖选中)
                                _mk_btn(cf, body, cmd=lambda x=body: (_close(), _op_insert_literal(x))).pack(fill="x"); continue
                            b = _mk_btn(cf, body, cmd=lambda x=body: (_close(), _apply_tag_item(x)))
                            if not sel: b.config(state="disabled")  # 无选中标签 修改项禁用
                            b.pack(fill="x"); mutable.append((b, 'sel'))
                    # 底部分隔横线(跨列, 与竖线粘合)
                    tk.Frame(host, height=1, bg=SEP_BG).grid(
                        row=1, column=0, columnspan=max(col_idx, 1), sticky="ew")
                    # 底部公共操作(跨列): 最初的 ttk.Button 样式(原生边框/悬停)
                    bf = tk.Frame(host, bg=POPUP_BG, padx=6, pady=3)
                    bf.grid(row=2, column=0, columnspan=max(col_idx, 1), sticky="ew")
                    rb = tk.Frame(bf, bg=POPUP_BG); rb.pack(fill="x")
                    # 右下角固定按钮: 固定后点外部/切换代码块/点按钮均不关闭弹窗(操作后保留) Escape强制关
                    def _toggle_pin():
                        on = state["tag_pinned"] = not state["tag_pinned"]
                        pin.config(text="已固定" if on else "固定",
                                   bg="#0078D4" if on else POPUP_BG,
                                   fg="white" if on else "#555555")
                    pin = tk.Button(rb, text="固定", relief="flat", bd=0, highlightthickness=0,
                        padx=6, pady=1, bg=POPUP_BG, fg="#555555", activebackground="#0078D4",
                        activeforeground="white", command=_toggle_pin)
                    pin.pack(side="right")
                    ttk.Button(rb, text="自定义…", command=lambda: (_close(), _op_change_custom())).pack(side="left")
                    # 操作按钮全集(右键/双击/固定态共用同一集合): 无选中/无配对灰显而非隐藏换文案
                    mutable.append((ttk.Button(rb, text="删标签", command=lambda: (_close(), _op_delete_tag_only())), 'pair'))
                    mutable[-1][0].pack(side="left", padx=3)
                    mutable.append((ttk.Button(rb, text="删整块", command=lambda: (_close(), _op_delete_block())), 'sel'))
                    mutable[-1][0].pack(side="left")
                    mutable.append((ttk.Button(rb, text="取消选中", command=lambda: (_close(), _clear_tagsel())), 'sel'))
                    mutable[-1][0].pack(side="right")
                    for b, needs in mutable:  # 初始灰显(与_refresh_pinned同规则 基于本次sel)
                        ok = bool(sel) if needs == 'sel' else has_pair
                        if not ok: b.config(state="disabled")

                    # 浮动拖动: 在空白/分隔区按住拖动移动弹窗(按钮点击不受影响)
                    drag = {"dx": 0, "dy": 0}
                    def _drag_start(ev):
                        drag["dx"] = ev.x_root - popup.winfo_x()
                        drag["dy"] = ev.y_root - popup.winfo_y()
                    def _drag_move(ev):
                        popup.geometry(f"+{max(0, ev.x_root - drag['dx'])}+{max(0, ev.y_root - drag['dy'])}")
                    def _collect_frames(w):
                        out = []
                        for c in w.winfo_children():
                            if isinstance(c, tk.Frame):
                                out.append(c); out.extend(_collect_frames(c))
                        return out
                    for wdg in [host] + _collect_frames(host):
                        wdg.bind("<Button-1>", _drag_start, add="+")
                        wdg.bind("<B1-Motion>", _drag_move, add="+")

                    # 定位(贴右键点 并夹在屏幕内)
                    popup.update_idletasks()
                    w, h = popup.winfo_reqwidth(), popup.winfo_reqheight()
                    sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()
                    x = max(0, min(e.x_root, sw - w)); y = max(0, min(e.y_root, sh - h))
                    popup.geometry(f"+{x}+{y}")
                    popup.focus_set()
                    popup.lift()

                    # 点外部/Escape关闭: 仅当指针不在弹窗内时才关(点按钮不触发); 固定态点外部不关
                    def _on_focusout(ev):
                        if state.get("tag_pinned"): return
                        # py3.8: FocusOut 的 x_root/y_root 可能为 "??" 致 winfo_containing 抛 bad screen distance; 改用实时指针位置
                        try: wdg = popup.winfo_containing(ev.x_root, ev.y_root)
                        except tk.TclError:
                            try: wdg = popup.winfo_containing(popup.winfo_pointerx(), popup.winfo_pointery())
                            except tk.TclError: return  # 仍取不到则忽略 避免崩溃
                        while wdg is not None:
                            if wdg is popup: return
                            try: wdg = wdg.master
                            except Exception: return
                        _close()
                    popup.bind("<FocusOut>", _on_focusout, add="+")
                    popup.bind("<Escape>", lambda e=None: _close(force=True))

                # 右键: 选中光标处标签并弹操作菜单
                def _on_text_menu(e):
                    content = txt.get("1.0", "end-1c")
                    off = _tk_to_off(txt.index("@%d,%d" % (e.x, e.y)), content)
                    txt.mark_set("insert", "@%d,%d" % (e.x, e.y))  # 同步光标到点击处
                    _popup_tag_menu(_select_tag(off), e)

                # 双击: 命中标签→弹快速菜单(换种/删标签/删整块) 命中空白→切换编辑态(仅方向键移光标) 拦截系统双击选词
                def _on_text_dbl(e):
                    content = txt.get("1.0", "end-1c")
                    off = _tk_to_off(txt.index("@%d,%d" % (e.x, e.y)), content)
                    sel = _select_tag(off)
                    if sel:
                        _popup_tag_menu(sel, e)
                    else:
                        _set_edit_mode(not state.get("edit_mode"))
                        if state.get("edit_mode"):
                            try: txt.mark_set("insert", "@%d,%d" % (e.x, e.y))
                            except tk.TclError: pass
                    return "break"

                # 左键: 命中标签则高亮开闭(标出闭合 不滚动) 否则清选中; 不影响光标定位
                def _on_text_click(e):
                    content = txt.get("1.0", "end-1c")
                    _select_tag(_tk_to_off(txt.index("@%d,%d" % (e.x, e.y)), content))
                # 键入: 清除标签选中(防高亮随编辑漂移) 并按改动刷新●标记; 修饰键不清(否则按Ctrl+C时前置Ctrl已丢选中)
                _MOD_KEYS = {'Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R', 'Win_L', 'Win_R', 'Caps_Lock', 'Num_Lock'}
                def _on_text_key(e):
                    if e.keysym not in _MOD_KEYS: _clear_tagsel()
                    _sync_dirty_mark()
                txt.bind("<Button-1>", _on_text_click, add="+")
                txt.bind("<Double-Button-1>", _on_text_dbl)
                txt.bind("<KeyPress>", _on_text_key, add="+")
                txt.bind("<Button-3>", _on_text_menu)
                # 复制: 手动选区优先走系统; 无选区但有标签选中→Ctrl+C复制标签本身(不含正文/闭合)
                def _on_copy(e):
                    if txt.tag_ranges("sel") or not state.get("tagsel"): return
                    _copy_tag(); return "break"
                txt.bind("<Control-c>", _on_copy)
                txt.bind("<Control-C>", _on_copy)
                # 固定态弹窗: 点击正文时保持置顶(防被主窗口盖住)
                def _lift_popup(_e):
                    tp = state.get("tag_popup")
                    if tp is not None and state.get("tag_pinned"):
                        try: tp.lift()
                        except tk.TclError: pass
                txt.bind("<Button-1>", _lift_popup, add="+")
                win.bind("<Button-1>", _lift_popup, add="+")

                # 撤销/重做 (Tk Text undo栈; 载入时edit_reset清栈 故仅本文件内可撤销; 标签操作已包入undo分隔符为一组; 只读态下临时解锁)
                def _ra_cross(is_undo):
                    """全部替换跨文件撤销/重做: txt越过该批次边界(内容与快照吻合)时 同步还原/重应用其余文件"""
                    pair = state.get("ra_pair")
                    if not pair or state.get("loading") or is_undo == state.get("ra_undone"): return
                    snap = (pair[0] if is_undo else pair[1])
                    if txt.get("1.0", "end-1c") != snap.get(state.get("current_file")): return  # 未越过批次边界(可能仅撤销了手工编辑)
                    _commit_current()  # 当前文件已还原/重应用 同步缓存与modified_files
                    for f, v in snap.items():
                        if f == state["current_file"]: continue
                        self.modified_files[f] = v.encode("utf-8")
                        state["html_cache"].pop(f, None)  # 失效缓存 下次经modified_files重读
                        if f in state["cache_order"]: state["cache_order"].remove(f)
                        state["by_file"].pop(f, None); state["scanned_files"].discard(f)
                        state["color_cache"].pop(f, None)
                    state["ra_undone"] = is_undo
                    do_find(reset=True, force=True, navigate=False)
                def _undo():
                    unlocked = _ensure_editable()
                    try: txt.edit_undo()
                    except tk.TclError: pass
                    if unlocked: txt.config(state="disabled")
                    _ra_cross(True)
                    _sync_dirty_mark()
                def _redo():
                    unlocked = _ensure_editable()
                    try: txt.edit_redo()
                    except tk.TclError: pass
                    if unlocked: txt.config(state="disabled")
                    _ra_cross(False)
                    _sync_dirty_mark()
                win.bind("<Control-z>", lambda e: (_undo(), "break")[1])
                win.bind("<Control-y>", lambda e: (_redo(), "break")[1])
                win.bind("<Control-Z>", lambda e: (_redo(), "break")[1])  # Ctrl+Shift+Z 重做

                # 关窗前提交未保存编辑
                def _on_win_close():
                    _commit_current(); win.destroy()
                win.protocol("WM_DELETE_WINDOW", _on_win_close)

                # --- 异步多线程颜色高亮：调用 workers_cfg 设置线程数并分块并行处理长文 ---
                import queue as _queue
                _color_q = _queue.Queue()
                _prefetched = set()  # 已预取相邻文件的防重复标记 (词条变更时随之清空)
                def _merge_color_ranges(matches):
                    """相邻/重叠的同tag区间合并 不同tag(色号或角色不同)绝不合并 降区间数"""
                    matches.sort(key=lambda x: (x[0], x[2]))
                    merged = []
                    for s, e, c in matches:
                        if merged and merged[-1][2] == c and s <= merged[-1][1]:
                            if e > merged[-1][1]: merged[-1] = (merged[-1][0], e, c)
                        else: merged.append((s, e, c))
                    return merged
                def _compute_color_ranges(content, compiled):
                    """后台计算染色区间: 短文本单线程 长文本线程池分块并行检索(带重叠区防跨行截断) 返回合并后区间"""
                    mw = getattr(self, 'workers_cfg', None)
                    if callable(mw): mw = mw()
                    max_workers = int(mw) if mw else 4
                    length = len(content)
                    pt_tags = [(ct.pattern, self._color_tag_name(ct.color, ct.role or 'fg')) for ct in compiled]
                    if length < 3000 or max_workers <= 1:
                        matches = []
                        for pat, tag in pt_tags:
                            matches.extend((m.start(), m.end(), tag) for m in pat.finditer(content))
                    else:
                        chunk_len = length // max_workers
                        overlap = 2000 # 重叠区长度，防止跨行截断
                        ranges = [(max(0, i * chunk_len - (overlap if i > 0 else 0)), 
                                  min(length, (i + 1) * chunk_len + (overlap if i < max_workers - 1 else 0))) 
                                  for i in range(max_workers)]
                        def search_chunk(sub_range, pat, tag):
                            sub_start, sub_end = sub_range
                            sub_str = content[sub_start:sub_end]
                            return [(sub_start + m.start(), sub_start + m.end(), tag) for m in pat.finditer(sub_str)]
                        matches = []
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = [executor.submit(search_chunk, r, pat, tag) for pat, tag in pt_tags for r in ranges]
                            for f in futures:
                                try: matches.extend(f.result())
                                except Exception as ex: logger.error(f"颜色词条匹配失败: {ex}")
                    return _merge_color_ranges(matches)
                def _paint_color_ranges(merged, fpath, content):
                    """主线程分批渲染 (文件/词条变化则丢弃过期批)"""
                    if not merged or state.get("current_file") != fpath: return
                    terms_key = state.get("color_terms_key")
                    painted_key = (fpath, terms_key)
                    def apply_batch(idx=0, step=250):
                        if state.get("current_file") != fpath or state.get("color_terms_key") != terms_key or idx >= len(merged): return
                        batch = merged[idx:idx+step]
                        by_tag = {}
                        for s, e, tag in batch:
                            by_tag.setdefault(tag, []).extend((to_tk_idx(s, content), to_tk_idx(e, content)))
                        for tag, idxs in by_tag.items():
                            txt.tag_add(tag, *idxs)
                        if idx + step < len(merged): win.after(15, apply_batch, idx + step)
                        else: state["color_painted"] = painted_key
                    win.after(0, apply_batch)
                def _poll_color_render():
                    """主线程轮询后台渲染结果队列执行绘制 (Tk非线程安全 后台线程直调win.after会在主线程忙碌时偶发丢失→切文件染色失效)"""
                    try:
                        try:
                            while True:
                                fpath, merged, snap_content = _color_q.get_nowait()
                                _paint_color_ranges(merged, fpath, snap_content)
                        except _queue.Empty: pass
                        win.after(25, _poll_color_render)
                    except tk.TclError: pass  # 窗口已销毁 停止轮询
                win.after(25, _poll_color_render)
                def _color_task(fpath, content, compiled, paint_it):
                    """后台计算染色区间: 当前文件经队列交主线程绘制 相邻预取仅预热缓存(切换时命中缓存近零延迟)"""
                    try:
                        merged = _compute_color_ranges(content, compiled)
                        state["color_cache"][fpath] = (content, merged)
                        while len(state["color_cache"]) > 24:  # FIFO上限 防长会话内存膨胀(被挤出的文件下次重算)
                            state["color_cache"].pop(next(iter(state["color_cache"])))
                        if paint_it: _color_q.put((fpath, merged, content))
                    except Exception as ex:
                        logger.error(f"异步多线程渲染颜色失败: {ex}")
                def _prefetch_neighbors(fpath, compiled):
                    """后台预取spine相邻文件的染色结果到缓存 使首次切换到新文件即命中缓存 (file_pos由do_find构建 为空时跳过)"""
                    if not state["file_pos"]: return
                    fs = sorted(state["file_pos"], key=lambda f: state["file_pos"][f])
                    if fpath not in fs: return
                    i = fs.index(fpath)
                    for nf in ([fs[i - 1]] if i > 0 else []) + ([fs[i + 1]] if i + 1 < len(fs) else []):
                        if nf in state["color_cache"] or nf in _prefetched: continue
                        _prefetched.add(nf)
                        try:
                            with zipfile.ZipFile(self.epub_path, 'r') as z: nc = get_cached_content(nf, z)
                        except Exception: continue
                        from threading import Thread
                        Thread(target=_color_task, args=(nf, nc, compiled, False), daemon=True).start()

                def async_render_colors(content, fpath):
                    terms_key = tuple(self.text_search_terms)
                    if state["color_terms_key"] != terms_key:
                        state["color_terms_key"] = terms_key
                        state["color_compiled"] = [ct for t in self.text_search_terms
                                                   if t.strip().startswith('[') and (ct := self._parse_color_term(t.strip())) is not None]
                        state["color_cache"].clear()
                        state["color_painted"] = None
                        _prefetched.clear()
                        # 主线程按色号与角色去重注册tag (仅词条变更时重建)
                        tags = {}
                        for ct in state["color_compiled"]:
                            role = ct.role or 'fg'  # 预览默认前景色
                            tag = self._color_tag_name(ct.color, role)
                            tags[tag] = (ct.color, role)
                        state["color_tags"] = tags
                        for tag, (color, role) in tags.items():
                            txt.tag_config(tag, **({'foreground': color} if role == 'fg' else {'background': color}))
                        # 标色tag后创建自然在点选蓝之上(叠加显示) 无需调整; sel保持最上
                        txt.tag_raise("sel")
                    compiled = state["color_compiled"]
                    if not compiled: return

                    painted_key = (fpath, terms_key)
                    if state["color_painted"] == painted_key: return

                    if fpath in state["color_cache"]:
                        snap_content, merged = state["color_cache"][fpath]
                        _paint_color_ranges(merged, fpath, snap_content)
                    else:
                        from threading import Thread
                        Thread(target=_color_task, args=(fpath, content, compiled, True), daemon=True).start()
                    _prefetch_neighbors(fpath, compiled)  # 预取相邻文件 切换到新文件时命中缓存近零延迟

                # 显示/搜索共用的换行预处理:两边对同一内容做同一变换,保证字符偏移一致
                def prettify(c):
                    c = c.replace('\r\n', '\n').replace('\r', '\n')  # 归一化换行,防止Tk折叠\r\n导致偏移漂移
                    #【换char所以目前不需要 注释掉】为极长的标签块换行:标签各自独立成行,word wrap不再从标签内部的空格处断行
                    #c = re.sub(r'(</(?:div|p|h[1-6]|ul|ol|li|section|html|body|table)>)\s*', r'\1\n', c, flags=re.I)
                    # 保护措施：强制切分超长单行，防止 Tkinter 在 wrap="word" 下因单行字符过多而直接卡死
                    #c = re.sub(r'([^\n]{1000}[^\s]*)\s+', r'\1\n', c)
                    #c = re.sub(r'([^\n]{3000})', r'\1\n', c)
                    return c

                # 内存读取与缓存函数 (LRU双上限: 最多50个文件 或 总字符1000万≈20-40MB 先到先逐出 防个别超大文件撑爆内存)
                _CACHE_MAX_FILES, _CACHE_MAX_CHARS = 50, 10_000_000
                def get_cached_content(fpath, z):
                    if fpath in state["html_cache"]:
                        state["cache_order"].remove(fpath)
                        state["cache_order"].append(fpath)
                        return state["html_cache"][fpath]

                    raw = (self.modified_files[fpath] if fpath in self.modified_files and self.modified_files[fpath] is not None
                           else z.read(fpath) if fpath in z.namelist() else b"")
                    content = prettify(raw.decode('utf-8', 'ignore'))

                    state["html_cache"][fpath] = content
                    state["cache_order"].append(fpath)
                    while state["cache_order"]:
                        if len(state["cache_order"]) <= _CACHE_MAX_FILES and sum(len(state["html_cache"][f]) for f in state["cache_order"]) <= _CACHE_MAX_CHARS: break
                        state["html_cache"].pop(state["cache_order"].pop(0), None)
                    return content

                # 加载文件内容到文本预览框并同步文件树选中状态
                def load_content_to_text(fpath):
                    if state.get("current_file") == fpath and txt.index("end-1c") != "1.0": return # 防止左右切换或搜索时重复渲染相同的已读文件
                    _commit_current()  # 切换前把上一文件的编辑提交到 modified_files
                    _clear_tagsel()
                    state["current_file"] = fpath; win.title(fpath)
                    
                    with zipfile.ZipFile(self.epub_path, 'r') as z: content = get_cached_content(fpath, z)

                    state["loaded_content"] = content  # 载入态基准 供撤销到最前时清●比对
                    state["loading"] = True  # 载入期间禁止提交/重绘
                    txt.config(state="normal"); txt.delete("1.0", "end"); txt.insert("1.0", content)  # 正文保持可编辑
                    txt.edit_reset(); txt.edit_modified(False)  # 清undo栈: 切换文件不应跨文件撤销
                    state.pop("ra_pair", None)  # 撤销栈已清 全部替换跨文件快照随之作废
                    state["color_painted"] = None  # delete+insert已清空全部tag(含染色) 重置染色去重标记 否则重进已染文件被去重拦截致切换不染色
                    state["loading"] = False
                    # 浏览态(默认): 方向键走原逻辑(切文件/导航结果) F2进入编辑态; 正文始终normal可编辑/可复制 不变色
                    state["edit_mode"] = False
                    txt.config(state="normal")
                    _sync_dirty_mark()
                    
                    # 触发异步多线程颜色高亮 
                    async_render_colors(content, fpath)
                    # 传入当前文本上下文供坐标极速转换
                    paint_m_async(content)
                    for n in self.n_map.values():
                        if n and ftree.exists(n) and ftree.item(n, "tags")[0] == fpath: ftree.selection_set(n); ftree.see(n)

                # 高亮异步分批渲染: 接收传入的 content 文本以供行列计算
                def paint_m_async(content):
                    key = (state.get("last_q"), state["current_file"])
                    spans = state.get("by_file", {}).get(state["current_file"], ())
                    # 同文件同查询且已有匹配:已刷/正在刷,不重复
                    if spans and state.get("painted") == key and txt.tag_ranges("m"): return
                    if not spans:
                        # 无匹配仅清黄标不记录painted: 若提前记录, 后续扫描出的匹配会被去重守卫拦截致黄标画不上
                        txt.tag_remove("m", "1.0", "end")
                        return
                    state["painted"] = key
                    txt.tag_remove("m", "1.0", "end")
                    state["paint_token"] = token = state.get("paint_token", 0) + 1
                    def batch(i=0, step=400):  # 400个匹配项一批 分批渲染
                        if state.get("paint_token") != token: return  # 文件已切换,丢弃过期批次
                        # 调用 to_tk_idx()，完全隔离 Tkinter 死循环计算
                        idx = [v for s, e in spans[i:i+step] for v in (to_tk_idx(s, content), to_tk_idx(e, content))]
                        if idx: txt.tag_add("m", *idx)
                        if i + step < len(spans): win.after(15, batch, i + step)
                    if spans: win.after(0, batch)

                # 执行正则搜索定位，支持全局匹配与高亮
                def do_find(rev=False, reset=False, navigate=True, force=False):
                    raw = se.get()
                    ct = ClassList._parse_color_term(raw) if raw.strip().startswith('[') else None
                    q = ct.raw if ct else raw  # 去掉[色号]前缀仅按正则搜索
                    if not q:
                        # 清空查询: 取消挂起记录 清除高亮
                        hid = state.pop('hist_id', None)
                        if hid is not None:
                            try: win.after_cancel(hid)
                            except Exception: pass
                        txt.tag_remove("m", "1.0", "end"); txt.tag_remove("cur", "1.0", "end")
                        state.pop("keep_pos", None)  # 彻底清空查询:位置记忆一并作废
                        state.update({"last_q": None, "results": [], "by_file": {}, "search_index": -1, "painted": None, "scanned_files": set()})
                        return sl.config(text="0/0")

                    # 仅查询变化时取消旧挂起并重置缓存(导航同查询不碰计时器 避免连续导航永不记录)
                    is_new = force or q != state["last_q"]
                    keep = None  # 必须先初始化: 非新查询的导航调用也会走后续同步分支
                    if is_new:
                        # 记忆当前匹配(文件+起点)到state: 中间态会重置index致记忆丢失, 跨调用存keep_pos续接
                        if 0 <= state["search_index"] < len(state["results"]):
                            state["keep_pos"] = state["results"][state["search_index"]]
                        keep = state.get("keep_pos")  # 局部变量续接上次有效位置(可能来自多轮删除前)
                        hid = state.pop('hist_id', None)
                        if hid is not None:
                            try: win.after_cancel(hid)
                            except Exception: pass
                        state.update({"last_q": q, "results": [], "by_file": {}, "search_index": -1, "painted": None, "scanned_files": set()})
                    # 仅新查询/强制/当前文件未缓存时才扫描; 纯导航(同查询同文件)复用既有结果 跳过zip打开与finditer
                    need_scan = is_new or state["current_file"] not in state["by_file"]
                    if need_scan:
                        try:
                            with zipfile.ZipFile(self.epub_path, "r") as z:
                                nl = z.namelist()
                                # 文件顺序改为跟随左侧文件树显示序(spine列表)
                                if not state["file_pos"]:
                                    _order = []
                                    def _walk(item):
                                        for c in ftree.get_children(item):
                                            p = ftree.item(c, "tags")[0]
                                            if not p.endswith('/'):
                                                _order.append(p)
                                            _walk(c)  # 目录节点递归下钻
                                    _walk("")
                                    seen = set(_order)
                                    # 兜底: 运行期新增、建树后未出现的html文件 按名称排序追加到末尾
                                    _order += sorted(f for f in set(nl) | set(self.modified_files.keys())
                                                        if f.endswith((".html", ".xhtml")) and f not in seen)
                                    state["file_pos"] = {f: i for i, f in enumerate(_order) if f.endswith((".html", ".xhtml"))}
                                # 非全局: 针对当前文件扫描(同查询已扫过则复用by_file 跳过finditer)
                                if not global_search_var.get():
                                    cf = state["current_file"]
                                    if cf and cf.endswith((".html", ".xhtml")) and (cf in nl or self.modified_files.get(cf) is not None):
                                        if cf not in state["by_file"]:
                                            state["by_file"][cf] = [m.span() for m in re.finditer(q, get_cached_content(cf, z))]
                                            state["scanned_files"].add(cf)
                                        state["results"] = [(cf, s) for s in state["by_file"][cf]]
                                    else:
                                        state["results"] = []
                                else:
                                    # 全局: 仅增量扫描未扫描过的文件
                                    cand = list(nl) + list(self.modified_files.keys())
                                    s_files = sorted({f for f in cand if f.endswith((".html", ".xhtml")) and (f in nl or self.modified_files.get(f) is not None)})
                                    new_matches = False
                                    for f in s_files:
                                        if f not in state["scanned_files"]:
                                            spans = [m.span() for m in re.finditer(q, get_cached_content(f, z))]
                                            state["scanned_files"].add(f)
                                            # 重扫文件(编辑/替换后偏移已变)先剔除旧结果条目 防重复累积致计数虚高+残留高亮
                                            state["results"] = [r for r in state["results"] if r[0] != f]
                                            if spans:
                                                state["by_file"][f] = spans
                                                state["results"].extend((f, s) for s in spans)
                                                new_matches = True
                                    if new_matches:
                                        state["results"].sort(key=lambda x: (state["file_pos"].get(x[0], 9999), x[1]))
                                # 全局逐文件扫描挤占LRU缓存(50个/1000万字符) 当前文件可能被逐出致content取空、高亮落1.0, 扫描后回读当前文件
                                if state["current_file"] and state["current_file"] not in state["html_cache"]:
                                    get_cached_content(state["current_file"], z)
                        except Exception as e:
                            logger.error(f"正则搜索失败: {e}")
                            # 中间态正则(如[^未闭合)报错直接返回, 清除过期高亮并重置绘制状态
                            txt.tag_remove("m", "1.0", "end"); txt.tag_remove("cur", "1.0", "end")
                            state.update({"results": [], "by_file": {}, "search_index": -1, "painted": None, "scanned_files": set()})
                            return sl.config(text="Err")
                    
                    res = state["results"]
                    content = state["html_cache"].get(state["current_file"], "")

                    # 仅新查询时挂起记录(导航不重置计时 1秒后照常记录)
                    if is_new:
                        state['hist_id'] = win.after(1000, lambda q=q: self._record_history(
                            q, self.text_search_history,
                            fixed={dt for t in self.text_search_terms if (dt := self._dropdown_term(t.strip())) is not None}))
                    # 重扫后同步记忆位置: 同文件取起点最近邻(span删改后漂移精确相等常失配); 比较取span[0]起点; 无匹配则置-1回落定位模式; 仅锚定到当前文件(否则min()会选中跨文件任意远处匹配致计数乱跳)
                    if keep is not None and keep[0] == state["current_file"]:
                        cands = [(i, r[1][0]) for i, r in enumerate(res) if r[0] == keep[0]]
                        state["search_index"] = min(cands, key=lambda t: abs(t[1] - keep[1][0]))[0] if cands else -1
                    elif keep is not None:  # 记忆命中在其它文件不锚定,交给下方定位模式取当前文件第一条
                        state["search_index"] = -1
                    # 无匹配出口: 显式清掉cur橙标与m黄标(原只靠paint_m_async清m, cur从不清理致残留)
                    if not res:
                        txt.tag_remove("cur", "1.0", "end"); txt.tag_remove("m", "1.0", "end")
                        paint_m_async(content)
                        return sl.config(text="0/0")
                        
                    # ↑↓导航:只移动索引(O(1)),不再tag_remove全文重刷、不再重新finditer
                    if navigate:
                        if state["search_index"] < 0:
                            # 当前文件自身有匹配时优先落在本文件(↓取首条 ↑取末条): 原file_pos严格比较会跳过当前文件
                            same = [i for i, (f, _) in enumerate(res) if f == state["current_file"]]
                            if same:
                                state["search_index"] = same[-1] if rev else same[0]
                            else:
                                cur_pos = state["file_pos"].get(state["current_file"], -1)
                                if cur_pos < 0:  # 当前文件不在扫描范围(如非html文件):直接取首/尾
                                    state["search_index"] = len(res) - 1 if rev else 0
                                elif rev:  # ↑: 从后往前找第一条位于当前文件之前的结果,没有则回绕到最后一条
                                    state["search_index"] = next((i for i in range(len(res) - 1, -1, -1)
                                                                  if state["file_pos"].get(res[i][0], -1) < cur_pos), len(res) - 1)
                                else:      # ↓: 从前往后找第一条位于当前文件之后的结果,没有则回绕到第一条
                                    state["search_index"] = next((i for i, (f, _) in enumerate(res)
                                                                  if state["file_pos"].get(f, -1) > cur_pos), 0)
                        else:
                            state["search_index"] = (state["search_index"] + (-1 if rev else 1)) % len(res)
                    elif not (0 <= state["search_index"] < len(res)) or res[state["search_index"]][0] != state["current_file"]:
                        # 定位模式:索引无效(未初始化/已越界)或未落在当前文件时,找当前文件的第一条结果(找不到就不跳)
                        state["search_index"] = next((i for i, (f, _) in enumerate(res) if f == state["current_file"]), -1)
                    if state["search_index"] < 0:  # 当前文件无匹配:显示总数但不强行跳走,同时清掉过期高亮
                        txt.tag_remove("cur", "1.0", "end"); txt.tag_remove("m", "1.0", "end")
                        paint_m_async(content)
                        return sl.config(text=f"-/{len(res)}")
                        
                    target_path, (gs, ge) = res[state["search_index"]]
                    # 非全局禁止跨文件跳转(结果域已限定当前文件,此分支双保险拦截残留跨文件条目)
                    if not global_search_var.get() and target_path != state["current_file"]:
                        txt.tag_remove("cur", "1.0", "end"); txt.tag_remove("m", "1.0", "end")
                        paint_m_async(content)
                        return sl.config(text=f"-/{len(res)}")
                    if target_path != state["current_file"]:
                        load_content_to_text(target_path)  # 跨文件跳转(仅↑↓全局导航时触发)
                    # 文件改变后必须重新拉取最新上下文才能保证坐标正确
                    content = state["html_cache"].get(state["current_file"], "")
                    # 高亮并跳转到当前匹配项: 仅清旧cur区间(非全文) 避免大文本tag_remove全文卡顿
                    _cr = txt.tag_ranges("cur")
                    if _cr: txt.tag_remove("cur", _cr[0], _cr[-1])
                    txt.tag_add("cur", (s_idx := to_tk_idx(gs, content)), to_tk_idx(ge, content))
                    txt.see(s_idx)
                    # 黄底分批异步渲染
                    paint_m_async(content)
                    sl.config(text=f"{state['search_index'] + 1}/{len(res)}")

                # ===== 正则替换(替换模板沿用regex_manager的re.sub语法: \1 \g<name> 等) =====
                def _replace_query():
                    """取当前搜索正则(去[色号]前缀)与替换串 编译失败返回None"""
                    raw = se.get()
                    ct = ClassList._parse_color_term(raw) if raw.strip().startswith('[') else None
                    q = ct.raw if ct else raw
                    if not q: return None
                    try: return re.compile(q), re_box.get()
                    except re.error as e:
                        logger.error(f"替换正则错误: {e}"); sl.config(text="Err"); return None

                def _record_replace_hist(repl):
                    """替换串入独立历史(去重 追加末尾 上限10) 并刷新替换下拉"""
                    if not repl: return
                    if repl in self.replace_history: self.replace_history.remove(repl)
                    self.replace_history.append(repl)
                    if len(self.replace_history) > 10: del self.replace_history[:-10]
                    self._refresh_replace_boxes()

                def do_replace_cur():
                    """单个替换: 仅替换当前目标匹配(_apply_splices单撤销单元 重扫后索引自然落位下一条)"""
                    pr = _replace_query()
                    if not pr or not (0 <= state["search_index"] < len(state["results"])): return
                    pat, repl = pr
                    target_path, (gs, ge) = state["results"][state["search_index"]]
                    if target_path != state["current_file"]:
                        load_content_to_text(target_path)  # 全局模式跨文件: 先载入目标文件再替换
                    content = state["html_cache"].get(state["current_file"], "")
                    try:
                        m = pat.match(content, gs)  # 锚定重放finditer匹配 以支持\g<name>等分组展开
                        seg = m.expand(repl) if m else repl
                    except re.error as e:
                        logger.error(f"替换表达式错误: {e}"); return sl.config(text="Err")
                    if seg == content[gs:ge]: return  # 替换前后相同不动作(不产生撤销单元/不记历史)
                    _apply_splices([(gs, ge, seg)], content)
                    _record_replace_hist(repl)

                def do_replace_all():
                    """全部替换: 作用域=当前文件(非全局)/全部有匹配文件(全局); 其他文件直写modified_files(不走txt撤销栈)"""
                    pr = _replace_query()
                    if not pr: return
                    pat, repl = pr
                    cf = state["current_file"]
                    scope = sorted(state["by_file"], key=lambda f: state["file_pos"].get(f, 9999)) if global_search_var.get() else [cf]
                    olds, news = {}, {}  # 全部替换快照: 供跨文件撤销/重做
                    try:
                        with zipfile.ZipFile(self.epub_path, "r") as z:
                            for f in scope:
                                if f not in state["by_file"]: continue  # 无匹配文件跳过
                                content = get_cached_content(f, z)
                                if f == cf:
                                    splices = [(m.start(), m.end(), m.expand(repl)) for m in pat.finditer(content)]
                                    if splices:
                                        olds[f] = content
                                        news[f] = pat.sub(repl, content)  # 与逐匹配expand等价 供重做快照
                                        _apply_splices(splices, content)  # 当前文件: 单撤销单元+重渲染重扫
                                else:
                                    new = pat.sub(repl, content)
                                    if new != content:
                                        olds[f] = content; news[f] = new
                                        self.modified_files[f] = new.encode("utf-8")
                                        state["html_cache"].pop(f, None)  # 失效缓存(下次经modified_files重读)
                                        if f in state["cache_order"]: state["cache_order"].remove(f)
                                        state["by_file"].pop(f, None); state["scanned_files"].discard(f)
                                        state["color_cache"].pop(f, None)
                    except re.error as e:
                        logger.error(f"替换表达式错误: {e}"); return sl.config(text="Err")
                    if olds: state["ra_pair"] = (olds, news); state["ra_undone"] = False
                    _record_replace_hist(repl)
                    do_find(reset=True, force=True, navigate=False)  # 强制全量重扫刷新计数/高亮(结果与扫描缓存已随替换失效)

                # 快捷键: ←→切换文件 ↑↓切换结果 输入防抖搜索; 焦点沿master链判定(下拉展开时焦点在弹层Toplevel), 仅判focus_get() is se会漏判致方向键误触
                def focus_in_search():
                    w = win.focus_get()
                    while w is not None:
                        if w is se: return True
                        try: w = w.master
                        except Exception: return False
                    return False
                def focus_in_replace():
                    w = win.focus_get()
                    while w is not None:
                        if w is re_box: return True
                        try: w = w.master
                        except Exception: return False
                    return False
                def on_switch(rev, _e):
                    if focus_in_search() or focus_in_replace(): return  # 搜索/替换框内: 放行方向键给光标移动(不影响匹配与标黄)
                    if state.get("edit_mode"): return  # 编辑态: 放行方向键给光标移动
                    nxt = get_next_text(rev)
                    ftree.selection_set(nxt); ftree.see(nxt)
                    load_content_to_text(ftree.item(nxt, "tags")[0])
                    do_find(reset=True, navigate=False)  # 只定位不高跳
                    return "break"  # 浏览态: 拦截Text class默认光标移动(防方向键误滚)
                # 弹层(combobox下拉)是否展开: 用Tk内部PopdownWindow判定
                def _popdown_mapped():
                    try:
                        return bool(int(se.tk.call('winfo', 'ismapped',
                                    se.tk.call('::ttk::combobox::PopdownWindow', str(se)))))
                    except Exception:
                        return False
                # ↑↓导航结果: 下拉展开时放行给combobox列表移动; 否则导航+break阻止combobox↓误触发popdown
                def on_nav(rev, _e=None):
                    if _popdown_mapped(): return
                    if focus_in_replace(): return  # 替换框内: 放行方向键(IME选词/光标移动) 不导航不重扫 不动匹配标黄
                    if state.get("edit_mode"): return  # 编辑态: 放行方向键给光标移动
                    do_find(rev=rev)
                    if focus_in_search(): _focus_txt()
                    return "break"
                win.bind("<Left>",  lambda e, r=1: on_switch(r, e))
                win.bind("<Right>", lambda e, r=0: on_switch(r, e))
                win.bind("<Up>",    lambda e, r=1: on_nav(r, e))
                win.bind("<Down>",  lambda e, r=0: on_nav(r, e))
                # 正文级绑定: widget标签先于Text class标签执行 故break可拦截class默认光标移动/滚动(修复无匹配时↑↓滚到底)
                txt.bind("<Left>",  lambda e, r=1: on_switch(r, e))
                txt.bind("<Right>", lambda e, r=0: on_switch(r, e))
                txt.bind("<Up>",    lambda e, r=1: on_nav(r, e))
                txt.bind("<Down>",  lambda e, r=0: on_nav(r, e))
                # se同绑: widget级break拦截combobox↓自动popdown
                se.bind("<Up>",   lambda e: on_nav(True), add="+")
                se.bind("<Down>", lambda e: on_nav(False), add="+")
                # 夺焦到正文便于↑↓导航; Unpost后异步抢回防焦点丢失
                def _grab_txt():
                    try:
                        if txt.winfo_exists(): txt.focus_set()
                    except tk.TclError: pass
                def _focus_txt(delays=(10, 60, 150)):
                    _grab_txt()
                    for d in delays: win.after(d, _grab_txt)
                def on_unpost(_e=None):
                    try:
                        if not win.winfo_exists(): return
                    except tk.TclError: return
                    mx, my = win.winfo_pointerxy()
                    # 仅指针在正文区才夺焦(点文件树等外部时让焦点跟随点击目标)
                    if sf.winfo_rooty() + sf.winfo_height() <= my < win.winfo_rooty() + win.winfo_height() \
                       and win.winfo_rootx() <= mx < win.winfo_rootx() + win.winfo_width():
                        _focus_txt()
                se.bind("<<Unpost>>", on_unpost, add="+")
                # 兜底:部分Tk版本点击外部关闭弹层不产生<<Unpost>> 轮询弹层mapped状态转移捕捉关闭时刻
                def _poll_popdown():
                    try:
                        if not win.winfo_exists(): return
                    except tk.TclError: return
                    mapped = _popdown_mapped()
                    if getattr(_poll_popdown, 'was', False) and not mapped: on_unpost(None)
                    _poll_popdown.was = mapped
                    win.after(100, _poll_popdown)
                _poll_popdown()
                # 输入防抖: 200ms停顿后刷新高亮定位
                (ft := [0]) and se.bind("<KeyRelease>", lambda e: (win.after_cancel(ft[0]) if ft[0] else None, ft.__setitem__(0, win.after(200, lambda: do_find(reset=True, navigate=False)))))
                
                # 打开窗口默认焦点强行切到 txt(文本预览区域)
                txt.focus_set()
                win.after(100, lambda: txt.focus_set())

                # 词条变更刷新回调: 供 edit_search_cfg 保存后触发(样式重标 + 正文失效重渲染)
                def on_terms_changed():
                    paint_class_color()
                    try:
                        if not win.winfo_exists(): return
                    except tk.TclError: return
                    state.update(color_terms_key=None, color_compiled=None, color_painted=None)
                    state["color_cache"].clear()
                    for tag in state.get("color_tags", {}):
                        try: txt.tag_remove(tag, "1.0", "end")
                        except tk.TclError: pass
                    state["color_tags"] = {}
                    if (cf := state.get("current_file")) and cf in state["html_cache"]:
                        async_render_colors(state["html_cache"][cf], cf)
                self._on_color_terms_changed = on_terms_changed

                load_content_to_text(p)
            except Exception as ex: 
                logger.exception(f"预览文件时发生错误: {ex}")
                messagebox.showerror("错误", str(ex), parent=cw)
        ftree.bind("<Double-1>", preview_file)

        # bs4解析并从内存/磁盘同步删除OPF引用
        def _sync_opf(deleted_paths):
                try:
                    with zipfile.ZipFile(self.epub_path, 'r') as z:
                        if not (opf_p := (BeautifulSoup(z.read("META-INF/container.xml"), "xml").find("rootfile") or {}).get("full-path")): return
                        opf_dir = os.path.dirname(opf_p)
                        # 优先从内存读取已有的修改，实现链式删除
                        soup = BeautifulSoup(self.modified_files.get(opf_p) or z.read(opf_p), "xml")
                    rel_ps = {os.path.relpath(p, opf_dir).replace("\\", "/") for p in deleted_paths}
                    # 在提取 rid 时增加有效性检查，防止匹配到 None
                    rem_ids = {rid for it in soup.find_all("item") if (rid := it.get("id")) and it.get("href") in rel_ps and [it.decompose()]}
                    [it.decompose() for it in soup.find_all("itemref") if it.get("idref") in rem_ids]
                    self.modified_files[opf_p] = soup.encode(formatter="minimal")
                    logger.info(f"内存同步：OPF已剔除 {len(rem_ids)} 个引用")
                except Exception as e: logger.error(f"OPF同步失败: {e}")

        # Bs4获取OPF Spine顺序、nav名 解析XML并构建映射
        def get_opf_spine_order(z):
            try:
                bs = BeautifulSoup(z.read("META-INF/container.xml").decode("utf-8"), "xml")
                opf_full_path = (bs.find("rootfile") or {}).get("full-path", "")
                if not opf_full_path: return {}, set()
                opf_dir = (lambda d: f"{d.replace(chr(92), '/')}/" if d else "")(os.path.dirname(opf_full_path))
                opf_soup = BeautifulSoup(z.read(opf_full_path).decode("utf-8"), "xml")
                manifest = {it.get("id"): it.get("href", "") for it in opf_soup.find_all("item") if it.get("id")}
                spine = {f"{opf_dir}{manifest[idref]}": idx
                        for idx, itemref in enumerate(opf_soup.find_all("itemref"))
                        if (idref := itemref.get("idref")) and idref in manifest}
                # 获取manifest中nav名
                navs = {f"{opf_dir}{it.get('href', '')}" for it in opf_soup.find_all("item") if "nav" in (it.get("properties") or "")}
                return spine, navs
            except: return {}, set()

        # 解析单个HTML文件的函数（子线程执行：纯计算，无UI操作）
        def _parse_html_file(file_content_bytes, filename):
            results = {'counts': {}, 'samples': {}, 'class_tags': [], 'img_counts': {}}
            try:
                import posixpath # 性能优于pathlib 且强制使用正斜杠符合epub规范
                root = lxml.html.fromstring(file_content_bytes)
                base_dir = posixpath.dirname(filename)
                # XPath 一次遍历提取class、img、svg、image的标签
                for el in root.xpath('//*[@class] | //img | //*[(local-name()="image")]'):
                    tag = el.tag.rsplit('}', 1)[-1].lower()
                    # 处理图片路径
                    if tag in ('img', 'image'):
                        # 兼容src、href、xlink:href等属性
                        src = el.get('src') or el.get('href') or el.get('xlink:href') or el.get('{http://www.w3.org/1999/xlink}href')
                        if src and not src.startswith(('http', 'data:')):
                            # 规范化路径，处理 ../ 符号并去掉 URL 参数/锚点
                            clean_src = src.split('#')[0].split('?')[0]
                            abs_p = posixpath.normpath(posixpath.join(base_dir, clean_src))
                            results['img_counts'][abs_p] = results['img_counts'].get(abs_p, 0) + 1
                    # 处理包含class的节点（这里不使用elif，因为img也可能带有class）
                    if cls_str := el.get('class'):
                        cls_list = cls_str.split()
                        s_raw = ""
                        # 性能优化：在子线程预先判断是否需要提取实例字符串
                        if any(len(self.samples_data.get(c, [])) < 15 for c in cls_list):
                            s_raw = lxml.html.tostring(el, encoding='unicode', method='html', with_tail=False).strip()
                        for c in cls_list:
                            results['counts'][c] = results['counts'].get(c, 0) + 1
                            results['class_tags'].append((c, tag))
                            if s_raw and len(results['samples'].get(c, [])) < 15: # 每个class仅收集前15个实例，避免过度内存占用
                                results['samples'].setdefault(c, []).append((filename, re.sub(r'\s+', ' ', s_raw)[:150]))
            except Exception as e:
                logger.error(f"解析 {filename} 出错: {e}")
            return results

        # 合并解析结果到主数据结构（主线程执行：包含UI更新）
        def _merge_results(results):
            # 同步更新左侧文件树的图片计数
            for p, cnt in results.get('img_counts', {}).items():
                self.img_counts[p] = self.img_counts.get(p, 0) + cnt
                if p in self.n_map and ftree.exists(self.n_map[p]):
                    ftree.item(self.n_map[p], values=(f"{self.img_counts[p]}",))

            for c, cnt in results['counts'].items():
                self.counts_data[c] = self.counts_data.get(c, 0) + cnt
                # 处理html提取的class数据 (保留原码判断逻辑与Treeview更新)
                for tag in [t for cls, t in results['class_tags'] if cls == c]:
                    [ (self.cats[k].add(c), key := (k, c),
                       tree.item(class_to_iid[key], values=(self.counts_data[c],)) if key in class_to_iid else
                       (class_to_iid.update({key: tree.insert(nodes[k], "end", text=c, values=(self.counts_data[c],))}),
                        self.all_items_refs.append((k, c, class_to_iid[key])),
                        # 字母顺序重排
                        ch := sorted(tree.get_children(nodes[k]), key=lambda x: tree.item(x, 'text').lower()),
                        [tree.move(child, nodes[k], idx) for idx, child in enumerate(ch)]))
                      for k, v in [('Class列表', 1), ('Span列表', tag=='span'), ('图片Class列表', tag=='img'), 
                                   ('非P标签列表', tag!='p'), ('非P、img、body标签列表', tag not in ['p','img','body'])] if v ]
                    break # 每个class在一次结果合并中只更新一次分类树
            for c, s_list in results['samples'].items():
                if len(self.samples_data.get(c, [])) < 15: # 仅在样本不足时合并，避免过度覆盖
                    self.samples_data.setdefault(c, []).extend(s_list[:15 - len(self.samples_data.get(c, []))])

        # 构建epub文件树 提取样式和实例数据
        def parse_gen():
            with zipfile.ZipFile(self.epub_path, 'r') as z:
                spine, navs = get_opf_spine_order(z)
                def sort_key(p):
                    ext = os.path.splitext(p)[1].lower()
                    # 语义权重：HTML(0) > CSS(1) > ncx/opf(2) > 图片(3) > 其他(4)
                    w = {'html': 0, 'xhtml': 0, 'css': 1, 'ncx': 2, 'opf': 2}.get(ext[1:], 3 if ext in ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.bmp') else 4)
                    # HTML组内细分：spine正文(0)在前按阅读顺序，nav及非spine文档(1)在后按文件名排
                    return (w, ((0 if p in spine and p not in navs else 1), spine.get(p, 0), p.lower()) if w == 0 else (1, p.lower()))
                nl = sorted(z.namelist(), key=sort_key)
                # 构建文件树
                [ (parts := p.split('/'), [ (cur := "/".join(parts[:i+1]), pre := "/".join(parts[:i]), 
                   cur not in self.n_map and self.n_map.update({cur: ftree.insert(self.n_map[pre], "end", text=cur, 
                   tags=(cur if (i==len(parts)-1 and not p.endswith('/')) else cur+"/",))}),
                   (i < len(parts)-1) and ftree.item(self.n_map[cur], open=True) # 直接赋值True 列表全部展开
                  ) for i in range(len(parts) - (1 if p.endswith('/') else 0))]) for p in nl ]
                yield

                css_files = [f for f in nl if f.endswith('.css')]
                html_files = [f for f in nl if f.endswith(('.html', '.xhtml'))]
                # 提取css样式
                for f in css_files:
                    if not self._running: return
                    b_txt = z.read(f).decode('utf-8', 'ignore')
                    [[self.style_data.setdefault(c, {}).setdefault(f, []).append({'selector': p, 'content': re.sub(r';\s*', ';\n  ', b.strip())})
                      for p in [s.strip() for s in sel.split(',')]
                      for m in re.findall(r'(?:\.([\w-]+))|(?:\b([a-zA-Z1-6]+)\b)', p)
                      for c in m if c] for sel, b in re.findall(r'([^{]+)\{([^}]+)\}', re.sub(r'/\*.*?\*/', '', b_txt, flags=re.DOTALL))]
                    yield

                # 多线程动态分发处理html
                max_workers = int(w) if (w := self.workers_cfg) != 'Auto' else max(2, min(os.cpu_count() or 2, 8)) # 读取配置 自动(最低2最高8)或手动的线程数
                all_tasks = [] # 主线程预读字节流 规避ZipFile线程锁.准备动态分发
                for f in html_files:
                    try: all_tasks.append((z.read(f), f))
                    except: pass
                # 按线程动态分发任务.子线程仅负责解析 按文件独立提交.主线程负责结果合并和UI更新
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    fs = {executor.submit(_parse_html_file, data, name): name for data, name in all_tasks}
                    count = 0
                    for future in as_completed(fs):
                        if not self._running: break
                        res = future.result()
                        if res: _merge_results(res) # 回主线程合并数据并更新UI
                        # 10%的任务完成率刷新一次UI，保持界面响应性
                        count += 1
                        if count % 10 == 0:
                            yield
                yield # 每批次完成后交还UI控制权

        # 样式筛选词条标色: 仅带[色号]前缀的词条参与标色 无前缀词条仅用于筛选
        def paint_class_color():
            terms = [ct for t in self.style_filter_terms
                     if t.strip().startswith('[') and (ct := self._parse_color_term(t)) is not None]
            for ct in terms:
                role = ct.role or 'bg'  # 类列表默认背景色
                tree.tag_configure(self._color_tag_name(ct.color, role), **({'foreground': ct.color} if role == 'fg' else {'background': ct.color}))
            for _, cls, iid in self.all_items_refs:
                color = crole = None
                if terms:
                    css_texts = [rule['selector'] + rule['content'] for rules in self.style_data.get(cls, {}).values() for rule in rules]
                    full_text = cls + ("\n" + "\n".join(css_texts) if css_texts else "")
                    for ct in terms:
                        if ct.pattern.search(cls if ct.name_only else full_text):
                            color, crole = ct.color, ct.role or 'bg'; break
                tree.item(iid, tags=(self._color_tag_name(color, crole),) if color else ())
            apply_sort()
        self._paint_tree_colors = paint_class_color  # 供词条编辑器保存后无预览窗时也立即重标类表颜色

        gen = parse_gen()
        def run_step(): # 递归调用生成器分步处理
            if self._running:
                try: next(gen); self._after_id = cw.after(1, run_step) # 只记录最新一个定时器id：链式调度下任意时刻仅一个存活
                except StopIteration: paint_class_color() # 解析完成 全量标色
                except Exception: logger.exception("class_list run_step 发生异常")
        run_step()

        # 显示样式详情+实例
        def show_details(event=None):
            if not (item := (tree.identify_row(event.y) if event else (tree.selection() or [None])[0])) or item in nodes.values(): return
            win = tk.Toplevel(cw)
            rec = self.win_size.setup(win, "class_list_details", f"500x480+{self.root.winfo_x()+320}+{self.root.winfo_y()-20}", mode='cascade')
            win.bind('<Configure>', rec, add='+')
            win.protocol("WM_DELETE_WINDOW", win.destroy); win.focus_force()
            # 获取下一个节点的 lambda，用于左右键切换
            get_nxt = lambda r: (b := tree.get_children(tree.parent(tree.selection()[0])))[(b.index(tree.selection()[0]) + (-1 if r else 1)) % len(b)]

            pw = ttk.PanedWindow(win, orient="vertical"); pw.pack(fill="both", expand=True, padx=5, pady=5)
            ts = [tk.Text(f := ttk.Frame(pw), height=1, font=('Consolas', 10 if i==0 else 9), bg="#ffffff" if i==0 else "#f9f9f9", wrap="word") for i in range(2)]
            [ (pw.add(t.master, weight=w), (v := ttk.Scrollbar(t.master, command=t.yview)).pack(side="right", fill="y"), 
               t.config(yscrollcommand=v.set), t.pack(side="left", fill="both", expand=True)) for t, w in zip(ts, [6, 10]) ]
            if _sash := ClassList._sash_cache.get('class_list_details'):
                win.after(100, lambda: pw.sashpos(0, int(_sash)))
            pw.bind('<ButtonRelease-1>', lambda e: ClassList._sash_cache.update({'class_list_details': str(pw.sashpos(0))}) if pw.winfo_exists() else None)
            def update_view():
                name = tree.item(tree.selection()[0], "text"); win.title(name)
                rules = [f"文件: {p}\n{r['selector']} {{\n" + "\n".join(f"  {l.strip()}" for l in r['content'].split('\n') if l.strip()) + "\n}" 
                         for p, rs in self.style_data.get(name, {}).items() for r in rs]
                # 组装实例数据：利用 setdefault 进行分组
                gps = {}; [gps.setdefault(f, []).append(s) for f, s in self.samples_data.get(name, [])]
                samples = [f"【文件: {f}】\n" + "\n\n".join(ss) for f, ss in gps.items()]
                for t, cnt in zip(ts, ["\n\n".join(rules) or f"/* 未找到 {name} */", "\n\n".join(samples)]):
                    t.config(state="normal"); t.delete("1.0", "end"); t.insert("end", cnt); t.config(state="disabled")
            # 文本框右键复制：有选中复制选中，否则复制全部
            def txt_copy(t, sel):
                txt = t.get("sel.first", "sel.last") if sel else t.get("1.0", "end").rstrip("\n")
                if txt:
                    win.clipboard_clear(); win.clipboard_append(txt)
            dmenu = tk.Menu(win, tearoff=0)
            def on_det_right_click(event):
                t = event.widget; has_sel = bool(t.tag_ranges("sel"))
                dmenu.delete(0, "end")
                dmenu.add_command(label="复制样式名", command=lambda: (win.clipboard_clear(), win.clipboard_append(win.title())))
                dmenu.add_command(label="复制选中", state="normal" if has_sel else "disabled", command=lambda: txt_copy(t, True))
                dmenu.add_command(label="复制全部", command=lambda: txt_copy(t, False))
                dmenu.post(event.x_root, event.y_root)
            [t.bind("<Button-3>", on_det_right_click) for t in ts]
            # 绑定键盘和关闭协议
            [win.bind(k, lambda e, r=v: [tree.selection_set(get_nxt(r)), tree.see(tree.selection()[0]), update_view()]) for k, v in [("<Left>", 1), ("<Right>", 0)]]
            win.protocol("WM_DELETE_WINDOW", lambda: [win.destroy(), tree.focus_set() if tree.winfo_exists() else None]); update_view()
        tree.bind("<Double-1>", show_details)

        def copy_names():
            names = [tree.item(i, "text") for i in tree.selection()]
            self.root.clipboard_clear()
            self.root.clipboard_append('\n'.join(names))
            logger.info(f"已复制 {len(names)} 个样式名到剪贴板")
            tree.focus_set()

        def copy_selected():
            items = tree.selection()
            details_list = []
            for i in items:
                name = tree.item(i, "text")
                if name in self.style_data:
                    for _, rules in self.style_data[name].items():  # 将 path 改为 _，表示不读取文件路径
                        for rule in rules:
                            lines = [f"  {line.strip()}" for line in rule['content'].split('\n') if line.strip()]
                            details_list.append(f"\n{rule['selector']} {{\n" + '\n'.join(lines) + "\n}")
                else:
                    details_list.append(f"{name}\n未找到CSS定义\n")
            self.root.clipboard_clear()
            self.root.clipboard_append('\n'.join(details_list))
            logger.info(f"已复制 {len(items)} 个条目的详细样式到剪贴板")
            tree.focus_set()

        def write_selected_to_style_mem():
            items = tree.selection()
            style_lines = []
            for i in items:
                name = tree.item(i, "text")
                if name in self.style_data:
                    for _, rules in self.style_data[name].items():
                        for rule in rules:
                            lines = [line.strip() for line in rule['content'].split('\n') if line.strip()]
                            style_lines.append(f"{rule['selector']} {{\n" + '\n'.join(lines) + "\n}\n")
            if style_lines:
                self.append_temp_style_content('\n'.join(style_lines) + "\n")
                logger.info(f"已追加 {len(style_lines)} 条详细样式到内存（临时style）")  # 成功仅日志提示 不弹窗
            else:
                logger.info("选中的条目没有找到对应的CSS定义")
                messagebox.showinfo("写入", "选中的条目没有找到对应的CSS定义", parent=cw)  # 无追加才弹窗提示
            tree.focus_set()

        menu = tk.Menu(tree, tearoff=0)
        menu.add_command(label="复制选中条目样式名", command=copy_names)
        menu.add_command(label="复制选中条目详细样式", command=copy_selected)
        menu.add_command(label="临时追加到自定义style", command=write_selected_to_style_mem)
        def on_right_click(event):
            iid = tree.identify_row(event.y)
            if iid:
                tree.selection_add(iid)
                menu.post(event.x_root, event.y_root)
        tree.bind("<Button-3>", on_right_click)

        # 筛选功能：支持正则表达式（忽略大小写），输入非法正则时回退为普通子串匹配
        _filter_hist_id = [None]; _last_filter_q = [None]
        def do_filter(*_):
            keyword = filter_var.get()  # 保留首尾空格(词条空格有效) 仅纯空白视为空
            if not keyword.strip(): keyword = ""
            ct = ClassList._parse_color_term(keyword) if keyword.strip().startswith('[') else None
            regex = ct.raw if ct else keyword
            name_only = bool(ct and ct.name_only)
            try: match = (lambda s: re.search(regex, s, re.IGNORECASE)) if regex else None
            except re.error: match = lambda s: regex.lower() in s.lower()
            tree.selection_remove(tree.selection())
            for group, cls, iid in self.all_items_refs:
                tree.detach(iid)
                css_texts = [] if name_only else [rule['selector'] + rule['content'] for rules in self.style_data.get(cls, {}).values() for rule in rules]
                if not keyword or match(cls) or any(match(t) for t in css_texts):
                    tree.reattach(iid, nodes[group], "end")
            paint_class_color()  # 筛选/清空后重标颜色+排序(paint_class_color末尾调apply_sort)
            # 延迟记录筛选历史(新查询1秒后记录 与正文搜索一致)
            if keyword != _last_filter_q[0]:
                _last_filter_q[0] = keyword
                if _filter_hist_id[0] is not None:
                    try: cw.after_cancel(_filter_hist_id[0])
                    except Exception: pass
                _filter_hist_id[0] = cw.after(1000, lambda q=keyword: self._record_history(
                    q, self.style_filter_history,
                    fixed={dt for t in self.style_filter_terms if (dt := self._dropdown_term(t.strip())) is not None})) if keyword else None

        # 临时样式：单按钮始终可打开编辑窗(可手动编写内容) 窗内底部 确定=保存关闭 清空=原清空功能(有内容才执行 无内容不弹info)
        def edit_temp_style():
            win = tk.Toplevel(cw)
            win.title("编辑临时样式")
            win.geometry(f"400x280+{cw.winfo_x()+60}+{cw.winfo_y()+60}"); win.focus_force()
            row = ttk.Frame(win); row.pack(side="bottom", fill="x", padx=5, pady=(0, 5))  # 先pack底部按钮区 保证任意窗口尺寸下按钮可见
            frame = ttk.Frame(win)
            frame.pack(fill="both", expand=True, padx=5, pady=5)
            text = tk.Text(frame, wrap="word", font=('Consolas', 10))
            text.insert("end", self.get_temp_style_content())
            vsb = ttk.Scrollbar(frame, command=text.yview)
            text.config(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y")
            text.pack(side="left", fill="both", expand=True)
            def on_confirm():
                self.set_temp_style_content(text.get("1.0", "end-1c"))
                logger.info("临时样式编辑已保存")
                win.destroy()
                tree.focus_set()
            def on_clear():
                text.delete("1.0", "end")  # 仅清空编辑区 不写入暂存不记日志 保存由"确定"手动执行 X关闭不落盘
            ttk.Button(row, text="确定", width=6, command=on_confirm).pack(side="right")
            ttk.Button(row, text="清空", width=6, command=on_clear).pack(side="right", padx=(0, 5))
            def on_x_close():
                win.destroy()  # 按X/标题栏关闭: 不保存直接退出 仅确定按钮才写入
                tree.focus_set()
            win.protocol("WM_DELETE_WINDOW", on_x_close)
        ttk.Button(filter_frame, text="临时样式", command=edit_temp_style, width=8).pack(side="left", padx=2, pady=2)

        cw.lift()
        cw.focus_force()
        tree.focus_set()

        # 在窗口关闭时清理旧的缓存目录 用于atexit没触发的情况下
        def clean_old_epub_cache():
            limit = time.time() - 86400
            # 清理sesame_root下修改时间超过24小时的旧目录
            count = sum(1 for p in self.sesame_root.iterdir() if p.is_dir() and p.stat().st_mtime < limit and not shutil.rmtree(p, True))
            if count > 0: logger.info(f"清理了 {count} 个过期的临时缓存目录")