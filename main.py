# -*- coding: utf-8 -*-
"""
FastAPI 入门 Demo —— 边写接口边学 Python
=========================================
这个文件既是一个可以运行的 FastAPI 应用，也是一份 Python 学习笔记。
每个接口都对应一个或几个 Python 核心知识点，注释里会明确标出。

启动方式（先在终端进入本目录）：
    uvicorn main:app --reload
    或直接运行  python main.py

启动后浏览器访问：
    http://127.0.0.1:8000/                  -> 根路径
    http://127.0.0.1:8000/hello/小明        -> 路径参数
    http://127.0.0.1:8000/items/42?q=abc    -> 路径参数 + 查询参数
    http://127.0.0.1:8000/docs             -> 自动生成的接口文档（Swagger）
    http://127.0.0.1:8000/python-demo      -> 用接口演示 Python 小技巧

知识点索引（对应代码中的注释）：
    1. import 导入机制
    2. 类的实例化（FastAPI(...)）
    3. 装饰器 @app.get
    4. 类型注解 Type Hints
    5. 路径参数与查询参数
    6. f-string 格式化字符串
    7. 类型转换与默认参数
    8. 异常处理 raise / HTTPException
    9. Pydantic 数据模型（对比 dataclass）
    10. async / await 异步编程
    11. 依赖注入 Depends（函数作为参数）
    12. 列表推导式 / 字典推导式 / enumerate / zip / lambda
    13. Annotated 参数元数据
    14. if __name__ == "__main__"
"""

# =========================================================
# 知识点 1：import 导入机制
# ---------------------------------------------------------
# 每个 .py 文件都是一个"模块"。import 可以把其他模块里的名字拿过来用。
# from xxx import yyy 表示只导入其中的一部分，更省内存、更清晰。
# =========================================================
import asyncio  # Python 标准库：异步编程支持
from typing import Annotated, Optional  # typing：类型注解工具

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field  # pydantic：数据校验库
from fastapi.responses import HTMLResponse  # 用于返回主页 HTML

# =========================================================
# 知识点 2：类的实例化
# ---------------------------------------------------------
# FastAPI(...) 是"调用类创建对象"的过程（构造对象）。
# 括号里的参数叫"关键字参数"（参数名=值），和位置参数相比更不容易传错。
# =========================================================
app = FastAPI(
    title="我的第一个 FastAPI 应用",
    description="边写接口边学 Python 的演示项目",
    version="1.0.0",
)

# =========================================================
# 附加功能：抖音视频下载器
# ---------------------------------------------------------
# 把独立的 douyin_downloader.py 里的 router 挂载到本应用上，
# 浏览器访问 http://127.0.0.1:8000/douyin-download 即可使用。
# =========================================================
from douyin_downloader import router as douyin_router  # noqa: E402
app.include_router(douyin_router)

# 附加功能：AI 数据图表分析（输入主题 -> 调用大模型 -> ECharts 图表 + Excel 导出）
# 浏览器访问 http://127.0.0.1:8000/ai-chart 即可使用。
from pathlib import Path  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
_static_dir = Path(__file__).resolve().parent / 'static'
app.mount('/static', StaticFiles(directory=str(_static_dir), check_dir=False), name='static')  # noqa: E402
from ai_chart import router as ai_chart_router  # noqa: E402
app.include_router(ai_chart_router)

# 附加功能：购物比价助手（输入商品与需求 -> 淘宝 / 京东 按销量价格对比 + 建议 + 购买链接）
# 浏览器访问 http://127.0.0.1:8000/buy-helper 即可使用。
from buy_helper import router as buy_helper_router  # noqa: E402
app.include_router(buy_helper_router)

# 附加功能：账单分析（上传 Excel/CSV -> DeepSeek 消费分类 -> 图表汇总 + 省钱建议）
# 浏览器访问 http://127.0.0.1:8000/bill-analysis 即可使用。
from bill_analysis import router as bill_router  # noqa: E402
app.include_router(bill_router)

# 附加功能：学习模型助手（提出想学的内容 -> 第一性原理 / 金字塔原理 / 贝叶斯定理 三视角拆解 -> 思维导图展示）
# 浏览器访问 http://127.0.0.1:8000/study-assistant 即可使用。
from study_assistant import router as study_router  # noqa: E402
app.include_router(study_router)

# 附加功能：翻译助手（输入中文 -> 免费公共接口 MyMemory 返回英文 -> 小写/大写/驼峰等格式一键复制，无需密钥）
# 浏览器访问 http://127.0.0.1:8000/translator 即可使用。
from translator import router as translator_router  # noqa: E402
app.include_router(translator_router)



