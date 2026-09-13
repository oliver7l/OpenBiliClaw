"""把「真实面经新增真题」写成话术，写入 interview_scripts 表。

覆盖 面经真题汇编_带出处.md 里、题库（id=61~89）还没有答案的新增题：
- Coding 新增 11 题（LC3/蓄水池/大数打印/矩阵置零/建树/回文/三数/交叉熵正则/SoftNMS/SQL连续登录/杂项）
- 笔试高频考点（4 场机考）
- 八股新增（推荐去偏/因果 uplift/大模型/CV/风控/传统ML）
- 项目深挖 15 连问
- 场景题 5 组（广告竞价/冷启动/AUC-GMV/AB失效/短链TCC秒杀）
- HR 面口径

直接写 SQLite，不依赖 LLM。
撤销：DELETE FROM interview_scripts WHERE source LIKE '面经真题-拼多多-%';
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "interview.db"
NOW = datetime.now().isoformat(timespec="seconds")

NEW_SCRIPTS = [
    # ================= Coding =================
    {
        "code": "C1-LC3",
        "title": "面经 Coding：LC3 无重复字符最长子串（真考 4 次最高频）",
        "priority": "高",
        "tags": "coding,滑动窗口,真考高频,LC3",
        "key_points": "滑动窗口+记字符上次位置；O(n) 必须，O(n^2) 被打回；环形变体=拼接+窗口上限 n",
        "note": "S5一面/S2二面/S8一面/S11-0812二面。S8 原话要求 O(n)。",
        "content": """## 为什么必须会
真考 4 次（S5 一面、S2 二面、S8 一面、S11 0812 二面），是拼多多 coding 最高频。S8 原话：「面试官要求 O(n)，写 O(n²) 会被打回」。

## 标准解（滑动窗口 + 记位置，O(n)）
```python
def lengthOfLongestSubstring(s: str) -> int:
    last, left, ans = {}, 0, 0
    for right, ch in enumerate(s):
        if ch in last and last[ch] >= left:
            left = last[ch] + 1        # 左边界跳到重复字符右边
        last[ch] = right
        ans = max(ans, right - left + 1)
    return ans
```
复杂度 O(n)，空间 O(min(n, 字符集))。

## 两个易错点（面试官就盯这个）
1. 判断重复必须带 `last[ch] >= left`，否则会把窗口外的旧位置误判为重复（O(n²) 心态的典型 bug）。
2. 刷新答案要在调整 left 之后。

## 追问 1：为什么不用 set + while 的模板？
set 模板均摊 O(n)，但内层 while 会被追问「最坏是不是 O(n²)」——其实 left 最多移 n 次仍是 O(n)；用「记位置」写法内层无循环更干净，推荐主答。

## 追问 2（真考变体）：首尾可相连的环形字符串最长多少？
令 `t = s + s`，在 t 上跑滑动窗口，**窗口长度上限为 n**。
```python
def maxLenCircular(s: str) -> int:
    n = len(s)
    if not n: return 0
    if len(set(s)) == n: return n       # 全不重复直接 n
    t, last, left, ans = s + s, {}, 0, 0
    for right, ch in enumerate(t):
        if ch in last and last[ch] >= left:
            left = last[ch] + 1
        last[ch] = right
        if right - left + 1 > n:        # 环形限制
            left = right - n + 1
        ans = max(ans, right - left + 1)
    return ans
```
口诀：**环形 = 拼接 + 窗口设上限 n**（少了上限会跑到 2n）。

## 追问 3：返回子串本身？
滑窗里同时维护 start 与 best_len，刷新时记 `start = left`，最后 `s[start:start+best_len]`。
""",
    },
    {
        "code": "C2-Reservoir",
        "title": "面经 Coding：蓄水池抽样（从 n 个数等概率抽 m 个）",
        "priority": "高",
        "tags": "coding,概率采样,蓄水池抽样,真考",
        "key_points": "流式 O(n)；前 m 个入池，第 i 个以 m/(i+1) 概率替换；每人中签率都是 m/n",
        "note": "S12 面经熊「一面凉经」原话：没见过这种题型，崩。会的人 3 分钟，不会直接死。",
        "content": """## 出处
S12 面经熊《一面凉经》——候选人原话「没见过这种题型，崩」。这题**会的人 3 分钟、不会的人直接死**，性价比极高。

## 问题
给一个长度未知、只能遍历一次的数据流，等概率抽取 m 个元素。

## 标准解
```python
import random
def reservoir(stream, m):
    res = []
    for i, x in enumerate(stream):
        if i < m:
            res.append(x)
        else:
            j = random.randint(0, i)     # [0, i] 均匀
            if j < m:                    # 概率 m/(i+1) 替换
                res[j] = x
    return res
```

## 为什么每个人中签都是 m/n（必须能推）
- **前 m 个**：第 i(≥m) 个到来时不被替换的概率是 i/(i+1)，连乘 ∏ = m/n。
- **第 i 个(i≥m)**：入池概率 m/(i+1) × 之后不被替换 (i+1)/n = m/n。
结论：**与元素位置无关，全体都是 m/n**。

## 追问 1：m=1 简化版（最常考）
```python
def random_one(stream):
    choice = None
    for i, x in enumerate(stream):
        if random.randint(0, i) == 0:    # 概率 1/(i+1)
            choice = x
    return choice
```

## 追问 2：为什么用它？
不知 n、不能二次遍历、不能全存内存：海量日志随机抽样、大文件随机取 k 行、流式均匀采样。**不是"取前 m 个"**（前 m 个有偏）。

## 追问 3：加权蓄水池（A-Res）
按权重 w 抽，概率正比于 w：第 i 个取 key = `u^(1/w_i)`（u~U(0,1)），维护大小 m 的**最小堆**，最后堆内即结果。用于按曝光量/热度加权采样。
""",
    },
    {
        "code": "C3-BigNum",
        "title": "面经 Coding：打印 1~N 位所有数字（大数用字符串）",
        "priority": "高",
        "tags": "coding,大数,字符串模拟,DFS",
        "key_points": "第一句就要说 int 会溢出，用字符串/数组存每位；字符串自增 或 递归全排列",
        "note": "S12 面经熊（魏无忌·二面）。考的就是「你意识到不能用 int」。",
        "content": """## 问题
输入 n，按顺序打印 1 到最大的 n 位十进制数（n=3 打印 1~999）。

## 陷阱（第一句就要说出来）
n 可能很大（如 n=100），**int 一定溢出**。面试官考的就是"你意没意识到不能用 int"。

## 解法一：字符串模拟大数加法（推荐）
```python
def print_max_n_digits(n: int):
    if n <= 0: return
    num = ['0'] * n                    # 低位在右
    while not increment(num):
        print(''.join(num).lstrip('0') or '0')

def increment(num) -> bool:
    carry = 1
    for i in range(len(num) - 1, -1, -1):   # 低位到高位
        if carry == 0: break
        total = int(num[i]) + carry
        if total >= 10:
            if i == 0: return True          # 最高位进位 = 溢出
            num[i] = str(total - 10); carry = 1
        else:
            num[i] = str(total); carry = 0
    return False
```

## 解法二：递归全排列（更短）
n 个位置，每位放 0~9：
```python
def print_max_n_digits(n):
    if n <= 0: return
    buf = ['0'] * n
    def dfs(idx):
        if idx == n:
            s = ''.join(buf).lstrip('0')
            if s: print(s)
            return
        for d in range(10):
            buf[idx] = str(d); dfs(idx + 1)
    dfs(0)
```
复杂度 O(n · 10^n)。

## 追问
"判断回文大数""大数加法/乘法"——**统一心法：字符串/数组存每位，从低位往高位模拟进位**。
""",
    },
    {
        "code": "C4-LC73",
        "title": "面经 Coding：LC73 矩阵置零（O(1) 空间）",
        "priority": "中",
        "tags": "coding,数组,原地算法,LC73",
        "key_points": "先标记后清除；用首行首列当标记位，先记下首行首列自身是否需要清零",
        "note": "S6 二面。",
        "content": """## 问题
矩阵中某元素为 0，则其所在行和列全置 0。

## 陷阱
发现 0 立刻清零 → 会把"被清零的位置"当成新的 0 源头 → 全矩阵变 0。必须**先标记、后清除**。

## 标准解（用首行首列当标记位，O(1) 额外空间）
```python
def setZeroes(matrix):
    R, C = len(matrix), len(matrix[0])
    first_row_zero = any(matrix[0][c] == 0 for c in range(C))
    first_col_zero = any(matrix[r][0] == 0 for r in range(R))
    for r in range(1, R):
        for c in range(1, C):
            if matrix[r][c] == 0:
                matrix[r][0] = 0        # 标记该行
                matrix[0][c] = 0        # 标记该列
    for r in range(1, R):
        for c in range(1, C):
            if matrix[r][0] == 0 or matrix[0][c] == 0:
                matrix[r][c] = 0
    if first_row_zero:
        for c in range(C): matrix[0][c] = 0
    if first_col_zero:
        for r in range(R): matrix[r][0] = 0
```

