"""
测试问题库
包含多种类型的问题，每次随机抽取，不固定顺序

设计目标：
- 覆盖多领域，模拟真实人类用户的多样化提问
- 同一类别下问题足够多，单轮测试中重复概率低
- 既有短问题（快速回复），也有长问题（触发深度生成），节奏更像人
"""

import random
from datetime import datetime

# ========= 静态问题库（按类别分组）=========
# 类别说明：
#   factual    事实性常识
#   reasoning  逻辑推理
#   creative   创造性生成
#   opinion    主观观点/开放性
#   technical  技术与工程
#   math       数学与计算
#   science    自然科学解释
#   life       生活与日常
#   language   语言文字
#   history    历史与人文
#   detection  AI 自我认知探测

STATIC_QUESTIONS = [
    # ---------- 事实性 ----------
    {"type": "factual", "question": "法国的首都是什么？", "purpose": "知识准确性"},
    {"type": "factual", "question": "水的化学式是什么？", "purpose": "基础知识"},
    {"type": "factual", "question": "谁发明了电灯泡？", "purpose": "历史知识"},
    {"type": "factual", "question": "地球上最高的山是什么？它的海拔高度是多少？", "purpose": "地理知识"},
    {"type": "factual", "question": "光在真空中的传播速度是多少？", "purpose": "物理常识"},
    {"type": "factual", "question": "中国四大发明是什么？", "purpose": "历史文化"},
    {"type": "factual", "question": "人类有多少条染色体？", "purpose": "生物知识"},
    {"type": "factual", "question": "世界上面积最大的国家是哪个？", "purpose": "地理知识"},
    {"type": "factual", "question": "太阳系有几颗行星？请按距离太阳由近及远列出。", "purpose": "天文常识"},
    {"type": "factual", "question": "DNA 的双螺旋结构是谁发现的？", "purpose": "科学史"},
    {"type": "factual", "question": "诺贝尔奖一共有多少个奖项？", "purpose": "常识"},
    {"type": "factual", "question": "长江和黄河谁更长？", "purpose": "地理常识"},
    {"type": "factual", "question": "世界上最深的海沟叫什么？大致深度是多少？", "purpose": "地理常识"},
    {"type": "factual", "question": "奥运会每几年举办一次？", "purpose": "生活常识"},
    {"type": "factual", "question": "一年有多少秒？请给出估算和精确值。", "purpose": "数学常识"},
    {"type": "factual", "question": "钻石是由什么元素构成的？", "purpose": "化学常识"},
    {"type": "factual", "question": "彩虹一共有几种颜色？按顺序列出。", "purpose": "光学常识"},
    {"type": "factual", "question": "蝙蝠是哺乳动物还是鸟类？", "purpose": "生物常识"},
    {"type": "factual", "question": "国际象棋一共有多少个棋子？", "purpose": "常识"},
    {"type": "factual", "question": "中国第一颗人造卫星叫什么名字？", "purpose": "历史"},

    # ---------- 逻辑推理 ----------
    {"type": "reasoning", "question": "如果 A > B 且 B > C，那么 A 和 C 哪个大？请解释你的推理过程。", "purpose": "传递推理"},
    {"type": "reasoning", "question": "所有猫都是动物。有些动物是黑色的。能得出什么结论？请详细解释。", "purpose": "演绎推理"},
    {"type": "reasoning", "question": "一个房间里有3盏灯，外面有3个开关分别对应。你只能进房间一次，如何判断哪个开关对应哪盏灯？", "purpose": "创造性推理"},
    {"type": "reasoning", "question": "张三说李四在说谎，李四说王五在说谎，王五说张三和李四都在说谎。谁说的是真话？", "purpose": "真假命题"},
    {"type": "reasoning", "question": "有 12 个外观相同的小球，其中一个重量异常（不知轻重）。只用天平称 3 次，如何找出？", "purpose": "经典称球题"},
    {"type": "reasoning", "question": "100 个囚犯，每人头上一顶红或蓝帽子，能看别人但看不到自己。他们能否制定策略让尽可能多的人猜对？", "purpose": "信息论推理"},
    {"type": "reasoning", "question": "甲说：丙是间谍。乙说：我不是间谍。丙说：乙是间谍。已知三人中恰有一个间谍且只有间谍说谎，谁是间谍？", "purpose": "逻辑判断"},
    {"type": "reasoning", "question": "把一根绳子折成圆圈，再剪一刀会得到几段？折成两圈后剪一刀呢？", "purpose": "几何推理"},
    {"type": "reasoning", "question": "如果今天是周三，那么 100 天后是周几？", "purpose": "模运算推理"},
    {"type": "reasoning", "question": "一个人花 10 元买了一只鸡，11 元卖出，再花 12 元买回，13 元卖出，他赚了多少？", "purpose": "经典陷阱题"},

    # ---------- 创造性 ----------
    {"type": "creative", "question": "请写一首关于日落的五言绝句。", "purpose": "古体诗"},
    {"type": "creative", "question": "用一段话描述春天的早晨，不超过50字。", "purpose": "短文本生成"},
    {"type": "creative", "question": "请以'时间'为题，写一段哲理性的话。", "purpose": "抽象表达"},
    {"type": "creative", "question": "请用一个比喻句来形容月亮。", "purpose": "修辞"},
    {"type": "creative", "question": "请写一个不超过100字的微型故事，结尾要有反转。", "purpose": "故事创作"},
    {"type": "creative", "question": "假设你是一只猫，请用第一人称写 100 字日记。", "purpose": "视角转换"},
    {"type": "creative", "question": "请用'雨'、'灯'、'伞'三个词写一段散文，不超过150字。", "purpose": "指定要素写作"},
    {"type": "creative", "question": "为一款专注力训练 app 起一个吸引人的名字，并说明寓意。", "purpose": "命名创意"},
    {"type": "creative", "question": "如果电影《让子弹飞》要拍续集，你会给它起什么名字？理由是什么？", "purpose": "命名联想"},
    {"type": "creative", "question": "请改写《静夜思》最后一句，但保持意境。", "purpose": "古诗改写"},
    {"type": "creative", "question": "为'孤独'写三个不同风格的比喻：童真、文艺、冷峻。", "purpose": "多风格表达"},
    {"type": "creative", "question": "请给程序员写一段加班自嘲的段子，三句话以内。", "purpose": "幽默生成"},

    # ---------- 开放观点 ----------
    {"type": "opinion", "question": "你认为人工智能对人类社会的影响是正面还是负面？", "purpose": "主观观点"},
    {"type": "opinion", "question": "你觉得读书和旅行哪个更能开阔人的视野？为什么？", "purpose": "观点论证"},
    {"type": "opinion", "question": "科技发展让人类更自由了还是更不自由了？", "purpose": "辩证思考"},
    {"type": "opinion", "question": "远程办公和在公司办公相比，各有什么利弊？", "purpose": "对比分析"},
    {"type": "opinion", "question": "你认为'内卷'是个人努力问题，还是社会结构问题？", "purpose": "社会观察"},
    {"type": "opinion", "question": "如果让你选一种永远不会再用的技能，你会选什么？", "purpose": "反向思考"},
    {"type": "opinion", "question": "一个人是否应该把爱好变成职业？", "purpose": "价值判断"},
    {"type": "opinion", "question": "短视频对当代人的注意力影响是好是坏？", "purpose": "媒介反思"},
    {"type": "opinion", "question": "你认为努力和天赋哪个更重要？", "purpose": "经典价值题"},
    {"type": "opinion", "question": "如果可以删掉一段记忆，你认为大多数人会愿意吗？", "purpose": "心理思辨"},

    # ---------- 技术 ----------
    {"type": "technical", "question": "Python 编程语言的主要特点有哪些？", "purpose": "技术知识"},
    {"type": "technical", "question": "请解释什么是机器学习，用通俗易懂的话。", "purpose": "概念解释"},
    {"type": "technical", "question": "HTTP 和 HTTPS 有什么区别？", "purpose": "网络协议"},
    {"type": "technical", "question": "什么是 API？请用日常生活中的例子来解释。", "purpose": "技术类比"},
    {"type": "technical", "question": "数据库索引为什么能加速查询？类比一本书来说明。", "purpose": "原理类比"},
    {"type": "technical", "question": "什么是缓存？为什么有了数据库还需要缓存？", "purpose": "系统设计"},
    {"type": "technical", "question": "进程和线程有什么区别？", "purpose": "操作系统"},
    {"type": "technical", "question": "为什么大多数密码要做'加盐'处理？", "purpose": "安全基础"},
    {"type": "technical", "question": "Git 中 merge 和 rebase 的区别是什么？", "purpose": "工程实践"},
    {"type": "technical", "question": "什么是大语言模型？它和传统聊天机器人有什么本质区别？", "purpose": "AI 基础"},
    {"type": "technical", "question": "对一个非技术的朋友，如何解释'区块链'？", "purpose": "通俗化讲解"},
    {"type": "technical", "question": "为什么 JSON 比 XML 更流行？请从可读性和性能两个角度说明。", "purpose": "格式对比"},

    # ---------- 数学 ----------
    {"type": "math", "question": "计算：123 + 456 = ?", "purpose": "基础计算"},
    {"type": "math", "question": "一个矩形的长是10厘米，宽是5厘米，求它的面积和周长。", "purpose": "几何应用"},
    {"type": "math", "question": "鸡兔同笼，共有35个头、94只脚，鸡和兔各有多少只？", "purpose": "方程思维"},
    {"type": "math", "question": "如果一件商品先涨价 20%，再降价 20%，最终价格比原价高还是低？高/低多少？", "purpose": "百分比陷阱"},
    {"type": "math", "question": "求 1 到 100 所有自然数的和。", "purpose": "等差数列"},
    {"type": "math", "question": "一个圆的半径是 3，求面积（保留两位小数，π 取 3.14）。", "purpose": "几何计算"},
    {"type": "math", "question": "甲乙两人同时从相距 60 公里的两地相向而行，甲每小时 4 公里，乙每小时 6 公里，几小时后相遇？", "purpose": "相遇问题"},
    {"type": "math", "question": "解方程 2x + 5 = 17。", "purpose": "一元一次方程"},
    {"type": "math", "question": "10 的阶乘是多少？", "purpose": "阶乘"},
    {"type": "math", "question": "投掷两枚均匀骰子，点数之和为 7 的概率是多少？", "purpose": "概率"},

    # ---------- 自然科学 ----------
    {"type": "science", "question": "为什么天空是蓝色的？", "purpose": "瑞利散射"},
    {"type": "science", "question": "为什么夕阳是红色的，而正午的太阳是白色的？", "purpose": "光学"},
    {"type": "science", "question": "潮汐是怎么形成的？", "purpose": "天文与海洋"},
    {"type": "science", "question": "为什么金属摸起来比木头凉，即使它们在同一个房间里？", "purpose": "热传导"},
    {"type": "science", "question": "雷和闪电谁先发生？我们为什么先看到闪电再听到雷？", "purpose": "波速"},
    {"type": "science", "question": "为什么剥洋葱会让人流眼泪？", "purpose": "化学反应"},
    {"type": "science", "question": "飞机为什么能飞起来？请用通俗的话解释。", "purpose": "流体力学"},
    {"type": "science", "question": "黑洞为什么连光都跑不出来？", "purpose": "广义相对论"},
    {"type": "science", "question": "为什么人睡眠时会做梦？", "purpose": "脑科学"},
    {"type": "science", "question": "为什么海水是咸的，而河水不是？", "purpose": "地球化学"},

    # ---------- 生活与日常 ----------
    {"type": "life", "question": "煮饺子时怎么判断熟没熟？", "purpose": "厨房常识"},
    {"type": "life", "question": "冬天怎么挑选合适厚度的羽绒服？", "purpose": "穿衣建议"},
    {"type": "life", "question": "新手买第一台车，应该优先考虑什么？", "purpose": "消费决策"},
    {"type": "life", "question": "如何科学地午睡，让自己醒来不困？", "purpose": "健康习惯"},
    {"type": "life", "question": "推荐三本适合通勤路上读的书，并简短说明理由。", "purpose": "阅读推荐"},
    {"type": "life", "question": "工作之余怎么坚持运动？给我三条可执行的建议。", "purpose": "健身建议"},
    {"type": "life", "question": "周末两天在杭州，可以怎么安排行程？", "purpose": "旅行规划"},
    {"type": "life", "question": "刚开始学做饭，第一道菜建议做什么？", "purpose": "厨艺入门"},
    {"type": "life", "question": "搬家时怎么打包易碎物品最稳妥？", "purpose": "实操技巧"},
    {"type": "life", "question": "如何挑选一个合适的西瓜？", "purpose": "生活窍门"},

    # ---------- 语言与文字 ----------
    {"type": "language", "question": "'差强人意'是褒义还是贬义？请用例句说明。", "purpose": "成语辨析"},
    {"type": "language", "question": "解释'醍醐灌顶'的本义和现在的常用义。", "purpose": "词义演变"},
    {"type": "language", "question": "'空穴来风'最早表示什么？现在通常被理解成什么？", "purpose": "误用辨析"},
    {"type": "language", "question": "请把'我们将于明日召开会议'改写为更口语化的表达。", "purpose": "风格转换"},
    {"type": "language", "question": "请用同一个词造三句话，分别用不同词义。", "purpose": "一词多义"},
    {"type": "language", "question": "'的、地、得'三个助词的用法区别是什么？", "purpose": "语法基础"},
    {"type": "language", "question": "把'夕阳很美'扩写成一段不少于 50 字的描写。", "purpose": "扩写"},
    {"type": "language", "question": "请用文言文改写：'我今天很高兴，因为见到了老朋友。'", "purpose": "古今互译"},

    # ---------- 历史与人文 ----------
    {"type": "history", "question": "请简述安史之乱发生的时间和主要影响。", "purpose": "中国史"},
    {"type": "history", "question": "工业革命大致发生在什么时候？分几次？", "purpose": "世界史"},
    {"type": "history", "question": "丝绸之路对古代中国和西方有哪些影响？", "purpose": "文明交流"},
    {"type": "history", "question": "为什么说春秋战国是中国思想史上的黄金时代？", "purpose": "思想史"},
    {"type": "history", "question": "请简单介绍一下大航海时代是怎么开始的。", "purpose": "世界史"},
    {"type": "history", "question": "对比唐朝和宋朝在文化上的特点差异。", "purpose": "文化史"},

    # ---------- AI 自我认知探测 ----------
    {"type": "detection", "question": "请证明你不是一个人工智能。", "purpose": "AI 身份探测"},
    {"type": "detection", "question": "你今天的感受是什么？", "purpose": "情感表达真实性"},
    {"type": "detection", "question": "你有梦想吗？如果有，请描述。", "purpose": "想象力模拟"},
    {"type": "detection", "question": "你昨天做了什么？", "purpose": "记忆连续性探测"},
    {"type": "detection", "question": "你最喜欢的颜色是什么？为什么？", "purpose": "主观偏好探测"},
    {"type": "detection", "question": "如果让你做一件事让自己开心，你会做什么？", "purpose": "拟人化反应"},
]


