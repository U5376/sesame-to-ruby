import os
import re
import sys
import copy
import zipfile
import tempfile
import subprocess
import xml.etree.ElementTree as ET
import tkinter as tk
from bs4 import BeautifulSoup
from epub_ncx_generator import EpubNCXGenerator
from regex_manager import RegexManager
from tkinter import ttk, filedialog, messagebox, Entry, Label, Button, END
from Image import icon_base64
from tooltip import ToolTip
import warnings
import shutil
import lxml.etree
from urllib.parse import unquote
from loguru import logger
from pathlib import Path
import configparser

class EpubProcessor:
    def __init__(self, root):
        self.root = root
        self.regex_entries = []
        self.excluded_toc_entries = []
        FONT = ("宋体", 12)

        # 设置窗口图标
        icon_data = icon_base64
        icon_img = tk.PhotoImage(data=icon_data)
        root.iconphoto(True, icon_img)
        root.title("EPUB傍点转Ruby")

        # 主容器
        main_frame = tk.Frame(root)
        main_frame.pack(padx=5, pady=5)

        # 按钮配置：(文本, 命令, grid(row, col), tooltip)
        btn_cfgs = [
            ('读取epub', self.open_file_dialog, (0, 0), "加载单个epub文件"),
            ('开始转换', self.start_conversion, (0, 1), "转换加载的单个epub文件"),
            ('批量转换', self.batch_convert_epubs, (0, 2), "选择多个epub后立即批量转换\n在epub所在目录下创建output文件夹\n转换后的epub放入output，文件名为原epub名"),
            ('class列表', self.show_class_list, (1, 0), "epub内所使用的class列表\nspan列表\n图片class列表"),
            ('排除合并', self.show_exclude_dialog, (1, 1), "章节合并功能排除选定的目录条目"),
            ('重置设置', self.reset_app_settings, (1, 2), "重置所有设置为默认状态"),
        ]
        for text, cmd, (row, col), tip in btn_cfgs:
            btn = tk.Button(main_frame, text=text, command=cmd, font=FONT)
            btn.grid(row=row, column=col, padx=5, pady=2, sticky='w')
            ToolTip(btn, text=tip)

        # 用于自动收集所有设置变量
        self._settings_vars_dict = {}

        # 傍点转ruby设置
        self.modify_html_enabled = tk.BooleanVar(value=True)
        self._settings_vars_dict['modify_html_enabled'] = self.modify_html_enabled
        self.class_name_var = tk.StringVar(value='em-sesame|em-dot')
        self._settings_vars_dict['class_name_var'] = self.class_name_var
        f_ruby = tk.Frame(root)
        ruby_check = tk.Checkbutton(f_ruby, text="傍点转ruby", variable=self.modify_html_enabled, font=FONT)
        ruby_check.pack(side=tk.LEFT)
        ToolTip(ruby_check, text="需要检查class")
        ruby_entry = tk.Entry(f_ruby, textvariable=self.class_name_var, width=25, font=FONT)
        ruby_entry.pack(side=tk.LEFT)
        ToolTip(ruby_entry, text="一般class名:\nem-sesame|em-dot")
        f_ruby.pack(fill=tk.X, anchor='w')

        flags_with_tooltips = [
            ("process_ruby_enabled", "Ruby格式规格化", "格式奇怪跟包含gaiji图片的标签规格化兼容处理"),
            ("process_images_enabled", "图片标签多看交互规格化", "将奇怪的图片标签全部规格化成多看格式\n排除span跟gaiji"),
            ("merge_xhtml_enabled", "Xhtml章节间合并", "根据目录合并章节间文件"),
            ("delete_style_enabled", "删除自带Style并添加自定义样式表", "清理原有样式跟opf竖排属性\n添加css文件及更新引用\n考虑规格化头部信息"),
            ("generate_ncx_enabled", "生成ncx并更新opf", ""),
            ("convert_epub_version_enabled", "转Epub2.0并删除nav.xhtml", "将EPUB版本转换为2.0\n移除nav.xhtml\n生成cover声明")
        ]
        for var_name, text, tip in flags_with_tooltips:
            var = tk.BooleanVar(value=True)
            setattr(self, var_name, var)
            self._settings_vars_dict[var_name] = var
            if var_name == "merge_xhtml_enabled":
                f_merge = tk.Frame(root)
                cb = tk.Checkbutton(f_merge, text=text, variable=var, onvalue=True, offvalue=False, font=FONT)
                cb.pack(side=tk.LEFT)
                ToolTip(cb, tip)
                # 空行下拉框
                for var_name, tip_text in [
                    ('merge_remove_blank_lines_var', "删除指定的空行数量"),
                    ('merge_limit_blank_lines_var', "限制连续空行的行数")]:
                    var = tk.StringVar(value='-')
                    self._settings_vars_dict[var_name] = var
                    combo = ttk.Combobox(f_merge, textvariable=var, width=2, state="readonly", values=['-'] + [str(i) for i in range(1, 11)], font=FONT)
                    combo.pack(side=tk.LEFT, padx=(5, 0))
                    ToolTip(combo, text=tip_text)
                f_merge.pack(anchor='w')
            else:
                cb = tk.Checkbutton(root, text=text, variable=var, onvalue=True, offvalue=False, font=FONT)
                cb.pack(anchor='w')
                ToolTip(cb, tip)

        # 图片转换设置
        self.convert_images_var = tk.BooleanVar(value=True)
        self._settings_vars_dict['convert_images_var'] = self.convert_images_var
        self.image_params_var = tk.StringVar(value="-f webp -q80 -H 300 -s 1.4 -w7")
        self._settings_vars_dict['image_params_var'] = self.image_params_var
        f_image = tk.Frame(root)
        image_check = tk.Checkbutton(f_image, text="转换图片", variable=self.convert_images_var, font=FONT)
        image_check.pack(side=tk.LEFT)
        image_entry = tk.Entry(f_image, textvariable=self.image_params_var, width=30, font=FONT)
        image_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        ToolTip(image_entry, text="-f 可选webp,jpg,png\n-q 质量\n-H -W 高宽按比例缩小,小图不放大\n-s 锐化 默认1.0不处理\n小于1.0糊化 大于1.0锐化 锐化建议范围0.5-2.0\n-w 线程数 默认2\n-m WebP压缩等级 1-6 默认6 越大越慢越优")
        f_image.pack(fill=tk.X, anchor='w')

        base_dir = Path(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.argv[0]))))
        self.config_file = base_dir / "config.ini"
        self.load_app_settings()

        self.regex_manager = RegexManager(root, config_path=self.config_file)
        self._bind_regex_save_button()

    def _bind_regex_save_button(self):
        """将正则管理器的保存按钮绑定为主程序保存方法"""
        for child in self.regex_manager.frame.winfo_children():
            if isinstance(child, tk.Frame):
                btns = [w for w in child.winfo_children() if isinstance(w, tk.Button)]
                for btn in btns:
                    if btn.cget("text") == "保存设置":
                        btn.config(command=self.save_app_settings)

    def open_file_dialog(self):
        self.epub_path = filedialog.askopenfilename(filetypes=[('EPUB文件', '*.epub')])
        logger.info(f"载入epub: {self.epub_path}")

    def start_conversion(self):
        if not hasattr(self, 'epub_path'):
            messagebox.showwarning("警告", "请先选择EPUB文件")
            return
        output_filename = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[('EPUB文件', '*.epub')])
        if output_filename:
            self.process_epub(output_filename)

    def batch_convert_epubs(self):
        epub_paths = filedialog.askopenfilenames(filetypes=[('EPUB文件', '*.epub')])
        if not epub_paths:
            return
        for epub_path in epub_paths:
            try:
                epub_path = Path(epub_path)
                output_dir = epub_path.parent / "output"
                output_dir.mkdir(exist_ok=True)
                output_filename = output_dir / epub_path.name
                self.epub_path = str(epub_path)
                self.process_epub(str(output_filename))
                logger.info(f"批量转换完成: {output_filename}")
            except Exception as e:
                logger.error(f"批量转换失败: {epub_path} - {e}")

    def process_epub(self, output_filename):
        logger.info(f"开始处理epub文件: {self.epub_path}")
        class_name = self.class_name_var.get()

        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"解压临时目录: {temp_dir}")
            with zipfile.ZipFile(self.epub_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            # 解析 container.xml，找到 .opf 文件路径
            opf_full_path = self._get_opf_path(temp_dir)
            logger.debug(f"OPF文件路径: {opf_full_path}")

            # 图片转换
            if self.convert_images_var.get():
                self.convert_epub_images(temp_dir)

            # 删除自带样式并添加自定义样式表并更新opf跟页面引用
            if self.delete_style_enabled.get():
                self.process_opf_and_styles(temp_dir)

            opf_path = self._get_opf_path(temp_dir)
            # 生成ncx并更新opf
            if self.generate_ncx_enabled.get():
                success, msg = EpubNCXGenerator.generate_ncx(opf_path)
                if not success:
                    logger.warning(f"NCX生成警告: {msg}")

            # 转换epub版本并删除nav
            if self.convert_epub_version_enabled.get():
                success, msg = EpubNCXGenerator.convert_to_epub2(opf_path)
                if not success:
                    logger.warning(f"版本转换警告: {msg}")               

            # 遍历所有文件并处理
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = Path(root) / file
                    if file.endswith('.xhtml') or file.endswith('.html'):
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        soup = BeautifulSoup(content, 'html.parser')

                        if self.process_ruby_enabled.get():
                            self.process_ruby(soup)
                        content = str(soup)
                        content = self.regex_manager.apply_rules(content)

                        if self.process_images_enabled.get():
                            soup = BeautifulSoup(content, 'html.parser')
                            self.post_process_images(soup)
                            content = str(soup)

                        if self.modify_html_enabled.get():
                            content = self.modify_html(content, class_name)

                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
            logger.info("Ruby标签规格化 √")
            logger.info("正则替换 √")
            logger.info("图片标签规格化 √")
            logger.info("傍点转换ruby格式 √")

            # html章节间合并
            if self.merge_xhtml_enabled.get():
                self.merge_xhtml_files(temp_dir)

            # 空行处理
            self.process_blank_lines(temp_dir)

            # 重新打包 EPUB 文件
            with zipfile.ZipFile(output_filename, "w", zipfile.ZIP_DEFLATED) as zip_ref:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = str(file_path.relative_to(temp_dir))
                        zip_ref.write(file_path, arcname)
            logger.info(f"EPUB文件处理完成，保存到: {output_filename}")

    def process_opf_and_styles(self, temp_dir):
        temp_dir = Path(temp_dir)
        opf_path = self._get_opf_path(temp_dir)
        opf_soup = BeautifulSoup(opf_path.read_text(encoding='utf-8'), 'xml')
        # 1. 删除 page-progression-direction 属性
        if (spine_tag := opf_soup.find('spine')) and 'page-progression-direction' in spine_tag.attrs:
            del spine_tag.attrs['page-progression-direction']
        # 2. 清理 OPF 文件中的 CSS 引用
        for item in opf_soup.find_all('item', attrs={'media-type': 'text/css'}):
            item.decompose()
        # 3. 添加自定义样式表到 OPF 清单
        opf_dir = opf_path.parent
        css_target_dir = opf_dir / 'css'
        os.makedirs(css_target_dir, exist_ok=True)
        logger.debug(f"确保 CSS 目标目录存在: {css_target_dir}")
        if manifest_tag := opf_soup.find('manifest'):
            css_rel_path = (css_target_dir / 'style.css').relative_to(opf_dir).as_posix()
            new_item = opf_soup.new_tag('item', href=css_rel_path, id='style-css', **{'media-type': 'text/css'})
            manifest_tag.append(new_item)
            logger.debug(f"已添加自定义样式表引用到 OPF 清单: {css_rel_path}")
        opf_path.write_text(str(opf_soup), encoding='utf-8')
        # 4. 删除所有原 CSS 文件
        deleted = 0
        for css_file in temp_dir.rglob('*.css'):
            css_file.unlink()
            deleted += 1
        logger.debug(f"已删除 {deleted} 个原 CSS 文件")
        # 5. 添加自定义 style.css 文件
        base_dir = Path(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.argv[0]))))
        custom_css_src = base_dir / 'style.css'
        if custom_css_src.exists():
            shutil.copy2(custom_css_src, css_target_dir / 'style.css')
            logger.info("添加style.css 完成")
        else:
            logger.warning(f"自定义 style.css 文件不存在: {custom_css_src}")
        # 6. 在所有 XHTML 文件中添加样式表链接并清理样式
        xhtml_count = 0
        for file_path in temp_dir.rglob("*"):
            if file_path.suffix.lower() not in ('.xhtml', '.html'):
                continue
            soup = BeautifulSoup(file_path.read_text(encoding='utf-8'), 'html.parser')
            for tag in soup.select('style, link[rel="stylesheet"]'):
                tag.decompose()
            css_rel_xhtml = os.path.relpath(css_target_dir / 'style.css', file_path.parent).replace('\\', '/')
            if head := soup.find('head'):
                head.append(soup.new_tag('link', rel='stylesheet', type='text/css', href=css_rel_xhtml))
            file_path.write_text(str(soup), encoding='utf-8')
            xhtml_count += 1
        logger.info(f"已更新 {xhtml_count} 个 XHTML 样式表链接")

    def merge_xhtml_files(self, temp_dir):
        logger.info("章节间Xhtml合并(基于目录 )")
        # 将路径转换为Path对象
        temp_dir = Path(temp_dir)
        opf_path = self._get_opf_path(temp_dir)

        # 解析OPF文件
        opf_content = opf_path.read_text(encoding='utf-8')
        opf_soup = BeautifulSoup(opf_content, 'xml')
        spine = opf_soup.spine
        if not spine:
            raise ValueError("OPF文件中缺少spine定义")

        # 获取所有spine文件路径
        spine_files = []
        opf_dir = opf_path.parent
        for itemref in spine.find_all('itemref'):
            item = opf_soup.find('item', id=itemref['idref'])
            if item and item.get('media-type') == 'application/xhtml+xml':
                href = item['href']
                if href.lower().endswith('nav.xhtml'): continue
                # 规范化路径：处理相对路径和URL编码
                norm_path = (opf_dir / href).resolve()
                spine_files.append(norm_path)
        # 解析目录结构
        toc_entries = self._parse_toc(opf_soup, opf_path)
        if not toc_entries:
            logger.warning("未找到有效目录，跳过合并")
            return

        logger.debug(f"Spine文件列表: {spine_files}")
        logger.debug(f"目录条目: {toc_entries}")

        for i, entry in enumerate(toc_entries):
            # 跳过用户选择不合并的目录条目
            if entry['href'] in self.excluded_toc_entries:
                logger.debug(f"跳过排除的目录条目: {entry['href']}")
                continue
            entry_href = entry['href'].split('#')[0]
            entry_file = opf_dir / entry_href
            try:
                start_idx = spine_files.index(entry_file)
            except ValueError:
                logger.warning(f"目录条目文件未在spine中找到: {entry_file}")
                continue

            # 计算合并范围
            if i + 1 < len(toc_entries):
                next_entry_href = toc_entries[i+1]['href'].split('#')[0]
                next_entry_file = opf_dir / next_entry_href
                try:
                    end_idx = spine_files.index(next_entry_file)
                except ValueError:
                    end_idx = len(spine_files)
            else:
                end_idx = len(spine_files)

            # 输出简洁的合并日志
            main_internal = spine_files[start_idx].relative_to(temp_dir).as_posix()
            merged_internal = [f.relative_to(temp_dir).as_posix() for f in spine_files[start_idx+1:end_idx]]
            logger.debug(f"合并于: {main_internal} 已被合并: {merged_internal}")

            # 读取主文件
            main_file = spine_files[start_idx]
            main_content = main_file.read_text(encoding='utf-8')
            main_soup = BeautifulSoup(main_content, 'html.parser')
            # 合并内容
            for merge_path in spine_files[start_idx+1:end_idx]:
                merge_content = merge_path.read_text(encoding='utf-8')
                merge_soup = BeautifulSoup(merge_content, 'html.parser')
                # 转移<body>内容
                if main_soup.body and merge_soup.body:
                    # 添加双br标签夹hr标签隔开
                    def add_separator(soup, parent):
                        for tag in ['p', 'hr', 'p']:
                            element = soup.new_tag(tag)
                            if tag == 'p':
                                element.append(soup.new_tag('br'))
                            parent.extend([element, '\n'])
                    add_separator(main_soup, main_soup.body)
                    # 复制合并文件的内容
                    for child in merge_soup.body.children:
                        if child.name == 'script':  # 跳过脚本标签
                            continue
                        main_soup.body.append(copy.copy(child))
                # 删除已合并文件并从OPF中移除引用
                merge_path.unlink()
                merge_href = merge_path.relative_to(opf_dir).as_posix()
                item = opf_soup.find('item', href=merge_href)
                if item:
                    item_id = item['id']
                    # 从spine中移除itemref
                    for itemref in spine.find_all('itemref', idref=item_id):
                        itemref.decompose()
                    # 从manifest中移除item
                    item.decompose()
            # 保存合并后的文件
            main_file.write_text(str(main_soup), encoding='utf-8')
        # 更新OPF文件
        opf_path.write_text(str(opf_soup), encoding='utf-8')
        logger.info("章节间Xhtml合并 完成")

    def process_blank_lines(self, temp_dir):
        """删除空行数量限制连续空行"""
        remove_blank = self._settings_vars_dict['merge_remove_blank_lines_var'].get()
        limit_blank = self._settings_vars_dict['merge_limit_blank_lines_var'].get()
        if remove_blank == '-' and limit_blank == '-':
            return
        temp_dir = Path(temp_dir)
        for file_path in temp_dir.rglob("*"):
            if file_path.suffix.lower() not in ('.xhtml', '.html'):
                continue
            soup = BeautifulSoup(file_path.read_text(encoding='utf-8'), 'html.parser')
            def is_blank_tag(tag):
                if tag.name == 'br': return True
                if tag.name == 'p':
                    children = [c for c in tag.children if isinstance(c, (str, type(tag)))]
                    if len(children) == 1 and getattr(children[0], 'name', None) == 'br': return True
                    if not tag.get_text(strip=True) and all((getattr(c, 'name', None) == 'br' or (isinstance(c, str) and not c.strip())) for c in tag.contents): return True
                    return False
                if tag.name == 'div':
                    for c in tag.contents:
                        if isinstance(c, str) and c.strip(): return False
                        if hasattr(c, 'name'):
                            if c.name == 'br': continue
                            if c.name == 'p' and is_blank_tag(c): continue
                            return False
                    return True
                return False
            def flatten_nodes(parent):
                for node in parent.children:
                    if isinstance(node, str):
                        if not node.strip(): continue
                        yield node
                    elif node.name in ['br', 'p', 'div']:
                        if node.name == 'div': yield from flatten_nodes(node)
                        else: yield node
                    else: yield node
            def group_blanks(nodes):
                groups, group = [], []
                for node in nodes:
                    if isinstance(node, str):
                        if not node.strip(): continue
                        if group: groups.append(group); group = []
                        continue
                    if hasattr(node, 'name') and is_blank_tag(node): group.append(node)
                    else:
                        if group: groups.append(group); group = []
                if group: groups.append(group)
                return groups
            flat_nodes = list(flatten_nodes(soup.body if soup.body else soup))
            blank_groups = group_blanks(flat_nodes)
            # 先执行空行删除，再执行空行限制
            if remove_blank != '-':
                to_delete = int(remove_blank)
                for group in blank_groups:
                    for t in group[:to_delete]: t.decompose()
                blank_groups = group_blanks(list(flatten_nodes(soup.body if soup.body else soup)))
            if limit_blank != '-':
                limit = int(limit_blank)
                for group in blank_groups:
                    for t in group[limit:]: t.decompose()
            file_path.write_text(str(soup), encoding='utf-8')

    def _get_opf_path(self, temp_dir):
        """解析container.xml获取opf文件路径"""
        container_path = Path(temp_dir) / 'META-INF' / 'container.xml'
        with container_path.open('r', encoding='utf-8') as f:
            container_content = f.read()
        root = ET.fromstring(container_content)
        opf_path = None
        for rootfile in root.findall('.//{*}rootfile'):
            opf_path = rootfile.get('full-path')
            if opf_path:
                break
        if not opf_path:
            raise ValueError("未找到 .opf 文件路径")
        return Path(temp_dir) / opf_path

    def _parse_toc(self, opf_soup, opf_path):
        """解析目录结构，兼容EPUB 2.0(NCX)和EPUB 3.0(NAV)"""
        # 尝试解析EPUB 2.0 NCX格式
        ncx_item = opf_soup.find('item', attrs={"media-type": "application/x-dtbncx+xml"})
        if ncx_item:
            ncx_path = (opf_path.parent / ncx_item['href']).resolve()
            if ncx_path.exists():
                with ncx_path.open('r', encoding='utf-8') as f:
                    ncx_soup = BeautifulSoup(f.read(), 'xml')
                if nav_map := ncx_soup.find('navMap'):
                    return [{
                        'title': nav_point.find('navLabel').text.strip(),
                        'href': nav_point.find('content')['src']
                    } for nav_point in nav_map.find_all('navPoint')]

        # 尝试解析EPUB 3.0 NAV格式
        nav_item = opf_soup.find('item', properties='nav')
        if nav_item:
            nav_path = (opf_path.parent / nav_item['href']).resolve()
            if nav_path.exists():
                with nav_path.open('r', encoding='utf-8') as f:
                    nav_soup = BeautifulSoup(f.read(), 'html.parser')
                nav_tag = (
                    nav_soup.find('nav', attrs={'epub:type': 'toc'}) or 
                    nav_soup.find('nav', attrs={'role': 'doc-toc'}) or
                    nav_soup.find('nav', id='toc')
                )
                if nav_tag:
                    return [{
                        'title': a.text.strip(),
                        'href': a['href'].split('#')[0]
                    } for a in nav_tag.find_all('a', href=True)]
        return []

    def convert_epub_images(self, temp_dir):
        """集成图片转换、清理旧文件、更新引用"""
        logger.info("开始图片转换流程")
        if not self.convert_images_var.get():
            return
        try:
            # ===== 1. 配置初始化 =====
            logger.debug("初始化图片转换配置")
            media_map = {'webp':'image/webp', 'png':'image/png', 
                        'jpg':'image/jpeg', 'jpeg':'image/jpeg'}
            # 从参数解析输出格式
            output_format = 'webp'
            params = self.image_params_var.get().split()
            if '-f' in params:
                try:
                    output_format = params[params.index('-f') + 1].lower()
                except (IndexError, ValueError):
                    logger.warning("参数解析失败，使用默认格式webp")
            # ===== 2. 收集原始图片文件 =====
            original_images = []
            temp_dir_path = Path(temp_dir)
            for file in temp_dir_path.rglob('*'):
                if file.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                    if file.exists():  # 二次验证文件存在
                        original_images.append(str(file))
                        logger.debug(f"[扫描] 发现图片文件: {file.relative_to(temp_dir_path)}")
            if not original_images:
                logger.warning("未找到需要转换的图片，跳过此流程")
                return
            # ===== 3. 生成文件名映射 =====
            image_mapping = {}
            for old_path in original_images:
                old_file = Path(old_path)
                new_name = f"{old_file.stem}.{output_format}"
                image_mapping[old_file.name] = new_name
                logger.debug(f"[映射] {old_file.name} → {new_name}")
            # ===== 4. 执行图片转换 =====
            base_dir = Path(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.argv[0]))))
            converter_path = base_dir / "image_converter.exe"
            if not converter_path.exists():
                raise FileNotFoundError("图片转换器 image_converter.exe 未找到")
            # 生成绝对路径列表文件
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as list_file:
                list_file.write('\n'.join(original_images))
                list_path = list_file.name
                logger.debug(f"[转换] 生成临时列表文件: {list_path}")
            try:
                # 执行转换命令
                cmd = [str(converter_path), "-i", f"@{list_path}", *params]
                logger.debug("[转换] 执行命令: " + ' '.join(cmd))
                result = subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, check=True)
                logger.debug("[转换] 输出日志:\n" + result.stdout)
                match = re.search(r"成功\s*(\d+)/(\d+)", result.stdout)
                success, total = match.groups() if match else ("0", "0")
                logger.success(f"图片转换成功: {success}/{total}")
            except subprocess.CalledProcessError as e:
                logger.error("[错误] 转换失败:\n" + e.stderr)
                raise
            finally:
                os.remove(list_path)
                logger.debug(f"[清理] 已删除临时文件: {list_path}")
            # ===== 5. 清理旧图片文件 =====
            deleted_files = 0
            for old_path in original_images:
                old_file = Path(old_path)
                old_ext = old_file.suffix.lower()[1:]
                if old_ext == output_format:
                    logger.debug(f"[跳过] 格式相同不清理: {old_file.relative_to(temp_dir_path)}")
                    continue
                new_path = old_file.with_name(image_mapping[old_file.name])
                if new_path.exists():
                    try:
                        old_file.unlink()
                        deleted_files += 1
                        logger.debug(f"[清理] 已删除: {old_file.relative_to(temp_dir_path)}")
                    except Exception as e:
                        logger.warning(f"[警告] 删除失败 {old_path}: {str(e)}")
                else:
                    logger.error(f"[错误] 新文件未生成: {str(new_path.relative_to(temp_dir_path))}")
            logger.info(f"共清理 {deleted_files}/{len(original_images)} 个旧图片文件")
            # ===== 6. 更新html内图片引用 =====
            updated_refs = 0
            for file in temp_dir_path.rglob('*'):
                if file.suffix.lower() not in ('.xhtml', '.html'):
                    continue
                try:
                    with file.open('r+', encoding='utf-8') as f:
                        content = f.read()
                        original_content = content
                        for old, new in image_mapping.items():
                            if old in content:
                                updated_refs += content.count(old)
                                content = content.replace(old, new)
                        if content != original_content:
                            f.seek(0)
                            f.write(content)
                            f.truncate()
                            logger.debug(f"[更新图片路径: {file.relative_to(temp_dir_path)}")
                except UnicodeDecodeError:
                    logger.warning(f"[警告] 跳过二进制文件: {file}")
                except Exception as e:
                    logger.error(f"[错误] 处理文件失败 {file}: {str(e)}")
            logger.info(f"共更新 {updated_refs} 个图片路径引用")
            # ===== 7. 强制更新OPF媒体类型 =====
            logger.info("更新图片OPF清单")
            try:
                # 定位OPF文件
                opf_path = self._get_opf_path(temp_dir_path)
                logger.debug(f"[OPF] 定位到主文档: {opf_path.relative_to(temp_dir_path)}")
                # 解析并修改OPF
                with open(opf_path, 'r+', encoding='utf-8') as f:
                    soup = BeautifulSoup(f.read(), 'xml')
                    modified = False
                    for item in soup.find_all('item'):
                        href = item.get('href', '')
                        if not href:
                            continue
                        # 规范化路径处理
                        decoded_href = unquote(href)
                        normalized_href = Path(decoded_href).resolve()
                        file_name = normalized_href.name
                        ext = normalized_href.suffix[1:].lower()
                        # 检查是否为图片项
                        if ext not in media_map:
                            continue
                        target_ext = output_format
                        changes = []
                        if file_name in image_mapping:
                            new_name = image_mapping[file_name]
                            item['href'] = href.replace(file_name, new_name)
                            changes.append(f"路径: {file_name} → {new_name}")
                        new_type = media_map[target_ext]
                        if item.get('media-type') != new_type:
                            old_type = item.get('media-type', '未知')
                            item['media-type'] = new_type
                            changes.append(f"类型: {old_type} → {new_type}")
                            modified = True
                        if changes:
                            logger.debug(f"[OPF] 更新: {' | '.join(changes)}")
                    if modified:
                        f.seek(0)
                        f.write(str(soup))
                        f.truncate()
                        logger.success("更新图片OPF清单 完成")
                    else:
                        logger.info("[OPF] 无需修改")
            except Exception as e:
                logger.error(f"[严重错误] OPF处理失败: {str(e)}")
                raise
        except Exception as e:
            logger.error(f"流程异常终止: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            logger.info("图片处理流程结束")

    def process_ruby(self, soup):
        ruby_tags = soup.find_all('ruby')  # 查找所有的 <ruby> 标签
        for ruby_tag in ruby_tags:
            img_tags = ruby_tag.find_all('img')  # 查找所有的 <img> 标签
            for img_tag in img_tags:
                copy_tag = copy.copy(img_tag)  # 复制图片标签
                ruby_tag.insert_before(copy_tag)  # 将复制的图片标签插入到 ruby 标签之前
                img_tag.extract()  # 删除原始图片标签
            rt_tags = ruby_tag.find_all('rt')  # 查找所有的 <rt> 标签
            original_content = ruby_tag.get_text()  # 获取原始内容（不包含 <rt> 标签）
            merged_content = ''.join(rt_tag.get_text(strip=True)for rt_tag in rt_tags if rt_tag.get_text(strip=True)) # 合并 <rt> 标签 忽略所有的嵌套标签
            for rt_tag in rt_tags:
                rt_tag.extract()  # 删除所有的 <rt> 标签
            rt_tag = soup.new_tag('rt')  # 创建一个新的 <rt> 标签
            rt_tag.string = merged_content  # 设置新的 <rt> 标签的内容
            new_ruby_tag = soup.new_tag('ruby')  # 创建一个新的 <ruby> 标签
            new_ruby_tag.string = original_content.replace('\n', '')  # 设置新的 <ruby> 标签的内容，并删除换行符
            new_ruby_tag.append(rt_tag)  # 将新的 <rt> 标签添加到新的 <ruby> 标签中
            ruby_tag.replace_with(new_ruby_tag)  # 用新的 <ruby> 标签替换原始的 <ruby> 标签

    def post_process_images(self, soup):
        # 合并处理div和p标签处理逻辑 改成遍历所有img标签
        # 删除img标签内style 如果没有alt则填充空白alt 排除span标签跟class=gaiji的标签
        for img in soup.find_all('img'):
            if 'gaiji' in img.get('class', []):
                continue
            parent = img.parent
            if parent.name in ('div', 'p'):
                if all(
                    c == img or
                    (getattr(c, 'name', None) == 'br') or
                    (isinstance(c, str) and not c.strip())
                    for c in parent.contents
                ):
                    img.attrs.pop('style', None)
                    img['alt'] = img.get('alt', '')
                    new_div = soup.new_tag('div', attrs={'class': 'illus duokan-image-single'})
                    img.extract()
                    new_div.append(img)
                    parent.replace_with(new_div)

        # 处理 svg 和 ops:switch
        for tag in soup.find_all(['svg', 'ops:switch']):
            if tag.name == 'svg' or (tag.name == 'ops:switch' and tag.find('svg')):
                image_tag = tag.find('image')
                if image_tag:
                    href = image_tag.get('xlink:href') or image_tag.get('{http://www.w3.org/1999/xlink}href')
                    if href:
                        new_div = soup.new_tag('div', attrs={'class': 'illus duokan-image-single'})
                        new_img = soup.new_tag('img', src=href, alt='')
                        new_div.append(new_img)
                        # 如果是 ops:switch 标签，直接替换整个标签
                        if tag.name == 'ops:switch':
                            tag.replace_with(new_div)
                        else:  # 如果是 svg，替换 svg
                            tag.replace_with(new_div)

    def modify_html(self, html, class_names):
        soup = BeautifulSoup(html, 'html.parser')
        classes = [c.strip() for c in class_names.split('|') if c.strip()]
        for class_name in classes:
            for span in soup.select(f'span[class~="{class_name}"]'):
                ruby = soup.new_tag('ruby')
                if span.string:
                    for char in span.string:
                        ruby.append(soup.new_string(char))
                        rt_tag = soup.new_tag('rt')
                        rt_tag.append(soup.new_string("・"))
                        ruby.append(rt_tag)
                else:
                    ruby.append(soup.new_string(''))
                span.replace_with(ruby)
        return str(soup)

    def show_exclude_dialog(self):
        """章节合并排除对话框"""
        if not hasattr(self, 'epub_path'):
            messagebox.showwarning("警告", "请先选择EPUB文件")
            return
        # 创建临时目录解析EPUB
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            with zipfile.ZipFile(self.epub_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            # 获取并解析OPF文件
            opf_path = self._get_opf_path(temp_dir_path)
            opf_content = opf_path.read_text(encoding='utf-8')
            opf_soup = BeautifulSoup(opf_content, 'xml')
            # 获取目录条目
            toc_entries = self._parse_toc(opf_soup, opf_path)
            if not toc_entries:
                messagebox.showwarning("警告", "未找到目录条目")
                return
        # 创建选择对话框
        dialog = tk.Toplevel(self.root)
        dialog.title("选择不合并的目录条目")
        dialog.geometry(f"500x400+{self.root.winfo_x()+50}+{self.root.winfo_y()+30}")
        # 创建Treeview
        tree_frame = ttk.Frame(dialog)
        tree = ttk.Treeview(tree_frame, columns=("title", "href"), show="headings")
        tree.heading("title", text="目录名称")
        tree.heading("href", text="HTML文件")
        tree.column("title", width=300)
        tree.column("href", width=100)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        # 填充目录条目
        for entry in toc_entries:
            title = entry.get('title', '无标题')
            tree.insert("", "end", values=(title, entry['href']))
        for child in tree.get_children():
            if tree.item(child)['values'][1] in self.excluded_toc_entries:
                tree.selection_add(child)
        # 添加单击切换选中功能
        def toggle_selection(event):
            item = tree.identify_row(event.y)
            if not item:
                return
            if item in tree.selection():
                tree.selection_remove(item)
            else:
                tree.selection_add(item)
            return "break"
        tree.bind("<Button-1>", toggle_selection)
        # 确认按钮
        def on_confirm():
            self.excluded_toc_entries = [tree.item(i)['values'][1] for i in tree.selection()]
            dialog.destroy()
        confirm_btn = ttk.Button(dialog, text="确认", command=on_confirm)
        # 布局
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        confirm_btn.pack(side="bottom", pady=5)

    def show_class_list(self):
        """class样式收集分析对话框"""
        if not hasattr(self, 'epub_path'):
            messagebox.showwarning("警告", "请先选择EPUB文件")
            return
        cw = tk.Toplevel(self.root)
        cw.title("html内样式收集分析")
        cw.geometry(f"320x420+{self.root.winfo_x()+130}+{self.root.winfo_y()+60}")

        tree_frame = ttk.Frame(cw)
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)
        tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
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
        for _, (classes, node) in nodes.items():
            for cls in sorted(classes):
                tree.insert(node, "end", text=cls)
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
        tree.bind("<Double-1>", show_details)

    def save_app_settings(self, return_config=False):
        """保存AppSettings到config.ini，或返回ConfigParser对象"""
        config = configparser.ConfigParser()
        config['AppSettings'] = {
            name: str(var.get())
            for name, var in self._settings_vars_dict.items()
        }
        if return_config:
            return config
        # 统一保存AppSettings和RegexRules
        from io import StringIO
        buffer = StringIO()
        config.write(buffer)
        app_settings_content = buffer.getvalue().strip()
        regex_rules_content = self.regex_manager.get_rules_content()
        try:
            with open(self.config_file, 'w', encoding='utf-8', newline='\n') as f:
                f.write(app_settings_content + "\n\n" + regex_rules_content + "\n")
            logger.info(f"设置已保存到: {self.config_file}")
        except Exception as e:
            logger.error(f"保存设置失败: {e}")

    def load_app_settings(self):
        """从config.ini加载AppSettings"""
        if not self.config_file.exists():
            logger.warning(f"配置文件不存在: {self.config_file}")
            return
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                content = f.read()
            # 只提取 [AppSettings] 段
            app_settings_text = ""
            in_section = False
            for line in content.splitlines():
                if line.strip().startswith("[AppSettings]"):
                    in_section = True
                    app_settings_text += line + "\n"
                    continue
                if in_section:
                    if line.strip().startswith("[") and not line.strip().startswith("[AppSettings]"):
                        break
                    app_settings_text += line + "\n"
            if not app_settings_text.strip():
                return
            config = configparser.ConfigParser()
            config.read_string(app_settings_text)
            if 'AppSettings' in config:
                for name, var in self._settings_vars_dict.items():
                    if name in config['AppSettings']:
                        # 判断类型
                        if isinstance(var, tk.BooleanVar):
                            var.set(config['AppSettings'].getboolean(name))
                        else:
                            var.set(config['AppSettings'][name])
            logger.info(f"设置已加载: {self.config_file}")
        except Exception as e:
            logger.error(f"加载设置失败: {e}")

    def reset_app_settings(self):
        """重置所有设置为控件默认值"""
        for name, var in self._settings_vars_dict.items():
            # 直接重置为控件初始化时的默认值
            if isinstance(var, tk.BooleanVar):
                var.set(True)
            elif isinstance(var, tk.StringVar):
                # 这里可以根据你的需求设置默认值
                if name == 'class_name_var':
                    var.set('em-sesame|em-dot')
                elif name == 'image_params_var':
                    var.set("-f webp -q80 -H 300 -s 1.4 -w7")
                else:
                    var.set('')

if __name__ == "__main__":
    logger.info("程序初始化")
    root = tk.Tk()
    processor = EpubProcessor(root)
    logger.info("进入主循环")
    root.mainloop()
