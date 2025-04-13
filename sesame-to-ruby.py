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
import warnings
import shutil
import lxml.etree
from urllib.parse import unquote
from loguru import logger

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

        # 第一行：操作按钮
        tk.Button(main_frame, text='读取epub', command=self.open_file_dialog, font=FONT).grid(
            row=0, column=0, padx=5, pady=2, sticky='w')
        tk.Button(main_frame, text='开始转换', command=self.start_conversion, font=FONT).grid(
            row=0, column=1, padx=5, pady=2, sticky='w')

        # 第二行：功能按钮
        tk.Button(main_frame, text='class列表', command=self.show_class_list, font=FONT).grid(
            row=1, column=0, padx=5, pady=2)
        tk.Button(main_frame, text='排除合并', command=self.show_exclude_dialog, font=FONT).grid(
            row=1, column=1, padx=5, pady=2)
        
        # 傍点转ruby设置
        self.modify_html_enabled = tk.BooleanVar(value=True)
        self.class_name_var = tk.StringVar(value='em-sesame|em-dot')
        f_ruby = tk.Frame(root)
        tk.Checkbutton(f_ruby, text="傍点转ruby", variable=self.modify_html_enabled, font=FONT).pack(side=tk.LEFT)
        tk.Entry(f_ruby, textvariable=self.class_name_var, width=25, font=FONT).pack(side=tk.LEFT)
        f_ruby.pack(fill=tk.X, anchor='w')

        flags = [
            "process_ruby_enabled",
            "process_images_enabled",
            "merge_xhtml_enabled",
            "delete_style_enabled",
            "generate_ncx_enabled",
            "convert_epub_version_enabled"]
        for flag in flags:setattr(self, flag, tk.BooleanVar(value=True))
        buttons_config = [
            ("Ruby格式规格化", self.process_ruby_enabled),
            ("图片标签多看交互规格化", self.process_images_enabled),
            ("Xhtml章节间合并", self.merge_xhtml_enabled),
            ("删除自带Style并添加自定义样式表", self.delete_style_enabled),
            ("生成ncx并更新opf", self.generate_ncx_enabled),
            ("转Epub2.0并删除nav.xhtml", self.convert_epub_version_enabled)]
        for text, variable in buttons_config:
            check_button = tk.Checkbutton(root, text=text, variable=variable, onvalue=True, offvalue=False, font=FONT)
            check_button.pack(anchor='w')

        # 图片转换设置
        self.convert_images_var = tk.BooleanVar(value=True)
        self.image_params_var = tk.StringVar(value="-f webp -q 85 -H 300 -s 1.4")
        f_image = tk.Frame(root)
        tk.Checkbutton(f_image, text="转换图片", variable=self.convert_images_var, font=FONT).pack(side=tk.LEFT)
        tk.Entry(f_image, textvariable=self.image_params_var, width=30, font=FONT).pack(side=tk.LEFT)
        f_image.pack(fill=tk.X, anchor='w')        

        self.regex_manager = RegexManager(root)

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
            messagebox.showinfo("处理完成", f"输出文件已保存到：{output_filename}")

    def process_epub(self, output_filename):
        logger.info(f"开始处理epub文件: {self.epub_path}")
        class_name = self.class_name_var.get()

        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"解压临时目录: {temp_dir}")
            with zipfile.ZipFile(self.epub_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            # 解析 container.xml，找到 .opf 文件路径
            container_path = os.path.join(temp_dir, 'META-INF', 'container.xml')
            with open(container_path, 'r', encoding='utf-8') as f:
                container_content = f.read()
            root = ET.fromstring(container_content)
            opf_path = None
            for rootfile in root.findall('.//{*}rootfile'):
                opf_path = rootfile.get('full-path')
                if opf_path:
                    break
            if not opf_path:
                raise ValueError("未找到 .opf 文件路径")
            opf_full_path = os.path.join(temp_dir, opf_path)
            logger.debug(f"OPF文件路径: {opf_full_path}")

            # 图片转换
            if self.convert_epub_version_enabled.get():
                self.convert_epub_images(temp_dir)

            # 删除自带样式并添加自定义样式表并更新opf跟页面引用
            if self.delete_style_enabled.get():
                self.process_opf_and_styles(opf_full_path, temp_dir)

            # 遍历所有文件并处理
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
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
                self.merge_xhtml_files(temp_dir, opf_full_path)

            # 重新打包 EPUB 文件
            with zipfile.ZipFile(output_filename, "w", zipfile.ZIP_DEFLATED) as zip_ref:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zip_ref.write(file_path, arcname)
            logger.info(f"EPUB文件处理完成，保存到: {output_filename}")

    def process_opf_and_styles(self, opf_path, temp_dir):
            """生成ncx 自定义样式表几清单引用更新"""
            # 读取并解析 OPF 文件
            with open(opf_path, 'r', encoding='utf-8') as f:
                opf_content = f.read()
            opf_soup = BeautifulSoup(opf_content, 'xml')

            # 1. 删除 page-progression-direction 属性
            spine_tag = opf_soup.find('spine')
            if spine_tag and 'page-progression-direction' in spine_tag.attrs:
                del spine_tag.attrs['page-progression-direction']

            # 2. 清理 OPF 文件中的 CSS 引用
            for item in opf_soup.find_all('item', attrs={'media-type': 'text/css'}):
                item.decompose()

            # 3. 动态生成CSS路径 添加自定义样式表到OPF清单
            opf_dir = os.path.dirname(opf_path)
            css_target_dir = os.path.join(opf_dir, 'css')
            os.makedirs(css_target_dir, exist_ok=True)
            manifest_tag = opf_soup.find('manifest')
            if manifest_tag:
                css_rel_path = os.path.relpath(
                    os.path.join(css_target_dir, 'style.css'),
                    opf_dir
                ).replace('\\', '/')
                new_item = opf_soup.new_tag('item', attrs={
                    'href': css_rel_path,
                    'id': 'style-css',
                    'media-type': 'text/css'
                })
                manifest_tag.append(new_item)
            # 写回 OPF 文件
            with open(opf_path, 'w', encoding='utf-8') as f:
                f.write(str(opf_soup))

            # 4. 删除所有自带CSS文件
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    if file.endswith('.css'):
                        os.remove(os.path.join(root, file))

            # 5. 添加自定义的 style.css 文件
            custom_css_src = os.path.join(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__), 'style.css')
            if os.path.exists(custom_css_src):
                shutil.copy(custom_css_src, os.path.join(css_target_dir, 'style.css'))
                logger.info(f"添加style.css 完成")
            else:
                logger.warning(f"自定义style.css文件不存在: {custom_css_src}")

            # 6. 在所有 XHTML 文件中添加样式表链接并清理样式
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    if not file.lower().endswith(('.xhtml', '.html')):
                        continue
                    file_path = os.path.join(root, file)
                    with open(file_path, 'r+', encoding='utf-8') as f:
                        soup = BeautifulSoup(f.read(), 'html.parser')
                        # 清理旧样式标签
                        for tag in soup.select('style, link[rel="stylesheet"]'):
                            tag.decompose()
                        # 动态计算相对路径
                        css_rel_xhtml = os.path.relpath(
                            os.path.join(css_target_dir, 'style.css'),
                            os.path.dirname(file_path)
                        ).replace('\\', '/')
                        # 添加新链接
                        if head_tag := soup.find('head'):
                            link_tag = soup.new_tag('link',
                                rel='stylesheet',
                                type='text/css',
                                href=css_rel_xhtml)
                            head_tag.append(link_tag)
                        # 写回文件
                        f.seek(0)
                        f.write(str(soup))
                        f.truncate()
            if self.generate_ncx_enabled.get():
                # 生成ncx 更新opf
                success, msg = EpubNCXGenerator.generate_ncx(opf_path)
                if not success:
                    logger.warning(f"NCX生成警告: {msg}")
            if self.convert_epub_version_enabled.get():               
                # opf修改Epub版本为2.0 并删除nav文件
                success, msg = EpubNCXGenerator.convert_to_epub2(opf_path)
                if not success:
                    logger.warning(f"版本转换警告: {msg}")
                # 删除nav（如果存在）
                nav_item = opf_soup.find('item', properties='nav')
                if nav_item:
                    nav_path = os.path.join(os.path.dirname(opf_path), nav_item['href'])
                    if os.path.exists(nav_path):
                        os.remove(nav_path)
            logger.info("OPF文件和样式处理完成")

    def merge_xhtml_files(self, temp_dir, opf_path):
        logger.info("章节间Xhtml合并(基于目录 )")
        # 解析OPF文件
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        spine = opf_soup.spine
        if not spine:
            raise ValueError("OPF文件中缺少spine定义")

        # 获取所有spine文件路径
        spine_files = []
        opf_dir = os.path.dirname(opf_path)
        for itemref in spine.find_all('itemref'):
            item = opf_soup.find('item', id=itemref['idref'])
            if item and item.get('media-type') == 'application/xhtml+xml':
                href = item['href']
                # 规范化路径：处理相对路径和URL编码
                normalized_href = os.path.normpath(os.path.join(opf_dir, href))
                spine_files.append(normalized_href)
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
            entry_file = os.path.normpath(os.path.join(opf_dir, entry_href))
            
            try:
                start_idx = spine_files.index(entry_file)
            except ValueError:
                logger.warning(f"目录条目文件未在spine中找到: {entry_file}")
                continue

            # 计算合并范围
            if i + 1 < len(toc_entries):
                next_entry_href = toc_entries[i+1]['href'].split('#')[0]
                next_entry_file = os.path.normpath(os.path.join(opf_dir, next_entry_href))
                try:
                    end_idx = spine_files.index(next_entry_file)
                except ValueError:
                    end_idx = len(spine_files)
            else:
                end_idx = len(spine_files)

            # 输出简洁的合并日志
            main_internal = os.path.relpath(spine_files[start_idx], temp_dir).replace(os.sep, '/')
            merged_internal = [os.path.relpath(f, temp_dir).replace(os.sep, '/') for f in spine_files[start_idx+1:end_idx]]
            logger.debug(f"合并于: {main_internal} 已被合并: {merged_internal}")

            # 读取主文件
            main_file = spine_files[start_idx]
            with open(main_file, 'r', encoding='utf-8') as f:
                main_soup = BeautifulSoup(f.read(), 'html.parser')
            
            # 合并内容
            for merge_file in spine_files[start_idx+1:end_idx]:
                merge_path = os.path.relpath(merge_file, temp_dir).replace(os.sep, '/')
                with open(merge_file, 'r', encoding='utf-8') as f:
                    merge_soup = BeautifulSoup(f.read(), 'html.parser')
                
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
                os.remove(merge_file)
                merge_href = os.path.relpath(merge_file, opf_dir).replace(os.sep, '/')
                item = opf_soup.find('item', href=merge_href)
                if item:
                    item_id = item['id']
                    for itemref in spine.find_all('itemref', idref=item_id):
                        itemref.decompose()

            # 保存合并后的文件
            with open(main_file, 'w', encoding='utf-8') as f:
                f.write(str(main_soup))

        # 更新OPF文件
        with open(opf_path, 'w', encoding='utf-8') as f:
            f.write(str(opf_soup))
        logger.info("章节间Xhtml合并 完成")

    def _parse_toc(self, opf_soup, opf_path):
        """解析目录结构，兼容EPUB 2.0(NCX)和EPUB 3.0(NAV)"""
        # 尝试解析EPUB 2.0 NCX格式
        ncx_item = opf_soup.find('item', attrs={"media-type": "application/x-dtbncx+xml"})
        if ncx_item:
            ncx_path = os.path.normpath(os.path.join(os.path.dirname(opf_path), ncx_item['href']))
            if os.path.exists(ncx_path):
                with open(ncx_path, 'r', encoding='utf-8') as f:
                    ncx_soup = BeautifulSoup(f.read(), 'xml')
                if nav_map := ncx_soup.find('navMap'):
                    return [{
                        'title': nav_point.find('navLabel').text.strip(),
                        'href': nav_point.find('content')['src']
                    } for nav_point in nav_map.find_all('navPoint')]

        # 尝试解析EPUB 3.0 NAV格式
        nav_item = opf_soup.find('item', properties='nav')
        if nav_item:
            nav_path = os.path.normpath(os.path.join(os.path.dirname(opf_path), nav_item['href']))
            if os.path.exists(nav_path):
                with open(nav_path, 'r', encoding='utf-8') as f:
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
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                        full_path = os.path.join(root, file)
                        if os.path.exists(full_path):  # 二次验证文件存在
                            original_images.append(full_path)
                            logger.debug(f"[扫描] 发现图片文件: {os.path.relpath(full_path, temp_dir)}")
            if not original_images:
                logger.warning("未找到需要转换的图片，跳过此流程")
                return
            # ===== 3. 生成文件名映射 =====
            image_mapping = {}
            for old_path in original_images:
                old_name = os.path.basename(old_path)
                new_name = f"{os.path.splitext(old_name)[0]}.{output_format}"
                image_mapping[old_name] = new_name
                logger.debug(f"[映射] {old_name} → {new_name}")
            # ===== 4. 执行图片转换 =====
            converter_path = os.path.join(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__), "image_converter.exe")
            if not os.path.exists(converter_path):
                raise FileNotFoundError("图片转换器 image_converter.exe 未找到")
            # 生成绝对路径列表文件
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as list_file:
                list_file.write('\n'.join(original_images))
                list_path = list_file.name
                logger.debug(f"[转换] 生成临时列表文件: {list_path}")
            try:
                # 执行转换命令
                cmd = [converter_path, "-i", f"@{list_path}", *params]
                logger.debug("[转换] 执行命令:", ' '.join(cmd))
                result = subprocess.run(cmd,cwd=temp_dir,capture_output=True,text=True,check=True)
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
                dir_path = os.path.dirname(old_path)
                old_name = os.path.basename(old_path)
                new_path = os.path.join(dir_path, image_mapping[old_name])
                if os.path.exists(new_path):
                    try:
                        os.remove(old_path)
                        deleted_files += 1
                        logger.debug(f"[清理] 已删除: {os.path.relpath(old_path, temp_dir)}")
                    except Exception as e:
                        logger.warning(f"[警告] 删除失败 {old_path}: {str(e)}")
                else:
                    logger.error(f"[错误] 新文件未生成: {os.path.relpath(new_path, temp_dir)}")
            logger.info(f"共清理 {deleted_files}/{len(original_images)} 个旧图片文件")
            # ===== 6. 更新html内图片引用 =====
            updated_refs = 0
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if not file.lower().endswith(('.xhtml', '.html')):
                        continue
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r+', encoding='utf-8') as f:
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
                                logger.debug(f"[更新图片路径: {os.path.relpath(file_path, temp_dir)}")
                    except UnicodeDecodeError:
                        logger.warning(f"[警告] 跳过二进制文件: {file}")
                    except Exception as e:
                        logger.error(f"[错误] 处理文件失败 {file}: {str(e)}")
            logger.info(f"共更新 {updated_refs} 个图片路径引用")
            # ===== 7. 强制更新OPF媒体类型 =====
            logger.info("更新图片OPF清单")
            try:
                # 定位OPF文件
                container_path = os.path.join(temp_dir, 'META-INF', 'container.xml')
                with open(container_path, 'r', encoding='utf-8') as f:
                    soup = BeautifulSoup(f.read(), 'xml')
                    opf_rel_path = soup.find('rootfile')['full-path']
                opf_path = os.path.normpath(os.path.join(temp_dir, opf_rel_path))
                logger.debug(f"[OPF] 定位到主文档: {os.path.relpath(opf_path, temp_dir)}")
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
                        normalized_href = os.path.normpath(decoded_href).replace('\\', '/')
                        file_name = os.path.basename(normalized_href)
                        ext = os.path.splitext(file_name)[1][1:].lower()
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
            merged_content = ''.join(rt_tag.string.strip() if rt_tag.string else '' for rt_tag in rt_tags)  # 合并 <rt> 标签内的内容
            for rt_tag in rt_tags:
                rt_tag.extract()  # 删除所有的 <rt> 标签
            rt_tag = soup.new_tag('rt')  # 创建一个新的 <rt> 标签
            rt_tag.string = merged_content  # 设置新的 <rt> 标签的内容
            new_ruby_tag = soup.new_tag('ruby')  # 创建一个新的 <ruby> 标签
            new_ruby_tag.string = original_content.replace('\n', '')  # 设置新的 <ruby> 标签的内容，并删除换行符
            new_ruby_tag.append(rt_tag)  # 将新的 <rt> 标签添加到新的 <ruby> 标签中
            ruby_tag.replace_with(new_ruby_tag)  # 用新的 <ruby> 标签替换原始的 <ruby> 标签

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
            if img_tag:
                href = img_tag.get('xlink:href', '')
                if href.startswith('../Images/') or href.startswith('../image/'):
                    new_div = soup.new_tag('div', attrs={'class': 'illus duokan-image-single'})
                    new_img = soup.new_tag('img', src=img_tag['xlink:href'], alt='', attrs={'class': 'fit'})
                    new_div.append(new_img)
                    svg.replace_with(new_div)

        for switch in soup.find_all('ops:switch'):
            svg = switch.find('svg')
            if svg:
                img_tag = svg.find('image')
                if img_tag and img_tag.get('xlink:href', '').startswith('../image/'):
                    new_div = soup.new_tag('div', attrs={'class': 'illus duokan-image-single'})
                    new_img = soup.new_tag('img', src=img_tag['xlink:href'], alt='', attrs={'class': 'fit'})
                    new_div.append(new_img)
                    switch.replace_with(new_div)

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
        """显示目录条目选择对话框"""
        if not hasattr(self, 'epub_path'):
            messagebox.showwarning("警告", "请先选择EPUB文件")
            return
        # 创建临时目录解析EPUB
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(self.epub_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            # 解析OPF文件获取目录
            container_path = os.path.join(temp_dir, 'META-INF', 'container.xml')
            with open(container_path, 'r', encoding='utf-8') as f:
                container_content = f.read()
            root = ET.fromstring(container_content)
            opf_path = None
            for rootfile in root.findall('.//{*}rootfile'):
                opf_path = rootfile.get('full-path')
                if opf_path:
                    break
            if not opf_path:
                raise ValueError("未找到 .opf 文件路径")
            opf_full_path = os.path.join(temp_dir, opf_path)
            with open(opf_full_path, 'r', encoding='utf-8') as f:
                opf_soup = BeautifulSoup(f.read(), 'xml')
            # 获取目录条目
            toc_entries = self._parse_toc(opf_soup, opf_full_path)
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
        """class列表收集分析"""
        if not hasattr(self, 'epub_path'):
            messagebox.showwarning("警告", "请先选择EPUB文件")
            return
        class_window = tk.Toplevel(self.root)
        class_window.title("html内样式收集分析")
        class_window.geometry(f"320x420+{self.root.winfo_x()+130}+{self.root.winfo_y()+60}")
        tree_frame = ttk.Frame(class_window)
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)
        tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        style_data = {}
        used_classes = set()
        used_spans = set()
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(self.epub_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            # CSS解析
            css_pattern = re.compile(r'([^{]+)\{([^}]+)\}', re.DOTALL)
            for root_dir, _, files in os.walk(temp_dir):
                for file in files:
                    if file.endswith('.css'):
                        css_file = os.path.join(root_dir, file)
                        rel_path = os.path.relpath(css_file, temp_dir)
                        with open(css_file, 'r', encoding='utf-8') as f:
                            content = f.read()
                            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
                            for match in css_pattern.finditer(content):
                                selectors_part, css_body = match.groups()
                                selectors = [s.strip() for s in selectors_part.split(',')]
                                formatted = re.sub(r'\s*{\s*', '', css_body, count=1)
                                formatted = re.sub(r';\s*', ';\n  ', formatted.strip())
                                formatted = re.sub(r'\s*}\s*', '', formatted)
                                for selector in selectors:
                                    # 提取选择器中的所有类名
                                    classes = re.findall(r'\.([\w-]+)', selector)
                                    for cls in classes:
                                        if cls not in style_data:
                                            style_data[cls] = {}
                                        if rel_path not in style_data[cls]:
                                            style_data[cls][rel_path] = []
                                        # 存储完整选择器和格式化后的内容
                                        style_data[cls][rel_path].append({
                                            'selector': selector,
                                            'content': formatted
                                        })
            # HTML解析部分
            html_pattern = re.compile(r'.*\.(x?html?)$', re.I)
            for root_dir, _, files in os.walk(temp_dir):
                for file in files:
                    if html_pattern.match(file):
                        file_path = os.path.join(root_dir, file)
                        with open(file_path, 'r', encoding='utf-8') as f:
                            soup = BeautifulSoup(f.read(), 'html.parser')
                            for tag in soup.find_all(class_=True):
                                used_classes.update(tag.get('class', []))
                            for span in soup.find_all('span'):
                                if span.get('class'):
                                    used_spans.update(span.get('class'))
        class_node = tree.insert("", "end", text="Class列表", open=True)
        span_node = tree.insert("", "end", text="Span列表", open=True)
        for cls in sorted(used_classes):
            tree.insert(class_node, "end", text=cls)
        for span_cls in sorted(used_spans):
            tree.insert(span_node, "end", text=span_cls)
        # 样式详情显示
        def show_details(event):
            item = tree.selection()[0]
            parent = tree.parent(item)
            if parent in (class_node, span_node):
                style_name = tree.item(item, "text")
                details = []
                if style_name in style_data:
                    for path, rules in style_data[style_name].items():
                        for rule in rules:
                            content_lines = [
                                f"  {line.strip()}" 
                                for line in rule['content'].split('\n') 
                                if line.strip()
                            ]
                            details.append(
                                f"文件: {path}\n"
                                f"{rule['selector']} {{\n" + '\n'.join(content_lines) + "\n}\n\n"
                            )
                detail_win = tk.Toplevel(class_window)
                detail_win.title(f"{style_name}")
                detail_win.geometry(f"350x180+{self.root.winfo_x()+160}+{self.root.winfo_y()+130}")
                text_frame = ttk.Frame(detail_win)
                text_frame.pack(fill="both", expand=True, padx=5, pady=5)
                text = tk.Text(text_frame, wrap=tk.WORD, font=('Consolas', 10))
                vsb = ttk.Scrollbar(text_frame, command=text.yview)
                text.configure(yscrollcommand=vsb.set)
                vsb.pack(side="right", fill="y")
                text.pack(side="left", fill="both", expand=True)
                text.insert("end", "".join(details) or f"未找到 {style_name} 的CSS定义")
                text.config(state="disabled")
        tree.bind("<Double-1>", show_details)

if __name__ == "__main__":
    logger.info("程序初始化")
    root = tk.Tk()
    processor = EpubProcessor(root)
    logger.info("进入主循环")
    root.mainloop()
