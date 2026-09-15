# -*- coding: utf-8 -*-
"""番号识别引擎模块（对应设计文档第 29 章 / 模块文件 24 号）。

三层架构：
    Layer 1 规则引擎（RuleEngine）      —— 正则快速匹配，覆盖常见模式
    Layer 2 深度学习（DLModel）         —— 字符级 BiLSTM+CRF（ONNX 推理，可选加载）
    Layer 3 反馈校正（CorrectionDB）    —— 人工校正历史缓存（本地 SQLite）

统一入口为 PatternRecognitionEngine.extract_code()，严格按设计文档
29.4 节的三层流程执行；深度学习相关依赖（onnxruntime）缺失时优雅降级，
规则引擎始终可用。
"""
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 识别结果数据类（设计 29.4 节 RecognitionResult）
# ---------------------------------------------------------------------------
@dataclass
class RecognitionResult:
    """番号识别结果。

    :ivar code: 识别出的番号（规范化后为「前缀-数字」，如 ``ABF-087``），
        未识别时为空字符串
    :ivar confidence: 置信度，范围 [0.0, 1.0]
    :ivar source: 结果来源标识（correction_history / rule_engine:<规则名> /
        dl_model / fused 等）
    :ivar raw: 原始输入文件名
    """
    code: str = ""
    confidence: float = 0.0
    source: str = ""
    raw: str = ""

    def is_valid(self) -> bool:
        """是否识别出了有效番号。"""
        return bool(self.code) and self.confidence > 0.0


