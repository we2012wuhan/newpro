# -*- coding: utf-8 -*-
"""
Python 进阶知识点速查（可直接运行学习）
=========================================
运行方式：在终端执行  python python_advanced.py

本文件覆盖：
    1. 生成器 generator（yield）
    2. 装饰器（函数进阶，理解 FastAPI @app.get 的底层原理）
    3. 类与对象（OOP 三大特性、property）
    4. dataclass 数据类
    5. 上下文管理器 with
    6. 异常处理 try / except / else / finally
    7. 模块、包与 __name__ == "__main__"
    8. 虚拟环境与 pip（终端命令，不是 Python 代码）
    9. 常用模块速览（json / os / pathlib / datetime）
"""

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def section(title: str) -> None:
    """打印章节标题"""
    print("\n" + "=" * 60)
    print(f"【{title}】")
    print("=" * 60)


# =========================================================
# 知识点 1：生成器 generator（yield）
# =========================================================
section("1. 生成器 generator")

# 普通函数用 return 一次性返回全部结果；
# 生成器函数用 yield"边算边给"，每次调用只产出当前一个值。
def countdown(n):
    print("  （进入生成器函数，还没开始产出）")
    while n > 0:
        yield n        # 产出 n，然后暂停在这里
        n -= 1
    print("  （生成器里的值用完了）")

for num in countdown(3):
    print("  倒计时：", num)

# 为什么用生成器？处理超大数据时不会一次性占满内存。
# 对比：list(range(100000000)) 会占用数百 MB；range 本身几乎不占内存。
# 生成器只能遍历一次，用完就没了：
print("再遍历一次，发现是空的：", list(countdown(0)))

# 生成器表达式：小括号包住推导式
squares = (x * x for x in range(5))
print("生成器表达式：", list(squares))


# =========================================================
# 知识点 2：装饰器（函数进阶）
# =========================================================
section("2. 装饰器")

# 装饰器本质：接收一个函数，返回一个新函数（新函数通常会"包裹"原函数）。
def my_timer(func):
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)          # 调用原函数
        print(f"  函数 {func.__name__} 耗时 {time.time() - start:.4f} 秒")
        return result
    return wrapper

# @my_timer 等价于：slow_add = my_timer(slow_add)
# 即：把函数作为参数传给 my_timer，再把返回的新函数重新赋值给同名变量。
@my_timer
def slow_add(a, b):
    time.sleep(0.01)
    return a + b

print("slow_add(1, 2) =", slow_add(1, 2))

# 常见用途：日志、计时、权限校验、缓存……
# FastAPI 的 @app.get("/") 原理相同：app.get 返回一个装饰器，
# 把你的函数"注册"到路由表，从而让 / 这个地址能调用到它。

# 装饰器还能带参数（@app.get("/x") 就是带参装饰器）：
def repeat(times):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for _ in range(times):
                result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator

@repeat(3)
def say_hi():
    print("  hi（会被打印 3 次）")

say_hi()


# =========================================================
# 知识点 3：类与对象（OOP）
# =========================================================
section("3. 类与对象")

# class 定义类；类里定义的函数叫"方法"；用"类名()"创建对象（实例）。
class Dog:
    species = "犬科"          # 类属性：所有实例共享

    # __init__ 是构造函数：创建对象时自动调用，用来初始化实例属性
    def __init__(self, name, age):
        self.name = name      # 实例属性：每个对象各有一份
        self.age = age

    def bark(self):           # 实例方法：第一个参数永远是 self（对象自己）
        return f"{self.name}：汪汪！"

    def __str__(self):        # print(对象) 时显示的内容
        return f"Dog(name={self.name}, age={self.age})"

dog = Dog("旺财", 3)
print("实例属性：", dog.name, dog.age)
print("调用方法：", dog.bark())
print("类属性：", Dog.species, "（实例也能访问：", dog.species, "）")
print("print 对象时：", dog)

# 继承：子类自动拥有父类的方法，还能新增/重写
class GuideDog(Dog):
    def guide(self):          # 子类新增方法
        return f"{self.name} 正在导盲"

    def bark(self):           # 子类重写父类方法（多态）
        return f"{self.name}：汪汪汪！（导盲犬专属叫声）"

guide = GuideDog("阿黄", 4)
print("继承来的方法：", guide.guide())
print("重写后的方法：", guide.bark())

# @property：把方法"伪装"成属性，访问时不用加括号
class Circle:
    def __init__(self, radius):
        self.radius = radius

    @property
    def area(self):
        return 3.14 * self.radius ** 2

c = Circle(2)
print("圆的面积（属性式访问）：", c.area)

# 封装：用 _ 开头的属性约定为"内部使用，不要外部直接改"
# 私有属性 __x 会被改名（name mangling），彻底防外部访问
class Account:
    def __init__(self, balance):
        self.__balance = balance      # 双下划线开头 = 私有

    def deposit(self, amount):
        self.__balance += amount

    def show(self):
        return f"余额：{self.__balance}"

acc = Account(100)
acc.deposit(50)
print("封装演示：", acc.show())


# =========================================================
# 知识点 4：dataclass 数据类
# =========================================================
section("4. dataclass 数据类")

# 只想"存一堆数据"的类，用 @dataclass 可以少写大量样板代码：
# 它自动生成 __init__ / __repr__ / __eq__ 等方法。
@dataclass
class Point:
    x: int
    y: int

