import os
import re
import copy
import tkinter as tk
from tkinter import ttk,font
from bs4 import BeautifulSoup
import ebooklib.epub as epub
from tkinter import filedialog, messagebox, Entry, Label, Button, END
from ebooklib import ITEM_DOCUMENT
from Image import icon_base64
import warnings


class ToolTip(object):
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event=None):
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            self.tooltip_window,
            text=self.text,
            font=("宋体", 10),
            background="#ffffe0",
            relief="solid",
            borderwidth=1
        )
        label.pack()

    def hide_tooltip(self, event=None):
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class EpubProcessor:
    def __init__(self, root):
        self.root = root
        self.regex_entries = []
        
        # 设置默认字体
        #default_font = ("Aria", 12)
        #root.option_add("*Font", default_font)
        
        # 设置窗口图标
        icon_data = icon_base64  # 图标的Base64数据
        icon_img = tk.PhotoImage(data=icon_data)
        root.iconphoto(True, icon_img)

        root.title("EPUB傍点转Ruby")

        self.class_name_var = tk.StringVar()
        self.class_name_var.set('em-sesame')  # 默认值

        # 添加功能启用标志
        self.modify_html_enabled = tk.BooleanVar(value=True)
        self.process_ruby_enabled = tk.BooleanVar(value=True)
        self.process_images_enabled = tk.BooleanVar(value=True)

        class_name_entry = Entry(root, textvariable=self.class_name_var, font=("宋体", 12))
        class_name_label = Label(root, text='傍点class名', font=("宋体", 12))

        class_name_label.pack()
        class_name_entry.pack()

        open_button = Button(root, text='选择epub文件', command=self.open_file_dialog, font=("宋体", 12))
        open_button.pack()

        add_regex_button = Button(root, text='添加正则匹配', command=self.add_regex_entry, font=("宋体", 12))
        add_regex_button.pack()
        
        # 添加说明按钮
        self.initialize_tooltip_content()        

        # 添加勾选框以启用/禁用功能
        modify_html_check_button = tk.Checkbutton(root, text="傍点转换ruby格式", variable=self.modify_html_enabled, onvalue=True, offvalue=False, font=("宋体", 12))
        process_ruby_check_button = tk.Checkbutton(root, text="Ruby格式规格化", variable=self.process_ruby_enabled, onvalue=True, offvalue=False, font=("宋体", 12))
        process_images_check_button = tk.Checkbutton(root, text="图片标签多看交互规格化", variable=self.process_images_enabled, onvalue=True, offvalue=False, font=("宋体", 12))

        modify_html_check_button.pack(anchor='w')
        process_ruby_check_button.pack(anchor='w')
        process_images_check_button.pack(anchor='w')  # 左对齐放置

        self.add_default_regex_rules()

    def initialize_tooltip_content(self):
        # 在这里填写您需要展示的说明内容
        self.description = """
        基础功能:
        1.傍点class名称需要确认
        2.图片处理可能会不正确,图片处理是在正则匹配之后执行,
        请确认处理后的epub.日语epub有些会用图片替用文字标点
        导致脚本出问题,特别是把图片代替文字放在ruby内会直删
        建议查看epub内的图片对照内容.手动编辑掉这些奇葩玩意
        3.ruby处理是删掉了多余的rb代码并且合并多个rt规格化不让其造成
        后面ruby兼容正则变换的混乱
        """
        self.show_tooltip_button = Button(self.root, text='说明', command=self.show_help_description, font=("宋体", 12))
        self.show_tooltip_button.pack(anchor='w', padx=5)

    def show_help_description(self):
        messagebox.showinfo(title="说明", message=self.description)

    def open_file_dialog(self):
        path = filedialog.askopenfilename(filetypes=[('EPUB文件', '*.epub')])
        if path:
            output_filename = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[('EPUB文件', '*.epub')])
            if output_filename:
                self.process_epub(path, output_filename)
                messagebox.showinfo("处理完成", f"输出文件已保存到：{output_filename}")

    def process_epub(self, path, output_filename):
        class_name = self.class_name_var.get()
        regex_pairs = [(re.compile(entry[0].get()), entry[1].get()) for entry in self.regex_entries]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=UserWarning)
            book = epub.read_epub(path)
        for item in book.get_items():
            if item.get_type() == ITEM_DOCUMENT:
                content = item.get_content().decode('utf8')
                soup = BeautifulSoup(content, 'html.parser')

                if self.process_ruby_enabled.get():
                    self.process_ruby(soup)

                content = str(soup)
                content = self.apply_regex_rules(content, regex_pairs)

                if self.process_images_enabled.get():
                    soup = BeautifulSoup(content, 'html.parser')
                    self.post_process_images(soup)
                    content = str(soup)

                if self.modify_html_enabled.get():
                    content = self.modify_html(content, class_name)

                item.content = content.encode('utf8')
                
        for item in book.get_items():
            if item.get_name() == "content.opf":
                content = item.get_content().decode('utf8')

                # 删除<spine>标签的page-progression-direction属性
                content = re.sub(r'(<spine .*)page-progression-direction="rtl"(.*>)', r'\1\2', content)
            
                # 删除<itemref idref="nav.xhtml" linear="no"/>行
                content = re.sub(r'<itemref idref="nav.xhtml" linear="no"/>', '', content)

                item.content = content.encode('utf8')

        output_path = os.path.join(os.path.dirname(path), output_filename)
        epub.write_epub(output_path, book)


    def process_ruby(self, soup):
        ruby_tags = soup.find_all('ruby')  # 查找所有的 <ruby> 标签
        for ruby_tag in ruby_tags:
            img_tags = ruby_tag.find_all('img')  # 查找所有的 <img> 标签
            for img_tag in img_tags:
                copy_tag = copy.copy(img_tag)  # 复制图片标签
                ruby_tag.insert_before(copy_tag)  # 将复制的图片标签插入到 ruby 标签之前
                img_tag.extract()  # 删除原始图片标签

            rt_tags = ruby_tag.find_all('rt')  # 查找所有的 <rt> 标签

            original_content = ruby_tag.get_text()  # 获取原始内容（包含<rb>标签.不包含 <rt> 标签）

            merged_content = ''.join(rt_tag.string.strip() if rt_tag.string else '' for rt_tag in rt_tags)  # 合并 <rt> 标签内的内容

            for rt_tag in rt_tags:
                rt_tag.extract()  # 删除所有的 <rt> 标签

            rt_tag = soup.new_tag('rt')  # 创建一个新的 <rt> 标签
            rt_tag.string = merged_content  # 设置新的 <rt> 标签的内容

            new_ruby_tag = soup.new_tag('ruby')  # 创建一个新的 <ruby> 标签
            new_ruby_tag.string = original_content.replace('\n', '')  # 设置新的 <ruby> 标签的内容，并删除换行符
            new_ruby_tag.append(rt_tag)  # 将新的 <rt> 标签添加到新的 <ruby> 标签中

            ruby_tag.replace_with(new_ruby_tag)  # 用新的 <ruby> 标签替换原始的 <ruby> 标签
            # 用新的<ruby>标签替换原始的<ruby>标签 其中并不包含<rb>标签         

    def post_process_images(self, soup):
        for div in soup.find_all('div'):
            img_tag = div.find('img', alt=True, style=True)
            if img_tag and 'width' in img_tag['style']:
                div.attrs['class'] = 'illus duokan-image-single'
                del img_tag['style']

        for p in soup.find_all('p'):
            img_tags = p.find_all('img', alt=True)
            for img_tag in img_tags:
                if 'gaiji' not in img_tag.get('class', []):
                    new_div = soup.new_tag('div', attrs={'class': 'illus duokan-image-single'})
                    new_div.append(img_tag.extract())
                    p.insert_before(new_div)

        for svg in soup.find_all('svg'):
            img_tag = svg.find('image')
            if img_tag and img_tag.get('xlink:href', '').startswith('../Images/'):
                new_div = soup.new_tag('div', attrs={'class': 'illus duokan-image-single'})
                new_img = soup.new_tag('img', src=img_tag['xlink:href'], alt='', attrs={'class': 'fit'})
                new_div.append(new_img)
                svg.replace_with(new_div) 

    def modify_html(self, html, class_name):
        soup = BeautifulSoup(html, 'html.parser')

        for span in soup.find_all('span', class_=class_name):
            ruby = soup.new_tag('ruby')
            
            # 确保 span.string 不为 None
            if span.string:
                for char in span.string:
                    ruby.append(soup.new_string(char))  # 添加字符
                    rt_tag = soup.new_tag('rt')
                    rt_tag.append(soup.new_string("・"))  # 添加 rt 标签
                    ruby.append(rt_tag)
            else:
                # 如果 span.string 为 None, 这里可以添加额外处理逻辑（例如跳过或使用默认值）
                ruby.append(soup.new_string(''))  # 如果为空，直接添加一个空字符串
            
            span.replace_with(ruby)  # 替换原来的 span 标签

        return str(soup)

    def apply_regex_rules(self, content, regex_pairs):
        for (pattern, replacement) in regex_pairs:
            content = pattern.sub(replacement, content)
        return content

    def add_regex_entry(self, regex="", replace="", description="", tooltip=None):
        frame = tk.Frame(self.root)
        frame.pack()

        regex_entry = Entry(frame)
        regex_entry.insert(END, regex)
        replace_entry = Entry(frame)
        replace_entry.insert(END, replace)
        delete_button = Button(frame, text="删除", command=lambda f=frame: self.delete_regex_entry(f), font=("宋体", 12))

        Label(frame, text=f"", font=("宋体", 12)).pack(side=tk.LEFT)
        regex_entry.pack(side=tk.LEFT)
        Label(frame, text="", font=("宋体", 12)).pack(side=tk.LEFT)
        replace_entry.pack(side=tk.LEFT)

        if tooltip is not None:
            ToolTip(regex_entry, text=tooltip)

        delete_button.pack(side=tk.LEFT)

        self.regex_entries.append((regex_entry, replace_entry, frame))

    def delete_regex_entry(self, frame):
        frame.destroy()
        self.regex_entries = [(e1, e2, f) for (e1, e2, f) in self.regex_entries if f.winfo_exists()]

    def add_default_regex_rules(self):
        self.add_regex_entry(r"<body\s.*?>", "<body>", description="", tooltip="清除body样式")
        self.add_regex_entry(r"<div\s.*?>", "<div>", description="", tooltip="清除div样式")
        self.add_regex_entry(r"<p\s.*?>", "<p>", description="", tooltip="清除p样式")
        self.add_regex_entry(r'<span class="tcy">(.*?)</span>', r'\1', description="", tooltip="清除tcy标签")
        self.add_regex_entry(r'(<ruby>.*?<rt>)(.*?)(<\/rt><\/ruby>)', r'\1\2\3《\2》', description="", tooltip="ruby兼容增加《》")


if __name__ == "__main__":
    root = tk.Tk()
    processor = EpubProcessor(root)
    root.mainloop()
