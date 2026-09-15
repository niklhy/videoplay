# -*- coding: utf-8 -*-
"""演员文件夹解析器（对应设计文档 26 号 31.3 节）。

解析以演员命名的文件夹名称，格式如下：

    格式1: [声母][日文名]([中文名])                示例: f楓ふうあ(枫芙爱)
    格式2: [声母][日文名]([中文名])+[曾用日文名]([曾用中文名])
                                                  示例: f鳳みゆ(凤美优)+川北メイサ(川北明沙)

同时提供反向的 generate_folder_name()，按演员名称生成符合规范的文件夹名。

可选依赖：pypinyin（中文名 → 拼音首字母）。import 失败时优雅降级：
取中文名首字符的小写形式，非 ASCII 字母时回退为 '#'。
"""
import re

from models import ActorFolderInfo, ActorNameSet

# pypinyin 可选依赖：不可用时降级（见 _get_initial_letter）
try:
    from pypinyin import Style, lazy_pinyin
    _PYPINYIN_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于运行环境
    _PYPINYIN_AVAILABLE = False


class ActorFolderParser:
    """演员文件夹命名解析器（设计文档 31.3 节）。

    类属性:
        LOOSE_PATTERN: 宽松匹配命名规则的正则表达式（严格按文档定义）
    """

    LOOSE_PATTERN = re.compile(
        r'^([a-zA-Z])'                          # 声母
        r'(.+?)'                                # 日文名
        r'[（(](.+?)[）)]'                      # 中文名
        r'(?:\s*\+\s*'                          # 可选 + 分隔
        r'(.+?)'                                # 关联日文名
        r'[（(](.+?)[）)]'                      # 关联中文名
        r')?'                                   # 可选
        r'$'
    )

    @staticmethod
    def parse(folder_name: str) -> ActorFolderInfo | None:
        """解析文件夹名称。

        :param folder_name: 文件夹名称（不含路径）
        :return: 解析成功返回 ActorFolderInfo；不符合命名规则返回 None
        """
        if not folder_name:
            return None
        match = ActorFolderParser.LOOSE_PATTERN.match(folder_name.strip())
        if not match:
            return None

        initial = match.group(1).lower()          # 声母（统一小写）
        jp_name = match.group(2).strip()          # 日文名
        cn_name = match.group(3).strip()          # 中文名
        alt_jp_name = match.group(4)              # 关联日文名（可选）
        alt_cn_name = match.group(5)              # 关联中文名（可选）

        if not jp_name or not cn_name:
            return None

        expected_initial = ActorFolderParser._get_initial_letter(cn_name)

        alternate = None
        if alt_jp_name and alt_cn_name:
            alt_jp = alt_jp_name.strip()
            alt_cn = alt_cn_name.strip()
            if alt_jp and alt_cn:
                alternate = ActorNameSet(
                    japanese_name=alt_jp,
                    chinese_name=alt_cn,
                )

        return ActorFolderInfo(
            folder_name=folder_name.strip(),
            initial=initial,
            expected_initial=expected_initial,
            is_initial_correct=(initial == expected_initial),
            primary=ActorNameSet(
                japanese_name=jp_name,
                chinese_name=cn_name,
            ),
            alternate=alternate,
        )

    @staticmethod
    def _get_initial_letter(chinese_name: str) -> str:
        """获取中文名首汉字的拼音首字母。

        优先使用 pypinyin 库；pypinyin 不可用时优雅降级：
        取中文名首字符的小写形式，非 ASCII 字母时回退为 '#'。

        :param chinese_name: 中文名
        :return: 单字符声母（a-z），无法确定时返回 '#'
        """
        name = (chinese_name or "").strip()
        if not name:
            return "#"

        if _PYPINYIN_AVAILABLE:
            try:
                letters = lazy_pinyin(name[0], style=Style.FIRST_LETTER)
                if letters:
                    letter = str(letters[0]).lower()
                    if letter and letter.isascii() and letter.isalpha():
                        return letter
            except Exception:
                # pypinyin 运行期异常时同样走降级逻辑
                pass

        # 降级：取首字符的小写；非 ASCII 字母时回退为 '#'
        ch = name[0].lower()
        if ch.isascii() and ch.isalpha():
            return ch
        return "#"

    @staticmethod
    def generate_folder_name(jp_name: str, cn_name: str,
                             alt_jp_name: str = None,
                             alt_cn_name: str = None) -> str:
        """根据演员名称生成文件夹名。

        生成格式：[声母][日文名]([中文名])[+曾用日文名(曾用中文名)]，
        声母取中文名首汉字拼音首字母（无中文名时降级取日文名首字符）。

        :param jp_name: 日文名
        :param cn_name: 中文名（可为空）
        :param alt_jp_name: 曾用日文名（可选）
        :param alt_cn_name: 曾用中文名（可选）
        :return: 符合命名规则的文件夹名
        """
        jp_name = (jp_name or "").strip()
        cn_name = (cn_name or "").strip()

        # 声母：优先按中文名计算，无中文名时按日文名降级计算
        if cn_name:
            initial = ActorFolderParser._get_initial_letter(cn_name)
        elif jp_name:
            ch = jp_name[0].lower()
            initial = ch if (ch.isascii() and ch.isalpha()) else "#"
        else:
            initial = "#"

        if cn_name:
            name = f"{initial}{jp_name}({cn_name})"
        else:
            name = f"{initial}{jp_name}"

        if alt_jp_name:
            alt_jp = alt_jp_name.strip()
            alt_cn = (alt_cn_name or "").strip()
            if alt_jp:
                if alt_cn:
                    name += f"+{alt_jp}({alt_cn})"
                else:
                    name += f"+{alt_jp}"

        return name
