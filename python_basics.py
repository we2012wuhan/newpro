# -*- coding: utf-8 -*-
"""
Python 基础知识点速查（可直接运行学习）
=========================================
运行方式：在终端执行  python python_basics.py
它会按章节打印示例输出。建议"先看注释理解概念，再看打印结果验证"。

本文件覆盖：
    1. 变量与数据类型
    2. 字符串与 f-string
    3. 列表 / 元组 / 字典 / 集合
    4. 切片 [start:end:step]
    5. 条件与循环（enumerate / zip）
    6. 推导式（列表 / 字典 / 集合 / 生成器）
    7. 函数（默认参数、*args、**kwargs、lambda）
    8. 类型注解（Type Hints）
    9. 常用内置函数（sorted / map / filter / any / all）
    10. 动手小练习（含参考答案）
"""


def section(title: str) -> None:
    """打印章节标题，方便在输出里定位"""
    print("\n" + "=" * 60)
    print(f"【{title}】")
    print("=" * 60)


# =========================================================
# 知识点 1：变量与数据类型
# =========================================================
section("1. 变量与数据类型")

# Python 是动态类型语言：变量不需要声明类型，赋值时自动确定。
num = 10          # int    整数
price = 19.9      # float  浮点数
name = "小明"     # str    字符串
is_ok = True      # bool   布尔值（True / False）
nothing = None    # NoneType，表示"空"（注意首字母大写）

print(f"num={num}，类型={type(num)}")
print(f"price={price}，类型={type(price)}")
print(f"name={name}，类型={type(name)}")
print(f"is_ok={is_ok}，类型={type(is_ok)}")
print(f"nothing={nothing}，类型={type(nothing)}")

# type() 查看类型；isinstance() 判断"是不是某种类型"
print(f"num 是 int 吗？{isinstance(num, int)}")
print(f"name 是 str 吗？{isinstance(name, str)}")

# 同时给多个变量赋值（元组解包）
a, b, c = 1, 2, 3
print(f"元组解包：a={a}, b={b}, c={c}")


# =========================================================
# 知识点 2：字符串与 f-string
# =========================================================
section("2. 字符串与 f-string")

# 单引号 / 双引号都行；三引号可以写多行文本
s1 = "hello"
s2 = 'world'
s3 = """第一行
第二行"""
print("三引号字符串：", repr(s3))

# 拼接：+ 号；注意字符串只能和字符串拼接
print("拼接结果：", s1 + " " + s2)

# 常用字符串方法：upper / lower / strip / split / replace / join
text = "  Python 是 最好的语言  "
print("去掉首尾空格：", repr(text.strip()))
words = text.strip().split()          # split() 默认按空白拆分
print("按空格拆分：", words)
print("用 - 连接：", "-".join(words))
print("替换：", text.strip().replace("最好", "很") )

# f-string（重点，必须掌握）：
#   1) 在字符串前加 f，就能用 {变量} 或 {表达式} 直接插值
#   2) 支持格式化：{值:格式}
pi = 3.1415926
print(f"圆周率保留两位小数：{pi:.2f}")
print(f"100 的二进制：{100:b}，十六进制：{100:x}")
print(f"字符串对齐：{'左对齐':<8}|{'右对齐':>8}|")

# 老式写法 .format()（了解即可，新代码推荐 f-string）
print("老式写法：{} + {} = {}".format(1, 2, 1 + 2))


# =========================================================
# 知识点 3：列表 / 元组 / 字典 / 集合
# =========================================================
section("3. 四种常用容器")

# 列表 list：有序、可修改、可重复，最常用
fruits = ["苹果", "香蕉", "橙子"]
fruits.append("葡萄")         # 末尾追加
fruits.insert(0, "西瓜")      # 指定位置插入
fruits.remove("苹果")         # 按值删除
print("列表：", fruits)
print("第 1 个元素：", fruits[0], "；最后一个：", fruits[-1])
print("切片：", fruits[1:3])

# 元组 tuple：有序、不可修改，适合存放"不该变"的数据
point = (10, 20)
print("元组：", point, "，长度：", len(point))
# point[0] = 99  # 这行会报错：元组不支持修改

