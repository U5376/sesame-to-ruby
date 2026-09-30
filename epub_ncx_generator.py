import os
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
            paths = EpubNCXGenerator.find_nav_path(opf_path)
            nav_path, ncx_path = paths['nav'], paths['ncx']
            target_ncx = opf_dir / 'toc.ncx'

            if ncx_path:
                # 存在ncx则确保在opf根目录下并且名称为toc
                if ncx_path.resolve() != target_ncx.resolve(): 
                    shutil.move(ncx_path, target_ncx); logger.debug(f"已将ncx移动到根目录: {target_ncx}")
                EpubNCXGenerator._update_opf_reference(opf_path, 'toc.ncx')
                logger.info("toc.ncx已存在，已确保OPF内引用和spine的正确")
                return True, "toc.ncx已存在，已确保OPF内引用和spine的正确"

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
    def find_nav_path(opf_path):
        """查找nav和ncx文件路径 返回dict (公开工具: 主程序分割/预览排除目录文档用)"""
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
    def fix_ncx_paths(opf_path, path_fix_enabled=True, offset_enabled=True, atokagi_enabled=True, manual_offset=0, cover_enabled=None):
        """检查并修正ncx中的src路径,尝试-1修正目录，补全あとがき与表紙(封面)条目"""
        opf_path = Path(opf_path)
        cover_enabled = atokagi_enabled if cover_enabled is None else cover_enabled  # cover_enabled 缺省(None)时沿用 atokagi_enabled 的开关状态
        opf_soup = BeautifulSoup(opf_path.read_text(encoding='utf-8'), 'xml')
        # 提取 Manifest 和 Spine 信息
        id_to_href = {i['id']: i['href'] for i in opf_soup.find('manifest').find_all('item') if i.get('id') and i.get('href')}
        manifest_tag = opf_soup.find('manifest')  # manifest节点缓存(del_orphan_c0移除条目用)
        spine_files = [id_to_href[r['idref']] for r in opf_soup.find('spine').find_all('itemref') if r.get('idref') in id_to_href]
        name_to_href = {Path(f).name: f for f in reversed(spine_files)}  # 文件名 -> spine href (O(1)查表; reversed保证重名时保留首个, 与原next()行为一致)
        html_hrefs = [i['href'] for i in opf_soup.find('manifest').find_all('item') if i.get('media-type') in ('text/html', 'application/xhtml+xml')]
        html_idx = {h: i for i, h in reversed(list(enumerate(html_hrefs)))}  # href -> 索引 O(1)查表(reversed保证重复href取首个, 与list.index()一致), 替代offset_src回调内随匹配数累积的O(n²)线性扫描
        paths = EpubNCXGenerator.find_nav_path(opf_path)
        nav_path, ncx_path = paths.get('nav'), paths.get('ncx')
        ncx_changed, nav_changed, opf_changed = False, False, False  # opf_changed: del_orphan_c0移除manifest/guide条目后需写回OPF

        # ---- 公共工具 ----
        def get_idx(h):
            # href(去锚点) -> spine索引 (先精确匹配, 再按文件名兜底), 找不到返回-1
            if not h: return -1
            c_h = h.split('#')[0]
            if c_h in spine_files: return spine_files.index(c_h)
            return spine_files.index(m_h) if (m_h := name_to_href.get(Path(c_h).name)) else -1

        def in_spine(h):
            # 检查路径(去锚点)是否在spine内
            return get_idx(h) >= 0

        def del_orphan_c0(rel):
            # 未进spine的c0.xhtml封面占位页 → 删物理文件+移除manifest条目+清理opf的guide引用(ncx/nav均可触发.非处理nav的guide)
            nonlocal opf_changed
            if rel and (fn := Path(rel).name).lower() == 'c0.xhtml' and get_idx(rel) < 0:  # 判定基准=不在spine(与broken同源); lower兼容C0.XHTML(Windows不区分大小写)
                acts = []
                if (f := opf_path.parent / rel).exists():
                    f.unlink()
                    acts.append(f"删除文件:{rel}")
                if its := [i for i in manifest_tag.find_all('item') if Path(i.get('href', '')).name.lower() == 'c0.xhtml']:  # 按文件名匹配manifest条目(兼容./前缀与子目录写法)
                    hrefs = [i.get('href', '') for i in its]  # decompose会将attrs置None, 需先取出href再销毁
                    for it in its: it.decompose()
                    acts.append(f"移除manifest条目:{'+'.join(hrefs)}")
                if refs := [r for r in opf_soup.find_all('reference') if Path(r.get('href', '')).name.lower() == 'c0.xhtml']:
                    old_hrefs = [r['href'] for r in refs]  # 先取旧href再覆盖(覆盖后r['href']已是新值); 多条时全部记录
                    for r in refs: r['href'] = spine_files[0]  # 指向c0的guide引用重定向到spine首文件(原目标已删, 避免死链; 不限于cover类型, 如text/Beginning引用)
                    acts.append(f"guide引用:{'+'.join(old_hrefs)} -> {spine_files[0]}")
                if its or refs: opf_changed = True
                if acts: logger.debug(f"清理c0.xhtml占位页: {' + '.join(acts)}")

        def flatten(nodes):
            # 先序展平entries树(文档序; 元素与树内对象同引用)
            return [e for n in nodes for e in [n, *flatten(n.get('children') or [])]]

        is_cover = lambda t: (t or '').strip() in ('表紙', '表纸', '封面')  # 标题是否为表纸|封面

        # 修正ncx (合并写入逻辑：路径修正 + 批量偏移 + 补全 あとがき/表紙)
        ncx_text = None
        if ncx_path and ncx_path.exists():
            ncx_text = ncx_path.read_text(encoding='utf-8')
            
            # 检查修正ncx中src路径 (受path_fix_enabled控制)
            if path_fix_enabled:
                def replace_src(m):
                    nonlocal ncx_changed
                    s_p, *anc = m.group(1).split('#', 1)
                    if (m_h := name_to_href.get(Path(s_p).name)) and m_h != s_p:  # O(1)查表替代原next()线性扫描, 语义一致
                        ncx_changed = True
                        logger.debug(f"修正ncx路径: {m.group(1)} -> {m_h}{'#'+anc[0] if anc else ''}")
                        return f'src="{m_h}{"#" + anc[0] if anc else ""}"'
                    return m.group(0)
                ncx_text = re.sub(r'src="([^"]+)"', replace_src, ncx_text)
                if ncx_changed: logger.success("ncx目录路径已修正")

            # 优先强制偏移,0则跳过.自动判断最后一条目录文件是否存在，不存在则-1修正（受offset_enabled控制）
            ncx_srcs = re.findall(r'src="([^"]+)"', (re.search(r'<navMap>([\s\S]*?)</navMap>', ncx_text, re.I) or [0, ""])[1]) #仅匹配navMap内的src
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
                    nonlocal ncx_changed
                    s_p, *anc = m.group(1).split('#', 1)
                    idx = html_idx[s_p] if s_p in html_idx else (len(html_hrefs) if s_p == last_src else -1)  # O(1)查表, 语义与index()一致
                    if idx >= 0:
                        n_h = html_hrefs[max(0, min(len(html_hrefs)-1, idx + shift))]
                        if n_h != s_p: ncx_changed, s_p = True, n_h
                    return f'src="{s_p}{"#" + anc[0] if anc else ""}"'
                ncx_text = re.sub(r'src="([^"]+)"', offset_src, ncx_text)

        # 只有在 ncx 或 nav 确实缺失“あとがき”条目时 才寻找あとがき文件（受atokagi_enabled控制）
        ncx_missing = ncx_text and 'あとがき' not in ncx_text
        nav_content = nav_path.read_text(encoding='utf-8') if (nav_path and nav_path.exists()) else ""
        nav_missing = bool(nav_content) and 'あとがき' not in nav_content
        atokagi_file = None

        if atokagi_enabled:
            # 寻找唯一 あとがき 文件 (Body前20行内且全书唯一的HTML)
            if ncx_missing or nav_missing:
                candidates = [
                    h for h in spine_files
                    if (f := opf_path.parent / h).exists()
                    and (c := f.read_text(encoding='utf-8', errors='ignore'))
                    # 仅匹配body标签.避免使用[\s\S]*)$扫描全文
                    and (m := re.search(r'<body[^>]*>', c, re.I))
                    # 仅截取body后的2000个字符进行按行切分，提取前20行
                    and (zone := "\n".join(c[m.end():m.end()+2000].splitlines()[:20]))
                    # 匹配逻辑：匹配任何标签内包含 あとがき 的行 (兼容独立标题和描述性标题)
                    and re.search(r'<[^>]+>[^<]*あとがき[^<]*</[^>]+>', zone)
                ]
                # 确保全书满足上述条件的 HTML 文件有且仅为一个
                atokagi_file = candidates[0] if len(candidates) == 1 else None
                if not atokagi_file:
                    cand_desc = f"({'+'.join(candidates)})" if candidates else ""
                    logger.debug(f"あとがき补全跳过: 候选数={len(candidates)}{cand_desc} (需恰好1个; 匹配规则=body后2000字符内前20行存在含あとがき的完整标签行)")

        # ===== ncx 补全あとがき/表紙条目 仅结构级插入才重建navMap(惰性重建) =====
        if ncx_text and ((atokagi_file and ncx_missing) or (cover_enabled and spine_files)):
            entries = EpubNCXGenerator._parse_ncx_to_entries(ncx_path, ncx_text=ncx_text)  # 解析内存文本: 前两步的src修正/偏移结果不回退
            seq = flatten(entries)  # 文档序平铺, 供表紙查找复用 (あとがき条目非表紙, 插入后无需刷新)
            m_nav = re.search(r'(<navMap>)(.*?)(</navMap>)', ncx_text, re.DOTALL)  # navMap定位(重建用)
            rebuilt = False  # 是否发生结构级插入(需重建navMap)
            ncx_acts = []  # ncx侧动作聚合
            # 补全ncx あとがき条目 (保留空条目并修复索引)
            if atokagi_file and ncx_missing and m_nav:
                a_idx = spine_files.index(atokagi_file)
                ins_pos = next((i for i, e in enumerate(entries) if e['href'] and get_idx(e['href']) > a_idx), len(entries))
                entries.insert(ins_pos, {'title': 'あとがき', 'href': atokagi_file, 'children': []})
                rebuilt = True
                ncx_acts.append(f"补全あとがき,路径:{atokagi_file}")
            # 补全ncx 表紙条目 (受cover_enabled控制,缺省沿用atokagi_enabled)
            if cover_enabled and spine_files:
                cover_file = spine_files[0]  # spine列表内第一个文件视为表纸
                broken = [e for e in seq if is_cover(e['title']) and not in_spine(e['href'])]
                if broken:
                    # 情况2: 已有表紙条目但路径不在spine → 覆盖src(标题保留, 免得出现两个表纸条目); 丢弃旧锚点(片段是旧文件内的id, 换目标后必成死链)
                    for e in broken:
                        o_h = e['href']
                        del_orphan_c0(o_h.split('#')[0])  # c0.xhtml 不在spine则删物理文件+清理OPF引用
                        if rebuilt: e['href'] = cover_file  # あとがき已触发重建 → 改树随重建一并生效
                        else: ncx_text = ncx_text.replace(f'src="{o_h}"', f'src="{cover_file}"')  # src级定点替换, 不触发重建(格式零漂移)
                        ncx_acts.append(f"覆盖表紙({o_h} -> {cover_file})")
                        ncx_changed = True
                elif not any(is_cover(e['title']) for e in seq):
                    # 情况1: 无任何封面/表纸条目 → 头部插入(重建时playOrder自动重排为1..N)
                    entries.insert(0, {'title': '表紙', 'href': cover_file, 'children': []})
                    rebuilt = True
                    ncx_acts.append(f"补全表紙,路径=({cover_file})")
            # 统一重建navMap: 仅结构级插入时执行(src修改不重建)
            if rebuilt:
                if m_nav:
                    ncx_text = ncx_text[:m_nav.start(2)] + "\n" + "".join(EpubNCXGenerator._build_ncx_points(entries, EpubNCXGenerator.PlayOrder(1))) + "\n" + ncx_text[m_nav.end(2):]
                    ncx_changed = True
                else:
                    logger.warning("ncx中未找到<navMap>, 跳过navMap重建")
            if ncx_acts: logger.success(f"ncx: {' + '.join(ncx_acts)}")  # ncx侧全部动作一行日志

        # ===== nav 补全あとがき/表紙条目 (bs4单次parse → soup上修改 → 单次写回) =====
        # 仅在确有nav侧任务时才解析; 注意: ncx缺失不影响nav补全(与源码一致, ncx/nav是两条独立的目录线)
        if nav_content and ((atokagi_file and nav_missing) or (cover_enabled and spine_files)):
            nav_soup = BeautifulSoup(nav_content, 'html.parser')
            if (toc := nav_soup.find('nav', {'epub:type': 'toc'}) or nav_soup.find('nav', {'role': 'doc-toc'})) and (root := toc.find(['ol', 'ul'])):
                nav_acts = []  # nav侧动作聚合
                def nav2opf(h):
                    # nav相对href -> OPF相对路径; relative_to改用relpath 支持../跨目录
                    try:
                        rel = Path(os.path.relpath((nav_path.parent / h.split('#')[0]).resolve(), opf_path.parent.resolve())).as_posix()
                    except ValueError:
                        return None
                    return None if rel.startswith('..') else rel

                def opf2nav(p):
                    # OPF相对路径 -> 相对nav.xhtml的路径
                    try:
                        return Path(os.path.relpath((opf_path.parent / p).resolve(), nav_path.parent.resolve())).as_posix()
                    except ValueError:
                        return None

                # 补全nav あとがき条目 (opf2nav判空: 跨目录等异常时跳过而非抛异常)
                if atokagi_file and nav_missing and (nav_rel_atokagi := opf2nav(atokagi_file)):
                    a_idx = spine_files.index(atokagi_file)
                    ins = next(
                        (li for li in root.find_all('li', recursive=False) if
                         # 提取带链接的<a>标签
                         (a := li.find('a', href=True)) and
                         # 统一路径：将相对于nav.xhtml的href转换为相对于OPF的标准相对路径
                         (rel := nav2opf(a['href'])) in spine_files
                         # 位置判定：确保找到的条目在spine中的位置排在あとがき之后
                         and spine_files.index(rel) > a_idx), None)
                    new_li = nav_soup.new_tag('li')
                    new_li.append(nav_soup.new_tag('a', href=nav_rel_atokagi, string='あとがき'))
                    (ins.insert_before(new_li) if ins else root.append(new_li)); new_li.insert_after(NavigableString('\n'))
                    nav_changed = True
                    nav_acts.append(f"补全あとがき,路径:{nav_rel_atokagi}")

                # 表紙补全 (受cover_enabled控制)
                if cover_enabled and spine_files:
                    cover_file = spine_files[0]  # spine列表内第一个文件视为表纸
                    if nav_rel_cover := opf2nav(cover_file):
                        cover_as = [a for a in root.find_all('a', href=True) if is_cover(a.get_text(strip=True))]
                        if not cover_as:
                            # 情况1: 无封面/表纸条目 → 在目录头部插入
                            new_li = nav_soup.new_tag('li')
                            new_li.append(nav_soup.new_tag('a', href=nav_rel_cover, string='表紙'))
                            (first_li.insert_before(new_li) if (first_li := root.find('li', recursive=False)) else root.insert(0, new_li)); new_li.insert_after(NavigableString('\n'))
                            nav_changed = True
                            nav_acts.append(f"补全表紙,路径=({nav_rel_cover})")
                        else:
                            # 情况2: 封面/表纸标题条目路径不在spine → 覆盖其href(避免出现两个表纸条目); 丢弃旧锚点(片段是旧文件内的id, 换目标后必成死链)
                            for a in cover_as:
                                if not in_spine(rel := nav2opf(a['href'])):
                                    del_orphan_c0(rel)  # c0.xhtml 不在spine则删物理文件+清理OPF引用 (nav这里处理可能是多余的 姑且保留)
                                    nav_acts.append(f"覆盖表紙({a['href']} -> {nav_rel_cover})")
                                    a['href'] = nav_rel_cover
                                    nav_changed = True
                        # 修复nav内指向孤儿c0.xhtml的链接(landmarks地标/page-list等全文档扫描; toc区封面条目已由上方覆盖兜住, 先改href不会重复命中)
                        for a in [a for a in nav_soup.find_all('a', href=True)
                                  if Path(a['href'].split('#')[0]).name.lower() == 'c0.xhtml'
                                  and not in_spine(nav2opf(a['href']))]:  # 判定基准与del_orphan_c0一致(不在spine)
                            nav_acts.append(f"landmarks地标引用:({a['href']} -> {nav_rel_cover})")
                            a['href'] = nav_rel_cover
                            nav_changed = True
                # nav单次写回(默认bs4格式; あとがき与表紙共存时避免多次写文件)
                if nav_acts: logger.success(f"nav: {' + '.join(nav_acts)}")  # nav侧全部动作一行日志
                if nav_changed:
                    nav_path.write_text(nav_soup.decode(formatter='html'), encoding='utf-8')

        # 将文件写入逻辑移至最外层，确保路径修正、偏移和后记补全均能正常触发保存
        if ncx_changed and ncx_path and ncx_text:
            ncx_path.write_text(ncx_text, encoding='utf-8')
        if opf_changed:
            opf_path.write_text(str(opf_soup), encoding='utf-8')  # manifest/guide有移除时写回OPF(与convert_to_epub2同风格str序列化)
        if not (ncx_changed or nav_changed or opf_changed):
            logger.debug("ncx nav无需修正")  # 只有在ncx与nav与opf均无任何修改时才显示此日志
        return True, "fix_ncx_paths完成"

    @staticmethod
    def insert_sub_chapters(opf_path, parent_href, sub_chapters):
        """插入子章节(相对层级: 1=父节点的同级节点, 2=父节点的子节点)"""
        if not sub_chapters or not (ps := EpubNCXGenerator.find_nav_path(opf_path)): return 0
        # 有锚点按文件名定位父节点, 无锚点(None)由inject头部插入分支处理
        target_fn = Path(parent_href.split('#')[0]).name if parent_href else None
        added_this_time = 0
        # 文档级去重键(标题, 去锚点文件名)
        s_key = lambda s: (s['title'].strip(), Path(s['href'].split('#')[0]).name)
        _all_subs = sub_chapters  # 原始列表备份(ncx/nav两侧各自独立过滤, 互不影响)
        # ncx 处理：解析 -> 内存递归插入 -> 重写ncx格式.按顺序根据depth相对插入同级或次级条目
        if (nx_p := ps.get('ncx')) and nx_p.exists():
            entries = EpubNCXGenerator._parse_ncx_to_entries(nx_p)
            # ncx已有同标题同文件条目时过滤(如源书带#锚点的章节), 避免nav/ncx覆盖度不一致时ncx写入重复条目
            _flat = lambda ns: [e for n in ns for e in [n, *_flat(n.get('children') or [])]]
            dup = {(e['title'].strip(), Path(e['href'].split('#')[0]).name) for e in _flat(entries)}
            for s in _all_subs:
                if s_key(s) in dup: logger.opt(colors=True).debug(f"<r>ncx去重跳过</r> <w>标题: {s['title']}, 路径: {s['href']}</w>")
            sub_chapters = [s for s in _all_subs if s_key(s) not in dup]
            def inject(nodes):
                # 递归查找锚点节点并相对插入; 锚点为None时头部插入nodes最前面(仅顶层调用时nodes=entries)
                if target_fn is None: # 无锚点(None)头部插入 按sub_chapters原序插到头部(2级挂靠前一条1级,无则退化顶级)
                    cur, top = None, 0 # cur:前一条1级条目(2级挂靠对象), top:头部插入位置索引(仅1级推进)
                    for s in sub_chapters:
                        new = {'id': s['id'], 'title': BeautifulSoup(s['title'], 'html.parser').get_text(strip=True), 'href': s['href'], 'children': []}
                        if s.get('depth', 2) == 1 or cur is None: nodes.insert(top, new); cur, top = new, top + 1 # 1级(或无法挂靠的2级): 顺序插入头部
                        else: cur.setdefault('children', []).append(new) # 2级: 挂靠前一条1级条目
                    return len(sub_chapters)
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
            if sub_chapters and (cnt := inject(entries)) is not None:
                nx_p.write_text(EpubNCXGenerator._create_ncx_content(EpubNCXGenerator._get_uid_from_opf(opf_path), entries, 
                                EpubNCXGenerator._get_book_title_from_opf(opf_path)), 'utf-8')
                logger.opt(colors=True).debug(f"<g>ncx:在 {target_fn}</> 后续追加 {cnt} 个章节" if target_fn else f"<y>ncx:目录头部(无锚点)</>追加 {cnt} 个章节")
                added_this_time = cnt

        # nav追加插入章节(简易测试没问题 不常用 可能会出问题)
        if (nv_p := ps.get('nav')) and nv_p.exists():
            sp = BeautifulSoup(nv_p.read_text('utf-8'), 'html.parser')
            # 获取OPF所在目录, 用于将OPF相对路径转换为相对nav的路径
            opf_dir = Path(opf_path).parent.resolve()
            rel = lambda h: (opf_dir / h).resolve().relative_to(nv_p.parent).as_posix()
            # 锚点定位仅限toc导航区, 避免命中landmarks等其他nav内的链接影响其结构
            toc_nav = sp.find('nav', {'epub:type': 'toc'}) or sp.find('nav', {'role': 'doc-toc'})
            # nav文档级去重: 已有同标题同文件条目时过滤
            n_exist = {(a.get_text(strip=True), Path(a['href'].split('#')[0]).name) for a in toc_nav.find_all('a', href=True)} if toc_nav else set()
            for s in _all_subs:
                if s_key(s) in n_exist: logger.opt(colors=True).debug(f"<r>nav去重跳过</r> <w>标题: {s['title']}, 路径: {s['href']}</w>")
            sub_chapters = [s for s in _all_subs if s_key(s) not in n_exist]
            if target_fn and sub_chapters and toc_nav and (ta := toc_nav.find('a', href=lambda h: h and Path(h.split('#')[0]).name == target_fn)) and (cur_li := ta.parent):
                cur_ol, cnt = cur_li.find(['ol', 'ul']), 0
                for s in sub_chapters:
                    (nl := sp.new_tag('li')).append(sp.new_tag('a', href=rel(s['href']), string=s['title'])) # href经rel()转换
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
                    logger.opt(colors=True).debug(f"<g>nav:在 {target_fn} 后续追加 {cnt} 个章节</>")
                    added_this_time = max(added_this_time, cnt)
            elif not target_fn and sub_chapters and toc_nav and (root := toc_nav.find(['ol', 'ul'])): # 无锚点头部插入 定位toc根列表
                prev_top, cnt = None, 0 # prev_top:前一条1级条目(2级挂靠对象)
                for s in sub_chapters:
                    (nl := sp.new_tag('li')).append(sp.new_tag('a', href=rel(s['href']), string=s['title'])) # href经rel()转换
                    if s.get('depth', 2) == 1 or prev_top is None: # 1级(或无法挂靠的2级): 顺序插入头部 保持sub_chapters原序
                        (prev_top.insert_after(NavigableString('\n'), nl) if prev_top else root.insert(0, nl)); prev_top = nl
                    else: # 2级: 挂靠前一条1级条目的内部列表(ol/ul) 采用extend高密度压入换行符
                        (sub_ol := prev_top.find(['ol', 'ul'])) or prev_top.extend([NavigableString('\n'), sub_ol := sp.new_tag('ol'), NavigableString('\n')])
                        sub_ol.extend([NavigableString('\n'), nl, NavigableString('\n')])
                    cnt += 1
                if cnt:
                    nv_p.write_text(sp.decode(formatter='html'), 'utf-8')
                    logger.opt(colors=True).debug(f"<w>nav:目录头部(无锚点)</>追加 {cnt} 个章节")
                    added_this_time = max(added_this_time, cnt)
        return added_this_time # 返回给外层循环累计

    @staticmethod
    def _parse_ncx_to_entries(ncx_path, ncx_text=None):
        """解析 ncx 为嵌套字典 (ncx_text传入时优先解析内存文本, 避免读到磁盘旧内容导致前序修正回退)"""
        soup = BeautifulSoup(ncx_text if ncx_text is not None else ncx_path.read_text('utf-8'), 'xml')
        def parse(tag):
            return [{
                'title': pt.find('navLabel').text.strip(),
                'href': pt.find('content')['src'],
                'children': parse(pt)
            } for pt in tag.find_all('navPoint', recursive=False)]
        return parse(soup.find('navMap')) if soup.find('navMap') else []