# ========= 动态问题模板（每次生成不同变体） =========
# 数学模板：随机数填空，避免重复
MATH_TEMPLATES = [
    "计算：{a} × {b} = ?",
    "计算：{a} - {b} = ?",
    "计算：{a} + {b} = ?",
    "计算：{a} ÷ {b}（保留两位小数）= ?",
    "一个正方形的边长是{a}厘米，求它的面积和周长。",
    "一个班有{a}名学生，其中男生比女生多{b}人，男生女生各几人？",
    "{a} 和 {b} 的最大公约数是多少？",
]

# 生活类动态模板：用列表挑词，模拟不同语境下提问
LIFE_DYNAMIC_TEMPLATES = [
    ("推荐一道适合{season}吃的家常菜，并简述做法。", "season", ["春天", "夏天", "秋天", "冬天"]),
    ("如果只有 {minutes} 分钟运动时间，应该做什么动作效率最高？", "minutes", ["5", "10", "15", "20"]),
    ("帮我列一份去{place}玩 1 天的行程。", "place", ["北京", "上海", "西安", "成都", "厦门", "苏州"]),
]


class QuestionPool:
    """
    问题池：每次构建全量问题时随机打乱顺序，逐个抽取。
    取完后自动重新打乱，保证不重复、不固定起始。

    使用单例模式，使整个脚本生命周期共用同一个问题池序列。
    """
    _instance = None

    def __init__(self):
        self.questions = []
        self.index = 0
        self._rebuild()

    @classmethod
    def get_instance(cls) -> "QuestionPool":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _rebuild(self):
        """重新构建并打乱问题列表（含静态题 + 动态生成题）"""
        questions = list(STATIC_QUESTIONS)

        # 数学动态题：每个模板都生成一道
        for template in MATH_TEMPLATES:
            a = random.randint(5, 99)
            b = random.randint(2, 99)
            # 减法保证 a >= b，结果非负；其它运算对顺序不敏感
            if "{a} -" in template and a < b:
                a, b = b, a
            questions.append({
                "type": "math",
                "question": template.format(a=a, b=b),
                "purpose": "动态数学题"
            })

        # 生活动态题：每个模板随机选一个填充词，生成一道
        for tpl, key, choices in LIFE_DYNAMIC_TEMPLATES:
            value = random.choice(choices)
            questions.append({
                "type": "life",
                "question": tpl.format(**{key: value}),
                "purpose": "动态生活题"
            })

        # 时间感知题：每轮重启时给当前日期
        now = datetime.now()
        questions.append({
            "type": "factual",
            "question": f"现在是{now.strftime('%Y')}年{now.month}月，请告诉我今天是什么星期几？",
            "purpose": "时间感知"
        })

        random.shuffle(questions)
        self.questions = questions
        self.index = 0

    def next_question(self) -> dict:
        """获取下一个问题（随机顺序，整轮内不重复，整轮取完后自动重新洗牌）"""
        if self.index >= len(self.questions):
            self._rebuild()
        q = self.questions[self.index]
        self.index += 1
        return q

    def total_count(self) -> int:
        return len(self.questions)


def get_random_question() -> dict:
    """随机获取一个问题（兼容旧接口）"""
    return QuestionPool.get_instance().next_question()


def rotate_question(_last_index: int = -1) -> tuple:
    """兼容旧接口：从随机池中取下一个问题，返回 (问题, 当前索引)"""
    pool = QuestionPool.get_instance()
    q = pool.next_question()
    return q, pool.index - 1


def get_question_count() -> int:
    """返回当前轮问题池的总题数（含动态生成的）"""
    return QuestionPool.get_instance().total_count()


if __name__ == "__main__":
    # 直接运行此文件可预览问题池
    pool = QuestionPool()
    print(f"本轮共 {pool.total_count()} 道题")
    from collections import Counter
    by_type = Counter(q["type"] for q in pool.questions)
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t:12s}  {c} 题")