## 为什么不会冲突
首行/首列"自己要不要清零"在开头就用两个 bool 存下来了，所以它们的格子可以放心当标记位。

## 追问
- 允许 O(m+n) 空间？→ 两个 bool 数组，思路同上更简单。
- 元素是浮点、要判"是否等于 0"？→ 用 `abs(x) < eps`，别用 `==`。
""",
    },
    {
        "code": "C5-Tree",
        "title": "面经 Coding：前序+中序建树 + 层序打印",
        "priority": "中",
        "tags": "coding,二叉树,递归,BFS",
        "key_points": "前序首=根，中序定位根切左右；哈希表 O(1) 定位根；层序用 deque",
        "note": "S7 一面（Temu 用户增长）。",
        "content": """## 问题
给前序 + 中序遍历，构建二叉树，再层序打印验证。

## 标准解
```python
from collections import deque
class TreeNode:
    def __init__(self, v): self.val, self.left, self.right = v, None, None

def build(pre, ino):
    idx = {v: i for i, v in enumerate(ino)}      # 值 -> 中序下标，O(1) 定位
    def helper(pl, pr, il, ir):
        if pl > pr: return None
        k = idx[pre[pl]]
        node = TreeNode(pre[pl])
        left_size = k - il                       # 左子树节点数
        node.left  = helper(pl + 1, pl + left_size, il, k - 1)
        node.right = helper(pl + left_size + 1, pr, k + 1, ir)
        return node
    return helper(0, len(pre) - 1, 0, len(ino) - 1)

def level_order(root):
    if not root: return []
    q, res = deque([root]), []
    while q:
        n = q.popleft(); res.append(n.val)
        if n.left: q.append(n.left)
        if n.right: q.append(n.right)
    return res
```

## 核心不变量
前序第一个 = 根；中序里根左边是左子树、右边是右子树；两段长度对上区间。O(n)（哈希定位根；否则 O(n²)）。

## 追问
1. 为什么只有前序+中序能唯一确定？前序+后序不行（无法区分只有一个孩子的左右；除非满二叉树）。
2. 有重复值怎么办？→ 哈希存"值→下标列表"或按题目约定。
3. 不建树直接输出后序？→ 递归区间先左后右再打根（同一题高频变体）。
""",
    },
    {
        "code": "C6-LC5",
        "title": "面经 Coding：LC5 最长回文子串",
        "priority": "中",
        "tags": "coding,回文,中心扩展,Manacher",
        "key_points": "中心扩展 O(n^2)；奇偶两种中心都要扩；O(n) 用 Manacher",
        "note": "S2 三面。",
        "content": """## 标准解：中心扩展 O(n²)
```python
def longestPalindrome(s):
    if not s: return ""
    start, end = 0, 0
    def expand(l, r):
        while l >= 0 and r < len(s) and s[l] == s[r]:
            l, r = l - 1, r + 1
        return l + 1, r - 1
    for i in range(len(s)):
        l1, r1 = expand(i, i)          # 奇回文
        l2, r2 = expand(i, i + 1)      # 偶回文
        if r1 - l1 > end - start: start, end = l1, r1
        if r2 - l2 > end - start: start, end = l2, r2
    return s[start:end + 1]
```
**必须有奇偶两次扩展**（aba / abba 两种中心），漏一个就错。空间 O(1)。

## 追问 1：O(n) 解法？
**Manacher**：插 `#` 统一奇偶 + 维护最右回文边界 r 和中心 c，利用回文对称性复用半径，每字符最多扩一次 → O(n)。说出思想+复杂度即可。

## 追问 2：最长回文**子序列**（可不连续）？
DP：`dp[i][j] = dp[i+1][j-1] + 2 if s[i]==s[j] else max(dp[i+1][j], dp[i][j-1])`。
""",
    },
    {
        "code": "C7-LC15",
        "title": "面经 Coding：LC15 三数之和",
        "priority": "中",
        "tags": "coding,双指针,去重,LC15",
        "key_points": "排序+双指针 O(n^2)；三个位置分别跳重复（i/l/r），去重就是全部难点",
        "note": "S5 二面。",
        "content": """## 标准解（排序 + 双指针，O(n²)）
```python
def threeSum(nums):
    nums.sort(); res, n = [], len(nums)
    for i in range(n - 2):
        if nums[i] > 0: break                          # 最小都>0，剪枝
        if i > 0 and nums[i] == nums[i-1]: continue     # 去重①：第一个数
        l, r = i + 1, n - 1
        while l < r:
            t = nums[i] + nums[l] + nums[r]
            if t == 0:
                res.append([nums[i], nums[l], nums[r]])
                while l < r and nums[l] == nums[l+1]: l += 1   # 去重②
                while l < r and nums[r] == nums[r-1]: r -= 1   # 去重③
                l, r = l + 1, r - 1
            elif t < 0: l += 1
            else: r -= 1
    return res
```

## 关键
**去重是这题全部难点**：排序后在 i、l、r 三个位置分别跳重复，漏一处就出重复三元组。

## 追问
- 三数之和**最接近 target**？→ 双指针，维护 `abs(t - target)` 最小值。
- 四数之和？→ 外层再套一层循环 + 同样双指针，两层都要去重。
""",
    },
    {
        "code": "C8-CE-Reg",
        "title": "面经 Coding：手写带 L1/L2 正则的交叉熵损失",
        "priority": "中",
        "tags": "coding,推导,交叉熵,正则化",
        "key_points": "softmax+CE 梯度就是 p-y；L1 梯度 sign(w)、L2 梯度 λw；L1 稀疏 L2 平滑",
        "note": "S5 一面，要求手推 + 实现。",
        "content": """## 公式
```
L = -1/N Σ y·log p  +  λ1‖W‖₁  +  λ2/2‖W‖₂²
```
- L1：‖W‖₁ = Σ|w|，次梯度 sign(w)
- L2：(λ2/2)Σw²，梯度 λ2·w（**乘 1/2 就是为了求导后干净地剩 λw**）

## 实现
```python
import numpy as np
def softmax(z):
    z = z - z.max(axis=1, keepdims=True)     # 数值稳定，必须减 max
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)

def ce_loss_with_reg(W, b, X, Y, lam1=0.0, lam2=0.0):
    N = X.shape[0]
    P = softmax(X @ W + b)
    loss = -np.sum(Y * np.log(P + 1e-12)) / N + lam1*np.sum(np.abs(W)) + lam2/2*np.sum(W*W)
    dZ = (P - Y) / N                          # softmax + CE 的天赐梯度
    dW = X.T @ dZ + lam1*np.sign(W) + lam2*W
    db = dZ.sum(axis=0)
    return loss, dW, db
```

## 必答的一句
**softmax + 交叉熵的梯度就是 p - y**（∂L/∂z_c = p_c - y_c），极其简洁，这是二者配对使用的根本原因。

## 追问
1. 为什么 L1 稀疏？→ 梯度是常数 sign(w)，不管 w 多小都往 0 推，到 0 后次梯度区间含 0 能"停住"；L2 梯度 λw 随 w 变小而变小，只能无限逼近 0。
2. L1 不可导怎么优化？→ 近端梯度/ISTA/坐标下降/次梯度；工程上近似+截断。
3. 为什么不正则 bias？→ 会强行压偏置为 0，破坏拟合，一般只正则权重。
""",
    },
    {
        "code": "C9-SoftNMS",
        "title": "面经 Coding：手写 SoftNMS",
        "priority": "低",
        "tags": "coding,CV,目标检测,NMS",
        "key_points": "NMS 硬删除 → SoftNMS 软降权；Gaussian 衰减优于 Linear；工业界已被 NMS-free 替代",
        "note": "S12（魏无忌·一面），CV 岗常考。",
        "content": """## 区别一句话
NMS 是**硬删除**（IoU 超阈值框直接消失）；SoftNMS 是**软降权**（IoU 越大分数衰减越狠，但不立刻删，可能被别的框救回）。

## 实现
```python
import numpy as np
def soft_nms(boxes, scores, iou_thr=0.3, sigma=0.5, method='gaussian', score_thr=0.001):
    N = len(boxes)
    for i in range(N):
        max_idx = i
        for j in range(i + 1, N):                # 每轮找剩余最高分
            if scores[j] > scores[max_idx]: max_idx = j
        boxes[i], boxes[max_idx] = boxes[max_idx], boxes[i]
        scores[i], scores[max_idx] = scores[max_idx], scores[i]
        for j in range(i + 1, N):
            iou = compute_iou(boxes[i], boxes[j])
            if method == 'linear':
                if iou > iou_thr: scores[j] *= (1 - iou)
            else:
                scores[j] *= np.exp(-(iou * iou) / sigma)   # Gaussian（默认更好）
            if scores[j] < score_thr: scores[j] = 0
    return boxes, scores
```

