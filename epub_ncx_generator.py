import re
import uuid
import shutil
from pathlib import Path
from bs4 import BeautifulSoup
from loguru import logger

class EpubNCXGenerator:
    @staticmethod
    def generate_ncx(opf_path):
        """基于nav文件生成精确的NCX目录"""
        try:
            opf_dir = Path(opf_path).parent
            paths = EpubNCXGenerator._find_nav_path(opf_path)
            nav_path, ncx_path = paths['nav'], paths['ncx']
            target_ncx = opf_dir / 'toc.ncx'

            if ncx_path:
                # 存在ncx则确保在opf根目录下并且名称为toc
                if ncx_path.resolve() != target_ncx.resolve(): shutil.move(ncx_path, target_ncx)
                logger.debug(f"已将ncx移动到根目录: {target_ncx}")
                EpubNCXGenerator._update_opf_reference(opf_path, 'toc.ncx')
                EpubNCXGenerator.fix_ncx_paths(opf_path)
                logger.info("toc.ncx已存在，已确保OPF引用和spine跟ncx内路径正确")
                return True, "toc.ncx已存在，已确保OPF引用和spine跟ncx内路径正确"

            if nav_path:
                # 不存在ncx,解析nav文件获取目录结构并创建toc
                toc_entries = EpubNCXGenerator._parse_nav(nav_path, opf_dir)
                uid = EpubNCXGenerator._get_uid_from_opf(opf_path)
                book_title = EpubNCXGenerator._get_book_title_from_opf(opf_path)
                with open(target_ncx, 'w', encoding='utf-8') as f:
                    f.write(EpubNCXGenerator._create_ncx_content(uid, toc_entries, book_title))
                EpubNCXGenerator._update_opf_reference(opf_path, 'toc.ncx')
                EpubNCXGenerator.fix_ncx_paths(opf_path)
                logger.success("ncx生成成功（基于nav）")
                return True, "ncx生成成功（基于nav）"

            logger.error("未找到有效的nav跟ncx文件")
            return False, "未找到有效的nav跟ncx文件"
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
                # 寻找epub根目录（包含mimetype文件的目录）
                epub_root = opf_path.parent
                for parent in opf_path.parents:
                    if (parent / 'mimetype').exists():
                        epub_root = parent
                        break
                # 递归删除epub根目录下所有 .bw 文件
                for bw_file in epub_root.rglob('*.bw'):
                    bw_file.unlink()
                    logger.debug(f"已删除 .bw 文件: {bw_file}")
                # 递归删除epub根目录下所有 .js 文件
                for js_file in epub_root.rglob('*.js'):
                    js_file.unlink()
                    logger.debug(f"已删除 js 文件: {js_file}")
                # 从manifest中移除nav的item
                nav_item.decompose()
                logger.debug("已从OPF manifest中移除nav条目")
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
        """查找nav和ncx文件路径 返回dict"""
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        opf_dir = Path(opf_path).parent
        items = {
            'nav': opf_soup.find('item', {'properties': 'nav'}),
            'ncx': opf_soup.find('item', {'media-type': 'application/x-dtbncx+xml'})
        }
        result = {}
        for k, item in items.items():
            path = (opf_dir / item['href']).resolve() if item and item.get('href') else None
            if path and path.exists():
                result[k] = path
            else:
                if path: logger.warning(f"{k}文件不存在 {path}")
                result[k] = None
        return result

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
                    href = a['href']  # 保留锚点
                    full_path = (Path(base_dir) / href.split('#', 1)[0]).resolve()
                    entry = {
                        'title': a.text, # 完整保留标题
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
        id_tag = opf_soup.find('dc:identifier')
        return id_tag.text.strip() if id_tag and id_tag.text else f'urn:uuid:{uuid.uuid4()}'

    @staticmethod
    def _update_opf_reference(opf_path, ncx_href='toc.ncx'):
        """更新OPF中的NCX引用"""
        with open(opf_path, 'r', encoding='utf-8') as f:
            opf_soup = BeautifulSoup(f.read(), 'xml')
        # 移除旧NCX引用
        [item.decompose() for item in opf_soup.find_all('item', {'media-type': 'application/x-dtbncx+xml'})]
        # 添加新NCX引用
        manifest = opf_soup.find('manifest')
        manifest.append(opf_soup.new_tag('item', attrs={
            'id': 'ncx', 'href': ncx_href, 'media-type': 'application/x-dtbncx+xml'
        }))
        # 更新spine属性
        spine = opf_soup.find('spine')
        if spine: spine['toc'] = 'ncx'
        with open(opf_path, 'w', encoding='utf-8') as f:
            f.write(str(opf_soup))

    @staticmethod
    def fix_ncx_paths(opf_path):
        """检查并修正ncx中的src路径,尝试-1修正目录"""
        opf_path = Path(opf_path)
        opf_soup = BeautifulSoup(opf_path.read_text(encoding='utf-8'), 'xml')
        manifest = opf_soup.find('manifest')
        id_to_href = {item['id']: item['href'] for item in manifest.find_all('item') if item.has_attr('id') and item.has_attr('href')}
        spine_files = [id_to_href[itemref['idref']] for itemref in opf_soup.find('spine').find_all('itemref') if itemref['idref'] in id_to_href]
        ncx_item = manifest.find('item', {'media-type': 'application/x-dtbncx+xml'})
        if not ncx_item or not ncx_item.get('href'): return False, "未找到NCX"
        ncx_path = (opf_path.parent / ncx_item['href']).resolve()
        ncx_text = ncx_path.read_text(encoding='utf-8')
        # 统一的状态标记，只要有任何改动就设为 True
        any_changed = False 
        # ---检查修正src路径---
        def replace_src(match):
            nonlocal any_changed
            src = match.group(1)
            src_path, *anchor = src.split('#', 1)
            match_href = next((f for f in spine_files if Path(f).name == Path(src_path).name), None)
            if match_href and match_href != src_path:
                any_changed = True
                logger.debug(f"修正ncx路径: {src} -> {match_href}")
                return f'src="{match_href + ("#" + anchor[0] if anchor else "")}"'
            return match.group(0)
        ncx_text = re.sub(r'src="([^"]+)"', replace_src, ncx_text)
        if any_changed:
            logger.success("ncx目录路径已修正")

        # ---判断最后一条目录文件是否存在，不存在则批量-1修正---
        ncx_srcs = re.findall(r'src="([^"]+)"', ncx_text)
        if ncx_srcs:
            last_src = ncx_srcs[-1].split('#', 1)[0]
            if not (opf_path.parent / last_src).exists():
                soup = BeautifulSoup(ncx_text, 'xml')
                nav_points = soup.find_all('navPoint')
                last_title = nav_points[-1].find('navLabel').text.strip() if nav_points else ""
                logger.warning(f"最后一条目录文件不存在 {last_title} | ({ncx_srcs[-1]}) 全部目录批量-1修正")
                html_hrefs = [item['href'] for item in manifest.find_all('item')
                              if item.get('media-type') in ('text/html', 'application/xhtml+xml') and item.has_attr('href')]
                def offset_replace_src(match):
                    nonlocal any_changed
                    src_path, *anchor = match.group(1).split('#', 1)
                    try:
                        idx = html_hrefs.index(src_path)
                        new_href = html_hrefs[idx-1] if idx > 0 else src_path
                    except ValueError:
                        new_href = html_hrefs[-1] if src_path == last_src else src_path
                    
                    if new_href != src_path:
                        any_changed = True
                        return f'src="{new_href + ("#" + anchor[0] if anchor else "")}"'
                    return match.group(0)
                ncx_text = re.sub(r'src="([^"]+)"', offset_replace_src, ncx_text)
        if any_changed:
            ncx_path.write_text(ncx_text, encoding='utf-8')
            return True, "目录-1修正完成"
        # 只有在 any_changed 依旧为 False 时才显示此日志
        logger.debug("ncx无需修正")
        return True, "ncx无需修正"
