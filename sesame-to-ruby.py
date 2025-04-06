import os
import re
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
from urllib.parse import unquote
from loguru import logger

class EpubProcessor:
    def __init__(self, root):
        self.root = root
        self.regex_entries = []
        FONT = ("宋体", 12)

        # 设置窗口图标
        icon_data = icon_base64  # 图标的Base64数据
        icon_img = tk.PhotoImage(data=icon_data)
        root.iconphoto(True, icon_img)
        root.title("EPUB傍点转Ruby")

        self.class_name_var = tk.StringVar()
        self.class_name_var.set('em-sesame')  # 默认值

        class_name_entry = Entry(root, textvariable=self.class_name_var, font=FONT)
        class_name_label = Label(root, text='傍点class名', font=FONT)

        class_name_label.pack()
        class_name_entry.pack()

        open_button = Button(root, text='选择epub文件', command=self.open_file_dialog, font=FONT)
        open_button.pack()

        flags = ["modify_html_enabled",
            "process_ruby_enabled",
            "process_images_enabled",
            "merge_xhtml_enabled",
            "delete_style_enabled",
            "generate_ncx_enabled",
            "convert_epub_version_enabled"]
        for flag in flags:setattr(self, flag, tk.BooleanVar(value=True))

        buttons_config = [
            ("傍点转换ruby格式", self.modify_html_enabled),
            ("Ruby格式规格化", self.process_ruby_enabled),
            ("图片标签多看交互规格化", self.process_images_enabled),
            ("Xhtml章节间合并", self.merge_xhtml_enabled),
            ("删除自带Style并添加自定义样式表", self.delete_style_enabled),
            ("生成ncx并更新opf", self.generate_ncx_enabled),
            ("转Epub2.0并删除nav.xhtml", self.convert_epub_version_enabled)]
        for text, variable in buttons_config:
            check_button = tk.Checkbutton(root, text=text, variable=variable, onvalue=True, offvalue=False, font=FONT)
            check_button.pack(anchor='w')

        # 图片转换
        self.convert_images_var = tk.BooleanVar(value=True)
        self.image_params_var = tk.StringVar(value="-f webp -q 85 -H 300 -s 1.4")
        f = tk.Frame(root)
        tk.Checkbutton(f, text="转换图片", variable=self.convert_images_var, font=FONT).pack(side=tk.LEFT)
        tk.Entry(f, textvariable=self.image_params_var, width=30, font=FONT).pack(side=tk.LEFT)
        f.pack(fill=tk.X, anchor='w')

        self.regex_manager = RegexManager(root)

    def open_file_dialog(self):
        path = filedialog.askopenfilename(filetypes=[('EPUB文件', '*.epub')])
        logger.debug(f"用户选择的文件路径: {path}")
        if path:
            output_filename = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[('EPUB文件', '*.epub')])
            logger.debug(f"输出文件路径: {output_filename}")
            if output_filename:
                self.process_epub(path, output_filename)
                messagebox.showinfo("处理完成", f"输出文件已保存到：{output_filename}")

    def process_epub(self, path, output_filename):
        logger.info(f"开始处理EPUB文件: {path}")
        class_name = self.class_name_var.get()
        regex_pairs = self.regex_manager.get_rules()

        with tempfile.TemporaryDirectory() as temp_dir:
            logger.debug(f"解压临时目录: {temp_dir}")
            with zipfile.ZipFile(path, 'r') as zip_ref:
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
            custom_css_src = os.path.join(os.path.dirname(__file__), 'style.css')
            if os.path.exists(custom_css_src):
                shutil.copy(custom_css_src, os.path.join(css_target_dir, 'style.css'))
            else:
                messagebox.showwarning("警告", "自定义样式表文件 style.css 不存在")

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
                    messagebox.showwarning("NCX生成警告", msg)
            if self.convert_epub_version_enabled.get():               
                # opf修改Epub版本为2.0 并删除nav文件
                success, msg = EpubNCXGenerator.convert_to_epub2(opf_path)
                if not success:
                    messagebox.showwarning("版本转换警告", msg)
                # 删除nav（如果存在）
                nav_item = opf_soup.find('item', properties='nav')
                if nav_item:
                    nav_path = os.path.join(os.path.dirname(opf_path), nav_item['href'])
                    if os.path.exists(nav_path):
                        os.remove(nav_path)
            logger.info("OPF文件和样式处理完成")

    def merge_xhtml_files(self, temp_dir, opf_path):
        logger.info("章节间合并Xhtml文件")
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

            logger.debug(f"合并范围: {start_idx} -> {end_idx}")

            # 读取主文件
            main_file = spine_files[start_idx]
            with open(main_file, 'r', encoding='utf-8') as f:
                main_soup = BeautifulSoup(f.read(), 'html.parser')
            
            # 合并内容
            for merge_file in spine_files[start_idx+1:end_idx]:
                logger.debug(f"正在合并文件: {merge_file}")
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
                
                # 删除已合并文件
                os.remove(merge_file)
                logger.debug(f"已删除文件: {merge_file}")

                # 从OPF中移除引用
                merge_href = os.path.relpath(merge_file, opf_dir)
                item = opf_soup.find('item', href=merge_href)
                if item:
                    item_id = item['id']
                    for itemref in spine.find_all('itemref', idref=item_id):
                        itemref.decompose()

            # 保存合并后的文件
            with open(main_file, 'w', encoding='utf-8') as f:
                f.write(str(main_soup))
                logger.debug(f"已保存合并后的主文件: {main_file}")

        # 更新OPF文件
        with open(opf_path, 'w', encoding='utf-8') as f:
            f.write(str(opf_soup))
        logger.info("XHTML文件合并完成")

    def _parse_toc(self, opf_soup, opf_path):
        """解析目录结构，兼容EPUB 2.0（NCX）和EPUB 3.0（NAV）"""
        ncx_item = opf_soup.find('item', attrs={"media-type": "application/x-dtbncx+xml"})
        if ncx_item:
            ncx_path = os.path.normpath(os.path.join(os.path.dirname(opf_path), ncx_item['href']))
            if os.path.exists(ncx_path):
                with open(ncx_path, 'r', encoding='utf-8') as f:
                    ncx_soup = BeautifulSoup(f.read(), 'xml')
                if nav_map := ncx_soup.find('navMap'):
                    return [{'href': content['src']} for content in nav_map.find_all('content')]

        if nav_item := opf_soup.find('item', properties='nav'):
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
                    return [{'href': a['href'].split('#')[0]} for a in nav_tag.find_all('a', href=True)]
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
            converter_path = os.path.join(os.path.dirname(__file__), "image_converter.exe")
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
            except subprocess.CalledProcessError as e:
                logger.error("[错误] 转换失败:\n" + e.stderr)
                raise
            finally:
                os.remove(list_path)
                logger.debug(f"[清理] 已删除临时文件: {list_path}")
            # ===== 5. 清理旧文件 =====
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
            logger.info(f"[状态] 共清理 {deleted_files}/{len(original_images)} 个旧文件")
            # ===== 6. 更新内容文件 =====
            updated_files = 0
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if not file.lower().endswith(('.xhtml', '.html', '.opf')):
                        continue
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r+', encoding='utf-8') as f:
                            content = f.read()
                            original_content = content
                            for old, new in image_mapping.items():
                                content = content.replace(old, new)
                            if content != original_content:
                                f.seek(0)
                                f.write(content)
                                f.truncate()
                                updated_files += 1
                                logger.debug(f"[更新] 已修改: {os.path.relpath(file_path, temp_dir)}")
                    except UnicodeDecodeError:
                        logger.warning(f"[警告] 跳过二进制文件: {file}")
                    except Exception as e:
                        logger.error(f"[错误] 处理文件失败 {file}: {str(e)}")
            logger.info(f"[状态] 共更新 {updated_files} 个内容文件")
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
                        if file_name in image_mapping or ext == target_ext:
                            # 更新href
                            if file_name in image_mapping:
                                new_name = image_mapping[file_name]
                                item['href'] = href.replace(file_name, new_name)
                                logger.debug(f"[OPF] 更新路径: {file_name} → {new_name}")
                            # 强制更新media-type
                            new_type = media_map[target_ext]
                            if item.get('media-type') != new_type:
                                logger.debug(f"[OPF] 更新类型: {item.get('media-type')} → {new_type}")
                                item['media-type'] = new_type
                                modified = True
                    if modified:
                        f.seek(0)
                        f.write(str(soup))
                        f.truncate()
                        logger.info("更新图片OPF清单 成功")
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
            if img_tag and img_tag.get('xlink:href', '').startswith('../Images/'):
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

    def modify_html(self, html, class_name):
        soup = BeautifulSoup(html, 'html.parser')
        for span in soup.find_all('span', class_=class_name):
            ruby = soup.new_tag('ruby')
            if span.string:
                for char in span.string:
                    ruby.append(soup.new_string(char))
                    rt_tag = soup.new_tag('rt')
                    rt_tag.append(soup.new_string("・"))
                    ruby.append(rt_tag)
            else:
                ruby.append(soup.new_string(''))  # 如果为空，直接添加一个空字符串
            span.replace_with(ruby)  # 替换原来的 span 标签
        return str(soup)

if __name__ == "__main__":
    logger.info("程序初始化")
    root = tk.Tk()
    processor = EpubProcessor(root)
    logger.info("进入主循环")
    root.mainloop()
