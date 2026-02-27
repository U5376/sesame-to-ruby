import atexit
import os
import re
import shutil
import tempfile
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox
import lxml.html

class ClassList:
    def __init__(self, root, epub_path, get_temp, set_temp, append_temp, win_size=None):
        self.root, self.epub_path = root, epub_path
        self.get_temp_style_content, self.set_temp_style_content, self.append_temp_style_content = get_temp, set_temp, append_temp
        self.win_size = win_size
        self.style_data, self.samples_data, self.counts_data = {}, {}, {}
        self.cats = {k: set() for k in ['Class列表', 'Span列表', '图片Class列表', '非P标签列表', '非P、img、body标签列表']}
        self.all_items_refs, self.n_map, self.st = [], {"": ""}, {"#0": False, "count": False}
        self.show_class_list()

    def show_class_list(self):
        cw = tk.Toplevel(self.root)
        cw.title("html内样式收集分析")
        self.win_size.setup(cw, "class_list_main", f"600x480+{self.root.winfo_x()+-30}+{self.root.winfo_y()+30}")

        pw = ttk.PanedWindow(cw, orient="horizontal")
        pw.pack(fill="both", expand=True)
        
        # 左侧文件树
        lf, rf = ttk.Frame(pw, width=150), ttk.Frame(pw, width=330)
        [pw.add(f, weight=w) for f, w in [(lf, 1), (rf, 0)]]
        ftree = ttk.Treeview(lf, show="tree", selectmode="browse")
        ftree.pack(side="left", fill="both", expand=True)
        f_vsb = ttk.Scrollbar(lf, command=ftree.yview); f_vsb.pack(side="right", fill="y")
        ftree.config(yscrollcommand=f_vsb.set)
        # 右侧class列表
        filter_frame = ttk.Frame(rf); filter_frame.pack(fill="x", padx=3, pady=2)
        filter_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=filter_var).pack(side="left", fill="x", expand=True)
        tf = ttk.Frame(rf); tf.pack(fill="both", expand=True, padx=3, pady=2)
        tree = ttk.Treeview(tf, columns=("count",), show="tree headings", selectmode="extended")

        # 排序逻辑函数
        self.lc = None
        def sort_col(col):
            self.st[col] = (col == "#0") if col != self.lc else not self.st[col]
            self.lc = col
            for n in nodes.values():
                items = sorted([(tree.set(c, "count"), tree.item(c, "text"), c) for c in tree.get_children(n)], 
                               key=lambda x: int(x[0]) if col=="count" else x[1].lower(), reverse=not self.st[col])
                [tree.move(it[2], n, i) for i, it in enumerate(items)]
            [tree.heading(c, text=f"{'类名' if c=='#0' else '总量'}{(' ▲' if self.st[c] else ' ▼') if c==col else ''}") for c in ["#0", "count"]]

        # 初始表头设置
        tree.heading("#0", text="类名", anchor="w", command=lambda: sort_col("#0"))
        tree.heading("count", text="总量", anchor="center", command=lambda: sort_col("count"))
        tree.column("#0", width=200); tree.column("count", width=50, anchor="center")
        tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tf, command=tree.yview); vsb.grid(row=0, column=1, sticky="ns")
        tree.config(yscrollcommand=vsb.set); tf.columnconfigure(0, weight=1); tf.rowconfigure(0, weight=1)

        ttk.Style().configure("Treeview", indent=8) #调整Treeview缩进余白
        # 默认展开控制
        nodes = {k: tree.insert("", "end", text=k, open=(k in ['Class列表', 'Span列表', '图片Class列表'])) for k in self.cats}
        class_to_iid = {}

        # 预览逻辑+搜索框
        def preview_file(e):
            if not (sel := ftree.selection()) or not (p := ftree.item(sel[0], "tags")[0]) or p.endswith('/'): return
            try:
                # 图片读取逻辑:解压至临时文件并调用默认图片查看器
                exts = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
                if p.lower().endswith(exts):
                    # 建立基于EPUB路径指纹的唯一临时目录
                    td = os.path.join(tempfile.gettempdir(), f"epub_img_{hash(self.epub_path)}")
                    if not os.path.exists(td):
                        os.makedirs(td)
                        # 注册清理逻辑，d=td 利用默认参数捕获当前路径
                        atexit.register(lambda d=td: shutil.rmtree(d, ignore_errors=True))
                        # 一次性全量解压(这里可能需要性能优化 改成异步处理或者按需解压)
                        with zipfile.ZipFile(self.epub_path, 'r') as z:
                            [open(os.path.join(td, os.path.basename(x)), 'wb').write(z.read(x)) for x in z.namelist() if x.lower().endswith(exts)]
                    target = os.path.join(td, os.path.basename(p))
                    return os.startfile(target) if hasattr(os, 'startfile') else __import__('subprocess').run(['open', target])
                #  内存读取预览文本逻辑 显示内容+正则搜索
                win = tk.Toplevel(cw); win.geometry(f"600x500+{self.root.winfo_x()+60}+{self.root.winfo_y()+50}"); win.focus_force()
                
                #定义跳过图片的获取逻辑 (用于左右键切换)
                def get_next_text(rev):
                    bro = ftree.get_children(ftree.parent(ftree.selection()[0]))
                    idx = bro.index(ftree.selection()[0])
                    valid = [b for b in bro if not (p := ftree.item(b, "tags")[0].lower()).endswith(exts) and not p.endswith('/')]
                    if not valid: return ftree.selection()[0]
                    curr_sel = ftree.selection()[0]
                    if curr_sel in valid:
                        return valid[(valid.index(curr_sel) + (-1 if rev else 1)) % len(valid)]
                    return valid[(idx + (-1 if rev else 0)) % len(valid)]

                sf = ttk.Frame(win); sf.pack(fill="x", padx=2, pady=2)
                se = ttk.Entry(sf); se.pack(side="left", fill="x", expand=1)
                sl = ttk.Label(sf, text="0/0"); sl.pack(side="right", padx=5)
                [ttk.Button(sf, text=t, width=3, command=lambda r=v: do_find(r)).pack(side="right") for t, v in [("↓", 0), ("↑", 1)]]
                txt = tk.Text(win, font=('Consolas', 10), wrap="word")
                sv = ttk.Scrollbar(win, command=txt.yview); txt.config(yscrollcommand=sv.set)
                [f.pack(side=s, fill=y, expand=e) for f,s,y,e in [(sv,"right","y",0), (txt,"left","both",1)]]
                [txt.tag_config(k, background=v) for k,v in [("m", "yellow"), ("cur", "orange")]]
                def do_find(rev=False):
                    [txt.tag_remove(t, "1.0", "end") for t in ("m", "cur")]
                    if not (q := se.get()): return sl.config(text="0/0")
                    res, s, n = [], "1.0", tk.IntVar()
                    while (s := txt.search(q, s, "end", regexp=1, count=n)):
                        res.append((s, n.get())); s = f"{s}+{res[-1][1]}c"
                    if not res: return sl.config(text="0/0")
                    l_q = getattr(do_find, 'lq', "")
                    do_find.i = (getattr(do_find, 'i', -1) + (-1 if rev else 1)) % len(res) if q == l_q else (0 if not rev else -1)
                    do_find.lq, (p_c, p_l) = q, res[do_find.i]
                    [txt.tag_add("m", r, f"{r}+{rl}c") for r, rl in res]
                    txt.tag_add("cur", p_c, f"{p_c}+{p_l}c")
                    txt.see(p_c); sl.config(text=f"{do_find.i+1}/{len(res)}")

                def load_content():
                    cp = ftree.item(ftree.selection()[0], "tags")[0]; win.title(cp)
                    with zipfile.ZipFile(self.epub_path, 'r') as z: content = z.read(cp).decode('utf-8', 'ignore')
                    txt.config(state="normal"); txt.delete("1.0", "end"); txt.insert("1.0", content); txt.config(state="disabled")
                    do_find()

                # 键盘绑定：回车/下键=向下，Shift+回车/上键=向上 左右键绑定 使用 get_next_text 自动过滤图片
                [win.bind(k, lambda e, r=v: [ftree.selection_set(get_next_text(r)), ftree.see(ftree.selection()[0]), load_content()]) for k, v in [("<Left>", 1), ("<Right>", 0)]]
                (ft := [0]) and se.bind("<KeyRelease>", lambda e: (win.after_cancel(ft[0]) if ft[0] else None, ft.__setitem__(0, win.after(500, lambda: do_find(0)))))
                se.focus_set(); load_content()
            except Exception as ex: messagebox.showerror("错误", str(ex), parent=cw)
        ftree.bind("<Double-1>", preview_file)

        def parse_gen():
            with zipfile.ZipFile(self.epub_path, 'r') as z:
                nl = sorted(z.namelist())
                [ (parts := p.split('/'), [ (cur := "/".join(parts[:i+1]), pre := "/".join(parts[:i]), 
                   cur not in self.n_map and self.n_map.update({cur: ftree.insert(self.n_map[pre], "end", text=cur, tags=(cur if (i==len(parts)-1 and not p.endswith('/')) else cur+"/",))}),
                   (i < len(parts)-1 and any(x.endswith(('.html', '.xhtml')) for x in nl if x.startswith(cur))) and ftree.item(self.n_map[cur], open=True)
                  ) for i in range(len(parts))]) for p in nl ]
                yield
                for f in nl:
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
                                if len(self.samples_data.get(c, [])) < 10: # 提取10个实例
                                    s_raw = lxml.html.tostring(el, encoding='unicode', method='html', with_tail=False).strip()
                                    self.samples_data.setdefault(c, []).append((f, re.sub(r'\s+', ' ', s_raw)[:150]))
                        yield

        gen = parse_gen()
        def run_step():
            try: next(gen); cw.after(1, run_step)
            except (StopIteration, Exception): pass
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
                samples = [f"【文件: {f}】\n" + "\n".join(ss) for f, ss in gps.items()]
                for t, cnt in zip(ts, ["\n\n".join(rules) or f"/* 未找到 {name} */", "\n\n".join(samples)]):
                    t.config(state="normal"); t.delete("1.0", "end"); t.insert("end", cnt); t.config(state="disabled")
            # 绑定键盘和关闭协议
            [win.bind(k, lambda e, r=v: [tree.selection_set(get_nxt(r)), tree.see(tree.selection()[0]), update_view()]) for k, v in [("<Left>", 1), ("<Right>", 0)]]
            win.protocol("WM_DELETE_WINDOW", lambda: [win.destroy(), tree.focus_set()]); update_view()
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
            if style_lines: self.append_temp_style_content('\n'.join(style_lines) + "\n")
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
                win.destroy()
                tree.focus_set()
            win.protocol("WM_DELETE_WINDOW", on_close)

        def clear_temp_style():
            self.set_temp_style_content("")
            messagebox.showinfo("清空", "临时样式已清空", parent=cw)
            tree.focus_set()
        btn_edit = ttk.Button(filter_frame, text="编辑", command=edit_temp_style, width=5)
        btn_edit.pack(side="left", padx=2, pady=2)
        btn_clear = ttk.Button(filter_frame, text="清空", command=clear_temp_style, width=5)
        btn_clear.pack(side="left", padx=2, pady=2)

        cw.lift()
        cw.focus_force()
        tree.focus_set()