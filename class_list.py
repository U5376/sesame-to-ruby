import os
import re
import zipfile
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox
from bs4 import BeautifulSoup

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
        html_pattern = re.compile(r'.*\.(x?html?)$', re.I)

        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(self.epub_path, 'r') as zf: zf.extractall(tmp)
            for root, _, files in os.walk(tmp):
                for file in files:
                    path = os.path.join(root, file)
                    rel = os.path.relpath(path, tmp)
                    if file.endswith('.css'):
                        with open(path, 'r', encoding='utf-8') as f:
                            content = re.sub(r'/\*.*?\*/', '', f.read(), flags=re.DOTALL)
                            for sel, body in css_pattern.findall(content):
                                sel = [s.strip() for s in sel.split(',')]
                                fmt = re.sub(r';\s*', ';\n  ', body.strip())
                                for s in sel:
                                    for cls in re.findall(r'\.([\w-]+)', s):
                                        style_data.setdefault(cls, {}).setdefault(rel, []).append({'selector': s, 'content': fmt})
                    elif html_pattern.match(file):
                        with open(path, 'r', encoding='utf-8') as f:
                            soup = BeautifulSoup(f.read(), 'html.parser')
                            used_classes |= {cls for tag in soup.find_all(class_=True) for cls in tag.get('class', [])}
                            used_spans   |= {cls for tag in soup.find_all('span') if tag.get('class') for cls in tag['class']}
                            used_images  |= {cls for tag in soup.find_all('img') if tag.get('class') for cls in tag['class']}
        nodes = {
            'Class列表': (used_classes, tree.insert("", "end", text="Class列表", open=True)),
            'Span列表':  (used_spans,   tree.insert("", "end", text="Span列表", open=True)),
            '图片Class列表': (used_images,  tree.insert("", "end", text="图片Class列表", open=True))
        }
        all_items = []
        for group, (classes, node) in nodes.items():
            for cls in sorted(classes):
                item_id = tree.insert(node, "end", text=cls)
                all_items.append((group, cls, item_id))

        def show_details(event):
            item = tree.selection()[0]
            parent = tree.parent(item)
            if parent in (nodes['Class列表'][1], nodes['Span列表'][1], nodes['图片Class列表'][1]):
                name = tree.item(item, "text")
                details = []
                if name in style_data:
                    for path, rules in style_data[name].items():
                        for rule in rules:
                            lines = [f"  {line.strip()}" for line in rule['content'].split('\n') if line.strip()]
                            details.append(f"文件: {path}\n{rule['selector']} {{\n" + '\n'.join(lines) + "\n}\n\n")
                win = tk.Toplevel(cw)
                win.title(name)
                win.geometry(f"350x180+{self.root.winfo_x()+160}+{self.root.winfo_y()+130}")
                frame = ttk.Frame(win)
                frame.pack(fill="both", expand=True, padx=5, pady=5)
                text = tk.Text(frame, wrap="word", font=('Consolas', 10))
                text.insert("end", "".join(details) or f"未找到 {name} 的CSS定义")
                text.config(state="disabled")
                vsb = ttk.Scrollbar(frame, command=text.yview)
                text.config(yscrollcommand=vsb.set)
                vsb.pack(side="right", fill="y")
                text.pack(side="left", fill="both", expand=True)
                def on_close():
                    win.destroy()
                    tree.focus_set()
                win.protocol("WM_DELETE_WINDOW", on_close)
        tree.bind("<Double-1>", show_details)

        def copy_selected():
            items = tree.selection()
            details_list = []
            for i in items:
                name = tree.item(i, "text")
                if name in style_data:
                    for path, rules in style_data[name].items():
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
                    for path, rules in style_data[name].items():
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
                for path, rules in style_data[cls].items():
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
            win.geometry(f"400x300+{cw.winfo_x()+60}+{cw.winfo_y()+60}")
            frame = ttk.Frame(win)
            frame.pack(fill="both", expand=True, padx=5, pady=5)
            text = tk.Text(frame, wrap="word", font=('Consolas', 10))
            current_content = self.get_temp_style_content()
            text.insert("end", current_content)
            text.pack(fill="both", expand=True)
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