import atexit
import os
import re
import sys
import copy
import zipfile
import tempfile
import time
import subprocess
import shutil
from pathlib import Path
from urllib.parse import unquote
import configparser

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD
from bs4 import BeautifulSoup, NavigableString # bs4需要lxml库 会优先自动使用
from loguru import logger

from Image import icon_base64
from tooltip import ToolTip
from epub_ncx_generator import EpubNCXGenerator
from regex_manager import RegexManager, AutoScrollbar
from class_list import ClassList

class EpubProcessor:
    def __init__(self, root):
        self.root = root
        self.regex_entries = []
        self.excluded_toc_entries = []
        self._exclude_tempdirs = set()
        self.sesame_root = Path(tempfile.gettempdir(), "sesame_cache"); self.sesame_root.mkdir(parents=True, exist_ok=True)
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
            ('读取epub', self.open_file_dialog, (0, 0), "加载单个epub文件\n支持拖拽epub进UI窗口"),
            ('开始转换', self.start_conversion, (0, 1), "转换加载的单个epub文件"),
            ('批量转换', self.batch_convert_epubs, (0, 2), "批量转换\n支持epub拖拽到按钮\n原名文件保存至output文件夹"),
            ('class列表', self.show_class_list, (1, 0), "epub内所使用的class列表\nspan列表\n图片class列表"),
            ('排除合并', self.show_exclude_dialog, (1, 1), "优先显示nav后显示ncx.注意偏移只对ncx生效\n章节合并功能排除选定的目录条目\n批量也能排除指定的章节名\n右键管理排除列表"),
            ('重置设置', self.reset_app_settings, (1, 2), "重置所有设置为默认状态\n右键重置内存winsize值"),
        ]
        for text, cmd, (row, col), tip in btn_cfgs:
            btn = tk.Button(main_frame, text=text, command=cmd, font=FONT)
            btn.grid(row=row, column=col, padx=5, pady=2, sticky='w')
            ToolTip(btn, text=tip)
            if text == '批量转换':
                btn.drop_target_register(DND_FILES)
                btn.dnd_bind('<<Drop>>', lambda e, self=self: self.root.after(100, lambda: self.batch_convert_epubs(
                    [f for f in self.root.tk.splitlist(e.data) if f.lower().endswith('.epub')])))
            elif text == '重置设置':
                btn.bind('<Button-3>', lambda e: self.win_size.clear())
            elif text == '排除合并':
                btn.bind('<Button-3>', lambda e: self.show_exclude_list_dialog() if e.num == 3 else None)

        # 处理选项 滚动条区域
        self._settings_vars_dict = {}  # 自动收集所有设置变量
        MAX_SHOW = 8  
        self.CFG = [
            ('modify_html_enabled', '傍点转ruby', '需要检查class', [('class_name_var', 'em-sesame|em-dot|kenten', tk.Entry, {'w': 25}, '一般class名:\nem-sesame|em-dot|kenten')]),
            ('process_ruby_enabled', 'Ruby格式规格化', '格式奇怪跟包含gaiji图片的标签规格化兼容处理', []),
            ('process_images_enabled', '图片标签多看交互规格化', '将奇怪的图片标签全部规格化成多看格式\n排除span跟gaiji', []),
            ('merge_xhtml_enabled', 'Xhtml章节间合并', '根据目录合并章节间文件', [
                ('merge_separator_var', '3br', ttk.Combobox, {'w': 5, 'val': ['-','hr+br']+[f'{i}br' for i in range(1, 9)]}, '章节合并时插入的分隔符样式'),
                ('merge_remove_blank_lines_var', '-', ttk.Combobox, {'w': 2, 'val': ['-']+[str(i) for i in range(1, 10)], 'px': (13,0)}, '删除指定的空行数量'),
                ('merge_limit_blank_lines_var', '3', ttk.Combobox, {'w': 2, 'val': ['-']+[str(i) for i in range(1, 10)], 'px': (3,0)}, '限制连续空行的行数')]),
            ('delete_style_enabled', '删除自带Style并添加自定义样式表', '清理原有样式跟opf竖排属性\n添加css文件及更新引用\n规格化头部信息', []),
            ('generate_ncx_enabled', '生成ncx并更新opf', '自动对照opf列表修正路径', [
                ('ncx_offset_enabled', '偏移', tk.Checkbutton, {'px': (3, 0)}, '最后一条目录文件不存在时进行-1顺序修正\n自动偏移开关,不影响强制偏移\n只用于ncx nav没写'),
                ('ncx_manual_offset_val', '0', tk.Entry, {'w': 3, 'px': (0, 0)}, '强制目录偏移+ -，0不执行操作\n优先于自动偏移\n只用于ncx nav没写'),
                ('ncx_atokagi_enabled', '补全后记', tk.Checkbutton, {'px': (3, 0)}, '自动补全ncx/nav缺失的あとがき条目\n前20行含あとがき关键词全书唯一html')]),
            ('convert_epub_version_enabled', '转Epub2.0并删除nav.xhtml', '将EPUB版本转换为2.0\n移除nav.xhtml\n生成cover声明', []),
            ('convert_images_var', '转换图片', '图片转换设置', [
                ('image_params_var', '-f webp -q80 -H1300 -s1 -w8 -A', tk.Entry, {'w': 10, 'sticky': 'ew'}, 
                 ('-f 可选webp,jpg,png\n-q 质量\n-H -W 高宽按比例缩小,小图不放大\n'
                  '-s 锐化 默认1.0不处理\n-A 保留透明通道Alpha\n-w 线程数\n-m WebP压缩等级 1-6'))]),
            ('set_lang_enabled', '语言标识', 'opf跟head的头部语言标识参数', [('set_lang_var', 'ja', tk.Entry, {'w': 10}, 'ja\nzh-CN')]),
        ]
        # 1.变量初始化
        for k, _, _, ex in self.CFG:
            v = tk.BooleanVar(value=True); self._settings_vars_dict[k] = v; setattr(self, k, v)
            for ek, ev, cls, _, _ in ex:
                var = (tk.BooleanVar(value=True) if cls == tk.Checkbutton else tk.StringVar(value=ev))
                self._settings_vars_dict[ek] = var; setattr(self, ek, var)
        # 2.布局
        f_scroll = tk.Frame(root); f_scroll.pack(fill=tk.X, padx=(1, 0), pady=0)
        cvs = tk.Canvas(f_scroll, highlightthickness=0)
        vsb = AutoScrollbar(f_scroll, canvas=cvs, orient="vertical", command=cvs.yview)
        cvs.configure(yscrollcommand=vsb.set)
        cvs.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(cvs); win = cvs.create_window((0, 0), window=self.inner, anchor='nw')
        # 高度自适应绑定
        _u = lambda e=None: [
            self.inner.update_idletasks(),
            cvs.configure(height=sum(c.winfo_reqheight() for c in self.inner.winfo_children()[:MAX_SHOW]), 
                          scrollregion=cvs.bbox("all")),
            cvs.itemconfig(win, width=cvs.winfo_width())]
        cvs.bind('<Configure>', _u); root.after(10, _u)
        # 3.渲染逻辑
        for k, txt, tip, extras in self.CFG:
            row = tk.Frame(self.inner); row.pack(fill=tk.X, anchor='w')
            cb = tk.Checkbutton(row, text=txt, variable=self._settings_vars_dict[k], font=FONT)
            cb.pack(side=tk.LEFT); ToolTip(cb, text=tip)
            for ek, et, cls, kw, etp in extras:
                cfg = {'variable' if cls==tk.Checkbutton else 'textvariable': self._settings_vars_dict[ek], 'font': FONT}
                if cls == tk.Checkbutton: cfg['text'] = et
                else: cfg.update({'width': kw.get('w'), **({'values': kw.get('val'), 'state': 'readonly'} if cls==ttk.Combobox else {})})
                w = cls(row, **cfg); ToolTip(w, text=etp)
                w.pack(side=tk.LEFT, fill=(tk.X if kw.get('sticky')=='ew' else None), expand=(kw.get('sticky')=='ew'), padx=kw.get('px', 0))

        # 配置路径与读写
        base_dir = Path(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.argv[0]))))
        self.config_file = base_dir / "config.ini"
        self.win_size = WinSize(self.config_file)
        self.log_level_var = self._settings_vars_dict.setdefault('log_level', tk.StringVar(value="info"))
        self.load_app_settings()
        recorder = self.win_size.setup(root, "main", "350x620+600+160")
        root.bind('<Configure>', recorder, add='+')

        self.regex_manager = RegexManager(root, self.config_file, self.log_level_var, self)
        self._save_config()

        # 拖拽支持
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self._on_drop_epub)

    def _save_config(self):
        """将正则管理器的保存按钮绑定为主程序保存方法"""
        [btn.config(command=self.save_app_settings)
        for child in self.regex_manager.frame.winfo_children() if isinstance(child, tk.Frame)
        for btn in child.winfo_children() if isinstance(btn, tk.Button) and btn.cget("text") == "保存设置"]

    def _on_drop_epub(self, event):
        # 只处理epub拖拽加载
        self.epub_path = next((f for f in self.root.tk.splitlist(event.data) if f.lower().endswith('.epub')), None)
        if self.epub_path: logger.info(f"拖拽载入epub: {self.epub_path}")

    def open_file_dialog(self):
        self.epub_path = filedialog.askopenfilename(filetypes=[('EPUB文件', '*.epub')])
        logger.info(f"载入epub: {self.epub_path}")

    def start_conversion(self):
        if not hasattr(self, 'epub_path'): return messagebox.showwarning('警告', '请先选择EPUB文件')
        if not (fn := filedialog.asksaveasfilename(defaultextension='.epub', filetypes=[('EPUB文件', '*.epub')])): return
        logger.opt(exception=True).catch(lambda: self.process_epub(fn))()

    def batch_convert_epubs(self, epub_paths=None):
        if not (ps := epub_paths or filedialog.askopenfilenames(filetypes=[('EPUB文件', '*.epub')])): return
        out = Path(ps[0]).parent / 'output'; out.mkdir(exist_ok=True)
        counts = {'ERROR': 0, 'WARNING': 0}
        logger_id = logger.add(lambda r: counts.__setitem__(r.record["level"].name, counts[r.record["level"].name]+1) or None, level='WARNING')
        for p in ps:
            try:
                self.epub_path = p
                self.process_epub(str(out / Path(p).name))
            except Exception:
                logger.opt(exception=True).error(f"文件处理失败: {Path(p).name}")
        logger.remove(logger_id)
        logger.success(f"批量转换完成: 共{len(ps)}，ERROR:{counts['ERROR']}，WARNING:{counts['WARNING']}")

    def _fmt(self, soup):
        from bs4.formatter import HTMLFormatter, EntitySubstitution
        sub_func = getattr(EntitySubstitution, 'substitute_xml', 
                           getattr(EntitySubstitution, 'substitute_xml_entities', None))
        return soup.decode(formatter=HTMLFormatter(entity_substitution=lambda s: 
            sub_func(s).replace('\u00A0', '&#160;')))

    def process_epub(self, output_filename):
        """实际开始处理流程"""
        logger.info(f"开始处理epub文件: {self.epub_path}")
        class_name = self.class_name_var.get()

        with tempfile.TemporaryDirectory(dir=self.sesame_root) as temp_dir:
            logger.info(f"解压临时目录: {temp_dir}")
            with zipfile.ZipFile(self.epub_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            # 解析 container.xml，找到 .opf 文件路径
            opf_full_path = self._get_opf_path(temp_dir)
            logger.debug(f"OPF文件路径: {opf_full_path}")

            # 图片转换
            if self.convert_images_var.get():
                self.convert_epub_images(temp_dir)

            # 删除自带样式并添加自定义样式表并更新opf跟页面引用 更改opf语言标识 跟原逻辑独立两个开关
            self.process_opf_and_styles(temp_dir)

            opf_path = self._get_opf_path(temp_dir)
            # 生成ncx并更新opf
            if self.generate_ncx_enabled.get():
                success, msg = EpubNCXGenerator.generate_ncx(opf_path)
                if not success: logger.warning(f"NCX生成警告: {msg}")

            # 调用fix_ncx_paths并传递 目录偏移、强制偏移、补全あとが 开关状态
            EpubNCXGenerator.fix_ncx_paths(opf_path, self.ncx_offset_enabled.get(), self.ncx_atokagi_enabled.get(), self.ncx_manual_offset_val.get())

            # 转换epub版本并删除nav
            if self.convert_epub_version_enabled.get():
                success, msg = EpubNCXGenerator.convert_to_epub2(opf_path)
                if not success:
                    logger.warning(f"版本转换警告: {msg}")

            # 重新解析目录 正则匹配追加、分割章节
            opf_path = self._get_opf_path(temp_dir)
            toc_data = self._parse_toc(BeautifulSoup(opf_path.read_text('utf-8'), 'xml'), opf_path)
            self._apply_regex_split(temp_dir, toc_data)

            # 章节间合并
            if self.merge_xhtml_enabled.get():
                self.merge_xhtml_files(temp_dir)

            # 遍历所有文件并处理
            for xf in Path(temp_dir).rglob("*"):
                    if xf.suffix.lower() in ('.xhtml', '.html'):
                        soup = BeautifulSoup(xf.read_text('u8'), 'html.parser')
                        # ruby规格化 傍点转ruby
                        if self.process_ruby_enabled.get(): self.process_ruby(soup)
                        if self.modify_html_enabled.get(): self.modify_html(soup, class_name)

                        # 正则替换
                        content = self.regex_manager.apply_rules(self._fmt(soup))

                        # 图片标签处理
                        if self.process_images_enabled.get():
                            soup_img = BeautifulSoup(content, 'html.parser')
                            self.post_process_images(soup_img)
                            content = self._fmt(soup_img)
                        xf.write_text(content, 'u8')

            [logger.info(msg) for var, msg in [
                (self.process_ruby_enabled, "Ruby标签规格化 √"),
                (self.modify_html_enabled, "傍点转换ruby格式 √"),
                (None, "正则替换 √"), 
                (self.process_images_enabled, "图片标签规格化 √")
            ] if var is None or var.get()]

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
        temp_dir, opf_path = Path(temp_dir), self._get_opf_path(Path(temp_dir))
        opf_soup = BeautifulSoup(opf_path.read_text('u8'), 'xml')
        # 获取开关状态
        is_lang_enabled, is_style_enabled = self.set_lang_enabled.get(), self.delete_style_enabled.get()
        # 获取并修改语言标识
        set_var = self._settings_vars_dict.get('set_lang_var')
        lang_val = set_var.get().strip() if set_var and hasattr(set_var, 'get') and set_var.get().strip() else "ja"

        if is_lang_enabled:
            tag = opf_soup.find('dc:language') or (opf_soup.metadata.append(opf_soup.new_tag('dc:language')) or opf_soup.find('dc:language'))
            orig = ' '.join(tag.string.split()) if tag.string else "未定义"
            tag.string = lang_val; logger.info(f"语言标识：{orig} -> {lang_val}")

        if is_style_enabled:
            # 1. 清理 OPF 属性与 CSS 引用
            if (spine := opf_soup.find('spine')) and 'page-progression-direction' in spine.attrs: del spine['page-progression-direction']
            [item.decompose() for item in opf_soup.find_all('item', attrs={'media-type': 'text/css'})]
            css_dir = opf_path.parent / 'css'
            if not css_dir.exists(): 
                css_dir.mkdir(parents=True, exist_ok=True); logger.debug(f"确保 CSS 目标目录存在: {css_dir}")
            if manifest := opf_soup.find('manifest'):
                manifest.append(opf_soup.new_tag('item', href='css/style.css', id='style-css', **{'media-type': 'text/css'}))
            # 2. 删除原 CSS 并添加自定义 style.css 文件
            deleted = sum(1 for f in temp_dir.rglob('*.css') if not f.unlink())
            logger.debug(f"已删除 {deleted} 个原 CSS 文件")
            base_path = Path(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.argv[0]))))
            if (custom_src := base_path / 'style.css').exists():
                shutil.copy2(custom_src, css_dir / 'style.css')
                try:
                    temp_s = getattr(self, 'temp_style_content', '').strip()
                    if temp_s:
                        with open(css_dir / 'style.css', "a", encoding="u8") as f: f.write(f"\n{temp_s}")
                        logger.info("已追加临时样式到 style.css")
                    else: logger.debug("临时样式为空，未追加")
                    logger.success("添加style.css 完成")
                except Exception as e: logger.error(f"获取临时样式失败: {e}")
            # 3. XHTML 规格化头部信息与CSS重建
            xhtml_count = 0
            for xf in [f for f in temp_dir.rglob("*") if f.suffix.lower() in ('.xhtml', '.html')]:
                soup = BeautifulSoup(xf.read_text('u8'), 'lxml-xml')
                html = soup.find('html') or soup.append(soup.new_tag('html')) or soup.find('html')
                # 规格化 HTML 属性
                html.attrs = {'xmlns': "http://www.w3.org/1999/xhtml", 'xmlns:epub': "http://www.idpf.org/2007/ops", 'xml:lang': lang_val}
                # 重建 Head 信息
                title_str = soup.title.string.strip() if soup.title and soup.title.string else ""
                head = soup.head or html.insert(0, soup.new_tag('head')) or soup.head
                head.clear()
                rel_css = os.path.relpath(css_dir / 'style.css', xf.parent).replace('\\', '/')
                for node in [NavigableString('\n'), soup.new_tag('title'), NavigableString('\n'), 
                             soup.new_tag('link', rel='stylesheet', type='text/css', href=rel_css), NavigableString('\n')]:
                    if node.name == 'title': node.string = title_str
                    head.append(node)
                if soup.body: [s.decompose() for s in soup.body.select('script')]
                # 写入带规格化头的源码
                xf.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n\n{html.decode(formatter="minimal")}', 'u8')
                xhtml_count += 1
            logger.info(f"已更新 {xhtml_count} 个Xhtml头部信息与样式链接")
        # 去除metadata下子标签文本首尾的换行跟空格
        if opf_soup.metadata: [setattr(t, 'string', t.string.strip()) for t in opf_soup.metadata.find_all() if t.string]
        if is_lang_enabled or is_style_enabled:
            opf_path.write_text(str(opf_soup), 'u8')

    def merge_xhtml_files(self, temp_dir):
        logger.info("章节间Xhtml合并(基于目录)")
        temp_dir, opf_path = Path(temp_dir), self._get_opf_path(Path(temp_dir))
        opf_soup = BeautifulSoup(opf_path.read_text('utf-8'),'xml')
        spine = opf_soup.spine or (_ for _ in ()).throw(ValueError("OPF 文件缺少 spine 定义"))
        opf_dir = opf_path.parent

        # 构建 spine 列表
        spine_files = [(opf_dir/itm.get('href')).resolve() for ref in spine.find_all('itemref')
                    if (idr:=ref.get('idref')) and (itm:=opf_soup.find('item',id=idr))
                    and itm.get('media-type') in ['application/xhtml+xml', 'text/html']
                    and (href:=itm.get('href')) and not href.lower().endswith('nav.xhtml')]
        logger.debug(f"Spine文件列表: {spine_files}")

        toc = self._parse_toc(opf_soup,opf_path)
        logger.debug(f"目录条目: {toc}")
        toc_anchors=[]
        for e in toc:
            href = e.get('href') or ''
            title = e.get('title', '无标题')
            # 排除逻辑：1.标题和href严格相同 2.标题相同且去锚点后href相同 3.标题相同
            is_ex = (title, href) in (ex := self.excluded_toc_entries) or (title, href.split('#')[0]) in ex or any(title == t for t, _ in ex)
            if is_ex: logger.debug(f"跳过排除的目录条目: {title} | ({href})")
            f = (opf_dir / href.split('#', 1)[0]).resolve()
            if not f.exists(): logger.warning(f"目录条目文件不存在，已跳过: {title} | ({href})"); continue
            try: idx = spine_files.index(f)
            except ValueError: logger.warning(f"目录条目路径对照spine列表异常: {title} | ({href})"); continue
            toc_anchors.append((idx, title, f, is_ex)) # 将标记存入
        if not toc_anchors: return logger.warning("未找到有效目录，跳过合并")

        toc_anchors.sort(key=lambda x:x[0])
        sep=(self._settings_vars_dict.get('merge_separator_var') or type('',(),{'get':lambda s:'hr+br'})()).get()
        tags=[]if sep=='-'else(['p','hr','p']if sep=='hr+br'else['p']*(int(sep[0])if sep.endswith('br')and sep[0].isdigit()else 2))

        modified=False
        for i,(s,_,m,is_ex) in enumerate(toc_anchors):
            if is_ex: continue # 仅作为合并边界 不作为发起者合并后续章节
            g=spine_files[s:(toc_anchors[i+1][0] if i+1<len(toc_anchors) else len(spine_files))]
            if len(g)<2: continue
            logger.debug(f"合并于: {g[0].relative_to(temp_dir).as_posix()} 已合并: {[x.relative_to(temp_dir).as_posix() for x in g[1:]]}")
            ms=BeautifulSoup(m.read_text('utf-8'),'html.parser')
            for sub in g[1:]:
                if not sub.exists(): logger.warning(f"合并目标文件不存在，已跳过: {sub}"); continue
                mg = BeautifulSoup(sub.read_text('utf-8'), 'html.parser')
                if not getattr(mg, 'body', None): logger.warning(f"缺失body: {sub}"); continue
                for t in tags: el=ms.new_tag(t); t=='p' and el.append(ms.new_tag('br')); ms.body.append(el); ms.body.append(ms.new_string('\n'))
                [ms.body.append(copy.copy(c)) for c in mg.body.children if c.name!='script']
                sub.unlink(missing_ok=True)
                rel=sub.relative_to(opf_dir).as_posix()
                if (it:=opf_soup.find('item',href=rel)):
                    [tg.decompose() for tg in [*spine.find_all('itemref',idref=it.get('id')),it]]; modified=True
            m.write_text(str(ms),'utf-8')
        (opf_path.write_text(str(opf_soup),'utf-8'),logger.info("章节间Xhtml合并 完成")) if modified else logger.info("无需更新 OPF，无章节被合并")

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
            file_path.write_text(self._fmt(soup), encoding='utf-8')

    def _get_opf_path(self, temp_dir):
        """解析container.xml 准确获取opf名字路径"""
        container_path = Path(temp_dir) / 'META-INF' / 'container.xml'
        with container_path.open('r', encoding='utf-8') as f:
            container_content = f.read()
        soup = BeautifulSoup(container_content, 'xml')
        rootfile = soup.find('rootfile')
        if not rootfile or not rootfile.get('full-path'):
            raise ValueError("未找到 .opf 文件路径")
        return Path(temp_dir) / rootfile['full-path']

    def _parse_toc(self, opf_soup, opf_path):
        """解析目录结构 优先nav 后解析ncx"""
        # nav
        if (nav_item := opf_soup.find('item', properties='nav')) and (nav_path := (opf_path.parent / nav_item['href']).resolve()).exists():
            with nav_path.open('r', encoding='utf-8') as f:
                nav_soup = BeautifulSoup(f.read(), 'html.parser')
            if (nav_tag := nav_soup.find('nav', attrs={'epub:type': 'toc'}) or 
                        nav_soup.find('nav', attrs={'role': 'doc-toc'}) or
                        nav_soup.find('nav', id='toc')):
                return [
                    {'title': a.text.strip(), 'href': a['href'].split('#')[0], 
                     'depth': len(a.find_parents('li')) - 1}
                    for a in nav_tag.find_all('a', href=True)]
        # ncx
        if (ncx_item := opf_soup.find('item', attrs={"media-type": "application/x-dtbncx+xml"})) and (ncx_path := (opf_path.parent / ncx_item['href']).resolve()).exists():
            with ncx_path.open('r', encoding='utf-8') as f:
                ncx_soup = BeautifulSoup(f.read(), 'xml')
            if nav_map := ncx_soup.find('navMap'):
                return [
                    {'title': nav_point.find('navLabel').text.strip(), 'href': nav_point.find('content')['src'],
                     'depth': len(nav_point.find_parents('navPoint'))}
                    for nav_point in nav_map.find_all('navPoint')]
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
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=self.sesame_root) as list_file:
                list_file.write('\n'.join(original_images))
                list_path = list_file.name
            logger.debug(f"[转换] 生成临时列表文件: {list_path}")
            cmd = [str(converter_path), "-i", f"@{list_path}", *params]
            try:
                logger.debug("[转换] 执行命令: " + ' '.join(cmd))
                result = subprocess.run(cmd, cwd=temp_dir, capture_output=True, check=True, encoding='utf-8', errors='replace')
                out = result.stdout or ""
                logger.debug("[转换] 输出日志:\n" + out)
                m = re.search(r"成功\s*(\d+)/(\d+)", out)
                success, total = m.groups() if m else ("0", "0")
                logger.success(f"图片转换成功: {success}/{total}")
            except subprocess.CalledProcessError as e:
                err = e.stderr.decode('utf-8', errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or "")
                logger.error(f"[错误] 转换失败:\n{err}")
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
                        logger.info("图片OPF清单 无需修改")
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
        # 合并连续的ruby标签
        ruby_tags, i = soup.find_all('ruby'), 0
        while i < len(ruby_tags) - 1:
            c, n = ruby_tags[i], ruby_tags[i + 1]
            # 检查两个ruby标签是否相邻
            s = c.next_sibling
            while s and (not getattr(s, 'name', None)) and not s.strip(): s = s.next_sibling
            if s is n:
                [c.append(x) for x in list(n.contents)]  # 合并两个ruby标签
                n.decompose()
                ruby_tags.pop(i + 1)  #  性能优化：更新本地列表
            else: i += 1
        for ruby_tag in ruby_tags:  # 遍历所有ruby标签
            rt_tags = ruby_tag.find_all('rt')
            if rt_tags and rt_tags[0].get_text(strip=True).startswith('・'):continue  # 跳过rt标签后以・开头则跳过的ruby
            img_tags = ruby_tag.find_all('img')  # 查找ruby内所有img标签
            merged_content = ''.join(t for t in [rt.get_text(strip=True) for rt in rt_tags] if t)  # 合并 <rt> 标签 忽略所有的嵌套标签
            for rt in rt_tags: rt.extract()  # 删除残留的原rt标签
            for rb in ruby_tag.find_all('rb'): rb.unwrap() # 删除全部rb标签
            if img_tags:  # ruby内含图片的处理
                for child in list(ruby_tag.contents):  # 遍历ruby内所有子节点
                    if getattr(child, 'name', None) != 'rt':  # 除rt标签内容
                        ruby_tag.insert_before(child.extract() if hasattr(child, 'extract') else child)  # 搬到ruby前面
                new_ruby = soup.new_tag('ruby')  # 创建新ruby标签
                new_ruby.string = '\u00A0'  # 空占位符 预防空标签不显示内容
                rt = soup.new_tag('rt')  # 创建新rt标签
                rt.string = merged_content if merged_content else '\u00A0'  # rt内容或空占位
                new_ruby.append(rt)  # 添加rt到ruby
                ruby_tag.replace_with(new_ruby)  # 用新ruby替换原ruby
            else:  # 正常ruby标签处理
                original_content = ruby_tag.get_text().replace('\n', '')  # 获取原内容并去除换行
                new_ruby = soup.new_tag('ruby')  # 创建新ruby标签
                new_ruby.string = original_content  # 设置ruby正文
                rt = soup.new_tag('rt')  # 创建新rt标签
                rt.string = merged_content  # 设置rt内容
                new_ruby.append(rt)  # 添加rt到ruby
                ruby_tag.replace_with(new_ruby)  # 用新ruby替换原ruby

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

    def modify_html(self, soup, class_names):
        classes = [c.strip() for c in class_names.split('|') if c.strip()]
        for class_name in classes:
            # 使用 soup.select 替代 re 匹配，更准确且快
            for span in soup.select(f'span[class~="{class_name}"], em[class~="{class_name}"]'): 
                # 获取纯文本，防止 span 内部有其他标签导致 .string 为空
                text_content = span.get_text() 
                if text_content:
                    ruby = soup.new_tag('ruby')
                    for char in text_content:
                        ruby.append(soup.new_string(char))
                        rt_tag = soup.new_tag('rt')
                        rt_tag.append(soup.new_string("・"))
                        ruby.append(rt_tag)
                    span.replace_with(ruby)

    def show_exclude_dialog(self):
        """章节合并排除/正则追加分割章节 对话框"""
        if not getattr(self, "epub_path", None): return messagebox.showwarning("警告", "请先选择EPUB文件")
        # 1. 环境准备与记忆初始化
        self._fcache, self._saved_hrefs = {}, {item[1] for item in getattr(self, "excluded_toc_entries", [])}
        # 使用文件修改时间和路径哈希生成唯一的临时目录，避免多次操作时的冲突
        st = Path(self.epub_path).stat()
        h_p = abs(hash(str(Path(self.epub_path).resolve())))
        ts = time.strftime("%y%m%d_%H%M%S", time.localtime(st.st_mtime))
        self._exclude_tempdir = self.sesame_root / f"epub_exclude_{h_p}_{ts}"
        self._exclude_tempdir.mkdir(parents=True, exist_ok=True)
        self._exclude_tempdirs.add(self._exclude_tempdir)
        temp_path = self._exclude_tempdir

        with zipfile.ZipFile(self.epub_path) as z: [z.extract(n, temp_path) for n in z.namelist() if n.lower().endswith(('.opf', '.ncx', '.xml', '.html', '.xhtml', '.htm'))]
        opf = self._get_opf_path(temp_path)
        EpubNCXGenerator.fix_ncx_paths(opf, self.ncx_offset_enabled.get(), self.ncx_atokagi_enabled.get(), self.ncx_manual_offset_val.get())
        self._init_toc, self._curr_toc = (t := self._parse_toc(BeautifulSoup(opf.read_text("utf-8"), "xml"), opf)), t.copy()
        if not t: return messagebox.showwarning("警告", "未找到目录条目")

        # 2. UI 构建
        dialog = tk.Toplevel(self.root); dialog.title("选择不合并条目 / 正则追加分割章节")
        self.win_size.setup(dialog, "show_exclude_dialog", f"605x600+{self.root.winfo_x()+50}+{self.root.winfo_y()+30}"); dialog.focus_force()
        main_frame = ttk.Frame(dialog); main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        tree = ttk.Treeview(main_frame, columns=("t", "h"), show="headings", selectmode="extended")
        sb = ttk.Scrollbar(main_frame, command=tree.yview); tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        [tree.heading(c, text=t) or tree.column(c, width=w) for c, t, w in [("t", "目录 (选中不合并)", 350), ("h", "HTML文件", 150)]]

        def update_mem(): 
            # 通过绑定的iid(即索引)，直接从源数据获取原始 href，避免 UI 污染
            if tree.get_children(): self._saved_hrefs = {self._curr_toc[int(i)]['href'] for i in tree.selection()}
        def refresh():
            ttk.Style().map("Treeview", foreground=[e for e in ttk.Style().map("Treeview", query_opt="foreground") if e[:2] != ("!disabled", "!selected")]) #修复py3.8 Tk8.6.9树视图tag颜色失效Bug
            tree.delete(*tree.get_children())
            tree.tag_configure("mis", font=("", 10, "overstrike"), foreground="gray") # 定义删除线样式
            ex = getattr(self, "excluded_toc_entries", [])
            for idx, e in enumerate(self._curr_toc):
                t, h = e.get('title', ''), e['href']
                # 判定：非虚拟章节且物理文件不存在时，标记为 mis
                tag = ("mis",) if "_spt_" not in h and not (opf.parent / unquote(h.split('#')[0])).exists() else ()
                iid = tree.insert("", "end", iid=str(idx), values=(("\u3000"*e.get('depth', 0)) + t, unquote(h)), tags=tag)
                # 匹配逻辑：1.记忆中的href 2.完整匹配 3.无锚点匹配 4.标题匹配
                if h in self._saved_hrefs or (t, h) in ex or (t, h.split('#')[0]) in ex or any(t == x[0] for x in ex): tree.selection_add(iid)
        def run_splits():
            update_mem(); self._curr_toc, self._split_rules = self._init_toc.copy(), []
            for cb, en in regex_entries:
                if (p := en.get().strip()): self._split_rules.append((p, '分割章节{idx}', 2 if cb.get() == "层级2(子章节)" else 1))
            patterns = [r[0] for r in self._split_rules]
            if patterns and (nt := self._internal_split_logic(patterns, self._curr_toc, temp_path, self._split_rules)): self._curr_toc = nt
            refresh()
        # 正则输入区
        regex_entries, reg_frame = [], ttk.Frame(dialog); reg_frame.pack(fill="x", padx=5)
        def add_row(txt="", level=2):
            row = ttk.Frame(reg_frame); row.pack(fill="x", pady=1)
            cb = ttk.Combobox(row, values=("层级2(子章节)", "层级1(同级)"), state="readonly", width=12)
            cb.pack(side="left", padx=2); cb.set("层级2(子章节)" if level == 2 else "层级1(同级)")
            en = tk.Entry(row); en.pack(side="left", fill="x", expand=True, padx=2); en.insert(0, txt); regex_entries.append((cb, en))
            m = tk.Menu(dialog, tearoff=0); m.add_command(label="新增正则框", command=add_row)
            m.add_command(label="删除正则条目", command=lambda: [row.destroy(), regex_entries.remove((cb, en)), run_splits()] if len(regex_entries)>1 else [en.delete(0, 'end'), run_splits()])
            m.add_command(label="粘贴并预览", command=lambda: [en.delete(0, 'end'), en.insert(0, dialog.clipboard_get()), run_splits()])
            en.bind("<Button-3>", lambda e: m.post(e.x_root, e.y_root)); en.bind("<Return>", lambda e: run_splits()); cb.bind("<<ComboboxSelected>>", lambda e: run_splits())
        tree.bind("<Button-1>", lambda e: (i:=tree.identify_row(e.y)) and [tree.selection_remove(i) if i in tree.selection() else tree.selection_add(i), update_mem()] and "break")
        [add_row(r, getattr(self, "_saved_regex_levels", [])[i] if len(getattr(self, "_saved_regex_levels", [])) > i else 2) for i, r in enumerate(getattr(self, "_saved_regex_list", []) or [""])]; run_splits()
        # 底部按钮
        btn_frame = ttk.Frame(dialog); btn_frame.pack(side="bottom", fill="x", pady=10)
        inner_box = ttk.Frame(btn_frame); inner_box.pack(anchor="center")
        ttk.Button(inner_box, text="预览全部正则追加、分割章节", command=run_splits).pack(side="left", padx=5)
        def on_confirm():
            run_splits(); update_mem()
            self._saved_regex_list = [en.get().strip() for _, en in regex_entries if en.get().strip()] or [""]
            self._saved_regex_levels = [2 if cb.get() == "层级2(子章节)" else 1 for cb, en in regex_entries]
            exist = {i[1] for i in getattr(self, "excluded_toc_entries", [])}
            # 通过iid(索引)直接从_curr_toc提取原始title和href
            new_items = [(self._curr_toc[int(i)].get('title', ''), self._curr_toc[int(i)]['href']) for i in tree.selection()]
            self.excluded_toc_entries = getattr(self, "excluded_toc_entries", []) + [x for x in new_items if x[1] not in exist]
            self.toc_data = self._curr_toc; dialog.destroy()
        ttk.Button(inner_box, text="追加排除条目/正则追加&分割子章节", command=on_confirm).pack(side="left", padx=5)

    def _get_spine_ordered_files(self, opf_path):
        """获取按 Spine 顺序排列的 HTML 文件列表"""
        soup = BeautifulSoup(opf_path.read_text('utf-8'), 'xml')
        m = {it['id']: it['href'] for it in soup.find('manifest').find_all('item')}
        return [f for r in soup.find('spine').find_all('itemref') if (idr := r.get('idref')) and (href := m.get(idr)) and (f := opf_path.parent / href)
                and f.exists() and f.suffix.lower() in ['.html', '.xhtml', '.htm']]

    def _clean_title(self, html_fragment):
        """统一标题清洗 处理多余标签及空格"""
        soup = BeautifulSoup(html_fragment, 'html.parser')
        for img in soup.find_all('img'): img.decompose() # 移除所有图片标签，避免 alt 属性干扰标题
        t = soup.get_text().replace('\u3000', ' ').replace('\xa0', ' ').strip() # 直接取 text，并处理全角/半角空格
        return ' '.join(t.split()) # 将多个连续空格合并为一个

    def _internal_split_logic(self, patterns, current_toc, temp_dir, split_rules=None):
        """章节分割预览逻辑"""
        try: rules = re.compile("|".join(f"(?:{p})" for p in patterns))
        except: return None
        if not hasattr(self, "_fcache"): self._fcache = {}
        opf, new_toc, dep, s_rules = self._get_opf_path(Path(temp_dir)), [], 0, split_rules or []
        lookup = {t['href'].split('#')[0].split('/')[-1]: t for t in current_toc}
        for hf in self._get_spine_ordered_files(opf):
            if (n := hf.name) in lookup: new_toc.append(e := lookup.pop(n)); dep = e.get('depth', 0)
            if not hf.exists(): continue
            if n not in self._fcache: self._fcache[n] = hf.read_text('utf-8', 'ignore')
            if rules.search(c := self._fcache[n]):
                for i, m in enumerate(rules.finditer(c), 1):
                    # 匹配层级(1=同级, 2=子级)，计算相对深度
                    matched = m.group()
                    lvl = next((r[2] for r in s_rules if re.search(f"(?:{r[0]})", matched)), 2)
                    new_toc.append({'title': self._clean_title(matched) or f"Sec {i}", 
                                    'href': f"{hf.stem}_spt_{i:03d}.xhtml", 'depth': dep + lvl - 1})
        for remain_node in lookup.values(): new_toc.append(remain_node) # 保留失效条目
        return new_toc

    def _apply_regex_split(self, temp_dir, current_toc=None):
        """正则匹配子章节追加分割逻辑"""
        if not (rules := getattr(self, '_split_rules', [])): return current_toc
        opf_p, total = self._get_opf_path(Path(temp_dir)), 0
        last_href = current_toc[0]['href'] if current_toc else None
        regex = re.compile("|".join(f"(?:{r[0]})" for r in rules if r))
        lookup = {Path(t['href'].split('#')[0]).name: t for t in (current_toc or [])}
        TPL = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n\n'
               '<html xml:lang="{l}" xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
               '<head>\n<title>{t}</title>\n<link href="../css/style.css" rel="stylesheet" type="text/css"/>\n</head>\n'
               '<body>\n{c}\n</body>\n</html>')
        for hf in self._get_spine_ordered_files(opf_p):
            if (n := hf.name) in lookup:
                last_href = lookup[n]['href']; logger.debug(f"父级起始锚点: {n} -> {last_href}")
            raw = hf.read_text('utf-8', 'ignore')
            if not (ms := list(regex.finditer(raw))) or not last_href: continue
            # 提取元数据：如果原文件有则用原文件的，没有则默认 ja
            title = (re.search(r'<title>(.*?)</title>', raw, re.I) or [0, "Chapter"])[1]
            lang = (re.search(r'xml:lang="(.*?)"', raw, re.I) or [0, "ja"])[1]
            logger.debug(f"正在分割文件: {n} | 当前锚点: {last_href}")
            hf.write_text(raw[:ms[0].start()].split("</body>")[0] + "\n</body>\n</html>", 'utf-8')
            ivs, subs, cur_h = [m.start() for m in ms] + [len(raw)], [], hf.relative_to(opf_p.parent).as_posix()
            # 依规则原序提取子章节信息 (1=同级/父, 2=子级)
            for i, m in enumerate(ms):
                new_f = hf.parent / f"{hf.stem}_spt_{i+1:03d}.xhtml"
                new_f.write_text(TPL.format(l=lang, t=title, c=raw[ivs[i]:ivs[i+1]].split("</body>")[0].strip()), 'utf-8')
                # 直接从 rules 匹配层级 (r[0]=pattern, r[2]=level)，匹配不到则默认为 2
                matched = m.group()
                s = {
                    'id': new_f.stem.replace('.', '_'), 
                    'href': new_f.relative_to(opf_p.parent).as_posix(), 
                    'title': self._clean_title(matched), 
                    'depth': next((r[2] for r in rules if re.search(f"(?:{r[0]})", matched)), 2)
                }
                subs.append(s)
                logger.debug(f"匹配条目: 标题={s['title']}, 层级={s['depth']}, 文件={new_f.name}")
            # OPF 原位插入(Manifest 紧跟原文件，Spine 保持顺序)
            soup = BeautifulSoup(opf_p.read_text('utf-8'), 'xml')
            if (old_it := soup.find('item', href=cur_h)) and (old_rf := soup.find('itemref', idref=old_it['id'])):
                for s in subs:
                    if not soup.find('item', id=s['id']):
                        old_it.insert_after(ni := soup.new_tag('item', id=s['id'], href=s['href'], **{'media-type': 'application/xhtml+xml'}))
                        old_it = ni
                [old_rf.insert_after(soup.new_tag('itemref', idref=s['id'])) for s in reversed(subs)]
                opf_p.write_text(str(soup), 'utf-8')
            try:
                if (added := EpubNCXGenerator.insert_sub_chapters(opf_p, last_href, subs)):
                    total += added
                    # 锚点更新逻辑：反向查找最后一个同级(depth=1)节点，规避全量列表生成跟层级塌陷.depth=2次级节点不更新，保持原父级锚点
                    if (l1_href := next((s['href'] for s in reversed(subs) if s.get('depth', 2) == 1), None)):
                        old_h, last_href = last_href, l1_href
                        logger.debug(f"锚点更新(同级): {old_h} -> {last_href} (新增 {added} 章节)")
            except Exception as e: logger.error(f"插入章节失败: {e}")
        if total > 0: logger.info(f"追加/分割章节完成: 共 {total} 条子章节")
        return current_toc

    def show_exclude_list_dialog(self):
        """章节合并排除的列表管理"""
        if not hasattr(self, '_exclude_initialized'):
            file_data = []
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    content = f.read().split('[ExcludeTocEntries]', 1)[-1].split('[', 1)[0]
                    for line in (l.strip() for l in content.splitlines() if '|' in l):
                        parts = (line.split('=', 1)[-1] if '=' in line else line).split('|', 1)
                        file_data.append(tuple(p.strip() for p in parts))
            except: pass
            mem = getattr(self, 'excluded_toc_entries', []) or []
            # 初始合并去重（标题+href一致才去重）
            self.excluded_toc_entries = mem + [i for i in file_data if i not in mem]
            self._exclude_initialized = True

        d = tk.Toplevel(self.root); d.title("已排除的合并章节列表")
        self.win_size.setup(d, "show_exclude_list", f"500x400+{self.root.winfo_x()+50}+{self.root.winfo_y()+30}"); d.focus_force()
        f_tree = ttk.Frame(d); f_tree.pack(fill="both", expand=True, padx=5, pady=5)
        tree = ttk.Treeview(f_tree, columns=("t", "h"), show="headings", selectmode="extended")
        sb = ttk.Scrollbar(f_tree, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); tree.pack(side="left", fill="both", expand=True)
        [tree.heading(c, text=t) or tree.column(c, width=w) for c, t, w in [("t", "目录名称", 300), ("h", "HTML文件", 150)]]
        [tree.insert("", "end", values=v) for v in self.excluded_toc_entries]

        sync = lambda: setattr(self, 'excluded_toc_entries', [tuple(tree.item(i, 'values')) for i in tree.get_children()])
        #拽排序逻辑
        tree.bind("<ButtonPress-1>", lambda e: setattr(tree, 's', tree.identify_row(e.y)), add='+')
        tree.bind("<B1-Motion>", lambda e: (t := tree.identify_row(e.y)) and t != getattr(tree, 's', '') and (tree.move(tree.s, '', tree.index(t)), sync()))
        def edit(e):
            if not (item := tree.identify_row(e.y)) or not (col := tree.identify_column(e.x)): return
            idx, (x, y, w, h) = int(col[1:])-1, tree.bbox(item, col)
            ent = tk.Entry(tree, relief="flat", highlightthickness=1); ent.place(x=x, y=y, width=w, height=h)
            ent.insert(0, tree.item(item, 'values')[idx]); ent.focus()
            save = lambda *a: [tree.item(item, values=[(ent.get() if i==idx else v) for i,v in enumerate(tree.item(item,'values'))]), ent.destroy(), sync()]
            ent.bind('<Return>', save); ent.bind('<FocusOut>', save)
        menu = tk.Menu(d, tearoff=0)
        menu.add_command(label="添加空白条目", command=lambda: [tree.insert('', 'end', values=("1", "1")), sync()])
        menu.add_command(label="删除选中条目", command=lambda: [tree.delete(*tree.selection()), sync()])
        tree.bind('<Double-1>', edit); tree.bind('<Delete>', lambda e: menu.invoke(1))
        tree.bind('<Button-3>', lambda e: [tree.selection_set(r) if not tree.selection() and (r:=tree.identify_row(e.y)) else None, menu.post(e.x_root, e.y_root)])
        ttk.Button(d, text="关闭", command=d.destroy).pack(side="bottom", pady=5)

    def show_class_list(self):
        """class样式收集分析对话框"""
        if not getattr(self, 'epub_path', None): return messagebox.showwarning("警告", "请先选择EPUB文件")
        if not hasattr(self, 'temp_style_content'): self.temp_style_content = ""
        ClassList(self.root, self.epub_path, 
                  lambda: self.temp_style_content, 
                  lambda v: setattr(self, 'temp_style_content', v), 
                  lambda v: setattr(self, 'temp_style_content', self.temp_style_content + v),
                  self.win_size)

    def save_app_settings(self, return_config=False):
        """保存到配置文件，或返回ConfigParser对象"""
        config = configparser.ConfigParser()
        config['AppSettings'] = {k: str(v.get()) for k, v in self._settings_vars_dict.items()}
        if self.win_size._states: self.win_size.save(config)
        if entries := getattr(self, 'excluded_toc_entries', []):
            config['ExcludeTocEntries'] = {str(i): f"{t}|{h}" for i, (t, h) in enumerate(entries)}
        if return_config: return config
        try:
            import io
            with io.StringIO() as buf:
                config.write(buf)
                content = f"{buf.getvalue().strip()}\n\n{self.regex_manager.get_rules_content()}\n"
            self.config_file.write_text(content, encoding='utf-8')
            logger.info(f"设置已保存到: {self.config_file}")
        except Exception as e: logger.error(f"保存设置失败: {e}")

    def load_app_settings(self):
        """用 configparser 读取配置（跳过正则段）"""
        if not self.config_file.exists(): return logger.warning(f"配置文件不存在: {self.config_file}")
        try:
            config = configparser.ConfigParser()
            config.read_string(self.config_file.read_text('utf-8').split('[RegexRules]', 1)[0])
            if 'AppSettings' in config:
                sec = config['AppSettings']
                for name, var in self._settings_vars_dict.items():
                    if name in sec:
                        var.set(sec.getboolean(name) if isinstance(var, tk.BooleanVar) else sec[name])
            if 'ExcludeTocEntries' in config:
                self.excluded_toc_entries = [tuple(v.split('|', 1)) for _, v in config.items('ExcludeTocEntries') if '|' in v]
            if hasattr(self, 'regex_manager'): self.regex_manager.set_log_level(self.log_level_var.get())
            logger.info(f"加载配置:[{self.log_level_var.get()}] {self.config_file}")
        except Exception as e: logger.error(f"加载配置失败: {e}")

    def reset_app_settings(self):
        """重置所有设置为控件默认值，并重置正则规则"""
        [var.set(True) if isinstance(var, tk.BooleanVar)
        else var.set(
            'em-sesame|em-dot' if name == 'class_name_var' else
            "-f webp -q80 -H1300 -W1200 -s1.0 -A -w8" if name == 'image_params_var' else
            'hr+br' if name == 'merge_separator_var' else
            '-' if name == 'merge_remove_blank_lines_var' else
            '3' if name == 'merge_limit_blank_lines_var' else
            '')
        for name, var in self._settings_vars_dict.items()]
        # 同步重置正则规则
        if hasattr(self, 'regex_manager'): self.regex_manager.reset_to_default()