# =========================================================
# 附加功能：工具箱主页（根路径 "/"）
# ---------------------------------------------------------
# 根路径不再返回 Hello World，而是返回一个"工具箱"导航页，
# 把 AI 生成图表、抖音下载器 两个常用入口集中在一起，方便查找。
# 知识点 4（类型注解）：函数签名 -> str 表示返回字符串（HTML）。
# =========================================================
_HOME_TEMPLATE = Path(__file__).resolve().parent / 'templates' / 'home.html'

_HOME_FALLBACK = (
    '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
    '<body style="background:#0a0f1e;color:#e8edf7;font-family:system-ui">'
    '<h2>Toolbox</h2>'
    '<p><a href="/ai-chart">AI Chart</a></p>'
    '<p><a href="/douyin-download">Douyin</a></p>'
    '</body></html>'
)


def _home_html() -> str:
    # 工具与本主页部署在同一个 FastAPI 应用里，链接统一用相对路径：
    # 本地运行是 http://127.0.0.1:8000/...，部署到 Vercel / 其它域名后会自动跟随当前域名，
    # 不再写死 127.0.0.1，因此换环境也不需要改代码。
    if _HOME_TEMPLATE.exists():
        return _HOME_TEMPLATE.read_text(encoding='utf-8')
    return _HOME_FALLBACK


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    # 返回工具箱主页 HTML（页面里的工具卡片地址由浏览器按当前域名动态补全）
    return _home_html()


# =========================================================
# 知识点 5：路径参数
# ---------------------------------------------------------
# URL 里的 {name} 是占位符，函数参数 name 会自动接收它。
# 注解 str 告诉 FastAPI 期望字符串，同时驱动类型转换和接口文档生成。
# =========================================================
@app.get("/hello/{name}")
def hello_name(name: str) -> dict:
    # 知识点 6：f-string 格式化字符串
    # f 前缀表示字符串里用 {变量} 直接插值，是 Python 3.6+ 最常用的写法。
    # 比老式 "你好，{}！".format(name) 更直观，还支持 {表达式}。
    return {"message": f"你好，{name}！"}


# =========================================================
# 知识点 7：类型转换 + 默认参数
# ---------------------------------------------------------
# item_id: int 声明为整数，浏览器传 42 会被自动转成 int；
# 传 abc 则校验失败，返回 422 状态码。
# q: str | None = None 表示：q 可以是字符串，也可以不传（取默认值 None）。
# "str | None" 是 Python 3.10+ 写法，等价于 Optional[str]。
# =========================================================
@app.get("/items/{item_id}")
def get_item(item_id: int, q: str | None = None) -> dict:
    return {
        "item_id": item_id,
        "item_id_type": type(item_id).__name__,  # 用 type() 查看实际类型
        "q": q,
    }


# =========================================================
# 知识点 7（续）：查询参数
# ---------------------------------------------------------
# URL 里 ?text=xxx 的部分会按参数名自动传给函数。
# 参数带默认值时可以不传；没有默认值的参数是必填的。
# =========================================================
@app.get("/echo")
def echo(text: str = "默认值") -> dict:
    # len() 内置函数：返回字符串/列表/字典等容器的长度
    return {"echo": text, "长度": len(text)}


# =========================================================
# 知识点 8：异常处理 raise
# ---------------------------------------------------------
# raise 用于主动抛出一个异常；HTTPException 是 FastAPI 提供的
# HTTP 异常类，抛出去后接口会返回对应的状态码和错误信息。
# =========================================================
@app.get("/calc")
def calc(a: int, b: int, op: str = "add") -> dict:
    if op == "add":
        result = a + b
    elif op == "sub":
        result = a - b
    elif op == "mul":
        result = a * b
    elif op == "div":
        if b == 0:  # 除数为 0 时主动报错，而不是让程序崩溃
            raise HTTPException(status_code=400, detail="除数不能为 0")
        result = a / b
    else:
        raise HTTPException(status_code=400, detail=f"不支持的运算符: {op}")

    return {"op": op, "result": result}


# =========================================================
# 知识点 9：Pydantic 数据模型（数据校验）
# ---------------------------------------------------------
# BaseModel 相当于 Python 自带 dataclass 的"增强版"：
#   1) 声明字段的同时声明类型；
#   2) 收到数据时自动校验类型和 Field 约束，不合法就返回 422；
#   3) 自带 .model_dump() 等方法方便序列化。
# 通常用它定义 POST 请求的"请求体"（Body）。
# =========================================================
class Item(BaseModel):
    # Field(..., 规则)：... 表示"必填"，后面可加约束和说明
    name: str = Field(..., min_length=1, max_length=20, description="商品名称")
    price: float = Field(..., gt=0, description="价格，必须大于 0")
    is_offer: bool = Field(default=False, description="是否促销")
    tags: list[str] = []  # Python 3.9+ 可以直接用 list[str] 声明元素类型


