import atexit
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import lxml.html
from bs4 import BeautifulSoup
from loguru import logger
from tkinterdnd2 import DND_FILES

class ClassList:
    def __init__(self, root, epub_path, get_temp, set_temp, append_temp, win_size=None):
        self.root, self.epub_path = root, epub_path
        self.get_temp_style_content, self.set_temp_style_content, self.append_temp_style_content = get_temp, set_temp, append_temp
        self.win_size = win_size
        self.style_data, self.samples_data, self.counts_data = {}, {}, {}
        self.cats = {k: set() for k in ['Class列表', 'Span列表', '图片Class列表', '非P标签列表', '非P、img、body标签列表']}
        self.all_items_refs, self.n_map, self.st = [], {"": ""}, {"#0": False, "count": False}
        self.preview_window = self.details_window = None
        self._after_ids = []
        self._running = True
        self.modified_files = {}
        self._dragging = False # 拖入拖出 互斥锁
        self.sesame_root = Path(tempfile.gettempdir(), "sesame_cache"); self.sesame_root.mkdir(parents=True, exist_ok=True)
        self.show_class_list()

    def show_class_list(self):
        cw = tk.Toplevel(self.root)
        cw.title("html内样式收集分析")
        cw.protocol("WM_DELETE_WINDOW", lambda c=cw: (setattr(self, '_running', False), 
                                                      [c.after_cancel(aid) for aid in self._after_ids], 
                                                      [(w.unbind('<Destroy>'), w.destroy()) for w in c.winfo_children()], 
                                                      [clean_old_epub_cache()],  # 关闭时清理旧缓存
                                                      c.destroy()))
        self.win_size.setup(cw, "class_list_main", f"600x480+{self.root.winfo_x()+-30}+{self.root.winfo_y()+30}")
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
                    [z_out.writestr(item, z_in.read(item.filename)) for item in z_in.infolist() if item.filename not in self.modified_files]
                    [z_out.writestr(path, content) for path, content in self.modified_files.items() if content]
                shutil.move(tmp_path, self.epub_path); self.modified_files.clear()
                logger.success("修改已成功保存至epub。")
                messagebox.showinfo("保存", "修改已成功保存至EPUB。", parent=cw)
            except Exception as e: 
                logger.exception(f"保存epub失败: {e}")
                messagebox.showerror("保存失败", str(e), parent=cw)
        
        ttk.Button(lf_top, text="保存", width=5, command=save_changes).pack(side="right")
        lf_tree_frame = ttk.Frame(lf); lf_tree_frame.pack(fill="both", expand=True, padx=(3, 0), pady=2)
        ftree = ttk.Treeview(lf_tree_frame, show="tree", selectmode="extended")
        ftree.pack(side="left", fill="both", expand=True)
        f_vsb = ttk.Scrollbar(lf_tree_frame, command=ftree.yview); f_vsb.pack(side="right", fill="y")
        ftree.config(yscrollcommand=f_vsb.set)
        
        # 文件树右键菜单 选中项删除确认及内存标记
        ftree_menu = tk.Menu(ftree, tearoff=0)
        ftree_menu.add_command(label="删除文件", command=lambda: (sel := ftree.selection()) and 
                                    messagebox.askyesno("确认", f"确认删除选中的 {len(sel)} 个项目?", parent=cw) and 
                                    [ (self.modified_files.__setitem__(ftree.item(i, "tags")[0], None), ftree.delete(i)) for i in sel ])
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
            # 拖出处理函数 (强制恢复视觉状态：利用锁定集合覆盖系统当前的单选状态)
            def drag_out_handler(event):
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
            # 绑定：锁定多选逻辑
            ftree.bind("<<TreeviewSelect>>", lambda e: setattr(drag_out_handler, 'last_sel', ftree.selection()))
            # Button-1 按下时，如果点击项在已选集中，则立即锁定整个集合防止 DND 启动时重置
            def lock_sel(e):
                rid, l_sel = ftree.identify_row(e.y), getattr(drag_out_handler, 'last_sel', ())
                setattr(drag_out_handler, 'locked_sel', l_sel if rid in l_sel else (rid,))
            ftree.bind("<Button-1>", lock_sel, add="+")

            # 注册拖放事件
            ftree.drop_target_register(DND_FILES); ftree.dnd_bind("<<Drop>>", drop_handler)
            ftree.drag_source_register(1, DND_FILES); ftree.dnd_bind("<<DragInitCmd>>", drag_out_handler)

        # 右侧class列表
        filter_frame = ttk.Frame(rf); filter_frame.pack(fill="x", padx=(0, 3), pady=2)
        filter_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=filter_var).pack(side="left", fill="x", expand=True)
        tf = ttk.Frame(rf); tf.pack(fill="both", expand=True, padx=(0, 3), pady=2)
        tree = ttk.Treeview(tf, columns=("count",), show="tree headings", selectmode="extended")

        # 排序逻辑函数
        self.lc = None
        def sort_col(col):
            self.st[col], self.lc = ((col == "#0") if col != self.lc else not self.st[col]), col
            for n in nodes.values():
                items = sorted([(tree.set(c, "count"), tree.item(c, "text"), c) for c in tree.get_children(n)], key=lambda x: int(x[0]) if col=="count" else x[1].lower(), reverse=not self.st[col])
                [tree.move(it[2], n, i) for i, it in enumerate(items)]
            [tree.heading(c, text=f"{'类名' if c=='#0' else '总量'}{(' ▲' if self.st[c] else ' ▼') if c==col else ''}") for c in ["#0", "count"]]

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
                rec = self.win_size.setup(win := tk.Toplevel(cw), key, f"600x500+{self.root.winfo_x()+60}+{self.root.winfo_y()+50}", mode='cascade')
                win.bind('<Configure>', rec, add='+')
                win.protocol("WM_DELETE_WINDOW", win.destroy); win.focus_force()
                state = {"current_file": p, "search_results": [], "search_index": -1, "last_q": None} # 状态存储 (用于搜索)

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
                se = ttk.Entry(sf); se.pack(side="left", fill="x", expand=1)
                
                # 全局搜索复选框
                global_search_var = tk.BooleanVar(value=False)
                ttk.Checkbutton(sf, text="全局匹配", variable=global_search_var, command=lambda: do_find(reset=True)).pack(side="left", padx=5)
                sl = ttk.Label(sf, text="0/0"); sl.pack(side="right", padx=5)
                [ttk.Button(sf, text=t, width=3, command=lambda r=v: do_find(rev=r)).pack(side="right") for t, v in [("↓", 0), ("↑", 1)]]
                txt = tk.Text(win, font=('Consolas', 10), wrap="word")
                sv = ttk.Scrollbar(win, command=txt.yview); txt.config(yscrollcommand=sv.set)
                [f.pack(side=s, fill=y, expand=e) for f,s,y,e in [(sv,"right","y",0), (txt,"left","both",1)]]
                [txt.tag_config(k, background=v) for k,v in [("m", "yellow"), ("cur", "orange")]]

                # 加载文件内容到文本预览框并同步文件树选中状态
                def load_content_to_text(fpath):
                    state["current_file"] = fpath; win.title(fpath)
                    content = (self.modified_files[fpath].decode('utf-8', 'ignore') if fpath in self.modified_files and self.modified_files[fpath] is not None else
                            zipfile.ZipFile(self.epub_path, 'r').read(fpath).decode('utf-8', 'ignore') if fpath in zipfile.ZipFile(self.epub_path, 'r').namelist() else "")
                    txt.config(state="normal"); txt.delete("1.0", "end"); txt.insert("1.0", content); txt.config(state="disabled")
                    [(ftree.selection_set(n), ftree.see(n)) for n in self.n_map.values() if n and ftree.exists(n) and ftree.item(n, "tags")[0] == fpath]

                # 执行正则搜索定位，支持全局匹配与高亮
                def do_find(rev=False, reset=False):
                    [txt.tag_remove(t, "1.0", "end") for t in ("m", "cur")]
                    if not (q := se.get()): return sl.config(text="0/0")
                    if q != state["last_q"] or reset: # 仅在查询变动或重置时重新扫描
                        state.update({"last_q": q, "search_results": [], "search_index": -1})
                        try:
                            with zipfile.ZipFile(self.epub_path, "r") as z:
                                nl = z.namelist()
                                s_files = sorted({f for f in (nl if global_search_var.get() else [state["current_file"]]) + list(self.modified_files.keys()) 
                                                if f.endswith((".html", ".xhtml")) and (f in nl or self.modified_files.get(f) is not None)})
                                [state["search_results"].extend([{"path": f, "span": m.span()} for m in re.finditer(q, (self.modified_files[f] if f in self.modified_files 
                                and self.modified_files[f] is not None else z.read(f)).decode("utf-8", "ignore"))]) for f in s_files]
                            if res := state["search_results"]: state["search_index"] = next((i for i, r in enumerate(res) if r["path"] == state["current_file"]), 0)
                        except Exception as e: 
                            logger.error(f"正则搜索失败: {e}")
                            return sl.config(text="Err")
                    if not state["search_results"]: return sl.config(text="0/0")
                    state["search_index"] = (state["search_index"] + (-1 if rev else 1)) % len(state["search_results"]) if not reset else state["search_index"]
                    target = state["search_results"][state["search_index"]]
                    if target["path"] != state["current_file"]: load_content_to_text(target["path"])
                    # 批量高亮所有匹配项
                    s_idx, cv = "1.0", tk.IntVar()
                    while (s_idx := txt.search(q, s_idx, "end", count=cv, regexp=True)): 
                        txt.tag_add("m", s_idx, (e_idx := f"{s_idx}+{cv.get()}c")); s_idx = e_idx
                    # 高亮并跳转到当前特定匹配项
                    m_idx, s_idx = sum(1 for i in range(state["search_index"]) if state["search_results"][i]["path"] == target["path"]), "1.0"
                    for _ in range(m_idx + 1): 
                        if not (s_idx := txt.search(q, s_idx, "end", count=cv, regexp=True)): break
                        if _ == m_idx: (txt.tag_add("cur", s_idx, (nxt := f"{s_idx}+{cv.get()}c")), txt.see(s_idx))
                        s_idx = f"{s_idx}+{cv.get()}c"
                    sl.config(text=f"{state['search_index'] + 1}/{len(state['search_results'])}")

                # 批量绑定快捷键：左右键切换文件，上下键切换搜索结果，输入框自动防抖搜索
                [win.bind(k, lambda e, r=v: [ftree.selection_set(nxt := get_next_text(r)), ftree.see(nxt), load_content_to_text(ftree.item(nxt, "tags")[0]), do_find(reset=True)]) for k, v in [("<Left>", 1), ("<Right>", 0)]]
                [win.bind(k, lambda e, r=v: do_find(rev=r)) for k, v in [("<Up>", 1), ("<Down>", 0)]]
                (ft := [0]) and se.bind("<KeyRelease>", lambda e: (win.after_cancel(ft[0]) if ft[0] else None, ft.__setitem__(0, win.after(500, lambda: do_find(reset=True)))))
                se.focus_set(); load_content_to_text(p)
            except Exception as ex: 
                logger.exception(f"预览文件时发生错误: {ex}")
                messagebox.showerror("错误", str(ex), parent=cw)
        ftree.bind("<Double-1>", preview_file)

        # Bs4获取OPF Spine顺序 解析XML并构建映射
        def get_opf_spine_order(z):
            try:
                bs = BeautifulSoup(z.read("META-INF/container.xml").decode("utf-8"), "xml")
                opf_full_path = (bs.find("rootfile") or {}).get("full-path", "")
                if not opf_full_path: return {}
                opf_dir = (lambda d: f"{d.replace(chr(92), '/')}/" if d else "")(os.path.dirname(opf_full_path))
                opf_soup = BeautifulSoup(z.read(opf_full_path).decode("utf-8"), "xml")
                manifest = {it.get("id"): it.get("href", "") for it in opf_soup.find_all("item") if it.get("id")}
                return {f"{opf_dir}{manifest[idref]}": idx 
                        for idx, itemref in enumerate(opf_soup.find_all("itemref")) 
                        if (idref := itemref.get("idref")) and idref in manifest}
            except: return {}

        # 构建epub文件树 提取样式和实例数据
        def parse_gen():
            with zipfile.ZipFile(self.epub_path, 'r') as z:
                spine= get_opf_spine_order(z)
                def sort_key(p):
                    low = p.lower()
                    # 语义权重：HTML(0) > CSS(1) > ncx/opf/xml(2) > 其他(3)
                    w = 0 if low.endswith(('.html', '.xhtml')) else 1 if low.endswith('.css') else 2 if low.endswith(('.ncx', '.opf', '.xml')) else 3
                    return (not p.endswith('/'), w, (0, spine.get(p, 0)) if w == 0 else (1, low))
                nl = sorted(z.namelist(), key=sort_key)
                [ (parts := p.split('/'), [ (cur := "/".join(parts[:i+1]), pre := "/".join(parts[:i]), 
                   cur not in self.n_map and self.n_map.update({cur: ftree.insert(self.n_map[pre], "end", text=cur, 
                   tags=(cur if (i==len(parts)-1 and not p.endswith('/')) else cur+"/",))}),
                   (i < len(parts)-1) and ftree.item(self.n_map[cur], open=True) # 直接赋值True 列表全部展开
                  ) for i in range(len(parts) - (1 if p.endswith('/') else 0))]) for p in nl ]
                yield
                for f in nl:
                    if not self._running: return
                    # 提取css样式
                    if f.endswith('.css'):
                        b_txt = z.read(f).decode('utf-8', 'ignore')
                        [[self.style_data.setdefault(c, {}).setdefault(f, []).append({'selector': p, 'content': re.sub(r';\s*', ';\n  ', b.strip())})
                          for p in [s.strip() for s in sel.split(',')]
                          for m in re.findall(r'(?:\.([\w-]+))|(?:\b([a-zA-Z1-6]+)\b)', p)
                          for c in m if c] for sel, b in re.findall(r'([^{]+)\{([^}]+)\}', re.sub(r'/\*.*?\*/', '', b_txt, flags=re.DOTALL))]
                    # 处理xhtml 使用 XPath 仅提取带 class 的标签
                    elif f.endswith(('.html', '.xhtml')):
                        for el in lxml.html.fromstring(z.read(f)).xpath('//*[@class]'):
                            tag, cls_list = el.tag.rsplit('}', 1)[-1].lower(), el.get('class').split()
                            for c in cls_list:
                                self.counts_data[c] = self.counts_data.get(c, 0) + 1
                                [ (self.cats[k].add(c), key := (k, c),
                                   tree.item(class_to_iid[key], values=(self.counts_data[c],)) if key in class_to_iid else
                                   (class_to_iid.update({key: tree.insert(nodes[k], "end", text=c, values=(self.counts_data[c],))}),
                                    self.all_items_refs.append((k, c, class_to_iid[key])),
                                    # 字母顺序重排
                                    ch := sorted(tree.get_children(nodes[k]), key=lambda x: tree.item(x, 'text').lower()),
                                    [tree.move(child, nodes[k], idx) for idx, child in enumerate(ch)]))
                                  for k, v in [('Class列表', 1), ('Span列表', tag=='span'), ('图片Class列表', tag=='img'), 
                                               ('非P标签列表', tag!='p'), ('非P、img、body标签列表', tag not in ['p','img','body'])] if v ]
                                if len(self.samples_data.get(c, [])) < 15: # 提取15个实例
                                    s_raw = lxml.html.tostring(el, encoding='unicode', method='html', with_tail=False).strip()
                                    self.samples_data.setdefault(c, []).append((f, re.sub(r'\s+', ' ', s_raw)[:150]))
                        yield
        gen = parse_gen()
        def run_step(): # 递归调用生成器分步处理
            if self._running:
                try: (next(gen), self._after_ids.append(cw.after(1, run_step)))
                except StopIteration: pass # 正常结束，静默处理
                except Exception: logger.exception("class_list run_step 发生异常")
        run_step()

        # 显示样式详情+实例
        def show_details(event=None):
            if not (item := (tree.identify_row(event.y) if event else (tree.selection() or [None])[0])) or item in nodes.values(): return
            win = tk.Toplevel(cw)
            rec = self.win_size.setup(win, "class_list_details", f"500x480+{self.root.winfo_x()+140}+{self.root.winfo_y()+80}", mode='cascade')
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
            # 绑定键盘和关闭协议
            [win.bind(k, lambda e, r=v: [tree.selection_set(get_nxt(r)), tree.see(tree.selection()[0]), update_view()]) for k, v in [("<Left>", 1), ("<Right>", 0)]]
            win.protocol("WM_DELETE_WINDOW", lambda: [win.destroy(), tree.focus_set() if tree.winfo_exists() else None]); update_view()
        tree.bind("<Double-1>", show_details)

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
            messagebox.showinfo("复制", f"已复制 {len(items)} 个条目的详细样式到剪贴板", parent=cw)
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
        menu.add_command(label="复制选中条目详细样式", command=copy_selected)
        menu.add_command(label="临时追加到自定义style", command=write_selected_to_style_mem)
        def on_right_click(event):
            iid = tree.identify_row(event.y)
            if iid:
                tree.selection_add(iid)
                menu.post(event.x_root, event.y_root)
        tree.bind("<Button-3>", on_right_click)

        # 筛选功能
        def do_filter(*_):
            keyword = filter_var.get().strip().lower()
            tree.selection_remove(tree.selection())
            for group, cls, iid in self.all_items_refs:
                tree.detach(iid)
                # 提取所有相关的 CSS 文本
                css_texts = [rule['selector'] + rule['content'] for rules in self.style_data.get(cls, {}).values() for rule in rules]
                # 执行综合匹配：关键词为空、匹配类名或匹配 CSS 内容
                if not keyword or keyword in cls.lower() or any(keyword in text.lower() for text in css_texts):
                    tree.reattach(iid, nodes[group], "end")
        filter_var.trace_add("write", do_filter)

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