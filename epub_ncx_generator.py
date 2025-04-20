import os
import re
import uuid
from pathlib import Path
from bs4 import BeautifulSoup
from loguru import logger

class EpubNCXGenerator:
    @staticmethod
    def generate_ncx(opf_path):
        """
        基于NAV文件生成精确的NCX目录
        返回：(success, message)
        """
        try:
            opf_dir = Path(opf_path).parent
            ncx_path = opf_dir / 'toc.ncx'
            nav_path = EpubNCXGenerator._find_nav_path(opf_path)
            if ncx_path.exists():
                logger.info("toc.ncx已存在，跳过生成")
                return True, "toc.ncx已存在，跳过生成"
            if not nav_path:
                logger.error("未找到有效的NAV文件")
                return False, "未找到有效的NAV文件"
            # 解析NAV文件获取目录结构
            toc_entries = EpubNCXGenerator._parse_nav(nav_path, opf_dir)
            # 生成NCX内容
            ncx_path = opf_dir / 'toc.ncx'
            uid = EpubNCXGenerator._get_uid_from_opf(opf_path)
            book_title = EpubNCXGenerator._get_book_title_from_opf(opf_path)
            
            with open(ncx_path, 'w', encoding='utf-8') as f:
                f.write(EpubNCXGenerator._create_ncx_content(uid, toc_entries, book_title))

            # 更新OPF引用
            EpubNCXGenerator._update_opf_reference(opf_path)
            
            logger.success("ncx生成成功（基于NAV）")
            return True, "ncx生成成功（基于NAV）"
        except Exception as e:
            logger.error(f"ncx生成失败: {str(e)}")
            return False, f"ncx生成失败: {str(e)}"

    @staticmethod
    def _get_book_title_from_opf(opf_path):
        """从OPF文件解析dc:title作为书籍标题"""
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        title_tag = opf_soup.find('dc:title')
        return title_tag.get_text(strip=True) if title_tag else "Unknown Title"

    @staticmethod
    def convert_to_epub2(opf_path):
        """修改epub版本为2.0，并删除 nav.xhtml，并确保epub2.0 cover声明"""
        try:
            opf_path = Path(opf_path)
            content = opf_path.read_text(encoding='utf-8')
            content = re.sub(
                r'<package[^>]+>',
                '<package version="2.0" unique-identifier="BookId" xmlns="http://www.idpf.org/2007/opf">',
                content
            )
            content = re.sub(r'\s+prefix="[^"]+"', '', content) # 删除 prefix 属性（EPUB 3 专属）
            opf_path.write_text(content, encoding='utf-8')
            soup = BeautifulSoup(content, 'xml')
            nav_item = soup.find('item', properties='nav')
            if nav_item:
                nav_path = opf_path.parent / nav_item['href']
                if nav_path.exists():
                    nav_path.unlink()
                    logger.debug(f"已删除 nav 文件: {nav_path}")
            # 查找manifest中cover图片item（优先 properties="cover-image" 的item）
            manifest = soup.find('manifest')
            cover_item = None
            if manifest:
                for item in manifest.find_all('item'):
                    if item.get('properties', '') == 'cover-image':
                        cover_item = item
                        break
                # 如果没有，再找 id=cover 或 id包含cover
                if not cover_item:
                    for item in manifest.find_all('item'):
                        if item.get('id', '').lower() == 'cover' or 'cover' in item.get('id', '').lower():
                            cover_item = item
                            break
            # 查找metadata中是否已有cover meta
            metadata = soup.find('metadata')
            has_cover_meta = False
            if metadata:
                for meta in metadata.find_all('meta'):
                    if meta.get('name') == 'cover':
                        has_cover_meta = True
                        break
            # 如果manifest有cover图片且metadata没有cover meta，则添加
            if cover_item and not has_cover_meta and metadata:
                new_meta = soup.new_tag('meta', attrs={'name': 'cover', 'content': cover_item['id']})
                metadata.append(new_meta)
                logger.debug(f"已添加epub2.0 cover meta: id={cover_item['id']}")
                opf_path.write_text(str(soup), encoding='utf-8')
            logger.success("修改epub版本号并添加cover声明√")
            return True, "修改epub版本号完毕"
        except Exception as e:
            logger.error(f"修改epub版本号失败: {e}")
            return False, f"修改epub版本号失败: {e}"

    @staticmethod
    def _find_nav_path(opf_path):
        """查找nav导航文件路径"""
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        # 查找EPUB3导航文件
        nav_item = opf_soup.find('item', {'properties': 'nav'})
        # 查找EPUB2 NCX文件
        if not nav_item:
            nav_item = opf_soup.find('item', {'media-type': 'application/x-dtbncx+xml'})
        if not nav_item:
            return None
        opf_dir = Path(opf_path).parent
        nav_href = nav_item.get('href', '')
        nav_path = (opf_dir / nav_href).resolve()
        # 验证文件存在性
        if not nav_path.exists():
            logger.warning(f"nav导航文件不存在 {nav_path}")
            return None
        
        return nav_path

    @staticmethod
    def _parse_nav(nav_path, base_dir):
        """解析NAV文件获取精确目录结构"""
        with open(nav_path, 'r', encoding='utf-8') as f:
            nav_soup = BeautifulSoup(f.read(), 'html.parser')
        
        toc_nav = nav_soup.find('nav', {'epub:type': 'toc'}) or \
                 nav_soup.find('nav', {'role': 'doc-toc'})
        
        entries = []
        current_parents = []  # 记录当前层级父节点

        def parse_nested_list(list_tag, depth=0):
            nonlocal entries, current_parents
            for li in list_tag.find_all('li', recursive=False):
                if a := li.find('a', href=True):
                    href = a['href'].split('#')[0]
                    full_path = (Path(base_dir) / href).resolve()
                    entry = {
                        'title': a.get_text(strip=True),
                        'href': href,
                        'file_path': str(full_path),
                        'depth': depth,
                        'children': []
                    }
                    if current_parents:
                        current_parents[-1]['children'].append(entry)
                    else:
                        entries.append(entry)
                    # 处理子列表
                    if child_list := li.find(['ol', 'ul']):
                        current_parents.append(entry)
                        parse_nested_list(child_list, depth + 1)
                        current_parents.pop()

        if toc_nav and (root_list := toc_nav.find(['ol', 'ul'])): 
            parse_nested_list(root_list)
        return entries

    @staticmethod
    def _create_ncx_content(uid, toc_entries, book_title):
        nav_points = []
        play_order = 1
        def calculate_max_depth(entries, current_depth=1):
            max_depth = current_depth
            for entry in entries:
                if entry['children']:
                    child_depth = calculate_max_depth(entry['children'], current_depth + 1)
                    if child_depth > max_depth:
                        max_depth = child_depth
            return max_depth
        def build_nav_points(entries, parent_id=None):
            nonlocal play_order
            points = []
            for entry in entries:
                point_id = f"navPoint-{play_order}"
                nav_point = f'''
                <navPoint id="{point_id}" playOrder="{play_order}">
                    <navLabel><text>{entry['title']}</text></navLabel>
                    <content src="{entry['href']}"/>'''
                play_order += 1
                if entry['children']:
                    child_points = build_nav_points(entry['children'], point_id)
                    nav_point += f'\n{"".join(child_points)}\n</navPoint>'
                else:
                    nav_point += '\n</navPoint>'
                points.append(nav_point)
            return points
        nav_points = build_nav_points(toc_entries)
        max_depth = calculate_max_depth(toc_entries) if toc_entries else 1
        return f'''<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
    <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <head>
    <meta content="{uid}" name="dtb:uid"/>
    <meta content="{max_depth}" name="dtb:depth"/>
    <meta content="0" name="dtb:totalPageCount"/>
    <meta content="0" name="dtb:maxPageNumber"/>
    </head>
    <docTitle>
     <text>{book_title}</text>
    </docTitle>
    <navMap>
    {"".join(nav_points)}
    </navMap>
    </ncx>'''

    @staticmethod
    def _get_uid_from_opf(opf_path):
        """从OPF获取唯一标识符"""
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        return opf_soup.find('dc:identifier').text or f'urn:uuid:{uuid.uuid4()}'

    @staticmethod
    def _update_opf_reference(opf_path):
        """更新OPF中的NCX引用"""
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        
        # 移除旧NCX引用
        for item in opf_soup.find_all('item', {'media-type': 'application/x-dtbncx+xml'}):
            item.decompose()
        
        # 添加新NCX引用
        manifest = opf_soup.find('manifest')
        new_item = opf_soup.new_tag(
            'item',
            attrs={  # 使用 attrs 参数明确指定属性字典
                'id': 'ncx',
                'href': 'toc.ncx',
                'media-type': 'application/x-dtbncx+xml'  # 直接定义带连字符的属性名
            }
        )
        manifest.append(new_item)

        # 更新spine属性
        if spine := opf_soup.find('spine'):
            spine['toc'] = 'ncx'

        with open(opf_path, 'w', encoding='utf-8') as f:
            f.write(str(opf_soup))