# 字典 dict：键值对，查询极快
person = {"name": "小明", "age": 18, "city": "上海"}
print("字典取值：", person["name"])
print("get 取值（键不存在返回默认值）：", person.get("job", "未知"))
person["age"] = 19            # 修改
person["hobby"] = "编程"      # 新增键
print("修改后的字典：", person)
print("所有键：", list(person.keys()), "；所有值：", list(person.values()))

# 集合 set：无序、自动去重
nums = [1, 2, 2, 3, 3, 3]
unique = set(nums)
print("去重后的集合：", unique)
print("集合运算：交集", {1, 2, 3} & {2, 3, 4}, "；并集", {1, 2, 3} | {3, 4})


# =========================================================
# 知识点 4：切片 [start:end:step]
# =========================================================
section("4. 切片 [start:end:step]")

# 切片是 Python 处理序列（字符串/列表/元组）的精髓
arr = list(range(10))  # range(10) -> 0,1,2,...,9
print("原列表：", arr)
print("前 3 个 arr[:3]：", arr[:3])
print("第 2~5 个 arr[1:5]：", arr[1:5])
print("从第 3 个到结尾 arr[3:]：", arr[3:])
print("最后 3 个 arr[-3:]：", arr[-3:])
print("隔一个取一个 arr[::2]：", arr[::2])
print("反转 arr[::-1]：", arr[::-1])


# =========================================================
# 知识点 5：条件与循环
# =========================================================
section("5. 条件与循环")

# if / elif / else：注意 Python 用"缩进"表示代码块，没有大括号
score = 85
if score >= 90:
    level = "优秀"
elif score >= 60:
    level = "及格"
else:
    level = "不及格"
print(f"成绩 {score} -> {level}")

# for + range()：range(起点, 终点, 步长)，不包含终点
total = 0
for i in range(1, 101):
    total += i
print(f"1+2+...+100 = {total}")

# enumerate：同时拿到下标和值（非常常用）
fruits = ["苹果", "香蕉", "橙子"]
for idx, fruit in enumerate(fruits):
    print(f"第 {idx} 个水果是 {fruit}")

# zip：并行遍历多个列表
names = ["张三", "李四", "王五"]
ages = [20, 21, 22]
for name, age in zip(names, ages):
    print(f"{name} 今年 {age} 岁")

# while：只要条件为真就一直循环
n = 3
while n > 0:
    print(f"倒计时：{n}")
    n -= 1

# 循环控制：break 跳出整个循环，continue 跳过本次
for i in range(10):
    if i == 2:
        continue  # 跳过 2，不打印
    if i == 5:
        break     # 到 5 就退出循环
    print("break/continue 演示：", i)


# =========================================================
# 知识点 6：推导式（Comprehension）
# =========================================================
section("6. 推导式")

numbers = [1, 2, 3, 4, 5, 6]

# 列表推导式 = 循环 + 条件 + 收集结果，一行搞定
squares = [x * x for x in numbers]
evens = [x for x in numbers if x % 2 == 0]
print("平方：", squares)
print("偶数：", evens)

# 字典推导式
square_dict = {x: x * x for x in numbers}
print("字典推导式：", square_dict)

# 集合推导式（自动去重）
mod_set = {x % 3 for x in numbers}
print("集合推导式：", mod_set)

# 生成器表达式：小括号包住，按需产出、不占内存（后面进阶文件详解）
gen = (x * x for x in numbers)
print("生成器表达式：", gen, "-> 逐个取出：", list(gen))

# 嵌套推导式：两层循环（顺序：先外层后内层）
matrix = [[1, 2, 3], [4, 5, 6]]
flat = [n for row in matrix for n in row]
print("二维列表展开：", flat)


# =========================================================
# 知识点 7：函数
# =========================================================
section("7. 函数")

# 定义函数：def 函数名(参数): 主体；return 返回结果（不写则返回 None）
def add(a, b):
    return a + b

print("add(1, 2) =", add(1, 2))

# 默认参数：调用时可以省略，省略时使用默认值
def greet(name, greeting="你好"):
    return f"{greeting}，{name}！"

