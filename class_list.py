import re
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox
import lxml.html

class ClassList:
    def __init__(self, root, epub_path, get_temp, set_temp, append_temp):
        self.root, self.epub_path = root, epub_path
        self.get_temp_style_content, self.set_temp_style_content, self.append_temp_style_content = get_temp, set_temp, append_temp
        self.style_data, self.samples_data, self.counts_data = {}, {}, {}
        self.cats = {k: set() for k in ['Class列表', 'Span列表', '图片Class列表', '非P标签列表', '非P、img、body标签列表']}
        self.all_items_refs, self.n_map = [], {"": ""}
        self.show_class_list()

    def show_class_list(self):
        cw = tk.Toplevel(self.root)
        cw.title("html内样式收集分析")
        cw.geometry(f"600x480+{self.root.winfo_x()+-30}+{self.root.winfo_y()+30}")

        pw = ttk.PanedWindow(cw, orient="horizontal")
        pw.pack(fill="both", expand=True)
        
        # 左侧文件树
        lf, rf = ttk.Frame(pw, width=150), ttk.Frame(pw, width=330)
        [pw.add(f, weight=w) for f, w in [(lf, 1), (rf, 0)]]
        
        ftree = ttk.Treeview(lf, show="tree", selectmode="browse")
        ftree.pack(side="left", fill="both", expand=True)
        f_vsb = ttk.Scrollbar(lf, command=ftree.yview); f_vsb.pack(side="right", fill="y")
        ftree.config(yscrollcommand=f_vsb.set)

        filter_frame = ttk.Frame(rf); filter_frame.pack(fill="x", padx=3, pady=2)
        filter_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=filter_var).pack(side="left", fill="x", expand=True)

        tf = ttk.Frame(rf); tf.pack(fill="both", expand=True, padx=3, pady=2)
        tree = ttk.Treeview(tf, columns=("count",), show="tree headings", selectmode="extended")
        tree.heading("#0", text="类名", anchor="w"); tree.heading("count", text="总量", anchor="center")
        tree.column("#0", width=200); tree.column("count", width=50, anchor="center")
        tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tf, command=tree.yview); vsb.grid(row=0, column=1, sticky="ns")
        tree.config(yscrollcommand=vsb.set); tf.columnconfigure(0, weight=1); tf.rowconfigure(0, weight=1)
        
        ttk.Style().configure("Treeview", indent=14)
        nodes = {k: tree.insert("", "end", text=k, open=(k == 'Class列表')) for k in self.cats}
        class_to_iid = {}

        # 预览逻辑
        def preview_file(e):
            if not (sel := ftree.selection()) or not (p := ftree.item(sel[0], "tags")[0]) or p.endswith('/'): return
            try:
                with zipfile.ZipFile(self.epub_path, 'r') as z: content = z.read(p).decode('utf-8', 'ignore')
                win = tk.Toplevel(cw); win.title(p)
                win.geometry(f"600x500+{self.root.winfo_x()+60}+{self.root.winfo_y()+50}"); win.focus_force()
                txt = tk.Text(win, font=('Consolas', 10), wrap="word") 
                sv = ttk.Scrollbar(win, command=txt.yview); txt.config(yscrollcommand=sv.set)
                sv.pack(side="right", fill="y"); txt.pack(side="left", fill="both", expand=True)
                txt.insert("1.0", content); txt.config(state="disabled")
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
                                if len(self.samples_data.get(c, [])) < 10:
                                    s_raw = lxml.html.tostring(el, encoding='unicode', method='html', with_tail=False).strip()
                                    self.samples_data.setdefault(c, []).append(re.sub(r'\s+', ' ', s_raw)[:150])
                        yield

        gen = parse_gen()
        def run_step():
            try: next(gen); cw.after(1, run_step)
            except (StopIteration, Exception): pass
        run_step()

        # 显示样式详情+实例
        def show_details(event=None):
            if not (item := tree.identify_row(event.y) if event else (tree.selection() or [None])[0]) or item in nodes.values(): return
            name = tree.item(item, "text")
            rules = [f"文件: {p}\n{r['selector']} {{\n" + "\n".join(f"  {l.strip()}" for l in r['content'].split('\n') if l.strip()) + "\n}" 
                     for p, rs in self.style_data.get(name, {}).items() for r in rs]

            win = tk.Toplevel(cw); win.title(name); win.geometry(f"500x480+{self.root.winfo_x()+140}+{self.root.winfo_y()+80}"); win.focus_force()
            pw = ttk.PanedWindow(win, orient="vertical")
            pw.pack(fill="both", expand=True, padx=5, pady=5)
            for i, (txt, wt) in enumerate([("\n\n".join(rules) or f"/* 未找到 {name} */", 6), ("\n\n".join(self.samples_data.get(name, [])), 10)]):
                f = ttk.Frame(pw); pw.add(f, weight=wt)
                t = tk.Text(f, height=1, font=('Consolas', 10 if i==0 else 9), bg="#ffffff" if i==0 else "#f9f9f9", wrap="word")
                v = ttk.Scrollbar(f, command=t.yview); t.config(yscrollcommand=v.set)
                t.insert("end", txt); t.config(state="disabled")
                v.pack(side="right", fill="y"); t.pack(side="left", fill="both", expand=True)
            win.protocol("WM_DELETE_WINDOW", lambda: [win.destroy(), tree.focus_set()])
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
            win.geometry(f"400x280+{cw.winfo_x()+60}+{cw.winfo_y()+60}")
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