import re
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox
import lxml.html

class ClassList:
    def __init__(self, root, epub_path, get_temp, set_temp, append_temp):
        self.root = root
        self.epub_path = epub_path
        self.get_temp_style_content = get_temp
        self.set_temp_style_content = set_temp
        self.append_temp_style_content = append_temp
        self.show_class_list()

    def show_class_list(self):
        cw = tk.Toplevel(self.root)
        cw.title("html内样式收集分析")
        cw.geometry(f"320x420+{self.root.winfo_x()+130}+{self.root.winfo_y()+60}")

        # 筛选输入框和按钮自适应窗口宽度
        filter_frame = ttk.Frame(cw)
        filter_frame.pack(fill="x", padx=3, pady=2)
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(filter_frame, textvariable=filter_var, width=10)
        filter_entry.pack(side="left", fill="x", expand=True)

        tree_frame = ttk.Frame(cw)
        tree_frame.pack(fill="both", expand=True, padx=3, pady=2)
        tree = ttk.Treeview(tree_frame, show="tree", selectmode="extended")
        tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=vsb.set)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        style_data, used_classes, used_spans, used_images = {}, set(), set(), set()
        css_pattern = re.compile(r'([^{]+)\{([^}]+)\}', re.DOTALL)

        try:
            with zipfile.ZipFile(self.epub_path, 'r') as archive:
                for file_name in archive.namelist():
                    # 处理css
                    if file_name.endswith('.css'):
                        with archive.open(file_name) as file:
                            clean_css = re.sub(r'/\*.*?\*/', '', file.read().decode('utf-8', 'ignore'), flags=re.DOTALL)
                            for selector, body in css_pattern.findall(clean_css):
                                formatted_content = re.sub(r';\s*', ';\n  ', body.strip())
                                for part in [s.strip() for s in selector.split(',')]:
                                    for class_name in re.findall(r'\.([\w-]+)', part):
                                        style_data.setdefault(class_name, {}).setdefault(file_name, []).append({'selector': part, 'content': formatted_content})
                    # 处理xhtml 使用 XPath 仅提取带 class 的标签
                    elif file_name.endswith(('.html', '.xhtml')):
                        with archive.open(file_name) as file:
                            html_tree = lxml.html.fromstring(file.read())
                            for element in html_tree.xpath('//*[@class]'):
                                class_list = element.get('class').split() # .split() 自动处理字符串并解决“只读首字母”问题
                                used_classes.update(class_list)
                                # 快速获取标签名并更新对应集合
                                tag_name = element.tag.rsplit('}', 1)[-1]
                                if tag_name == 'span': used_spans.update(class_list)
                                elif tag_name == 'img': used_images.update(class_list)
        except Exception as error:
            messagebox.showerror("解析失败", f"错误: {error}", parent=cw)

        # 界面填充
        categories = {'Class列表': used_classes, 'Span列表': used_spans, '图片Class列表': used_images}
        nodes = {title: (classes, tree.insert("", "end", text=title, open=True)) for title, classes in categories.items()}
        all_items = [(title, name, tree.insert(node, "end", text=name)) 
                     for title, (classes, node) in nodes.items() for name in sorted(classes)]

        def show_details(event):
            if not (item := tree.identify_row(event.y) or (sel := tree.selection() and sel[0])):
                return
            if tree.parent(item) in [nodes[k][1] for k in ['Class列表', 'Span列表', '图片Class列表']]:
                name = tree.item(item, "text")
                details = [
                    f"文件: {p}\n{r['selector']} {{\n" + 
                    "\n".join(f"  {line.strip()}" for line in r['content'].split('\n') if line.strip()) + 
                    "\n}\n\n"
                    for p, rs in style_data.get(name, {}).items() for r in rs
                ]

                win = tk.Toplevel(cw)
                win.title(name)
                win.geometry(f"350x180+{self.root.winfo_x()+160}+{self.root.winfo_y()+130}")
                
                f = ttk.Frame(win); f.pack(fill="both", expand=True, padx=5, pady=5)
                t = tk.Text(f, wrap="word", font=('Consolas', 10))
                t.insert("end", "".join(details) or f"未找到 {name} 的CSS定义")
                t.config(state="disabled")
                v = ttk.Scrollbar(f, command=t.yview); t.config(yscrollcommand=v.set)
                v.pack(side="right", fill="y"); t.pack(side="left", fill="both", expand=True)
                win.protocol("WM_DELETE_WINDOW", lambda: [win.destroy(), tree.focus_set()])
        tree.bind("<Double-1>", show_details)

        def copy_selected():
            items = tree.selection()
            details_list = []
            for i in items:
                name = tree.item(i, "text")
                if name in style_data:
                    for _, rules in style_data[name].items():  # 将 path 改为 _，表示不读取文件路径
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
                if name in style_data:
                    for _, rules in style_data[name].items():
                        for rule in rules:
                            lines = [line.strip() for line in rule['content'].split('\n') if line.strip()]
                            style_lines.append(f"{rule['selector']} {{\n" + '\n'.join(lines) + "\n}\n")
            new_content = '\n'.join(style_lines) + "\n"
            self.append_temp_style_content(new_content)
            messagebox.showinfo("写入", f"已追加 {len(items)} 个条目的详细样式到内存（临时style）", parent=cw)
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
            for group, cls, iid in all_items:
                show = False
                if cls in style_data:  # 修复 KeyError
                    for rules in style_data[cls].values():
                        for rule in rules:
                            if keyword in rule['selector'].lower() or keyword in rule['content'].lower():
                                show = True
                                break
                tree.detach(iid)
                if show:
                    tree.reattach(iid, nodes[group][1], "end")
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