## 追问
1. 为什么 Gaussian 比 Linear 好？→ Linear 在 IoU 略超阈值时衰减过猛且不连续；Gaussian 平滑连续，对"高 IoU 但确实是不同物体"更友好。
2. 现在还用吗？→ **基本被 NMS-free 替代**：DETR 用匈牙利二分图匹配、FCOS/ATSS 用 centerness、YOLOv8 用 DFL。SoftNMS 主要出现在面试和旧模型。
3. IoU = 交面积/并面积；GIoU/DIoU/CIoU 把中心点距离、长宽比加入回归损失。
""",
    },
    {
        "code": "C10-SQL",
        "title": "面经 Coding：SQL 最大连续登录天数",
        "priority": "中",
        "tags": "coding,SQL,窗口函数",
        "key_points": "日期 - 行号 = 分组标识；必须先对 (user_id, login_date) 去重",
        "note": "S5 一面。",
        "content": """## 核心技巧：日期 − 行号 = 分组标识
```sql
WITH t AS (
    SELECT user_id, login_date,
           DATE_SUB(login_date, INTERVAL
               ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date) DAY) AS grp
    FROM (SELECT DISTINCT user_id, login_date FROM login) d   -- 去重，一天多次登录算一次
)
SELECT user_id, MAX(cnt) AS max_consecutive_days
FROM (SELECT user_id, grp, COUNT(*) AS cnt FROM t GROUP BY user_id, grp) x
GROUP BY user_id;
```

## 为什么成立
连续日期的日期值每天 +1，行号也每天 +1，**两者相减是同一常量** → 同一段连续登录落到同一个 grp；一旦断档差值就变。

## 必须先去重
同一用户同一天多行会破坏差值恒定性。

## 追问
1. MySQL 5.7 无窗口函数？→ 用用户变量 `@row := IF(@uid=user_id, @row+1, 1)` 模拟行号。
2. 求"连续登录 ≥ 3 天的用户数"？→ `HAVING COUNT(*) >= 3`。
3. 求"当前连续登录天数"？→ 限制窗口到最近 k 天，或对最后一段单独取。
""",
    },
    {
        "code": "C11-Misc",
        "title": "面经 Coding：杂项小题速查（含369/三打印机/单例/attention/sqrt）",
        "priority": "中",
        "tags": "coding,杂项,贪心,设计模式,牛顿法",
        "key_points": "0~1000含369=657（补集7^3+1）；三打印机=LPT最小堆贪心；单例双检锁volatile；attention除以sqrt(d)稳方差；sqrt牛顿迭代二次收敛",
        "note": "拼多多爱考的小题，10 秒内要能答。",
        "content": """## 1) 0~1000 内所有含 3、6、9 的数字个数 = 657
用**补集**算，别暴力：
- 0~999 共 1000 数，按三位（允许前导 0）看，每位可放除 3/6/9 外的 7 个数字 → 完全不含 3/6/9 的有 7³ = 343 个。
- 1000 本身不含 → 补集 = 343 + 1 = 344。
- 答案 = 1001 − 344 = **657**。
面试官想听的是**补集思想**（"至少一个"难算、"一个都不含"好算）。

## 2) 3 台打印机分配队列使总时间最小 = LPT 贪心
每次把**当前最大任务**分给**当前负载最小**的机器（最小堆维护）：
```python
import heapq
def min_time(jobs, k=3):
    heap = [0] * k; heapq.heapify(heap)
    for t in sorted(jobs, reverse=True):     # 长任务优先
        load = heapq.heappop(heap)
        heapq.heappush(heap, load + t)
    return max(heap)
```
追问：最优吗？→ LPT 是 4/3 − 1/(3k) 近似比的贪心，**不一定全局最优**（最优要 DP/回溯）。

## 3) 单例模式（懒汉式）= 双检锁
```java
public class Singleton {
    private static volatile Singleton instance;   // volatile 防指令重排，必须加
    private Singleton() {}
    public static Singleton getInstance() {
        if (instance == null) {                   // 第一次检查（无锁，快）
            synchronized (Singleton.class) {
                if (instance == null) instance = new Singleton();  // 二次检查
            }
        }
        return instance;
    }
}
```
- 懒汉 vs 饿汉：懒汉=首次调用才创建（要处理线程安全）；饿汉=类加载即建（简单、天然安全，可能浪费）。
- volatile 作用：`new` 分三步（分配→初始化→赋值引用），JVM 可能重排为 1→3→2，别的线程会拿到"构造未完成"对象。
- 更好写法：**静态内部类**（JVM 类加载保证线程安全 + 懒加载，无需加锁）。

## 4) 实现链表支持插入/删除/查询
用**哨兵（dummy）节点**统一边界，否则删头要特判：
```python
class ListNode:
    def __init__(self, v=0, n=None): self.val, self.next = v, n
class LinkedList:
    def __init__(self): self.dummy, self.size = ListNode(), 0
    def add_at_head(self, v):
        self.dummy.next = ListNode(v, self.dummy.next); self.size += 1
    def get(self, i):
        if i < 0 or i >= self.size: return -1
        p = self.dummy.next
        for _ in range(i): p = p.next
        return p.val
    def delete_at(self, i):
        if i < 0 or i >= self.size: return
        p = self.dummy
        for _ in range(i): p = p.next
        p.next = p.next.next; self.size -= 1
```

## 5) 手写 self-attention
`Attention(Q,K,V) = softmax(QKᵀ/√d_k)V`
```python
def self_attention(X, Wq, Wk, Wv):
    Q, K, V = X @ Wq, X @ Wk, X @ Wv
    d_k = Q.shape[-1]
    A = softmax(Q @ K.T / (d_k ** 0.5), axis=-1)
    return A @ V, A
```
**为什么除以 √d_k**：Q、K 各维均值 0 方差 1 时点积方差是 d_k；d_k 大 → 点积大 → softmax 饱和 → 梯度趋近 0。除以 √d_k 把方差归一到 1，保持梯度尺度健康。

## 6) 计算平方根（不用 math.sqrt）
牛顿迭代：解 x² − a = 0，`x = (x + a/x) / 2`。
```python
def sqrt_newton(a, eps=1e-10):
    if a < 0: raise ValueError
    if a == 0: return 0
    x = a
    while abs(x*x - a) > eps:
        x = (x + a/x) / 2
    return x
```
二次收敛（每步有效位数翻倍），约 20 次到 double 精度。
""",
    },
    # ================= 笔试 =================
    {
        "code": "B-WRITTEN",
        "title": "面经笔试：4 场机考高频考点（贪心/模拟/前缀和/图论）",
        "priority": "高",
        "tags": "笔试,机考,贪心,Dijkstra,并查集",
        "key_points": "4题制；高频=贪心（几乎每场）+模拟+前缀和/哈希+图论；DP 少但一出现就难；技巧=先判题型",
        "note": "S9/S10 共 4 场机考：2026-08-23 / 2025-09-14 / 2024-08-25 / 实习笔试 + S1 附加笔试。",
        "content": """## 总规律
拼多多笔试 4 题制、难度中上。**高频 = 贪心（几乎每场必有）+ 模拟 + 前缀和/哈希 + 图论（Dijkstra/并查集）**；DP 出现少，但一出现就偏难（多维 DP）。

## 2026-08-23 机考
1. **展廊灯带后缀点亮** → 贪心：从右往左扫，只在"当前状态≠目标"时操作一次（别正着模拟）。
2. **护栏补强** → 贪心 + 二分答案：二分补强量，check 可行性（注意单调性）。
3. **驿站补给最短耗时** → Dijkstra（heapq 堆优化，朴素 O(n²) 会 TLE；注意点权/边权）。
4. **和差方程最少标定** → 带权并查集（差值双向，合并带符号）。

## 2025-09-14 秋招笔试（改编版）
1. **密码学家的挑战** → 字符串按奇偶位置分别提取再拼接的规律 → 模拟（先写小样例验证）。
2. **项目经理的难题** → 带截止日期调度 → 贪心 + 最大堆：按 deadline 排，超期时弹出耗时最大的那个。
3. **魔法师的能量平衡** → "子段和 = 子段长度" 变形为 Σ(a_i − 1) = 0 → **前缀和 + 哈希**（key = S[i] − i）。
4. **考试策略大师** → 贪心 + 数论（最小公倍数 / "净贡献位置"）。

## 2024-08-25 笔试
1. **删边产生最大联通分量** → 边权排序贪心 / 反向加边 + 并查集增量维护。
2. **奇偶相加** → 贪心（最先能变奇数的优先）。
3. **从右往左交换模拟** → 模拟 + 前缀记录（前缀和/树状数组降维统计交换次数）。
4. **首尾字符能否相接 + 最大 01 区间** → 模拟（把首尾相接做成环：复制一份再滑窗/前缀）。