print(greet("小明"))
print(greet("小明", "晚上好"))

# *args：把多余的位置参数收集成一个元组
def sum_all(*args):
    print(f"  收到的位置参数：{args}，类型：{type(args)}")
    return sum(args)

print("sum_all(1, 2, 3, 4) =", sum_all(1, 2, 3, 4))

# **kwargs：把多余的关键字参数收集成一个字典
def show_info(**kwargs):
    for key, value in kwargs.items():
        print(f"  {key} = {value}")

show_info(name="小明", age=18, city="上海")

# 调用时也能解包：*列表 按位置展开，**字典 按名字展开
def add3(a, b, c):
    return a + b + c

nums = [1, 2, 3]
print("add3(*nums) =", add3(*nums))
person = {"a": 10, "b": 20, "c": 30}
print("add3(**person) =", add3(**person))

# lambda：匿名函数，适合"只用一次"的简单逻辑
double = lambda x: x * 2
print("lambda 双倍：", double(10))

# lambda 最常见用法：给 sorted 指定排序规则
people = [{"name": "张三", "age": 30}, {"name": "李四", "age": 25}]
print("按年龄排序：", sorted(people, key=lambda p: p["age"]))


# =========================================================
# 知识点 8：类型注解（Type Hints）
# =========================================================
section("8. 类型注解")

# 注解只起"说明"作用，运行时不会强制校验（校验要靠 pydantic 等库）
def add_hint(a: int, b: int) -> int:
    return a + b

print("带注解的函数：", add_hint(1, 2))

# 组合类型：list[int]、dict[str, int]、tuple[int, str]
# 可选类型：str | None（Python 3.10+），等价于 Optional[str]
def describe(items: list[str], count: int | None = None) -> str:
    return f"共 {len(items)} 项，count={count}"

print(describe(["a", "b"]))
print(describe(["a", "b"], 5))


# =========================================================
# 知识点 9：常用内置函数
# =========================================================
section("9. 常用内置函数")

numbers = [3, 1, 4, 1, 5, 9, 2, 6]
print("排序 sorted：", sorted(numbers))
print("倒序：", sorted(numbers, reverse=True))
print("最大/最小/求和：", max(numbers), min(numbers), sum(numbers))

# map：把函数批量作用到每个元素（懒加载，要用 list() 取出）
print("map 平方：", list(map(lambda x: x * x, [1, 2, 3])))

# filter：按条件过滤
print("filter 大于 4：", list(filter(lambda x: x > 4, numbers)))

# any / all：判断"是否存在" / "是否全部满足"
print("any(存在偶数吗？)：", any(x % 2 == 0 for x in numbers))
print("all(全部为正数吗？)：", all(x > 0 for x in numbers))

# 其他高频内置函数：len() / range() / enumerate() / zip()
# type() / isinstance() / str() / int() / list() / dict() / print()
print("字符串转整数：", int("42") + 8)


# =========================================================
# 知识点 10：动手小练习（含答案）
# =========================================================
section("10. 动手小练习")

print("""
练习 1：用一行推导式，把 1~100 里能被 3 整除的数放进列表。
练习 2：写一个函数，接收任意多个数字，返回其中的最大值。
练习 3：用 zip + dict()，把 ['name','age'] 和 ['小明', 18] 变成一个字典。
练习 4：统计字符串 'abracadabra' 中每个字符出现的次数（提示：字典推导式）。
练习 5：把 ['  苹果  ', '香蕉', '  橙子  '] 去空格并转成大写列表。

参考答案（先自己写，再对照）：
  1. [x for x in range(1, 101) if x % 3 == 0]
  2. def my_max(*args): return max(args)
  3. dict(zip(['name', 'age'], ['小明', 18]))
  4. {c: s.count(c) for c in set(s)}  （s = 'abracadabra'）
  5. [f.strip().upper() for f in ['  苹果  ', '香蕉', '  橙子  ']]
""")


# =========================================================
# 模块入口：只有"直接运行本文件"时才执行
# =========================================================
if __name__ == "__main__":
    print("\n【提示】运行  python python_advanced.py  可以继续学习进阶知识点。")
