import atexit
import configparser
import os
import re
import shutil
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import lxml.html
from bs4 import BeautifulSoup
from loguru import logger
from tkinterdnd2 import DND_FILES

class ClassList:
    def __init__(self, root, epub_path, get_temp, set_temp, append_temp, workers_cfg='Auto', win_size=None, text_search_terms=None, style_filter_terms=None, config_file=None, text_search_history=None, style_filter_history=None):
        self.root, self.epub_path = root, epub_path
        self.get_temp_style_content, self.set_temp_style_content, self.append_temp_style_content = get_temp, set_temp, append_temp
        self.workers_cfg = workers_cfg
        self.win_size = win_size
        # 下拉词条为内存记忆 持久化仅在搜索词条对话框手动保存时触发(本类save_search_box_only局部写)
        self.text_search_terms = text_search_terms if text_search_terms is not None else []
        self.style_filter_terms = style_filter_terms if style_filter_terms is not None else []
        # 历史(纯内存 不持久化) 由主程序持有 跨ClassList实例共享 关窗/换配置不丢失
        self.text_search_history = text_search_history if text_search_history is not None else []
        self.style_filter_history = style_filter_history if style_filter_history is not None else []
        self.config_file = config_file
        self._search_boxes, self._style_boxes = [], []  # 存活的搜索/筛选下拉框引用 用于词条变更后刷新
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

    # 下拉列表中分隔固定词条与临时(历史)词条的横线 选中时会被识别并跳过
    _SEP_LINE = "─" * 24

    def _combo_values(self, fixed_terms, history):
        """下拉词条: 自定义词条 + 分隔横线 + 历史(新记录排末尾)"""
        fixed = list(fixed_terms)
        slots = max(0, 20 - len(fixed))
        hist = [h for h in history if h not in fixed][-slots:] if slots else []
        sep = [self._SEP_LINE] if fixed and hist else []
        return (fixed + sep + hist)[:20]

    def _record_history(self, q, history):
        """记录历史(去重追加到末尾 上限20条 仅内存)"""
        if not q: return
        if q in history: history.remove(q)
        history.append(q)
        if len(history) > 20: del history[:-20]
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

    def save_search_box_only(self):
        """搜索词条对话框专用: 仅更新[SearchBox]段与search_cfg_editor窗口尺寸 其余配置原样保留"""
        if not self.config_file: return
        try:
            raw = self.config_file.read_text('utf-8')
            pre, sep, rules = raw.partition('[RegexRules]')  # 正则段由regex_manager管理 原样切出
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read_string(pre)
            esc = lambda items: '|'.join(t.replace('|', '\\|') for t in items)
            if geom := self.win_size._states.get('search_cfg_editor'):
                if 'WinSize' not in cfg: cfg.add_section('WinSize')
                cfg['WinSize']['search_cfg_editor'] = geom
            cfg['SearchBox'] = {'text_terms': esc(self.text_search_terms), 'style_terms': esc(self.style_filter_terms)}
            import io
            with io.StringIO() as buf:
                cfg.write(buf)
                out = buf.getvalue().strip()
            if sep: out += '\n\n[RegexRules]' + rules
            self.config_file.write_text(out + '\n', encoding='utf-8')
            logger.info(f"搜索词条已保存(局部): {self.config_file}")
        except Exception as e: logger.error(f"保存搜索词条失败: {e}")

    def edit_search_cfg(self):
        """自定义搜索: 手动编辑正文搜索与样式筛选的下拉词条(保存写入当前配置)"""
        parent = getattr(self, '_cw', self.root)
        d = tk.Toplevel(parent)
        d.title("自定义搜索词条"); d.transient(parent)
        if self.win_size:
            rec = self.win_size.setup(d, "search_cfg_editor", f"380x420+{self.root.winfo_x()+80}+{self.root.winfo_y()+80}", mode='cascade')
            d.bind('<Configure>', rec, add='+')
        d.protocol("WM_DELETE_WINDOW", d.destroy); d.focus_force()
        d.minsize(340, 220)  # 保底尺寸 防止记录的窗口几何过小压没按钮区
        # 先pack底部按钮区(side="bottom") 再pack文本框 保证任意窗口尺寸下按钮始终可见
        row = ttk.Frame(d); row.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        def read_terms(t):
            return list(dict.fromkeys(l for l in (x.strip() for x in t.get("1.0", "end").splitlines()) if l))
        def save_entries():
            self.text_search_terms[:] = read_terms(text_search_box)
            self.style_filter_terms[:] = read_terms(style_filter_box)
            self._refresh_search_boxes()
            self.save_search_box_only()  # 局部保存: 仅[SearchBox]段与search_cfg_editor 不影响其他
            d.destroy()
        ttk.Button(row, text="保存", width=5, command=save_entries).pack(side="right")
        ttk.Button(row, text="关闭", width=5, command=d.destroy).pack(side="right", padx=(0, 5))
        ttk.Label(d, text="正文搜索词条 (每行一条)").pack(side="top", anchor="w", padx=8, pady=(8, 0))
        text_search_box = tk.Text(d, font=('Consolas', 10), undo=True, height=6)
        text_search_box.pack(fill="both", expand=True, padx=8, pady=4)
        text_search_box.insert("1.0", "\n".join(self.text_search_terms))
        ttk.Label(d, text="样式筛选词条 (每行一条)").pack(side="top", anchor="w", padx=8)
        style_filter_box = tk.Text(d, font=('Consolas', 10), undo=True, height=6)
        style_filter_box.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        style_filter_box.insert("1.0", "\n".join(self.style_filter_terms))

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
        ttk.Button(lf_top, text="搜索词条", width=8, command=self.edit_search_cfg).pack(side="right", padx=(0, 4))
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
                         "html_cache": {}, "cache_order": [], "scanned_files": set(), "opacity_cache": {}}

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
                
                # 全局搜索复选框
                global_search_var = tk.BooleanVar(value=False)
                # 勾选切换时强制重扫但不导航跳转(避免把用户"弹回"到结果1所在的文件) 重扫后同步回切换前的匹配位置
                ttk.Checkbutton(sf, text="全局匹配", variable=global_search_var, command=lambda: do_find(reset=True, force=True, navigate=False)).pack(side="left", padx=5)
                sl = ttk.Label(sf, text="0/0"); sl.pack(side="right", padx=5)
                [ttk.Button(sf, text=t, width=3, command=lambda r=v: do_find(rev=r)).pack(side="right") for t, v in [("↓", 0), ("↑", 1)]]
                # wrap="word"渲染颜色多起来会造成卡顿 改为wrap="char"减轻渲染压力
                txt = tk.Text(win, font=('Consolas', 10), wrap="char")
                sv = ttk.Scrollbar(win, command=txt.yview); txt.config(yscrollcommand=sv.set)
                [f.pack(side=s, fill=y, expand=e) for f,s,y,e in [(sv,"right","y",0), (txt,"left","both",1)]]
                [txt.tag_config(k, background=v) for k,v in [("m", "yellow"), ("cur", "orange")]]
                # 匹配opacity块提供灰字高亮标签(之后估摸会做自定义列表?)
                txt.tag_config("opacity_hint", foreground="#7E7A7A")
                # sel(系统选中)提至最高优先级，避免选中标黄文本会难以辨别文字(白字黄底)
                txt.tag_raise("sel")

                # --- 异步多线程匹配：调用 workers_cfg 设置线程数并分块并行处理长文 ---
                def async_render_opacity(content, fpath):
                    if fpath in state["opacity_cache"]:
                        matches = state["opacity_cache"][fpath]
                        def apply_cached_batch(idx=0, step=200):
                            if state.get("current_file") != fpath or idx >= len(matches): return
                            batch = matches[idx:idx+step]
                            # 使用 line.col 定位，消灭批量打标签时的卡顿
                            indices = [v for s, e in batch for v in (to_tk_idx(s, content), to_tk_idx(e, content))]
                            if indices: txt.tag_add("opacity_hint", *indices)
                            win.after(15, apply_cached_batch, idx + step)
                        win.after(0, apply_cached_batch)
                        return

                    def task():
                        try:
                            # 动态获取 workers_cfg 设置的线程数
                            mw = getattr(self, 'workers_cfg', None)
                            if callable(mw): mw = mw()
                            max_workers = int(mw) if mw else 4
                            
                            pattern = re.compile(r'<(p|span|div|h[1-6])\b[^>]*\bstyle=[\'"][^\'"]*opacity:\s*0?\.\d+[^>]*>[\s\S]*?</\1>', re.IGNORECASE)
                            matches = []
                            length = len(content)
                            
                            # 短文本直接单线程，长文本利用线程池分块并行检索（带重叠区防跨行截断）
                            if length < 3000 or max_workers <= 1:
                                matches = [(m.start(), m.end()) for m in pattern.finditer(content)]
                            else:
                                chunk_len = length // max_workers
                                overlap = 2000 # 重叠区长度，防止跨行截断
                                ranges = [(max(0, i * chunk_len - (overlap if i > 0 else 0)), 
                                          min(length, (i + 1) * chunk_len + (overlap if i < max_workers - 1 else 0))) 
                                          for i in range(max_workers)]
                                
                                def search_chunk(sub_range):
                                    sub_start, sub_end = sub_range
                                    sub_str = content[sub_start:sub_end]
                                    return [(sub_start + m.start(), sub_start + m.end()) for m in pattern.finditer(sub_str)]
                                
                                from concurrent.futures import ThreadPoolExecutor
                                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                                    futures = [executor.submit(search_chunk, r) for r in ranges]
                                    seen = set()
                                    for f in futures:
                                        for gs, ge in f.result():
                                            if (gs, ge) not in seen:
                                                seen.add((gs, ge))
                                                matches.append((gs, ge))
                                    matches.sort(key=lambda x: x[0])

                            state["opacity_cache"][fpath] = matches
                            if not matches or state.get("current_file") != fpath: return
                            
                            # 回到主线程分批渲染Tag
                            def apply_batch(idx=0, step=200):
                                if state.get("current_file") != fpath or idx >= len(matches): return
                                batch = matches[idx:idx+step]
                                # 同样使用极速的 line.col 定位
                                indices = [v for s, e in batch for v in (to_tk_idx(s, content), to_tk_idx(e, content))]
                                if indices: txt.tag_add("opacity_hint", *indices)
                                win.after(15, apply_batch, idx + step)
                                
                            win.after(0, apply_batch)
                        except Exception as ex:
                            logger.error(f"异步多线程渲染 opacity 失败: {ex}")

                    from threading import Thread
                    Thread(target=task, daemon=True).start()

                # 显示/搜索共用的换行预处理:两边对同一内容做同一变换,保证字符偏移一致
                def prettify(c):
                    c = c.replace('\r\n', '\n').replace('\r', '\n')  # 归一化换行,防止Tk折叠\r\n导致偏移漂移
                    #【换char所以目前不需要 注释掉】为极长的标签块换行:标签各自独立成行,word wrap不再从标签内部的空格处断行
                    #c = re.sub(r'(</(?:div|p|h[1-6]|ul|ol|li|section|html|body|table)>)\s*', r'\1\n', c, flags=re.I)
                    # 保护措施：强制切分超长单行，防止 Tkinter 在 wrap="word" 下因单行字符过多而直接卡死
                    #c = re.sub(r'([^\n]{1000}[^\s]*)\s+', r'\1\n', c)
                    #c = re.sub(r'([^\n]{3000})', r'\1\n', c)
                    return c

                # 内存读取与缓存函数 (LRU 限制最多存15个文件，避免撑爆内存)
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
                    if len(state["cache_order"]) > 15:
                        del state["html_cache"][state["cache_order"].pop(0)]
                    return content

                # 加载文件内容到文本预览框并同步文件树选中状态
                def load_content_to_text(fpath):
                    if state.get("current_file") == fpath and txt.index("end-1c") != "1.0": return # 防止左右切换或搜索时重复渲染相同的已读文件
                    state["current_file"] = fpath; win.title(fpath)
                    
                    with zipfile.ZipFile(self.epub_path, 'r') as z: content = get_cached_content(fpath, z)
                    
                    txt.config(state="normal"); txt.delete("1.0", "end"); txt.insert("1.0", content); txt.config(state="disabled")
                    
                    # 触发异步多线程高亮 
                    async_render_opacity(content, fpath)
                    # 传入当前文本上下文供坐标极速转换
                    paint_m_async(content)
                    
                    [(ftree.selection_set(n), ftree.see(n)) for n in self.n_map.values() if n and ftree.exists(n) and ftree.item(n, "tags")[0] == fpath]

                # 高亮异步分批渲染: 接收传入的 content 文本以供行列计算
                def paint_m_async(content):
                    key = (state.get("last_q"), state["current_file"])
                    spans = state.get("by_file", {}).get(state["current_file"], ())
                    # 同文件同查询且已有匹配:已刷/正在刷,不重复
                    if spans and state.get("painted") == key: return
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
                    q = se.get()
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
                        
                    try:
                        with zipfile.ZipFile(self.epub_path, "r") as z:
                            nl = z.namelist()
                            if not state["file_pos"]:
                                all_valid = sorted({f for f in nl + list(self.modified_files.keys()) if f.endswith((".html", ".xhtml"))})
                                state["file_pos"] = {f: i for i, f in enumerate(all_valid)}

                            # 非全局时扫描域限定当前文件: 异文件条目会走file_pos跳到别的文件(span超长被clamp到末尾), 故排除
                            if global_search_var.get():
                                cand = list(nl) + list(self.modified_files.keys())
                            else:
                                cand = [state["current_file"]] + [p for p in self.modified_files if p == state["current_file"]]
                            s_files = sorted({f for f in cand
                                            if f.endswith((".html", ".xhtml")) and (f in nl or self.modified_files.get(f) is not None)})
                            
                            # 非全局: 结果域过滤到当前文件双保险拦截残留(modified泄漏/状态遗留), 过滤后补扫无重复计算
                            if not global_search_var.get():
                                state["results"] = [r for r in state["results"] if r[0] == state["current_file"]]
                            
                            new_matches = False
                            for f in s_files:
                                if f not in state["scanned_files"]:
                                    spans = [m.span() for m in re.finditer(q, get_cached_content(f, z))]
                                    state["scanned_files"].add(f)
                                    if spans:
                                        state["by_file"][f] = spans
                                        state["results"].extend((f, s) for s in spans)
                                        new_matches = True
                            
                            if new_matches:
                                state["results"].sort(key=lambda x: (state["file_pos"].get(x[0], 9999), x[1]))
                            # 全局扫描挤出LRU缓存(上限15)致content取空、高亮落1.0, 扫描后回读当前文件
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
                        state['hist_id'] = win.after(1000, lambda q=q: self._record_history(q, self.text_search_history))

                    # 重扫后同步记忆位置: 同文件取起点最近邻(span删改后漂移精确相等常失配); 比较取span[0]起点; 无匹配则置-1回落定位模式
                    if keep is not None:
                        cands = [(i, r[1][0]) for i, r in enumerate(res) if r[0] == keep[0]]
                        state["search_index"] = min(cands, key=lambda t: abs(t[1] - keep[1][0]))[0] if cands else -1

                    if not res:
                        paint_m_async(content)
                        return sl.config(text="0/0")
                        
                    # ↑↓导航:只移动索引(O(1)),不再tag_remove全文重刷、不再重新finditer
                    if navigate:
                        if state["search_index"] < 0:
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
                        
                    if state["search_index"] < 0:  # 当前文件无匹配:显示总数但不强行跳走
                        paint_m_async(content)
                        return sl.config(text=f"-/{len(res)}")
                        
                    target_path, (gs, ge) = res[state["search_index"]]
                    if target_path != state["current_file"]: 
                        load_content_to_text(target_path)  # 跨文件跳转(仅↑↓全局导航时触发)
                        # 文件改变后必须重新拉取最新上下文才能保证坐标正确
                        content = state["html_cache"].get(state["current_file"], "")
                        
                    # 高亮并跳转到当前特定匹配项: 同样使用极速的 line.col 定位
                    txt.tag_remove("cur", "1.0", "end")
                    txt.tag_add("cur", (s_idx := to_tk_idx(gs, content)), to_tk_idx(ge, content))
                    txt.see(s_idx)
                    # 黄底分批异步渲染
                    paint_m_async(content)
                    sl.config(text=f"{state['search_index'] + 1}/{len(res)}")

                # 快捷键: ←→切换文件 ↑↓切换结果 输入防抖搜索; 焦点沿master链判定(下拉展开时焦点在弹层Toplevel), 仅判focus_get() is se会漏判致方向键误触
                def focus_in_search():
                    w = win.focus_get()
                    while w is not None:
                        if w is se: return True
                        try: w = w.master
                        except Exception: return False
                    return False
                def on_switch(rev, _e):
                    if focus_in_search(): return
                    nxt = get_next_text(rev)
                    ftree.selection_set(nxt); ftree.see(nxt)
                    load_content_to_text(ftree.item(nxt, "tags")[0])
                    do_find(reset=True, navigate=False)  # 只定位不高跳
                def on_nav(rev, _e):
                    if focus_in_search(): return
                    do_find(rev=rev)
                win.bind("<Left>",  lambda e, r=1: on_switch(r, e))
                win.bind("<Right>", lambda e, r=0: on_switch(r, e))
                win.bind("<Up>",    lambda e, r=1: on_nav(r, e))
                win.bind("<Down>",  lambda e, r=0: on_nav(r, e))
                # 弹层展开时持全局grab吞掉正文Button-1; 改绑<<Unpost>>: 关闭时若指针在正文区则多档延迟夺焦(组合框Unpost后可能异步再抢); 列表选中时弹层也关闭, 用"最近选中"标记排除以保持搜索焦点
                _sel_at = [0.0]
                se.bind("<<ComboboxSelected>>", lambda e: _sel_at.__setitem__(0, time.monotonic()), add="+")
                # 延迟夺焦带销毁防护: 预览窗关闭后挂起after仍触发, 对已销毁txt执行focus_set抛TclError, 先查存活
                def _grab_txt():
                    try:
                        if txt.winfo_exists(): txt.focus_set()
                    except tk.TclError: pass
                def on_unpost(_e=None):
                    try:
                        if not win.winfo_exists() or time.monotonic() - _sel_at[0] < 0.25: return
                    except tk.TclError: return
                    mx, my = win.winfo_pointerxy()
                    if sf.winfo_rooty() + sf.winfo_height() <= my < win.winfo_rooty() + win.winfo_height() \
                       and win.winfo_rootx() <= mx < win.winfo_rootx() + win.winfo_width():
                        _grab_txt()
                        [win.after(d, _grab_txt) for d in (10, 60, 150)]
                se.bind("<<Unpost>>", on_unpost, add="+")
                # 兜底:部分Tk版本点击外部关闭弹层不产生<<Unpost>> 轮询弹层mapped状态转移捕捉关闭时刻
                def _poll_popdown():
                    try:
                        if not win.winfo_exists(): return
                    except tk.TclError: return
                    try:
                        mapped = bool(int(se.tk.call('winfo', 'ismapped',
                                    se.tk.call('::ttk::combobox::PopdownWindow', str(se)))))
                    except Exception:
                        mapped = False
                    if getattr(_poll_popdown, 'was', False) and not mapped: on_unpost(None)
                    _poll_popdown.was = mapped
                    win.after(100, _poll_popdown)
                _poll_popdown()
                # 防抖触发也改成 navigate=False(输入时只刷新高亮定位,不跳文件) 200ms防抖兼顾输入流畅与残留清理
                (ft := [0]) and se.bind("<KeyRelease>", lambda e: (win.after_cancel(ft[0]) if ft[0] else None, ft.__setitem__(0, win.after(200, lambda: do_find(reset=True, navigate=False)))))
                
                # 打开窗口默认焦点强行切到 txt(文本预览区域)
                txt.focus_set()
                win.after(100, lambda: txt.focus_set())
                
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
        gen = parse_gen()
        def run_step(): # 递归调用生成器分步处理
            if self._running:
                try: next(gen); self._after_id = cw.after(1, run_step) # 只记录最新一个定时器id：链式调度下任意时刻仅一个存活
                except StopIteration: pass # 正常结束，静默处理
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
            info = ("选中的条目没有找到对应的CSS定义", f"已追加 {len(items)} 个条目的详细样式到内存（临时style）")
            if style_lines: 
                self.append_temp_style_content('\n'.join(style_lines) + "\n")
                logger.info(info[1])
            messagebox.showinfo("写入", info[bool(style_lines)], parent=cw)
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
            keyword = filter_var.get().strip()
            try: match = (lambda s: re.search(keyword, s, re.IGNORECASE)) if keyword else None
            except re.error: match = lambda s: keyword.lower() in s.lower()
            tree.selection_remove(tree.selection())
            for group, cls, iid in self.all_items_refs:
                tree.detach(iid)
                # 提取所有相关的 CSS 文本
                css_texts = [rule['selector'] + rule['content'] for rules in self.style_data.get(cls, {}).values() for rule in rules]
                # 执行综合匹配：关键词为空、匹配类名或匹配 CSS 内容
                if not keyword or match(cls) or any(match(t) for t in css_texts):
                    tree.reattach(iid, nodes[group], "end")
            apply_sort()  # 筛选/清空后保持排序 未点过表头则按默认类名升序(与初始一致)
            # 延迟记录筛选历史(新查询1秒后记录 与正文搜索一致)
            if keyword != _last_filter_q[0]:
                _last_filter_q[0] = keyword
                if _filter_hist_id[0] is not None:
                    try: cw.after_cancel(_filter_hist_id[0])
                    except Exception: pass
                _filter_hist_id[0] = cw.after(1000, lambda q=keyword: self._record_history(q, self.style_filter_history)) if keyword else None

        # 编辑临时样式：弹出一个可编辑的Text窗口，显示全部暂存样式，编辑后自动保存
        def edit_temp_style():
            win = tk.Toplevel(cw)
            win.title("编辑临时样式")
            win.geometry(f"400x280+{cw.winfo_x()+60}+{cw.winfo_y()+60}"); win.focus_force()
            frame = ttk.Frame(win)
            frame.pack(fill="both", expand=True, padx=5, pady=5)
            text = tk.Text(frame, wrap="word", font=('Consolas', 10))
            current_content = self.get_temp_style_content()
            text.insert("end", current_content)
            vsb = ttk.Scrollbar(frame, command=text.yview)
            text.config(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y")
            text.pack(side="left", fill="both", expand=True)
            def on_close():
                self.set_temp_style_content(text.get("1.0", "end-1c"))
                logger.info("临时样式编辑已关闭并保存")
                win.destroy()
                tree.focus_set()
            win.protocol("WM_DELETE_WINDOW", on_close)

        def clear_temp_style():
            self.set_temp_style_content("")
            logger.info("已清空临时样式")
            messagebox.showinfo("清空", "临时样式已清空", parent=cw)
            tree.focus_set()
        btn_edit = ttk.Button(filter_frame, text="编辑", command=edit_temp_style, width=5)
        btn_edit.pack(side="left", padx=2, pady=2)
        btn_clear = ttk.Button(filter_frame, text="清空", command=clear_temp_style, width=5)
        btn_clear.pack(side="left", padx=2, pady=2)

        cw.lift()
        cw.focus_force()
        tree.focus_set()

        # 在窗口关闭时清理旧的缓存目录 用于atexit没触发的情况下
        def clean_old_epub_cache():
            limit = time.time() - 86400
            # 清理sesame_root下修改时间超过24小时的旧目录
            count = sum(1 for p in self.sesame_root.iterdir() if p.is_dir() and p.stat().st_mtime < limit and not shutil.rmtree(p, True))
            if count > 0: logger.info(f"清理了 {count} 个过期的临时缓存目录")