## 实习笔试
1. **字符串解码** `11a88b2c` → 双指针模拟（数字段+字母段配对；坑：多位数、字母重复）。
2. **数 1 的个数** → 两两配对贪心。
3. **三元组满意度 + 成本最小** → 多维 DP（滚动数组优化）——笔试唯一偏难题。
4. **n 个数中位数与平均数** → **双堆** O(log n)（树状数组/暴力 TLE）。

## 多模态岗附加笔试（S1）
1. **推箱子模拟** → 状态=(箱子集合,人位置)，逐步推进（判定"推不动"：前方是墙或箱子）。
2. **区间[L,R] 内为 3 的倍数的子串** → 逐位对 3 取模 + 前缀和/DP（不用算大整数）。
3. **向右看人数** = LC739 变体 → **单调栈**（递减栈存"还没找到更高者"的下标；楼主只过 60%，边界是坑）。
4. **字符串替换最小字典序** → 贪心（能换更小字符就换，但保证操作次数不超限；先写小样例摸规律）。

## 备考动作
把 **贪心 / 前缀和+哈希 / Dijkstra / 并查集** 四个模板各手写一遍，机考现场先花 2 分钟判题型再动手。
""",
    },
    # ================= 八股 =================
    {
        "code": "BG-BIAS",
        "title": "面经八股：三种偏差（位置/曝光/流行度）的来源与解法",
        "priority": "高",
        "tags": "八股,去偏,PAL,IPS,因果",
        "key_points": "位置偏差=特征污染(PAL/POSO)；曝光偏差=样本缺失(IPS/DR)；流行度偏差=混杂因子(因果embedding)",
        "note": "S4 一面。",
        "content": """## 三种偏差对照（必背）
| 偏差 | 来源 | 危害 | 解法 |
|---|---|---|---|
| **位置偏差** | 用户天然爱点靠前位置，与内容质量无关 | 模型学到"位置好坏"而非"内容好坏"，线上位置还在变 | **PAL**（训练把位置当特征、线上推理置 0）；**特征改造**（训练把位置设为 unknown）；**POSO**（position tower + content tower 解耦） |
| **曝光偏差** | 只有被曝光的物品才有标签 | 训练分布≠真实分布，模型把"没被曝光"当负样本 | **IPS/逆倾向得分**（按曝光概率加权，pw 高的权重低）；**DR**（IPS + 直接法模型兜底，方差更小） |
| **流行度偏差** | 热门曝光多→点击多→更推→更热门，马太效应 | 长尾/新物品无流量，生态恶化 | **因果 embedding**（显式建模流行度这个混杂因子，推理时反事实干预）；**去流行度正则/重加权**；**基于图的去偏** |

## 一句话区分
位置偏差 = **同一物品因位置不同而效果不同**（特征污染）；曝光偏差 = **只有部分物品被观测**（样本缺失）；流行度偏差 = **观测本身被热度扭曲**（混杂因子）。

## 可主动抛
我在 OPPO 做过**多位置预估校准（PAL）**：位置合并（12/345）+ 头部全量 + beam search，偏差 30%→20%，ARPU +1.04%。
""",
    },
    {
        "code": "BG-AUC-GAUC",
        "title": "面经八股：AUC vs GAUC；双塔为什么 serving 不放交叉特征",
        "priority": "高",
        "tags": "八股,评估指标,GAUC,双塔召回",
        "key_points": "AUC 全局、GAUC 按用户加权(推荐真在优化的)；双塔解耦才能离线预计算+ANN，加交叉则物品向量随用户变、索引失效",
        "note": "S4 一面。",
        "content": """## AUC vs GAUC（为什么推荐看 GAUC）
- **AUC**：全局指标，衡量"随机取一正一负、模型给正样本打分更高的概率"，对全局排序负责。
- **问题**：推荐里**只有同一用户内部的排序才有意义**。AUC 会把用户 A 和用户 B 的样本混算——若 A 天然点击率高，模型只要学会"识别高活用户"就能把 AUC 刷高，但单用户内排序可能很差。
- **GAUC**：按用户（或按请求）分组算 AUC，再按**样本量加权平均**：`GAUC = Σ_u (n_u/n) · AUC_u`。
- **一句话**：AUC 答"整体能否区分好物品"，GAUC 答"**对每个用户能否把 ta 想点的排前面**"——后者才是推荐真正在优化的。

## 双塔召回的优缺点；为什么 serving 不放交叉特征
- **优点**：用户塔/物品塔**解耦** → 物品向量**离线预计算 + ANN 索引**（Faiss/HNSW），线上只算用户向量再近邻检索，千万级候选毫秒响应。
- **缺点**：只在最后做内积/余弦，交互能力弱，精度天然不如精排的交叉模型。
- **为什么 serving 不放交叉**：一旦有 `用户×物品` 交叉特征，物品向量**不再是固定的**——换个用户，所有物品的表示都要重算，**预计算与索引全部失效**，退化成 O(候选数) 在线计算，性能/成本不可接受。
- **业界做法**：召回用双塔（快），精排用交叉（准），**级联各取所长**。

## 追问：负采样 / in-batch negative / logQ correction
- in-batch negative：一个 batch 里，其他样本的正物品当本样本负样本（省采样、GPU 利用率高）。
- **logQ correction**：in-batch 负样本来自"按热度采样"分布 Q(i)，会让模型**系统性高估热门物品**；修正 = logits 减 log Q(i)（`score = u·v_i − log Q(i)`）。直觉：越热门被当负样本的次数越多，要补偿回来。Q(i) 常用曝光频次的 **0.75 次幂**平滑。
""",
    },
    {
        "code": "BG-CAUSAL",
        "title": "面经八股：uplift 为何不用 CTR；PSM/DR；T/S/X/DR-learner",
        "priority": "高",
        "tags": "八股,因果推断,uplift,PSM,DML",
        "key_points": "四象限（必买/可说服/不回/睡美人）；只有观察数据用 PSM/DR；S=一模型加开关 T=两模型相减 X=互相补课 DR=双保险+交叉拟合",
        "note": "S4 二面、S14 本人一面真题。",
        "content": """## 为什么智能补贴用 uplift 而不是 CTR
- **CTR 答"谁会点/谁会买"**；uplift 答"**因为发了券，谁才买**"（增量）。
- 用 CTR/CVR 选人会踩两个坑：① **必然转化者（Sure Thing）**——本来就要买的人，发券=纯白送钱（补贴浪费），而这批人恰恰 CTR 最高；② **顽固不化者（Lost Cause）**——怎么发都不买。
- 应该只发 **说服敏感者（Persuadable）**（不发不买、发了才买）。
- **四象限必背**：Sure Thing（必买）/ Persuadable（可说服）/ Lost Cause（不回）/ Do Not Disturb（睡美人：发券反而反感）。CTR 模型只能定位第一象限，uplift 才能定位第二象限。

## 只有观察数据怎么办：PSM / DR-learner
- 历史数据里发券用户和没发券用户本身不同（混杂），直接比均值有偏。
- **PSM**：用协变量 X 拟合"被处理概率" e(X)，把倾向得分相近的处理/对照配对，再算差值。前提：**CIA（条件独立假设）+ 共同支撑域**。
- **DR-learner**：IPS 加权 + 结果模型回归，**双稳健**（倾向模型或结果模型任一正确即一致），方差比纯 IPS 小。
- **痛点**：PSM 只能平衡**可观测**混杂，不可观测无解 → 优先随机实验。

## T / S / X / DR-learner 的区别
| 方法 | 做法 | 优 | 缺 |
|---|---|---|---|
| **S-learner** | 一个模型，treatment 当特征，预测时 t=0/1 各跑一次相减 | 简单、样本利用率高 | treatment 被忽略时 uplift 恒 0；交互弱时差 |
| **T-learner** | 处理组/对照组各建一个模型，预测相减 | 直观、能捕捉异质性 | 样本不均衡时小样本侧不准；**两个模型误差叠加到差值**，方差大 |
| **X-learner** | 先 T-learner 粗估，再用另一组样本算反事实**残差修正**，按倾向得分加权 | 处理/对照**严重不均衡**时最好 | 实现复杂 |
| **DR-learner** | 交叉拟合 + 双稳健损失，直接优化 CATE | 理论性质最好（双稳健） | 工程最重 |

口诀：**S=一个模型加开关，T=两个模型相减，X=互相补课，DR=双保险+交叉拟合**。