# POST 接口：FastAPI 看到参数是 Item 类型，就知道要去解析请求体并校验
@app.post("/items")
def create_item(item: Item) -> dict:
    # model_dump() 是 Pydantic v2 的方法：把模型对象转回普通字典
    return {"received": item.model_dump()}


# =========================================================
# 知识点 10：async / await 异步编程
# ---------------------------------------------------------
# async def 定义"协程函数"，函数体内可以用 await 等待耗时操作
# （查数据库、调外部 API、读写文件等 IO 操作）。
# 异步接口在等待期间不占着线程，可以同时处理大量请求，提升并发能力。
# =========================================================
@app.get("/async-hello")
async def async_hello() -> dict:
    # asyncio.sleep 模拟耗时 0.1 秒的 IO 操作；
    # await 表示"在这里挂起等待，但不阻塞整个服务器"
    await asyncio.sleep(0.1)
    return {"message": "这是 async 接口，等待期间不阻塞其他请求"}


# =========================================================
# 知识点 11：依赖注入 Depends（函数作为参数传递）
# ---------------------------------------------------------
# Python 里"函数也是对象"，可以像变量一样传来传去。
# Depends(get_db) 告诉 FastAPI：调用 get_user 之前，先调用 get_db
# 把结果作为 db 参数传进来。适合复用"连接数据库/校验登录"等公共逻辑。
# =========================================================
def get_db():
    # 真实项目里这里通常是"建立数据库连接"；用完记得关闭
    print("==> 打开数据库连接（模拟）")
    return {"db": "mysql", "status": "ok"}


@app.get("/users/{user_id}")
def get_user(user_id: int, db=Depends(get_db)) -> dict:
    return {"user_id": user_id, "db": db}


# =========================================================
# 知识点 12：Python 高频小技巧（用接口演示）
# ---------------------------------------------------------
# 列表推导式 / 字典推导式 / enumerate / zip / lambda
# 这些都是写 Python 时最常用的"一行式"写法，能大幅减少代码量。
# =========================================================
@app.get("/python-demo")
def python_demo() -> dict:
    numbers = [1, 2, 3, 4, 5, 6]

    # 列表推导式：等价于 "for 循环 + append" 的一行写法
    squares = [n * n for n in numbers]            # [1, 4, 9, 16, 25, 36]
    evens = [n for n in numbers if n % 2 == 0]    # 带 if 条件过滤

    # 字典推导式：快速构造 {键: 值} 映射
    square_map = {n: n * n for n in numbers}      # {1: 1, 2: 4, ...}

    # enumerate：同时拿到"下标"和"值"
    indexed = [(i, n) for i, n in enumerate(numbers)]

    # zip：把多个列表按位置"拉链"式配对
    names = ["张三", "李四", "王五"]
    scores = [88, 95, 72]
    zipped = list(zip(names, scores))             # [("张三", 88), ...]

    # lambda：匿名函数，常配合 sorted 做自定义排序
    people = [{"name": "张三", "age": 30}, {"name": "李四", "age": 25}]
    sorted_by_age = sorted(people, key=lambda p: p["age"])

    return {
        "squares": squares,
        "evens": evens,
        "square_map": square_map,
        "indexed": indexed,
        "zipped": zipped,
        "sorted_by_age": sorted_by_age,
    }


# =========================================================
# 知识点 13：Annotated（参数元数据，推荐写法）
# ---------------------------------------------------------
# Annotated[X, 元数据] 把"类型"和"附加规则"写在一起，让代码更集中。
# 下面的写法等价于旧写法：name: str = Path(min_length=2, max_length=10)
# =========================================================
@app.get("/fruits/{name}")
def get_fruit(
    name: Annotated[str, Path(min_length=2, max_length=10, description="水果名称")],
    ripe: Annotated[bool, Query(description="是否成熟")] = True,
) -> dict:
    return {"fruit": name, "ripe": ripe}


@app.get("/test1")
def getParam(name:str) :
    person=[2,3,5,6]
    var = person[2]
    return var


# =========================================================
# 知识点 14：if __name__ == "__main__"
# ---------------------------------------------------------
# 直接运行本文件时，Python 会把它的模块名设为 __main__；
# 被其他文件 import 时，模块名则是文件名（这里是 main）。
# 因此这行代码可以让"直接运行"和"被导入"两种场景互不干扰。
# =========================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
