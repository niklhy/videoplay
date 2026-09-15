# -*- coding: utf-8 -*-
"""日文读音排序工具（对应设计文档 26 号 31.6 节）。

按假名五十音图顺序为日文人名生成排序键，支持演员列表的多字段排序：

- japanese_name：按日文读音（假名五十音图顺序）排序
- chinese_name：按拼音首字母排序（pypinyin 不可用时降级按 Unicode 码位）
- initial_letter：按声母 A-Z 排序
- id / created_at：按数值 / 时间排序

可选依赖：pypinyin（中文名 → 拼音首字母）。import 失败时优雅降级。
"""
from datetime import datetime

from models import Actor

# pypinyin 可选依赖：不可用时中文名排序降级为按 Unicode 码位
try:
    from pypinyin import Style, lazy_pinyin
    _PYPINYIN_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于运行环境
    _PYPINYIN_AVAILABLE = False


class JapaneseSort:
    """日文排序工具：按假名五十音图顺序排序（设计文档 31.6 节）。

    类属性:
        GOJUON_ORDER: 假名 -> 五十音行内序号的映射，
                      覆盖 あ～を 的平假名、片假名及浊音/半浊音，
                      行值编号与文档一致（あ行 1-5、か行 6-10、
                      さ行 11-15、た行 16-20、な行 21-25、は行 26-30、
                      ま行 31-35、や行 36-38、ら行 41-45、わ行 46-47），
                      同组浊音/半浊音与清音共享行值（如 が 与 か 同为 6）。
    """

    GOJUON_ORDER = {
        # あ行（1-5）
        'あ': 1, 'い': 2, 'う': 3, 'え': 4, 'お': 5,
        'ア': 1, 'イ': 2, 'ウ': 3, 'エ': 4, 'オ': 5,
        'ぁ': 1, 'ぃ': 2, 'ぅ': 3, 'ぇ': 4, 'ぉ': 5,
        'ァ': 1, 'ィ': 2, 'ゥ': 3, 'ェ': 4, 'ォ': 5,
        # か行（6-10）
        'か': 6, 'き': 7, 'く': 8, 'け': 9, 'こ': 10,
        'カ': 6, 'キ': 7, 'ク': 8, 'ケ': 9, 'コ': 10,
        'が': 6, 'ぎ': 7, 'ぐ': 8, 'げ': 9, 'ご': 10,
        'ガ': 6, 'ギ': 7, 'グ': 8, 'ゲ': 9, 'ゴ': 10,
        # さ行（11-15）
        'さ': 11, 'し': 12, 'す': 13, 'せ': 14, 'そ': 15,
        'サ': 11, 'シ': 12, 'ス': 13, 'セ': 14, 'ソ': 15,
        'ざ': 11, 'じ': 12, 'ず': 13, 'ぜ': 14, 'ぞ': 15,
        'ザ': 11, 'ジ': 12, 'ズ': 13, 'ゼ': 14, 'ゾ': 15,
        # た行（16-20）
        'た': 16, 'ち': 17, 'つ': 18, 'て': 19, 'と': 20,
        'タ': 16, 'チ': 17, 'ツ': 18, 'テ': 19, 'ト': 20,
        'だ': 16, 'ぢ': 17, 'づ': 18, 'で': 19, 'ど': 20,
        'ダ': 16, 'ヂ': 17, 'ヅ': 18, 'デ': 19, 'ド': 20,
        'っ': 18, 'ッ': 18,
        # な行（21-25）
        'な': 21, 'に': 22, 'ぬ': 23, 'ね': 24, 'の': 25,
        'ナ': 21, 'ニ': 22, 'ヌ': 23, 'ネ': 24, 'ノ': 25,
        # は行（26-30）
        'は': 26, 'ひ': 27, 'ふ': 28, 'へ': 29, 'ほ': 30,
        'ハ': 26, 'ヒ': 27, 'フ': 28, 'ヘ': 29, 'ホ': 30,
        'ば': 26, 'び': 27, 'ぶ': 28, 'べ': 29, 'ぼ': 30,
        'バ': 26, 'ビ': 27, 'ブ': 28, 'ベ': 29, 'ボ': 30,
        'ぱ': 26, 'ぴ': 27, 'ぷ': 28, 'ぺ': 29, 'ぽ': 30,
        'パ': 26, 'ピ': 27, 'プ': 28, 'ペ': 29, 'ポ': 30,
        # ま行（31-35）
        'ま': 31, 'み': 32, 'む': 33, 'め': 34, 'も': 35,
        'マ': 31, 'ミ': 32, 'ム': 33, 'メ': 34, 'モ': 35,
        # や行（36-38）
        'や': 36, 'ゆ': 37, 'よ': 38,
        'ヤ': 36, 'ユ': 37, 'ヨ': 38,
        'ゃ': 36, 'ゅ': 37, 'ょ': 38,
        'ャ': 36, 'ュ': 37, 'ョ': 38,
        # ら行（41-45）
        'ら': 41, 'り': 42, 'る': 43, 'れ': 44, 'ろ': 45,
        'ラ': 41, 'リ': 42, 'ル': 43, 'レ': 44, 'ロ': 45,
        # わ行（46-47）
        'わ': 46, 'を': 47,
        'ワ': 46, 'ヲ': 47,
        'ゎ': 46, 'ヮ': 46,
    }

    # 非假名字符排序前缀：以 '9' 开头，保证排在任何五十音序号（"001"-"047"）之后
    _NON_KANA_PREFIX = "9"

    @staticmethod
    def get_sort_key(text: str) -> str:
        """获取日文排序键。

        逐字符将假名映射为五十音行内序号（定宽三位数字符串）；
        非假名字符以 '9' + Unicode 码位映射到假名之后，保证日文名
        按读音排在前面，且非假名部分仍保持确定性顺序。

        :param text: 待排序文本（通常为日文名）
        :return: 可字典序比较的排序键字符串
        """
        if not text:
            return ""
        parts = []
        for ch in text:
            order = JapaneseSort.GOJUON_ORDER.get(ch)
            if order is not None:
                parts.append(f"{order:03d}")
            else:
                parts.append(f"{JapaneseSort._NON_KANA_PREFIX}{ord(ch):06x}")
        return "".join(parts)

    @staticmethod
    def _get_pinyin_key(text: str) -> str:
        """获取中文名拼音首字母排序键。

        pypinyin 可用时取各汉字拼音首字母拼接；
        不可用时降级为原文本本身（按 Unicode 码位排序）。

        :param text: 中文名
        :return: 排序键字符串
        """
        text = text or ""
        if not text:
            return ""
        if _PYPINYIN_AVAILABLE:
            try:
                letters = lazy_pinyin(text, style=Style.FIRST_LETTER)
                return "".join(str(x).lower() for x in letters)
            except Exception:
                pass
        return text

    @staticmethod
    def sort_actors(actors: list, sort_by: str = "japanese_name",
                    order: str = "asc") -> list:
        """排序演员列表。

        :param actors: Actor 列表（不修改原列表，返回新列表）
        :param sort_by: 排序字段，可选：
            "id"（数据库自增ID，数字排序）、
            "japanese_name"（日文读音，五十音图顺序）、
            "chinese_name"（中文拼音首字母）、
            "initial_letter"（声母 A-Z 顺序）、
            "created_at"（创建时间）
        :param order: "asc"（升序，默认）或 "desc"（降序）
        :return: 排序后的 Actor 新列表
        """
        reverse = (str(order).lower() == "desc")

        if sort_by == "id":
            key_func = lambda a: (a.actor_id if a.actor_id is not None else 0)
        elif sort_by == "chinese_name":
            key_func = lambda a: (JapaneseSort._get_pinyin_key(a.chinese_name),
                                  JapaneseSort.get_sort_key(a.japanese_name))
        elif sort_by == "initial_letter":
            key_func = lambda a: ((a.initial_letter or "").lower(),
                                  JapaneseSort.get_sort_key(a.japanese_name))
        elif sort_by == "created_at":
            key_func = lambda a: (a.created_at or datetime.min)
        else:
            # 默认及 japanese_name：按日文读音（五十音图顺序）排序
            key_func = lambda a: (JapaneseSort.get_sort_key(a.japanese_name),
                                  a.japanese_name or "")

        return sorted(actors, key=key_func, reverse=reverse)