## 可主动抛
中信做过因果归因体系 + 13 项策略落地；拼多多发券场景可直接用这套 uplift 口径。
""",
    },
    {
        "code": "BG-MMOE-PACING",
        "title": "面经八股：MMoE vs PLE + negative transfer；Pacing 算法",
        "priority": "高",
        "tags": "八股,多目标,MMoE,PLE,Pacing",
        "key_points": "MMoE 软选择专家；PLE 分共享/专属专家+多层，解跷跷板；任务冲突时负迁移；Pacing=PID/LP 预算平滑",
        "note": "S4 二面。",
        "content": """## MMoE vs PLE
- **MMoE**：底层一组共享 expert，每个任务一个 **gate**（软选择），输出是 experts 加权和。相比"硬共享"，让每个任务能选择性用不同专家。
- **PLE（Progressive Layered Extraction）**：MMoE 升级——区分**共享专家 + 任务专属专家**，并**多层叠加**。动机是解决 MMoE 的**跷跷板现象**（提升一个任务常损害另一个）。

## 什么时候发生 negative transfer（负迁移）
**任务间相关性低甚至冲突时**。比如"点击率"和"退货率"（一个催推、一个拦推）；"短视频完播"与"长视频观看"对时长要求相反。此时共享底层互相拖累，**表现还不如各任务独立建模**。
- **判断**：看各任务 loss 是否此消彼长（跷跷板）。
- **解法**：给冲突任务分配**独立专家**（PLE 思路）、**缩小共享层**、或直接**拆两个模型**。
- **主动抛**：腾讯 MMoE-Group/Special/Skill 的结构演化；我做过 ESMM 统一 CTR/CVR（两任务强相关，共享有效），但退货率这类反向任务就不适合硬共享。

## Pacing 算法（PID / 线性规划）
- **问题**：广告主给了总预算 B，要一天内花完，但**不能前 1 小时花光**（浪费流量、压垮系统），也**不能到点剩一大半**（跑不完）。这就是预算平滑。
- **PID 控制**：把"实际消耗速率 vs 目标速率（预算/剩余时间）"的偏差当误差：P（比例）偏差越大调得越狠；I（积分）消除长期稳态偏差；D（微分）抑制震荡。工程上常见**每 30 min 用 PID 调一次出价系数 λ**（我 OPPO oCPX 就是这么做的）。
- **线性规划**：把"每时段投放量"当决策变量，目标最大化总收益/最小化超投，约束 Σspend_t = B 且各时段有上下限。适合离线/准实时。
- **追问**：怎么判断 pacing 健康？→ 消耗曲线是否平滑贴近理想斜线、预算完成率、超投率。
""",
    },
    {
        "code": "BG-LLM",
        "title": "面经八股：大模型（self-attention√d / ZeRO / 8×A100训30B / FlashAttention / LoRA）",
        "priority": "中",
        "tags": "八股,大模型,分布式训练,ZeRO,LoRA,FlashAttention",
        "key_points": "√d 稳方差防 softmax 饱和；ZeRO-1/2/3 切优化器状态/梯度/参数；8×A100 训 30B=ZeRO3+offload+checkpoint 或 LoRA；FlashAttention 快在 IO；LoRA ΔW=BA 低秩",
        "note": "S2 一面/二面、S6 二面。",
        "content": """## self-attention 为什么除以 √d
Q、K 各维独立、均值 0 方差 1 时，点积的方差是 **d**。d 大 → 点积数值大 → softmax 落入饱和区（一个位置≈1、其余≈0）→ **梯度趋近 0**。除以 √d 把方差归一到 1，保持梯度尺度健康。

## 并行策略（数据/张量/流水线）
| 并行 | 切什么 | 通信 | 适用 |
|---|---|---|---|
| **数据并行 DP** | 切数据，每卡一份完整模型，梯度 all-reduce | 梯度同步（量大） | 模型能装进单卡 |
| **张量并行 TP** | 切单层内矩阵 | 每层前后 all-reduce（**频繁**，要高带宽 NVLink） | 单层太大 |
| **流水线并行 PP** | 切层 | 只在层边界传激活 | 模型太深；需 micro-batch 填**气泡** |
| **ZeRO** | 切优化器状态/梯度/参数（不切计算） | 按需 all-gather | 显存瓶颈通用解 |

## Megatron-LM 的 Tensor Parallel
- **MLP**：第一个矩阵**按列切**（各卡算 Y_i，无需通信），第二个矩阵**按行切**（各卡算部分和，**一次 all-reduce** 得结果）。"列切→行切"保证整个前向只通信 1 次，这是精髓。
- **Attention**：多头天然可切——每个头分到不同卡，输出投影按行切 + all-reduce。

## ZeRO 三阶段（都切显存、不切计算）
- ZeRO-1：切**优化器状态**（Adam 的 m/v + fp32 参数副本，显存大头）；
- ZeRO-2：再切**梯度**；
- ZeRO-3：再切**参数**本身（用到该层才 all-gather）。
一句话：ZeRO-1 省最多、代价最小；ZeRO-3 最省但通信最重。

## 只有 8 张 A100 怎么训 30B（组合拳）
1. **显存估算**：30B fp16 权重 60GB；梯度 +60GB；Adam 状态（fp32 m/v/主权重）约 +360GB → 单卡放不下。
2. **切分**：ZeRO-3，或 TP+PP+DP 三维并行（TP=8 或 TP4×PP2）。
3. **省显存**：bf16 混合精度 + **梯度检查点**（计算换显存）+ **FlashAttention** + **offload**（卸载到 CPU）。
4. **降成本**：**LoRA/QLoRA** 只训低秩参数 + 梯度累积模拟大 batch。
5. **数据侧**：序列打包（packing）提高 token 利用率。
6. **结论**：8×A100(80G) 训 30B **全参**需 ZeRO-3 + offload + checkpoint，很慢；**更现实是 LoRA 微调**。

## FlashAttention 为什么更快
理论复杂度不变，快在 **IO**。标准实现要显式写出 L×L 注意力矩阵（存 HBM 再读回），HBM 带宽是瓶颈。FlashAttention 用**分块(tiling) + online softmax**：Q/K/V 分块塞进 **SRAM** 算，**不物化完整注意力矩阵**，softmax 归一化因子滚动更新（running max/sum）→ **显存 O(L) 而非 O(L²)，速度 2~4×**。

## LoRA 为什么能减参
冻结原权重 W，只训低秩增量 `ΔW = BA`（B:d×r、A:r×d，r≪d），参数量从 d×d 降到 **2dr**，少几个数量级；推理时把 BA 合并回 W（不增延迟）；不同任务挂不同 LoRA。**rank r**：越大表达力越强但收益递减，常用 8/16/32/64；任务越偏离基座需要的 r 越大。

## 其它高频（能说就行）
- **RLHF/RLAIF/DPO**：RLHF 效果上限高但工程重（4 个模型）；RLAIF 用 AI 标注摆脱人工；DPO 闭式损失、无需 RM 与 RL 采样，简单稳定 → **电商客服/导购选 DPO 或 RLAIF**。
- **判断收敛**：训练/验证 loss 平稳 + 下游 eval 不再提升 + 梯度范数稳定。**崩溃定位**：看 loss 形态（NaN→学习率/溢出）→ grad norm（爆炸）→ loss scale（混合精度溢出）→ 极小复现 → 查数据。
- **幻觉缓解**：RAG（检索商品库喂上下文）+ 结构化字段强校验 + 后置校验 + 降温度/约束解码 + 要求引用来源。
- **长上下文**：RoPE 外推（NTK/YaRN）、稀疏/滑窗注意力、KV Cache 压缩（MQA/GQA/MLA）、RAG 替代长上下文、分块+摘要递归聚合。
- **梯度消失/爆炸**：残差连接 + LayerNorm + 合理初始化 + 梯度裁剪 + 门控。**ReLU vs GLU**：GLU 有门控、表达强、更稳（FFN 多用 SwiGLU）。**显存优化**：混合精度/梯度检查点/ZeRO/FlashAttention/packing/8-bit 优化器。
""",
    },
    {
        "code": "BG-CV",
        "title": "面经八股：CV/多模态（CLIP / MoCo / RepVGG量化 / 标签分配 / LM）",
        "priority": "低",
        "tags": "八股,CV,多模态,CLIP,MoCo",
        "key_points": "CLIP 对比学习双塔，batch 越大越好；MoCo 用队列+动量编码器扩负样本；RepVGG 多分支BN分布差异大量化不友好；Sinkhorn 可微近似二分图匹配；LM 插值 GD 与 GN 之间",
        "note": "S1（多模态岗）。非目标方向，扫一眼即可。",
        "content": """## CLIP 原理与缺点
- **原理**：图像 Encoder + 文本 Encoder 双塔，在**对比学习**下对齐图文表示（in-batch 的图文配对为正，其余为负）。
- **缺点**：① 只给相似度、不做细粒度定位；② 对否定/关系/计数不敏感（"没有猫"和"有猫" embedding 接近）；③ 受训练数据分布限制，长尾概念差；④ 需要**大 batch**。
- **batch 应该越大还是越小**：**越大越好**。对比学习里 batch 内其他样本就是负样本，batch 越大 → 负样本越多 → 表示区分度越高、梯度估计方差越小。这也是 CLIP 用数万级 batch 的原因。