p1 = Point(1, 2)
p2 = Point(1, 2)
print("自动生成的 __repr__：", p1)
print("自动生成的 __eq__：", p1 == p2)
print("直接按属性访问：", p1.x, p1.y)

# 对比记忆：FastAPI 里的 Pydantic BaseModel 就是 dataclass 的"增强版"，
# 额外提供类型校验、JSON 序列化、字段约束（Field）等功能。


# =========================================================
# 知识点 5：上下文管理器 with
# =========================================================
section("5. 上下文管理器 with")

# with 的核心作用：进入时自动执行 __enter__，退出时自动执行 __exit__。
# 最典型用法：操作文件，用完自动关闭，不用手写 close()。
with open("demo_temp.txt", "w", encoding="utf-8") as f:
    f.write("with 会自动帮我关闭文件！")

with open("demo_temp.txt", "r", encoding="utf-8") as f:
    content = f.read()
print("读到的内容：", content)
os.remove("demo_temp.txt")   # 清理演示用的临时文件

# 自己实现上下文管理器：实现 __enter__ 和 __exit__ 两个方法
class ManagedResource:
    def __enter__(self):
        print("  >>> 进入 with 块：打开资源")
        return self          # as 后面的变量会拿到这个返回值

    def __exit__(self, exc_type, exc_val, exc_tb):
        print("  >>> 退出 with 块：关闭资源")
        return False         # 返回 False 表示"不吞掉异常"

with ManagedResource() as res:
    print("  with 块内部：正在使用资源")


# =========================================================
# 知识点 6：异常处理
# =========================================================
section("6. 异常处理")

# 完整结构：try -> except（捕获）-> else（无异常时）-> finally（总会执行）
def divide(a, b):
    try:
        result = a / b
    except ZeroDivisionError as e:
        print(f"  捕获到除零异常：{e}")
        return None
    except TypeError as e:               # 可以写多个 except
        print(f"  捕获到类型异常：{e}")
        return None
    else:
        # 只有 try 里没有抛异常时才会执行 else
        print("  try 顺利执行，没有异常")
        return result
    finally:
        # 无论是否异常都会执行：常用于关闭文件/数据库连接
        print("  finally：无论如何都会执行")

print("divide(10, 2) =", divide(10, 2))
print("divide(10, 0) =", divide(10, 0))

# 主动抛出异常：raise 异常类("说明文字")
def check_age(age):
    if age < 0:
        raise ValueError("年龄不能为负数")
    return age

try:
    check_age(-1)
except ValueError as e:
    print("捕获主动抛出的异常：", e)

# 异常也有继承关系：ValueError 是 Exception 的子类，
# 所以 except Exception 能兜底捕获所有常规异常（但不建议滥用）。


# =========================================================
# 知识点 7：模块、包与 __name__ == "__main__"
# =========================================================
section("7. 模块与 __name__")

# 每个 .py 文件都是一个"模块"。
# 运行方式不同，模块名也不同：
#   - 直接运行  python python_advanced.py  -> __name__ == "__main__"
#   - 被导入    import python_advanced     -> __name__ == "python_advanced"
# 因此 if __name__ == "__main__": 可以让"脚本入口"只在直接运行时生效。
print(f"当前模块名是：{__name__}")

def main():
    print("程序入口 main() 只在直接运行时被调用")

if __name__ == "__main__":
    main()

# 包（package）：一个带 __init__.py 的目录，用来组织多个模块。
# 例如 fastapi 就是一个包：from fastapi import FastAPI
# 当项目变大时，可以按功能拆成多个 .py 文件，再用 import 互相引用。


# =========================================================
# 知识点 8：虚拟环境与 pip（在终端执行，不是 Python 代码）
# =========================================================
section("8. 虚拟环境与 pip")

print("""
为什么要用虚拟环境？
    不同项目依赖的第三方包版本可能互相冲突，
    虚拟环境为每个项目隔离出一套独立的第三方库，互不干扰。

常用命令（在终端 / 命令行里执行）：
    创建虚拟环境：  python -m venv .venv
    激活（Windows）：.venv\\Scripts\\activate
    激活（macOS/Linux）：source .venv/bin/activate
    退出虚拟环境：  deactivate
    安装依赖：      pip install -r requirements.txt
    导出依赖：      pip freeze > requirements.txt

注意：.venv 目录属于本机环境，不要提交到 git。
""")


# =========================================================
# 知识点 9：常用模块速览
# =========================================================
section("9. 常用模块速览")

# json：接口开发最常用，Python 对象 <-> JSON 字符串 互转
data = {"name": "小明", "age": 18, "tags": ["python", "fastapi"]}
json_str = json.dumps(data, ensure_ascii=False)   # 保留中文
print("转 JSON 字符串：", json_str)
print("JSON 转回 Python：", json.loads(json_str))

# pathlib / os：文件和路径操作
print("当前工作目录：", os.getcwd())
print("本文件绝对路径：", Path(__file__).resolve())
print("requirements.txt 是否存在：", Path("requirements.txt").exists())

# datetime：时间处理
now = datetime.now()
print("当前时间：", now)
print("格式化：", now.strftime("%Y-%m-%d %H:%M:%S"))
print("日期运算（加一天）：", now + __import__("datetime").timedelta(days=1))


# =========================================================
# 文件入口
# =========================================================
if __name__ == "__main__":
    print("\n【提示】学完这些，再回头读 main.py 里的 FastAPI 代码会轻松很多！")