# ---------------------------------------------------------------------------
# 校正历史记录数据类（CorrectionDB 查询返回结构）
# ---------------------------------------------------------------------------
@dataclass
class CorrectionRecord:
    """单条人工校正历史记录。

    :ivar filename: 原始文件名
    :ivar predicted_code: 引擎预测番号
    :ivar corrected_code: 用户校正后的番号
    :ivar context: 校正场景（如 unknown / gallery / crawler）
    :ivar created_at: 记录创建时间（文本形式）
    :ivar confidence: 校正记录的可信度，人工校正视为权威来源，固定 1.0
    """
    filename: str = ""
    predicted_code: str = ""
    corrected_code: str = ""
    context: str = ""
    created_at: str = ""
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Layer 3: 反馈校正层（CorrectionDB）
# ---------------------------------------------------------------------------
class CorrectionDB:
    """校正历史数据库（基于 db_manager 的通用 execute/query 接口）。

    独立维护 ``correction_history`` 表，思路参考 db_manager 中的
    ``custom_codes`` 表，但存储的是「预测 → 校正」的反馈记录，
    供增量训练与相似模式自动修正使用。
    """

    TABLE_NAME = "correction_history"

    def __init__(self, db_manager):
        """初始化并确保 correction_history 表存在。

        :param db_manager: DatabaseManager 实例（提供 execute/query 方法）
        """
        self.db_manager = db_manager
        self._ensure_table()

    def _ensure_table(self) -> None:
        """幂等创建校正历史表与文件名索引。"""
        self.db_manager.execute(
            """CREATE TABLE IF NOT EXISTS correction_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                predicted_code TEXT DEFAULT '',
                corrected_code TEXT DEFAULT '',
                context TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.db_manager.execute(
            "CREATE INDEX IF NOT EXISTS idx_correction_filename "
            "ON correction_history(filename)"
        )

    def find(self, filename: str) -> Optional[CorrectionRecord]:
        """查询指定文件名的最新一条校正记录。

        :param filename: 原始文件名
        :return: CorrectionRecord，无记录时返回 None
        """
        rows = self.db_manager.query(
            "SELECT filename, predicted_code, corrected_code, context, created_at "
            "FROM correction_history WHERE filename=? "
            "ORDER BY id DESC LIMIT 1",
            (filename,),
        )
        if not rows:
            return None
        row = rows[0]
        return CorrectionRecord(
            filename=row["filename"],
            predicted_code=row["predicted_code"] or "",
            corrected_code=row["corrected_code"] or "",
            context=row["context"] or "",
            created_at=str(row["created_at"] or ""),
            confidence=1.0,
        )

    def save(self, filename: str, predicted: str, corrected: str,
             context: str = "") -> None:
        """保存一条人工校正记录。

        :param filename: 原始文件名
        :param predicted: 引擎预测的番号
        :param corrected: 用户校正后的番号
        :param context: 校正场景
        """
        self.db_manager.execute(
            "INSERT INTO correction_history "
            "(filename, predicted_code, corrected_code, context, created_at) "
            "VALUES (?,?,?,?,?)",
            (filename, predicted or "", corrected or "", context or "",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

    def count(self) -> int:
        """返回校正记录总条数（供增量训练触发判断）。"""
        rows = self.db_manager.query(
            "SELECT COUNT(*) AS c FROM correction_history")
        return int(rows[0]["c"]) if rows else 0


# ---------------------------------------------------------------------------
# Layer 1: 规则引擎（RuleEngine）
# ---------------------------------------------------------------------------
class RuleEngine:
    """基于正则表达式的番号快速匹配引擎（设计 29.1 节 Layer 1）。

    内置能力：
        - 常见番号模式正则（XXX-NNN / XXX_NNN / XXXNNN / XX-NNN / XXX-NNNNN 等）
        - 前缀噪声过滤（[HD]、[FHD]、[4K]、【中文】等质量/语言标签）
        - 已知厂商代码白名单（命中白名单时置信度 0.95+）
    """

    # 已知厂商代码白名单（常见番号前缀，全部大写）
    VENDOR_PREFIXES = frozenset({
        "ABF", "ABC", "ABP", "ABS", "ADN", "AGEM", "AI", "AKA", "ALDN",
        "AMBI", "AOI", "AP", "APAA", "APAK", "APKH", "APNS", "AQSH",
        "ARM", "ATID", "ATOM", "AUKG", "AVOP", "AWT", "AYB", "BADA",
        "BBAN", "BBI", "BDA", "BF", "BIJN", "BLK", "BOKD", "BONY",
        "CAND", "CAPIW", "CAWD", "CESD", "CHN", "CJOD", "CLUB", "CMC",
        "CND", "CORE", "CWP", "DAZD", "DDH", "DDK", "DDT", "DGL",
        "DIC", "DLDSS", "DMM", "DNJR", "DOCP", "DOKS", "DRPT", "DVDMS",
        "DVMM", "EBOD", "EIMF", "EKDV", "EKW", "EMOI", "EQ", "EXVR",
        "FAA", "FC2", "FCDSS", "FCP", "FCTN", "FERA", "FHD", "FNEO",
        "FOCS", "FPRE", "FSDSS", "GAH", "GENM", "GESU", "GEXP", "GHKQ",
        "GIMG", "GVG", "HAWA", "HBAD", "HDKA", "HIBI", "HJMO", "HMN",
        "HND", "HOI", "HONB", "HUNT", "HYPN", "HZGB", "IENF", "IQQQ",
        "IPIT", "IPX", "IPTD", "IPZ", "JBD", "JFB", "JJDA", "JKSR",
        "JMSZ", "JUL", "JUY", "JUQ", "JUX", "KAVR", "KAWD", "KBI",
        "KH", "KIRE", "KSBJ", "LAA", "LIDM", "MADM", "MGT", "MIAE",
        "MIDE", "MIFD", "MIJ", "MIMK", "MIRD", "MIST", "MKMP", "MMB",
        "MMKS", "MMND", "MNTJ", "MOT", "MRHP", "MSFH", "MUDR", "MVSD",
        "MXGS", "NGOD", "NKKD", "NNPJ", "NSPS", "NTK", "NTTR", "NXG",
        "OBA", "OKAX", "OKS", "ONEZ", "OOMN", "PBD", "PDV", "PGD",
        "PKPL", "PRED", "PPPE", "QBD", "QNME", "REAL", "ROYD", "RBD",
        "REBDB", "RCTD", "ROYD", "SDDE", "SDJS", "SDMM", "SDMU", "SDNM",
        "SHKD", "SIM", "SIS", "SIVR", "SNIS", "SOAV", "SONE", "SSNI",
        "SSPD", "STARS", "STSK", "SW", "TAAK", "TIKB", "TSP", "TYSF",
        "URKK", "USBA", "VDD", "VEMA", "VENX", "VRTM", "WANZ", "WFR",
        "XRW", "YMDD", "YRH", "YSN", "YST", "ZEX", "ZKWD", "ZUKO",
    })

    # 前缀噪声：方括号/圆括号/书名号包裹的质量、语言、来源标签
    _PREFIX_NOISE_RE = re.compile(
        r"^\s*(?:\[[^\[\]]*\]|【[^【】]*】|\([^()]*\)|（[^（）]*）)+\s*"
    )
    # 裸前缀噪声词（HD / FHD / 4K 等后接分隔符），循环剥离
    _BARE_PREFIX_NOISE_RE = re.compile(
        r"^(?:HD|FHD|UHD|4K|2K|BD|DL|HR|HQ|MV|中文|字幕|中字|无码|有码|高清)"
        r"[-_.\s]+",
        re.IGNORECASE,
    )

    def __init__(self):
        """初始化规则引擎并编译全部正则规则。

        规则按优先级排序，首个命中的规则决定结果；置信度策略：
            - 厂商白名单命中：0.95（规范连字符模式）/ 0.93（其他分隔符）
            - 一般 XXX-NNN 连字符模式：0.90
            - XXX_NNN 下划线模式：0.85
            - XXXNNN 紧凑模式：0.75
            - 带噪声前缀命中：在对应基础置信度上扣减 0.05
        """
        self._rules = [
            ("hyphen", re.compile(
                r"(?<![A-Za-z0-9])([A-Za-z]{2,5})-(\d{3,5})(?![0-9])")),
            ("underscore", re.compile(
                r"(?<![A-Za-z0-9])([A-Za-z]{2,5})_(\d{3,5})(?![0-9])")),
            ("compact", re.compile(
                r"(?<![A-Za-z0-9])([A-Za-z]{2,5})(\d{3,5})(?![A-Za-z0-9])")),
        ]
        # 扩展名（剥离用）
        self._ext_re = re.compile(r"\.[A-Za-z0-9]{1,5}$")

    # ------------------------------------------------------------------
    def _strip_noise_prefix(self, text: str) -> tuple:
        """剥离文件名前缀噪声（质量标签 / 语言标签）。

        :param text: 已去除扩展名的文件名
        :return: (剥离后的文本, 是否发生过剥离)
        """
        original = text
        # 先剥离 []【】()（） 包裹的标签组
        while True:
            stripped = self._PREFIX_NOISE_RE.sub("", text)
            if stripped == text:
                break
            text = stripped
        # 再剥离裸噪声词（如 "HD-"、"4K_"、"中文 "）
        while True:
            stripped = self._BARE_PREFIX_NOISE_RE.sub("", text)
            if stripped == text:
                break
            text = stripped
        return text, (text != original)

    # ------------------------------------------------------------------
    def match(self, filename: str) -> RecognitionResult:
        """对文件名执行规则匹配，返回识别结果。

        :param filename: 原始文件名（可含路径与扩展名）
        :return: RecognitionResult；未命中时 code 为空、confidence 为 0
        """
        raw = filename or ""
        base = os.path.basename(raw.strip())
        base = self._ext_re.sub("", base)
        cleaned, had_prefix_noise = self._strip_noise_prefix(base)

        for rule_name, pattern in self._rules:
            match_obj = pattern.search(cleaned)
            if match_obj is None:
                continue
            prefix = match_obj.group(1).upper()
            number = match_obj.group(2)
            code = "%s-%s" % (prefix, number)
            confidence = self._score(rule_name, prefix, had_prefix_noise)
            return RecognitionResult(
                code=code, confidence=confidence,
                source="rule_engine:%s" % rule_name, raw=raw,
            )

        return RecognitionResult(code="", confidence=0.0,
                                 source="rule_engine:none", raw=raw)

    # ------------------------------------------------------------------
    def _score(self, rule_name: str, prefix: str,
               had_prefix_noise: bool) -> float:
        """按规则类型与白名单命中情况计算置信度。

        :param rule_name: 命中规则名（hyphen / underscore / compact）
        :param prefix: 番号前缀（大写）
        :param had_prefix_noise: 是否剥离过前缀噪声
        :return: 置信度 [0.0, 1.0]
        """
        in_whitelist = prefix in self.VENDOR_PREFIXES
        if rule_name == "hyphen":
            confidence = 0.95 if in_whitelist else 0.90
        elif rule_name == "underscore":
            confidence = 0.93 if in_whitelist else 0.85
        else:  # compact
            confidence = 0.88 if in_whitelist else 0.75
        if had_prefix_noise and confidence > 0.8:
            # 带噪声前缀的命中略降置信度
            confidence -= 0.05
        return round(min(max(confidence, 0.0), 1.0), 4)


# ---------------------------------------------------------------------------
# Layer 2: 深度学习模型（DLModel，可选加载）
# ---------------------------------------------------------------------------
class DLModel:
    """字符级 BiLSTM+CRF 番号识别模型（ONNX Runtime 推理，可选加载）。

    设计 29.3 节：训练用 PyTorch，分发转换为 ONNX；推理仅依赖
    onnxruntime（约 20MB）。本类在库缺失或模型文件不存在时优雅降级：
    ``available`` 为 False，``predict`` 返回空结果，绝不抛异常。

    模型未训练时由规则引擎回退；本类已保留完整 ONNX 推理接口，
    未来放入训练好的 ``*.onnx`` 文件即可自动启用。
    """

    # 标签体系：字符级 BIO 标注（番号内部 B=起始 I=后续，O=其他）
    _LABELS = ("O", "B", "I")

    def __init__(self, model_path: str = "models/"):
        """尝试加载 ONNX 模型。

        :param model_path: 模型目录路径，目录内任意 ``*.onnx`` 文件都会被尝试加载
        """
        self.model_path = model_path
        self.available = False
        self._session = None
        self._model_file = ""
        self._load_error = ""

        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            self._load_error = "onnxruntime 未安装，深度学习引擎不可用"
            logger.info("DLModel: %s，使用规则引擎回退", self._load_error)
            return

        if not os.path.isdir(model_path):
            self._load_error = "模型目录不存在: %s" % model_path
            logger.info("DLModel: %s", self._load_error)
            return

        onnx_files = sorted(
            f for f in os.listdir(model_path) if f.lower().endswith(".onnx"))
        if not onnx_files:
            self._load_error = "模型目录中无 .onnx 文件: %s" % model_path
            logger.info("DLModel: %s", self._load_error)
            return

        for onnx_file in onnx_files:
            try:
                import onnxruntime as ort
                model_file = os.path.join(model_path, onnx_file)
                self._session = ort.InferenceSession(
                    model_file, providers=["CPUExecutionProvider"])
                self._model_file = model_file
                self.available = True
                logger.info("DLModel: 已加载 ONNX 模型 %s", model_file)
                return
            except Exception as exc:  # 模型损坏等异常，继续尝试下一个
                self._load_error = "模型加载失败: %s (%s)" % (onnx_file, exc)
                logger.warning("DLModel: %s", self._load_error)

    # ------------------------------------------------------------------
    def predict(self, filename: str) -> RecognitionResult:
        """对文件名执行深度学习推理。

        模型不可用 / 未训练 / 推理失败时返回 confidence=0 的空结果，
        由上层回退到规则引擎，绝不抛异常。

        :param filename: 原始文件名
        :return: RecognitionResult
        """
        raw = filename or ""
        if not self.available or self._session is None:
            return RecognitionResult(code="", confidence=0.0,
                                     source="dl_model:unavailable", raw=raw)
        try:
            base = os.path.basename(raw.strip())
            code = self._run_onnx_inference(base)
        except Exception as exc:
            logger.warning("DLModel: 推理失败（%s），回退规则引擎", exc)
            return RecognitionResult(code="", confidence=0.0,
                                     source="dl_model:error", raw=raw)
        if not code:
            return RecognitionResult(code="", confidence=0.0,
                                     source="dl_model:no_match", raw=raw)
        return RecognitionResult(code=code, confidence=0.80,
                                 source="dl_model", raw=raw)

    # ------------------------------------------------------------------
    def _run_onnx_inference(self, text: str) -> str:
        """执行 ONNX 会话推理并解码番号（BiLSTM+CRF BIO 序列解码）。

        预期模型输入：字符 id 序列（int64 [1, seq_len]）；
        预期模型输出：每个字符的标签 logits（float [1, seq_len, 3]），
        标签顺序对应 ``self._LABELS``（O / B / I）。解码时取每个字符
        最大概率标签，拼接 B..I 连续段作为番号候选。

        :param text: 文件名（不含扩展名）
        :return: 解码出的番号，无有效段时返回空字符串
        """
        inputs = self._session.get_inputs()
        if not inputs:
            return ""
        input_name = inputs[0].name
        # 模型未训练/占位时若输入名不符合约定，安全返回空
        seq = list(text)
        if not seq:
            return ""
        try:
            import numpy as np
            # 字符级 id：使用 ASCII 码映射，未来由词表文件替换
            char_ids = [[min(max(ord(c), 0), 0xFFFF) for c in seq]]
            logits = self._session.run(
                None, {input_name: np.array(char_ids, dtype="int64")})[0]
        except Exception:
            return ""
        if logits is None or len(logits) == 0:
            return ""
        # 取每个位置最大概率标签
        best = logits[0].argmax(axis=-1)
        segments = []
        current = []
        for idx, label_id in enumerate(best):
            label = self._LABELS[int(label_id)] \
                if int(label_id) < len(self._LABELS) else "O"
            if label == "B":
                if current:
                    segments.append("".join(current))
                current = [seq[idx]]
            elif label == "I" and current:
                current.append(seq[idx])
            else:
                if current:
                    segments.append("".join(current))
                    current = []
        if current:
            segments.append("".join(current))
        # 挑选最像番号的段（大写字母+数字组合），无则返回首个段
        code_re = re.compile(r"^[A-Za-z]{2,5}[-_]?\d{3,5}$")
        for segment in segments:
            candidate = segment.strip()
            if code_re.match(candidate):
                return candidate.upper().replace("_", "-")
        return ""


# ---------------------------------------------------------------------------
# 统一入口：PatternRecognitionEngine（设计 29.4 节）
# ---------------------------------------------------------------------------
class PatternRecognitionEngine:
    """番号识别统一入口，编排三层架构。

    使用示例::

        engine = PatternRecognitionEngine(db_manager)
        result = engine.extract_code("ABF-087-UC.mp4")
        # -> RecognitionResult(code='ABF-087', confidence=0.95, ...)
        engine.report_correction("ABF-087-UC.mp4", "ABF-087", "ABF-088",
                                 context="gallery")
    """

    # 高置信度直通阈值（设计 29.4 节 Step 1/2 使用）
    HIGH_CONFIDENCE_THRESHOLD = 0.9
    # 融合权重：规则引擎 / 深度学习
    RULE_WEIGHT = 0.6
    DL_WEIGHT = 0.4

    def __init__(self, db_manager, model_path: str = "models/"):
        """初始化三层组件。

        :param db_manager: DatabaseManager 实例（供 CorrectionDB 使用）
        :param model_path: ONNX 模型目录，深度学习模型为可选加载
        """
        self.rule_engine = RuleEngine()
        self.dl_model = DLModel(model_path)
        self.correction_db = CorrectionDB(db_manager)

    # ------------------------------------------------------------------
    def extract_code(self, filename: str,
                     context: str = "unknown") -> RecognitionResult:
        """统一番号提取入口（严格按设计 29.4 节三层流程）。

        Step 1: 查询校正历史，高置信度记录直接返回；
        Step 2: 规则引擎快速匹配，高置信度直接返回；
        Step 3: 深度学习模型推理并与规则结果加权融合。

        :param filename: 原始文件名
        :param context: 提取场景标识（如 unknown / gallery / crawler）
        :return: RecognitionResult
        """
        raw = filename or ""

        # Step 1: 校正历史（人工校正视为权威来源，confidence=1.0）
        history = self.correction_db.find(raw)
        if history and history.confidence > self.HIGH_CONFIDENCE_THRESHOLD \
                and history.corrected_code:
            return RecognitionResult(code=history.corrected_code,
                                     confidence=1.0,
                                     source="correction_history", raw=raw)

        # Step 2: 规则引擎快速匹配
        rule_result = self.rule_engine.match(raw)
        if rule_result.confidence > self.HIGH_CONFIDENCE_THRESHOLD:
            return rule_result

        # Step 3: 深度学习模型融合
        if self.dl_model.available:
            dl_result = self.dl_model.predict(raw)
            return self._fuse_results(rule_result, dl_result)
        return rule_result

    # ------------------------------------------------------------------
    def _fuse_results(self, rule_result: RecognitionResult,
                      dl_result: RecognitionResult) -> RecognitionResult:
        """融合规则引擎与深度学习结果（加权策略）。

        两路均未命中时返回规则的空结果；仅一路命中时取其结果；
        两路命中且番号一致时取较高置信度并小幅加成；
        不一致时按各自置信度加权，取加权值较高的一路番号。

        :param rule_result: 规则引擎结果
        :param dl_result: 深度学习结果
        :return: 融合后的 RecognitionResult
        """
        rule_valid = rule_result.is_valid()
        dl_valid = dl_result.is_valid()
        if not rule_valid and not dl_valid:
            return RecognitionResult(code="", confidence=0.0,
                                     source="fused:none", raw=rule_result.raw)
        if rule_valid and not dl_valid:
            return rule_result
        if dl_valid and not rule_valid:
            return dl_result
        if rule_result.code == dl_result.code:
            confidence = min(max(rule_result.confidence,
                                 dl_result.confidence) + 0.02, 1.0)
            return RecognitionResult(code=rule_result.code,
                                     confidence=round(confidence, 4),
                                     source="fused:agree", raw=rule_result.raw)
        # 番号不一致：按置信度加权选优
        rule_score = rule_result.confidence * self.RULE_WEIGHT
        dl_score = dl_result.confidence * self.DL_WEIGHT
        if rule_score >= dl_score:
            return RecognitionResult(
                code=rule_result.code,
                confidence=round(rule_result.confidence, 4),
                source="fused:rule_preferred", raw=rule_result.raw)
        return RecognitionResult(
            code=dl_result.code,
            confidence=round(dl_result.confidence, 4),
            source="fused:dl_preferred", raw=rule_result.raw)

    # ------------------------------------------------------------------
    def report_correction(self, filename: str, predicted: str,
                          corrected: str, context: str = "unknown") -> None:
        """用户人工校正后调用：记录校正并检查是否触发增量重训练。

        :param filename: 原始文件名
        :param predicted: 引擎预测的番号
        :param corrected: 用户校正后的番号
        :param context: 校正场景
        """
        self.correction_db.save(filename, predicted, corrected, context)
        logger.info("PatternRecognition: 记录校正 %s -> %s (%s)",
                    predicted, corrected, filename)
        self._check_and_trigger_retrain()

    # ------------------------------------------------------------------
    def _check_and_trigger_retrain(self) -> None:
        """检查校正样本量是否达到重训练阈值（当前仅记录日志，留接口）。

        未来增量训练实现后，在此触发样本导出与模型重训练流程；
        当前版本仅统计校正记录数并写日志。
        """
        try:
            total = self.correction_db.count()
        except Exception as exc:
            logger.warning("PatternRecognition: 校正计数失败: %s", exc)
            return
        logger.info("PatternRecognition: 当前校正样本 %d 条，"
                    "增量重训练接口待启用", total)