## MoCo 原理；如何增大 batch
- 维护一个**队列（memory bank）**存历史 batch 的 encoded key，用**动量更新的 Encoder**保证队列特征一致（否则新旧特征分布不一致，对比失效）。
- **怎么增大 batch**：不靠增大真实 batch，而靠**队列缓存大量历史负样本**（负样本数从 batch size 扩到 queue size），动量更新保证 code book 不漂移。

## 为什么 RepVGG 不适合量化
RepVGG 训练是多分支（3×3 + 1×1 + identity），推理时重参数化成单个 3×3。合并引入**大量 BN 折叠计算**，各分支 BN scale 分布差异大 → **量化 scale 被少数大分支主导，小分支精度被吃掉**；结构过于规整，量化误差缺少补偿空间。改进：QAT、对每个分支单独量化再合并（QARepVGG）、用多样化结构增鲁棒性。

## 目标检测发展史
传统 HOG+SVM → RCNN 系（两阶段 anchor-based）→ YOLO/SSD（一阶段回归）→ anchor-free（FCOS/CenterNet）→ **DETR**（Transformer + 集合预测，NMS-free）→ CLIP 后的**开放词汇检测**（GLIP/GroundingDINO，文本提示出框）。

## 标签分配 / Sinkhorn / NMS-free
- 标签分配 = 决定"哪个预测框负责哪个 GT"（MaxIoU/ATSS/SimOTA/匈牙利匹配）。
- DETR 用**匈牙利二分图匹配**（一对一，天然无需 NMS）。
- **Sinkhorn** = "可微的近似二分图匹配"（在打分矩阵上迭代行列归一化得到双随机矩阵，可作 soft 匹配层嵌进网络）。
- 其他 NMS-free：一对多辅助头 + 一对一主头（YOLOv10）、centerness/quality 打分、集合预测。

## 高斯牛顿 vs LM；LM 的多视角
- **高斯牛顿**用 JᵀJ 近似 Hessian（残差小/近线性时二次收敛快），但 JᵀJ 可能不可逆。
- **LM** 加阻尼：`(JᵀJ + λI)Δ = −Jᵀr`——λ 大退化成**梯度下降**（稳但慢），λ 小退化成**高斯牛顿**（快但不稳），用"是否降低残差"动态调 λ。
- 多视角：**信赖域方法**（带球约束的二次模型）、**信赖域 vs 线搜索**、**岭回归的推广**、**插值于 GD 与 GN 之间**。
- **为什么深度学习不易陷入局部最小**：高维空间"鞍点远多于局部极小"，且高维下局部极小的 loss 通常接近全局；SGD 噪声 + 大 batch 重叠性帮助逃逸。
""",
    },
    {
        "code": "BG-RISK-ML",
        "title": "面经八股：风控/数据（IV/PSI、分层抽样、DML正交）+ 传统 ML（手推LR/L1/LSTM）",
        "priority": "中",
        "tags": "八股,风控,IV,PSI,DML,LR,LSTM",
        "key_points": "IV 看有用性、PSI 看稳定性；分层抽样减方差（消掉层间方差）；Neyman 正交=对干扰函数一阶不敏感；softmax+CE 梯度=p-y；L1 稀疏靠软阈值",
        "note": "S5（风控）、S11/S12（传统 ML）。",
        "content": """## IV / PSI（风控上线特征必备）
- **IV（信息量）** = Σ (好人占比 − 坏人占比) × WOE，衡量单特征对好/坏的区分能力（<0.02 无用、0.1~0.3 中等、>0.5 警惕过拟合）。
- **PSI（群体稳定性）** = Σ (实际占比 − 预期占比) × ln(实际/预期)，衡量**分布漂移**（<0.1 稳定、0.1~0.25 关注、>0.25 不稳定）。
- **一个看"有没有用"，一个看"稳不稳"**，上线特征两个都要看。

## A/B：DAG 与分层抽样为什么减小方差
- **DAG**：有向无环图描述分流层级（用户→实验→层→桶），保证**同层互斥、异层正交**。
- **分层抽样减方差**：按协变量分层（层内同质、层间异质），总方差 = Σ w_h²σ_h²（层内）+ 层间项；分层后每层按比例抽样，**层间差异被消掉** → Var_strat ≤ Var_SRS。实验里"按活跃度分层"能显著降方差、提检验功效。

## DML 与 Neyman 正交
- **DML**：**交叉拟合(cross-fitting)** + 残差回归——用 ML 分别拟合 E[Y|X]、E[T|X]，拿残差 (Y−Ŷ) 与 (T−T̂) 做回归估计因果效应。
- **Neyman 正交**：估计量对**干扰函数的一阶扰动不敏感**，即 ∂/∂η E[ψ] = 0。直觉：把一阶偏差项消掉，即使 ML 估计 E[Y|X] 有小误差，也不会**一阶**污染最终因果估计（只留可忽略高阶误差）。这是 DML 能用任意 ML 且达 √n 收敛的根本原因。

## 手推 LR
1. logit：z = wᵀx + b
2. 概率：p = σ(z)，等价 **ln(p/(1−p)) = wᵀx + b**（对数几率线性）
3. 似然：L = ∏ p^y (1−p)^(1−y)
4. 负对数 → 交叉熵：J = −Σ[y·ln p + (1−y)·ln(1−p)]
5. 梯度：**∂J/∂w = Σ (p_i − y_i) x_i**（sigmoid + CE 配对的产物）
6. 更新：w ← w − η(p − y)x

## L1 正则怎么求解
目标 J = loss + λ‖w‖₁，在 0 处不可导。解法：① 子梯度下降（用 sign(w)）；② **坐标下降 + 软阈值算子**（`w_j = soft_threshold(ρ_j, λ)`：小于 λ 置 0、大于的收缩 λ —— **这就是稀疏性的机制**）；③ 近端梯度 ISTA/FISTA；④ LARS。

## LSTM 公式
```
f_t = σ(W_f·[h_{t-1}, x_t] + b_f)      遗忘门
i_t = σ(W_i·[h_{t-1}, x_t] + b_i)      输入门
o_t = σ(W_o·[h_{t-1}, x_t] + b_o)      输出门
C̃_t = tanh(W_C·[h_{t-1}, x_t] + b_C)   候选记忆
C_t = f_t ⊙ C_{t-1} + i_t ⊙ C̃_t         细胞状态
h_t = o_t ⊙ tanh(C_t)                   隐状态
```
核心：C_t 的更新是**加法**，梯度沿"细胞状态高速公路"传回，避免 RNN 连乘梯度消失。遗忘门=忘多少旧的，输入门=写多少新的，输出门=露多少。

## 其它
- **XGBoost**：加法模型 + **二阶泰勒**（用 g、h 直接算叶子最优权重 w* = −G/(H+λ)）+ 正则（叶子数 γ + 叶子权重 L2）+ 行列采样 → **本质是牛顿法一族**。**KS** 看最佳阈值区分度、**AUC** 看排序能力。
- **detach() vs item()**：detach 管**计算图**（脱离梯度、共享数据），item 管**取值**（单元素 tensor → Python 标量）。共同点：都不能再对它反传。
- **自监督/无监督/半监督/自回归**：自监督 ⊂ 无监督（从数据自身造监督信号）；半监督是"少量标签+大量无标签"；自回归是一种建模方式（用前 k 个预测第 k+1）。**关系是维度不同，不是互斥**。
""",
    },
    # ================= 项目深挖 =================
    {
        "code": "PRJ-15Q",
        "title": "面经项目深挖：15 连问答题模板（拼多多最爱这一环）",
        "priority": "高",
        "tags": "项目深挖,15连问,量化数字",
        "key_points": "杀伤力全在数字；每题结尾主动迁移到拼多多场景；准备 3 数字+1 故障故事",
        "note": "S2 三面原句照录。拼多多最爱这一环，没数字=没做过。",
        "content": """## 核心心法
面试官反复问「解决后质量提升了多少」「指标结果分别是多少」——**没有数字 = 没做过**。
提前给 3 个项目各写一张卡片：背景 / 我的角色 / 技术方案 / **3 个量化数字** / **1 个故障故事**。
每题结尾**主动补一句"这个经验可直接用到拼多多的 XX 场景"**，让面试官看到迁移能力。

