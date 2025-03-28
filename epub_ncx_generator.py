﻿import os
import re
import uuid
from bs4 import BeautifulSoup

class EpubNCXGenerator:
    @staticmethod
    def generate_ncx(opf_path):
        """
        基于NAV文件生成精确的NCX目录
        返回：(success, message)
        """
        try:
            opf_dir = os.path.dirname(opf_path)
            ncx_path = os.path.join(opf_dir, 'toc.ncx')
            nav_path = EpubNCXGenerator._find_nav_path(opf_path)
            # 检查ncx nav是否存在
            if os.path.exists(ncx_path):
                return True, "toc.ncx已存在，跳过生成"
            if not nav_path:
                return False, "未找到有效的NAV文件"

            # 解析NAV文件获取目录结构
            toc_entries = EpubNCXGenerator._parse_nav(nav_path, opf_dir)
            
            # 生成NCX内容
            ncx_path = os.path.join(opf_dir, 'toc.ncx')
            uid = EpubNCXGenerator._get_uid_from_opf(opf_path)
            
            with open(ncx_path, 'w', encoding='utf-8') as f:
                f.write(EpubNCXGenerator._create_ncx_content(uid, toc_entries))

            # 更新OPF引用
            EpubNCXGenerator._update_opf_reference(opf_path)
            
            return True, "NCX生成成功（基于NAV）"
        except Exception as e:
            return False, f"NCX生成失败: {str(e)}"

    @staticmethod
    def convert_to_epub2(opf_path):
        """修改epub版本号为2"""
        try:
            with open(opf_path, 'r+', encoding='utf-8') as f:
                content = f.read()
                # 修改package声明
                content = re.sub(
                    r'<package[^>]+>',
                    '<package version="2.0" unique-identifier="BookId" xmlns="http://www.idpf.org/2007/opf">',
                    content
                )
                content = re.sub(r'\s+prefix="[^"]+"', '', content)  # 确保删除epub3.0 prefix 属性
                # 写回文件
                f.seek(0)
                f.write(content)
                f.truncate()
            return True, "修改epub版本号成功"
        except Exception as e:
            return False, f"修改epub版本号失败: {str(e)}"

    @staticmethod
    def _find_nav_path(opf_path):
        """智能查找导航文件路径"""
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        
        # 查找EPUB3导航文件
        nav_item = opf_soup.find('item', {'properties': 'nav'})
        
        # 查找EPUB2 NCX文件
        if not nav_item:
            nav_item = opf_soup.find('item', {'media-type': 'application/x-dtbncx+xml'})
        
        if not nav_item:
            return None
        
        opf_dir = os.path.dirname(opf_path)
        nav_href = nav_item.get('href', '')
        nav_path = os.path.normpath(os.path.join(opf_dir, nav_href))
        
        # 验证文件存在性
        if not os.path.exists(nav_path):
            print(f"警告：导航文件不存在 {nav_path}")
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
                    full_path = os.path.normpath(os.path.join(base_dir, href))
                    entry = {
                        'title': a.get_text(strip=True),
                        'href': href,
                        'file_path': full_path,
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
    def _create_ncx_content(uid, toc_entries):
        nav_points = []
        play_order = 1

        def build_nav_points(entries, parent_id=None):
            nonlocal play_order
            points = []
            for entry in entries:
                point_id = f"navPoint-{play_order}"
                nav_point = f'''
                <navPoint id="{point_id}" playOrder="{play_order}"{' parent="' + parent_id + '"' if parent_id else ''}>
                    <navLabel><text>{entry['title']}</text></navLabel>
                    <content src="{entry['href']}"/>'''
                play_order += 1
                if entry['children']:
                    child_points = build_nav_points(entry['children'], point_id)
                    nav_point += f'\n{"".join(child_points)}\n            </navPoint>'
                else:
                    nav_point += '\n            </navPoint>'
                points.append(nav_point)
            return points

        nav_points = build_nav_points(toc_entries)
        return f'''<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
    <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <head>
    <meta name="dtb:uid" content="{uid}"/>
    <meta name="dtb:depth" content="{max(e['depth'] for e in toc_entries) if toc_entries else 1}"/>
    </head>
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
        
        # 添加新NCX引用（关键修正）
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