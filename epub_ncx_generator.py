import re
import uuid
import shutil
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString
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
            soup = BeautifulSoup(content, 'xml')
            # 规格化package标签
            package_tag = soup.find('package')
            if package_tag:
                package_tag.attrs = {
                    'version': '2.0',
                    'unique-identifier': "BookId", # 关联dc:identifier[@id](EPUB标准).然而对上了sigil元数据会不显示,没啥必要维持原值
                    'xmlns': "http://www.idpf.org/2007/opf"}
            # 规格化metadata标签
            metadata_tag = soup.find('metadata')
            if metadata_tag:
                metadata_tag.attrs.pop('xmlns:opf', None) # 会导致bs4追加opf:前缀 现在姑且删掉 有opf:前缀的标签会自动修正成没前缀的
                metadata_tag.attrs.pop('prefix', None) # 删除prefix属性(epub3专属)
                # 确保存在dc命名空间声明
                if 'xmlns:dc' not in metadata_tag.attrs:
                    metadata_tag.attrs['xmlns:dc'] = "http://purl.org/dc/elements/1.1/"
            nav_item = soup.find('item', properties='nav')
            # 寻找 EPUB 根目录（包含 mimetype 文件的目录，若无则默认为 OPF 所在目录）
            epub_root = next((p for p in opf_path.parents if (p / 'mimetype').exists()), opf_path.parent)

            # 递归删除 EPUB 根目录下所有 .bw .js rights.xml文件
            for ext in ['*.bw', '*.js', 'rights.xml']:
                for extra_file in epub_root.rglob(ext):
                    extra_file.unlink()
                    logger.debug(f"已删除 {extra_file.suffix[1:]} 文件: {extra_file}")

            # 处理 nav_item 的物理删除与条目移除
            if nav_item:
                nav_path = opf_path.parent / nav_item['href']
                if nav_path.exists():
                    nav_path.unlink()
                    logger.debug(f"已删除 nav 文件: {nav_path}")
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
        def calculate_max_depth(entries, current_depth=1):
            return max([calculate_max_depth(e['children'], current_depth + 1) for e in entries if e.get('children')] + [current_depth])
        order_gen = EpubNCXGenerator.PlayOrder()
        nav_points = EpubNCXGenerator._build_ncx_points(toc_entries, order_gen)
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
    class PlayOrder:
        def __init__(self, n=1): self.n = n
        def get(self): return (v := self.n, setattr(self, 'n', v + 1))[0]

    @staticmethod
    def _build_ncx_points(entries, order_gen):
        points = []
        for entry in entries:
            play_order = order_gen.get()
            point_id = f"navPoint-{play_order}"
            nav_point = f'''
            <navPoint id="{point_id}" playOrder="{play_order}">
                <navLabel><text>{entry['title']}</text></navLabel>
                <content src="{entry['href']}"/>'''
            if entry.get('children'):
                child_xmls = EpubNCXGenerator._build_ncx_points(entry['children'], order_gen)
                nav_point += f'\n{"".join(child_xmls)}\n</navPoint>'
            else:
                nav_point += '\n</navPoint>'
            points.append(nav_point)
        return points

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
    def fix_ncx_paths(opf_path, offset_enabled=True, atokagi_enabled=True, manual_offset=0):
        """检查并修正ncx中的src路径,尝试-1修正目录，补全あとがき条目"""
        opf_path = Path(opf_path)
        opf_soup = BeautifulSoup(opf_path.read_text(encoding='utf-8'), 'xml')

        # 提取 Manifest 和 Spine 信息
        id_to_href = {i['id']: i['href'] for i in opf_soup.find('manifest').find_all('item') if i.get('id') and i.get('href')}
        spine_files = [id_to_href[r['idref']] for r in opf_soup.find('spine').find_all('itemref') if r.get('idref') in id_to_href]
        html_hrefs = [i['href'] for i in opf_soup.find('manifest').find_all('item') if i.get('media-type') in ('text/html', 'application/xhtml+xml')]

        paths = EpubNCXGenerator._find_nav_path(opf_path)
        nav_path, ncx_path, any_changed = paths.get('nav'), paths.get('ncx'), False

        # 修正ncx (合并写入逻辑：路径修正 + 批量偏移 + 补全 あとがき)
        ncx_text = None
        if ncx_path and ncx_path.exists():
            ncx_text = ncx_path.read_text(encoding='utf-8')
            
            # 检查修正ncx中src路径
            def replace_src(m):
                nonlocal any_changed
                s_p, *anc = m.group(1).split('#', 1)
                if (m_h := next((f for f in spine_files if Path(f).name == Path(s_p).name), None)) and m_h != s_p:
                    any_changed = True
                    logger.debug(f"修正ncx路径: {m.group(1)} -> {m_h}{'#'+anc[0] if anc else ''}")
                    return f'src="{m_h}{"#" + anc[0] if anc else ""}"'
                return m.group(0)
            ncx_text = re.sub(r'src="([^"]+)"', replace_src, ncx_text)
            if any_changed: logger.success("ncx目录路径已修正")

            # 1.优先强制偏移,0则跳过 2.自动判断最后一条目录文件是否存在，不存在则-1修正（受offset_enabled控制）
            ncx_srcs = re.findall(r'src="([^"]+)"', ncx_text)
            last_f = ncx_srcs[-1] if ncx_srcs else ""; last_src = last_f.split('#')[0]
            m_v = int(manual_offset or 0)
            missing = last_src and not (opf_path.parent / last_src).exists()
            shift = m_v if m_v else (-1 if (offset_enabled and missing) else 0)
            if shift:
                l_t = (BeautifulSoup(ncx_text, 'xml').find_all('navPoint') or [None])[-1]
                lbl = l_t.find('navLabel').text.strip() if l_t and l_t.find('navLabel') else ""
                logger.warning(f"强制目录{m_v:+}偏移,目录最后一条: {lbl} | ({last_f})" if m_v else 
                               f"目录最后一条文件不存在: {lbl} | ({last_f}) 全部目录批量-1修正")
                def offset_src(m):
                    nonlocal any_changed
                    s_p, *anc = m.group(1).split('#', 1)
                    idx = html_hrefs.index(s_p) if s_p in html_hrefs else (len(html_hrefs) if s_p == last_src else -1)
                    if idx >= 0:
                        n_h = html_hrefs[max(0, min(len(html_hrefs)-1, idx + shift))]
                        if n_h != s_p: any_changed, s_p = True, n_h
                    return f'src="{s_p}{"#" + anc[0] if anc else ""}"'
                ncx_text = re.sub(r'src="([^"]+)"', offset_src, ncx_text)

        # 只有在 ncx 或 nav 确实缺失“あとがき”条目时 才寻找あとがき文件（受atokagi_enabled控制）
        ncx_missing = ncx_text and 'あとがき' not in ncx_text
        nav_missing = nav_path and nav_path.exists() and 'あとがき' not in (nav_content := nav_path.read_text(encoding='utf-8'))
        atokagi_file = None

        if atokagi_enabled:
            # 寻找唯一 あとがき 文件 (Body前20行内且全书唯一的HTML)
            if ncx_missing or nav_missing:
                candidates = [
                    h for h in spine_files 
                    if (f := opf_path.parent / h).exists() 
                    and (c := f.read_text(encoding='utf-8', errors='ignore'))
                    # 提取 body 后前 20 行
                    and (m := re.search(r'<body[^>]*>([\s\S]*)$', c, re.I))
                    and (zone := "\n".join(m.group(1).splitlines()[:20]))
                    # 匹配逻辑：匹配任何标签内包含 あとがき 的行 (兼容独立标题和描述性标题)
                    and re.search(r'<[^>]+>[^<]*あとがき[^<]*</[^>]+>', zone)
                ]
                # 确保全书满足上述条件的 HTML 文件有且仅为一个
                atokagi_file = candidates[0] if len(candidates) == 1 else None

            # 补全ncx あとがき条目 (保留空条目并修复索引)
            if ncx_text and atokagi_file and ncx_missing and (m_nav := re.search(r'(<navMap>)(.*?)(</navMap>)', ncx_text, re.DOTALL)):
                def get_idx(h): 
                    if not h: return -1
                    c_h = h.split('#')[0]
                    return spine_files.index(c_h) if c_h in spine_files else next((i for i, f in enumerate(spine_files) if Path(f).name == Path(c_h).name), -1)

                pts = re.findall(r'<navPoint[\s\S]*?</navPoint>', m_nav.group(2))
                entries = [{'title': (re.search(r'<text[^>]*>(.*?)</text>', p, re.DOTALL) or [0, ""])[1].strip(),
                            'href': (re.search(r'src="([^"]+)"', p) or [0, ""])[1]} for p in pts]
                
                a_idx = spine_files.index(atokagi_file)
                ins_pos = next((i for i, e in enumerate(entries) if e['href'] and get_idx(e['href']) > a_idx), len(entries))
                entries.insert(ins_pos, {'title': 'あとがき', 'href': atokagi_file, 'children': []})
                
                ncx_text = ncx_text[:m_nav.start(2)] + "\n" + "".join(EpubNCXGenerator._build_ncx_points(entries, EpubNCXGenerator.PlayOrder(1))) + "\n" + ncx_text[m_nav.end(2):]
                any_changed = True
                logger.success(f"ncx 已补全あとがき条目: 标题=あとがき, 路径={atokagi_file}")

            if any_changed and ncx_path: ncx_path.write_text(ncx_text, encoding='utf-8')

            # 补全nav あとがき条目
            if nav_missing and atokagi_file:
                nav_soup = BeautifulSoup(nav_content, 'html.parser')
                if (toc := nav_soup.find('nav', {'epub:type': 'toc'}) or nav_soup.find('nav', {'role': 'doc-toc'})) and (root := toc.find(['ol', 'ul'])):
                    a_idx = spine_files.index(atokagi_file)
                    lis = root.find_all('li', recursive=False)
                    ins = next((li for li in lis if (a := li.find('a', href=True)) and (h := a['href'].split('#')[0]) in spine_files and spine_files.index(h) > a_idx), None)
                    
                    new_li = nav_soup.new_tag('li')
                    new_li.append(nav_soup.new_tag('a', href=atokagi_file))
                    new_li.a.string = 'あとがき'
                    ins.insert_before(new_li) if ins else root.append(new_li)
                    
                    nav_path.write_text(nav_soup.decode(formatter='html'), encoding='utf-8')
                    any_changed = True
                    logger.success(f"nav 已补全あとがき条目: 标题=あとがき, 路径={atokagi_file}")

        # 只有在 any_changed 依旧为 False 时才显示此日志
        if not any_changed: logger.debug("ncx无需修正")
        return True, "ncx无需修正"

    @staticmethod
    def insert_sub_chapters(opf_path, parent_href, sub_chapters):
        """插入子章节(相对层级: 1=父节点的同级节点, 2=父节点的子节点)"""
        if not sub_chapters or not (ps := EpubNCXGenerator._find_nav_path(opf_path)): return 0
        target_fn, added_this_time = Path(parent_href.split('#')[0]).name, 0

        # ncx 处理：解析 -> 内存递归插入 -> 重写ncx格式.按顺序根据depth相对插入同级或次级条目
        if (nx_p := ps.get('ncx')) and nx_p.exists():
            entries = EpubNCXGenerator._parse_ncx_to_entries(nx_p)
            def inject(nodes):
                for i, n in enumerate(nodes):
                    if Path(n['href'].split('#')[0]).name == target_fn:
                        cur, idx, cnt = n, i + 1, 0 # cur:当前父节点, idx:插入位置索引
                        for s in sub_chapters:
                            new = {'id': s['id'], 'title': BeautifulSoup(s['title'], 'html.parser').get_text(strip=True), 
                                   'href': s['href'], 'children': []}
                            if s.get('depth', 2) == 1: # 1级: 插入nodes列表(兄弟) 并更新当前父节点
                                nodes.insert(idx, new); cur, idx = new, idx + 1
                            else: cur.setdefault('children', []).append(new) # 2级: 插入当前父节点下 增加容错防御
                            cnt += 1
                        return cnt
                    if n.get('children') and (res := inject(n['children'])) is not None: return res
            if (cnt := inject(entries)) is not None:
                nx_p.write_text(EpubNCXGenerator._create_ncx_content(EpubNCXGenerator._get_uid_from_opf(opf_path), entries, 
                                EpubNCXGenerator._get_book_title_from_opf(opf_path)), 'utf-8')
                logger.debug(f"ncx:在 {target_fn} 后续追加 {cnt} 个章节")
                added_this_time = cnt

        # nav追加插入章节(简易测试没问题 不常用 可能会出问题)
        if (nv_p := ps.get('nav')) and nv_p.exists():
            sp = BeautifulSoup(nv_p.read_text('utf-8'), 'html.parser')
            if (ta := sp.find('a', href=lambda h: h and Path(h.split('#')[0]).name == target_fn)) and (cur_li := ta.parent):
                cur_ol, cnt = cur_li.find(['ol', 'ul']), 0
                for s in sub_chapters:
                    (nl := sp.new_tag('li')).append(sp.new_tag('a', href=s['href'], string=s['title']))
                    if s.get('depth', 2) == 1: # 1级: 紧接在同级节点后插入，并更新基准
                        cur_li.insert_after(NavigableString('\n')); cur_li.next_sibling.insert_after(nl)
                        cur_li, cur_ol = nl, None
                    else: # 2级: 放入内部列表 (ol/ul)，采用 extend 高密度压入换行符
                        if not cur_ol: 
                            cur_li.extend([NavigableString('\n'), cur_ol := sp.new_tag('ol'), NavigableString('\n')])
                        cur_ol.extend([NavigableString('\n'), nl, NavigableString('\n')])
                    cnt += 1
                if cnt:
                    nv_p.write_text(sp.decode(formatter='html'), 'utf-8')
                    logger.debug(f"nav: 在 {target_fn} 后续追加 {cnt} 个章节")
                    added_this_time = max(added_this_time, cnt)
        return added_this_time # 返回给外层循环累计

    @staticmethod
    def _parse_ncx_to_entries(ncx_path):
        """解析 ncx 为嵌套字典"""
        soup = BeautifulSoup(ncx_path.read_text('utf-8'), 'xml')
        def parse(tag):
            return [{
                'title': pt.find('navLabel').text.strip(),
                'href': pt.find('content')['src'],
                'children': parse(pt)
            } for pt in tag.find_all('navPoint', recursive=False)]
        return parse(soup.find('navMap')) if soup.find('navMap') else []