## 15 连问速答骨架（括号=你该填的弹药）
1. **数据量级是多少？** → 样本条数/token 数/特征维度/日增（OPPO 日均 X 亿曝光；腾讯 X 亿行为序列）
2. **数据预处理全流程？** → 采集→清洗→去重(精确/SimHash)→质量打分→采样→特征构造→存储（讲"分层路由"：优质入训练池、低质入复盘池）
3. **脏/低质/重复/敏感数据？解决后质量提升多少？** → 问题→方案→**数字**（去重率 X%、低质由 X% 降到 Y%、收敛更快 X%）
4. **垂直领域怎么适配/蒸馏？有专属指令集吗？** → 数据筛选→指令构造→蒸馏(大模型产标注)→领域评测集（领域准确率 X%→Y%）
5. **用什么基座？为什么？** → 尺寸/协议/中文能力/推理成本/生态（说清选型逻辑，别只说"qwen 好用"）
6. **训练框架？有做框架二次修改吗？** → PyTorch/DeepSpeed/Megatron/原生；有就讲自定义算子/通信优化/数据管道，没有就诚实说
7. **最棘手的技术故障？** → 现象→影响→定位过程→根因→修复（用 OPPO 校准偏差那个，id=77 有完整版）
8. **怎么定位根因？优化后量化提升？** → 监控→二分定位→最小复现→修（给百分比/绝对值）
9. **SFT 指令集怎么构建？如何避免知识遗忘？** → 数据来源/标注规则/正负比/样本量/混通用数据（正:负=X:Y，混 X% 通用指令）
10. **多模态图文不匹配、模态失衡怎么解决？** → 数据层(过滤/对齐/重采样)+模型层(模态 dropout/门控融合)+损失层(对比 ITC+生成)
11. **用了哪些评估指标？结果分别是多少？** → PPL/BLEU/ROUGE/MMLU/C-Eval/领域准确率 + A/B 线上指标（每个数字都要能解释口径）
12. **有人工主观评估吗？怎么降低偏差？** → 维度(准确性/流畅性/有用性)→打分(Likert 1-5)→**双盲 + 多人独立 + Kappa 一致性校验**
13. **效果未达预期的情况？** → 现象→归因→调整→结果（讲"负结果"反而显真实）
14. **重新做会优化哪些环节？哪些选型是坑？** → 复盘视角：数据>模型>训练>评估；说一个真实的坑
15. **前沿方向试过吗？** → Agent-RL/长上下文/高效推理 + 结合最近读的（生成式推荐、OneRec 等）

## 一句话
**3 个量化数字 + 1 个故障故事**，每个项目都能讲；这比任何八股都值钱。
""",
    },
    # ================= 场景题 =================
    {
        "code": "SC-AD",
        "title": "面经场景题：设计广告竞价 + 出价系统（召回→粗排→精排→出价→pacing）",
        "priority": "高",
        "tags": "场景题,系统设计,广告竞价,oCPX,Pacing",
        "key_points": "五层链路；eCPM=出价×pCTR；oCPX 折算 点击出价=转化出价×pCVR；Pacing PID 每30min调λ；机制激励相容",
        "note": "S4 三面（主管面）。你的强项（OPPO 广告全链路），要主动抛。",
        "content": """## 分层讲（五层链路）
1. **召回**：从百万广告主按定向（人群/地域/时段）+ 相关性召回几千条 → 双塔 + ANN。
2. **粗排**：几千 → 几百，轻量模型（双塔/线性 + 少量交叉）。
3. **精排**：几百 → 几十，**预估 pCTR × pCVR**（多任务 MMoE/PLE），按 **eCPM** 排序。
4. **出价（竞价）**：二价/一价拍卖。**oCPX** 下广告主给"转化目标出价"（如 30 元/下单），平台需**把转化出价折算成点击出价**：`点击出价 = 转化出价 × pCVR`；排序按 `eCPM = 出价 × pCTR`。
5. **Pacing**：预算平滑，**PID 每 30 min 调一次出价系数 λ**（我 OPPO 真做过，重点讲），目标"按时花完且不过投"。

## 机制设计要点
**激励相容（truthful bidding）**、**GSP/VCG** 的选择、**广告密度上限**、**冷启动探索预算**、**防作弊（点击欺诈）**。
> 可引用美团 SIGIR'24：广告拍卖与自然流量**深度融合**，在 eCPM 之外引入"用户体验折价 α"，即排序分 = eCPM − α×体验损失。

## 监控
消耗速率、超投率、eCPM 分布、广告主 ROI、CTR/CVR 校准。

## 追问
- 为什么不做纯 GSP？→ 考虑激励相容与平台长期收益。
- Pacing 和出价什么关系？→ Pacing 输出 λ 乘到出价上，控制消耗节奏。
""",
    },
    {
        "code": "SC-COLD",
        "title": "面经场景题：新用户冷启动（群体画像 + bandit + meta-learning）",
        "priority": "中",
        "tags": "场景题,冷启动,bandit,元学习",
        "key_points": "三层：群体先验画像→bandit探索→meta-learning快速适应；指标=7日留存/首日CTR/探索效率",
        "note": "S4 三面。",
        "content": """## 为什么难
新用户**没有行为序列**，个性化无从下手。

## 三层解法
1. **群体画像（群体先验）**：用注册渠道、设备、地域、时段等**弱特征**，把新用户映射到相似人群，用该人群偏好做**先验打分**（= 贝叶斯先验）。
2. **Bandit 探索（Thompson Sampling / UCB）**：不只利用先验，要**主动探索**——推荐里混入一定比例探索内容，用探索流量反馈更新其后验。**我在 OPPO 的"探索流量池 + 贝叶斯平滑"，通过率 +15%**。
3. **Meta-learning（MAML 思想）**：学习"如何快速适应新用户"的初始化，让模型用**极少交互**就快速个性化。

## 关键指标
新用户 7 日留存、首日点击率、探索效率（ε 取值）、探索/利用平衡。

## 可主动抛
冷启动在搜/广/推**三场景不同**：推荐看兴趣探索、搜索看 query 理解（可用 query 字面匹配兜底）、广告看受众定向（可用地域/时段先验）。
""",
    },
    {
        "code": "SC-AUCGMV",
        "title": "面经场景题：线上 AUC 没掉但 GMV 掉 3% 怎么排查",
        "priority": "高",
        "tags": "场景题,排查,AUC,校准,GMV",
        "key_points": "AUC只衡量排序、不衡量校准；第一嫌疑是PCVR校准漂移；再查排序结构/数据/系统/业务；手段=分桶对比+特征回放+指标下钻",
        "note": "S4 三面。考数据感，不是标准答案，考排查框架。",
        "content": """## 排查框架（模型→数据→系统→业务）
1. **先确认口径**：GMV 是全站还是某实验桶？掉的是哪部分（新客/老客、某类目、某端）？→ **先切维度定位范围**。
2. **模型/预估层**：**AUC 只看排序、不看校准（calibration）**。GMV 高度依赖绝对价格/概率（出价、Pacing、GMV 预估），首查 **PCVR 校准是否漂移**（预估 5% 实际 3%）→ 这会导致 AUC 不掉但 GMV 掉。
3. **排序结构**：AUC 保住相对顺序，但可能**高分位被少数高价商品占据**（结构性变化）→ 看分数/位置/类目分布是否漂移。
4. **数据层**：特征穿越、特征延迟（当天新特征未回填）、样本分布漂移、新品类目 embedding 没学到。
5. **系统层**：延迟升高导致**超时降级**（回退热门）、召回数减少、缓存击穿、限流。
6. **业务/外部**：大促节奏、竞对补贴、供应链缺货（推了买不到）、流量结构变化。
7. **验证手段**：**分桶对比 + 特征回放(replay) + 指标下钻 + 反事实分析**。

## 一句总结
**AUC 是"排序"，GMV 是"排序 × 校准 × 覆盖 × 供给"**，所以 AUC 不掉不代表链路健康——**第一嫌疑永远是校准**。

## 可主动抛
我在中信做过因果归因体系（13 项策略落地），这套"切维度 + 归因 + 反事实"的方法可直接用来定位 GMV 掉的原因。
""",
    },
    {
        "code": "SC-ABFAIL",
        "title": "面经场景题：AB 实验显著正向、全量后效果消失",
        "priority": "高",
        "tags": "场景题,AB实验,新奇效应,SUTVA",
        "key_points": "新奇效应/污染SUTVA/流量结构差异/幸存者偏差/反馈回路/统计问题/工程降级；正确做法=AA校验+分层正交+跑满周期+灰度放量",
        "note": "S4 三面。",
        "content": """## 可能原因（按概率）