class WinSize:
    def __init__(self, config_file=None):
        self._states = {}
        if config_file and config_file.exists():
            try:
                c = config_file.read_text('utf-8').split('[WinSize]', 1)[1].split('[', 1)[0]
                self._states = {k.strip(): v.strip() for l in c.splitlines() if '=' in l for k, v in [l.split('=', 1)]}
            except Exception: pass

    def clear(self):
        self._states = {}
        logger.info("已清空内存WinSize数值")

    def setup(self, window, key, default_geom, mode=None):
        """应用位置并返回专属防抖记录器。内部自动处理坐标纠偏与边界保底。"""
        geom = self._states.get(key) or default_geom
        if (m := __import__('re').match(r"(\d+)x(\d+)([-+]-?\d+)([-+]-?\d+)", geom)):
            w, h, x, y = [int(s.replace('+-', '-').replace('-+', '-').replace('--', '+')) for s in m.groups()]
            sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
            # 级联逻辑
            if mode == 'cascade':
                ex = {(t.winfo_x(), t.winfo_y()) for t in window.master.winfo_children() if isinstance(t, tk.Toplevel) and t != window}
                while any(abs(x - ex_x) < 10 and abs(y - ex_y) < 10 for ex_x, ex_y in ex): x, y = x + 30, y + 30
            # 3. 边界保底：强制回正，防止记录负值
            x, y = max(0, min(x, sw - w)), max(0, min(y, sh - h))
            try: window.geometry(f"{w}x{h}+{x}+{y}")
            except Exception: pass

        def recorder(e=None):
            # 闭包记录器 自带过滤和防抖
            if e and e.widget != window: return
            if hasattr(window, '_ds'): window.after_cancel(window._ds)
            window._ds = window.after(500, lambda: self._states.update({key: window.winfo_geometry()}) if window.winfo_exists() else None)
        return recorder

    def save(self, config):
        config['WinSize'] = self._states

if __name__ == "__main__":
    logger.info("程序初始化")
    root = TkinterDnD.Tk()
    processor = EpubProcessor(root)
    atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in getattr(processor, '_exclude_tempdirs', set())])
    logger.info("进入主循环")
    root.mainloop()