1. **新奇效应（Novelty Effect）**：用户对新策略短期好奇点击，长期回落 → **需要跑 ≥2 周，看趋势而非单点**。
2. **实验期污染 / SUTVA 违背**：实验组和对照组互相影响（同一用户被两桶影响、社交/推荐的同质内容外溢），全量后"对照组消失"，效果自然没了。
3. **流量结构差异**：实验只跑 X% 流量，或许是**低竞争时段 / 特定人群**，全量后面对更难的人群。
4. **幸存者偏差 / 样本选择**：实验期恰好赶上活动、节假日；或实验桶和线上流量的用户构成不同。
5. **反馈回路（Feedback Loop）**：实验期模型看不到全量数据，全量后推荐结果互相影响（马太效应加剧、内容同质化），生态指标恶化。
6. **统计问题**：**样本量不足就宣布显著**（多重比较未校正）、**指标是代理指标**（点击涨了但转化没涨）、**实验周期不足**（未覆盖完整周期）。
7. **工程问题**：全量后 QPS 升高导致**降级/超时**，实际生效的策略和实验期不一致（配置没同步、特征在线上取不到）。

## 正确做法
设计阶段就做 **A/A 校验**、**分层正交**、**跑满周期**、**预注册指标 + 护栏指标**、**灰度逐步放量（1%→5%→20%→50%）** 并在每一步观察。

## 一句话
"实验显著"只说明**在实验流量上**显著；全量后人群、竞争、反馈回路都变了——**先怀疑新奇效应和 SUTVA，再怀疑流量结构**。
""",
    },
    {
        "code": "SC-SYSDESIGN",
        "title": "面经场景题：高并发短链 + 分布式事务 TCC + TOPK/秒杀",
        "priority": "中",
        "tags": "场景题,系统设计,短链,TCC,秒杀,TOPK",
        "key_points": "短链=发号器(Redis INCR/号段/雪花)+62进制+多级缓存+布隆过滤器；TCC 空回滚=Cancel前查Try记录、悬挂=Cancel写标记拒绝迟到Try；秒杀=Redis预减库存+MQ削峰+幂等唯一索引",
        "note": "S3（服务端）、牛客《拼多多2025春招面经》。",
        "content": """## 高并发短链系统设计（发号器 / 缓存 / 碰撞）
- **发号器**：① **自增 ID + 转 62 进制**（[0-9a-zA-Z]，6 位 = 568 亿）；② 分布式发号——**Redis INCR**（简单但有单点/持久化风险）、**号段模式**（一次取 1000 个本地分配，减 Redis 压力）、**雪花算法**（时间戳+机器ID+序列，趋势递增但要处理时钟回拨）。
- **映射与碰撞**：短码→长链存 MySQL/Redis/KV；若短码由长链哈希生成要处理**哈希碰撞**（加盐重试），或用发号器天然无碰撞。
- **读取性能**：短码是**热点 Key** → 多级缓存（本地 Caffeine + Redis）+ CDN 层缓存 302；用**布隆过滤器**挡不存在的短码（防缓存穿透）。
- **写**：发号 → 落库 → 写缓存（**先写库再删缓存**，防不一致）。
- **监控**：QPS、缓存命中率、P99 延迟、短码碰撞率。

## 分布式事务 TCC（空回滚、悬挂）
- **TCC**：**Try**（预留资源，如冻结额度）→ **Confirm**（确认提交）→ **Cancel**（释放预留）。相比 2PC，不长时间持有数据库锁。
- **空回滚**：Try 因网络超时**没执行成功**，但协调器认为失败并发起 Cancel → Cancel 在"没有预留资源"时被调用。**解决**：Cancel 里先**判断是否存在 Try 记录**，没有就直接返回成功（幂等空操作）；或用"防悬挂表"记录事务状态。
- **悬挂（Hanging）**：Try 的网络请求**延迟到最后才到达**——此时 Cancel 已执行完，这个迟到的 Try 又预留了资源，导致**资源被永久占用**。**解决**：**Cancel 时写一条"已回滚"标记**，Try 执行前先检查该标记，若已回滚则**拒绝执行 Try**。
- **幂等**：Confirm/Cancel 都可能重试，必须用**事务 ID 做幂等键**。

## TOP K 问题
- **内存够**：`heapq` 维护大小 K 的**小顶堆**，O(n log K)。
- **海量/流式**：**分治 + 归并**（分片各取 topK 再合并）；或**哈希分桶 + 每桶小顶堆**。
- **要求第 K 大精确值**：**快速选择（QuickSelect）** 平均 O(n)。
- 经验：K 很小、n 极大 → 分桶再堆最快。

## 秒杀设计
1. **前端**：按钮置灰、答题防脚本、静态资源 CDN。
2. **网关**：限流（令牌桶）、黑名单、验证码。
3. **库存**：**Redis 预减库存**（Lua 原子）+ 异步下单（MQ 削峰）；`DECR` 返回负值即失败，**不要用数据库悲观锁**（扛不住）。
4. **防超卖**：Redis 原子扣减 + 唯一索引（`user_id × 活动_id` 幂等）+ MQ 串行落库。
5. **兜底**：下单失败 Redis `INCR` 回补、削峰队列限长、降级到"排队中"页。
""",
    },
    {
        "code": "HR-口径",
        "title": "面经 HR 面口径（11116 / 为什么来拼多多 / 家庭情况 / 薪资）",
        "priority": "高",
        "tags": "HR面,11116,薪资,口径",
        "key_points": "11116 不能犹豫；为什么来拼多多要业务+技术+对口经历三件套；薪资给区间锚定市场；家庭情况如实简短",
        "note": "S1/S2/S4/S7。11116 每轮开场必问，答不好直接凉。",
        "content": """## 原句与口径（S1/S2/S4/S7）
| 面试官原句 | 口径要点 | 参考话术 |
|---|---|---|
| **「我们是 11116，工作节奏比较快，能接受吗？」（每轮开场都问）** | **态度坚决、不犹豫、不讨价还价** | 「能接受。我在上一段经历就是高强度业务节奏，习惯了大促和 online 的压力，也清楚拼多多的节奏。我选拼多多就是因为它做事效率高、决策快。」 |
| 「为什么选择拼多多？最大的吸引力？」 | 业务 + 技术 + 成长三点，别只说"大厂" | 「① 电商是搜广推最能拿到真实反馈的场景，数据量大、迭代快；② 拼多多的推荐/广告还在快速演进（比如 Temu 全球化），能接触前沿问题；③ 我做过 OPPO 广告全链路和腾讯的 CTR/CVR 建模，想在更大流量、更复杂的电商场景验证。」 |
| 「你的背景是 XX（机器人/自动驾驶/银行），为什么来拼多多？」 | 把**跨领域讲成优势**（迁移能力），别解释成"找不到对口" | 「跨领域经历让我在特征工程和系统能力上更扎实。而且搜广推的很多方法和我在 OPPO/腾讯做的排序建模、因果归因相通，我在中信做过 13 项因果策略落地，这套东西到电商发券/补贴场景可以直接用。」 |
| 「有投其他 IT 大厂吗？其他家 offer 拿到多少？」 | **诚实但保留**，不说具体数字，强调拼多多首选 | 「有面几家，还在流程中。但拼多多是我优先级最高的——业务和技术方向最匹配。」（被追问薪资就说"希望能和同岗位合理水平对齐，具体更看重团队和成长空间"） |
| **「是否独生子女？有无对象？父母做什么工作？」（问得很细）** | 如实回答，**简短平和**，表达"稳定、能投入工作" | 不需要编造，正常回答即可。可自然带一句"家里支持我在一线城市长期发展"。 |
| 「期望薪资是多少？往下压一压还考虑吗？」 | 给**区间**、锚定**市场水平**、留谈判空间 | 「参考同岗位市场水平，我的期望是 XX~XX。具体会综合岗位职责和整体 package 来看，我更关心方向匹配和成长速度。」 |
| 「职业规划 / 绩效考核形式 / 企业文化介绍」 | 规划讲"**技术深度 + 业务价值**"，别说"想转管理" | 「未来 2~3 年想在一个业务场景里把排序/生成式推荐做深，能独立负责一条链路，做出可量化的业务收益。」 |

## 两条底线
- **11116 不能犹豫**（犹豫 = 出局）；
- **「为什么来拼多多」要有具体理由**（业务 + 技术 + 你的对口经历三件套）。
""",
    },
]


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    inserted = []
    for s in NEW_SCRIPTS:
        source = f"面经真题-拼多多-{s['code']}-2026-09-12"
        cur.execute("SELECT id FROM interview_scripts WHERE source=?", (source,))
        if cur.fetchone():
            print(f"已存在 source={source}，跳过")
            continue
        cur.execute(
            """INSERT INTO interview_scripts
               (type, company, position, title, content, key_points, priority, tags, used_count, note, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "技术问答", "拼多多", "AI算法工程师(电商推荐)", s["title"], s["content"],
                s["key_points"], s["priority"], s["tags"], 0, s["note"], source, NOW, NOW,
            ),
        )
        inserted.append((cur.lastrowid, s["code"], s["title"]))
    conn.commit()
    conn.close()

    print(f"\n新增 {len(inserted)} 条：")
    for rid, code, t in inserted:
        print(f"  id={rid} | {code} | {t}")


if __name__ == "__main__":
    main()